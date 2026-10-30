---
name: pytest-internals
description: How this repository's pytest machinery really behaves, and the bug each rule closed - the live-suite gate in tests/conftest.py (consent only from positional paths in config.args, per test, never from a marker; the Libero backstop fixture; the --liberaqt-qt-bin pin) and the liberaqt pytest plugin in src/liberaqt/pytest_plugin.py (command-line option vs liberaqt.toml precedence, failure diagnostics from pytest_runtest_makereport, trace file names, token redaction, warnings for ignored config). Use before editing tests/conftest.py, tests/e2e/conftest.py or pytest_plugin.py, before adding a pytest option, marker, fixture or live test module, and whenever live tests run when they should not (or are deselected or skipped when they should run), --liberaqt-qt-bin seems ignored, a liberaqt.toml setting seems to have no effect, or liberaqt-trace/ output is missing or misnamed.
---

# pytest internals

Two pieces of pytest code in this repository look simple and are not: the gate that keeps the live
suites from launching real applications by accident, and the plugin users load. Both are
*interactions* between pytest's hooks, its option parsing and a config file, so a bug in either is
invisible from a unit test of one function. That is why both are pinned with real inner pytest
sessions through `pytester`. The first version of the gate shipped with five bypasses because
nothing tested it, and the old gate fails 16 of the tests that pin it now.

**Test every change here the same way**: an inner session in `tests/unit/test_live_gate.py` (a
miniature tree that uses the real `tests/conftest.py`) or `tests/unit/test_pytest_plugin.py`.
Write the test first and watch it fail against the current code.

## The live-suite gate (`tests/conftest.py`)

A live test runs only when a *positional argument* names a path inside `tests/e2e` that contains
it. The module docstring explains the layout; these are its rules and the hole each one closed.

- **Consent comes from `config.args`, and only when `config.args_source` is `ARGS`**
  (`_live_arguments`). The first version scanned the raw command line and took every word not
  starting with `-` for a path, option *values* included. So `pytest tests/ --ignore tests/e2e/qt`,
  an *exclusion*, opted the run into all 40 Libero tests. It hid because `--ignore=` with an
  equals sign did not trigger it. `--deselect X`, `--rootdir tests/e2e` and `-p no:cacheprovider`
  from inside the tree had the same hole. `testpaths` and the fallback to the working directory are
  never consent. `@file`, `PYTEST_ADDOPTS` and `addopts` paths are, since pytest treats them as
  positional arguments.
- **Consent is per test** (`_covers`). A live argument covers only the tests beneath it. A node id
  covers what it names, and `::test_x` does not cover `test_x_other`. It used to be one yes/no for
  the whole run, so `pytest tests/ tests/e2e/qt` got Libero too, and so did a node id that matched
  nothing.
- **A marker is never consent.** `-m "not slow"` is how people ask for a quicker run, and it must
  not mean "launch Libero SoC". The gate is `pytest_collection_modifyitems` with `trylast`, so it
  runs after pytest's own `-m`, `-k` and `--deselect`, and its message describes what was really
  withheld. The directory markers are applied earlier, in `pytest_itemcollected`, from
  `_DIRECTORY_MARKERS`. Two implementations of one hook cannot live in one module, which is why
  marking moved to the other hook.
- **The Libero modules have a backstop**, because `--noconftest` switches off the gate along with
  every other conftest. The gate records the files it let through in
  `config._liberaqt_live_consent` (`CONSENT_ATTR`). Each Libero module carries a module-scoped
  autouse `_asked_for_by_path` fixture that skips unless its file is in that set. It is
  module-scoped so it runs *before* the module fixture that launches Libero; a function-scoped one
  would run after. `tests/unit/test_tree_layout.py` checks that every Libero module has it.
- **The Qt pin is checked in `pytest_collection_finish`, against the final selection.**
  `--liberaqt-qt-bin` with no Qt test selected is a usage error. So is a pinned directory that does
  not exist, which used to turn all 63 Qt tests into skips and exit 0. `LIBERAQT_QT_BIN` is a
  standing default like `QTDIR`, so exporting it does not break `pytest tests/ -m "not live"`; it
  only has to be valid when a Qt test uses it.
- **`pytest_addoption` lives in `tests/conftest.py`, not `tests/e2e/conftest.py`**, even though
  the option belongs to the e2e suite. pytest calls that hook only for *initial* conftests, the
  ones on the path to a command-line argument. From the e2e conftest the option registered for
  `pytest tests/e2e` and not for `pytest tests`, without an error, and `--liberaqt-qt-bin` was then
  silently ignored. Because the option lives in a conftest, pytest's first pass does not know it:
  write `--liberaqt-qt-bin=DIR`, or put a test path before it.

### Adding a live test module

- Put it under `tests/e2e/qt/` or `tests/e2e/libero/`. The directory supplies `live` plus `qt_app`
  or `libero`, so a new live test cannot forget its markers.
- Declare `writes_disk` and `slow` per file when they apply, since they cut across directories.
- A Libero module needs the `_asked_for_by_path` fixture; copy it from an existing module.
- Skip cleanly when the application is absent. A fixture *error* escapes an `xfail` marker, so a
  fixture for an optional or unbuilt artifact must call `pytest.skip`.
- Then check the counts in CLAUDE.md's Commands section still hold, and update them.

## The pytest plugin (`src/liberaqt/pytest_plugin.py`)

Four things about it each cost a real bug. All are pinned by `tests/unit/test_pytest_plugin.py`.

- **An option that has a default cannot beat the config file.** `--liberaqt-timeout` had
  `default=5.0`, so `setdefault` could not tell "passed" from "not passed" and `liberaqt.toml`
  always won. Every option the toml can also set has `default=None`. `--liberaqt-headless` is a
  `BooleanOptionalAction` so the command line can turn it *off*; otherwise `headless = true`
  hard-failed every test on Windows with no way out.
- **Diagnostics are written from `pytest_runtest_makereport`, not from a fixture teardown.**
  Teardown is too late for `app`, which closes its application there, and never ran for
  `app_session` at all. The hook fires after the test body and before any teardown, finds the
  driver through `config.stash`, and captures every application still running.
- **Trace file names come from the scrubbed node id**, capped at 150 characters. The bare test name
  collided across files. On Windows a `:` in a parametrize id wrote both files into NTFS alternate
  data streams of a zero-byte file, invisible to everything.
- **The auth token is redacted in `Transport._record`**, because the history is what lands in
  `liberaqt-trace/`, and CI uploads that folder. It redacts a *copy*. The wire bytes are serialised
  before `_record` runs, but mutating the caller's dict would still be a trap.

**A config that exists and is ignored must never be silent.** An unknown key, a `[tool.liberaqt]`
section and a Python without a TOML parser all warn. The last one was every 3.9 and 3.10 user,
including the 3.9 CI leg, until `tomli` was made a dependency for them.
