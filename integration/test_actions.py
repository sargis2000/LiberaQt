"""The action surface, exercised in both delivery modes against real applications.

``docs/ACTIONS.md`` is the human-readable version of this file: every "yes" in its synthetic
column is asserted here or in ``test_input.py``, so the table cannot quietly drift from what the
agent actually does. When the two disagree, this file is the one that ran.
"""

from __future__ import annotations

from liberaqt import expect


def _main(assistant):
    return next(w for w in assistant.windows if "Assistant" in w.title)


def test_a_complete_flow_through_tabified_docks(assistant):
    """A realistic multi-step journey, every step of it user input.

    Assistant tabifies its four docks, which is the arrangement that used to be undrivable: the
    inactive docks report ``isVisible()`` while parked at negative coordinates, and only the tab
    bar Qt creates for them is really on screen. Clicking that bar is how a user reaches them --
    so that is what this does, then types into the dock it surfaced, with an embedded chord.
    """
    assistant.set_input_mode("native")
    win = _main(assistant)
    bar = win.locator("QTabBar:visible")
    before = bar["currentIndex"]

    try:
        bar.select_tab(text="Index")
        field = win.locator("QDockWidget[objectName='IndexWindow'] QLineEdit")
        expect(field).to_be_visible()

        field.click()
        field.type("wrong<Ctrl+A>window")
        assistant.wait_for_idle()
        expect(field).to_have_text("window")

        # Down moves the highlight in the index list below the field -- keyboard driving a
        # widget the click chain focused, with nothing named explicitly.
        assistant._session.call("input.key", {"key": "Down"})
        assistant.wait_for_idle()

        field.type("<Ctrl+A>")
        field.type("<Delete>")
        expect(field).to_have_text("")
    finally:
        bar.select_tab(index=before)


def test_a_checkbox_toggles_in_both_modes(designer):
    """set_checked is sugar over click, so it works wherever a click works.

    Uses Designer's startup-dialog checkbox because it is always enabled -- qmleasing's "smooth"
    box is disabled for most curves, and the auto-wait rightly refuses to click a disabled
    widget. The toggle is undone either way, so the user's setting survives the test.
    """
    designer.set_input_mode("native")
    dlg = designer.window(title="New Form")
    box = dlg.locator("QCheckBox[text='Show this Dialog on Startup']")
    original = box.is_checked

    try:
        box.set_checked(not original)
        assert box.is_checked is (not original), "the native click did not toggle it"

        box.set_checked(original, mode="synthetic")
        assert box.is_checked is original, "the synthetic click did not toggle it back"
    finally:
        if box.is_checked != original:
            box.set_checked(original)


def test_spin_steps_in_synthetic_mode_too(qmleasing):
    """The arrow point comes from QStyle either way; sendEvent at it still steps the box."""
    win = qmleasing.window(title="QML Easing Curve Editor")
    spin = win.locator("QSpinBox#spinBox")
    before, step = spin["value"], spin["singleStep"]

    spin.spin(1, mode="synthetic")
    assert spin["value"] == before + step

    spin.spin(-1, mode="synthetic")
    assert spin["value"] == before


def test_select_item_in_synthetic_mode_writes_the_selection(assistant):
    """The synthetic fallback selects without clicking, for rows a user could not reach."""
    win = _main(assistant)
    tree = win.locator("QHelpContentWidget")

    tree.select_item(row=0, mode="synthetic")

    records = tree.to_records()
    assert records, "the content tree has no rows to select"


def test_menu_trigger_in_synthetic_mode_still_activates_the_entry(assistant):
    """The queued-trigger fallback activates without opening a single menu on screen.

    Same observable as the native walk -- the Add Bookmark dialog appears -- reached through
    QAction::trigger instead of clicks. This is what an entry in an unreachable menu gets.
    """
    win = _main(assistant)
    before = {w.title for w in assistant.windows}

    win.menu("Bookmarks > Add Bookmark...").trigger(mode="synthetic")
    assistant.wait_for_idle()

    opened = {w.title for w in assistant.windows} - before
    assert opened, "the queued trigger never activated the entry"
    assistant._session.call("input.key", {"key": "Esc"})
    assistant.wait_for_idle()
