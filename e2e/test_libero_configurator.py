"""Driving a dialog that is not in the application at all.

Libero's IP core configurator is not a window inside `libero.exe`. Choosing "Configure core" in
the Catalog spawns a **separate executable**, `coreconfig.exe`, parameterised by the core's XML
and talking back to Libero over local HTTP:

    coreconfig -url "http://127.0.0.1:54666" -Family "SmartFusion2" -Die "M2S005"
               -ConfigFile ".../DirectCore/COREAHBLTOAXI/2.1.101/fs/p0f0/COREAHBLTOAXI.xml" ...

So the agent injected into Libero cannot see it: `app.windows` never mentions it, and no selector
will ever reach it. That is not a gap in the driver -- it is a different process.

It is drivable anyway, because a Qt child inherits the injection environment and therefore loads
an agent of its own. This test is the worked example of that, and the regression guard for the
bug it exposed: every agent used to publish its port to one shared path, so the configurator
overwrote Libero's within seconds of opening. The launcher reads that file once at startup, so
nothing noticed; anything re-reading it would have been talking to the wrong process.

Read-mostly, but it does create a project and a component on disk, and it leaves a configurator
process running unless torn down -- `coreconfig.exe` outlives Libero, so this cleans up after
itself explicitly.
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

LIBERO_GLOB = "C:/Microchip/Libero_SoC_*/Libero_SoC/Designer/bin/libero.exe"
PROJECT_DIR = Path(r"C:\Users\Public\lqt_cfg")
PROJECT_NAME = f"cfg_{os.getpid()}"
PART = "M2S005-1TQ144"
COMPONENT = "my_ahb2axi"
OBJECTS = str(Path(__file__).with_name("objects.yaml"))

#: The catalog lists it as "CoreAHBLTOAXI", not the "COREAHBLTOAXI" the documentation uses, so
#: it is matched on the distinctive part of the name rather than exactly.
CORE = "AHBLTOAXI"


def _libero() -> str:
    found = sorted(glob.glob(LIBERO_GLOB))
    if not found:
        pytest.skip("Libero is not installed")
    return found[-1]


@pytest.fixture(scope="module")
def configured():
    """Libero with a project, a named component, and its configurator open.

    Yields ``(app, child, endpoint)``: the Libero application, the attached configurator, and the
    endpoint the child published.
    """
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

        win = main()
        for bar in win.locator("QTabBar").all():
            if bar.is_visible:
                try:
                    bar.select_tab(text="Catalog")
                    break
                except Exception:  # noqa: BLE001 - try the next bar
                    continue
        time.sleep(3)
        app.wait_for_idle(timeout=60.0)

        core = win.locator("Idedock::CoresViewImpl").first.row(has_text=CORE)
        # timeout=0 deliberately: one attempt, no retry. The default retry loop re-clicks several
        # times a second, and repeated right-clicks on a catalog row trigger its default action --
        # which opens the component dialog on its own and makes the test look like it worked for
        # the wrong reason.
        core.context_menu("Configure core", timeout=0)
        time.sleep(5)

        name = app.window(title="Create Component").locator("QLineEdit#componentName")
        name.click()
        name.type(f"<Ctrl+A>{COMPONENT}")
        try:
            app.window(title="Create Component").locator("QPushButton[text='OK']").click()
        except LiberaQtError:
            # The click is posted; only the idle wait times out, because launching the
            # configurator keeps the application busy well past any sensible idle window.
            pass

        endpoint = app.wait_for_child_agent(timeout=90.0)
        child_pid = endpoint.pid
        child = app.attach_child(endpoint)
        try:
            yield app, child, endpoint
        finally:
            # coreconfig.exe outlives Libero, so closing the driver is not enough.
            if child_pid is not None:
                subprocess.run(["taskkill", "/PID", str(child_pid), "/F"],
                               capture_output=True, check=False)


def test_the_configurator_is_a_separate_process(configured):
    """The premise: Libero's own agent cannot see it, because it is not in Libero."""
    app, _child, endpoint = configured

    assert endpoint.pid != app._process.agent_pid, "the configurator shares Libero's process"
    titles = [w.title for w in app.windows]
    assert not any("Configurator" in t for t in titles), (
        f"Libero's agent should not see the configurator's window, but reported {titles}"
    )


def test_the_child_agent_is_reachable_and_populated(configured):
    """Attaching to it gives a real object tree to drive."""
    _app, child, _endpoint = configured

    titles = [w.title for w in child.windows]
    assert "Configurator" in titles, f"the child reported {titles}"

    window = child.window(title="Configurator")
    assert window.tree(depth=0).get("class") == "Actspiritui::TgiGeneratorDialog"
    assert window.locator("*").count > 50, "the configurator's tree is implausibly small"


def test_the_child_did_not_overwrite_the_parents_port(configured):
    """The regression this whole change exists for.

    One shared port file meant the configurator replaced Libero's port with its own. Both are now
    published under their own pid, so both survive -- and the parent's entry still points at the
    parent.
    """
    app, _child, endpoint = configured
    process = app._process

    published = {e.pid: e.port for e in process.agents()}
    assert process.agent_pid in published, "Libero's own entry has gone"
    assert published[process.agent_pid] == process.port, (
        f"Libero's published port changed from {process.port} to "
        f"{published[process.agent_pid]}; a child overwrote it"
    )
    assert published[endpoint.pid] == endpoint.port
    assert len(published) >= 2


def test_the_component_was_created_on_disk(configured):
    """What naming the component actually did, asserted where it is unambiguous."""
    work = PROJECT_DIR / PROJECT_NAME / "component" / "work"
    assert (work / COMPONENT).is_dir(), (
        f"expected a {COMPONENT} component; found {sorted(p.name for p in work.glob('*'))}"
        if work.is_dir() else f"{work} does not exist"
    )
