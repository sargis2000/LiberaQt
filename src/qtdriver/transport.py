"""Newline-delimited JSON over a loopback TCP socket.

One background reader thread demultiplexes responses (correlated by ``id``) from unsolicited
events. ``call()`` blocks the calling thread on a per-request Event, so the public API stays
synchronous while the wire protocol stays async.
"""

from __future__ import annotations

import itertools
import json
import socket
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from .errors import ConnectionLostError, ProtocolError, TimeoutError, from_agent_error
from .protocol import PROTOCOL_VERSION


class _Pending:
    __slots__ = ("event", "response")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.response: Optional[dict] = None


class Transport:
    """Synchronous request/response client for the qtdriver agent."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0, token: str = "",
                 trace: bool = False):
        self.host = host
        self.port = port
        self.token = token
        self.trace = trace
        self.hello: Dict[str, Any] = {}
        self.history: List[dict] = []       # last N messages, attached to failure reports
        self.history_limit = 200

        self._sock: Optional[socket.socket] = None
        self._ids = itertools.count(1)
        self._pending: Dict[int, _Pending] = {}
        self._lock = threading.Lock()
        self._reader: Optional[threading.Thread] = None
        self._closed = threading.Event()
        self._listeners: Dict[str, List[Callable[[dict], None]]] = {}
        self._buffer = b""
        self._failure: Optional[BaseException] = None

    # ------------------------------------------------------------------ lifecycle

    def connect(self, timeout: float = 10.0) -> dict:
        deadline = time.monotonic() + timeout
        last_err: Optional[Exception] = None
        while time.monotonic() < deadline:
            try:
                self._sock = socket.create_connection((self.host, self.port), timeout=2.0)
                self._sock.settimeout(None)
                break
            except OSError as exc:               # agent not listening yet
                last_err = exc
                time.sleep(0.05)
        else:
            raise ConnectionLostError(
                f"could not connect to agent at {self.host}:{self.port}: {last_err}"
            )

        self._reader = threading.Thread(target=self._read_loop, name="qtdriver-reader", daemon=True)
        self._reader.start()

        self.hello = self._await_hello(timeout=5.0)
        if self.hello.get("protocol") != PROTOCOL_VERSION:
            raise ProtocolError(
                f"agent speaks protocol {self.hello.get('protocol')}, "
                f"client speaks {PROTOCOL_VERSION}",
                hint="Run `qtdriver agents install` to refresh the agent binary.",
            )
        self._send({"type": "auth", "token": self.token, "protocol": PROTOCOL_VERSION})
        return self.hello

    def close(self) -> None:
        self._closed.set()
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

    @property
    def is_connected(self) -> bool:
        return self._sock is not None and not self._closed.is_set()

    # ------------------------------------------------------------------ requests

    def call(self, cmd: str, params: Optional[dict] = None, timeout: float = 10.0,
             selector: Any = None) -> Any:
        """Send a command and block until the response arrives.

        Agent-side errors are translated into the exception hierarchy in ``errors.py``.
        """
        if not self.is_connected:
            raise ConnectionLostError(
                "not connected to an agent",
                data={"failure": repr(self._failure)} if self._failure else None,
            )

        req_id = next(self._ids)
        pending = _Pending()
        with self._lock:
            self._pending[req_id] = pending

        message = {
            "id": req_id,
            "cmd": cmd,
            "params": params or {},
            "timeout_ms": int(timeout * 1000),
        }
        self._send(message)

        if not pending.event.wait(timeout + 1.0):
            with self._lock:
                self._pending.pop(req_id, None)
            raise TimeoutError(
                f"no response to {cmd} within {timeout:.1f}s",
                hint="The GUI thread is probably blocked. Check for a modal loop in the AUT.",
            )

        response = pending.response or {}
        if not response.get("ok", False):
            raise from_agent_error(response.get("error", {}), selector=selector)
        return response.get("result")

    def on(self, event_name: str, callback: Callable[[dict], None]) -> None:
        self._listeners.setdefault(event_name, []).append(callback)

    # ------------------------------------------------------------------ internals

    def _send(self, message: dict) -> None:
        sock = self._sock
        if sock is None:
            raise ConnectionLostError("socket is closed")
        line = json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n"
        self._record(message, outgoing=True)
        try:
            sock.sendall(line)
        except OSError as exc:
            raise ConnectionLostError(f"send failed: {exc}") from exc

    def _await_hello(self, timeout: float) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.hello:
                return self.hello
            time.sleep(0.01)
        raise ProtocolError("agent never sent a hello banner")

    def _read_loop(self) -> None:
        try:
            while not self._closed.is_set():
                sock = self._sock
                if sock is None:
                    return
                chunk = sock.recv(65536)
                if not chunk:
                    raise ConnectionLostError("agent closed the connection")
                self._buffer += chunk
                while b"\n" in self._buffer:
                    raw, self._buffer = self._buffer.split(b"\n", 1)
                    if raw.strip():
                        self._dispatch(json.loads(raw.decode("utf-8")))
        except Exception as exc:                     # noqa: BLE001 - surfaced to callers
            self._failure = exc
            self._closed.set()
            with self._lock:
                pending, self._pending = self._pending, {}
            for p in pending.values():
                p.response = {"ok": False, "error": {"code": "internal", "message": str(exc)}}
                p.event.set()

    def _dispatch(self, message: dict) -> None:
        self._record(message, outgoing=False)
        msg_type = message.get("type")
        if msg_type == "hello":
            self.hello = message
            return
        if msg_type == "event":
            for cb in self._listeners.get(message.get("event", ""), []):
                try:
                    cb(message.get("data", {}))
                except Exception:                    # noqa: BLE001 - listener must not kill reader
                    pass
            return
        req_id = message.get("id")
        if req_id is None:
            return
        with self._lock:
            pending = self._pending.pop(req_id, None)
        if pending is not None:
            pending.response = message
            pending.event.set()

    def _record(self, message: dict, outgoing: bool) -> None:
        entry = {"dir": ">" if outgoing else "<", "t": time.time(), "msg": message}
        self.history.append(entry)
        if len(self.history) > self.history_limit:
            del self.history[: len(self.history) - self.history_limit]
        if self.trace:
            print(f"{entry['dir']} {json.dumps(message)[:400]}")
