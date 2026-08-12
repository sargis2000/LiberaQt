"""pytest integration.

Enabled automatically via the ``pytest11`` entry point. Reads defaults from ``liberaqt.toml``
so tests do not hard-code the executable path.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from . import LiberaQt


def pytest_addoption(parser: Any) -> None:
    """Register the ``--liberaqt-*`` command line options.

    Args:
        parser: The pytest argument parser.
    """
    group = parser.getgroup("liberaqt")
    group.addoption("--liberaqt-exe", default=None, help="path to the application under test")
    group.addoption("--liberaqt-qt", default=None, help="force a Qt version, e.g. 6.7")
    group.addoption("--liberaqt-headless", action="store_true",
                    help="run with the offscreen QPA platform (Linux)")
    group.addoption("--liberaqt-slowmo", type=float, default=0.0,
                    help="seconds to sleep before each command, for debugging")
    group.addoption("--liberaqt-timeout", type=float, default=5.0, help="default action timeout")
    group.addoption("--liberaqt-trace", action="store_true", help="log every protocol message")
    group.addoption("--liberaqt-input-mode", choices=("native", "synthetic"), default=None,
                    help="how input reaches the application; native behaves like a real user")


def _load_config(rootdir: Path) -> dict[str, Any]:
    path = rootdir / "liberaqt.toml"
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
        return tomllib.load(fh).get("liberaqt", {})


@pytest.fixture(scope="session")
def liberaqt_config(pytestconfig: Any) -> dict[str, Any]:
    """Session-scoped configuration, merged from every source.

    Precedence is command line, then ``liberaqt.toml`` in the rootdir, then the ``LIBERAQT_EXE``
    environment variable, so CI can override a checked-in config without editing it.

    Args:
        pytestconfig: The pytest config object.

    Returns:
        The merged settings.
    """
    cfg = _load_config(Path(str(pytestconfig.rootdir)))
    if pytestconfig.getoption("--liberaqt-exe"):
        cfg["executable"] = pytestconfig.getoption("--liberaqt-exe")
    if pytestconfig.getoption("--liberaqt-qt"):
        cfg["qt"] = pytestconfig.getoption("--liberaqt-qt")
    if pytestconfig.getoption("--liberaqt-headless"):
        cfg["headless"] = True
    if pytestconfig.getoption("--liberaqt-input-mode"):
        cfg["input_mode"] = pytestconfig.getoption("--liberaqt-input-mode")
    cfg.setdefault("executable", os.environ.get("LIBERAQT_EXE"))
    cfg.setdefault("timeout", pytestconfig.getoption("--liberaqt-timeout"))
    return cfg


@pytest.fixture(scope="session")
def liberaqt(pytestconfig: Any, liberaqt_config: dict[str, Any]):
    """Session-scoped driver, closed when the run ends.

    Args:
        pytestconfig: The pytest config object.
        liberaqt_config: Merged settings from :func:`liberaqt_config`.

    Yields:
        A :class:`~liberaqt.LiberaQt` shared by every test in the session.
    """
    driver = LiberaQt(
        default_timeout=float(liberaqt_config.get("timeout", 5.0)),
        slowmo=pytestconfig.getoption("--liberaqt-slowmo"),
        trace=pytestconfig.getoption("--liberaqt-trace"),
        input_mode=liberaqt_config.get("input_mode"),
    )
    yield driver
    driver.close()


@pytest.fixture
def app(liberaqt, liberaqt_config: dict[str, Any], request: Any):
    """A fresh AUT process per test. Slower, but tests cannot leak state into each other."""
    exe = liberaqt_config.get("executable")
    if not exe:
        pytest.skip("no application configured (set --liberaqt-exe or liberaqt.toml)")
    application = liberaqt.launch(
        exe,
        args=liberaqt_config.get("args"),
        qt=liberaqt_config.get("qt"),
        object_map=liberaqt_config.get("object_map"),
        headless=bool(liberaqt_config.get("headless")),
    )
    yield application
    _attach_diagnostics(request, application)
    application.close()


@pytest.fixture(scope="session")
def app_session(liberaqt, liberaqt_config: dict[str, Any]):
    """One AUT process for the whole session. Faster; use when tests are read-only."""
    exe = liberaqt_config.get("executable")
    if not exe:
        pytest.skip("no application configured")
    application = liberaqt.launch(
        exe, args=liberaqt_config.get("args"), qt=liberaqt_config.get("qt"),
        object_map=liberaqt_config.get("object_map"),
        headless=bool(liberaqt_config.get("headless")),
    )
    yield application
    application.close()


@pytest.fixture
def win(app):
    """The application's first window, for tests that only use one.

    Args:
        app: The ``app`` fixture.

    Returns:
        The first :class:`~liberaqt.window.Window`.
    """
    return app.window()


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item: Any, call: Any):
    """Record each phase's result on the test item.

    The ``app`` fixture reads this during teardown to decide whether to save diagnostics, since
    a fixture cannot otherwise tell whether its test passed.

    Args:
        item: The test item.
        call: The phase being reported.

    Yields:
        To the next hook implementation.
    """
    outcome = yield
    report = outcome.get_result()
    setattr(item, f"_liberaqt_report_{report.when}", report)


def _attach_diagnostics(request: Any, application) -> None:
    """On failure, save a screenshot and the last protocol messages next to the test run.

    This is the highest-value debugging feature a UI automation tool has: a failed CI run should
    not require reproducing the failure locally.
    """
    report = getattr(request.node, "_liberaqt_report_call", None)
    if report is None or not report.failed:
        return
    outdir = Path(str(request.config.rootdir)) / "liberaqt-trace"
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
