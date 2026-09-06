"""The Qt Quick side, driven against qmleasing.

`qmleasing` is the useful target because it is *both*: a QWidget shell and a separate live Qt
Quick window in one process. An application that is only one or the other cannot show that the
two trees are reported consistently, which is exactly where this used to go wrong.

Three bugs are pinned here, all of which reported something plausible rather than failing:

* `object.tree` returned the Quick window and zero children, while `object.find` saw forty
  objects inside it. The walk was fine -- `find` goes through `visualChildren`, which steps from
  a `QQuickWindow` into its `contentItem`. `dumpTree` just had its own copy that did not, and
  additionally dropped anything that was neither `QWidget` nor `QWindow`; a `QQuickItem` is
  neither. That is why `liberaqt inspect` printed "no objects found" for a window full of them.

* `Window.kind` answered `"widget"` for every window ever, including a `QQuickView`. The agent
  only sent `window_type` from `window.list`, and `kind` reads `object.info`, so it always fell
  through to its default.

* A rootless search found no widgets at all, because `rootObjects()` enumerated only
  `QGuiApplication::topLevelWindows()` and a `QWidget` is not a `QObject` child of its backing
  window.

Counts are asserted as relationships. qmleasing renders a grid of curve previews, so its object
count tracks the window height -- measured at 155 objects in a 669px-tall window and 355 in a
720px one. Anything hardcoded here would be measuring the screen, not the driver.
"""

from __future__ import annotations

import sys

import pytest

from liberaqt import LiberaQt
from liberaqt import selectors as sel
from liberaqt.locator import Locator

EXE = ".exe" if sys.platform == "win32" else ""


@pytest.fixture(scope="module")
def easing(qt_tool):
    """qmleasing, settled, with both of its windows up.

    Located through the shared ``qt_tool`` fixture, so ``--liberaqt-qt-bin`` selects which Qt
    installation this runs against.
    """
    with LiberaQt(default_timeout=10.0) as lq:
        app = lq.launch(str(qt_tool("qmleasing")), timeout=90.0)
        app.wait_for_idle(timeout=30.0)
        if len(app.windows) < 2:
            pytest.skip("qmleasing did not open both its widget shell and its Quick window")
        yield app


@pytest.fixture(scope="module")
def quick_window(easing):
    """The Qt Quick top-level, which is the one with no title."""
    return next(w for w in easing.windows if not w.title)


@pytest.fixture(scope="module")
def widget_window(easing):
    """The QWidget shell, which carries the application's title."""
    return next(w for w in easing.windows if w.title)


def _node_count(node: dict) -> int:
    return 1 + sum(_node_count(child) for child in node.get("children", []))


# ------------------------------------------------------------------ window kind


def test_a_quick_top_level_is_reported_as_quick(quick_window):
    """Regression cover: this answered "widget" for every window in existence.

    `window_type` was only ever sent by `window.list`, and `kind` asks `object.info` -- so the
    default won every time and the answer happened to be right only for widget windows.
    """
    assert quick_window.kind == "quick"


def test_a_widget_top_level_is_still_reported_as_widget(widget_window):
    """The other half: the fix must not simply flip the answer."""
    assert widget_window.kind == "widget"


# ------------------------------------------------------------------ the tree walk


def test_object_tree_descends_into_the_quick_scene(quick_window):
    """The tripwire CLAUDE.md noted had been lost when the old suite was deleted.

    It used to be an xfail(strict=True) so that fixing the walk would force the marker off.
    Written as a plain assertion now because the walk *is* fixed, and this fails the moment it
    breaks again.
    """
    tree = quick_window.tree(depth=-1)
    assert tree.get("children"), "the Quick window still reports no children at all"
    assert _node_count(tree) > 1


def test_the_tree_and_the_engine_agree_about_the_quick_window(quick_window):
    """They disagreed completely: find saw 40 objects where tree saw one.

    Not asserted as equality -- `visual_only` drops non-visual QObjects that `*` still matches --
    but the tree must no longer be the single bare node it used to be.
    """
    found = quick_window.locator("*").count
    nodes = _node_count(quick_window.tree(depth=-1))
    assert found > 1, "the engine cannot see into the Quick scene either; something else is wrong"
    assert nodes > 1, f"tree reported {nodes} node(s) where the engine found {found} objects"


def test_quick_items_are_reachable_and_described(quick_window):
    """QML objects resolve and their properties read back, which was always true."""
    items = quick_window.locator("QQuickItem")
    assert items.count > 0, "no QQuickItem under the Quick window"
    assert items.first.class_name


# ------------------------------------------------------------------ rootless search


def test_a_rootless_search_reaches_widgets(easing):
    """`TODO(m0)`: rootObjects() enumerated only top-level *windows*.

    A QWidget is not a QObject child of its backing QWindow, so a search with no root found zero
    widgets in an application full of them -- silently, as an empty result rather than an error.
    """
    session = easing._session
    widgets = Locator(session, sel.coerce("QWidget"), None).count
    assert widgets > 0, "a rootless search still finds no widgets"


def test_a_rootless_search_reaches_quick_items_too(easing):
    """Both trees have to be reachable from the same rootless search, not one or the other."""
    session = easing._session
    assert Locator(session, sel.coerce("QQuickItem"), None).count > 0


def test_a_rootless_search_does_not_report_anything_twice(easing):
    """A widget's backing window is skipped, or its whole subtree is walked twice.

    Both the QWidget and its QWindow are top-level objects; enumerating both without skipping
    would report every widget in the application two times over.
    """
    session = easing._session
    handles = [loc.resolve() for loc in Locator(session, sel.coerce("QWidget"), None).all()]
    assert len(handles) == len(set(handles)), (
        f"{len(handles) - len(set(handles))} widgets were reported more than once"
    )
