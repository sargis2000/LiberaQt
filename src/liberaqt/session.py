"""Session: owns the transport, the timeout policy and the object map.

Everything user-facing (Application, Window, Locator) delegates its wire access here, so there is
exactly one place that knows how to talk to an agent.
"""

from __future__ import annotations

import base64
import os
from typing import Any

from .errors import LiberaQtError
from .protocol import Cmd
from .transport import Transport
from .waits import TimeoutPolicy


class ObjectMap:
    """Symbolic name -> selector string. Loaded from YAML or a plain dict."""

    def __init__(self, mapping: dict | None = None):
        self._flat = {}
        if mapping:
            self._flatten(mapping, prefix="")

    @classmethod
    def load(cls, path: str | None) -> ObjectMap:
        if not path:
            return cls()
        if not os.path.exists(path):
            raise LiberaQtError(f"object map not found: {path}")
        try:
            import yaml  # noqa: PLC0415 - optional dependency
        except ImportError as exc:
            raise LiberaQtError(
                "object maps need PyYAML", hint="pip install 'liberaqt[yaml]'"
            ) from exc
        with open(path, encoding="utf-8") as fh:
            return cls(yaml.safe_load(fh) or {})

    def _flatten(self, mapping: dict, prefix: str) -> None:
        for key, value in mapping.items():
            full = f"{prefix}{key}"
            if isinstance(value, dict):
                self._flatten(value, prefix=f"{full}.")
            else:
                self._flat[full] = value

    def resolve(self, name: str) -> str:
        try:
            return self._flat[name]
        except KeyError as exc:
            close = [k for k in self._flat if name.split(".")[0] in k][:5]
            raise LiberaQtError(
                f"'{name}' is not in the object map",
                hint=f"did you mean one of: {close}" if close else "check objects.yaml",
            ) from exc

    def __len__(self) -> int:
        return len(self._flat)

    def items(self):
        return self._flat.items()


class Session:
    def __init__(self, transport: Transport, object_map: ObjectMap | None = None,
                 default_timeout: float = 5.0, slowmo: float = 0.0):
        self.transport = transport
        self.object_map = object_map or ObjectMap()
        self.timeouts = TimeoutPolicy(default_timeout)
        self.slowmo = slowmo
        self.idle_options = {"quiet_ms": 50, "animations": True, "network": False}

    def call(self, cmd: str, params: dict | None = None, timeout: float | None = None,
             selector: Any = None) -> Any:
        if self.slowmo:
            import time
            time.sleep(self.slowmo)
        return self.transport.call(cmd, params, timeout=self.timeouts.resolve(timeout) + 5.0,
                                   selector=selector)

    def wait_for_idle(self, quiet_ms: int | None = None, animations: bool | None = None,
                      network: bool | None = None, timeout: float | None = None) -> None:
        opts = dict(self.idle_options)
        if quiet_ms is not None:
            opts["quiet_ms"] = quiet_ms
        if animations is not None:
            opts["animations"] = animations
        if network is not None:
            opts["network"] = network
        self.call(Cmd.WAIT_IDLE, opts, timeout=timeout)

    def grab(self, handle: str | None = None, path: str | None = None) -> bytes:
        result = self.call(Cmd.GRAB, {"handle": handle})
        data = base64.b64decode(result.get("png", ""))
        if path:
            with open(path, "wb") as fh:
                fh.write(data)
        return data
