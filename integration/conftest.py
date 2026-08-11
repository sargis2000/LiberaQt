"""Fixtures for the real-application suite.

These tests drive Qt's own shipped applications -- Designer, Assistant and Linguist -- rather than
a purpose-built sample. They are large, production Qt Widgets programs written with no knowledge
of LiberaQT, which is the point: a toy application agrees with whatever the driver happens to do,
and a real one does not.

They are also the only large Qt applications guaranteed to be injectable, because they ship inside
the Qt installation itself and are therefore built with exactly the Qt minor version and compiler
ABI that the matching agent is built against.

Point the suite at a Qt installation with ``--liberaqt-qt-bin``, ``LIBERAQT_QT_BIN`` or ``QTDIR``;
with none of those it looks beside ``qmake`` on ``PATH``. Every fixture skips rather than fails
when its application is absent, so a Qt build without qttools still runs the rest.
"""

from __future__ import annotations

import glob
import os
import shutil
import sys
from pathlib import Path

import pytest

from liberaqt import LiberaQt, LiberaQtError

#: Applications this suite knows how to drive, and what each one is here to exercise.
QT_APPS = {
    "designer": "modal startup dialog, namespaced classes, the largest tree",
    "assistant": "dock widgets, a text field to type into",
    "linguist": "objectNames containing spaces and slashes",
    "qdbusviewer": "a main window with an empty title, and a tab widget",
    "qmleasing": "a QWidget shell alongside a live Qt Quick scene",
}


#: Where Libero installs itself by default. It is not part of Qt, so it is found separately.
LIBERO_GLOB = "C:/Microchip/Libero_SoC_*/Libero_SoC/Designer/bin/libero.exe"


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add the application location options."""
    parser.addoption("--liberaqt-qt-bin", default=None,
                     help="directory holding designer/assistant/linguist")
    parser.addoption("--liberaqt-libero", default=None,
                     help="path to libero.exe, for the third-party target")
    parser.addoption("--liberaqt-e2e", action="store_true",
                     help="run the Libero end-to-end flow, which writes projects and synthesises")
    parser.addoption("--liberaqt-e2e-dir", default=r"C:\Users\Public\lqt_e2e",
                     help="where the end-to-end flow puts its project; keep it SHORT (MAX_PATH)")


def _candidate_dirs(configured: str | None) -> list[Path]:
    """Places a Qt bin directory may be, most explicit first."""
    out = []
    for value in (configured, os.environ.get("LIBERAQT_QT_BIN")):
        if value:
            out.append(Path(value))
    qtdir = os.environ.get("QTDIR")
    if qtdir:
        out += [Path(qtdir) / "bin", Path(qtdir)]
    qmake = shutil.which("qmake") or shutil.which("qmake6")
    if qmake:
        out.append(Path(qmake).parent)
    return out


@pytest.fixture(scope="session")
def qt_bin(pytestconfig: pytest.Config) -> Path:
    """Directory holding Qt's shipped applications.

    Returns:
        The first directory that actually contains one of them.
    """
    suffix = ".exe" if sys.platform == "win32" else ""
    for directory in _candidate_dirs(pytestconfig.getoption("--liberaqt-qt-bin")):
        if any((directory / f"{name}{suffix}").is_file() for name in QT_APPS):
            return directory
    pytest.skip("no Qt bin directory found; pass --liberaqt-qt-bin or set QTDIR")


@pytest.fixture(scope="session")
def driver() -> LiberaQt:
    """One driver for the whole session, closing every application it launched."""
    with LiberaQt(default_timeout=10.0) as instance:
        yield instance


def _launch(driver: LiberaQt, qt_bin: Path, name: str):
    exe = qt_bin / (f"{name}.exe" if sys.platform == "win32" else name)
    if not exe.is_file():
        pytest.skip(f"{name} is not part of this Qt installation")
    return driver.launch(str(exe))


@pytest.fixture(scope="session")
def designer(driver: LiberaQt, qt_bin: Path):
    """Qt Designer, once its startup dialog has appeared.

    Designer opens a modal "New Form" dialog a moment after the main window, so the fixture waits
    for it rather than racing it. It is deliberately left open: a modal dialog over a live main
    window is exactly the arrangement a sample application never has, and reading the main window
    underneath it has to keep working.
    """
    app = _launch(driver, qt_bin, "designer")
    app.wait_for_window(title="New Form", timeout=30.0)
    return app


@pytest.fixture(scope="session")
def designer_main(designer):
    """Qt Designer's main window."""
    return designer.window(title="Qt Designer")


@pytest.fixture(scope="session")
def assistant(driver: LiberaQt, qt_bin: Path):
    """Qt Assistant, a dock-heavy help browser."""
    return _launch(driver, qt_bin, "assistant")


@pytest.fixture(scope="session")
def linguist(driver: LiberaQt, qt_bin: Path):
    """Qt Linguist, whose objectNames contain spaces and slashes."""
    return _launch(driver, qt_bin, "linguist")


@pytest.fixture(scope="session")
def qdbusviewer(driver: LiberaQt, qt_bin: Path):
    """Qt D-Bus Viewer, whose main window carries no title at all.

    It reports a connection error on Windows, where there is no session bus. That does not matter
    here: the widget tree is built either way, and an application in an error state is a perfectly
    ordinary thing to have to drive.
    """
    return _launch(driver, qt_bin, "qdbusviewer")


@pytest.fixture(scope="session")
def qmleasing(driver: LiberaQt, qt_bin: Path):
    """QML Easing Curve Editor: a QWidget shell plus a separate Qt Quick window."""
    app = _launch(driver, qt_bin, "qmleasing")
    app.wait_for_idle()
    return app


@pytest.fixture(scope="session")
def quick_window(qmleasing):
    """The Qt Quick top-level of the easing editor, which is the one with no title."""
    return qmleasing.window(title="")


# ------------------------------------------------------------------ third-party target
#
# Everything above ships with Qt. Libero is the opposite: a large commercial application from a
# vendor who has never heard of this project, on a completely different ABI (Qt 5.15, 32-bit,
# MSVC 2019). It is the only target here that proves the ABI resolution actually works, rather
# than always picking the one agent that happens to be installed.


@pytest.fixture(scope="session")
def libero_exe(pytestconfig: pytest.Config) -> str:
    """Path to libero.exe, skipping the suite when Libero is not installed."""
    configured = pytestconfig.getoption("--liberaqt-libero") or os.environ.get("LIBERAQT_LIBERO")
    if configured:
        if not Path(configured).is_file():
            pytest.skip(f"libero.exe not found at {configured}")
        return configured
    found = sorted(glob.glob(LIBERO_GLOB))
    if not found:
        pytest.skip("Libero is not installed; pass --liberaqt-libero=<path to libero.exe>")
    return found[-1]


def _start_libero(driver: LiberaQt, libero_exe: str):
    """Launch Libero and get past its startup prompt.

    Answers the update prompt with "No" -- "Yes" would reach out to the network -- and leaves the
    "Do not remind me again" box alone, because a test suite has no business changing a user's
    saved settings. The prompt is optional so this still works once somebody has ticked it.
    """
    app = driver.launch(libero_exe, timeout=240.0)
    try:
        app.wait_for_window(title="Information", timeout=60.0) \
           .locator("QPushButton[text='No']").click()
    except LiberaQtError:
        pass
    app.wait_for_window(title="Libero", timeout=120.0)
    app.wait_for_idle(timeout=60.0)
    return app


@pytest.fixture(scope="module")
def libero_e2e(driver: LiberaQt, libero_exe: str, e2e_enabled):
    """A Libero instance of its own for the end-to-end flow.

    Deliberately not the session-scoped `libero`: opening a project rewrites the main window
    title, which every fixture matching on "Libero" would then fail to find.
    """
    return _start_libero(driver, libero_exe)


@pytest.fixture(scope="session")
def libero(driver: LiberaQt, libero_exe: str):
    """Libero SoC, past its startup prompt and settled, with no project open."""
    return _start_libero(driver, libero_exe)


@pytest.fixture(scope="session")
def libero_main(libero):
    """Libero's main window."""
    return libero.window(title="Libero")


# ------------------------------------------------------------------ end-to-end flow
#
# Opt-in: unlike everything else here it writes a Libero project to disk and drives a real tool
# flow, so it must never run just because someone typed `pytest integration/`.


@pytest.fixture(scope="session")
def e2e_enabled(pytestconfig: pytest.Config) -> None:
    """Gate the destructive flow behind an explicit flag."""
    if not pytestconfig.getoption("--liberaqt-e2e"):
        pytest.skip("end-to-end flow is opt-in; pass --liberaqt-e2e")


@pytest.fixture(scope="session")
def e2e_project_dir(pytestconfig: pytest.Config, e2e_enabled) -> Path:
    """A short directory for the generated project.

    Deliberately not tmp_path: pytest's temporary directories are long, and Libero puts up a
    modal path-length warning that blocks every subsequent action.
    """
    directory = Path(pytestconfig.getoption("--liberaqt-e2e-dir"))
    if len(str(directory)) > 40:
        pytest.skip(f"--liberaqt-e2e-dir is too long for Libero: {directory}")
    if directory.exists():
        shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


@pytest.fixture(scope="session")
def e2e_hdl_file(e2e_project_dir: Path) -> Path:
    """A minimal synthesisable Verilog module for the import and synthesis steps."""
    path = e2e_project_dir / "counter.v"
    path.write_text(
        "// Minimal synthesisable design used by the LiberaQT end-to-end test.\n"
        "module counter (\n"
        "    input  wire       clk,\n"
        "    input  wire       rst_n,\n"
        "    output reg  [7:0] count\n"
        ");\n"
        "    always @(posedge clk or negedge rst_n) begin\n"
        "        if (!rst_n)\n"
        "            count <= 8'd0;\n"
        "        else\n"
        "            count <= count + 8'd1;\n"
        "    end\n"
        "endmodule\n",
        encoding="utf-8",
    )
    return path
