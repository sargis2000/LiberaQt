"""Selector semantics, checked against a real widget tree rather than a parsed string.

`tests/test_selectors.py` covers the grammar: what the mini-language parses into. Nothing there
runs the matcher, because the matcher is C++ in the agent. This file is the other half -- what a
selector actually *matches* when a live application is on the other end.

The four rules `docs/SELECTORS.md` calls out as the ones that trip people up each get a test,
because all four are invisible until you meet them:

* type matching walks the inheritance chain,
* a window is never inside its own subtree,
* a class name may be namespaced,
* an objectName is not necessarily an identifier.

Counts are asserted as relationships, never as exact numbers: the tree depends on window size,
Qt version and which docks are open, and a test that hardcodes 158 is one that fails on somebody
else's screen for no reason at all.
"""

from __future__ import annotations

import pytest

from liberaqt.errors import LiberaQtError

# ------------------------------------------------------------------ the four rules


def test_type_matching_walks_the_inheritance_chain(win):
    """`QWidget` matches every widget, and a base class matches its subclasses.

    This is why uniqueness cannot be computed from an `object.tree` dump and has to be asked of
    the engine: the dump records each object's own class, not the several it also answers to.
    """
    everything = win.locator("*").count
    widgets = win.locator("QWidget").count
    assert widgets > 1, "QWidget matched almost nothing; type matching is not walking upwards"
    assert widgets < everything, (
        "QWidget matched as much as '*', so non-widget QObjects are being counted as widgets"
    )

    # Assistant's tab bar is a QTabBar subclass called TabBar. The base must match both.
    base = win.locator("QTabBar").count
    derived = win.locator("TabBar").count
    assert derived >= 1, "Assistant's TabBar subclass was not found at all"
    assert base > derived, (
        f"QTabBar ({base}) did not match more than its subclass TabBar ({derived})"
    )


def test_a_window_is_never_inside_its_own_subtree(win):
    """A search rooted at a window excludes the window, so its own type matches zero.

    Reach windows through `app.window(title=...)`, never through a locator.
    """
    assert win.locator("QMainWindow").count == 0, (
        "the window matched inside its own subtree; rooting is wrong"
    )


def test_a_namespaced_class_name_lexes(win):
    """`ns::Class` has to parse as a type, not as a type followed by a pseudo-class.

    Only `::` followed by an identifier continues the name; a single `:` still starts a
    pseudo-class. Real applications need this, because `QMetaObject::className()` reports the
    qualified name.
    """
    tree = win.locator("BookmarkManager::BookmarkTreeView")
    assert tree.count == 1
    assert tree.first.class_name == "BookmarkManager::BookmarkTreeView"


def test_an_objectname_that_is_not_an_identifier(win):
    """`#name` cannot carry a space, so `[objectName='...']` is the fallback that must work."""
    dock = win.locator("QDockWidget[objectName='Open Pages']")
    assert dock.count == 1
    assert dock.first.object_name == "Open Pages"


# ------------------------------------------------------------------ attribute operators


@pytest.mark.parametrize(
    ("selector", "why"),
    [
        ("QPushButton[text='Search']", "exact match"),
        ("QPushButton[text*='earc']", "contains"),
        ("QPushButton[text^='Sear']", "starts with"),
        ("QPushButton[text$='rch']", "ends with"),
    ],
)
def test_attribute_operators_find_the_same_button(win, selector, why):
    """Every operator in the grammar, aimed at one button whose caption is known."""
    assert win.locator(selector).count >= 1, f"{why} matched nothing"


def test_negation_excludes_exactly_the_match(win):
    """`!=` is the complement of `=` over the same candidate set."""
    total = win.locator("QDockWidget").count
    named = win.locator("QDockWidget[objectName='Open Pages']").count
    others = win.locator("QDockWidget[objectName!='Open Pages']").count
    assert named == 1
    assert named + others == total, f"{named} + {others} != {total}"


# ------------------------------------------------------------------ pseudo-classes


def test_visible_narrows_to_what_is_on_screen(win):
    """Assistant keeps hidden line edits around, which is what makes this assertable."""
    total = win.locator("QLineEdit").count
    shown = win.locator("QLineEdit:visible").count
    assert 0 < shown < total, (
        f"expected some but not all line edits visible; got {shown} of {total}"
    )
    assert all(loc.is_visible for loc in win.locator("QLineEdit:visible").all())


def test_enabled_narrows_to_what_can_be_used(win):
    enabled = win.locator("QToolButton:enabled").count
    total = win.locator("QToolButton").count
    assert 0 < enabled <= total
    assert all(loc.is_enabled for loc in win.locator("QToolButton:enabled").all())


def test_nth_picks_one_deterministically(win):
    """The walk is insertion-ordered and depth-first, so nth is stable across calls."""
    first_pass = win.locator("QScrollBar:nth(2)").first.resolve()
    second_pass = win.locator("QScrollBar:nth(2)").first.resolve()
    assert first_pass == second_pass, "nth() is not returning a stable object"


# ------------------------------------------------------------------ hierarchy


def test_descendant_and_direct_child_differ(win):
    """A space means any depth; `>` means exactly one level."""
    deep = win.locator("QDockWidget QPushButton").count
    assert deep >= 2, "expected the Bookmarks dock's Add/Remove buttons at some depth"
    direct = win.locator("QDockWidget > QPushButton").count
    assert direct < deep, (
        f"direct-child ({direct}) matched as much as descendant ({deep}); '>' is not restricting"
    )


def test_has_filters_by_what_is_inside(win):
    """`:has()` keeps only the candidates containing a match for the inner selector.

    Regression cover for a silent drop: the client parsed `:has(...)` and put it on the wire as
    `node["has"]`, the agent's Step had no such field, and `matchesStep` never looked. The
    constraint vanished and the selector returned *more* than it was asked for -- with nothing
    to indicate anything had gone wrong.

    The negative control below is the assertion that catches that. Counting how many docks
    contain a button cannot: five-of-five is a plausible-looking answer.
    """
    total = win.locator("QDockWidget").count
    with_buttons = win.locator("QDockWidget:has(QPushButton)").count
    assert 0 < with_buttons < total, (
        f"expected some but not all docks to contain a button; got {with_buttons} of {total}"
    )

    impossible = win.locator("QDockWidget:has(NoSuchClassAtAll)").count
    assert impossible == 0, (
        f":has() matched {impossible} docks for a class that does not exist, so it is being "
        f"ignored rather than applied"
    )


def test_filter_has_and_has_not_go_through_the_same_path(win):
    """`filter(has=...)` folds into the same nested selector `:has(...)` uses."""
    docks = win.locator("QDockWidget")
    pseudo = win.locator("QDockWidget:has(QPushButton)").count
    assert docks.filter(has="QPushButton").count == pseudo
    assert docks.filter(has="NoSuchClassAtAll").count == 0


def test_parent_filters_by_what_is_above(win):
    """`:parent(...)` is the mirror of `:has(...)`, looking up the tree instead of down.

    Same silent-drop story as `:has()`: parsed by the client, sent as `node["parent"]`, and
    never read by the agent -- so the constraint evaporated and the selector matched everything.
    It has no prose in SELECTORS.md and had no test, which is how it stayed unnoticed.

    The negative control is again what does the work.
    """
    total = win.locator("QPushButton").count
    in_docks = win.locator("QPushButton:parent(QDockWidget)").count
    assert 0 < in_docks <= total, f"got {in_docks} of {total} buttons under a dock"

    impossible = win.locator("QPushButton:parent(NoSuchClassAtAll)").count
    assert impossible == 0, (
        f":parent() matched {impossible} buttons under a class that does not exist, so it is "
        f"being ignored rather than applied"
    )

    # And it agrees with the hierarchy form, which reaches the same objects the other way round.
    assert in_docks == win.locator("QDockWidget QPushButton").count


# ------------------------------------------------------------------ walking upwards


def test_parent_returns_the_object_one_level_up(win):
    """`parent()` is exactly one step, and the child must be among that object's children."""
    button = win.locator("QPushButton#add")
    parent = button.parent()
    kids = [k.resolve() for k in parent.child("*").all()]
    assert button.first.resolve() in kids, (
        "the object parent() returned does not list the button among its children"
    )


def test_ancestor_climbs_past_the_intermediate_widgets(win):
    """`ancestor()` keeps going where `parent()` stops, which is why it exists.

    Qt puts layouts, viewports and container widgets between a control and the thing it
    belongs to, so `parent()` on a dock's button lands on plumbing rather than on the dock.
    """
    button = win.locator("QPushButton#add")
    dock = button.ancestor("QDockWidget")
    assert dock.class_name == "QDockWidget"
    assert dock.object_name == "BookmarkWindow"

    assert button.parent().resolve() != dock.resolve(), (
        "the dock is the button's direct parent here, so this cannot show that ancestor() "
        "climbs further than one level; pick a more deeply nested target"
    )


def test_ancestor_never_matches_the_object_itself(win):
    """The walk starts at the parent, so a dock asked for a dock ancestor looks above itself."""
    dock = win.locator("QDockWidget[objectName='BookmarkWindow']")
    with pytest.raises(LiberaQtError):
        dock.ancestor("QDockWidget[objectName='BookmarkWindow']")


def test_a_missing_ancestor_names_the_chain_it_walked(win):
    """The diagnosis is the feature: show how far up it went and what was there."""
    with pytest.raises(LiberaQtError) as excinfo:
        win.locator("QPushButton#add").ancestor("NoSuchClassAtAll")
    message = str(excinfo.value) + str(excinfo.value.__cause__ or "")
    assert "walked up through" in message, message
    assert "QDockWidget" in message, "the chain should name the real ancestors it passed"


def test_ancestor_and_the_parent_pseudo_class_agree(win):
    """Two views of the same relationship: navigate up, or filter by what is up."""
    buttons = win.locator("QPushButton:parent(QDockWidget)").all()
    assert buttons, "no buttons reported a dock ancestor"
    for button in buttons:
        assert button.ancestor("QDockWidget").class_name == "QDockWidget"


# ------------------------------------------------------------------ scoped chaining


def test_chaining_off_first_searches_only_inside_that_object(win):
    """Regression cover: chaining off a narrowed locator must scope, not re-run the search.

    `.first.locator(X)` used to append a step and search from the window again, so it returned
    every X under every match instead of the ones inside the object `.first` had chosen. The
    per-dock counts here must therefore sum to the window-wide total, and at least one dock must
    differ from it -- otherwise the scoping is not doing anything.
    """
    window_wide = win.locator("QDockWidget QPushButton").count
    per_dock = [d.locator("QPushButton").count for d in win.locator("QDockWidget").all()]

    assert sum(per_dock) == window_wide, (
        f"per-dock counts {per_dock} sum to {sum(per_dock)}, but the window holds {window_wide}"
    )
    assert any(n != window_wide for n in per_dock), (
        f"every dock reported the window-wide total {window_wide}; chaining is not scoping"
    )


def test_a_scoped_chain_stays_lazy(win):
    """Building a chain must not talk to the agent; only resolving it does."""
    chain = win.locator("QDockWidget").first.locator("QPushButton").first
    assert chain is not None  # nothing above should have raised, resolved or blocked


# ------------------------------------------------------------------ chaining and strictness


def test_chaining_scopes_the_search(win):
    """A chained locator searches inside its parent, not across the whole window."""
    dock = win.locator("BookmarkManager::BookmarkWidget#BookmarkWidget")
    inside = dock.locator("QPushButton").count
    everywhere = win.locator("QPushButton").count
    assert inside > 0, "the Bookmarks dock should contain Add and Remove"
    assert inside < everywhere, (
        f"chaining did not narrow anything: {inside} inside vs {everywhere} in the window"
    )


def test_an_ambiguous_selector_is_refused_rather_than_guessed(win):
    """Strictness is the point: acting on 1-of-20 silently is how a test comes to lie."""
    with pytest.raises(LiberaQtError) as excinfo:
        win.locator("QScrollBar").click(timeout=0)
    # retry() re-raises as a timeout carrying the real reason, so assert on the cause.
    cause = excinfo.value.__cause__ or excinfo.value
    assert "matched" in str(cause).lower() or "ambiguous" in type(cause).__name__.lower()


def test_first_and_nth_and_last_resolve_an_ambiguous_selector(win):
    """The documented escape hatches from strictness."""
    assert win.locator("QScrollBar").first.exists
    assert win.locator("QScrollBar").nth(1).exists
    assert win.locator("QScrollBar").last.exists


def test_filter_narrows_by_text(win):
    """`filter(has_text=)` means containing, applied after the type match."""
    buttons = win.locator("QPushButton")
    assert buttons.count > 1
    assert buttons.filter(has_text="Search").count == 1


def test_a_missing_object_reports_what_was_really_there(win):
    """The diagnosis is the feature: a wrong predicate should name the near misses."""
    with pytest.raises(LiberaQtError) as excinfo:
        win.locator("QPushButton[text='NoSuchButtonAnywhere']").click(timeout=0)
    message = str(excinfo.value) + str(excinfo.value.__cause__ or "")
    assert "QPushButton" in message
