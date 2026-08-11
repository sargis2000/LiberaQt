"""Qt Assistant: a dock-widget-heavy help browser.

Around 185 objects, of which Assistant names enough that most are addressable without resorting
to position.
"""

from liberaqt import expect


def test_dock_widgets_are_addressable(assistant):
    win = assistant.window(title="Qt Assistant")
    for name in ("ContentWindow", "IndexWindow", "SearchWindow", "BookmarkWindow"):
        expect(win.locator(f"QDockWidget#{name}")).to_exist()


def test_dock_widget_titles(assistant):
    win = assistant.window(title="Qt Assistant")
    expect(win.locator("QDockWidget#IndexWindow")).to_have_text("Index")
    expect(win.locator("QDockWidget#BookmarkWindow")).to_have_text("Bookmarks")


def test_toolbars_exist(assistant):
    win = assistant.window(title="Qt Assistant")
    for name in ("NavigationToolBar", "FilterToolBar", "AddressToolBar"):
        expect(win.locator(f"QToolBar#{name}")).to_exist()


def test_filter_line_edit_accepts_text(assistant):
    """Typing into a widget of an application that has never heard of us."""
    win = assistant.window(title="Qt Assistant")
    field = win.locator("QLineEdit#lineEdit")
    field.fill("signals")
    expect(field).to_have_text("signals")
    field.clear()
    expect(field).to_have_text("")


def test_stacked_widget_property(assistant):
    win = assistant.window(title="Qt Assistant")
    stack = win.locator("QStackedWidget#stackedWidget")
    assert stack["count"] >= 1


def test_nested_selector_scopes_to_its_parent(assistant):
    """A descendant search must return fewer objects than the same search app-wide."""
    win = assistant.window(title="Qt Assistant")
    everywhere = win.locator("QPushButton").count
    inside = win.locator("QDockWidget#BookmarkWindow QPushButton").count
    assert 0 <= inside < everywhere
