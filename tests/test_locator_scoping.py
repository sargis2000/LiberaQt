"""Chaining beneath a locator that stands for one object.

Regression cover for a silent wrong-answer bug: chaining off ``.first`` / ``.nth(i)`` / a
``.all()`` element used to append a step to the *selector* and re-run the whole search, throwing
away the narrowing that had just been asked for. ``win.locator("QDockWidget").first
.locator("QPushButton")`` therefore meant "buttons in any dock", not "buttons in that one dock",
and returned every match instead of the intended subset.

Measured against Qt Assistant while the bug was live: chaining off ``.first`` returned 3 buttons
drawn from all five docks, where the dock in question contains none at all.

These assert on the ``root`` the client puts on the wire, because that is the whole mechanism:
a scoped child searches beneath a resolved handle rather than carrying a longer selector.
"""

import pytest

from liberaqt.locator import Locator
from liberaqt.selectors import parse
from liberaqt.waits import TimeoutPolicy


class ScriptedSession:
    """Answers each object.find from a scripted queue, recording the params it was given."""

    def __init__(self, *results):
        self.timeouts = TimeoutPolicy(0.5)
        self.slowmo = 0.0
        self.finds = []
        self._results = list(results)

    def call(self, cmd, params=None, timeout=None, selector=None):
        params = params or {}
        if cmd == "object.find":
            self.finds.append(params)
            return {"handles": list(self._results.pop(0))}
        raise AssertionError(f"unexpected command {cmd}")

    def wait_for_idle(self, **kw):
        pass


def _root_of(find_params):
    return find_params.get("root")


def test_chaining_off_first_scopes_to_that_object():
    session = ScriptedSession(["dock1", "dock2"], ["button-in-dock1"])
    dock = Locator(session, parse("QDockWidget"), "win").first
    handles = dock.locator("QPushButton")._find()

    assert handles == ["button-in-dock1"]
    parent_find, child_find = session.finds
    assert _root_of(parent_find) == "win", "the parent search should still be window-scoped"
    assert _root_of(child_find) == "dock1", (
        "the child search must be rooted at the object .first resolved to"
    )
    assert child_find["selector"]["steps"][0]["type"] == "QPushButton", (
        "the child selector must not have the parent's steps prepended to it"
    )


def test_chaining_off_nth_scopes_to_that_object():
    session = ScriptedSession(["a", "b", "c"], ["inner"])
    Locator(session, parse("QWidget"), "win").nth(2).locator("QLabel")._find()
    assert _root_of(session.finds[1]) == "c"


def test_chaining_off_last_scopes_to_that_object():
    session = ScriptedSession(["a", "b", "c"], ["inner"])
    Locator(session, parse("QWidget"), "win").last.locator("QLabel")._find()
    assert _root_of(session.finds[1]) == "c"


def test_chaining_off_an_all_element_scopes_to_that_object():
    """`.all()` hands back handle-bound locators, which are one object by construction."""
    session = ScriptedSession(["d1", "d2"], ["inner"])
    docks = Locator(session, parse("QDockWidget"), "win").all()
    docks[1].locator("QPushButton")._find()
    assert _root_of(session.finds[1]) == "d2"


def test_child_is_scoped_the_same_way():
    """The direct-child form needs the same treatment as the descendant form."""
    session = ScriptedSession(["dock1", "dock2"], ["kid"])
    Locator(session, parse("QDockWidget"), "win").first.child("QPushButton")._find()
    child_find = session.finds[1]
    assert _root_of(child_find) == "dock1"
    assert child_find["selector"]["steps"][0]["direct_child"] is True


def test_an_unnarrowed_locator_still_joins_selectors():
    """The un-narrowed case must keep working: one search with a longer selector, no extra trip."""
    session = ScriptedSession(["found"])
    Locator(session, parse("QDockWidget"), "win").locator("QPushButton")._find()

    assert len(session.finds) == 1, "an un-narrowed chain should not resolve its parent separately"
    steps = session.finds[0]["selector"]["steps"]
    assert [s["type"] for s in steps] == ["QDockWidget", "QPushButton"]
    assert _root_of(session.finds[0]) == "win"


def test_narrowing_after_chaining_keeps_the_scope():
    """`.first.locator(X).first` must stay inside the object the first `.first` chose."""
    session = ScriptedSession(["dock1", "dock2"], ["b1", "b2"])
    loc = Locator(session, parse("QDockWidget"), "win").first.locator("QPushButton").first
    assert loc.resolve() == "b1"
    assert _root_of(session.finds[1]) == "dock1"


def test_chaining_is_still_lazy():
    """Building a chain must perform no I/O; resolution is what talks to the agent."""
    session = ScriptedSession(["dock1"], ["inner"])
    chain = Locator(session, parse("QDockWidget"), "win").first.locator("QPushButton").first
    assert session.finds == [], f"chaining performed {len(session.finds)} searches before use"
    chain.resolve()
    assert len(session.finds) == 2


@pytest.mark.parametrize("depth", [2, 3, 4])
def test_scoping_survives_repeated_chaining(depth):
    """Each narrowed link roots the next, however many there are."""
    session = ScriptedSession(*([[f"level{i}"] for i in range(depth)]))
    loc = Locator(session, parse("QWidget"), "win").first
    for _ in range(depth - 1):
        loc = loc.locator("QWidget").first
    loc.resolve()
    roots = [_root_of(f) for f in session.finds]
    assert roots == ["win"] + [f"level{i}" for i in range(depth - 1)]


# ------------------------------------------------------------------ walking upwards


class AncestorSession(ScriptedSession):
    """Adds object.ancestor, answering with a fixed handle and recording the params."""

    def __init__(self, *results, ancestor="up"):
        super().__init__(*results)
        self.ancestors = []
        self._ancestor = ancestor

    def call(self, cmd, params=None, timeout=None, selector=None):
        if cmd == "object.ancestor":
            self.ancestors.append({"params": params or {}, "selector_kw": selector})
            return {"handle": self._ancestor}
        return super().call(cmd, params, timeout, selector)


def test_parent_asks_for_the_ancestor_with_no_selector():
    """No selector means "one level up", which is what the agent keys off."""
    session = AncestorSession(["btn"], ancestor="the-parent")
    parent = Locator(session, parse("QPushButton"), "win").parent()

    assert parent.resolve() == "the-parent"
    sent = session.ancestors[0]["params"]
    assert sent["handle"] == "btn"
    assert "selector" not in sent, "parent() must not send a selector; that means 'nearest match'"


def test_ancestor_sends_the_selector_to_match_against():
    session = AncestorSession(["view"], ancestor="the-dock")
    found = Locator(session, parse("QTreeView"), "win").ancestor("QDockWidget")

    assert found.resolve() == "the-dock"
    sent = session.ancestors[0]["params"]
    assert sent["handle"] == "view"
    assert sent["selector"]["steps"][0]["type"] == "QDockWidget"


def test_ancestor_accepts_keyword_selector_fields():
    session = AncestorSession(["view"])
    Locator(session, parse("QTreeView"), "win").ancestor(type="QDockWidget")
    assert session.ancestors[0]["params"]["selector"]["steps"][0]["type"] == "QDockWidget"


def test_neither_decorates_the_error_with_the_locator_selector():
    """Regression cover: passing `selector=` replaced the agent's diagnosis with a generic one.

    The agent answers a failed ancestor search by naming the chain it walked, which is the whole
    value of the error. `from_agent_error` rebuilds any error carrying a selector as "no object
    matched selector: ...", throwing that away -- the same trap `_act` documents.
    """
    # Two scripted finds: parent() and ancestor() each resolve the locator first.
    session = AncestorSession(["btn"], ["btn"])
    loc = Locator(session, parse("QPushButton"), "win")
    loc.parent()
    loc.ancestor("QDockWidget")
    assert [a["selector_kw"] for a in session.ancestors] == [None, None]


def test_the_result_is_bound_to_the_returned_handle():
    """The answer is a specific object, so it must not re-run the original selector."""
    session = AncestorSession(["btn"], ancestor="up")
    parent = Locator(session, parse("QPushButton"), "win").parent()
    before = len(session.finds)
    assert parent.resolve() == "up"
    assert parent.resolve() == "up"
    assert len(session.finds) == before, "resolving the parent should not search again"


def test_chaining_beneath_a_parent_scopes_to_it():
    """A handle-bound result is narrowed, so chaining off it searches inside that object."""
    session = AncestorSession(["btn"], ["sibling"], ancestor="the-parent")
    parent = Locator(session, parse("QPushButton"), "win").parent()
    parent.locator("QLabel")._find()
    assert session.finds[-1]["root"] == "the-parent"
