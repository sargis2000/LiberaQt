"""Opening SmartTime from inside Libero SoC, and trying to drive it.

SmartTime is Libero's static timing analyser. It is not a window inside Libero: it is a separate
``smartsta.exe``, launched from the Design Flow once place and route has produced timing data.
That makes it the same shape of problem as the core configurator in
``test_libero_configurator.py`` -- a child process with its own event loop, reachable only if it
loads an agent of its own.

What this file pins, all of it measured against the real tool:

* the Design Flow entry is ``Open SmartTime``, and its context menu offers ``Open Interactively``;
* triggering it really does start ``smartsta.exe``;
* that process builds a GUI -- ``qwindows.dll`` is loaded -- which is what separates it from
  Libero's Netlist Viewer, a ``QCoreApplication``-only process no injection mode can ever reach.

* and that it can be **attached to and driven**, which is what makes this the case that proves
  cross-architecture injection. SmartTime is 64-bit (``Designer/bin64/``) where Libero is 32-bit,
  so it exercises a path the core configurator does not: that child is 32-bit like its parent.

That last one failed for a long time, and the reason is worth keeping. Everything looked right --
the injection environment reached the process with its token, the 64-bit agent directory was on
its ``QT_PLUGIN_PATH``, ``qwindows.dll`` was loaded, and the DLL loaded cleanly under
``LoadLibrary`` -- yet no agent appeared and nothing anywhere reported an error. The cause was
that every agent build advertised the same plugin key, ``liberaqt``, and Qt binds a key to
exactly one library: the first on the path. 32-bit Libero owned it, and the 64-bit child was
locked out in silence. It was settled by ``QT_DEBUG_PLUGINS=1``, which showed both libraries
claiming the one key, and confirmed by putting the 64-bit directory first -- which broke Libero's
own agent in precisely the same way. Each build now also advertises ``liberaqt_32`` or
``liberaqt_64``, and the launcher names both.

Three properties of the flow that cost real time to discover:

* **Libero runs a second, headless ``smartsta.exe``.** It is its background timing engine: the
  same executable with no Qt loaded at all -- no ``Qt5Core.dll``, no window. Matching on the
  process name alone finds that one and reports that SmartTime opened when nothing has. Only an
  instance with a window on screen is the interactive tool.
* **The flow entry is opened by double-clicking it.** Right-clicking this view answers "the
  context menu did not appear after the right-click" even with nothing else open, so the context
  menu is not a usable route here.

* **A force-killed project keeps nothing.** Reopening one whose Libero was killed rather than
  closed leaves every flow step disabled -- Place and Route offers only ``Help``, Synthesize has
  no ``Run``. So the whole flow has to run in one session; it cannot be prepared once and reused.
* **The flow entry is no completion signal.** It accepts a click while place and route is still
  running, and simply does nothing, so waiting on it proceeds far too early in silence. This
  waits for the ``designer/`` output directory to stop being written to instead.

Slow: creates a project and places and routes it, so several minutes.
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
PROJECT_DIR = Path(r"C:\Users\Public\lqt_smarttime")
PROJECT_NAME = f"st_{os.getpid()}"
PART = "M2S005-1TQ144"
OBJECTS = str(Path(__file__).with_name("objects.yaml"))
HDL_MODULE = "counter"
VERILOG = (
    "module counter (input wire clk, input wire rst_n, output reg [7:0] count);\n"
    "always @(posedge clk or negedge rst_n) begin\n"
    "if (!rst_n) count <= 8'd0; else count <= count + 8'd1;\n"
    "end\n"
    "endmodule\n"
)


def _libero() -> str:
    found = sorted(glob.glob(LIBERO_GLOB))
    if not found:
        pytest.skip("Libero is not installed")
    return found[-1]


def _processes() -> dict[str, str]:
    out = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True).stdout
    return {line.split('","')[1].strip('"'): line.split('","')[0].strip('"')
            for line in out.splitlines() if line.startswith('"')}


def _modules(pid: int) -> list[str]:
    """Module names loaded by a live process, for telling a GUI process from a headless one."""
    script = f"(Get-Process -Id {pid}).Modules | Select-Object -Expand ModuleName"
    out = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                         capture_output=True, text=True).stdout
    return out.split()


def _main_window_title(pid: int) -> str:
    """Title of a live process's main window, empty if it has none on screen."""
    script = f"(Get-Process -Id {pid} -ErrorAction SilentlyContinue).MainWindowTitle"
    return subprocess.run(["powershell", "-NoProfile", "-Command", script],
                          capture_output=True, text=True).stdout.strip()


def _show_dock(win, caption: str) -> bool:
    """Bring a tabified dock to the front.

    Libero remembers its dock layout between runs, so which tab is current is not something a
    test may assume.
    """
    for bar in win.locator("QTabBar").all():
        if bar.is_visible:
            try:
                bar.select_tab(text=caption)
                return True
            except LiberaQtError:
                continue
    return False


def _dismiss_message_boxes(app) -> None:
    """Clear any QMessageBox.

    It can carry the *same* title as the dialog behind it, so this matches on class; matching on
    title makes the window ambiguous and the click lands on the wrong one.
    """
    for _ in range(4):
        box = next((w for w in app.windows if w.tree(depth=0).get("class") == "QMessageBox"), None)
        if box is None:
            return
        for label in ("OK", "Yes", "Close"):
            try:
                box.locator(f"QPushButton[text='{label}']").click()
                break
            except LiberaQtError:
                continue
        time.sleep(2)


def _wait_until_quiet(directory: Path, quiet: float = 60.0, timeout: float = 1800.0) -> bool:
    """Wait for a directory to stop being written to.

    Args:
        directory: The tool output directory to watch.
        quiet: Seconds without a new write that count as finished.
        timeout: Give up after this long.

    Returns:
        True if it went quiet, False on timeout.
    """
    newest, last_change, began = 0.0, time.time(), time.time()
    while time.time() - began < timeout:
        try:
            stamps = [f.stat().st_mtime for f in directory.rglob("*") if f.is_file()]
        except OSError:
            stamps = []
        current = max(stamps) if stamps else 0.0
        if current > newest:
            newest, last_change = current, time.time()
        if newest and time.time() - last_change > quiet:
            return True
        time.sleep(10)
    return False


@pytest.fixture(scope="module")
def smarttime():
    """A placed-and-routed project with SmartTime opened from the Design Flow.

    Yields:
        A ``(app, pid)`` pair: the Libero application, and SmartTime's process id or None.
    """
    shutil.rmtree(PROJECT_DIR, ignore_errors=True)
    PROJECT_DIR.mkdir(parents=True, exist_ok=True)
    hdl_file = PROJECT_DIR / f"{HDL_MODULE}.v"
    hdl_file.write_text(VERILOG, encoding="utf-8")
    smarttime_pid = None

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

        # ---- new project -------------------------------------------------------------------
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

        # ---- import the HDL ----------------------------------------------------------------
        main().menu("File > Import > HDL Source Files").trigger()
        time.sleep(4)
        imp = app.window(title="Import Files")
        field = imp.obj("import_files.file_name")
        # The bare file name, not a full path: the dialog opens in the project location and
        # resolves against it, and a full path is answered with "counter.v File not found".
        field.type(hdl_file.name)
        # Escape the completer popup. It holds an application-wide mouse grab that swallows the
        # click on Open, which is the only reason this step exists.
        if any(w.title == "" for w in app.windows):
            field.press("Escape")
            time.sleep(1)
        _dismiss_message_boxes(app)

        for _ in range(4):
            dialog = next((w for w in app.windows
                           if w.tree(depth=0).get("class") == "Eveetl::EnhancedFileDlg"), None)
            if dialog is None:
                break
            try:
                dialog.locator("QPushButton[text='Open']").click()
            except LiberaQtError:
                pass
            try:
                app.wait_for_idle(timeout=60.0)
            except LiberaQtError:
                pass
            time.sleep(3)
            _dismiss_message_boxes(app)
        else:
            pytest.skip("the import dialog did not close")

        project_root = PROJECT_DIR / PROJECT_NAME
        if not (project_root / "hdl" / f"{HDL_MODULE}.v").is_file():
            pytest.skip("the HDL was not imported")

        # ---- build the hierarchy and set the root ------------------------------------------
        win = main()
        _show_dock(win, "Design Hierarchy")
        time.sleep(2)
        win.obj("main.build_hierarchy").click()
        app.wait_for_idle(timeout=180.0)
        time.sleep(6)
        win.obj("main.hierarchy").first \
           .row(has_text=f"{HDL_MODULE} ({HDL_MODULE}.v)").context_menu("Set As Root")
        app.wait_for_idle(timeout=60.0)
        time.sleep(3)

        # ---- place and route ---------------------------------------------------------------
        win = main()
        _show_dock(win, "Design Flow")
        time.sleep(2)
        with app._session.timeouts.override(900.0):
            win.obj("main.flow").first.item("Place and Route").context_menu("Run", timeout=20.0)
        if not _wait_until_quiet(project_root / "designer"):
            pytest.skip("place and route did not finish")
        try:
            app.wait_for_idle(timeout=300.0)
        except LiberaQtError:
            pass
        time.sleep(10)

        # ---- open SmartTime ----------------------------------------------------------------
        before = set(_processes())
        win = main()
        _show_dock(win, "Design Flow")
        time.sleep(3)

        # Double-click, which is how the tool is opened by hand. Not the context menu: a
        # right-click here answers "the context menu did not appear after the right-click" even
        # with nothing else open and the entry plainly on screen. That is unexplained, and
        # `scroll_into_view()` is not the answer -- it is a silent no-op on this view, because
        # `Flowview::View` is a QAbstractScrollArea rather than the QScrollArea the agent looks
        # for, so it reports `scrolled: false` and raises nothing.
        with app._session.timeouts.override(600.0):
            win.obj("main.flow").first.item("Open SmartTime").double_click(timeout=20.0)

        # Libero runs a *background* smartsta.exe as its timing engine -- same executable, no Qt
        # loaded at all, no window. Taking the first process with the right name finds that one
        # and reports success while nothing has opened, so wait for an instance that actually
        # puts a SmartTime window on screen.
        deadline = time.time() + 420.0
        while time.time() < deadline and smarttime_pid is None:
            for pid, name in _processes().items():
                if pid in before or name.lower() != "smartsta.exe":
                    continue
                if "SmartTime" in _main_window_title(int(pid)):
                    smarttime_pid = int(pid)
                    break
            if smarttime_pid is None:
                time.sleep(5)

        try:
            yield {"app": app, "pid": smarttime_pid}
        finally:
            if smarttime_pid is not None:
                subprocess.run(["taskkill", "/PID", str(smarttime_pid), "/F"],
                               capture_output=True, check=False)


# ------------------------------------------------------------------ opening it


def test_double_clicking_the_flow_entry_opens_smarttime(smarttime):
    """Double-clicking ``Open SmartTime`` in the Design Flow starts the interactive tool.

    Not the context menu: right-clicking this view answers "the context menu did not appear
    after the right-click" even with nothing else open.
    """
    pid = smarttime["pid"]
    assert pid is not None, "no smartsta.exe with a SmartTime window ever appeared"
    assert _processes().get(str(pid), "").lower() == "smartsta.exe"


def test_smarttime_actually_shows_its_window(smarttime):
    """A process that starts and dies would satisfy the test above, so check for a real window.

    The title is the tool's own: ``SmartTime - [Maximum Delay Analysis View]``.
    """
    pid = smarttime["pid"]
    if pid is None:
        pytest.skip("smartsta.exe never started")
    title = _main_window_title(pid)
    assert "SmartTime" in title, f"no SmartTime window; MainWindowTitle={title!r}"


def test_smarttime_builds_a_gui(smarttime):
    """The distinction that decides whether a tool is reachable at all.

    Generic plugins are instantiated by the QPA platform plugin, which exists only once a
    ``QGuiApplication`` has been constructed. Libero's Netlist Viewer loads QtCore alone and can
    therefore never be injected by any mode; SmartTime loads ``qwindows.dll``, so the door is
    open in principle.
    """
    pid = smarttime["pid"]
    if pid is None:
        pytest.skip("smartsta.exe never started")
    modules = [m.lower() for m in _modules(pid)]
    assert "qwindows.dll" in modules, f"no platform plugin loaded: {modules[:20]}"
    assert "qt5widgets.dll" in modules


# ------------------------------------------------------------------ driving it


def test_smarttime_publishes_an_agent_and_can_be_driven(smarttime):
    """The thing this is all for: a second process, of another ABI, driven over its own link.

    This is the case that proves cross-architecture injection works. It failed until each agent
    build was given a key naming its pointer size: Qt binds a plugin key to exactly one library,
    so while every build claimed only ``liberaqt``, 32-bit Libero owned the key and this 64-bit
    child was locked out in silence -- environment delivered, plugin present on its path, and no
    error anywhere. See ``liberaqt.launcher.sibling_plugin_dirs`` and ``AgentPlugin``.
    """
    app, pid = smarttime["app"], smarttime["pid"]
    if pid is None:
        pytest.skip("smartsta.exe never started")

    endpoint = next((e for e in app.child_agents if e.pid == pid), None)
    assert endpoint is not None, (
        f"no agent published a port for pid {pid}; "
        f"ports seen: {[(e.pid, e.port) for e in app.child_agents]}"
    )

    session = app.attach_child(endpoint)
    window = next(w for w in session.windows if "SmartTime" in (w.title or ""))
    assert window.locator("*").count > 0
