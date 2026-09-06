"""The examples the documentation tells a new user to write, run against a real application.

Documentation rots quietly: a selector that no longer exists still *reads* correctly, and the
person who finds out is a newcomer following the getting-started page on their first afternoon.
So the tutorial's code lives here as well, and if it stops working this suite says so.

Every selector below was taken from `liberaqt inspect` against Qt Assistant rather than invented,
which is exactly what ``docs/getting-started/first-test.md`` tells the reader to do. Writing this
file caught six mistakes in the docs it checks, among them two selectors that never existed, a
menu entry with the wrong capitalisation, and an import path for ``LiberaQtTimeoutError`` that
does not work.

Qt Assistant ships with every Qt installation, so this needs no purpose-built sample application.
"""

from __future__ import annotations

import pytest

from liberaqt import LiberaQtTimeoutError, expect, liberaqt
from liberaqt.errors import ObjectNotFoundError


def _root_cause(exc: BaseException) -> BaseException:
    """The original error at the end of a ``__cause__`` chain.

    Depth varies: an action nests the action's retry around the resolution's, so it is two deep,
    while resolving directly is one. Walking is the only form that is right for both.
    """
    while exc.__cause__ is not None:
        exc = exc.__cause__
    return exc


@pytest.fixture(scope="module")
def app(qt_tool):
    """Qt Assistant, through the shared lookup so ``--liberaqt-qt-bin`` selects the Qt."""
    with liberaqt(default_timeout=15.0) as lq:
        application = lq.launch(str(qt_tool("assistant")), timeout=90.0)
        application.set_input_mode("native")
        yield application


@pytest.fixture
def win(app):
    return app.window(title="Qt Assistant")


# ------------------------------------------------------------------ getting started


def test_the_smallest_thing_that_works(win):
    """The first example on the getting-started page."""
    expect(win.locator("HelpViewer")).to_be_visible()


def test_expect_retries_until_the_condition_holds(win):
    expect(win.locator("QDockWidget#ContentWindow")).to_be_visible()
    expect(win.locator("QToolButton[text='Next']")).to_exist()


def test_counting_and_narrowing(win):
    docks = win.locator("QDockWidget")
    assert docks.count >= 4
    expect(docks.first).to_exist()


# ------------------------------------------------------------------ documented gotchas


def test_a_widget_in_a_background_dock_tab_needs_the_tab_selected_first(win):
    """A background dock's contents are not actionable.

    A dock whose tab is not current has no on-screen position, so the tutorial teaches selecting
    the tab first, and this is why.

    Note the visible-tab-bar loop rather than ``.first``: Assistant has two ``QTabBar``s and the
    first one is hidden, which is the sort of thing that makes a naive example fail.
    """
    for bar in win.locator("QTabBar").all():
        if bar.is_visible:
            bar.select_tab(text="Index")
            break

    field = win.locator("QDockWidget#IndexWindow").first.locator("QLineEdit").first
    field.click()
    field.type("signal")
    assert "signal" in field.text


def test_an_objectname_that_is_not_an_identifier_needs_the_attribute_form(win):
    """``#`` cannot spell a name with a space in it -- Assistant really ships one."""
    expect(win.locator("QDockWidget[objectName='Open Pages']")).to_exist()


def test_a_menu_path_is_matched_exactly(win):
    """``View > Zoom in``, not ``Zoom In``. The agent lists the real entries when it refuses."""
    win.menu("View > Zoom in").trigger()


def test_hidden_ui_becomes_actionable_once_opened(win):
    """The Find bar does not exist on screen until asked for."""
    win.menu("Edit > Find in Text...").trigger()

    box = win.locator("QCheckBox[text='Case Sensitive']")
    box.check()
    expect(box).to_be_checked()
    box.uncheck()
    expect(box).to_be_checked(False)


# ------------------------------------------------------------------ failure shapes


def test_a_missing_locator_surfaces_as_a_timeout(win):
    """Documented in the troubleshooting page, and the exact chain matters.

    An action wraps two retries, so its chain is ``TimeoutError -> TimeoutError ->
    ObjectNotFoundError``; resolving directly gives one level. Asserting on ``__cause__`` alone
    is right for one and wrong for the other.
    """
    with pytest.raises(LiberaQtTimeoutError) as excinfo:
        win.locator("QPushButton#nope").click(timeout=1)
    assert isinstance(_root_cause(excinfo.value), ObjectNotFoundError)

    with pytest.raises(LiberaQtTimeoutError) as excinfo:
        win.locator("QPushButton#nope").resolve(timeout=1)
    assert isinstance(excinfo.value.__cause__, ObjectNotFoundError)


def test_the_agent_reports_what_it_can_do(app):
    """What the concepts page tells the reader to trust instead of documentation."""
    assert app.capabilities["commands"]
    assert app.supports("object.find")
    assert not app.supports("quick.evaluate"), "quick.* is documented as unimplemented"
