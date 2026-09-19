"""`:has()` and `:parent()` on the other ABI entirely.

`e2e/test_selectors_live.py` proves these against Qt Assistant: Qt 6.7, MinGW, 64-bit, an
application that ships inside the same Qt the agent was built from. This file proves the same
two pseudo-classes against Microchip Libero SoC: Qt 5.15, MSVC, **32-bit**, a large commercial
application from a vendor who has never heard of this project.

That difference is the point. The agent is a Qt plugin, so it must match the application's Qt
minor version *and* its compiler ABI -- a 32-bit MSVC application needs a 32-bit MSVC agent, and
nothing else will load at all. Passing here means the selector engine behaves the same on both
sides of that divide, rather than on the one ABI that happened to be installed.

Both pseudo-classes were silently dropped by the agent until they were implemented: parsed by
the client, put on the wire, and never read. So each test below carries a negative control --
asking for something impossible and requiring zero. Counting real matches cannot catch a filter
that is not filtering, because "all of them" is a perfectly plausible-looking answer.

Read-only: this opens no project and changes nothing on disk, unlike `test_libero_synthesis.py`.
"""

from __future__ import annotations

import glob
import io
from pathlib import Path

import pytest

from liberaqt import LiberaQt, LiberaQtError

#: Where Libero installs itself. Newest first, so a machine with several picks the latest.
LIBERO_GLOB = "C:/Microchip/Libero_SoC_*/Libero_SoC/Designer/bin/libero.exe"


def _libero_exe() -> str:
    found = sorted(glob.glob(LIBERO_GLOB))
    if not found:
        pytest.skip("Libero is not installed")
    return found[-1]


@pytest.fixture(scope="module")
def libero_win():
    """Libero's main window, past its startup prompt, with no project open.

    Its own driver rather than the session-scoped one in conftest: that one is wired to Qt's
    shipped applications, and this is deliberately a different ABI.
    """
    exe = _libero_exe()
    if not Path(exe).is_file():
        pytest.skip(f"Libero is not installed at {exe}")

    with LiberaQt(default_timeout=15.0) as lq:
        app = lq.launch(exe, timeout=240.0)
        # "No" rather than "Yes": Yes reaches out to the network. Optional, because somebody may
        # already have ticked "do not remind me".
        try:
            app.wait_for_window(title="Information", timeout=60.0) \
               .locator("QPushButton[text='No']").click()
        except LiberaQtError:
            pass
        app.wait_for_window(title="Libero", timeout=120.0)
        app.wait_for_idle(timeout=60.0)
        yield next(w for w in app.windows if w.title.startswith("Libero"))


def test_the_agent_loaded_on_this_abi_at_all(libero_win):
    """The precondition everything else here rests on: a 32-bit MSVC agent in a 32-bit MSVC app.

    Stated as its own test so that an ABI mismatch reads as "the agent did not load" rather than
    as a selector bug in the tests below.
    """
    assert libero_win.locator("*").count > 100, "the object tree is implausibly small"
    assert libero_win.locator("QWidget").count > 10


def test_has_filters_by_what_is_inside(libero_win):
    """`:has()` keeps only the candidates containing a match, on Qt 5.15 as on 6.7."""
    total = libero_win.locator("QDockWidget").count
    assert total > 1, "expected Libero to have several docks"

    with_views = libero_win.locator("QDockWidget:has(QAbstractItemView)").count
    assert 0 < with_views <= total, f"got {with_views} of {total} docks containing a view"

    impossible = libero_win.locator("QDockWidget:has(NoSuchClassAtAll)").count
    assert impossible == 0, (
        f":has() matched {impossible} docks for a class that does not exist, so it is being "
        f"ignored rather than applied"
    )


def test_has_agrees_with_the_descendant_form(libero_win):
    """A dock matches `:has(X)` exactly when a descendant search beneath it finds an X."""
    for dock in libero_win.locator("QDockWidget").all():
        contains_view = dock.locator("QAbstractItemView").count > 0
        name = dock.object_name
        matched = libero_win.locator(
            f"QDockWidget[objectName='{name}']:has(QAbstractItemView)"
        ).count
        assert bool(matched) == contains_view, (
            f"dock {name!r}: :has() said {bool(matched)}, but chaining found "
            f"{dock.locator('QAbstractItemView').count} views inside it"
        )


def test_parent_filters_by_what_is_above(libero_win):
    """`:parent()` is the mirror of `:has()`, and must agree with the hierarchy form."""
    hierarchy_form = libero_win.locator("QDockWidget QAbstractItemView").count
    parent_form = libero_win.locator("QAbstractItemView:parent(QDockWidget)").count
    assert parent_form == hierarchy_form, (
        f":parent() found {parent_form} views under a dock, the descendant form "
        f"{hierarchy_form}; the two must reach the same objects"
    )

    impossible = libero_win.locator("QAbstractItemView:parent(NoSuchClassAtAll)").count
    assert impossible == 0, (
        f":parent() matched {impossible} views under a class that does not exist"
    )


def test_parent_walks_past_intermediate_widgets(libero_win):
    """`:parent()` means *any* ancestor, not just the immediate one.

    Libero nests its views several layers below the dock -- dock, then a page widget, then
    usually a splitter -- so a `:parent()` that only checked `object->parent()` would find
    nothing here even though the descendant form finds plenty.
    """
    under_dock = libero_win.locator("QAbstractItemView:parent(QDockWidget)").count
    directly_under = libero_win.locator("QDockWidget > QAbstractItemView").count
    assert under_dock > 0, "no view reported a QDockWidget ancestor at any depth"
    assert under_dock >= directly_under
    assert under_dock > directly_under, (
        "every view sits immediately under its dock, so this cannot show that :parent() "
        "walks further than one level; pick a deeper-nested target"
    )


def test_ancestor_reaches_the_dock_a_deep_view_lives_in(libero_win):
    """`ancestor()` on the ABI where the nesting is deepest.

    Libero buries its views several layers under the dock, so this is the case that makes the
    parent/ancestor distinction concrete: `parent()` lands on plumbing, `ancestor()` on the dock.
    """
    views = libero_win.locator("QAbstractItemView:parent(QDockWidget)").all()
    assert views, "no item view in Libero reported a dock ancestor"

    view = views[0]
    dock = view.ancestor("QDockWidget")

    # Asserted by identity, not by class name: Libero subclasses QDockWidget into its own
    # namespaced `Aqwidget::AQDockWidget`, so `ancestor("QDockWidget")` correctly returns
    # something whose className() is not "QDockWidget" at all. Type matching walks the
    # inheritance chain here exactly as it does in a downward search, and checking the string
    # would assert the opposite of what is wanted.
    every_dock = [d.resolve() for d in libero_win.locator("QDockWidget").all()]
    assert dock.resolve() in every_dock, (
        f"ancestor() returned {dock.class_name!r}, which is not among the window's docks"
    )

    assert view.parent().resolve() != dock.resolve(), (
        "expected at least one widget between the view and its dock in Libero"
    )


def test_parent_of_a_view_is_still_a_real_object(libero_win):
    """One step up must land somewhere sane, and the view must be among its children."""
    view = libero_win.locator("QAbstractItemView:parent(QDockWidget)").all()[0]
    parent = view.parent()
    assert parent.class_name, "the parent reported no class name"
    assert view.resolve() in [k.resolve() for k in parent.child("*").all()]


def test_a_missing_ancestor_names_the_chain_it_walked(libero_win):
    """The failure has to be a diagnosis on this ABI too, not just a bare not-found."""
    view = libero_win.locator("QAbstractItemView:parent(QDockWidget)").all()[0]
    with pytest.raises(LiberaQtError) as excinfo:
        view.ancestor("NoSuchClassAtAll")
    message = str(excinfo.value) + str(excinfo.value.__cause__ or "")
    assert "walked up through" in message, message


def test_the_namespaced_libero_classes_still_lex(libero_win):
    """Libero's own classes are namespaced, which is what forced `::` into the lexer.

    Retained here because Assistant's `BookmarkManager::BookmarkTreeView` is a Qt-side example
    and this is a third-party one, on the ABI where it was originally found.
    """
    assert libero_win.locator("Hierview::DHierView").count >= 0  # must parse, may be absent
    assert libero_win.locator("Flowview::View").count >= 0


def test_highlight_draws_and_does_not_perturb(libero_win):
    """`highlight()` on Qt 5.15 / MSVC / 32-bit, checked both ways that matter.

    The marker has to actually appear -- six tests once passed against a highlight that drew
    nothing at all, because a plain QWidget ignores a stylesheet border without
    `WA_StyledBackground` -- and it must stay invisible to the engine, since its whole job is
    telling you the truth about what a selector found.
    """
    image = pytest.importorskip("PIL.Image", reason="pixel assertions need Pillow")

    def red_pixels() -> int:
        raw = image.open(io.BytesIO(libero_win.screenshot())).convert("RGB").tobytes()
        return sum(1 for i in range(0, len(raw), 3)
                   if raw[i] > 180 and raw[i + 1] < 80 and raw[i + 2] < 80)

    views = [v for v in libero_win.locator("QAbstractItemView:parent(QDockWidget)").all()
             if v.is_visible]
    if not views:
        pytest.skip("no visible item view in Libero's default layout")

    # Warm-up grab before counting. Qt allocates a paint-related object the first time a window
    # is grabbed -- once, then never again -- and measuring across that allocation blames the
    # resulting +1 on the highlight. Measured on Libero: 1212 objects, 1213 after the first
    # screenshot, 1213 for every screenshot after it.
    red_pixels()

    before_all = libero_win.locator("*").count
    before_red = red_pixels()

    views[0].highlight(duration=5.0, color="#ff0000", width=4)

    assert red_pixels() > before_red + 100, "the marker was not drawn on this ABI"
    assert libero_win.locator("*").count == before_all, "the overlay leaked into the tree"
    assert libero_win.locator("*[objectName='__liberaqt_highlight']").count == 0
