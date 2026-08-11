"""Qt Linguist: the awkward-objectName case.

Linguist names widgets things like ``comment/context view`` and ``scroll area``. Those cannot be
written after a ``#``, so they exercise the quoted attribute form instead -- a case no sample
application with tidy camelCase names would ever produce.
"""

from liberaqt import expect


def test_menus_are_named(linguist):
    win = linguist.window(title="Qt Linguist[*]")
    for name in ("menuFile", "menuHelp", "menuView", "menuValidation", "menuTranslation"):
        expect(win.locator(f"QMenu#{name}")).to_exist()


def test_object_name_containing_a_slash(linguist):
    """``comment/context view`` only resolves through [objectName='...']."""
    win = linguist.window(title="Qt Linguist[*]")
    expect(win.locator("FormWidget[objectName='comment/context view']")).to_exist()


def test_object_name_containing_a_space(linguist):
    win = linguist.window(title="Qt Linguist[*]")
    expect(win.locator("MessageEditor[objectName='scroll area']")).to_exist()


def test_menu_bar_and_central_widget(linguist):
    win = linguist.window(title="Qt Linguist[*]")
    expect(win.locator("QMenuBar#menubar")).to_exist()
    expect(win.locator("QWidget#centralwidget")).to_be_visible()


def test_window_title_carries_the_modified_placeholder(linguist):
    """Qt's ``[*]`` placeholder is part of the real title; we must not silently normalise it."""
    win = linguist.window(title="Qt Linguist[*]")
    assert "Linguist" in win.title
