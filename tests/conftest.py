"""Test-tree wide configuration: the category markers and the live-suite gate.

The tree is split by *what has to be installed* for a test to do anything:

``tests/unit``
    Pure Python. No Qt, no agent, no application. This is what CI runs.
``tests/e2e/qt``
    Needs a Qt installation, because it drives Qt's own shipped tools.
``tests/e2e/libero``
    Needs Microchip Libero SoC *and* a 32-bit MSVC agent. Most of it writes projects under
    ``C:/Users/Public``, and one test takes minutes.

That axis is the filesystem's job, because it is the axis you point pytest at. The orthogonal
properties -- does this test write outside a tmp dir, is it slow -- are markers, because they cut
across the directories and would otherwise fragment the tree into one-file corners.

**The gate.** A live test runs only when a *positional argument* names a path inside
``tests/e2e`` that contains it. Two rules follow, and each closes a hole the first version had:

* Only pytest's parsed positional arguments count (``config.args``, when they came from the
  invocation). The first version scanned the raw command line and took every word not starting
  with ``-`` for a path -- option *values* included -- so ``--ignore tests/e2e/qt``, an
  exclusion, opted the run into all of Libero.
* Consent is per test, not per run. ``pytest tests/ tests/e2e/qt`` asks for the Qt suite; it
  used to open the gate for everything live, Libero included.

A marker is never consent: ``-m "not slow"`` is the natural way to ask for a quicker run, and it
must not become "launch Libero SoC". Nor is the working directory: from inside the live tree,
pass ``.``. Nor is ``testpaths``, which is where a bare ``pytest`` gets its paths.

The Libero modules carry a backstop of their own, because every conftest -- this one included --
can be switched off with ``--noconftest``: see :data:`CONSENT_ATTR`.

Why ``pytest_addoption`` lives *here* rather than in ``tests/e2e/conftest.py``, which is where it
belongs by subject: pytest only calls that hook for *initial* conftests, meaning the ones on the
path from the rootdir down to each command-line argument. From ``tests/e2e/conftest.py`` the
option registers for ``pytest tests/e2e`` and not for ``pytest tests``. It does not fail loudly
when it is missing, either -- the hook is replayed during collection, so the option ends up
existing with its default and ``--liberaqt-qt-bin`` is silently ignored. That is the exact
failure the option was added to prevent: believing you tested 6.5 while actually testing 6.7.

One consequence of living in a conftest at all: pytest reads a first pass of the command line
before any conftest loads, so ``--liberaqt-qt-bin DIR`` with no test path in front of it takes
``DIR`` for a test path and stops with "unrecognized arguments". Write ``--liberaqt-qt-bin=DIR``,
or put the path first as every example here does.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

#: Directory under ``tests/`` -> the markers every test beneath it gets, automatically. Each
#: level adds only what is new there, so a Qt test is ``live`` once, not twice.
#:
#: Auto-applying beats a ``pytestmark`` line per file: a new live test cannot be added without a
#: marker, because the directory it lives in supplies one.
_DIRECTORY_MARKERS = {
    ("e2e",): ("live",),
    ("e2e", "qt"): ("qt_app",),
    ("e2e", "libero"): ("libero",),
}

_TESTS_ROOT = Path(__file__).resolve().parent
_LIVE_ROOT = _TESTS_ROOT / "e2e"

#: Set on the config once the gate has run, holding the resolved test files it let through.
#:
#: The Libero modules read it by this literal name -- a conftest is not importable -- and skip
#: when it is absent, which means no gate ran at all. That is the ``--noconftest`` case, where
#: the alternative is launching Libero and writing a project under ``C:/Users/Public``.
CONSENT_ATTR = "_liberaqt_live_consent"


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add the option that picks which Qt installation the live suites drive."""
    parser.addoption(
        "--liberaqt-qt-bin",
        default=None,
        help="directory holding Qt's assistant/qmleasing, for the tests/e2e/qt suite "
             "(write --liberaqt-qt-bin=DIR unless a test path comes first)",
    )


def _relative_parts(path: Path) -> tuple[str, ...]:
    """A test file's directory relative to ``tests/``, or ``()`` outside the tree."""
    try:
        return Path(path).resolve().parent.relative_to(_TESTS_ROOT).parts
    except ValueError:
        return ()


def pytest_itemcollected(item: pytest.Item) -> None:
    """Apply the directory markers as each test is collected.

    Here rather than in ``pytest_collection_modifyitems``, which the gate needs to itself: this
    runs during collection, so the markers exist by the time ``-m libero`` is evaluated.

    Args:
        item: The test just collected.
    """
    parts = _relative_parts(item.path)
    for depth in range(1, len(parts) + 1):
        for marker in _DIRECTORY_MARKERS.get(parts[:depth], ()):
            item.add_marker(marker)


def _live_arguments(config: pytest.Config) -> list[tuple[Path, str]]:
    """The positional arguments that point into the live tree, as ``(path, node suffix)``.

    Only arguments pytest parsed as positional, and only when they came from the invocation --
    the command line, ``PYTEST_ADDOPTS``, ``addopts`` or an ``@file``. Not ``testpaths``, and not
    the working directory pytest falls back to when given nothing.

    Args:
        config: The active pytest config.

    Returns:
        One ``(resolved path, text after "::" or "")`` per argument inside ``tests/e2e``.
    """
    if config.args_source is not pytest.Config.ArgsSource.ARGS:
        return []
    base = Path(config.invocation_params.dir)
    out = []
    for argument in config.args:
        file_part, _, suffix = argument.partition("::")
        candidate = Path(file_part)
        if not candidate.is_absolute():
            candidate = base / candidate
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved == _LIVE_ROOT or _LIVE_ROOT in resolved.parents:
            out.append((resolved, suffix))
    return out


def _covers(argument: tuple[Path, str], item: pytest.Item) -> bool:
    """Whether one live argument asks for this particular test.

    Args:
        argument: A ``(path, node suffix)`` from :func:`_live_arguments`.
        item: A collected live test.

    Returns:
        True when the argument is a directory above the test, its file, or a node id selecting
        it.
    """
    path, suffix = argument
    item_path = Path(item.path).resolve()
    if path != item_path:
        return path in item_path.parents
    if not suffix:
        return True
    # A node id covers what it names and everything beneath it -- a parametrized case or a
    # method of a class -- but "test_x" must not cover "test_x_other".
    item_suffix = item.nodeid.partition("::")[2]
    return (item_suffix == suffix or item_suffix.startswith(suffix + "::")
            or item_suffix.startswith(suffix + "["))


def _describe_withheld(config: pytest.Config, withheld: list[pytest.Item]) -> str:
    """Say what was held back, and the command -- from here -- that asks for each part of it."""
    base = Path(config.invocation_params.dir)

    def ask(*parts: str) -> str:
        return "pytest " + os.path.relpath(_LIVE_ROOT.joinpath(*parts), base).replace("\\", "/")

    qt = [i for i in withheld if i.get_closest_marker("qt_app")]
    libero = [i for i in withheld if i.get_closest_marker("libero")]
    described = []
    if qt:
        described.append(f"{len(qt)} driving Qt's own tools: {ask('qt')}")
    if libero:
        writes = sum(1 for i in libero if i.get_closest_marker("writes_disk"))
        also = f" ({writes} write projects to disk)" if writes else ""
        described.append(f"{len(libero)} driving Libero SoC{also}: {ask('libero')}")
    other = len(withheld) - len(qt) - len(libero)
    if other:
        described.append(f"{other} more: {ask()}")
    return (f"liberaqt: {len(withheld)} live tests not run. They drive real applications, so "
            f"they are asked for by path -- " + "; ".join(described))


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Deselect every live test no positional argument asked for.

    ``trylast``, so it sees the selection pytest's own ``-m``, ``-k`` and ``--deselect`` left,
    and the message describes what was actually withheld.

    Args:
        config: The active pytest config.
        items: The collected tests, filtered in place.
    """
    arguments = _live_arguments(config)
    consented: set[Path] = set()
    kept: list[pytest.Item] = []
    withheld: list[pytest.Item] = []
    for item in items:
        if not item.get_closest_marker("live"):
            kept.append(item)
        elif any(_covers(argument, item) for argument in arguments):
            kept.append(item)
            consented.add(Path(item.path).resolve())
        else:
            withheld.append(item)

    setattr(config, CONSENT_ATTR, frozenset(consented))
    if not withheld:
        return
    items[:] = kept
    config.hook.pytest_deselected(items=withheld)
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(_describe_withheld(config, withheld), yellow=True)


def pytest_collection_finish(session: pytest.Session) -> None:
    """Refuse a Qt pin that cannot mean what it says, judged against the final selection.

    Two ways a pin went green having tested nothing. ``--liberaqt-qt-bin`` given to a run that
    selects no Qt test is a no-op, so it is refused. And a pinned directory that does not exist
    -- a typo, or a Qt version not installed -- used to turn every Qt test into a skip and exit
    0, so it is refused whenever there is a Qt test to run.

    ``LIBERAQT_QT_BIN`` is a standing default, like ``QTDIR``, so selecting no Qt test with it
    exported is fine: it only has to be *valid* when something uses it.

    Args:
        session: The pytest session, whose ``items`` are final here.

    Raises:
        pytest.UsageError: A pin that means nothing, or names no directory.
    """
    config = session.config
    flag = config.getoption("--liberaqt-qt-bin")
    env = os.environ.get("LIBERAQT_QT_BIN")
    uses_qt = any(item.get_closest_marker("qt_app") for item in session.items)

    if flag and not uses_qt:
        raise pytest.UsageError(
            "--liberaqt-qt-bin pins a Qt installation, but no Qt test is selected, so it would "
            "mean nothing. Point the run at the Qt suite: "
            "`pytest tests/e2e/qt --liberaqt-qt-bin <dir>`"
        )
    pinned = flag or env
    if uses_qt and pinned and not Path(pinned).is_dir():
        source = "--liberaqt-qt-bin" if flag else "LIBERAQT_QT_BIN"
        raise pytest.UsageError(
            f"{source} names {pinned}, which is not a directory. Every Qt test would skip and "
            f"the run would look green; pass the bin directory of an installed Qt instead."
        )
