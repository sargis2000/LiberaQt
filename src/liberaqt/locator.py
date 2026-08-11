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
from .protocol import Cmd, decode_value
from .waits import retry

if TYPE_CHECKING:
    from .session import Session

SelectorLike = Union[str, dict, "sel.Selector", None]


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
    """

    def __init__(self, session: Session, selector: sel.Selector,
                 root_handle: str | None = None, index: int | None = None,
                 strict: bool = True):
        self._session = session
        self._selector = selector
        self._root = root_handle
        self._index = index
        self._strict = strict

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
        return Locator(self._session, sel.join(self._selector, child), self._root,
                       strict=self._strict)

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
        return Locator(self._session, narrowed, self._root, self._index, self._strict)

    def nth(self, index: int) -> Locator:
        """Choose one match by position, in the agent's deterministic tree order.

        Selecting a match explicitly turns off strictness, since ambiguity is now intended.

        Args:
            index: Zero-based position. Negative indexes count from the end.

        Returns:
            A new locator bound to that position.
        """
        return Locator(self._session, self._selector, self._root, index, strict=False)

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
        result = self._session.call(
            Cmd.FIND,
            {"selector": self._selector.to_json(), "root": self._root, "limit": limit},
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
                self._session.call(
                    Cmd.OBJ_INFO, {"handle": handle, "require_actionable": True},
                    selector=self._selector,
                )
            payload = {"handle": handle}
            payload.update(params or {})
            return self._session.call(cmd, payload, selector=self._selector)

        result = retry(once, timeout=timeout, description=f"action {cmd} on {self._selector}")
        self._session.wait_for_idle()
        return result

    def click(self, button: str = "left", modifiers: list[str] | None = None,
              position: tuple | None = None, count: int = 1,
              timeout: float | None = None) -> None:
        """Click the object with a real mouse event.

        Args:
            button: ``"left"``, ``"right"`` or ``"middle"``.
            modifiers: Held modifiers, e.g. ``["Ctrl", "Shift"]``.
            position: ``(x, y)`` within the object. Defaults to its centre.
            count: Number of clicks; ``2`` is a double click.
            timeout: Seconds to wait for the object to become clickable.
        """
        self._act(Cmd.CLICK, {"button": button, "modifiers": modifiers or [],
                              "pos": list(position) if position else None, "count": count},
                  timeout=timeout)

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

    def hover(self, timeout: float | None = None) -> None:
        """Move the pointer over the object, for tooltips and hover states.

        Args:
            timeout: Seconds to wait for the object to become hoverable.
        """
        self._act(Cmd.HOVER, timeout=timeout)

    def fill(self, text: str, timeout: float | None = None) -> None:
        """Clear and set text in one shot.

        Fast, but sets the text directly rather than emitting per-key events. Use :meth:`type`
        where the application reacts to individual keystrokes, such as a search-as-you-type box.

        Args:
            text: Text to set. Empty clears the field.
            timeout: Seconds to wait for the object to become editable.
        """
        self._act(Cmd.SET_TEXT, {"text": text}, timeout=timeout)

    def type(self, text: str, delay: float = 0.0, timeout: float | None = None) -> None:
        """Type character by character with real key events.

        Args:
            text: Text to type.
            delay: Seconds between keystrokes, for applications that debounce input.
            timeout: Seconds to wait for the object to become editable.
        """
        self._act(Cmd.TYPE_TEXT, {"text": text, "delay_ms": int(delay * 1000)}, timeout=timeout)

    def press(self, key: str, count: int = 1, timeout: float | None = None) -> None:
        """Press a key or chord.

        Args:
            key: Key name or chord, e.g. ``"Ctrl+S"``, ``"Enter"``, ``"Alt+F4"``.
            count: How many times to press it.
            timeout: Seconds to wait for the object to become actionable.
        """
        self._act(Cmd.KEY, {"key": key, "count": count}, timeout=timeout)

    def clear(self, timeout: float | None = None) -> None:
        """Clear the object's text.

        Args:
            timeout: Seconds to wait for the object to become editable.
        """
        self.fill("", timeout=timeout)

    def set_checked(self, checked: bool = True, timeout: float | None = None) -> None:
        """Set a checkbox or toggle to a state, clicking only if it is not already there.

        Args:
            checked: Desired state.
            timeout: Seconds to wait for the object to become clickable.
        """
        if self.is_checked != checked:
            self.click(timeout=timeout)

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
                      timeout: float | None = None) -> None:
        """Choose an entry in a combo box or list.

        Args:
            text: Entry label to select.
            index: Zero-based entry position, as an alternative to ``text``.
            timeout: Seconds to wait for the object to become actionable.
        """
        self._act(Cmd.SELECT_ITEM, {"text": text, "index": index}, timeout=timeout)

    def select_item(self, text: str | None = None, row: int | None = None,
                    column: int | None = None, timeout: float | None = None) -> None:
        """Select a cell or row in an item view.

        Args:
            text: Cell text to select.
            row: Zero-based row index.
            column: Zero-based column index.
            timeout: Seconds to wait for the object to become actionable.
        """
        self._act(Cmd.SELECT_ITEM, {"text": text, "row": row, "column": column}, timeout=timeout)

    def select_tab(self, text: str | None = None, index: int | None = None,
                   timeout: float | None = None) -> None:
        """Switch a tab widget to one of its tabs.

        Works on either the ``QTabWidget`` or its ``QTabBar``, since which one a selector lands on
        is an implementation detail of the application. Tab captions are matched with any ``&``
        accelerator removed, so the text is what the user actually sees.

        Args:
            text: Tab caption to switch to.
            index: Zero-based tab position, as an alternative to ``text``.
            timeout: Seconds to wait for the object to become actionable.

        Example:
            ::

                win.locator("QTabWidget").select_tab("Services")
        """
        self._act(Cmd.TAB_SELECT, {"text": text, "index": index}, timeout=timeout)

    def scroll_into_view(self, timeout: float | None = None) -> None:
        """Scroll ancestors until the object is visible.

        Args:
            timeout: Seconds to wait for the object to resolve.
        """
        self._act(Cmd.INVOKE, {"method": "__scroll_into_view", "args": []}, timeout=timeout)

    def wheel(self, dx: int = 0, dy: int = 0, timeout: float | None = None) -> None:
        """Scroll over the object with the mouse wheel.

        Args:
            dx: Horizontal scroll in wheel steps.
            dy: Vertical scroll in wheel steps. Positive scrolls down.
            timeout: Seconds to wait for the object to become actionable.
        """
        self._act(Cmd.WHEEL, {"dx": dx, "dy": dy}, timeout=timeout)

    def drag_to(self, target: Locator, steps: int = 10, timeout: float | None = None) -> None:
        """Drag this object onto another.

        Args:
            target: Where to drop. Resolved before the drag begins.
            steps: Intermediate move events. More steps look more human, which matters for
                widgets that only start a drag after a movement threshold.
            timeout: Seconds to wait for this object to become actionable.
        """
        self._act(Cmd.DRAG, {"to_handle": target.resolve(), "steps": steps}, timeout=timeout)

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

    def properties(self) -> list[dict]:
        """List every ``Q_PROPERTY`` on the object.

        Returns:
            One entry per property, with its name, type and whether it is writable.
        """
        return self._session.call(Cmd.LIST_PROPERTIES, {"handle": self.resolve()})

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

        Args:
            expression: Expression to evaluate, e.g. ``"model.count"``.

        Returns:
            The decoded result.

        Raises:
            UnsupportedOperationError: The object is not a Qt Quick item.
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
        params = {"handle": self.resolve(), "text": has_text, "row": index}
        result = self._session.call(Cmd.ITEM_RECT, params)
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
