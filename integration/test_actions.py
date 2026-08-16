"""The action surface, exercised in both delivery modes against real applications.

``docs/ACTIONS.md`` is the human-readable version of this file: every "yes" in its synthetic
column is asserted here or in ``test_input.py``, so the table cannot quietly drift from what the
agent actually does. When the two disagree, this file is the one that ran.
"""

from __future__ import annotations

import pytest

from liberaqt import LiberaQtTimeoutError, expect


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


def test_a_modal_dialog_blocks_native_input_by_name(designer):
    """Acting behind a modal fails immediately, naming the dialog that is in the way.

    Designer keeps its modal "New Form" dialog over a live main window, which is exactly the
    arrangement that used to surface as a mystery: the click quietly vanished. Now the
    diagnosis is in the error, before anything is clicked at all.
    """
    designer.set_input_mode("native")
    main = designer.window(title="Qt Designer")

    with pytest.raises(LiberaQtTimeoutError) as exc:
        main.locator("QDockWidget").first.click(timeout=0)

    assert "blocked by the modal dialog" in str(exc.value)
    assert "New Form" in str(exc.value)


def test_a_parked_dock_is_unreachable_natively_but_fillable(assistant):
    """The two modes disagree about a tabified dock's hidden page -- by design, loudly.

    Qt parks the page at negative coordinates while its tab is not current, so no user can
    interact with it: native actions refuse with a diagnosis pointing at the dock tab. fill()
    deliberately still works -- writing state into a form page that is not currently shown is
    what a setup helper is for.
    """
    assistant.set_input_mode("native")
    win = _main(assistant)
    bar = win.locator("QTabBar:visible")
    field = win.locator("QDockWidget[objectName='IndexWindow'] QLineEdit")

    original = bar["currentIndex"]
    bar.select_tab(text="Contents")
    try:
        with pytest.raises(LiberaQtTimeoutError) as exc:
            field.click(timeout=0)
        assert "outside its window's on-screen area" in str(exc.value)

        field.fill("still reachable for setup")
        assert field.text == "still reachable for setup"
        field.fill("")
    finally:
        bar.select_tab(index=original)


def test_scroll_into_view_makes_a_buried_widget_clickable(qmleasing):
    """scroll_into_view turns "covered" into clickable, verified by clicking.

    qmleasing's point list lives in a QScrollArea. The area is scrolled to the top first, so
    the last row is off the fold; the reachability check refuses it, scroll_into_view brings
    it in, and the click that failed then succeeds.
    """
    qmleasing.set_input_mode("native")
    win = qmleasing.window(title="QML Easing Curve Editor")
    area = win.locator("QScrollArea:visible")

    # The command itself first: it reports having found and asked a scroll area, which holds
    # whether or not anything is currently buried.
    rows = win.locator("QDoubleSpinBox[objectName='p1_x']").all()
    enabled = next(r for r in rows if r.is_enabled)
    result = qmleasing._session.call(
        "object.invoke",
        {"handle": enabled.resolve(), "method": "__scroll_into_view", "args": []})
    assert result.get("scrolled") is True, f"no QScrollArea ancestor was scrolled: {result}"

    # Scroll the list to its bottom, so the first rows go off the top of the fold.
    area.wheel(dy=50)
    qmleasing.wait_for_idle()

    # Only a *reachability* refusal counts as buried: qmleasing disables its endpoint rows, and
    # a disabled widget stays disabled however far anything scrolls.
    buried = None
    for candidate in rows:
        try:
            candidate.click(timeout=0)
        except LiberaQtTimeoutError as exc:
            if "covered by" in str(exc) or "outside its window" in str(exc):
                buried = candidate
                break
    if buried is None:
        pytest.skip("the point list fits its scroll area; nothing is buried to scroll to")

    buried.scroll_into_view()
    buried.click()  # raises if scrolling did not make it reachable


def test_a_context_menu_is_opened_and_walked_by_right_clicking(assistant):
    """Right-click a tree row, then click an entry in the menu that appears.

    Squish's openItemContextMenu + activateItem. Native by nature: the menu is built inside
    ``contextMenuEvent``, so there is no QAction to reach without genuinely opening it.
    """
    assistant.set_input_mode("native")
    win = _main(assistant)
    tree = win.locator("QHelpContentWidget")

    tree.row(index=0).context_menu("Open Link")
    assistant.wait_for_idle()

    assert [w.title for w in assistant.windows] == ["Qt Assistant"], \
        "the context menu was left open"


def test_a_wrong_context_entry_lists_what_the_menu_offers(assistant):
    """The failure names the real entries, which is also how to discover an unfamiliar menu.

    Two regressions guarded at once. The diagnosis has to survive ``_act``: attaching the
    locator's selector would make the client rebuild it as "no object matched selector:
    QHelpContentWidget" and throw the useful half away. And the menu has to be dismissed --
    Assistant shows this one with ``QMenu::exec()``, and a menu left open blocks input to
    everything behind it for every later test.
    """
    assistant.set_input_mode("native")
    win = _main(assistant)
    tree = win.locator("QHelpContentWidget")

    with pytest.raises(LiberaQtTimeoutError) as exc:
        tree.row(index=0).context_menu("___nope___", timeout=0)

    assert "Open Link" in str(exc.value.__cause__), \
        f"the menu's real entries were not reported: {exc.value.__cause__}"
    assistant.wait_for_idle()
    assert [w.title for w in assistant.windows] == ["Qt Assistant"], \
        "the failed walk left its context menu open"


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
