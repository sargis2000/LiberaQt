"""Turn a live object tree into selectors a user can paste into a test.

The hard part is not generating candidate selectors, it is knowing whether one actually
identifies the object you meant. That question is only answerable by the selector engine itself:
type matching walks the metaobject inheritance chain, so a custom ``MyButton : QPushButton``
makes ``QPushButton`` match two objects even though the tree dump shows one of each class.
Guessing client-side would confidently report "unique" and be wrong.

So candidate *generation* lives here as pure functions, and candidate *verification* is delegated
to a resolver callable. The CLI passes one backed by the agent; tests pass a fake.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass

from .protocol import Cmd
from .selectors import parse

#: Longer strings make unwieldy selectors and are usually dynamic anyway.
MAX_TEXT = 40

#: Qt's own internal children (qt_scrollarea_viewport, qt_menubar, ...). Real, but never what a
#: test author is looking for, and they bury the handful of objects that are.
INTERNAL_PREFIX = "qt_"

#: Parts Qt builds inside composite widgets. They have no objectName and the application never
#: gets the chance to give them one, so they are excluded from "you should name these" advice.
QT_INTERNAL_CLASSES = frozenset({
    "QScrollBar", "QHeaderView", "QTableCornerButton", "QWidget", "QFrame",
    "QScrollArea", "QAbstractScrollArea", "QViewport", "QMenu", "QMenuBar",
})


def resolver_for(session, root_handle: str) -> Callable[[str], list]:
    """A resolver backed by a live agent, scoped to ``root_handle``.

    Answers come from the same engine the tests will use, which is the point: uniqueness cannot
    be inferred from a tree dump when type matching follows the inheritance chain.
    """
    def resolve(selector: str) -> list:
        result = session.call(
            Cmd.FIND,
            {"selector": parse(selector).to_json(), "root": root_handle, "limit": 0},
        )
        return result.get("handles", [])

    return resolve


def walk(node: dict | None, depth: int = 0) -> Iterator[tuple[int, dict]]:
    """Depth-first walk of an ``object.tree`` dump, mirroring the engine's own ordering."""
    if not node:
        return
    yield depth, node
    for child in node.get("children") or []:
        yield from walk(child, depth + 1)


def quote(value: str) -> str:
    """Quote a string for use inside a selector attribute test.

    Args:
        value: Raw text, which may contain quotes or backslashes.

    Returns:
        The value single-quoted, with backslashes and quotes escaped.
    """
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def is_internal(node: dict) -> bool:
    """Whether a node is one of Qt's own internal children.

    Args:
        node: A node from an ``object.tree`` dump.

    Returns:
        True for objects named ``qt_*``, which the application did not create.
    """
    return (node.get("objectName") or "").startswith(INTERNAL_PREFIX)


def candidates(node: dict) -> list[str]:
    """Selectors for this object, most robust first.

    Mirrors the recorder's ranking in docs/ARCHITECTURE.md section 6: an objectName is stable
    across redesigns, visible text survives layout changes but not translation, and a bare type
    is a coin flip. Position is deliberately absent -- it is only ever a fallback, and
    :func:`suggest` adds it once everything better has been ruled out.
    """
    cls = node.get("class") or "*"
    name = (node.get("objectName") or "").strip()
    text = (node.get("text") or "").strip()

    out = []
    if name:
        out.append(f"{cls}#{name}")
    if text and len(text) <= MAX_TEXT and "\n" not in text:
        out.append(f"{cls}[text={quote(text)}]")
    out.append(cls)
    return out


@dataclass
class Suggestion:
    """A selector for one object, and how well it pins it down."""

    node: dict
    depth: int
    selector: str
    matches: int
    positional: bool = False        # needed :nth(), i.e. nothing about the object is distinctive

    @property
    def unique(self) -> bool:
        """Whether the selector identifies this object and no other."""
        return self.matches == 1 and not self.positional

    @property
    def text(self) -> str:
        """The object's display text, trimmed. Empty when it has none."""
        return (self.node.get("text") or "").strip()

    @property
    def status(self) -> str:
        """A short verdict for the table: unique, ambiguous, or positional."""
        if self.unique:
            return "unique"
        if self.positional:
            # Only advise naming things the application actually owns.
            advice = "" if self.node.get("class") in QT_INTERNAL_CLASSES else " - set an objectName"
            return f"positional ({self.matches} of this type){advice}"
        return f"AMBIGUOUS ({self.matches} matches)"


def suggest(node: dict, depth: int, resolve: Callable[[str], list[str]]) -> Suggestion:
    """Pick the best selector for ``node``.

    ``resolve`` maps a selector to the handles it matches, in engine order. Candidates are tried
    best-first and the first one that resolves to exactly this object wins.
    """
    handle = node.get("handle")
    tried = candidates(node)
    handles: list[str] = []

    for selector in tried:
        handles = resolve(selector)
        if len(handles) == 1 and handles[0] == handle:
            return Suggestion(node, depth, selector, 1)

    # Nothing distinctive. Fall back to position within the bare-type matches, using the engine's
    # own ordering so the index means the same thing on the next run.
    bare = tried[-1]
    if handle in handles:
        return Suggestion(node, depth, f"{bare}:nth({handles.index(handle)})",
                          len(handles), positional=True)
    return Suggestion(node, depth, bare, len(handles), positional=True)


def suggest_all(tree: dict | None, resolve: Callable[[str], list[str]],
                include_internal: bool = False, skip_root: bool = True) -> list[Suggestion]:
    """Suggest a selector for every object in the tree.

    The root is skipped by default. Searches are rooted *at* the window, so the window can never
    appear among its own descendants -- it would always report zero matches. Windows are reached
    with ``app.window(title=...)``, not a locator.
    """
    out = []
    for depth, node in walk(tree):
        if skip_root and depth == 0:
            continue
        if not include_internal and is_internal(node):
            continue
        out.append(suggest(node, depth, resolve))
    return out


def format_table(suggestions: list[Suggestion], width: int = 46) -> str:
    """Render suggestions as a scannable, paste-ready table."""
    if not suggestions:
        return "no objects found"

    lines = [f"{'SELECTOR'.ljust(width)}  {'TEXT'.ljust(20)}  STATUS"]
    for item in suggestions:
        text = item.text
        if len(text) > 20:
            text = text[:17] + "..."
        lines.append(f"{item.selector.ljust(width)}  {text.ljust(20)}  {item.status}")
    return "\n".join(lines)


def summarize(suggestions: list[Suggestion]) -> str:
    """A count, plus advice only where advice would actually help."""
    total = len(suggestions)
    by_name = sum(1 for s in suggestions if s.unique and "#" in s.selector)
    by_text = sum(1 for s in suggestions if s.unique and "[text=" in s.selector)
    unique = sum(1 for s in suggestions if s.unique)

    lines = [f"{total} objects: {unique} uniquely addressable "
             f"({by_name} by objectName, {by_text} by text)"]

    # Only widgets the application itself created are worth naming. Qt builds scrollbars, headers
    # and viewports inside composite widgets, and telling someone to name those is bad advice.
    nameable = [s for s in suggestions
                if not s.unique and not s.text and s.node.get("class") not in QT_INTERNAL_CLASSES]
    if nameable:
        lines.append(
            f"{len(nameable)} have no stable identifier. If any is one of your own widgets, "
            "give it an objectName in the application -- it is the only selector that survives "
            "a redesign. The rest are Qt's internals and are usually reached through their "
            "parent instead."
        )
    return "\n".join(lines)