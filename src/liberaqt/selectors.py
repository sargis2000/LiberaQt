"""Selector mini-language parser.

Compiles ``"QDialog#settings > QPushButton[text='Apply']:visible"`` into the SelectorNode JSON
described in docs/PROTOCOL.md. Parsing happens client-side so that syntax errors surface with a
Python traceback instead of an opaque agent error.

Grammar (see docs/SELECTORS.md):

    selector    := step ( combinator step )*
    combinator  := '>' | whitespace
    step        := [type[!]] ['#' objectName] attr* pseudo*
    attr        := '[' key op value ']'
    op          := '=' | '*=' | '^=' | '$=' | '~=' | '!='
    pseudo      := ':visible' | ':enabled' | ':checked' | ':focused'
                 | ':first' | ':last' | ':nth(N)'
                 | ':has(SELECTOR)' | ':parent(SELECTOR)'
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .errors import InvalidSelectorError

OPERATORS = ("*=", "^=", "$=", "~=", "!=", "=")
STATE_PSEUDOS = {"visible", "enabled", "checked", "focused", "first", "last"}

# Note: ':' is deliberately NOT part of an identifier -- it introduces a pseudo-class.
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_\.]*|\*")


@dataclass
class Attr:
    """One attribute test inside a step, such as ``[text^='Vol']``.

    Attributes:
        key: Property or pseudo-attribute being tested, e.g. ``"text"``.
        op: Comparison: ``=`` exact, ``*=`` contains, ``^=`` starts with, ``$=`` ends with,
            ``~=`` matches a regular expression.
        value: Right-hand side, always compared as a string.
    """

    key: str
    op: str
    value: str

    def to_json(self) -> dict:
        """Convert to the protocol's wire form.

        Returns:
            A dict with ``key``, ``op`` and ``value``.
        """
        return {"key": self.key, "op": self.op, "value": self.value}


@dataclass
class Step:
    """One segment of a selector, e.g. the ``QPushButton#ok`` in ``QDialog > QPushButton#ok``.

    Attributes:
        type: Class or QML type name. Matches subclasses too unless ``exact_type`` is set.
        exact_type: Trailing ``!``: match this exact class, ignoring the inheritance chain.
        object_name: Required ``objectName``, written as ``#name``.
        attrs: Attribute tests that must all pass.
        states: Pseudo-classes such as ``visible`` or ``enabled``.
        index: Zero-based ``:nth(i)`` position among this step's matches.
        has: ``:has(...)`` -- keep only objects containing a match for this.
        parent: Preceding step this one must be a descendant of.
        direct_child: Whether ``parent`` must be the immediate parent rather than any ancestor.
    """

    type: str | None = None
    exact_type: bool = False          # trailing '!' -> exact class, no inherits
    object_name: str | None = None
    attrs: list[Attr] = field(default_factory=list)
    states: list[str] = field(default_factory=list)
    index: int | None = None
    has: Selector | None = None
    parent: Selector | None = None
    direct_child: bool = False        # this step must be a direct child of the previous one

    def to_json(self) -> dict:
        """Convert to the protocol's wire form.

        Only fields that are set are emitted, keeping the wire form readable in a trace.

        Returns:
            A dict describing this step.
        """
        node: dict[str, Any] = {}
        if self.type:
            node["type"] = self.type
        if self.exact_type:
            node["exact_type"] = True
        if self.object_name:
            node["objectName"] = self.object_name
        if self.attrs:
            node["attrs"] = [a.to_json() for a in self.attrs]
        if self.states:
            node["states"] = self.states
        if self.index is not None:
            node["index"] = self.index
        if self.has is not None:
            node["has"] = self.has.to_json()
        if self.parent is not None:
            node["parent"] = self.parent.to_json()
        if self.direct_child:
            node["direct_child"] = True
        return node


@dataclass
class Selector:
    """A parsed selector: a chain of steps plus the text it was written as.

    Attributes:
        steps: Steps to match, outermost first.
        source: Original selector string, kept so error messages can quote what the user wrote
            rather than a reconstruction of it.
    """

    steps: list[Step] = field(default_factory=list)
    source: str = ""

    def to_json(self) -> dict:
        """Convert to the protocol's wire form.

        Returns:
            A dict with ``steps`` and ``source``.
        """
        return {"steps": [s.to_json() for s in self.steps], "source": self.source}

    def __str__(self) -> str:
        return self.source or repr(self.to_json())


class _Parser:
    def __init__(self, text: str):
        self.text = text
        self.pos = 0

    # -- helpers -------------------------------------------------------------
    def error(self, msg: str) -> InvalidSelectorError:
        return InvalidSelectorError(
            f"{msg} at offset {self.pos} in {self.text!r}\n  {self.text}\n  {' ' * self.pos}^"
        )

    def peek(self) -> str:
        return self.text[self.pos] if self.pos < len(self.text) else ""

    def eat_ws(self) -> bool:
        seen = False
        while self.pos < len(self.text) and self.text[self.pos].isspace():
            self.pos += 1
            seen = True
        return seen

    def ident(self) -> str:
        m = _IDENT.match(self.text, self.pos)
        if not m:
            raise self.error("expected an identifier")
        self.pos = m.end()
        return m.group(0)

    def quoted_or_bare(self) -> str:
        ch = self.peek()
        if ch in "'\"":
            self.pos += 1
            out = []
            while self.pos < len(self.text):
                c = self.text[self.pos]
                if c == "\\" and self.pos + 1 < len(self.text):
                    out.append(self.text[self.pos + 1])
                    self.pos += 2
                    continue
                if c == ch:
                    self.pos += 1
                    return "".join(out)
                out.append(c)
                self.pos += 1
            raise self.error("unterminated string")
        start = self.pos
        while self.pos < len(self.text) and self.text[self.pos] not in "]":
            self.pos += 1
        if start == self.pos:
            raise self.error("expected a value")
        return self.text[start:self.pos].strip()

    def balanced(self) -> str:
        """Read a parenthesised group, honouring nesting and quotes."""
        if self.peek() != "(":
            raise self.error("expected '('")
        depth = 0
        start = self.pos + 1
        quote = ""
        while self.pos < len(self.text):
            c = self.text[self.pos]
            if quote:
                if c == quote:
                    quote = ""
            elif c in "'\"":
                quote = c
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    inner = self.text[start:self.pos]
                    self.pos += 1
                    return inner
            self.pos += 1
        raise self.error("unbalanced parentheses")

    # -- grammar -------------------------------------------------------------
    def parse(self) -> Selector:
        sel = Selector(source=self.text.strip())
        self.eat_ws()
        if not self.peek():
            raise self.error("empty selector")
        sel.steps.append(self.step(direct_child=False))
        while True:
            had_ws = self.eat_ws()
            ch = self.peek()
            if not ch:
                break
            if ch == ">":
                self.pos += 1
                self.eat_ws()
                sel.steps.append(self.step(direct_child=True))
            elif had_ws:
                sel.steps.append(self.step(direct_child=False))
            else:
                raise self.error("expected a combinator")
        return sel

    def step(self, direct_child: bool) -> Step:
        st = Step(direct_child=direct_child)
        ch = self.peek()
        if ch and (ch.isalpha() or ch in "_*"):
            st.type = self.ident()
            if self.peek() == "!":
                st.exact_type = True
                self.pos += 1
        while True:
            ch = self.peek()
            if ch == "#":
                self.pos += 1
                st.object_name = self.ident()
            elif ch == "[":
                self.pos += 1
                st.attrs.append(self.attr())
            elif ch == ":":
                self.pos += 1
                self.pseudo(st)
            else:
                break
        if st.type is None and st.object_name is None and not st.attrs and not st.states:
            raise self.error("step matches everything; add a type, #objectName or [attr]")
        return st

    def attr(self) -> Attr:
        key = self.ident()
        for op in OPERATORS:
            if self.text.startswith(op, self.pos):
                self.pos += len(op)
                value = self.quoted_or_bare()
                if self.peek() != "]":
                    raise self.error("expected ']'")
                self.pos += 1
                return Attr(key, op, value)
        raise self.error(f"expected one of {OPERATORS}")

    def pseudo(self, st: Step) -> None:
        name = self.ident()
        if name in STATE_PSEUDOS:
            if name == "first":
                st.index = 0
            elif name == "last":
                st.index = -1
            else:
                st.states.append(name)
            return
        if name == "nth":
            st.index = int(self.balanced())
            return
        if name == "has":
            st.has = parse(self.balanced())
            return
        if name == "parent":
            st.parent = parse(self.balanced())
            return
        raise self.error(f"unknown pseudo ':{name}'")


def parse(text: str) -> Selector:
    """Parse a selector string. Raises InvalidSelectorError with a caret-marked position."""
    return _Parser(text).parse()


def from_kwargs(**kwargs: Any) -> Selector:
    """Build a single-step selector from keyword arguments.

    ``type`` and ``objectName`` are structural; everything else becomes an exact attribute match.
    """
    st = Step()
    st.type = kwargs.pop("type", None)
    st.object_name = kwargs.pop("objectName", None) or kwargs.pop("object_name", None)
    index = kwargs.pop("index", None)
    if index is not None:
        st.index = int(index)
    for key, value in kwargs.items():
        if isinstance(value, bool) and key in STATE_PSEUDOS:
            if value:
                st.states.append(key)
            continue
        st.attrs.append(Attr(key, "=", str(value)))
    if st.type is None and st.object_name is None and not st.attrs and not st.states:
        raise InvalidSelectorError("empty selector: pass type=, objectName= or a property")
    sel = Selector(steps=[st])
    sel.source = _render(st)
    return sel


def coerce(selector: Any = None, /, **kwargs: Any) -> Selector:
    """Accept a string, a dict, an already-parsed Selector, or keyword arguments."""
    if selector is not None and kwargs:
        raise InvalidSelectorError("pass either a selector or keyword arguments, not both")
    if isinstance(selector, Selector):
        return selector
    if isinstance(selector, str):
        return parse(selector)
    if isinstance(selector, dict):
        return from_kwargs(**selector)
    if selector is None:
        return from_kwargs(**kwargs)
    raise InvalidSelectorError(f"cannot interpret {selector!r} as a selector")


def _render(st: Step) -> str:
    out = st.type or ""
    if st.exact_type:
        out += "!"
    if st.object_name:
        out += f"#{st.object_name}"
    for a in st.attrs:
        out += f"[{a.key}{a.op}{a.value!r}]"
    for s in st.states:
        out += f":{s}"
    if st.index is not None:
        out += f":nth({st.index})"
    return out or "*"


def join(parent: Selector, child: Selector) -> Selector:
    """Concatenate two selectors (used by Locator chaining)."""
    merged = Selector(steps=list(parent.steps) + list(child.steps))
    merged.source = f"{parent.source} {child.source}".strip()
    return merged
