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
    """One (or one-of-many) objects in the AUT's object tree."""

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
        """A descendant of this locator."""
        child = sel.coerce(selector, **kwargs)
        return Locator(self._session, sel.join(self._selector, child), self._root,
                       strict=self._strict)

    def child(self, selector: SelectorLike = None, /, **kwargs: Any) -> Locator:
        """A *direct* child of this locator."""
        child = sel.coerce(selector, **kwargs)
        child.steps[0].direct_child = True
        return Locator(self._session, sel.join(self._selector, child), self._root,
                       strict=self._strict)

    def filter(self, *, has: SelectorLike = None, has_text: str | None = None,
               has_not: SelectorLike = None) -> Locator:
        """Narrow the match set without an extra round trip."""
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
        return Locator(self._session, self._selector, self._root, index, strict=False)

    @property
    def first(self) -> Locator:
        return self.nth(0)

    @property
    def last(self) -> Locator:
        return self.nth(-1)

    def all(self) -> list[Locator]:
        """Resolve now and return one locator per match."""
        handles = self._find(limit=0)
        return [_HandleLocator(self._session, self._selector, h) for h in handles]

    def __iter__(self) -> Iterator[Locator]:
        return iter(self.all())

    @property
    def count(self) -> int:
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
        """Resolve to exactly one handle, retrying until the timeout expires."""
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
        self._act(Cmd.CLICK, {"button": button, "modifiers": modifiers or [],
                              "pos": list(position) if position else None, "count": count},
                  timeout=timeout)

    def double_click(self, **kw: Any) -> None:
        self.click(count=2, **kw)

    def right_click(self, **kw: Any) -> None:
        self.click(button="right", **kw)

    def hover(self, timeout: float | None = None) -> None:
        self._act(Cmd.HOVER, timeout=timeout)

    def fill(self, text: str, timeout: float | None = None) -> None:
        """Clear and set text in one shot. Fast; does not emit per-key events."""
        self._act(Cmd.SET_TEXT, {"text": text}, timeout=timeout)

    def type(self, text: str, delay: float = 0.0, timeout: float | None = None) -> None:
        """Type character by character with real key events, for keystroke-sensitive widgets."""
        self._act(Cmd.TYPE_TEXT, {"text": text, "delay_ms": int(delay * 1000)}, timeout=timeout)

    def press(self, key: str, count: int = 1, timeout: float | None = None) -> None:
        """Press a key or chord, e.g. ``"Ctrl+S"``, ``"Enter"``, ``"Alt+F4"``."""
        self._act(Cmd.KEY, {"key": key, "count": count}, timeout=timeout)

    def clear(self, timeout: float | None = None) -> None:
        self.fill("", timeout=timeout)

    def set_checked(self, checked: bool = True, timeout: float | None = None) -> None:
        if self.is_checked != checked:
            self.click(timeout=timeout)

    def check(self, **kw: Any) -> None:
        self.set_checked(True, **kw)

    def uncheck(self, **kw: Any) -> None:
        self.set_checked(False, **kw)

    def select_option(self, text: str | None = None, index: int | None = None,
                      timeout: float | None = None) -> None:
        self._act(Cmd.SELECT_ITEM, {"text": text, "index": index}, timeout=timeout)

    def select_item(self, text: str | None = None, row: int | None = None,
                    column: int | None = None, timeout: float | None = None) -> None:
        self._act(Cmd.SELECT_ITEM, {"text": text, "row": row, "column": column}, timeout=timeout)

    def scroll_into_view(self, timeout: float | None = None) -> None:
        self._act(Cmd.INVOKE, {"method": "__scroll_into_view", "args": []}, timeout=timeout)

    def wheel(self, dx: int = 0, dy: int = 0, timeout: float | None = None) -> None:
        self._act(Cmd.WHEEL, {"dx": dx, "dy": dy}, timeout=timeout)

    def drag_to(self, target: Locator, steps: int = 10, timeout: float | None = None) -> None:
        self._act(Cmd.DRAG, {"to_handle": target.resolve(), "steps": steps}, timeout=timeout)

    def screenshot(self, path: str | None = None) -> bytes:
        return self._session.grab(handle=self.resolve(), path=path)

    # ------------------------------------------------------------------ state

    def _info(self) -> dict:
        return self._session.call(Cmd.OBJ_INFO, {"handle": self.resolve()},
                                  selector=self._selector)

    @property
    def text(self) -> str:
        return self._info().get("text", "")

    @property
    def value(self) -> Any:
        return decode_value(self._info().get("value"))

    @property
    def is_visible(self) -> bool:
        return bool(self._info().get("visible"))

    @property
    def is_enabled(self) -> bool:
        return bool(self._info().get("enabled"))

    @property
    def is_checked(self) -> bool:
        return bool(self._info().get("checked"))

    @property
    def geometry(self) -> tuple:
        return tuple(self._info().get("geometry", ()))

    @property
    def class_name(self) -> str:
        return self._info().get("class", "")

    @property
    def object_name(self) -> str:
        return self._info().get("objectName", "")

    def exists(self) -> bool:
        return self.count > 0

    def __getitem__(self, name: str) -> Any:
        return decode_value(
            self._session.call(Cmd.GET_PROPERTY, {"handle": self.resolve(), "name": name},
                               selector=self._selector).get("value")
        )

    def __setitem__(self, name: str, value: Any) -> None:
        self._session.call(Cmd.SET_PROPERTY,
                           {"handle": self.resolve(), "name": name, "value": value},
                           selector=self._selector)

    def properties(self) -> list[dict]:
        return self._session.call(Cmd.LIST_PROPERTIES, {"handle": self.resolve()})

    def invoke(self, method: str, *args: Any) -> Any:
        return decode_value(
            self._session.call(Cmd.INVOKE,
                               {"handle": self.resolve(), "method": method, "args": list(args)},
                               selector=self._selector).get("value")
        )

    def evaluate(self, expression: str) -> Any:
        """Evaluate a JS expression in this item's QML context (Qt Quick objects only)."""
        return decode_value(
            self._session.call(Cmd.QUICK_EVALUATE,
                               {"handle": self.resolve(), "expression": expression},
                               selector=self._selector).get("value")
        )

    def wait_for_signal(self, signal: str, timeout: float | None = None) -> None:
        timeout = self._session.timeouts.resolve(timeout)
        self._session.call(Cmd.WAIT_SIGNAL,
                           {"handle": self.resolve(), "signal": signal,
                            "timeout_ms": int(timeout * 1000)},
                           timeout=timeout + 1)

    # ------------------------------------------------------------------ item views

    def to_records(self) -> list[dict]:
        """Read a model-backed view's contents as a list of dicts (one per row)."""
        return self._session.call(Cmd.MODEL_DATA, {"handle": self.resolve()}).get("rows", [])

    def row(self, has_text: str | None = None, index: int | None = None) -> Locator:
        params = {"handle": self.resolve(), "text": has_text, "row": index}
        result = self._session.call(Cmd.ITEM_RECT, params)
        return _HandleLocator(self._session, self._selector, result["handle"])

    def cell(self, row: int, column: int | str) -> Locator:
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
    """

    def __init__(self, session: Session, selector: sel.Selector, handle: str):
        super().__init__(session, selector)
        self._handle = handle

    def resolve(self, timeout: float | None = None) -> str:
        return self._handle

    def __repr__(self) -> str:
        return f"<Locator handle={self._handle} from {self._selector}>"
