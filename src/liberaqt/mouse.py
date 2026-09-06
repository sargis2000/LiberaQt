# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Sargis Khachatryan
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
              modifiers: list[str] | None = None, count: int = 1,
              mode: str | None = None) -> None:
        """Click at a point, then wait for the UI to settle.

        Args:
            x: Horizontal position in window pixels.
            y: Vertical position in window pixels.
            button: ``"left"``, ``"right"`` or ``"middle"``.
            modifiers: Held modifiers, e.g. ``["Ctrl"]``.
            count: Number of clicks; ``2`` is a double click.
            mode: ``"native"`` or ``"synthetic"`` for this call only, overriding the session
                default set by :meth:`~liberaqt.application.Application.set_input_mode`.
        """
        self._session.call(Cmd.CLICK, self._params(pos=[x, y], button=button,
                                                   modifiers=modifiers or [], count=count,
                                                   mode=mode))
        self._session.wait_for_idle()

    def move(self, x: int, y: int, mode: str | None = None) -> None:
        """Move the pointer without pressing anything.

        Args:
            x: Horizontal position in window pixels.
            y: Vertical position in window pixels.
            mode: ``"native"`` or ``"synthetic"`` for this call only.
        """
        self._session.call(Cmd.HOVER, self._params(pos=[x, y], mode=mode))

    def down(self, button: str = "left", mode: str | None = None) -> None:
        """Press and hold a button at the current position.

        Pair with :meth:`up` to build gestures that :meth:`drag` does not cover. The button
        stays held until released, including if the test fails in between.

        Args:
            button: ``"left"``, ``"right"`` or ``"middle"``.
            mode: ``"native"`` or ``"synthetic"`` for this call only.
        """
        self._session.call(Cmd.PRESS, self._params(button=button, mode=mode))

    def up(self, button: str = "left", mode: str | None = None) -> None:
        """Release a held button at the current position.

        Args:
            button: ``"left"``, ``"right"`` or ``"middle"``.
            mode: ``"native"`` or ``"synthetic"`` for this call only.
        """
        self._session.call(Cmd.RELEASE, self._params(button=button, mode=mode))

    def drag(self, from_xy: tuple, to_xy: tuple, steps: int = 10,
             mode: str | None = None) -> None:
        """Press, move in steps, and release.

        Args:
            from_xy: ``(x, y)`` to start from.
            to_xy: ``(x, y)`` to finish at.
            steps: Intermediate move events. More steps look more human, which matters for
                widgets that only start a drag after a movement threshold.
            mode: ``"native"`` or ``"synthetic"`` for this call only.
        """
        self._session.call(Cmd.DRAG, self._params(from_pos=list(from_xy), to_pos=list(to_xy),
                                                  steps=steps, mode=mode))
        self._session.wait_for_idle()

    def wheel(self, x: int, y: int, dx: int = 0, dy: int = 0,
              mode: str | None = None) -> None:
        """Scroll the wheel over a point.

        Args:
            x: Horizontal position in window pixels.
            y: Vertical position in window pixels.
            dx: Horizontal scroll in wheel steps.
            dy: Vertical scroll in wheel steps. Positive scrolls down.
            mode: ``"native"`` or ``"synthetic"`` for this call only.
        """
        self._session.call(Cmd.WHEEL, self._params(pos=[x, y], dx=dx, dy=dy, mode=mode))
        self._session.wait_for_idle()

    def _params(self, **kw) -> dict:
        """Command parameters, dropping a `mode` that was not asked for.

        Omitted rather than sent as null, so the agent falls back to the session mode instead of
        having to treat an explicit null as "unset".
        """
        params = {"handle": self._window}
        params.update({k: v for k, v in kw.items() if not (k == "mode" and v is None)})
        return params