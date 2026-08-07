"""Window-scoped keyboard input, for cases where no locator is the right target."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .protocol import Cmd

if TYPE_CHECKING:
    from .session import Session


class Keyboard:
    def __init__(self, session: Session, window_handle: str | None = None):
        self._session = session
        self._window = window_handle

    def press(self, key: str, count: int = 1) -> None:
        """``"Ctrl+S"``, ``"Enter"``, ``"F5"``, ``"Alt+Shift+Tab"``."""
        self._session.call(Cmd.KEY, {"handle": self._window, "key": key, "count": count})
        self._session.wait_for_idle()

    def type(self, text: str, delay: float = 0.0) -> None:
        self._session.call(Cmd.TYPE_TEXT,
                           {"handle": self._window, "text": text,
                            "delay_ms": int(delay * 1000)})
        self._session.wait_for_idle()

    def down(self, key: str) -> None:
        self._session.call(Cmd.PRESS, {"handle": self._window, "key": key})

    def up(self, key: str) -> None:
        self._session.call(Cmd.RELEASE, {"handle": self._window, "key": key})
