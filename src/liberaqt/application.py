"""Application handle: the top-level object a test interacts with."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .errors import LiberaQtError, UnsupportedOperationError
from .protocol import Cmd, Event
from .session import Session
from .waits import retry
from .window import Window

if TYPE_CHECKING:
    from .launcher import LaunchedProcess


class Application:
    """A running application under test.

    Obtained from :meth:`LiberaQt.launch` or :meth:`LiberaQt.connect`, never constructed
    directly. Everything a test does starts here, usually by finding a window.

    Args:
        session: The wire session used for every command.
        process: The launched process, when we started it. ``None`` when attached to an agent
            that was already running, in which case its lifetime is not ours to end.
    """

    def __init__(self, session: Session, process: LaunchedProcess | None = None):
        self._session = session
        self._process = process
        self._closed = False
        self._events: list[dict] = []
        for name in (Event.WINDOW_OPENED, Event.WINDOW_CLOSED, Event.APP_MESSAGE,
                     Event.APP_ABOUT_TO_QUIT):
            session.transport.on(name, lambda data, n=name: self._events.append({n: data}))

    # ------------------------------------------------------------------ info
    @property
    def info(self) -> dict:
        """Live details from the agent: Qt version, process id, application name."""
        return self._session.call(Cmd.INFO)

    @property
    def pid(self) -> int | None:
        """Process id of the application, or ``None`` if the agent never reported one."""
        return self._process.pid if self._process else self._session.transport.hello.get("pid")

    @property
    def qt_version(self) -> str:
        """Qt version the application is running, e.g. ``"6.7.3"``, from the handshake."""
        return self._session.transport.hello.get("qt", "")

    @property
    def logs(self) -> list[str]:
        """Captured stdout and stderr, oldest first.

        Empty when attached to an already-running process, since its output goes wherever it was
        already going. Trimmed to the most recent few thousand lines.
        """
        return list(self._process.log_lines) if self._process else []

    @property
    def is_running(self) -> bool:
        """Whether the application is still alive."""
        if self._process is not None:
            return self._process.is_running
        return self._session.transport.is_connected

    # ------------------------------------------------------------------ settings
    def set_input_mode(self, mode: str) -> None:
        """Choose how mouse and keyboard events reach the application.

        ``"native"``, the default, hands each event to Qt at the same seam a platform plugin uses,
        so Qt routes it exactly as it routes a real one: it hit-tests for the receiver, tracks
        hover and enter/leave, holds the implicit grab between press and release, derives double
        clicks, moves focus to what was clicked, and refuses input to a window a modal dialog has
        disabled. Anything a person could not do -- clicking a widget that is scrolled out of
        sight, typing into a field behind a modal -- fails instead of quietly succeeding.

        ``"synthetic"`` delivers each event straight to one widget instead. None of the above
        happens, so a click does not focus what it hits and typing afterwards goes nowhere; in
        exchange it still reaches a target that is off-screen or covered. Useful for setting up
        state, and as an escape hatch when a target cannot be reached any other way.

        Args:
            mode: ``"native"`` or ``"synthetic"``.

        Raises:
            UnsupportedOperationError: The agent predates this option, so the mode it is using is
                not the one that was asked for.
        """
        result = self._session.call(Cmd.SET_OPTIONS, {"input_mode": mode}) or {}
        if "input_mode" not in (result.get("accepted") or []):
            raise UnsupportedOperationError(
                f"this agent ignores input_mode, so it is not running in {mode!r} mode; "
                "rebuild the agent to choose the input delivery mode"
            )

    # ------------------------------------------------------------------ windows
    @property
    def windows(self) -> list[Window]:
        """Every visible top-level window, widget and Qt Quick alike."""
        entries = self._session.call(Cmd.WINDOW_LIST) or []
        return [Window(self._session, e["handle"], e) for e in entries]

    def window(self, title: str | None = None, index: int = 0,
               timeout: float | None = None) -> Window:
        """Wait for a window and return it.

        Retries until the window appears, so it is safe to call immediately after an action that
        opens a dialog.

        Args:
            title: Exact window title to match. Without it, any window matches.
            index: Which of the matching windows to take, in the agent's ordering.
            timeout: Seconds to wait. Defaults to the session timeout.

        Returns:
            The matching :class:`~liberaqt.window.Window`.

        Raises:
            TimeoutError: No window matched within the timeout. The message lists the titles
                that did exist, which is usually enough to spot a typo.
        """
        timeout = self._session.timeouts.resolve(timeout)

        def once() -> Window:
            entries = self._session.call(Cmd.WINDOW_LIST) or []
            if title is not None:
                entries = [e for e in entries if e.get("title") == title]
            if not entries:
                raise LiberaQtError(
                    f"no window matching title={title!r}",
                    data={"available": [e.get("title") for e in
                                        (self._session.call(Cmd.WINDOW_LIST) or [])]},
                )
            return Window(self._session, entries[index]["handle"], entries[index])

        return retry(once, timeout=timeout, description=f"window(title={title!r})")

    def wait_for_window(self, title: str | None = None,
                        timeout: float | None = None) -> Window:
        """Alias for :meth:`window`, for when waiting is the point of the call.

        Args:
            title: Exact window title to match.
            timeout: Seconds to wait. Defaults to the session timeout.

        Returns:
            The matching :class:`~liberaqt.window.Window`.
        """
        return self.window(title=title, timeout=timeout)

    # ------------------------------------------------------------------ misc
    def screenshot(self, path: str | None = None) -> bytes:
        """Grab the whole application as a PNG.

        Args:
            path: Where to write the image. When omitted, the bytes are only returned.

        Returns:
            The PNG image bytes.
        """
        return self._session.grab(path=path)

    def wait_for_idle(self, **kwargs: Any) -> None:
        """Block until the UI settles.

        Actions already do this, so it is only needed after something the driver did not perform
        itself, such as work triggered by an invoked slot.

        Args:
            **kwargs: Passed to :meth:`Session.wait_for_idle` (``quiet_ms``, ``animations``,
                ``network``, ``timeout``).
        """
        self._session.wait_for_idle(**kwargs)

    def on(self, event: str, callback: Callable[[dict], None]) -> None:
        """Subscribe to an agent event such as ``window.opened``.

        The callback runs on the transport's reader thread, so it should hand work off rather
        than block or call back into the driver.

        Args:
            event: Event name; see :class:`~liberaqt.protocol.Event`.
            callback: Called with the event's data payload.
        """
        self._session.transport.on(event, callback)

    def evaluate(self, expression: str, handle: str | None = None) -> Any:
        """Evaluate a QML/JavaScript expression. Qt Quick only.

        Args:
            expression: The expression to evaluate.
            handle: Object whose QML context to evaluate in. Defaults to the root context.

        Returns:
            The decoded result.

        Raises:
            UnsupportedOperationError: The application is not a Qt Quick one.
        """
        return self._session.call(Cmd.QUICK_EVALUATE,
                                  {"handle": handle, "expression": expression}).get("value")

    # ------------------------------------------------------------------ lifecycle
    def close(self, timeout: float = 5.0) -> int:
        """Ask the application to quit, then terminate it if it does not.

        Idempotent, and never raises: it is normal for the socket to drop while the application
        is on its way out.

        Args:
            timeout: Seconds to allow for a graceful exit before terminating.

        Returns:
            The process exit code, or 0 when we did not own the process.
        """
        if self._closed:
            return 0
        self._closed = True
        try:
            self._session.call(Cmd.QUIT, {"force": False}, timeout=timeout)
        except LiberaQtError:
            pass
        self._session.transport.close()
        if self._process is not None:
            return self._process.terminate(timeout=timeout)
        return 0

    def kill(self) -> int:
        """Terminate the application immediately, without asking it to quit.

        For a hung application that will not respond to :meth:`close`.

        Returns:
            The process exit code, or 0 when we did not own the process.
        """
        self._closed = True
        self._session.transport.close()
        if self._process is not None:
            self._process.popen.kill()
            return self._process.popen.wait(timeout=5)
        return 0

    def __enter__(self) -> Application:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"<Application pid={self.pid} qt={self.qt_version}>"