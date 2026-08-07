"""Selector suggestion: ranking, verification, and the fallbacks.

No agent needed. Candidate generation is pure, and verification goes through a resolver callable
that these tests fake -- which is the whole reason it is a callable.
"""

from liberaqt.suggest import (
    candidates,
    format_table,
    quote,
    suggest,
    suggest_all,
    summarize,
    walk,
)


def node(cls, name="", text="", handle="o1", children=None):
    return {"class": cls, "objectName": name, "text": text, "handle": handle,
            "children": children or []}


class FakeEngine:
    """Resolves selectors against a fixed mapping, recording what was asked."""

    def __init__(self, mapping):
        self.mapping = mapping
        self.asked = []

    def __call__(self, selector):
        self.asked.append(selector)
        return self.mapping.get(selector, [])


# ------------------------------------------------------------------ candidates

def test_objectname_outranks_text_outranks_type():
    assert candidates(node("QPushButton", name="okButton", text="OK")) == [
        "QPushButton#okButton",
        "QPushButton[text='OK']",
        "QPushButton",
    ]


def test_text_is_used_when_there_is_no_objectname():
    assert candidates(node("QLabel", text="User")) == ["QLabel[text='User']", "QLabel"]


def test_long_or_multiline_text_is_not_offered():
    assert candidates(node("QLabel", text="x" * 200)) == ["QLabel"]
    assert candidates(node("QLabel", text="two\nlines")) == ["QLabel"]


def test_apostrophes_are_escaped():
    assert quote("it's") == "'it\\'s'"
    assert candidates(node("QLabel", text="it's"))[0] == "QLabel[text='it\\'s']"


def test_missing_class_falls_back_to_wildcard():
    assert candidates({"handle": "o1"}) == ["*"]


# ------------------------------------------------------------------ verification

def test_first_uniquely_resolving_candidate_wins():
    target = node("QPushButton", name="okButton", text="OK", handle="o7")
    engine = FakeEngine({"QPushButton#okButton": ["o7"]})

    result = suggest(target, 1, engine)

    assert result.selector == "QPushButton#okButton"
    assert result.unique
    assert engine.asked == ["QPushButton#okButton"], "should stop at the first that works"


def test_falls_through_to_text_when_the_objectname_is_not_unique():
    target = node("QLabel", name="dup", text="User", handle="o3")
    engine = FakeEngine({"QLabel#dup": ["o3", "o9"], "QLabel[text='User']": ["o3"]})

    assert suggest(target, 1, engine).selector == "QLabel[text='User']"


def test_a_candidate_matching_a_different_object_is_rejected():
    """One match is not enough -- it has to be *this* object."""
    target = node("QLabel", text="User", handle="o3")
    engine = FakeEngine({"QLabel[text='User']": ["o8"], "QLabel": ["o8", "o3"]})

    result = suggest(target, 1, engine)

    assert result.positional
    assert result.selector == "QLabel:nth(1)", "index is this object's place in engine order"


def test_positional_fallback_uses_engine_ordering():
    target = node("QScrollBar", handle="o5")
    engine = FakeEngine({"QScrollBar": ["o2", "o4", "o5", "o7"]})

    result = suggest(target, 1, engine)

    assert result.selector == "QScrollBar:nth(2)"
    assert result.matches == 4
    assert not result.unique


def test_unresolvable_object_does_not_crash():
    result = suggest(node("QLabel", handle="ghost"), 1, FakeEngine({}))
    assert result.selector == "QLabel"
    assert result.matches == 0


# ------------------------------------------------------------------ walking

def test_walk_is_depth_first_with_depths():
    tree = node("A", handle="a", children=[
        node("B", handle="b", children=[node("C", handle="c")]),
        node("D", handle="d"),
    ])
    assert [(d, n["handle"]) for d, n in walk(tree)] == [
        (0, "a"), (1, "b"), (2, "c"), (1, "d")
    ]


def test_walk_handles_an_empty_tree():
    assert list(walk(None)) == []
    assert list(walk({})) == []


def test_root_is_skipped_because_a_window_is_not_inside_itself():
    tree = node("LoginWindow", handle="w", children=[node("QLabel", name="a", handle="o1")])
    engine = FakeEngine({"QLabel#a": ["o1"]})

    results = suggest_all(tree, engine)

    assert [r.node["handle"] for r in results] == ["o1"]
    assert not any("LoginWindow" in s for s in engine.asked)


def test_qt_internals_are_hidden_unless_asked_for():
    tree = node("W", handle="w", children=[
        node("QWidget", name="qt_scrollarea_viewport", handle="o1"),
        node("QLabel", name="real", handle="o2"),
    ])
    engine = FakeEngine({"QLabel#real": ["o2"], "QWidget#qt_scrollarea_viewport": ["o1"]})

    assert [r.node["handle"] for r in suggest_all(tree, engine)] == ["o2"]
    assert len(suggest_all(tree, engine, include_internal=True)) == 2


# ------------------------------------------------------------------ presentation

def test_summary_does_not_tell_you_to_name_qts_own_widgets():
    tree = node("W", handle="w", children=[
        node("QScrollBar", handle="o1"),
        node("QLabel", name="named", handle="o2"),
    ])
    engine = FakeEngine({"QLabel#named": ["o2"], "QScrollBar": ["o1", "o9"]})

    text = summarize(suggest_all(tree, engine))

    assert "1 by objectName" in text
    assert "objectName in the application" not in text, "QScrollBar is not the user's to name"


def test_summary_does_advise_naming_your_own_widgets():
    tree = node("W", handle="w", children=[node("MyCustomThing", handle="o1")])
    engine = FakeEngine({"MyCustomThing": ["o1", "o2"]})

    assert "objectName in the application" in summarize(suggest_all(tree, engine))


def test_table_lists_every_suggestion_with_its_status():
    tree = node("W", handle="w", children=[
        node("QLabel", name="a", text="Hello", handle="o1"),
        node("QScrollBar", handle="o2"),
    ])
    engine = FakeEngine({"QLabel#a": ["o1"], "QScrollBar": ["o2", "o3"]})

    table = format_table(suggest_all(tree, engine))

    assert "QLabel#a" in table and "Hello" in table and "unique" in table
    assert "QScrollBar:nth(0)" in table and "positional" in table


def test_empty_table_says_so():
    assert format_table([]) == "no objects found"
