"""Every action method, asserted against a running Qt Assistant.

The point of this file is that nothing else in the repository executes a single line of the C++
agent. A green `pytest tests/` says the client parses selectors and maps errors correctly; it
says nothing about whether a click lands. These tests are what make the claims in
`docs/ACTIONS.md` enforceable again.

Assertions follow one rule, taken from the hard-won note in CLAUDE.md: **assert on where input
lands, never on `hasFocus()`**. `QApplication::focusWidget()` is null whenever the application is
not the foreground one, which is normal under test and guaranteed when several are running. So
"the click focused the field" is checked by typing and seeing where the text went.

State is put back where a test changes it, because the application is session-scoped.
"""

from __future__ import annotations

import io

import pytest

from liberaqt import expect

#: The Bookmarks dock's filter box: visible, enabled, empty, and wired to nothing destructive.
#: The ideal scratchpad for text input -- once its dock is actually in front, see `bookmarks`.
FILTER = "QLineEdit#lineEdit"


def show_dock(win, caption: str) -> None:
    """Bring a tabified dock to the front, by clicking its tab as a user would.

    Assistant tabifies its docks, and Qt parks the inactive pages at negative coordinates -- so
    a widget in a background dock is `isVisible()` while sitting somewhere no click could ever
    reach. Native actionability refuses it with "outside its window's on-screen area", which is
    correct, and is the single most likely reason a dock-hosted widget looks unclickable.

    Called per test rather than once per session on purpose: only one dock can be in front at a
    time, so a test that needs Contents undoes what a test needing Bookmarks just did. Making it
    explicit at the point of use is what stops the two quietly fighting over the tab bar.

    Selecting the tab is itself a real click on the tab rectangle, so this doubles as the
    tabified-dock case of `select_tab`.
    """
    for bar in win.locator("QTabBar").all():
        if not bar.is_visible:
            continue
        try:
            bar.select_tab(text=caption)
            return
        except Exception:  # noqa: BLE001 - try the next bar; the skip below is the report
            continue
    pytest.skip(f"no visible tab bar carries a {caption!r} tab in this Assistant layout")


@pytest.fixture
def bookmarks(win):
    """The Bookmarks dock's filter field, with its dock brought to the front first."""
    show_dock(win, "Bookmarks")
    field = win.locator(FILTER)
    expect(field).to_be_visible()
    return field


@pytest.fixture(autouse=True)
def _clean_filter(win):
    """Leave the shared filter box empty however a test finishes with it."""
    yield
    try:
        win.locator(FILTER).fill("")
    except Exception:  # noqa: BLE001 - cleanup must never mask the real failure
        pass


# ------------------------------------------------------------------ text input


def test_type_puts_characters_in_the_field(bookmarks):
    """Real keystrokes, one per character, landing in the field that was clicked."""
    bookmarks.click()
    bookmarks.type("hello")
    expect(bookmarks).to_have_text("hello")


def test_type_is_what_a_click_focused(win, bookmarks):
    """A native click focuses what it hits, so the *window's* keyboard lands in that field.

    This is the focus assertion done the only way that survives the application being in the
    background: type at the window and see which widget received it.
    """
    bookmarks.click()
    win.keyboard.type("routed")
    expect(bookmarks).to_have_text("routed")


def test_type_understands_embedded_chords(bookmarks):
    """Chords ride inside the text: type, select all, type over the selection."""
    bookmarks.click()
    bookmarks.type("first")
    bookmarks.type("<Ctrl+A>second")
    expect(bookmarks).to_have_text("second")


def test_press_edits_the_field_like_a_key_does(bookmarks):
    """A single key press, delivered as a real key carrying both a code and its text."""
    bookmarks.click()
    bookmarks.type("abc")
    bookmarks.press("Backspace")
    expect(bookmarks).to_have_text("ab")


def test_fill_replaces_without_typing(bookmarks):
    """`fill` writes the property in one shot -- the documented non-input escape hatch."""
    bookmarks.fill("written directly")
    expect(bookmarks).to_have_text("written directly")


def test_clear_empties_the_field(bookmarks):
    bookmarks.fill("something")
    bookmarks.clear()
    expect(bookmarks).to_have_text("")


# ------------------------------------------------------------------ keyboard shortcuts


def test_a_window_shortcut_opens_the_find_bar(win):
    """Ctrl+F, then Escape -- a genuine application shortcut, not a poked handler.

    The find bar starts hidden, which is what makes this assertable: its visibility is the
    application's own response to the keystroke. It also exercises the rule that keys are aimed
    at a *window* and Qt hands them to its focus object.
    """
    bar = win.locator("FindWidget")
    if bar.is_visible:
        win.keyboard.press("Escape")
        expect(bar).to_be_hidden()

    win.keyboard.press("Ctrl+F")
    expect(bar).to_be_visible()

    win.keyboard.press("Escape")
    expect(bar).to_be_hidden()


# ------------------------------------------------------------------ checkboxes


def test_check_and_uncheck_toggle_a_real_checkbox(win):
    """`check`/`uncheck` click the style's click rect, and do nothing when already right."""
    win.keyboard.press("Ctrl+F")
    box = win.locator("QCheckBox[text='Case Sensitive']")
    expect(box).to_be_visible()
    was = box.is_checked
    try:
        box.check()
        assert box.is_checked is True
        box.check()  # already checked: must be a no-op, not a toggle
        assert box.is_checked is True
        box.uncheck()
        assert box.is_checked is False
    finally:
        box.set_checked(was)
        win.keyboard.press("Escape")


# ------------------------------------------------------------------ item views


def _first_caption(view) -> str:
    """The first readable cell of a view's first row.

    Taken by value rather than by key: `to_records` names columns after the horizontal header,
    and a tree with no header text keys them positionally instead. Asking for "text" works on a
    table and quietly yields nothing on a tree, which is how these tests came to skip.
    """
    rows = view.to_records()
    if not rows:
        return ""
    return next((str(v).strip() for v in rows[0].values() if str(v).strip()), "")


def test_select_item_selects_a_row_in_a_tree(win):
    """Native selection is a real click on the row, so the view's own click policy applies."""
    show_dock(win, "Contents")
    tree = win.locator("QHelpContentWidget")
    expect(tree).to_be_visible()
    caption = _first_caption(tree)
    assert caption, "the documentation tree read back no rows at all"
    tree.select_item(text=caption)


def test_row_addresses_a_row_by_containing_text(win):
    """`row(has_text=)` means containing; `item(text)` is exact.

    Reading a model needs no actionability, so this one works whichever dock is in front -- it
    is `select_item` above, an actual click, that needs Contents brought forward.
    """
    tree = win.locator("QHelpContentWidget")
    caption = _first_caption(tree)
    assert caption, "the documentation tree read back no rows at all"
    assert tree.row(has_text=caption[: max(3, len(caption) // 2)]).exists
    assert tree.item(caption).exists


# ------------------------------------------------------------------ scrolling


def test_wheel_scrolls_the_help_viewer(win):
    """A wheel event over the viewer moves *its own* scroll bar, as a user's wheel would.

    The bar has to be the viewer's, found by chaining beneath it. Picking the first scrollable
    bar in the window instead finds one belonging to a dock, which the viewer's wheel has no
    business moving -- and that reads as "wheel does nothing" when the wheel is working fine.
    """
    viewer = win.locator("QLiteHtmlWidget")
    expect(viewer).to_be_visible()
    bars = [b for b in viewer.locator("QScrollBar").all() if b["maximum"] > 0]
    assert bars, "the help viewer has nothing to scroll; is a page loaded?"
    bar = bars[0]

    before = bar["value"]
    viewer.wheel(dy=5)
    after = bar["value"]
    assert after > before, f"the wheel moved nothing: value stayed at {before}"

    viewer.wheel(dy=-5)
    assert bar["value"] < after, "scrolling back up did not move the bar"


# ------------------------------------------------------------------ highlighting


def test_highlight_returns_the_rect_it_drew(win):
    """The marker goes where the object is."""
    field = win.locator(FILTER)
    x, y, w, h = field.geometry
    field.highlight(duration=0.3)
    assert w > 0 and h > 0


def test_highlighting_does_not_change_what_selectors_see(win):
    """The point of the whole design: a debugging aid must not perturb what it inspects.

    The overlay is a real QWidget parented into the application, so without the internal-object
    exclusion it would show up in `*` counts, in `QWidget` searches and in `object.tree` -- and
    the tool would be lying to the person using it to find out the truth. Counted *while the
    marker is up*, which is what makes this meaningful: highlight() returns immediately and the
    overlay outlives the call.
    """
    before_all = win.locator("*").count
    before_widgets = win.locator("QWidget").count

    win.locator(FILTER).highlight(duration=5.0)

    assert win.locator("*").count == before_all, "the highlight overlay leaked into the tree"
    assert win.locator("QWidget").count == before_widgets
    assert win.locator("QWidget[objectName='__liberaqt_highlight']").count == 0
    assert win.locator("*[objectName='__liberaqt_highlight']").count == 0


def test_the_overlay_is_absent_from_object_tree_too(win):
    """`object.tree` walks raw children and never consults the matcher.

    So it repeats the exclusion itself, and this is what checks that it does.
    """
    win.locator(FILTER).highlight(duration=5.0)
    dumped = repr(win.tree(depth=-1))
    assert "__liberaqt_highlight" not in dumped, "the overlay appeared in the tree dump"


def test_the_marker_does_not_steal_the_click_it_covers(win, bookmarks):
    """It sits directly over the thing you are about to interact with, so it must be inert.

    Transparent to the mouse and refusing focus. If it were not, highlighting a field would make
    that field unclickable -- the exact opposite of a debugging aid.
    """
    bookmarks.highlight(duration=5.0)
    bookmarks.click()
    bookmarks.type("still works")
    expect(bookmarks).to_have_text("still works")


def test_highlight_does_not_require_actionability(win):
    """A covered or parked object is exactly the one worth looking at.

    The Contents tree is behind another dock tab here, so a click on it would be refused with
    "outside its window's on-screen area". Highlighting must still be allowed.
    """
    show_dock(win, "Bookmarks")
    hidden = win.locator("QHelpContentWidget")
    hidden.highlight(duration=0.3)


def test_a_cell_highlights_the_cell_rather_than_the_whole_view(win):
    """A composite handle names one cell, so the marker should be cell-sized, not view-sized."""
    show_dock(win, "Contents")
    tree = win.locator("QHelpContentWidget")
    caption = _first_caption(tree)
    assert caption, "the documentation tree read back no rows at all"

    row = tree.row(has_text=caption[: max(3, len(caption) // 2)])
    row.highlight(duration=0.3)

    _, _, view_w, view_h = tree.geometry
    _, _, cell_w, cell_h = row.geometry
    assert 0 < cell_h < view_h, (
        f"the cell ({cell_w}x{cell_h}) is not smaller than its view ({view_w}x{view_h}), "
        f"so this cannot show the cell was targeted"
    )


def test_the_marker_is_actually_drawn(win):
    """The one assertion the tests above cannot make: that anything appears at all.

    Every other highlight test here checks what the marker does *not* do -- leak into the tree,
    steal a click, demand actionability. All six of them passed while `highlight()` drew
    absolutely nothing, because a plain QWidget's paintEvent draws nothing and a stylesheet
    border on one is silently ignored without `WA_StyledBackground`. Only looking at the pixels
    catches that, which is why this counts them rather than diffing whole screenshots: Assistant
    animates its own status bar, so "the image changed" would have passed too.
    """
    Image = pytest.importorskip("PIL.Image", reason="pixel assertions need Pillow")

    def red_pixels() -> int:
        # tobytes() rather than getdata(): the latter is deprecated in Pillow 14, and walking
        # raw RGB triples is both stable across versions and faster on a full-window grab.
        raw = Image.open(io.BytesIO(win.screenshot())).convert("RGB").tobytes()
        return sum(
            1 for i in range(0, len(raw), 3)
            if raw[i] > 180 and raw[i + 1] < 80 and raw[i + 2] < 80
        )

    before = red_pixels()
    win.locator("QHelpContentWidget").highlight(duration=5.0, color="#ff0000", width=4)
    after = red_pixels()

    assert after > before + 200, (
        f"highlight() drew nothing detectable: {before} red pixels before, {after} after. "
        f"A stylesheet border needs WA_StyledBackground to paint."
    )
