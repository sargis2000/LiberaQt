"""Window-scoped mouse input.

Coordinate-based interaction is supported because sometimes there is no object to target
(custom-painted canvases, chart areas). It is deliberately not the ergonomic path -- prefer
locators, which survive layout changes.
"""

from __future__ import annotations

from typing import List, Optional

from .protocol import Cmd


class Mouse:
    def __init__(self, session: "Session", window_handle: Optional[str] = None):
        self._session = session
        self._window = window_handle

    def click(self, x: int, y: int, button: str = "left",
              modifiers: Optional[List[str]] = None, count: int = 1) -> None:
        self._session.call(Cmd.CLICK, {"handle": self._window, "pos": [x, y],
                                       "button": button, "modifiers": modifiers or [],
                                       "count": count})
        self._session.wait_for_idle()

    def move(self, x: int, y: int) -> None:
        self._session.call(Cmd.HOVER, {"handle": self._window, "pos": [x, y]})

    def down(self, button: str = "left") -> None:
        self._session.call(Cmd.PRESS, {"handle": self._window, "button": button})

    def up(self, button: str = "left") -> None:
        self._session.call(Cmd.RELEASE, {"handle": self._window, "button": button})

    def drag(self, from_xy: tuple, to_xy: tuple, steps: int = 10) -> None:
        self._session.call(Cmd.DRAG, {"handle": self._window, "from_pos": list(from_xy),
                                      "to_pos": list(to_xy), "steps": steps})
        self._session.wait_for_idle()

    def wheel(self, x: int, y: int, dx: int = 0, dy: int = 0) -> None:
        self._session.call(Cmd.WHEEL, {"handle": self._window, "pos": [x, y],
                                       "dx": dx, "dy": dy})
        self._session.wait_for_idle()
