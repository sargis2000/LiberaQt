"""LiberaQT -- Playwright-style UI automation for Qt desktop applications.

    from liberaqt import liberaqt, expect

    with liberaqt() as qd:
        app = qd.launch("./build/myapp")
        win = app.window(title="Login")
        win.locator("QLineEdit#username").fill("sargis")
        win.locator("QPushButton[text='Log in']").click()
        expect(win.locator("QLabel#status")).to_have_text("Welcome, sargis")
"""

from __future__ import annotations

import contextlib
from typing import Any, Dict, List, Optional

from . import selectors
from .application import Application
from .errors import (
    AgentMismatchError,
    AmbiguousSelectorError,
    ConnectionLostError,
    InvalidSelectorError,
    LaunchError,
    LiberaQtError,
    NotActionableError,
    ObjectNotFoundError,
    ProtocolError,
    SelectorError,
    StaleObjectError,
    UnsupportedOperationError,
)
from .errors import TimeoutError as LiberaQtTimeoutError
from .expect import expect
from .locator import Locator
from .protocol import PROTOCOL_VERSION
from .session import ObjectMap, Session
from .transport import Transport
from .window import Window

__version__ = "0.1.0.dev0"
__all__ = [
    "liberaqt", "LiberaQt", "Application", "Window", "Locator", "expect", "selectors",
    "ObjectMap", "LiberaQtError", "LaunchError", "AgentMismatchError", "ConnectionLostError",
    "ProtocolError", "SelectorError", "InvalidSelectorError", "ObjectNotFoundError",
    "AmbiguousSelectorError", "StaleObjectError", "NotActionableError", "LiberaQtTimeoutError",
    "UnsupportedOperationError", "PROTOCOL_VERSION", "__version__",
]


class LiberaQt:
    """Entry point. Owns every application it launches and cleans them up on exit."""

    def __init__(self, default_timeout: float = 5.0, slowmo: float = 0.0, trace: bool = False):
        self.default_timeout = default_timeout
        self.slowmo = slowmo
        self.trace = trace
        self._apps: List[Application] = []

    # ------------------------------------------------------------------ launching
    def launch(self, executable: str, args: Optional[List[str]] = None,
               cwd: Optional[str] = None, env: Optional[Dict[str, str]] = None,
               qt: Optional[str] = None, object_map: Optional[str] = None,
               timeout: float = 30.0, headless: bool = False,
               record: bool = False) -> Application:
        from .launcher import launch as _launch

        process = _launch(executable, args=args, cwd=cwd, env=env, qt=qt,
                          timeout=timeout, headless=headless, record=record)
        transport = Transport(port=process.port, token=process.token, trace=self.trace)
        try:
            transport.connect(timeout=10.0)
        except LiberaQtError:
            process.terminate()
            raise
        session = Session(transport, ObjectMap.load(object_map),
                          default_timeout=self.default_timeout, slowmo=self.slowmo)
        app = Application(session, process)
        self._apps.append(app)
        return app

    def connect(self, port: int, token: str = "", object_map: Optional[str] = None) -> Application:
        """Attach to an agent that is already listening (see docs/INJECTION.md section 4)."""
        transport = Transport(port=port, token=token, trace=self.trace)
        transport.connect(timeout=10.0)
        session = Session(transport, ObjectMap.load(object_map),
                          default_timeout=self.default_timeout, slowmo=self.slowmo)
        app = Application(session)
        self._apps.append(app)
        return app

    # ------------------------------------------------------------------ settings
    def set_default_timeout(self, timeout: float) -> None:
        self.default_timeout = timeout
        for app in self._apps:
            app._session.timeouts.default = timeout

    @contextlib.contextmanager
    def timeout(self, seconds: float):
        previous = self.default_timeout
        self.set_default_timeout(seconds)
        try:
            yield
        finally:
            self.set_default_timeout(previous)

    # ------------------------------------------------------------------ lifecycle
    def close(self) -> None:
        for app in reversed(self._apps):
            try:
                app.close()
            except Exception:  # noqa: BLE001 - teardown must never mask the real failure
                pass
        self._apps.clear()

    def __enter__(self) -> "LiberaQt":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def liberaqt(**kwargs: Any) -> LiberaQt:
    """Convenience constructor so ``with liberaqt() as qd:`` reads well."""
    return LiberaQt(**kwargs)
