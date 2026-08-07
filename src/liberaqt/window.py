"""Window handle. Also serves as the root Locator for its object tree."""

from __future__ import annotations

from typing import Any, List, Optional

from . import selectors as sel
from .locator import Locator, SelectorLike
from .protocol import Cmd


class Window(Locator):
    def __init__(self, session: "Session", handle: str, info: Optional[dict] = None):
        super().__init__(session, sel.Selector(steps=[], source=f"<window {handle}>"), handle)
        self._handle = handle
        self._cached = info or {}

    # ------------------------------------------------------------------ identity
    def resolve(self, timeout: Optional[float] = None) -> str:
        return self._handle

    def _info(self) -> dict:
        return self._session.call(Cmd.OBJ_INFO, {"handle": self._handle})

    @property
    def title(self) -> str:
        return self._info().get("title", "")

    @property
    def geometry(self) -> tuple:
        return tuple(self._info().get("geometry", ()))

    @property
    def is_active(self) -> bool:
        return bool(self._info().get("active"))

    @property
    def kind(self) -> str:
        """``"widget"`` or ``"quick"``."""
        return self._info().get("window_type", "widget")

    # ------------------------------------------------------------------ actions
    def activate(self) -> None:
        self._session.call(Cmd.INVOKE, {"handle": self._handle, "method": "raise", "args": []})
        self._session.call(Cmd.INVOKE,
                           {"handle": self._handle, "method": "activateWindow", "args": []})

    def close(self) -> None:
        self._session.call(Cmd.INVOKE, {"handle": self._handle, "method": "close", "args": []})

    def resize(self, width: int, height: int) -> None:
        self._session.call(Cmd.INVOKE,
                           {"handle": self._handle, "method": "resize", "args": [width, height]})

    def move(self, x: int, y: int) -> None:
        self._session.call(Cmd.INVOKE,
                           {"handle": self._handle, "method": "move", "args": [x, y]})

    def screenshot(self, path: Optional[str] = None) -> bytes:
        return self._session.grab(handle=self._handle, path=path)

    # ------------------------------------------------------------------ lookup
    def locator(self, selector: SelectorLike = None, /, **kwargs: Any) -> Locator:
        return Locator(self._session, sel.coerce(selector, **kwargs), self._handle)

    def obj(self, name: str) -> Locator:
        """Look a selector up in the object map (see docs/SELECTORS.md section 7)."""
        return self.locator(self._session.object_map.resolve(name))

    def menu(self, path: str) -> "MenuAction":
        return MenuAction(self._session, self._handle, path)

    @property
    def keyboard(self) -> "Keyboard":
        from .keyboard import Keyboard
        return Keyboard(self._session, self._handle)

    @property
    def mouse(self) -> "Mouse":
        from .mouse import Mouse
        return Mouse(self._session, self._handle)

    def tree(self, depth: int = -1, visual_only: bool = True) -> dict:
        """Dump this window's object tree. Used by `liberaqt inspect` and failure reports."""
        return self._session.call(Cmd.TREE, {"root": self._handle, "depth": depth,
                                             "visual_only": visual_only})

    def __repr__(self) -> str:
        title = self._cached.get("title", self._handle)
        return f"<Window {title!r}>"


class MenuAction:
    """A menu entry addressed by slash-separated path, e.g. ``"File/Recent/foo.txt"``."""

    def __init__(self, session: "Session", window_handle: str, path: str):
        self._session = session
        self._window = window_handle
        self._path = path

    def trigger(self) -> None:
        self._session.call(Cmd.MENU_TRIGGER, {"window": self._window, "path": self._path})
        self._session.wait_for_idle()

    @property
    def is_enabled(self) -> bool:
        info = self._session.call(Cmd.MENU_TRIGGER,
                                  {"window": self._window, "path": self._path, "probe": True})
        return bool(info.get("enabled"))
