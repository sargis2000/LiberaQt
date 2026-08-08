"""Window handle. Also serves as the root Locator for its object tree."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import selectors as sel
from .locator import Locator, SelectorLike
from .protocol import Cmd

if TYPE_CHECKING:
    from .keyboard import Keyboard
    from .mouse import Mouse
    from .session import Session


class Window(Locator):
    """A top-level window, and the root of the object tree searched inside it.

    Being a :class:`~liberaqt.locator.Locator` itself means ``win.locator(...)`` searches this
    window's subtree. A window is never among its own descendants, so a selector can never match
    the window it is searched in; reach windows through :meth:`Application.window` instead.

    Args:
        session: The wire session used for every command.
        handle: The agent's handle for this window.
        info: Details already known from ``window.list``, so :meth:`__repr__` costs no round trip.
    """

    def __init__(self, session: Session, handle: str, info: dict | None = None):
        super().__init__(session, sel.Selector(steps=[], source=f"<window {handle}>"), handle)
        self._handle = handle
        self._cached = info or {}

    # ------------------------------------------------------------------ identity
    def resolve(self, timeout: float | None = None) -> str:
        """Return this window's handle.

        Overrides the locator's search: a window is already resolved, so there is nothing to
        wait for and ``timeout`` is ignored.

        Args:
            timeout: Accepted for signature compatibility, and unused.

        Returns:
            The agent handle for this window.
        """
        return self._handle

    def _info(self) -> dict:
        return self._session.call(Cmd.OBJ_INFO, {"handle": self._handle})

    @property
    def title(self) -> str:
        """Current window title."""
        return self._info().get("title", "")

    @property
    def geometry(self) -> tuple:
        """Client-area geometry as ``(x, y, width, height)``.

        Excludes the window frame, so on a decorated window this differs from the ``pos``
        property by the title bar height. Write geometry with :meth:`resize` and :meth:`move`.
        """
        return tuple(self._info().get("geometry", ()))

    @property
    def is_active(self) -> bool:
        """Whether this window currently has window-manager focus."""
        return bool(self._info().get("active"))

    @property
    def kind(self) -> str:
        """``"widget"`` for a QWidget window, ``"quick"`` for a QQuickWindow."""
        return self._info().get("window_type", "widget")

    # ------------------------------------------------------------------ actions
    def activate(self) -> None:
        """Raise this window and ask the window manager to focus it.

        Whether focus is actually granted is the window manager's decision; headless and
        unmanaged displays commonly refuse.
        """
        # activateWindow is a plain public function, not a slot, so it is invisible to the
        # meta-object system; the agent exposes raise + activate together as a synthetic method.
        self._session.call(Cmd.INVOKE, {"handle": self._handle, "method": "__activate", "args": []})

    def close(self) -> None:
        """Close this window, as clicking its close button would.

        The application may refuse, for example to prompt about unsaved changes.
        """
        self._session.call(Cmd.INVOKE, {"handle": self._handle, "method": "close", "args": []})

    def resize(self, width: int, height: int) -> None:
        """Resize the window.

        Args:
            width: New width in pixels.
            height: New height in pixels.
        """
        # QWidget::resize is not a slot, so it is unreachable through object.invoke. It is the
        # setter behind the `size` property, though, which is reachable. Same for move/`pos`.
        self._session.call(Cmd.SET_PROPERTY,
                           {"handle": self._handle, "name": "size", "value": [width, height]})

    def move(self, x: int, y: int) -> None:
        """Move the window's top-left corner, frame included.

        Args:
            x: New horizontal position in screen pixels.
            y: New vertical position in screen pixels.
        """
        self._session.call(Cmd.SET_PROPERTY,
                           {"handle": self._handle, "name": "pos", "value": [x, y]})

    def screenshot(self, path: str | None = None) -> bytes:
        """Grab this window as a PNG.

        Args:
            path: Where to write the image. When omitted, the bytes are only returned.

        Returns:
            The PNG image bytes.
        """
        return self._session.grab(handle=self._handle, path=path)

    # ------------------------------------------------------------------ lookup
    def locator(self, selector: SelectorLike = None, /, **kwargs: Any) -> Locator:
        """Build a locator searching this window's subtree.

        Nothing is resolved yet: locators are lazy, and the search happens when an action or
        query needs it.

        Args:
            selector: Selector string, dict, or parsed ``Selector``.
            **kwargs: Selector fields given as keywords, e.g. ``type="QPushButton"``.

        Returns:
            An unresolved :class:`~liberaqt.locator.Locator`.
        """
        return Locator(self._session, sel.coerce(selector, **kwargs), self._handle)

    def obj(self, name: str) -> Locator:
        """Look a selector up in the object map by symbolic name.

        Args:
            name: Dotted name such as ``"login.submit"``.

        Returns:
            A locator for the mapped selector.

        Raises:
            LiberaQtError: The name is not in the map. The message suggests near matches.
        """
        return self.locator(self._session.object_map.resolve(name))

    def menu(self, path: str) -> MenuAction:
        """Address a menu entry by path.

        Args:
            path: Slash-separated path such as ``"File/Recent/foo.txt"``.

        Returns:
            A :class:`MenuAction` that can be triggered or queried.
        """
        return MenuAction(self._session, self._handle, path)

    @property
    def keyboard(self) -> Keyboard:
        """Window-scoped keyboard, for input not aimed at a particular widget."""
        from .keyboard import Keyboard
        return Keyboard(self._session, self._handle)

    @property
    def mouse(self) -> Mouse:
        """Window-scoped mouse, for gestures that no single locator describes."""
        from .mouse import Mouse
        return Mouse(self._session, self._handle)

    def tree(self, depth: int = -1, visual_only: bool = True) -> dict:
        """Dump this window's object tree.

        Used by ``liberaqt inspect`` and by failure reports. Each node carries its handle, class,
        objectName and display text, which is everything a selector is built from.

        Args:
            depth: How many levels to descend. ``-1`` means the whole tree.
            visual_only: Skip non-visual ``QObject`` children, which are rarely what a test is
                looking for and vastly outnumber the widgets.

        Returns:
            The root node, with children nested under ``"children"``.
        """
        return self._session.call(Cmd.TREE, {"root": self._handle, "depth": depth,
                                             "visual_only": visual_only})

    def __repr__(self) -> str:
        title = self._cached.get("title", self._handle)
        return f"<Window {title!r}>"


class MenuAction:
    """A menu entry addressed by slash-separated path, e.g. ``"File/Recent/foo.txt"``.

    Args:
        session: The wire session used for every command.
        window_handle: Window whose menu bar is searched.
        path: Slash-separated path to the entry.
    """

    def __init__(self, session: Session, window_handle: str, path: str):
        self._session = session
        self._window = window_handle
        self._path = path

    def trigger(self) -> None:
        """Activate the entry, opening each parent menu on the way, then wait for the UI to settle.

        Raises:
            ObjectNotFoundError: No entry matches the path.
        """
        self._session.call(Cmd.MENU_TRIGGER, {"window": self._window, "path": self._path})
        self._session.wait_for_idle()

    @property
    def is_enabled(self) -> bool:
        """Whether the entry is currently enabled, without triggering it."""
        info = self._session.call(Cmd.MENU_TRIGGER,
                                  {"window": self._window, "path": self._path, "probe": True})
        return bool(info.get("enabled"))