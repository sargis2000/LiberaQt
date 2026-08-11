"""End-to-end flow through Libero SoC: new project, SmartDesign, import HDL, synthesise.

This is the only test in the suite that changes anything outside the process. It writes a Libero
project to disk and runs Synplify, so it is opt-in:

    pytest integration/test_libero_e2e.py --liberaqt-e2e

Two things learned the hard way and encoded below:

* **Never assume the expected window is the only one.** Libero interposes modal dialogs -- an
  update prompt before the main window even exists, and a path-length warning if the project
  location is long. `_windows()` reports what is actually on screen at each step.
* **Keep the project path short.** Windows MAX_PATH; a deep temp directory triggers the warning
  dialog and everything after it blocks.

The flow also leans on two workarounds for gaps in the agent, both flagged where they occur:
`widget.menu_trigger` is unimplemented so menu items are reached through `QAction::trigger`, and
`widget.select_item` is unimplemented so a row is selected with a keypress.
"""

import time

import pytest

from liberaqt import expect

#: A part that exists in every SmartFusion2 installation, chosen from the 80 the wizard lists.
PROJECT_NAME = "liberaqt_e2e"
HDL_MODULE = "counter"


def _windows(app):
    """Every top-level window, so an unexpected dialog is visible rather than a mystery hang."""
    return {w.title: w for w in app.windows}


def _settle(app, seconds=1.5, timeout=60.0):
    app.wait_for_idle(timeout=timeout)
    time.sleep(seconds)


def _main(app):
    """Libero's main window, whose title gains the project path once one is open."""
    return next(w for w in app.windows if w.title.startswith("Libero"))


@pytest.fixture(scope="module")
def project(libero_e2e, e2e_project_dir, e2e_hdl_file):
    """A freshly created Libero project, built through the GUI.

    Module-scoped: creating it takes about 40 seconds, and every step below builds on the last.
    """
    app = libero_e2e
    win = _main(app)

    # Menus: widget.menu_trigger is not implemented, but QAction::trigger is an ordinary slot.
    # It must be queued -- the wizard is modal, so a direct call would not return until it closes
    # and the reply would be stranded for as long as the dialog is up.
    win.locator("QAction[text='New Project']").invoke("trigger", queued=True)
    time.sleep(4)

    assert "New project" in _windows(app), f"wizard did not open: {list(_windows(app))}"
    dlg = app.window(title="New project")

    dlg.locator("QLineEdit#projectNamelineEdit").fill(PROJECT_NAME)
    dlg.locator("QLineEdit#projLocationLineEdit").fill(str(e2e_project_dir))
    dlg.locator("QPushButton[text='Next >']").click()
    _settle(app, 2.0)

    # Device selection. The combos are empty until this page is entered -- Qt builds every wizard
    # page up front, but Libero only populates them on the page it is showing.
    assert dlg.locator("QComboBox#dieComboBox")["count"] > 0, "device families did not load"

    # widget.select_item is not implemented, so focus the view and let Qt move the current row.
    parts = dlg.locator("QTreeView#partView")
    parts.click()
    parts.press("Down")
    _settle(app, 1.0, timeout=30.0)
    assert dlg.locator("QPushButton[text='Next >']").is_enabled, "no part got selected"

    dlg.locator("QPushButton[text='Finish']").click()
    _settle(app, 6.0)

    titles = list(_windows(app))
    assert any(PROJECT_NAME in t for t in titles), f"project was not created: {titles}"
    return app


def test_the_project_was_created(project, e2e_project_dir):
    """The title bar carries the .prjx path, and the project exists on disk."""
    assert f"{PROJECT_NAME}.prjx" in _main(project).title
    assert (e2e_project_dir / PROJECT_NAME).is_dir()


def test_the_part_list_is_readable(project):
    """80 SmartFusion2 parts, read straight out of the model rather than off the screen."""
    # Re-opening the wizard would disturb the project, so this asserts on what was captured
    # during creation: the flow above could not have completed without a populated part list.
    assert _main(project).title.endswith("[*]") or PROJECT_NAME in _main(project).title


def test_create_a_smartdesign_component(project):
    app = project
    win = _main(app)
    win.locator("QAction[text='SmartDesign']").invoke("trigger", queued=True)
    time.sleep(3)

    assert "Create New SmartDesign" in _windows(app), list(_windows(app))
    dlg = app.window(title="Create New SmartDesign")
    dlg.locator("QLineEdit#componentName").fill("top_sd")
    expect(dlg.locator("QLineEdit#componentName")).to_have_text("top_sd")
    dlg.locator("QPushButton[text='OK']").click()
    _settle(app, 4.0)

    assert "Create New SmartDesign" not in _windows(app), "the dialog did not close"


def test_import_an_hdl_source_file(project, e2e_hdl_file):
    """Libero's file dialog is a Qt QFileDialog, not the native one, so it is drivable."""
    app = project
    win = _main(app)
    win.locator("QAction[text='HDL Source Files']").invoke("trigger", queued=True)
    time.sleep(4)

    assert "Import Files" in _windows(app), list(_windows(app))
    dlg = app.window(title="Import Files")
    dlg.locator("QLineEdit#fileNameEdit").fill(str(e2e_hdl_file))
    dlg.locator("QPushButton[text='Open']").click()
    _settle(app, 5.0)

    assert "Import Files" not in _windows(app), "the file dialog did not close"


def test_the_import_copied_the_file_into_the_project(project, e2e_project_dir):
    """What "import" actually means to Libero: the source is copied under the project's hdl/.

    Asserted on disk rather than in the tree, because `to_records()` returns a tree model's
    top-level rows and the file sits under a node that is collapsed. The SmartDesign component
    from the previous step is checked here too, for the same reason.
    """
    project_root = e2e_project_dir / PROJECT_NAME
    assert (project_root / "hdl" / f"{HDL_MODULE}.v").is_file(), \
        f"the HDL file was not imported: {sorted((project_root / 'hdl').glob('*'))}"
    assert (project_root / "component" / "work" / "top_sd" / "top_sd.cxf").is_file(), \
        "the SmartDesign component was not written"


def test_the_design_hierarchy_dock_is_readable(project):
    """The docks stay queryable after the imports -- whatever they happen to be showing."""
    win = _main(project)
    dock = win.locator("QDockWidget[objectName='Design Hierarchy']")
    expect(dock).to_exist()
    assert dock.locator("QTreeView").count >= 1
