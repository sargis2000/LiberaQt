"""Application handle: the top-level object a test interacts with."""

from __future__ import annotations

from typing import Any, Callable, List, Optional

from .errors import QtDriverError
from .protocol import Cmd, Event
from .session import Session
from .waits import retry
from .window import Window


class Application:
    def __init__(self, session: Session, process: Optional["LaunchedProcess"] = None):
        self._session = session
        self._process = process
        self._closed = False
        self._events: List[dict] = []
        for name in (Event.WINDOW_OPENED, Event.WINDOW_CLOSED, Event.APP_MESSAGE,
                     Event.APP_ABOUT_TO_QUIT):
            session.transport.on(name, lambda data, n=name: self._events.append({n: data}))

    # ------------------------------------------------------------------ info
    @property
    def info(self) -> dict:
        return self._session.call(Cmd.INFO)

    @property
    def pid(self) -> Optional[int]:
        return self._process.pid if self._process else self._session.transport.hello.get("pid")

    @property
    def qt_version(self) -> str:
        return self._session.transport.hello.get("qt", "")

    @property
    def logs(self) -> List[str]:
        return list(self._process.log_lines) if self._process else []

    @property
    def is_running(self) -> bool:
        if self._process is not None:
            return self._process.is_running
        return self._session.transport.is_connected

    # ------------------------------------------------------------------ windows
    @property
    def windows(self) -> List[Window]:
        entries = self._session.call(Cmd.WINDOW_LIST) or []
        return [Window(self._session, e["handle"], e) for e in entries]

    def window(self, title: Optional[str] = None, index: int = 0,
               timeout: Optional[float] = None) -> Window:
        """Wait for and return a window. Without ``title``, returns the ``index``-th window."""
        timeout = self._session.timeouts.resolve(timeout)

        def once() -> Window:
            entries = self._session.call(Cmd.WINDOW_LIST) or []
            if title is not None:
                entries = [e for e in entries if e.get("title") == title]
            if not entries:
                raise QtDriverError(
                    f"no window matching title={title!r}",
                    data={"available": [e.get("title") for e in
                                        (self._session.call(Cmd.WINDOW_LIST) or [])]},
                )
            return Window(self._session, entries[index]["handle"], entries[index])

        return retry(once, timeout=timeout, description=f"window(title={title!r})")

    def wait_for_window(self, title: Optional[str] = None,
                        timeout: Optional[float] = None) -> Window:
        return self.window(title=title, timeout=timeout)

    # ------------------------------------------------------------------ misc
    def screenshot(self, path: Optional[str] = None) -> bytes:
        return self._session.grab(path=path)

    def wait_for_idle(self, **kwargs: Any) -> None:
        self._session.wait_for_idle(**kwargs)

    def on(self, event: str, callback: Callable[[dict], None]) -> None:
        self._session.transport.on(event, callback)

    def evaluate(self, expression: str, handle: Optional[str] = None) -> Any:
        return self._session.call(Cmd.QUICK_EVALUATE,
                                  {"handle": handle, "expression": expression}).get("value")

    # ------------------------------------------------------------------ lifecycle
    def close(self, timeout: float = 5.0) -> int:
        if self._closed:
            return 0
        self._closed = True
        try:
            self._session.call(Cmd.QUIT, {"force": False}, timeout=timeout)
        except QtDriverError:
            pass
        self._session.transport.close()
        if self._process is not None:
            return self._process.terminate(timeout=timeout)
        return 0

    def kill(self) -> int:
        self._closed = True
        self._session.transport.close()
        if self._process is not None:
            self._process.popen.kill()
            return self._process.popen.wait(timeout=5)
        return 0

    def __enter__(self) -> "Application":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"<Application pid={self.pid} qt={self.qt_version}>"
