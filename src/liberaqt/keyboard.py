"""Window-scoped keyboard input, for cases where no locator is the right target."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .protocol import Cmd

if TYPE_CHECKING:
    from .session import Session


class Keyboard:
    """Raw keyboard input aimed at a window rather than a specific widget.

    Reach it through :attr:`Window.keyboard`. Input goes to whatever currently has focus, so
    prefer a locator's own :meth:`~liberaqt.locator.Locator.press` when there is an object to
    target: it waits for that object to be ready first.

    Args:
        session: The wire session used for every command.
        window_handle: Window to deliver input to. ``None`` targets the focused window.
    """

    def __init__(self, session: Session, window_handle: str | None = None):
        self._session = session
        self._window = window_handle

    def press(self, key: str, count: int = 1) -> None:
        """Press and release a key or chord, then wait for the UI to settle.

        Args:
            key: Key name or chord, e.g. ``"Ctrl+S"``, ``"Enter"``, ``"F5"``, ``"Alt+Shift+Tab"``.
            count: How many times to press it.
        """
        self._session.call(Cmd.KEY, {"handle": self._window, "key": key, "count": count})
        self._session.wait_for_idle()

    def type(self, text: str, delay: float = 0.0) -> None:
        """Type text character by character, then wait for the UI to settle.

        Args:
            text: Text to type.
            delay: Seconds between keystrokes, for applications that debounce input.
        """
        self._session.call(Cmd.TYPE_TEXT,
                           {"handle": self._window, "text": text,
                            "delay_ms": int(delay * 1000)})
        self._session.wait_for_idle()

    def down(self, key: str) -> None:
        """Press a key and hold it.

        Pair with :meth:`up` for interactions that need a modifier held across several actions,
        such as ctrl-clicking a series of rows. The key stays held until released, including if
        the test fails in between.

        Args:
            key: Key name, e.g. ``"Shift"``.
        """
        self._session.call(Cmd.PRESS, {"handle": self._window, "key": key})

    def up(self, key: str) -> None:
        """Release a held key.

        Args:
            key: Key name, e.g. ``"Shift"``.
        """
        self._session.call(Cmd.RELEASE, {"handle": self._window, "key": key})