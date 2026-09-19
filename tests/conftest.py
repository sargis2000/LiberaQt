"""Test-tree wide configuration: the category markers and the live-suite gate.

The tree is split by *what has to be installed* for a test to do anything:

``tests/unit``
    Pure Python. No Qt, no agent, no application. This is what CI runs.
``tests/e2e/qt``
    Needs a Qt installation, because it drives Qt's own shipped tools.
``tests/e2e/libero``
    Needs Microchip Libero SoC *and* a 32-bit MSVC agent.

That axis is the filesystem's job, because it is the axis you point pytest at. The orthogonal
properties -- does this test write outside a tmp dir, is it slow -- are markers, because they cut
across the directories and would otherwise fragment the tree into one-file corners.

Why ``pytest_addoption`` lives *here* rather than in ``tests/e2e/conftest.py``, which is where it
belongs by subject: pytest only calls that hook for *initial* conftests, meaning the ones on the
path from the rootdir down to each command-line argument. From ``tests/e2e/conftest.py`` the
option registers for ``pytest tests/e2e`` and not for ``pytest tests``. It does not fail loudly
when it is missing, either -- the hook is replayed during collection, so the option ends up
existing with its default and ``--liberaqt-qt-bin`` is silently ignored. That is the exact
failure the option was added to prevent: believing you tested 6.5 while actually testing 6.7.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

#: Directory under ``tests/`` -> the markers every test beneath it gets, automatically.
#:
#: Auto-applying beats a ``pytestmark`` line per file: a new live test cannot be added without a
#: marker, because the directory it lives in supplies one.
_DIRECTORY_MARKERS = {
    ("e2e",): ("live",),
    ("e2e", "qt"): ("live", "qt_app"),
    ("e2e", "libero"): ("live", "libero"),
}

_TESTS_ROOT = Path(__file__).parent


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add the option that picks which Qt installation the live suites drive."""
    parser.addoption(
        "--liberaqt-qt-bin",
        default=None,
        help="directory holding Qt's assistant/qmleasing, for the tests/e2e/qt suite",
    )


def pytest_configure(config: pytest.Config) -> None:
    """Declare the markers, so an unknown-mark warning means a real typo."""
    for name, description in (
        ("live", "drives a real application; needs it installed and a matching agent"),
        ("qt_app", "drives one of Qt's own shipped tools"),
        ("libero", "drives Microchip Libero SoC (Qt 5.15 / MSVC / x86)"),
        ("writes_disk", "creates files outside pytest's tmp directories"),
        ("slow", "takes minutes rather than seconds"),
    ):
        config.addinivalue_line("markers", f"{name}: {description}")


def _relative_parts(item: pytest.Item) -> tuple[str, ...]:
    """The item's directory, relative to ``tests/``.

    Args:
        item: The collected item.

    Returns:
        Path segments below ``tests/``, empty when the item is outside the tree.
    """
    try:
        return Path(item.path).parent.relative_to(_TESTS_ROOT).parts
    except ValueError:
        return ()


def _live_was_asked_for(config: pytest.Config) -> bool:
    """Whether the command line actually pointed at the live tree.

    A **path** is the only opt-in. It deliberately is not enough for the run to merely mention a
    marker: ``-m "not slow"`` is the natural way to ask for a quicker run, and treating any ``-m``
    expression as consent would turn that into "launch Libero SoC". Opting in by path cannot be
    triggered by accident, and it is what the suite already required before the two trees were
    merged.

    Args:
        config: The active pytest config.

    Returns:
        True when some argument names a path inside ``tests/e2e``.
    """
    live_root = (_TESTS_ROOT / "e2e").resolve()
    for argument in config.invocation_params.args:
        if argument.startswith("-"):
            continue
        # Strip a ``::test_name`` suffix before asking the filesystem about the path.
        candidate = Path(argument.split("::", 1)[0])
        if not candidate.is_absolute():
            candidate = Path(config.invocation_params.dir) / candidate
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved == live_root or live_root in resolved.parents:
            return True
    return False


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Apply the directory markers, then deselect the live tests unless they were asked for."""
    for item in items:
        parts = _relative_parts(item)
        for depth in range(1, len(parts) + 1):
            for marker in _DIRECTORY_MARKERS.get(parts[:depth], ()):
                item.add_marker(marker)

    if _live_was_asked_for(config):
        return

    live = [item for item in items if item.get_closest_marker("live")]
    if not live:
        return

    # A Qt pin that selects nothing is the failure this suite exists to avoid. Refuse the run
    # rather than exiting green having driven no application at all.
    if config.getoption("--liberaqt-qt-bin") or os.environ.get("LIBERAQT_QT_BIN"):
        raise pytest.UsageError(
            "a Qt installation was pinned, but no live test was selected: "
            "--liberaqt-qt-bin/LIBERAQT_QT_BIN only means something when the run points at the "
            "live tree, e.g. `pytest tests/e2e/qt --liberaqt-qt-bin <dir>`"
        )

    for item in live:
        items.remove(item)
    config.hook.pytest_deselected(items=live)
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(
            f"liberaqt: {len(live)} live tests not run (they drive real applications). "
            f"Ask for them by path: pytest tests/e2e",
            yellow=True,
        )
