"""Enumerating what a QObject can be told to do.

`invoke` could only ever be used by someone who already knew the method name, and `properties`
listed only the other half of the meta-object. That is a real gap in front of a third-party
widget: without headers, the meta-object is the whole discoverable surface -- and for a widget
that paints its own contents (a schematic canvas, a chart, a custom editor) it is the *only* way
in, because nothing inside it appears in the object tree at all.

These assert the client's filtering and the shape it expects back. What the agent actually
reports for a real class is covered live, since only a running Qt application has a meta-object.
"""

import pytest

from liberaqt.locator import Locator
from liberaqt.selectors import parse
from liberaqt.waits import TimeoutPolicy

#: Shaped as the agent sends it: a couple inherited from QWidget, a couple the class introduces.
CANVAS_METHODS = [
    {"name": "update", "signature": "update()", "kind": "slot", "callable": True,
     "return_type": "void", "parameters": [], "declared_in": "QWidget"},
    {"name": "destroyed", "signature": "destroyed(QObject*)", "kind": "signal", "callable": False,
     "return_type": "void", "parameters": [{"type": "QObject*", "name": "obj"}],
     "declared_in": "QObject"},
    {"name": "zoomToFit", "signature": "zoomToFit()", "kind": "slot", "callable": True,
     "return_type": "void", "parameters": [], "declared_in": "NlvSDWidget"},
    {"name": "command", "signature": "command(QString)", "kind": "method", "callable": True,
     "return_type": "QString", "parameters": [{"type": "QString", "name": "cmd"}],
     "declared_in": "NlvSDWidget"},
    {"name": "selectionChanged", "signature": "selectionChanged()", "kind": "signal",
     "callable": False, "return_type": "void", "parameters": [], "declared_in": "NlvSDWidget"},
]


class StubSession:
    """Answers object.find, object.info and object.list_methods, recording the calls."""

    def __init__(self, methods=CANVAS_METHODS, class_name="NlvSDWidget"):
        self.timeouts = TimeoutPolicy(0.5)
        self.slowmo = 0.0
        self.calls = []
        self._methods = methods
        self._class = class_name

    def call(self, cmd, params=None, timeout=None, selector=None):
        self.calls.append(cmd)
        if cmd == "object.find":
            return {"handles": ["canvas"]}
        if cmd == "object.list_methods":
            assert params["handle"] == "canvas"
            return {"methods": list(self._methods)}
        if cmd == "object.info":
            return {"handle": "canvas", "class": self._class}
        raise AssertionError(f"unexpected command {cmd}")

    def wait_for_idle(self, **kw):
        pass


@pytest.fixture
def canvas():
    session = StubSession()
    return Locator(session, parse("NlvSDWidget"), "win"), session


def test_every_method_comes_back_by_default(canvas):
    loc, _ = canvas
    assert [m["name"] for m in loc.methods()] == [
        "update", "destroyed", "zoomToFit", "command", "selectionChanged",
    ]


def test_it_asks_the_agent_rather_than_guessing(canvas):
    loc, session = canvas
    loc.methods()
    assert "object.list_methods" in session.calls


def test_callable_only_drops_signals(canvas):
    """A signal is listed so you know it exists, never as something to call.

    Emitting one fakes an event the application never had: it would make a test pass by
    describing something that did not happen.
    """
    loc, _ = canvas
    names = [m["name"] for m in loc.methods(callable_only=True)]
    assert names == ["update", "zoomToFit", "command"]
    assert "destroyed" not in names
    assert "selectionChanged" not in names


def test_declared_only_drops_what_was_inherited(canvas):
    """The point of the filter: a custom widget's own handful, not QWidget's hundred."""
    loc, _ = canvas
    assert [m["name"] for m in loc.methods(declared_only=True)] == [
        "zoomToFit", "command", "selectionChanged",
    ]


def test_the_two_filters_combine(canvas):
    loc, _ = canvas
    assert [m["name"] for m in loc.methods(declared_only=True, callable_only=True)] == [
        "zoomToFit", "command",
    ]


def test_the_entry_carries_what_invoke_needs(canvas):
    """Enough to call it without headers: name, argument types, and what comes back."""
    loc, _ = canvas
    command = next(m for m in loc.methods() if m["name"] == "command")
    assert command["signature"] == "command(QString)"
    assert command["kind"] == "method"          # Q_INVOKABLE rather than a slot
    assert command["return_type"] == "QString"
    assert command["parameters"] == [{"type": "QString", "name": "cmd"}]


def test_an_object_with_nothing_of_its_own_is_not_an_error():
    """Most widgets introduce no invokables at all; that is an empty list, not a failure."""
    session = StubSession(methods=[m for m in CANVAS_METHODS if m["declared_in"] != "NlvSDWidget"])
    loc = Locator(session, parse("QWidget"), "win")
    assert loc.methods(declared_only=True) == []
    assert loc.methods() != []


def test_a_missing_methods_key_reads_as_empty():
    """An older agent answering without the key must not crash the client."""
    class Bare(StubSession):
        def call(self, cmd, params=None, timeout=None, selector=None):
            if cmd == "object.list_methods":
                return {}
            return super().call(cmd, params, timeout, selector)

    assert Locator(Bare(), parse("QWidget"), "win").methods() == []


# ------------------------------------------------------------------ its older sibling


PROPERTIES = [
    {"name": "enabled", "type": "bool", "readable": True, "writable": True,
     "declared_in": "QWidget", "value": True},
    {"name": "zoomLevel", "type": "double", "readable": True, "writable": True,
     "declared_in": "NlvSDWidget", "value": 1.0},
]


class PropertySession(StubSession):
    def call(self, cmd, params=None, timeout=None, selector=None):
        if cmd == "object.list_properties":
            self.calls.append(cmd)
            return {"properties": list(PROPERTIES)}
        return super().call(cmd, params, timeout, selector)


def test_properties_returns_the_list_not_the_envelope():
    """Regression: this handed back the whole {"properties": [...]} response.

    Iterating it yielded the string "properties", so the first caller to read an entry got
    `AttributeError: 'str' object has no attribute 'get'` -- while the annotation promised
    list[dict]. Found by writing `methods()` beside it and noticing they disagreed.
    """
    loc = Locator(PropertySession(), parse("NlvSDWidget"), "win")
    found = loc.properties()

    assert isinstance(found, list)
    assert [p["name"] for p in found] == ["enabled", "zoomLevel"]


def test_properties_can_drop_inherited_ones():
    loc = Locator(PropertySession(), parse("NlvSDWidget"), "win")
    assert [p["name"] for p in loc.properties(declared_only=True)] == ["zoomLevel"]


def test_a_missing_properties_key_reads_as_empty():
    class Bare(StubSession):
        def call(self, cmd, params=None, timeout=None, selector=None):
            if cmd == "object.list_properties":
                return {}
            return super().call(cmd, params, timeout, selector)

    assert Locator(Bare(), parse("QWidget"), "win").properties() == []


# ------------------------------------------------------------------ the accessibility route


class AccessibleSession(StubSession):
    """Answers object.accessible with a tree, as a widget implementing accessibility would."""

    TREE = {
        "accessibility_active": False,
        "supported": True,
        "root": {
            "name": "", "role": 35, "rect": [196, 281, 323, 397], "child_count": 2,
            "actions": ["SetFocus"],
            "children": [
                {"name": "Active Qt", "role": 36, "rect": [222, 282, 284, 22],
                 "child_count": 0, "children": []},
                {"name": "Build with CMake", "role": 36, "rect": [222, 304, 284, 22],
                 "child_count": 0, "children": []},
            ],
        },
    }

    def call(self, cmd, params=None, timeout=None, selector=None):
        if cmd == "object.accessible":
            self.calls.append(cmd)
            self.last_params = params
            return dict(self.TREE)
        return super().call(cmd, params, timeout, selector)


def test_accessible_returns_children_with_screen_rects():
    """The whole point: geometry for things that are not QObjects."""
    loc = Locator(AccessibleSession(), parse("QTreeView"), "win")
    tree = loc.accessible()

    assert tree["supported"] is True
    kids = tree["root"]["children"]
    assert [k["name"] for k in kids] == ["Active Qt", "Build with CMake"]
    assert kids[0]["rect"] == [222, 282, 284, 22]


def test_depth_is_passed_through():
    session = AccessibleSession()
    Locator(session, parse("QTreeView"), "win").accessible(depth=1)
    assert session.last_params["depth"] == 1
    assert session.last_params["handle"] == "canvas"


def test_the_whole_tree_is_the_default():
    session = AccessibleSession()
    Locator(session, parse("QTreeView"), "win").accessible()
    assert session.last_params["depth"] == -1


def test_a_widget_exposing_nothing_is_a_real_answer():
    """Libero's NLview canvas: an accessible interface exists, with no children beneath it.

    Distinguishable from `supported: False`, and from accessibility being switched off -- three
    situations that look identical unless reported separately.
    """
    class Opaque(AccessibleSession):
        TREE = {
            "accessibility_active": False,
            "supported": True,
            "root": {"name": "", "role": 10, "rect": [599, 158, 1298, 514],
                     "child_count": 0, "actions": ["SetFocus"], "children": []},
        }

    tree = Locator(Opaque(), parse("NlvSDWidget"), "win").accessible()
    assert tree["supported"] is True
    assert tree["root"]["child_count"] == 0
    assert tree["root"]["rect"] == [599, 158, 1298, 514]


def test_no_interface_at_all_is_reported_separately():
    class Unsupported(AccessibleSession):
        TREE = {"accessibility_active": False, "supported": False}

    tree = Locator(Unsupported(), parse("QObject"), "win").accessible()
    assert tree["supported"] is False
    assert "root" not in tree
