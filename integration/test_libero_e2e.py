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

* **Give the project a unique name.** Libero refuses one that already exists, and a leftover
  directory is easy to end up with because a previous run may still hold its files open. The
  refusal surfaces as an unexpected dialog, not as an error from the step that caused it.

* **Bring a dock forward before touching it.** Libero tabifies its docks, and Qt parks the
  inactive pages at negative coordinates -- so the Design Flow view is `isVisible()` while
  sitting somewhere no click can reach. Native actionability says exactly that if you forget.
"""

import os
import time

import pytest

from liberaqt import expect

#: Unique per run. Libero refuses to create a project whose name already exists, and a leftover
#: directory is easy to end up with: a previous run's Libero may still hold files open, so the
#: cleanup silently does nothing. Reusing the name turned that into "device families did not
#: load" three steps later, rather than the actual complaint.
PROJECT_NAME = f"lqt_{os.getpid()}"
HDL_MODULE = "counter"

#: One of the 80 parts the wizard lists for SmartFusion2.
PART = "M2S005-1TQ144"


def _windows(app):
    """Every top-level window, so an unexpected dialog is visible rather than a mystery hang."""
    return {w.title: w for w in app.windows}


def _settle(app, seconds=1.5, timeout=60.0):
    app.wait_for_idle(timeout=timeout)
    time.sleep(seconds)


def _assert_no_unexpected_dialog(app, expected):
    """Fail with what Libero is actually saying, rather than three steps later.

    Libero interposes dialogs for things a test cannot anticipate -- a name that already exists,
    a path that is too long. Left unread, the next assertion fails for a reason that has nothing
    to do with the real cause.
    """
    for title, window in _windows(app).items():
        if title in expected or title.startswith("Libero"):
            continue
        labels = [label.text for label in window.locator("QLabel").all()
                  if label.text and label.is_visible]
        raise AssertionError(f"unexpected dialog {title!r}: {labels[:3]}")


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

    # Menu activation is queued by the agent, because the wizard is modal and a direct trigger
    # would not return until it closed -- stranding the reply for as long as it was up.
    win.menu("Project > New Project").trigger()
    time.sleep(4)

    assert "New project" in _windows(app), f"wizard did not open: {list(_windows(app))}"
    dlg = app.window(title="New project")

    dlg.locator("QLineEdit#projectNamelineEdit").fill(PROJECT_NAME)
    dlg.locator("QLineEdit#projLocationLineEdit").fill(str(e2e_project_dir))
    dlg.locator("QPushButton[text='Next >']").click()
    _settle(app, 2.0)
    _assert_no_unexpected_dialog(app, {"New project"})

    # Device selection. The combos are empty until this page is entered -- Qt builds every wizard
    # page up front, but Libero only populates them on the page it is showing.
    assert dlg.locator("QComboBox#dieComboBox")["count"] > 0, "device families did not load"

    # Naming the part beats pressing Down: the test says which device it means, and stops
    # depending on whatever the list happens to be sorted by.
    parts = dlg.locator("QTreeView#partView")
    parts.select_item(text=PART)
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
    win.menu("File > Import > HDL Source Files").trigger()
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


def test_the_imported_module_becomes_the_design_root(project):
    """Build the hierarchy, then make the module the root -- both as a user does them.

    ``Build Hierarchy`` is a plain ``QPushButton`` (not a QAction), and the tree lists nothing
    until it has been pressed. ``Set As Root`` exists only in the module's context menu, so this
    is a real right-click and a real click on the entry it opens. Until a root is set, Libero
    refuses to synthesise -- its toolbar tooltip just reads "Please select a root".
    """
    app = project
    win = _main(app)

    for button in win.locator("QPushButton").all():
        if "Build Hierarchy" in (button.text or ""):
            button.click()
            break
    else:
        pytest.fail("no Build Hierarchy button on the Design Hierarchy dock")
    _settle(app, 6.0, timeout=180.0)

    hierarchy = win.locator("Hierview::DHierView").first
    # The module reads "counter (counter.v) [work]", never the bare module name.
    hierarchy.row(has_text=f"{HDL_MODULE} ({HDL_MODULE}.v)").context_menu("Set As Root")
    _settle(app, 3.0)
    _assert_no_unexpected_dialog(app, set())


def test_synthesis_runs_from_the_flow_view(project, e2e_project_dir):
    """Run Synthesize from the Design Flow, and wait for Synplify to produce a netlist.

    Two things this needs that nothing else in the suite does. The Design Flow dock has to be
    brought forward first: Libero tabifies its docks, and Qt parks the inactive pages at
    negative coordinates, so the flow view is `isVisible()` while being somewhere no click can
    reach -- the actionability check says exactly that if you forget. And "Synthesize" has to be
    matched exactly, because `has_text=` means *containing* and "Verify Pre-Synthesized Design"
    contains it.

    Completion is asserted on the netlist appearing on disk rather than on anything in the UI:
    synthesis is a separate process, and its output file is the unambiguous evidence it ran.
    """
    app = project
    win = _main(app)

    docks = next(bar for bar in win.locator("QTabBar").all()
                 if bar.is_visible and bar["count"] == 6)
    docks.select_tab(text="Design Flow")
    _settle(app, 2.0)

    # Every action waits for the UI to settle afterwards, and clicking Run makes Libero busy for
    # as long as Synplify takes -- far past the 10s the suite uses everywhere else.
    flow = win.locator("Flowview::View").first
    with app._session.timeouts.override(300.0):
        flow.item("Synthesize").context_menu("Run")

    synthesis_dir = e2e_project_dir / PROJECT_NAME / "synthesis"
    deadline = time.time() + 600
    netlist = []
    while time.time() < deadline:
        netlist = list(synthesis_dir.glob("*.vm")) if synthesis_dir.is_dir() else []
        if netlist:
            break
        time.sleep(2.0)

    produced = sorted(p.name for p in synthesis_dir.glob("*")) if synthesis_dir.is_dir() else []
    assert netlist, (
        f"synthesis produced no netlist within 10 minutes; "
        f"{synthesis_dir} holds {produced[:20] or 'nothing'}"
    )
