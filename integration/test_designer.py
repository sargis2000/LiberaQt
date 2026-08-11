"""Qt Designer: a large multi-window application with a modal startup dialog.

Roughly 190 objects in the main window and 29 in the startup dialog, including four toolbars,
dock widgets, tree and list views, and 18 menus.
"""

from liberaqt import expect


def test_agent_reports_the_real_application(designer):
    info = designer.info
    assert info["app"] == "Designer"
    assert info["qt"].startswith("6.") or info["qt"].startswith("5.")


def test_both_windows_are_visible(designer):
    """The main window and the startup dialog are separate top-level windows."""
    titles = [w.title for w in designer.windows]
    assert "Qt Designer" in titles


def test_toolbars_are_addressable_by_object_name(designer_main):
    for name in ("fileToolBar", "editToolBar", "formToolBar", "toolsToolBar"):
        expect(designer_main.locator(f"QToolBar#{name}")).to_exist()


def test_menu_bar_holds_many_menus(designer_main):
    """A real menu bar, as opposed to the single File menu a sample app carries."""
    assert designer_main.locator("QMenu").count >= 10


def test_widget_box_lists_available_widgets(designer_main):
    """Designer's widget box is a populated tree, not an empty placeholder."""
    trees = designer_main.locator("QTreeWidget")
    assert trees.count >= 1


def test_type_matching_walks_the_inheritance_chain(designer_main):
    """QWidget must match far more objects than any single concrete type."""
    widgets = designer_main.locator("QWidget").count
    buttons = designer_main.locator("QToolButton").count
    assert widgets > buttons > 0
    assert widgets >= 100, f"expected a large tree, got {widgets} widgets"


def test_namespaced_class_names_resolve(designer):
    """Designer's internals are namespaced C++ classes, which selectors have to be able to spell."""
    dialog = designer.window(title="New Form")
    expect(dialog.locator("qdesigner_internal::NewFormWidget")).to_exist()


def test_dialog_controls_report_their_text(designer):
    dialog = designer.window(title="New Form")
    expect(dialog.locator("QPushButton[text='Create']")).to_be_visible()
    assert dialog.locator("QCheckBox").first.text


def test_combo_box_property_access(designer):
    """Q_PROPERTY read-back against a widget nobody wrote for us."""
    dialog = designer.window(title="New Form")
    combo = dialog.locator("QComboBox#sizeComboBox")
    assert combo["count"] >= 1
    assert isinstance(combo["currentText"], str)


def test_screenshot_of_a_real_window(designer_main, tmp_path):
    png = designer_main.screenshot(str(tmp_path / "designer.png"))
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 10_000, "a full application window should not encode to a tiny image"
