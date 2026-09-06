"""One end-to-end test: drive Libero SoC from launch to a finished synthesis run.

Deliberately a single linear test function with no fixtures. Every step depends on the one
before it -- there is no useful place to cut it in half -- and a flat script is far easier to
follow against a running application than a chain of module-scoped fixtures.

Run it explicitly; `pytest` on its own will not pick it up, because pyproject pins
`testpaths = ["tests"]`::

    pytest e2e/test_libero_synthesis.py -v -s

It writes a Libero project to disk and runs Synplify, so it takes ten to twenty minutes and is
not something to run by accident.

Input is native throughout: menus are walked by clicking each one open, fields are clicked into
and typed, the part is chosen by clicking its row, and Synthesize is started from a real
right-click. The two exceptions are marked where they happen and say why.

Four things learned the hard way, all of them still encoded below:

* **Never assume the expected window is the only one.** Libero interposes modal dialogs -- an
  update prompt before the main window exists, a path-length warning if the project location is
  long. `_no_unexpected_dialog` reports what is actually on screen rather than letting the next
  step fail for an unrelated reason.
* **Keep the project path short.** Windows MAX_PATH; a deep temp directory triggers that warning
  dialog and everything after it blocks. Hence a fixed short directory, not `tmp_path`.
* **Give the project a unique name.** Libero refuses one that already exists, and the refusal
  surfaces as an unexpected dialog three steps later rather than as an error from the step that
  caused it.
* **Bring a dock forward before touching it.** Libero tabifies its docks and Qt parks the
  inactive pages at negative coordinates, so the Design Flow view is `isVisible()` while sitting
  somewhere no click can reach. Native actionability says exactly that if you forget.
"""

import os
import shutil
import time
from pathlib import Path

import pytest

from liberaqt import LiberaQt, LiberaQtError

LIBERO = r"C:\Microchip\Libero_SoC_2026.1\Libero_SoC\Designer\bin\libero.exe"

#: Short on purpose: Libero warns about long paths with a modal dialog that blocks everything
#: after it, and pytest's tmp_path is far too deep.
PROJECT_DIR = Path(r"C:\Users\Public\lqt_e2e")

#: Unique per run. A leftover directory is easy to end up with -- a previous run's Libero may
#: still hold its files open, so the cleanup below silently does nothing.
PROJECT_NAME = f"lqt_{os.getpid()}"

#: One of the 80 parts the wizard lists for SmartFusion2. Named rather than picked by arrow key,
#: so the test says which device it means instead of depending on the list's sort order.
PART = "M2S005-1TQ144"

HDL_MODULE = "counter"

OBJECTS = str(Path(__file__).with_name("objects.yaml"))

VERILOG = """\
// Minimal synthesisable design used by the LiberaQT end-to-end test.
module counter (
    input  wire       clk,
    input  wire       rst_n,
    output reg  [7:0] count
);
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            count <= 8'd0;
        else
            count <= count + 8'd1;
    end
endmodule
"""


def _main(app):
    """Libero's main window, re-resolved each time: its title gains the project path."""
    return next(w for w in app.windows if w.title.startswith("Libero"))


def _settle(app, seconds=1.5, timeout=60.0):
    """Wait for the UI to go quiet, then let Libero's own background work catch up."""
    app.wait_for_idle(timeout=timeout)
    time.sleep(seconds)


def _no_unexpected_dialog(app, expected=()):
    """Fail with what Libero is actually saying, rather than three steps later."""
    for window in app.windows:
        if window.title in expected or window.title.startswith("Libero"):
            continue
        said = [lbl.text for lbl in window.locator("QLabel").all() if lbl.text and lbl.is_visible]
        raise AssertionError(f"unexpected dialog {window.title!r}: {said[:3]}")


def _show_dock(win, caption):
    """Bring a tabified dock to the front by clicking its tab, as a user does.

    Libero tabifies its docks and Qt parks the inactive pages at negative coordinates, so a
    widget in a background dock is `isVisible()` while sitting somewhere no click can reach.
    Native actionability refuses it by name -- "outside its window's on-screen area".

    Not optional, and not something the default layout can be trusted for: **Libero remembers
    which tab was in front across sessions**. Selecting Catalog in another test was enough to
    make this one fail three steps later, on a click that had worked for weeks.
    """
    for bar in win.obj("main.dock_tabs").all():
        if not bar.is_visible:
            continue
        try:
            bar.select_tab(text=caption)
            return True
        except LiberaQtError:
            continue
    return False


def _type_into(field, text):
    """Type into a field the way a person does: click it, select all, type over the selection.

    Not `fill()`, which writes the property directly -- no per-keystroke handlers, no validator,
    no textEdited. Typing is what proves the field is genuinely reachable and editable.
    """
    field.click()
    field.type(f"<Ctrl+A>{text}")


def test_libero_synthesises_a_project():
    """New project -> SmartDesign -> import HDL -> set root -> synthesise, as a user would."""
    if not Path(LIBERO).is_file():
        pytest.skip(f"Libero is not installed at {LIBERO}")

    shutil.rmtree(PROJECT_DIR, ignore_errors=True)
    PROJECT_DIR.mkdir(parents=True, exist_ok=True)
    hdl_file = PROJECT_DIR / f"{HDL_MODULE}.v"
    hdl_file.write_text(VERILOG, encoding="utf-8")

    with LiberaQt(default_timeout=15.0) as lq:
        app = lq.launch(LIBERO, timeout=240.0, object_map=OBJECTS)
        app.set_input_mode("native")

        # ---- get past the startup prompt --------------------------------------------------
        # "No" rather than "Yes": Yes reaches out to the network. The "do not remind me" box is
        # left alone, because a test has no business changing a user's saved settings -- which
        # also means the prompt is optional, and this still works once somebody has ticked it.
        try:
            app.wait_for_window(title="Information", timeout=60.0).obj("startup.dismiss").click()
        except LiberaQtError:
            pass
        app.wait_for_window(title="Libero", timeout=120.0)
        _settle(app, 2.0)

        # ---- Project > New Project ---------------------------------------------------------
        # A real menu walk: one click opens the menu, the next lands on the entry. The agent
        # queues it, because the wizard is modal and a direct trigger would not return until it
        # closed -- stranding the reply for as long as it was up.
        _main(app).menu("Project > New Project").trigger()
        time.sleep(4)

        titles = [w.title for w in app.windows]
        assert "New project" in titles, f"the wizard did not open: {titles}"
        dlg = app.window(title="New project")

        _type_into(dlg.obj("wizard.project_name"), PROJECT_NAME)
        _type_into(dlg.obj("wizard.project_location"), str(PROJECT_DIR))
        dlg.obj("wizard.next").click()
        _settle(app, 2.0)
        _no_unexpected_dialog(app, expected={"New project"})

        # ---- pick the device ---------------------------------------------------------------
        assert dlg.obj("wizard.die_combo")["count"] > 0, "device families did not load"

        dlg.obj("wizard.part_list").select_item(text=PART)
        _settle(app, 1.0, timeout=30.0)
        assert dlg.obj("wizard.next").is_enabled, "no part got selected"

        dlg.obj("wizard.finish").click()
        _settle(app, 6.0)

        titles = [w.title for w in app.windows]
        assert any(PROJECT_NAME in t for t in titles), f"project was not created: {titles}"
        assert f"{PROJECT_NAME}.prjx" in _main(app).title
        assert (PROJECT_DIR / PROJECT_NAME).is_dir()

        # ---- create a SmartDesign component ------------------------------------------------
        _main(app).menu("File > New > SmartDesign").trigger()
        time.sleep(3)

        titles = [w.title for w in app.windows]
        assert "Create New SmartDesign" in titles, titles
        sd = app.window(title="Create New SmartDesign")
        _type_into(sd.obj("smartdesign.component_name"), "top_sd")
        sd.obj("smartdesign.ok").click()
        _settle(app, 4.0)

        assert "Create New SmartDesign" not in [w.title for w in app.windows], \
            "the SmartDesign dialog did not close"

        # ---- import the HDL source ---------------------------------------------------------
        _main(app).menu("File > Import > HDL Source Files").trigger()
        time.sleep(4)

        titles = [w.title for w in app.windows]
        assert "Import Files" in titles, titles
        imp = app.window(title="Import Files")
        name_field = imp.obj("import_files.file_name")
        name_field.type(str(hdl_file))
        assert name_field.text == str(hdl_file), f"the path was mistyped: {name_field.text!r}"

        # Typing a path opens the dialog's completer: a dropdown listing the files that match,
        # as a *separate top-level popup*. Pick the entry from it, which is what a person does --
        # and which incidentally clears the popup's mouse grab. That grab is why this step is
        # not just a click on Open: while the popup is up, the next click anywhere is swallowed
        # dismissing it, so Open appears to do nothing at all and the dialog simply sits there.
        #
        # Two things to know if this ever needs debugging again. The popup is a borderless
        # QListView with no title, 26 pixels tall, sitting flush under the field -- easy to swear
        # it never appears, though it is stable and does not dismiss itself. And it is a real
        # file listing: to_records() on it returns Name / Size / Type / Date Modified, which is
        # how the entry is addressed by name here rather than by position.
        #
        # `Window` is a `Locator`, so the popup window drives directly as one. Reaching it via a
        # locator instead does not work: a window is never inside its own subtree, and the only
        # other visible QListView in this dialog is its sidebar ("My Computer", "khach").
        popup = next((w for w in app.windows if w.title == ""), None)
        if popup is not None:
            popup.select_item(text=hdl_file.name)

        imp.obj("import_files.open").click()
        _settle(app, 5.0)

        assert "Import Files" not in [w.title for w in app.windows], \
            "the file dialog did not close"

        # What "import" means to Libero: the source is copied under the project's own hdl/.
        project_root = PROJECT_DIR / PROJECT_NAME
        assert (project_root / "hdl" / f"{HDL_MODULE}.v").is_file(), \
            f"the HDL was not imported: {sorted((project_root / 'hdl').glob('*'))}"

        # ---- build the hierarchy and make the module the root ------------------------------
        # Build Hierarchy is a plain QPushButton, not a QAction, and the tree lists nothing
        # until it has been pressed. Set As Root exists only in the context menu, so this is a
        # real right-click and a real click on the entry it opens. Until a root is set Libero
        # refuses to synthesise -- its toolbar tooltip just reads "Please select a root".
        win = _main(app)
        assert _show_dock(win, "Design Hierarchy"), "no tab bar carries a Design Hierarchy tab"
        _settle(app, 2.0)
        win.obj("main.build_hierarchy").click()
        _settle(app, 6.0, timeout=180.0)

        # The module reads "counter (counter.v) [work]", never the bare module name.
        win.obj("main.hierarchy").first.row(
            has_text=f"{HDL_MODULE} ({HDL_MODULE}.v)"
        ).context_menu("Set As Root")
        _settle(app, 3.0)
        _no_unexpected_dialog(app)

        # ---- run synthesis from the Design Flow --------------------------------------------
        # Bring the dock forward first: it is tabified, so while it is not the current page Qt
        # parks it at negative coordinates and no click can reach it.
        if not _show_dock(win, "Design Flow"):
            pytest.fail("no visible tab bar carries a Design Flow tab")
        _settle(app, 2.0)

        # Two waits, deliberately different lengths. Settling after the click takes as long as
        # Synplify does, far past the 15s default -- hence the override. But the action's own
        # retry window stays short: a missing menu entry is never going to appear, and letting
        # it inherit 300s means thousands of right-clicks before the failure is reported.
        #
        # item(), not row(): row(has_text=) means *containing*, and "Verify Pre-Synthesized
        # Design" contains "Synthesize".
        flow = win.obj("main.flow").first
        with app._session.timeouts.override(300.0):
            flow.item("Synthesize").context_menu("Run", timeout=15.0)

        # ---- wait for a netlist ------------------------------------------------------------
        # Asserted on disk rather than on anything in the UI: synthesis is a separate process,
        # and its output file is the unambiguous evidence that it ran.
        synthesis_dir = project_root / "synthesis"
        deadline = time.time() + 600
        netlist = []
        while time.time() < deadline:
            netlist = sorted(synthesis_dir.glob("*.vm")) if synthesis_dir.is_dir() else []
            if netlist:
                break
            time.sleep(2.0)

        produced = sorted(p.name for p in synthesis_dir.glob("*")) if synthesis_dir.is_dir() else []
        assert netlist, (
            f"synthesis produced no netlist within 10 minutes; "
            f"{synthesis_dir} holds {produced[:20] or 'nothing'}"
        )
