# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Sargis Khachatryan
"""pytest integration.

Enabled automatically via the ``pytest11`` entry point. Reads defaults from ``liberaqt.toml``
so tests do not hard-code the executable path.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import warnings
from pathlib import Path
from typing import Any

import pytest

from . import LiberaQt

#: Every key ``liberaqt.toml`` understands under ``[liberaqt]``. Anything else is a typo, and a
#: typo used to be silently ignored -- ``executible = "..."`` turned a whole live suite into
#: skips that read as a pass in CI.
CONFIG_KEYS = frozenset(
    {"executable", "args", "qt", "object_map", "headless", "input_mode", "timeout"}
)

#: Default action timeout, when neither the command line nor ``liberaqt.toml`` gives one.
DEFAULT_TIMEOUT = 5.0

#: Where failure diagnostics go, under the rootdir. CI uploads it as an artifact.
TRACE_DIR = "liberaqt-trace"

#: The session's driver, published so the report hook can reach every application it launched.
_DRIVER = pytest.StashKey[LiberaQt]()


def pytest_addoption(parser: Any) -> None:
    """Register the ``--liberaqt-*`` command line options.

    Args:
        parser: The pytest argument parser.
    """
    group = parser.getgroup("liberaqt")
    group.addoption("--liberaqt-exe", default=None, help="path to the application under test")
    group.addoption("--liberaqt-qt", default=None, help="force a Qt version, e.g. 6.7")
    # Both spellings, so the command line can override liberaqt.toml in either direction. A plain
    # store_true could only ever turn headless on: `headless = true` in a checked-in config
    # hard-failed every test on Windows, where offscreen is Linux-only, with no way out.
    group.addoption("--liberaqt-headless", action=argparse.BooleanOptionalAction, default=None,
                    help="run with the offscreen QPA platform (Linux only)")
    group.addoption("--liberaqt-slowmo", type=float, default=0.0,
                    help="seconds to sleep before each command, for debugging")
    # No default here: a default makes "the user passed it" indistinguishable from "they did not",
    # and that is exactly how the command line came to lose to liberaqt.toml.
    group.addoption("--liberaqt-timeout", type=float, default=None,
                    help=f"default action timeout in seconds (default {DEFAULT_TIMEOUT})")
    group.addoption("--liberaqt-trace", action="store_true", help="log every protocol message")
    group.addoption("--liberaqt-input-mode", choices=("native", "synthetic"), default=None,
                    help="how input reaches the application; native behaves like a real user")


def _load_config(rootdir: Path) -> dict[str, Any]:
    """Read ``[liberaqt]`` from ``<rootdir>/liberaqt.toml``, warning about anything ignored.

    A config file that exists and is ignored must never be silent. Three ways it used to be:
    a Python without a TOML parser, a misspelt key, and the section written as ``[tool.liberaqt]``
    by someone used to pyproject.toml.

    Args:
        rootdir: pytest's rootdir.

    Returns:
        The ``[liberaqt]`` table, or ``{}``.
    """
    path = rootdir / "liberaqt.toml"
    if not path.exists():
        return {}
    try:
        import tomllib  # Python 3.11+
    except ModuleNotFoundError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ModuleNotFoundError:
            warnings.warn(
                f"liberaqt: {path} was ignored -- reading it needs Python 3.11+ or `tomli`",
                pytest.PytestConfigWarning, stacklevel=2,
            )
            return {}
    with open(path, "rb") as fh:
        document = tomllib.load(fh)

    section = document.get("liberaqt")
    if section is None:
        nested = document.get("tool", {}).get("liberaqt")
        if nested is not None:
            warnings.warn(
                f"liberaqt: {path} has [tool.liberaqt]; this file wants [liberaqt]. Ignored.",
                pytest.PytestConfigWarning, stacklevel=2,
            )
        return {}

    unknown = sorted(set(section) - CONFIG_KEYS)
    if unknown:
        warnings.warn(
            f"liberaqt: {path} has unknown key(s) {', '.join(unknown)}; "
            f"known keys are {', '.join(sorted(CONFIG_KEYS))}",
            pytest.PytestConfigWarning, stacklevel=2,
        )

    # A relative path means relative to the file that names it -- the only reading under which a
    # checked-in config works from any cwd. A bare name with nothing beside the config is left
    # alone, so an executable can still be found on PATH.
    for key in ("executable", "object_map"):
        value = section.get(key)
        if isinstance(value, str) and not Path(value).is_absolute() and (rootdir / value).exists():
            section[key] = str(rootdir / value)
    return dict(section)


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
    if pytestconfig.getoption("--liberaqt-headless") is not None:
        cfg["headless"] = pytestconfig.getoption("--liberaqt-headless")
    if pytestconfig.getoption("--liberaqt-input-mode"):
        cfg["input_mode"] = pytestconfig.getoption("--liberaqt-input-mode")
    if pytestconfig.getoption("--liberaqt-timeout") is not None:
        cfg["timeout"] = pytestconfig.getoption("--liberaqt-timeout")
    cfg.setdefault("executable", os.environ.get("LIBERAQT_EXE"))
    cfg.setdefault("timeout", DEFAULT_TIMEOUT)
    cfg.setdefault("headless", False)
    # Every known key present, so cfg["input_mode"] means "unset" rather than KeyError depending
    # on whether a liberaqt.toml happens to exist.
    for key in CONFIG_KEYS:
        cfg.setdefault(key, None)
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
        default_timeout=float(liberaqt_config.get("timeout", DEFAULT_TIMEOUT)),
        slowmo=pytestconfig.getoption("--liberaqt-slowmo"),
        trace=pytestconfig.getoption("--liberaqt-trace"),
        input_mode=liberaqt_config.get("input_mode"),
    )
    pytestconfig.stash[_DRIVER] = driver
    yield driver
    driver.close()


def _no_application(pytestconfig: Any) -> str:
    """Say what was looked at when no application is configured, so a skip is diagnosable."""
    toml = Path(str(pytestconfig.rootdir)) / "liberaqt.toml"
    where = f"no `executable` in {toml}" if toml.exists() else f"no {toml}"
    return f"no application configured: {where}, no --liberaqt-exe, no LIBERAQT_EXE"


def _launch(liberaqt: LiberaQt, liberaqt_config: dict[str, Any], pytestconfig: Any):
    exe = liberaqt_config.get("executable")
    if not exe:
        pytest.skip(_no_application(pytestconfig))
    return liberaqt.launch(
        exe,
        args=liberaqt_config.get("args"),
        qt=liberaqt_config.get("qt"),
        object_map=liberaqt_config.get("object_map"),
        headless=bool(liberaqt_config.get("headless")),
    )


@pytest.fixture
def app(liberaqt, liberaqt_config: dict[str, Any], pytestconfig: Any):
    """A fresh AUT process per test. Slower, but tests cannot leak state into each other."""
    application = _launch(liberaqt, liberaqt_config, pytestconfig)
    yield application
    application.close()


@pytest.fixture(scope="session")
def app_session(liberaqt, liberaqt_config: dict[str, Any], pytestconfig: Any):
    """One AUT process for the whole session. Faster; use when tests are read-only."""
    application = _launch(liberaqt, liberaqt_config, pytestconfig)
    yield application
    application.close()


@pytest.fixture
def win(app):
    """The application's first window, for tests that only use one.

    Built on :func:`app`, so it launches a fresh application per test. Asking for ``win``
    alongside ``app_session`` therefore starts a *second* one; use ``app_session.window()``.

    Args:
        app: The ``app`` fixture.

    Returns:
        The first :class:`~liberaqt.window.Window`.
    """
    return app.window()


def pytest_sessionstart(session: Any) -> None:
    """Clear the previous run's diagnostics, so nothing stale sits under a current name.

    A green run used to leave the last red run's screenshots in place, indistinguishable from
    fresh ones. Only the files this plugin writes are removed, and only by the controlling
    process -- an xdist worker must not wipe another worker's evidence.

    Args:
        session: The pytest session.
    """
    if hasattr(session.config, "workerinput"):
        return
    outdir = Path(str(session.config.rootdir)) / TRACE_DIR
    if not outdir.is_dir():
        return
    for stale in (*outdir.glob("*.png"), *outdir.glob("*.jsonl")):
        try:
            stale.unlink()
        except OSError:
            pass


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item: Any, call: Any):
    """Save diagnostics the moment a test body fails, while its applications are still running.

    Deliberately here rather than in a fixture's teardown. Teardown is too late for ``app``,
    which closes its application there, and it never ran at all for ``app_session`` -- which is
    how a failure under the session fixture came to leave no trace whatsoever.

    Args:
        item: The test item.
        call: The phase being reported.

    Yields:
        To the next hook implementation.
    """
    outcome = yield
    report = outcome.get_result()
    setattr(item, f"_liberaqt_report_{report.when}", report)
    if report.when == "call" and report.failed:
        driver = item.config.stash.get(_DRIVER, None)
        if driver is not None:
            _write_diagnostics(item, driver)


def _stem(nodeid: str) -> str:
    """A filename for a test, safe on every filesystem and unique across the whole run.

    The bare test name collided between files, and on Windows a ``:`` in a parametrize id -- a
    drive letter, say -- sent both artifacts into NTFS alternate data streams of a zero-byte
    file, invisible to ``ls``, Explorer and every CI artifact uploader.

    Args:
        nodeid: The pytest node id.

    Returns:
        A name of at most 150 characters from ``[A-Za-z0-9._-]``.
    """
    return re.sub(r"[^A-Za-z0-9._-]+", "_", nodeid).strip("_")[:150] or "test"


def _write_diagnostics(item: Any, driver: LiberaQt) -> None:
    """Save a screenshot and the last protocol messages for every application still running.

    Every one, not just the one a fixture owns: a test that opened a dialog in a second process,
    or used ``app_session`` and ``win`` together, failed with evidence from the wrong process or
    none at all.

    Args:
        item: The failed test item.
        driver: The session's driver.
    """
    running = [a for a in getattr(driver, "_apps", []) if not getattr(a, "_closed", False)]
    if not running:
        return
    outdir = Path(str(item.config.rootdir)) / TRACE_DIR
    outdir.mkdir(exist_ok=True)
    stem = _stem(item.nodeid)
    for index, application in enumerate(running):
        name = stem if len(running) == 1 else f"{stem}.pid{application.pid or index}"
        try:
            application.screenshot(str(outdir / f"{name}.png"))
        except Exception as exc:  # noqa: BLE001 - a diagnostic must never mask the real failure
            warnings.warn(f"liberaqt: no screenshot for {item.nodeid}: {exc}", stacklevel=1)
        try:
            (outdir / f"{name}.jsonl").write_text(
                "\n".join(json.dumps(e) for e in application._session.transport.history),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001
            warnings.warn(f"liberaqt: no protocol log for {item.nodeid}: {exc}", stacklevel=1)
