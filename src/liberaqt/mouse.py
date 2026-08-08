"""Window-scoped mouse input.

Coordinate-based interaction is supported because sometimes there is no object to target
(custom-painted canvases, chart areas). It is deliberately not the ergonomic path -- prefer
locators, which survive layout changes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .protocol import Cmd

if TYPE_CHECKING:
    from .session import Session


class Mouse:
    """Raw mouse input at window coordinates.

    Reach it through :attr:`Window.mouse`. Coordinates are relative to the window's top-left
    corner, and nothing here waits for actionability: there is no object to check.

    Args:
        session: The wire session used for every command.
        window_handle: Window the coordinates are relative to. ``None`` targets the screen.
    """

    def __init__(self, session: Session, window_handle: str | None = None):
        self._session = session
        self._window = window_handle

    def click(self, x: int, y: int, button: str = "left",
              modifiers: list[str] | None = None, count: int = 1) -> None:
        """Click at a point, then wait for the UI to settle.

        Args:
            x: Horizontal position in window pixels.
            y: Vertical position in window pixels.
            button: ``"left"``, ``"right"`` or ``"middle"``.
            modifiers: Held modifiers, e.g. ``["Ctrl"]``.
            count: Number of clicks; ``2`` is a double click.
        """
        self._session.call(Cmd.CLICK, {"handle": self._window, "pos": [x, y],
                                       "button": button, "modifiers": modifiers or [],
                                       "count": count})
        self._session.wait_for_idle()

    def move(self, x: int, y: int) -> None:
        """Move the pointer without pressing anything.

        Args:
            x: Horizontal position in window pixels.
            y: Vertical position in window pixels.
        """
        self._session.call(Cmd.HOVER, {"handle": self._window, "pos": [x, y]})

    def down(self, button: str = "left") -> None:
        """Press and hold a button at the current position.

        Pair with :meth:`up` to build gestures that :meth:`drag` does not cover. The button
        stays held until released, including if the test fails in between.

        Args:
            button: ``"left"``, ``"right"`` or ``"middle"``.
        """
        self._session.call(Cmd.PRESS, {"handle": self._window, "button": button})

    def up(self, button: str = "left") -> None:
        """Release a held button at the current position.

        Args:
            button: ``"left"``, ``"right"`` or ``"middle"``.
        """
        self._session.call(Cmd.RELEASE, {"handle": self._window, "button": button})

    def drag(self, from_xy: tuple, to_xy: tuple, steps: int = 10) -> None:
        """Press, move in steps, and release.

        Args:
            from_xy: ``(x, y)`` to start from.
            to_xy: ``(x, y)`` to finish at.
            steps: Intermediate move events. More steps look more human, which matters for
                widgets that only start a drag after a movement threshold.
        """
        self._session.call(Cmd.DRAG, {"handle": self._window, "from_pos": list(from_xy),
                                      "to_pos": list(to_xy), "steps": steps})
        self._session.wait_for_idle()

    def wheel(self, x: int, y: int, dx: int = 0, dy: int = 0) -> None:
        """Scroll the wheel over a point.

        Args:
            x: Horizontal position in window pixels.
            y: Vertical position in window pixels.
            dx: Horizontal scroll in wheel steps.
            dy: Vertical scroll in wheel steps. Positive scrolls down.
        """
        self._session.call(Cmd.WHEEL, {"handle": self._window, "pos": [x, y],
                                       "dx": dx, "dy": dy})
        self._session.wait_for_idle()