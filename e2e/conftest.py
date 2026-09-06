"""Shared launches for the live-application suite.

These tests drive Qt's own shipped programs rather than a purpose-built sample, for the reason
`docs/CONTRIBUTING.md` gives: a toy application agrees with whatever the driver happens to do, a
real one does not. Assistant is the main target -- it is small, starts fast, and happens to
contain one of nearly everything the action surface claims to handle.

The applications are session-scoped because launching one costs seconds and the tests below are
read-mostly. Anything that leaves state behind puts it back.

Point the suite at a Qt installation with ``--liberaqt-qt-bin``, ``LIBERAQT_QT_BIN`` or
``QTDIR``; with none of those it looks beside ``qmake`` on ``PATH``, then in the usual Windows
install location. Every fixture skips rather than fails when its application is absent.
"""

from __future__ import annotations

import glob
import os
import shutil
import sys
from pathlib import Path

import pytest

from liberaqt import LiberaQt

EXE = ".exe" if sys.platform == "win32" else ""


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add the application location option."""
    parser.addoption("--liberaqt-qt-bin", default=None,
                     help="directory holding assistant/designer/linguist")


def _candidates(configured: str | None) -> list[Path]:
    """Where to look for Qt's applications, best first.

    An explicit choice is *exclusive*. Falling back past it would mean pinning the suite to Qt
    6.5, silently getting 6.7 because the path was wrong, and believing 6.5 had been tested --
    the failure mode the pin exists to prevent. ``QTDIR`` is not treated as explicit: plenty of
    things set it incidentally.

    Args:
        configured: The ``--liberaqt-qt-bin`` value, if given.

    Returns:
        Directories to try. Exactly one entry when a choice was made explicitly.
    """
    explicit = configured or os.environ.get("LIBERAQT_QT_BIN")
    if explicit:
        return [Path(explicit)]

    out: list[Path] = []
    qtdir = os.environ.get("QTDIR")
    if qtdir:
        out += [Path(qtdir) / "bin", Path(qtdir)]
    qmake = shutil.which("qmake") or shutil.which("qmake6")
    if qmake:
        out.append(Path(qmake).parent)
    # Last resort: a default-location Qt on this machine, newest first.
    out += [Path(p) for p in sorted(glob.glob("C:/Qt/6.*/mingw_64/bin"), reverse=True)]
    out += [Path("/usr/lib/qt6/bin"), Path("/usr/lib/qt5/bin")]
    return out


@pytest.fixture(scope="session")
def qt_tool(pytestconfig: pytest.Config):
    """Locate one of Qt's shipped applications, or skip.

    Every Qt-based suite goes through this so they all drive the *same* installation. Without it
    each one globbed for itself and silently took the newest Qt on the machine, which means
    installing a second version changed nothing and the older one was never exercised.

    ``--liberaqt-qt-bin`` (or ``LIBERAQT_QT_BIN``, or ``QTDIR``) picks the installation, which is
    how you point the suite at a particular Qt version::

        pytest e2e/ --liberaqt-qt-bin "C:/Qt/6.5.9/mingw_64/bin"

    Returns:
        A callable taking an application name -- ``"assistant"``, ``"qmleasing"`` -- and
        returning its path.
    """
    configured = pytestconfig.getoption("--liberaqt-qt-bin")

    def find(name: str) -> Path:
        for directory in _candidates(configured):
            candidate = directory / f"{name}{EXE}"
            if candidate.is_file():
                return candidate
        pytest.skip(f"{name} is not in any Qt installation found; "
                    f"pass --liberaqt-qt-bin or set QTDIR")
        raise AssertionError("unreachable")

    return find


@pytest.fixture(scope="session")
def qt_bin(pytestconfig: pytest.Config) -> Path:
    """Directory holding Qt's shipped applications."""
    for directory in _candidates(pytestconfig.getoption("--liberaqt-qt-bin")):
        if (directory / f"assistant{EXE}").is_file():
            return directory
    pytest.skip("no Qt bin directory found; pass --liberaqt-qt-bin or set QTDIR")


@pytest.fixture(scope="session")
def driver() -> LiberaQt:
    """One driver for the whole session, closing every application it launched."""
    with LiberaQt(default_timeout=10.0) as instance:
        yield instance


@pytest.fixture(scope="session")
def assistant(driver: LiberaQt, qt_bin: Path):
    """Qt Assistant, settled and ready."""
    app = driver.launch(str(qt_bin / f"assistant{EXE}"), timeout=90.0)
    app.wait_for_window(title="Qt Assistant", timeout=60.0)
    app.wait_for_idle(timeout=30.0)
    return app


@pytest.fixture(scope="session")
def win(assistant):
    """Assistant's main window."""
    return assistant.window(title="Qt Assistant")
