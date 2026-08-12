"""LiberaQT -- Playwright-style UI automation for Qt desktop applications.

A small C++ agent is loaded into the application under test through Qt's generic plugin
mechanism, exposing its live ``QObject`` tree over a loopback JSON socket. This package turns
that into locators, assertions and a pytest plugin.

Example:
    ::

        from liberaqt import liberaqt, expect

        with liberaqt() as qd:
            app = qd.launch("./build/myapp")
            win = app.window(title="Login")
            win.locator("QLineEdit#username").fill("sargis")
            win.locator("QPushButton[text='Log in']").click()
            expect(win.locator("QLabel#status")).to_have_text("Welcome, sargis")

No ``sleep()`` is needed anywhere: every action waits for its target to exist, become visible and
become enabled, then waits for the UI to settle afterwards.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

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
    """Entry point. Owns every application it launches and cleans them up on exit.

    Use it as a context manager so a crashed test cannot leave an orphaned application process
    holding a port.

    Attributes:
        default_timeout: Seconds each action waits for its target to become actionable.
        slowmo: Seconds to sleep before every command. Useful for watching a test run.
        trace: Whether to log every protocol message, for debugging the driver itself.
        input_mode: How input reaches the application; see
            :meth:`~liberaqt.application.Application.set_input_mode`. ``None`` leaves the agent on
            its own default, which is ``"native"``, and costs no round trip.
    """

    def __init__(self, default_timeout: float = 5.0, slowmo: float = 0.0, trace: bool = False,
                 input_mode: str | None = None):
        self.default_timeout = default_timeout
        self.slowmo = slowmo
        self.trace = trace
        self.input_mode = input_mode
        self._apps: list[Application] = []

    # ------------------------------------------------------------------ launching
    def launch(self, executable: str, args: list[str] | None = None,
               cwd: str | None = None, env: dict[str, str] | None = None,
               qt: str | None = None, object_map: str | None = None,
               timeout: float = 30.0, headless: bool = False,
               record: bool = False) -> Application:
        """Start an application with the agent injected, and wait for it to call home.

        Args:
            executable: Path to the application binary, or a name on ``PATH``.
            args: Arguments passed to the application itself.
            cwd: Working directory for the new process.
            env: Base environment. The injection variables are added to a copy of it, so passing
                this does not lose ``PATH`` unless you omit it yourself.
            qt: Force a Qt version such as ``"6.7"`` instead of detecting it from the binary.
                Needed when detection fails, for example for a statically linked build.
            object_map: Path to a YAML object map, which makes :meth:`Window.obj` usable.
            timeout: Seconds to wait for the agent to report its port.
            headless: Run with the offscreen QPA platform. Linux only.
            record: Start the agent in recorder mode, as ``liberaqt record`` does.

        Returns:
            A connected :class:`~liberaqt.application.Application`.

        Raises:
            LaunchError: The executable is missing, exited early, or the agent never connected.
            AgentMismatchError: No installed agent matches the application's Qt build.
        """
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
        self._register(app)
        return app

    def connect(self, port: int, token: str = "", object_map: str | None = None) -> Application:
        """Attach to an agent that is already listening.

        For applications that embed the agent themselves rather than being launched by us; see
        ``docs/INJECTION.md`` section 4. The process lifetime is not ours, so :meth:`close`
        disconnects but never terminates it.

        Args:
            port: Loopback port the agent is listening on.
            token: Value of ``LIBERAQT_TOKEN`` the agent was started with.
            object_map: Path to a YAML object map.

        Returns:
            A connected :class:`~liberaqt.application.Application`.

        Raises:
            ConnectionLostError: Nothing is listening, or the token was rejected.
        """
        transport = Transport(port=port, token=token, trace=self.trace)
        transport.connect(timeout=10.0)
        session = Session(transport, ObjectMap.load(object_map),
                          default_timeout=self.default_timeout, slowmo=self.slowmo)
        app = Application(session)
        self._register(app)
        return app

    def _register(self, app: Application) -> None:
        """Track an application for cleanup, and apply the session-wide options to it."""
        self._apps.append(app)
        if self.input_mode is not None:
            app.set_input_mode(self.input_mode)

    # ------------------------------------------------------------------ settings
    def set_default_timeout(self, timeout: float) -> None:
        """Change the default action timeout, including for already-launched applications.

        Args:
            timeout: Seconds.
        """
        self.default_timeout = timeout
        for app in self._apps:
            app._session.timeouts.default = timeout

    @contextlib.contextmanager
    def timeout(self, seconds: float) -> Iterator[None]:
        """Temporarily change the default timeout, restoring it on exit.

        Args:
            seconds: Timeout to use inside the block.

        Yields:
            None.
        """
        previous = self.default_timeout
        self.set_default_timeout(seconds)
        try:
            yield
        finally:
            self.set_default_timeout(previous)

    # ------------------------------------------------------------------ lifecycle
    def close(self) -> None:
        """Close every application this instance launched, newest first.

        Never raises: a teardown failure must not mask whatever the test was actually reporting.
        """
        for app in reversed(self._apps):
            try:
                app.close()
            except Exception:  # noqa: BLE001 - teardown must never mask the real failure
                pass
        self._apps.clear()

    def __enter__(self) -> LiberaQt:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def liberaqt(**kwargs: Any) -> LiberaQt:
    """Construct a :class:`LiberaQt`.

    A lowercase alias, purely so that ``with liberaqt() as qd:`` reads well at the top of a test.

    Args:
        **kwargs: Passed straight to :class:`LiberaQt`.

    Returns:
        A new :class:`LiberaQt`.
    """
    return LiberaQt(**kwargs)