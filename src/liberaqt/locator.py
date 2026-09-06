# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Sargis Khachatryan
"""Lazy, chainable, auto-waiting element handle.

A ``Locator`` holds a selector, not an object. Nothing is sent over the wire until an action or a
state read happens, and resolution is retried until the timeout expires. This is what makes tests
survive the UI not being ready yet, which is the single largest source of flakiness in GUI tests.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, Union

from . import selectors as sel
from .errors import AmbiguousSelectorError, LiberaQtError, ObjectNotFoundError
from .keyboard import split_chords
from .protocol import Cmd, decode_value
from .waits import retry

if TYPE_CHECKING:
    from .session import Session

SelectorLike = Union[str, dict, "sel.Selector", None]

#: A key token inside :meth:`Locator.type` text: ``<Return>``, ``<Ctrl+A>``.
#: Only sequences that look like key chords are treated as keys, so ordinary text containing
#: ``<`` -- an HTML fragment, a comparison -- types literally. The second pattern is the same
#: thing with its one group around the whole token, which is what ``re.split`` needs to keep
#: the tokens in its output.


class Locator:
    """One (or one-of-many) objects in the application's object tree.

    Locators are lazy and cheap: building or chaining one performs no I/O. Resolution happens at
    action time and is retried, so a locator built before a dialog opens still works once it has.

    By default a locator is *strict*: resolving it when the selector matches several objects is
    an error rather than a silent pick of the first. Use :meth:`nth`, :attr:`first` or
    :attr:`last` to choose deliberately.

    Args:
        session: The wire session used for every command.
        selector: The parsed selector this locator stands for.
        root_handle: Object to search beneath. ``None`` searches from the application root.
        index: Which match to take, when one was chosen explicitly.
        strict: Whether an ambiguous match is an error.
        parent: Locator standing for the single object this one searches beneath. Set when
            chaining off a locator that had already been narrowed to one match, and resolved
            lazily at find time so that chaining still performs no I/O.
    """

    def __init__(self, session: Session, selector: sel.Selector,
                 root_handle: str | None = None, index: int | None = None,
                 strict: bool = True, parent: Locator | None = None):
        self._session = session
        self._selector = selector
        self._root = root_handle
        self._index = index
        self._strict = strict
        self._parent = parent

    # ------------------------------------------------------------------ chaining

    def locator(self, selector: SelectorLike = None, /, **kwargs: Any) -> Locator:
        """Build a locator for a descendant at any depth.

        Args:
            selector: Selector string, dict, or parsed ``Selector``.
            **kwargs: Selector fields given as keywords, e.g. ``type="QPushButton"``.

        Returns:
            A new unresolved locator scoped to this one's subtree.
        """
        child = sel.coerce(selector, **kwargs)
        if self._is_narrowed:
            return self._scoped_child(child)
        return Locator(self._session, sel.join(self._selector, child), self._root,
                       strict=self._strict)

    def child(self, selector: SelectorLike = None, /, **kwargs: Any) -> Locator:
        """Build a locator for a *direct* child, excluding deeper descendants.

        Args:
            selector: Selector string, dict, or parsed ``Selector``.
            **kwargs: Selector fields given as keywords.

        Returns:
            A new unresolved locator matching only immediate children.
        """
        child = sel.coerce(selector, **kwargs)
        child.steps[0].direct_child = True
        if self._is_narrowed:
            return self._scoped_child(child)
        return Locator(self._session, sel.join(self._selector, child), self._root,
                       strict=self._strict)

    @property
    def _is_narrowed(self) -> bool:
        """Whether this locator stands for one specific object rather than a match set.

        Chaining beneath such a locator has to *scope* to the object it found, not append a
        step to its selector: appending would re-run the whole search and quietly widen the
        result back out to every match. ``win.locator("QDockWidget").first.locator("QPushButton")``
        must mean "buttons in that one dock", not "buttons in any dock".
        """
        return self._index is not None

    def _scoped_child(self, child: sel.Selector) -> Locator:
        """A locator for ``child`` searched beneath whatever this one resolves to.

        The parent is resolved lazily, at find time, so chaining still performs no I/O.
        """
        return Locator(self._session, child, None, strict=self._strict, parent=self)

    def filter(self, *, has: SelectorLike = None, has_text: str | None = None,
               has_not: SelectorLike = None) -> Locator:
        """Narrow the match set without an extra round trip.

        The conditions are folded into the selector and evaluated by the agent, so filtering
        costs nothing beyond the search that was already going to happen.

        Args:
            has: Keep only objects containing a descendant matching this.
            has_text: Keep only objects whose display text contains this substring.
            has_not: Keep only objects *without* a descendant matching this.

        Returns:
            A new unresolved locator with the conditions applied.
        """
        narrowed = sel.Selector(steps=[sel.Step(**vars(s)) for s in self._selector.steps],
                                source=self._selector.source)
        last = narrowed.steps[-1]
        if has is not None:
            last.has = sel.coerce(has)
        if has_not is not None:
            last.attrs.append(sel.Attr("__has_not", "=", sel.coerce(has_not).source))
        if has_text is not None:
            last.attrs.append(sel.Attr("text", "*=", has_text))
        return Locator(self._session, narrowed, self._root, self._index, self._strict,
                       parent=self._parent)

    def nth(self, index: int) -> Locator:
        """Choose one match by position, in the agent's deterministic tree order.

        Selecting a match explicitly turns off strictness, since ambiguity is now intended.

        Args:
            index: Zero-based position. Negative indexes count from the end.

        Returns:
            A new locator bound to that position.
        """
        return Locator(self._session, self._selector, self._root, index, strict=False,
                       parent=self._parent)

    @property
    def first(self) -> Locator:
        """The first match, in tree order."""
        return self.nth(0)

    @property
    def last(self) -> Locator:
        """The last match, in tree order."""
        return self.nth(-1)

    def all(self) -> list[Locator]:
        """Resolve now and return one locator per match.

        Unlike the rest of the class this is eager, and the returned locators are bound to the
        handles found at this moment rather than re-resolving later.

        No match is an empty list, not an error: this is the plural form, and it is the same
        question :attr:`count` answers with zero. Looping over "however many there are" must not
        have to be written inside a try.

        Returns:
            One locator per matching object, in tree order. Empty when nothing matched.
        """
        handles = self._find(limit=0, allow_empty=True)
        return [_HandleLocator(self._session, self._selector, h) for h in handles]

    def __iter__(self) -> Iterator[Locator]:
        return iter(self.all())

    @property
    def count(self) -> int:
        """How many objects currently match. Zero is a valid answer, not an error."""
        return len(self._find(limit=0, allow_empty=True))

    # ------------------------------------------------------------------ resolution

    def _find(self, limit: int = 0, allow_empty: bool = False) -> list[str]:
        # A scoped child resolves its parent first: the parent is the root to search beneath.
        # Deferred to here rather than done when chaining, so building a locator stays free.
        root = self._parent.resolve() if self._parent is not None else self._root
        result = self._session.call(
            Cmd.FIND,
            {"selector": self._selector.to_json(), "root": root, "limit": limit},
            selector=self._selector,
        )
        handles = result.get("handles", [])
        if not handles and not allow_empty:
            raise ObjectNotFoundError(self._selector, result.get("near_misses"))
        return handles

    def resolve(self, timeout: float | None = None) -> str:
        """Resolve to exactly one handle, retrying until the timeout expires.

        Args:
            timeout: Seconds to keep retrying. ``0`` makes exactly one attempt. Defaults to the
                session timeout.

        Returns:
            The agent handle for the matched object.

        Raises:
            TimeoutError: Still unresolved when the timeout expired. The chained cause carries
                the real reason, which is usually an ObjectNotFoundError listing near misses or
                an AmbiguousSelectorError naming the candidates.
        """
        timeout = self._session.timeouts.resolve(timeout)

        def once() -> str:
            handles = self._find(limit=0)
            if self._index is not None:
                try:
                    return handles[self._index]
                except IndexError as exc:
                    raise ObjectNotFoundError(
                        f"{self._selector} :nth({self._index}) (only {len(handles)} matches)"
                    ) from exc
            if len(handles) > 1 and self._strict:
                raise AmbiguousSelectorError(
                    self._selector, self._describe(handles[:8]), total=len(handles)
                )
            return handles[0]

        return retry(once, timeout=timeout, description=f"locator {self._selector}")

    def _describe(self, handles: list[str]) -> list[dict]:
        """Describe candidate objects for an error message.

        Never raises. A diagnostic that fails must not replace the failure it exists to explain,
        and a handle can legitimately go stale between the find and this call.

        Args:
            handles: Handles to describe.

        Returns:
            One description per handle, falling back to a bare handle where lookup failed.
        """
        described = []
        for handle in handles:
            try:
                described.append(self._session.call(Cmd.OBJ_INFO, {"handle": handle}))
            except LiberaQtError:
                described.append({"handle": handle})
        return described

    # ------------------------------------------------------------------ actions

    def _act(self, cmd: str, params: dict | None = None, timeout: float | None = None,
             actionable: bool = True) -> Any:
        """Run the full action cycle: resolve, check actionability, act, wait for idle.

        The whole cycle is retried, not just the resolution, because an object can become
        unactionable between being found and being used.

        Args:
            cmd: Protocol command to send.
            params: Command parameters, merged with the resolved handle.
            timeout: Seconds to keep retrying. Defaults to the session timeout.
            actionable: Whether to require the object be visible, enabled and hit-testable.

        Returns:
            The command's result.

        Raises:
            TimeoutError: The object never became actionable, wrapping the real reason.
        """
        timeout = self._session.timeouts.resolve(timeout)

        def once() -> Any:
            handle = self.resolve(timeout=0)
            if actionable:
                info_params: dict[str, Any] = {"handle": handle, "require_actionable": True}
                # The agent's reachability checks (modal in front, covered, parked off-window)
                # apply to native delivery only, so the action's mode has to travel with the
                # actionability probe too or the check would test the wrong delivery.
                if params and params.get("mode"):
                    info_params["mode"] = params["mode"]
                self._session.call(Cmd.OBJ_INFO, info_params, selector=self._selector)
            payload = {"handle": handle}
            payload.update(params or {})
            # No `selector=` on the action itself. Resolution already succeeded -- we hold a
            # handle -- so a `not_found` from here is about something *inside* the widget: a
            # tab, a row, a menu entry. The agent's message is the whole diagnosis and lists
            # what was really there; attaching the selector would make `from_agent_error`
            # rebuild it as "no object matched selector: QTabWidget" and throw that away.
            return self._session.call(cmd, payload)

        result = retry(once, timeout=timeout, description=f"action {cmd} on {self._selector}")
        self._session.wait_for_idle()
        return result

    def click(self, button: str = "left", modifiers: list[str] | None = None,
              position: tuple | None = None, count: int = 1,
              timeout: float | None = None, mode: str | None = None) -> None:
        """Click the object with a real mouse event.

        Args:
            button: ``"left"``, ``"right"`` or ``"middle"``.
            modifiers: Held modifiers, e.g. ``["Ctrl", "Shift"]``.
            position: ``(x, y)`` within the object. Defaults to its centre.
            count: Number of clicks; ``2`` is a double click.
            timeout: Seconds to wait for the object to become clickable.
            mode: ``"native"`` or ``"synthetic"``, overriding the session's input mode for this
                one click; see :meth:`~liberaqt.application.Application.set_input_mode`.
        """
        params: dict[str, Any] = {"button": button, "modifiers": modifiers or [],
                                  "pos": list(position) if position else None, "count": count}
        if mode:
            params["mode"] = mode
        self._act(Cmd.CLICK, params, timeout=timeout)

    def double_click(self, **kw: Any) -> None:
        """Double-click the object.

        Args:
            **kw: Passed to :meth:`click`.
        """
        self.click(count=2, **kw)

    def right_click(self, **kw: Any) -> None:
        """Right-click the object, usually to open a context menu.

        Args:
            **kw: Passed to :meth:`click`.
        """
        self.click(button="right", **kw)

    def hover(self, timeout: float | None = None, mode: str | None = None) -> None:
        """Move the pointer over the object, for tooltips and hover states.

        Args:
            timeout: Seconds to wait for the object to become hoverable.
            mode: ``"native"`` or ``"synthetic"`` for this call only.
        """
        self._act(Cmd.HOVER, {"mode": mode} if mode else None, timeout=timeout)

    def fill(self, text: str, timeout: float | None = None) -> None:
        """Clear and set text in one shot, by writing the property.

        The one action here that is deliberately not user input: it writes ``text`` directly
        instead of typing, so nothing the application does *per keystroke* happens -- no
        ``textEdited``, no completer popup, no input mask, no per-key validator, no
        ``keyPressEvent`` override. Use it to set up state you are not testing, and :meth:`type`
        when the typing is the thing under test.

        A read-only field is refused rather than written to. Succeeding where a user could never
        have typed is a false pass, and a silent one. Reachability is deliberately *not*
        required, though: filling a field on a form page that is not currently shown is exactly
        what a state-setup helper is for, so the actionability probe runs with synthetic
        semantics.

        Args:
            text: Text to set. Empty clears the field.
            timeout: Seconds to wait for the object to become editable.
        """
        self._act(Cmd.SET_TEXT, {"text": text, "mode": "synthetic"}, timeout=timeout)

    def type(self, text: str, delay: float = 0.0, timeout: float | None = None,
             mode: str | None = None) -> None:
        """Type character by character, as a person would.

        Each character is a real press and release carrying both a key code and its text, so
        shortcuts, type-ahead and key handlers all see it. Unlike :meth:`fill` this cannot write
        into a field the user could not type into.

        Key chords may be embedded in the text, wrapped in angle brackets:
        ``type("hello<Ctrl+A>replaced<Return>")`` types, selects all, types over the selection
        and presses Return. A ``<`` that does not open a chord-shaped token -- ``"a < b"`` --
        types literally; one that does -- ``"<b>"`` -- is pressed as a key. Text that must stay
        literal regardless belongs in :meth:`fill`, which writes the property instead of typing.

        Args:
            text: Text to type, with optional ``<Key>`` chords.
            delay: Seconds between keystrokes, for applications that debounce input.
            timeout: Seconds to wait for the object to become editable.
            mode: ``"native"`` or ``"synthetic"`` for this call only.
        """
        extra: dict[str, Any] = {"mode": mode} if mode else {}
        for kind, value in split_chords(text):
            if kind == "chord":
                self._act(Cmd.KEY, {"key": value, **extra}, timeout=timeout)
            else:
                self._act(Cmd.TYPE_TEXT,
                          {"text": value, "delay_ms": int(delay * 1000), **extra},
                          timeout=timeout)

    def press(self, key: str, count: int = 1, timeout: float | None = None,
              mode: str | None = None) -> None:
        """Press a key or chord.

        Args:
            key: Key name or chord, e.g. ``"Ctrl+S"``, ``"Enter"``, ``"Alt+F4"``.
            count: How many times to press it.
            timeout: Seconds to wait for the object to become actionable.
            mode: ``"native"`` or ``"synthetic"`` for this call only.
        """
        params: dict[str, Any] = {"key": key, "count": count}
        if mode:
            params["mode"] = mode
        self._act(Cmd.KEY, params, timeout=timeout)

    def clear(self, timeout: float | None = None) -> None:
        """Clear the object's text.

        Args:
            timeout: Seconds to wait for the object to become editable.
        """
        self.fill("", timeout=timeout)

    def set_checked(self, checked: bool = True, timeout: float | None = None,
                    mode: str | None = None) -> None:
        """Set a checkbox or toggle to a state, clicking only if it is not already there.

        Args:
            checked: Desired state.
            timeout: Seconds to wait for the object to become clickable.
            mode: ``"native"`` or ``"synthetic"`` for this call only.
        """
        if self.is_checked != checked:
            self.click(timeout=timeout, mode=mode)

    def check(self, **kw: Any) -> None:
        """Ensure the object is checked.

        Args:
            **kw: Passed to :meth:`set_checked`.
        """
        self.set_checked(True, **kw)

    def uncheck(self, **kw: Any) -> None:
        """Ensure the object is unchecked.

        Args:
            **kw: Passed to :meth:`set_checked`.
        """
        self.set_checked(False, **kw)

    def select_option(self, text: str | None = None, index: int | None = None,
                      timeout: float | None = None, mode: str | None = None) -> None:
        """Choose an entry in a combo box or list, the way a user does.

        On the native path a combo box is clicked open and the entry is clicked in its popup, so
        everything the application hangs off that -- ``activated``, popup delegates, per-entry
        side effects -- happens as it would for a user. ``mode="synthetic"`` sets the current
        index directly instead.

        Args:
            text: Entry label to select.
            index: Zero-based entry position, as an alternative to ``text``.
            timeout: Seconds to wait for the object to become actionable.
            mode: ``"native"`` or ``"synthetic"`` for this call only.
        """
        params: dict[str, Any] = {"text": text, "index": index}
        if mode:
            params["mode"] = mode
        self._act(Cmd.SELECT_ITEM, params, timeout=timeout)

    def select_item(self, text: str | None = None, row: int | None = None,
                    column: int | None = None, timeout: float | None = None,
                    mode: str | None = None) -> None:
        """Select a cell or row in an item view by clicking it.

        On the native path the item is scrolled into view and clicked, so the selection that
        results is whatever the view's own policy makes of a click -- rows, cells, toggling --
        exactly as for a user. ``mode="synthetic"`` sets the current index and selection
        directly instead.

        Args:
            text: Cell text to select.
            row: Zero-based row index.
            column: Zero-based column index.
            timeout: Seconds to wait for the object to become actionable.
            mode: ``"native"`` or ``"synthetic"`` for this call only.
        """
        params: dict[str, Any] = {"text": text, "row": row, "column": column}
        if mode:
            params["mode"] = mode
        self._act(Cmd.SELECT_ITEM, params, timeout=timeout)

    def select_tab(self, text: str | None = None, index: int | None = None,
                   timeout: float | None = None, mode: str | None = None) -> None:
        """Switch a tab widget to one of its tabs by clicking the tab.

        Works on either the ``QTabWidget`` or its ``QTabBar``, since which one a selector lands on
        is an implementation detail of the application. Tab captions are matched with any ``&``
        accelerator removed, so the text is what the user actually sees. On the native path the
        tab's rectangle on the bar is clicked; a tab the bar has scrolled out of reach is refused
        rather than switched behind the user's back -- ``mode="synthetic"`` reaches it anyway.

        Args:
            text: Tab caption to switch to.
            index: Zero-based tab position, as an alternative to ``text``.
            timeout: Seconds to wait for the object to become actionable.
            mode: ``"native"`` or ``"synthetic"`` for this call only.

        Example:
            ::

                win.locator("QTabWidget").select_tab("Services")
        """
        params: dict[str, Any] = {"text": text, "index": index}
        if mode:
            params["mode"] = mode
        self._act(Cmd.TAB_SELECT, params, timeout=timeout)

    def context_menu(self, path: str, timeout: float | None = None) -> None:
        """Right-click the object and activate an entry in the menu that appears.

        Opens the menu and activates an entry in one move, and every step of it is real user
        input: a right-click opens the menu, a click lands on the entry, and a ``>`` in the path
        walks a submenu the same way. Works on item-view rows and cells too --
        ``tree.row(has_text="counter").context_menu("Set As Root")`` right-clicks the row.

        Native-only by nature: a context menu is built inside ``contextMenuEvent``, so there is
        no action to trigger without genuinely opening the menu. A wrong entry name fails
        listing what the menu really offers, which doubles as the way to *discover* an
        unfamiliar application's context menus.

        Args:
            path: Entry caption, with ``>`` between submenu levels, as the captions read on
                screen (``&`` accelerators are ignored).
            timeout: Seconds to wait for the object to become actionable.
        """
        self._act(Cmd.CONTEXT_MENU, {"path": path}, timeout=timeout)

    def spin(self, steps: int, timeout: float | None = None, mode: str | None = None) -> None:
        """Step a spin box by clicking its arrow buttons.

        Positive steps click the up arrow that many times, negative steps the down arrow. The
        arrows are located through the widget's style, so the clicks land wherever this
        application actually draws them. Each click is a separate action, as a user's would be,
        so per-step signals all fire.

        Args:
            steps: How far to step; the sign picks the arrow.
            timeout: Seconds to wait for the spin box to become actionable.
            mode: ``"native"`` or ``"synthetic"`` for this call only.
        """
        params: dict[str, Any] = {"part": "spin_up" if steps > 0 else "spin_down"}
        if mode:
            params["mode"] = mode
        for _ in range(abs(steps)):
            self._act(Cmd.CLICK, dict(params), timeout=timeout)

    def scroll_into_view(self, timeout: float | None = None) -> None:
        """Scroll every ``QScrollArea`` ancestor until the object is visible.

        Programmatic by design, like :meth:`fill` — and its actionability probe runs with
        synthetic semantics for the same reason: the whole point is a target that is *not*
        currently reachable, so demanding reachability first would refuse exactly the widgets
        this exists to rescue.

        **It can do nothing and say nothing.** Only a ``QScrollArea`` ancestor is scrolled. An
        ancestor that is some other ``QAbstractScrollArea`` is refused with a message naming
        :meth:`wheel` as the alternative, but a target with no scrolling ancestor at all — an
        item view's own widget, say, rather than a widget nested inside a scroll area — simply
        returns having scrolled nothing. So do not read a successful call as evidence the object
        is now on screen, and do not reach for this to bring an item-view row into view:
        :meth:`row` and :meth:`cell` scroll their own view as part of addressing a cell.

        Args:
            timeout: Seconds to wait for the object to resolve.

        Raises:
            UnsupportedOperationError: The only scrolling ancestor is a ``QAbstractScrollArea``
                that is not a ``QScrollArea``.
        """
        self._act(Cmd.INVOKE,
                  {"method": "__scroll_into_view", "args": [], "mode": "synthetic"},
                  timeout=timeout)

    def wheel(self, dx: int = 0, dy: int = 0, timeout: float | None = None,
              mode: str | None = None) -> None:
        """Scroll over the object with the mouse wheel.

        Args:
            dx: Horizontal scroll in wheel steps.
            dy: Vertical scroll in wheel steps. Positive scrolls down.
            timeout: Seconds to wait for the object to become actionable.
            mode: ``"native"`` or ``"synthetic"`` for this call only.
        """
        params: dict[str, Any] = {"dx": dx, "dy": dy}
        if mode:
            params["mode"] = mode
        self._act(Cmd.WHEEL, params, timeout=timeout)

    def drag_to(self, target: Locator, steps: int = 10, timeout: float | None = None,
                mode: str | None = None) -> None:
        """Drag this object onto another.

        Args:
            target: Where to drop. Resolved before the drag begins.
            steps: Intermediate move events. More steps look more human, which matters for
                widgets that only start a drag after a movement threshold.
            timeout: Seconds to wait for this object to become actionable.
            mode: ``"native"`` or ``"synthetic"`` for this call only.
        """
        params: dict[str, Any] = {"to_handle": target.resolve(), "steps": steps}
        if mode:
            params["mode"] = mode
        self._act(Cmd.DRAG, params, timeout=timeout)

    def highlight(self, duration: float = 1.0, color: str = "#ff3b30",
                  width: int = 3, timeout: float | None = None) -> None:
        """Draw a coloured box over the object for a moment, to see which one it is.

        A debugging aid, not an action: nothing about the application changes. Use it when a
        selector resolves but you are not sure it found what you meant -- the alternative is
        :meth:`screenshot` and reading coordinates.

        Returns as soon as the marker is up, so the duration is not added to the test's runtime.
        Actionability is deliberately *not* required: an object that is covered or parked
        off-screen is exactly the one worth looking at.

        The marker is invisible to the selector engine and to ``object.tree``, so highlighting
        cannot change what a later selector matches.

        Args:
            duration: Seconds to leave the marker up.
            color: Any CSS colour the Qt stylesheet parser accepts, e.g. ``"red"``, ``"#0a0"``.
            width: Border thickness in pixels.
            timeout: Seconds to wait for the object to resolve.

        Raises:
            UnsupportedOperationError: The object is not a widget; Quick items are not supported.

        Example:
            Checking a selector interactively::

                win.locator("QLineEdit:nth(2)").highlight(duration=3.0)
        """
        self._act(
            Cmd.HIGHLIGHT,
            {"duration_ms": int(duration * 1000), "color": color, "width": width},
            timeout=timeout,
            actionable=False,
        )

    def screenshot(self, path: str | None = None) -> bytes:
        """Grab just this object as a PNG.

        Args:
            path: Where to write the image. When omitted, the bytes are only returned.

        Returns:
            The PNG image bytes.
        """
        return self._session.grab(handle=self.resolve(), path=path)

    # ------------------------------------------------------------------ state

    def _info(self) -> dict:
        return self._session.call(Cmd.OBJ_INFO, {"handle": self.resolve()},
                                  selector=self._selector)

    @property
    def text(self) -> str:
        """Display text: a button's label, a field's contents, a label's caption."""
        return self._info().get("text", "")

    @property
    def value(self) -> Any:
        """Value of a value-bearing widget such as a slider or spin box."""
        return decode_value(self._info().get("value"))

    @property
    def is_visible(self) -> bool:
        """Whether the object is visible. Reading this never waits."""
        return bool(self._info().get("visible"))

    @property
    def is_enabled(self) -> bool:
        """Whether the object accepts input."""
        return bool(self._info().get("enabled"))

    @property
    def is_checked(self) -> bool:
        """Whether a checkable object is checked."""
        return bool(self._info().get("checked"))

    @property
    def geometry(self) -> tuple:
        """Geometry as ``(x, y, width, height)``, relative to the parent."""
        return tuple(self._info().get("geometry", ()))

    @property
    def class_name(self) -> str:
        """Qt class name, e.g. ``"QPushButton"``."""
        return self._info().get("class", "")

    @property
    def object_name(self) -> str:
        """The object's ``objectName``, empty when the application never set one."""
        return self._info().get("objectName", "")

    def exists(self) -> bool:
        """Whether anything currently matches. Does not wait.

        Returns:
            True when at least one object matches.
        """
        return self.count > 0

    def __getitem__(self, name: str) -> Any:
        """Read any ``Q_PROPERTY`` by name, e.g. ``locator["echoMode"]``.

        Args:
            name: Property name.

        Returns:
            The decoded value. Types JSON cannot express come back as tagged objects; see
            ``docs/PROTOCOL.md`` section 3.
        """
        return decode_value(
            self._session.call(Cmd.GET_PROPERTY, {"handle": self.resolve(), "name": name},
                               selector=self._selector).get("value")
        )

    def __setitem__(self, name: str, value: Any) -> None:
        """Write any writable ``Q_PROPERTY`` by name.

        The value is coerced to the property's declared type, so ``[x, y]`` reaches a ``QPoint``
        property and ``[w, h]`` a ``QSize`` one.

        Args:
            name: Property name.
            value: Value to write.

        Raises:
            UnsupportedOperationError: No such property, or it is read-only.
        """
        self._session.call(Cmd.SET_PROPERTY,
                           {"handle": self.resolve(), "name": name, "value": value},
                           selector=self._selector)

    def properties(self, declared_only: bool = False) -> list[dict]:
        """List every ``Q_PROPERTY`` on the object.

        Each entry carries ``name``, ``type``, ``readable``, ``writable``, ``declared_in`` and,
        when readable, its current ``value``.

        Args:
            declared_only: Keep only what this object's own class introduced, dropping the sixty
                or so it inherits from QWidget.

        Returns:
            One entry per property, in meta-object order.
        """
        # `.get`, not the raw response: this returned the whole {"properties": [...]} envelope,
        # so iterating it yielded the string "properties" and every caller got an AttributeError
        # the first time it touched an entry. The annotation had said list[dict] throughout.
        found = self._session.call(
            Cmd.LIST_PROPERTIES, {"handle": self.resolve()}
        ).get("properties", [])
        if declared_only:
            own = self.class_name
            found = [p for p in found if p.get("declared_in") == own]
        return found

    def call_native(self, module: str, symbol: str, argument: str,
                    signature: str = "cstr(bool*,cstr)") -> tuple[bool, str]:
        """Call an exported C++ method on this object, by symbol. The last resort.

        Plain public methods are invisible to the meta-object system, so a library can expose
        its whole API and none of it be reachable through :meth:`invoke`. This calls one anyway,
        by resolving the exported symbol in a module the application has already loaded.

        **This is sharp.** The agent runs inside the application under test, so a wrong signature
        is an access violation rather than an exception. Two things blunt it: only an
        already-loaded module is used, never one the agent loads itself; and on MSVC the call is
        made behind a structured-exception guard, so a fault comes back as an error instead of
        taking the application down. Neither makes a wrong signature *correct* -- check with a
        selector that the object really is an instance of the declaring class first, remembering
        that type matching walks the inheritance chain.

        Prefer :meth:`invoke` whenever the method is a slot or ``Q_INVOKABLE``. Reach for this
        only when :meth:`methods` has shown that it is not.

        Args:
            module: DLL the symbol lives in, e.g. ``"nlvqtb.dll"``. Must already be loaded.
            symbol: The exact exported name. C++ names are mangled; take the spelling from
                ``dumpbin /exports``.
            argument: The single string argument.
            signature: Which shape is being called. Only ``"cstr(bool*,cstr)"`` --
                ``const char *(bool *ok, const char *arg)`` -- is supported so far.

        Returns:
            ``(ok, value)``, where ``ok`` is what the callee wrote through its ``bool *``.

        Raises:
            LiberaQtError: The module is not loaded, the symbol does not exist, the signature is
                unsupported, or the call faulted.
        """
        result = self._session.call(Cmd.CALL_NATIVE, {
            "handle": self.resolve(), "module": module, "symbol": symbol,
            "signature": signature, "args": [argument],
        })
        return bool(result.get("ok")), str(result.get("value", ""))

    def accessible(self, depth: int = -1) -> dict:
        """What a screen reader would see beneath this object.

        The other general way into a widget that paints its own contents. Qt's accessibility
        framework exists so such a widget can describe what it drew -- a name, a role and a
        **rect per element** -- without exposing any QObjects. Where it is implemented, this is
        the only route that yields coordinates for things that are not objects.

        Rects are in *screen* coordinates, as QAccessible reports them.

        It is only as good as the widget's own implementation, and plenty implement nothing. A
        widget that does reports itself with no children, which is a real answer -- and worth
        having, because it distinguishes "nothing is exposed" from "we looked in the wrong
        place". Measured: Assistant's documentation tree reports 73 children with individual
        rects; Libero's NLview canvas reports zero.

        Args:
            depth: Levels to descend. ``-1`` for the whole tree, ``0`` for this object alone.

        Returns:
            ``{"accessibility_active": bool, "supported": bool, "root": {...}}``. ``supported``
            is false when the object has no accessible interface at all. ``accessibility_active``
            reports whether the framework is switched on in the process, which is reported
            separately because "off" and "exposes nothing" look identical from the outside.
        """
        return self._session.call(Cmd.ACCESSIBLE,
                                  {"handle": self.resolve(), "depth": depth})

    def methods(self, declared_only: bool = False, callable_only: bool = False) -> list[dict]:
        """Everything the meta-object knows how to call on this object.

        The companion to :meth:`properties`. Between them they are the entire surface a QObject
        offers when you do not have its headers, which is the situation for every third-party
        widget -- and the only way in at all for one that paints its own contents, where nothing
        is reachable through the object tree.

        Each entry carries ``name``, ``signature``, ``kind`` (``slot``, ``signal``, ``method``
        for a ``Q_INVOKABLE``, or ``constructor``), ``callable``, ``return_type``,
        ``parameters`` and ``declared_in``.

        Signals are listed but never ``callable``: emitting one fakes an event the application
        never had, which makes a test lie rather than drive anything.

        Args:
            declared_only: Keep only what this object's own class introduced, dropping the
                hundred-odd it inherits from QWidget and QObject.
            callable_only: Keep only what :meth:`invoke` can actually call.

        Returns:
            One dict per method, in meta-object order.

        Example:
            Finding out what a custom canvas offers::

                for m in canvas.methods(declared_only=True, callable_only=True):
                    print(m["signature"])
        """
        found = self._session.call(Cmd.LIST_METHODS, {"handle": self.resolve()}).get("methods", [])
        if declared_only:
            own = self.class_name
            found = [m for m in found if m.get("declared_in") == own]
        if callable_only:
            found = [m for m in found if m.get("callable")]
        return found

    def invoke(self, method: str, *args: Any, queued: bool = False) -> Any:
        """Call a slot or ``Q_INVOKABLE`` method on the object.

        Only those two are reachable: plain public functions are invisible to Qt's meta-object
        system. If lookup fails the error lists what *is* invokable on the class.

        Args:
            method: Method name.
            *args: Arguments, coerced to the declared parameter types.
            queued: Post the call instead of making it, and return as soon as it is queued.
                Required for anything that opens a modal dialog: such a method does not return
                until the dialog is dismissed, so a direct call would strand the reply for as
                long as the dialog is up. The return value is lost, so this is opt-in.

        Returns:
            The decoded return value, or ``None`` for a void method or a queued call.

        Raises:
            UnsupportedOperationError: No such slot or invokable method.

        Example:
            Opening a modal wizard from a menu action::

                win.locator("QAction[text='New Project']").invoke("trigger", queued=True)
        """
        params = {"handle": self.resolve(), "method": method, "args": list(args)}
        if queued:
            params["queued"] = True
        return decode_value(
            self._session.call(Cmd.INVOKE, params, selector=self._selector).get("value")
        )

    def evaluate(self, expression: str) -> Any:
        """Evaluate a JavaScript expression in this item's QML context. Qt Quick only.

        **Not implemented by the agent yet.** ``quick.evaluate`` is not registered, so this
        raises :class:`~liberaqt.errors.UnsupportedOperationError` against every current agent,
        Qt Quick or not. QML *discovery* works -- locators reach Quick items and read their
        properties -- but the ``quick.*`` commands do not exist. Check
        ``app.supports(Cmd.QUICK_EVALUATE)`` rather than assuming.

        Args:
            expression: Expression to evaluate, e.g. ``"model.count"``.

        Returns:
            The decoded result.

        Raises:
            UnsupportedOperationError: Always, at present -- see above.
        """
        return decode_value(
            self._session.call(Cmd.QUICK_EVALUATE,
                               {"handle": self.resolve(), "expression": expression},
                               selector=self._selector).get("value")
        )

    def wait_for_signal(self, signal: str, timeout: float | None = None) -> None:
        """Block until the object emits a signal.

        For work that finishes on its own schedule, where waiting for the UI to go idle is not a
        precise enough answer.

        Args:
            signal: Signal name, e.g. ``"clicked"``.
            timeout: Seconds to wait. Defaults to the session timeout.

        Raises:
            TimeoutError: The signal did not arrive in time.
        """
        timeout = self._session.timeouts.resolve(timeout)
        self._session.call(Cmd.WAIT_SIGNAL,
                           {"handle": self.resolve(), "signal": signal,
                            "timeout_ms": int(timeout * 1000)},
                           timeout=timeout + 1)

    # ------------------------------------------------------------------ item views

    def to_records(self) -> list[dict]:
        """Read a model-backed view's contents as one dict per row.

        Keys come from the horizontal header, falling back to the column index where a column
        has no header text.

        Returns:
            One dict per row, in model order.

        Raises:
            UnsupportedOperationError: The object is not a model-backed view.
        """
        return self._session.call(Cmd.MODEL_DATA, {"handle": self.resolve()}).get("rows", [])

    def parent(self) -> Locator:
        """The object one level above this one in the tree.

        The only direction the selector language cannot express: everything else searches
        downwards. Note that a parent is often not the widget you would point at on screen --
        Qt puts layouts, viewports and internal containers in between -- so :meth:`ancestor` is
        usually the one you want.

        Unlike :meth:`locator` this resolves immediately, because there is nothing to defer: the
        answer is a specific object, not a selector that might match later.

        Returns:
            A locator bound to the parent.

        Raises:
            ObjectNotFoundError: The object is a root and has no parent.

        Example:
            Stepping off a button onto whatever holds it::

                button.parent().class_name
        """
        # No `selector=`: resolution already succeeded, so a `not_found` from here is about
        # what lies *above* the object, and the agent's message -- which names the chain it
        # walked -- is the whole diagnosis. Passing the selector would make `from_agent_error`
        # rebuild it as "no object matched selector: QPushButton#add" and throw that away.
        result = self._session.call(Cmd.ANCESTOR, {"handle": self.resolve()})
        return _HandleLocator(self._session, self._selector, result["handle"])

    def ancestor(self, selector: SelectorLike = None, /, **kwargs: Any) -> Locator:
        """The nearest object above this one that matches, however many levels up it is.

        A parent is one step; an ancestor is any number. That difference is the whole point:
        real applications bury a widget several layers below the thing it belongs to, so
        ``view.parent()`` lands on a splitter or a viewport while
        ``view.ancestor("QDockWidget")`` reaches the dock.

        The search starts at the parent, so an object never matches itself.

        Args:
            selector: Selector string, dict, or parsed ``Selector`` the ancestor must match.
            **kwargs: Selector fields given as keywords, e.g. ``type="QDockWidget"``.

        Returns:
            A locator bound to the nearest matching ancestor.

        Raises:
            ObjectNotFoundError: Nothing above the object matched. The message lists the chain
                that was walked, which usually shows why.

        Example:
            Finding the dock a deeply nested view lives in::

                view.ancestor("QDockWidget").object_name
        """
        want = sel.coerce(selector, **kwargs)
        # No `selector=`, for the reason given in `parent`: it would replace the agent's
        # chain-walking diagnosis with a generic "nothing matched this selector".
        result = self._session.call(
            Cmd.ANCESTOR, {"handle": self.resolve(), "selector": want.to_json()},
        )
        return _HandleLocator(self._session, self._selector, result["handle"])

    def row(self, has_text: str | None = None, index: int | None = None) -> Locator:
        """Locate a row in an item view.

        Args:
            has_text: Match the row containing this text in any cell.
            index: Zero-based row index, as an alternative to ``has_text``.

        Returns:
            A locator bound to the matched row.

        Raises:
            ObjectNotFoundError: No row matched.
        """
        # "has_text" means containing, matching filter(has_text=...) and the docstring above.
        # select_item() deliberately stays exact: choosing "Widget" should not select "Widgets".
        params = {"handle": self.resolve(), "text": has_text, "row": index,
                  "match": "contains"}
        result = self._session.call(Cmd.ITEM_RECT, params)
        return _HandleLocator(self._session, self._selector, result["handle"])

    def item(self, text: str) -> Locator:
        """Locate an item by its exact text, at any depth.

        The exact-match counterpart to :meth:`row`, whose ``has_text`` means *containing*. Reach
        for this whenever one label is a substring of another: searching a Libero design flow
        for "Synthesize" with ``row()`` finds "Verify Pre-Synthesized Design" first.

        The search descends the whole tree and fetches lazily populated branches on the way, so
        a node the view has never expanded is still found.

        Args:
            text: The item's text, matched exactly.

        Returns:
            A locator bound to the matched item.

        Raises:
            ObjectNotFoundError: No item has that exact text.
        """
        result = self._session.call(Cmd.ITEM_RECT, {"handle": self.resolve(), "text": text})
        return _HandleLocator(self._session, self._selector, result["handle"])

    def cell(self, row: int, column: int | str) -> Locator:
        """Locate a single cell in an item view.

        Args:
            row: Zero-based row index.
            column: Column index, or the column's header text.

        Returns:
            A locator bound to the cell.

        Raises:
            ObjectNotFoundError: No such cell.
        """
        result = self._session.call(Cmd.ITEM_RECT,
                                    {"handle": self.resolve(), "row": row, "column": column})
        return _HandleLocator(self._session, self._selector, result["handle"])

    def __repr__(self) -> str:
        suffix = f":nth({self._index})" if self._index is not None else ""
        return f"<Locator {self._selector}{suffix}>"


class _HandleLocator(Locator):
    """A locator already bound to a resolved handle (result of .all(), .row(), ...).

    Deliberately does not re-resolve: it will raise ``StaleObjectError`` if the object goes away,
    which is the honest behaviour for a snapshot.

    Args:
        session: The wire session used for every command.
        selector: The selector this handle came from, kept for error messages.
        handle: The already-resolved agent handle.
    """

    def __init__(self, session: Session, selector: sel.Selector, handle: str):
        super().__init__(session, selector)
        self._handle = handle

    @property
    def _is_narrowed(self) -> bool:
        """Always: a handle locator is one object by construction.

        So chaining beneath it scopes to that object, rather than re-running the selector it
        came from and matching every sibling as well.
        """
        return True

    def resolve(self, timeout: float | None = None) -> str:
        """Return the bound handle without searching.

        Args:
            timeout: Accepted for signature compatibility, and unused.

        Returns:
            The handle this locator was built with.
        """
        return self._handle

    def __repr__(self) -> str:
        return f"<Locator handle={self._handle} from {self._selector}>"



