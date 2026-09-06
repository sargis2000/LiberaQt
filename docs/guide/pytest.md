# Running under pytest

The pytest plugin loads automatically once the package is installed — it registers a `pytest11`
entry point.

!!! danger "Do not add `pytest_plugins = ["liberaqt.pytest_plugin"]` to a conftest"
    That registers the module twice and pytest aborts. Declare it only when running against a
    source checkout that is *not* pip-installed.

## Configuration

Put defaults in `liberaqt.toml` at your rootdir, so tests do not hard-code paths:

```toml title="liberaqt.toml"
[liberaqt]
executable = "C:/Qt/6.7.3/mingw_64/bin/assistant.exe"
qt = "6.7"
timeout = 10.0
input_mode = "native"
headless = false
```

Precedence is **command line → `liberaqt.toml` → the `LIBERAQT_EXE` environment variable**.

## Fixtures

| Fixture | Scope | What it is |
|---------|-------|------------|
| `app` | function | a freshly launched `Application` |
| `app_session` | session | one `Application` shared by the whole run |
| `win` | function | the application's main window |
| `liberaqt` | function | the `LiberaQt` entry point itself |
| `liberaqt_config` | session | the resolved configuration dict |

```python
def test_the_window_opens(win):
    expect(win.locator("HelpViewer")).to_be_visible()


def test_a_dock_is_present(app):
    win = app.window(title="Qt Assistant")
    expect(win.locator("QDockWidget#IndexWindow")).to_exist()
```

!!! note "`Window` does not know its `Application`"
    There is no `win.app`. Keep both fixtures when you need the application later — for
    `app.capabilities`, or to reach a second window.

!!! note "These are built around a *single* configured executable"
    A suite that drives several different applications in one session has to build its own
    fixtures on top of `LiberaQt` directly. That is a deliberate limit, not an oversight.

## Command-line options

| Option | Effect |
|--------|--------|
| `--liberaqt-exe PATH` | the application to launch |
| `--liberaqt-qt 6.7` | force a Qt version instead of detecting it |
| `--liberaqt-headless` | offscreen QPA platform (Linux only) |
| `--liberaqt-slowmo 0.5` | sleep before each command, to watch a test run |
| `--liberaqt-timeout 10` | default action timeout |
| `--liberaqt-trace` | log every protocol message |
| `--liberaqt-input-mode native\|synthetic` | how input reaches the application |

```bash
pytest -v --liberaqt-exe "C:/Qt/6.7.3/mingw_64/bin/assistant.exe" --liberaqt-slowmo 0.3
```

`--liberaqt-slowmo` is the one to reach for when a test fails and you cannot see why: it slows
every command enough to watch.

## Failure diagnostics

When a test fails, the plugin writes to `liberaqt-trace/`:

- a **screenshot** of the application at the moment of failure;
- the **last protocol messages** exchanged.

That is usually enough to tell "the selector matched nothing" from "the click landed on the wrong
thing" without re-running.

## Writing your own fixtures

For anything the built-ins do not cover, use `LiberaQt` directly:

```python
import pytest
from liberaqt import LiberaQt, LiberaQtError

@pytest.fixture(scope="module")
def configured_app():
    with LiberaQt(default_timeout=20.0) as lq:
        app = lq.launch(EXE, timeout=240.0, object_map="objects.yaml")
        app.set_input_mode("native")
        try:
            app.wait_for_window(title="Update", timeout=30).obj("startup.dismiss").click()
        except LiberaQtError:
            pass                       # the prompt does not always appear
        yield app
```

!!! warning "A fixture *error* escapes an `xfail` marker"
    Only call-phase failures are covered by `xfail`. A fixture that raises will fail the test
    regardless, so fixtures for optional or unbuilt artifacts must `pytest.skip` instead of
    raising.

## Structuring a suite

Two suites with different jobs works well, and is what this project does for itself:

- **Unit tests** that need no Qt at all — selector parsing, error mapping, configuration. These
  run in CI on every push and are fast.
- **End-to-end tests** that drive real applications. These need the application installed, so they
  `pytest.skip` cleanly when it is absent, and they are where agent bugs are actually found.

!!! tip "Drive software that has never heard of your test framework"
    A purpose-built sample application agrees with whatever your driver happens to do. Every
    serious bug in this project was found against third-party software and none against a toy.
