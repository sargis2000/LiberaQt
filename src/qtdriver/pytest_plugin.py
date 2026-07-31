"""pytest integration.

Enabled automatically via the ``pytest11`` entry point. Reads defaults from ``qtdriver.toml``
so tests do not hard-code the executable path.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

import pytest

from . import QtDriver


def pytest_addoption(parser: Any) -> None:
    group = parser.getgroup("qtdriver")
    group.addoption("--qtdriver-exe", default=None, help="path to the application under test")
    group.addoption("--qtdriver-qt", default=None, help="force a Qt version, e.g. 6.7")
    group.addoption("--qtdriver-headless", action="store_true",
                    help="run with the offscreen QPA platform (Linux)")
    group.addoption("--qtdriver-slowmo", type=float, default=0.0,
                    help="seconds to sleep before each command, for debugging")
    group.addoption("--qtdriver-timeout", type=float, default=5.0, help="default action timeout")
    group.addoption("--qtdriver-trace", action="store_true", help="log every protocol message")


def _load_config(rootdir: Path) -> Dict[str, Any]:
    path = rootdir / "qtdriver.toml"
    if not path.exists():
        return {}
    try:
        import tomllib  # Python 3.11+
    except ModuleNotFoundError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ModuleNotFoundError:
            return {}
    with open(path, "rb") as fh:
        return tomllib.load(fh).get("qtdriver", {})


@pytest.fixture(scope="session")
def qtdriver_config(pytestconfig: Any) -> Dict[str, Any]:
    cfg = _load_config(Path(str(pytestconfig.rootdir)))
    if pytestconfig.getoption("--qtdriver-exe"):
        cfg["executable"] = pytestconfig.getoption("--qtdriver-exe")
    if pytestconfig.getoption("--qtdriver-qt"):
        cfg["qt"] = pytestconfig.getoption("--qtdriver-qt")
    if pytestconfig.getoption("--qtdriver-headless"):
        cfg["headless"] = True
    cfg.setdefault("executable", os.environ.get("QTDRIVER_EXE"))
    cfg.setdefault("timeout", pytestconfig.getoption("--qtdriver-timeout"))
    return cfg


@pytest.fixture(scope="session")
def qtdriver(pytestconfig: Any, qtdriver_config: Dict[str, Any]):
    driver = QtDriver(
        default_timeout=float(qtdriver_config.get("timeout", 5.0)),
        slowmo=pytestconfig.getoption("--qtdriver-slowmo"),
        trace=pytestconfig.getoption("--qtdriver-trace"),
    )
    yield driver
    driver.close()


@pytest.fixture
def app(qtdriver, qtdriver_config: Dict[str, Any], request: Any):
    """A fresh AUT process per test. Slower, but tests cannot leak state into each other."""
    exe = qtdriver_config.get("executable")
    if not exe:
        pytest.skip("no application configured (set --qtdriver-exe or qtdriver.toml)")
    application = qtdriver.launch(
        exe,
        args=qtdriver_config.get("args"),
        qt=qtdriver_config.get("qt"),
        object_map=qtdriver_config.get("object_map"),
        headless=bool(qtdriver_config.get("headless")),
    )
    yield application
    _attach_diagnostics(request, application)
    application.close()


@pytest.fixture(scope="session")
def app_session(qtdriver, qtdriver_config: Dict[str, Any]):
    """One AUT process for the whole session. Faster; use when tests are read-only."""
    exe = qtdriver_config.get("executable")
    if not exe:
        pytest.skip("no application configured")
    application = qtdriver.launch(
        exe, args=qtdriver_config.get("args"), qt=qtdriver_config.get("qt"),
        object_map=qtdriver_config.get("object_map"),
        headless=bool(qtdriver_config.get("headless")),
    )
    yield application
    application.close()


@pytest.fixture
def win(app):
    return app.window()


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item: Any, call: Any):
    outcome = yield
    report = outcome.get_result()
    setattr(item, f"_qtdriver_report_{report.when}", report)


def _attach_diagnostics(request: Any, application) -> None:
    """On failure, save a screenshot and the last protocol messages next to the test run.

    This is the highest-value debugging feature a UI automation tool has: a failed CI run should
    not require reproducing the failure locally.
    """
    report = getattr(request.node, "_qtdriver_report_call", None)
    if report is None or not report.failed:
        return
    outdir = Path(str(request.config.rootdir)) / "qtdriver-trace"
    outdir.mkdir(exist_ok=True)
    stem = request.node.name.replace("/", "_")
    try:
        application.screenshot(str(outdir / f"{stem}.png"))
    except Exception:  # noqa: BLE001
        pass
    try:
        import json
        (outdir / f"{stem}.jsonl").write_text(
            "\n".join(json.dumps(e) for e in application._session.transport.history),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001
        pass
