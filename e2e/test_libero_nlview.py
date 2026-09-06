"""Reading a schematic out of an NLview canvas, using Libero SoC as the host.

The library part of this is `liberaqt.nlview`, and none of it is Libero's: `NlvQWidget` is
NLview's own Qt binding class and `nlvqtb.dll` its own library, so the same code drives the
canvas in any tool that embeds the engine -- ModelSim and QuestaSim both ship it in this very
installation. What lives here is only the Libero flow needed to get a schematic on screen:
create a project, create a SmartDesign, instantiate a core.

Why it has to go through NLview's own command language at all, all of it measured rather than
assumed:

* nothing inside the canvas is a QObject, so no selector reaches an instance or a pin;
* its meta-object declares 75 methods and not one returns geometry;
* it implements no accessibility -- the canvas reports itself as one opaque rectangle with zero
  children, where an ordinary Qt view reports a child per row with a rect each.

So the only way in is `NlvQWidget::commandLine`, which is exported but is not a slot. That is
what `Locator.call_native` exists for, and this file is the case that justifies it.

Slow: creates a project, a SmartDesign and a configured core, so several minutes.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from liberaqt import LiberaQt, LiberaQtError
from liberaqt.nlview import NLVIEW_CLASS, NlviewCanvas

LIBERO_GLOB = "C:/Microchip/Libero_SoC_*/Libero_SoC/Designer/bin/libero.exe"
PROJECT_DIR = Path(r"C:\Users\Public\lqt_nlview")
PROJECT_NAME = f"nlv_{os.getpid()}"
PART = "M2S005-1TQ144"
OBJECTS = str(Path(__file__).with_name("objects.yaml"))
INSTANCE = "ahb2axi_inst"
CORE = "AHBLTOAXI"


def _libero() -> str:
    found = sorted(glob.glob(LIBERO_GLOB))
    if not found:
        pytest.skip("Libero is not installed")
    return found[-1]


@pytest.fixture(scope="module")
def canvas():
    """A SmartDesign with one core on it, wrapped as an NLview canvas."""
    shutil.rmtree(PROJECT_DIR, ignore_errors=True)
    PROJECT_DIR.mkdir(parents=True, exist_ok=True)
    child_pid = None

    with LiberaQt(default_timeout=20.0) as lq:
        app = lq.launch(_libero(), timeout=240.0, object_map=OBJECTS)
        app.set_input_mode("native")
        try:
            app.wait_for_window(title="Information", timeout=60.0).obj("startup.dismiss").click()
        except LiberaQtError:
            pass
        app.wait_for_window(title="Libero", timeout=120.0)
        app.wait_for_idle(timeout=60.0)
        time.sleep(2)

        def main():
            return next(w for w in app.windows if w.title.startswith("Libero"))

        main().menu("Project > New Project").trigger()
        time.sleep(4)
        dlg = app.window(title="New project")
        for key, value in (("wizard.project_name", PROJECT_NAME),
                           ("wizard.project_location", str(PROJECT_DIR))):
            field = dlg.obj(key)
            field.click()
            field.type(f"<Ctrl+A>{value}")
        dlg.obj("wizard.next").click()
        app.wait_for_idle(timeout=60.0)
        time.sleep(2)
        dlg.obj("wizard.part_list").select_item(text=PART)
        app.wait_for_idle(timeout=30.0)
        time.sleep(1)
        dlg.obj("wizard.finish").click()
        app.wait_for_idle(timeout=90.0)
        time.sleep(8)

        main().menu("File > New > SmartDesign").trigger()
        time.sleep(4)
        create = app.window(title="Create New SmartDesign")
        name = create.locator("QLineEdit#componentName")
        name.click()
        name.type("<Ctrl+A>top_sd")
        create.locator("QPushButton[text='OK']").click()
        app.wait_for_idle(timeout=90.0)
        time.sleep(8)

        win = main()
        for bar in win.locator("QTabBar").all():
            if bar.is_visible:
                try:
                    bar.select_tab(text="Catalog")
                    break
                except LiberaQtError:
                    continue
        time.sleep(3)
        app.wait_for_idle(timeout=60.0)

        # timeout=0: the default retry loop re-clicks, and repeated right-clicks on a catalog
        # row fire its default action, which opens the component dialog by itself.
        win.locator("Idedock::CoresViewImpl").first.row(has_text=CORE) \
           .context_menu("Instantiate in top_sd", timeout=0)
        time.sleep(6)
        if "Create Component" in [w.title for w in app.windows]:
            dialog = app.window(title="Create Component")
            field = dialog.locator("QLineEdit#componentName")
            field.click()
            field.type(f"<Ctrl+A>{INSTANCE}")
            try:
                dialog.locator("QPushButton[text='OK']").click()
            except LiberaQtError:
                pass    # the click lands; only the idle wait times out

        # Instantiating spawns coreconfig.exe, a separate process with its own agent.
        try:
            endpoint = app.wait_for_child_agent(timeout=120.0)
            child_pid = endpoint.pid
            configurator = app.attach_child(endpoint)
            for button in configurator.window(title="Configurator").locator("QPushButton").all():
                if (button.text or "").strip().upper() in ("OK", "GENERATE"):
                    try:
                        button.click()
                    except LiberaQtError:
                        pass
                    break
        except LiberaQtError:
            pytest.skip("the core configurator never appeared")

        time.sleep(20)
        try:
            app.wait_for_idle(timeout=120.0)
        except LiberaQtError:
            pass
        time.sleep(5)

        try:
            yield NlviewCanvas.find(next(w for w in app.windows if w.title.startswith("Libero")))
        finally:
            if child_pid is not None:
                subprocess.run(["taskkill", "/PID", str(child_pid), "/F"],
                               capture_output=True, check=False)


# ------------------------------------------------------------------ finding it


def test_the_canvas_is_found_by_nlviews_own_class(canvas):
    """Libero calls its subclass `Aqnlvcanvas::NlvSDWidget`; the library never needs to know.

    Type matching walks the inheritance chain, so searching for NLview's own `NlvQWidget` finds
    the canvas in any host.
    """
    assert NLVIEW_CLASS == "NlvQWidget"
    assert canvas.version, "the canvas did not answer `version`"


def test_the_engine_answers_and_names_itself(canvas):
    """A real reply through an exported non-slot method, which nothing else here can reach."""
    assert "GUI=QT" in canvas.version, f"unexpected version string: {canvas.version!r}"


def test_the_smartdesign_is_the_loaded_module(canvas):
    assert canvas.module_name.split()[0] == "top_sd"


def test_the_page_has_a_size(canvas):
    width, height = canvas.page_size
    assert width > 0 and height > 0


# ------------------------------------------------------------------ the data


def test_the_instance_is_reported_with_a_position(canvas):
    """What no other route could give: the instance, and where it sits."""
    instances = canvas.instances()
    assert instances, "the canvas reports no instances; did the core land on it?"

    inst = instances[0]
    assert inst.kind == "inst"
    assert INSTANCE in inst.name
    assert inst.x is not None and inst.y is not None, "no position came back"
    assert inst.page == 1


def test_the_instances_pins_are_listed(canvas):
    """Pin names, which is the question this whole route was opened to answer."""
    inst = canvas.instances()[0]
    assert inst.pins, "no pins reported for the instance"
    assert {"ACLK", "ARESETn"} <= set(inst.pins), f"got {inst.pins}"


def test_a_pattern_that_matches_nothing_is_empty_not_an_error(canvas):
    assert canvas.instances(pattern="no_such_instance_*") == []


# ------------------------------------------------------------------ coordinates


def test_schematic_coordinates_convert_to_screen(canvas):
    """The bridge to anything that clicks.

    NLview reports schematic units; the mouse wants pixels.
    """
    inst = canvas.instances()[0]
    x, y = canvas.to_screen(inst.x, inst.y)
    assert isinstance(x, int) and isinstance(y, int)
    # Distinct positions must not collapse onto one point.
    assert canvas.to_screen(inst.x + 50, inst.y + 50) != (x, y)


def test_the_conversion_round_trips(canvas):
    """Screen and schematic have to be inverses, or a computed click lands somewhere else."""
    inst = canvas.instances()[0]
    screen = canvas.to_screen(inst.x, inst.y)
    back = canvas.to_schematic(*screen)
    assert abs(back[0] - inst.x) <= 1 and abs(back[1] - inst.y) <= 1, (
        f"{(inst.x, inst.y)} -> {screen} -> {back}"
    )


# ------------------------------------------------------------------ failure behaviour


def test_a_rejected_command_carries_nlviews_own_complaint(canvas):
    """Its complaint names the correct syntax, which is how the command set was discovered."""
    with pytest.raises(LiberaQtError) as excinfo:
        canvas.command("no_such_nlview_command")
    assert "no_such_nlview_command" in str(excinfo.value)


def test_try_command_reports_failure_without_raising(canvas):
    """`help` is rejected *and* lists every command, so failure is the useful answer."""
    ok, output = canvas.try_command("help")
    assert ok is False
    assert "bbox" in output and "search" in output
