# LiberaQT — Python API design

Design target: a test written against `liberaqt` should read like a description of what a user
does, and a developer who has never seen the tool should be able to guess the method name.

!!! note "This is a design document, not a reference"
    It states the shape the API aims for. Anything not yet implemented is marked
    `NOT IMPLEMENTED` inline. For what actually exists today, see the
    [Python API reference](reference/api.md), which is generated from the code.

## 1. Hello world

```python
from liberaqt import liberaqt

with liberaqt() as qd:
    app = qd.launch("./build/myapp", args=["--no-splash"])
    win = app.window(title="Login")

    win.locator("QLineEdit#username").fill("sargis")
    win.locator("QLineEdit#password").fill("hunter2")
    win.locator("QPushButton[text='Log in']").click()

    assert win.locator("QLabel#status").text == "Welcome, sargis"
```

## 2. Entry points

```python
qd.launch(executable, args=[], cwd=None, env=None, qt=None,
          object_map=None, timeout=30.0, record=False) -> Application
qd.connect(port=..., token=...) -> Application       # attach to a running agent
```

`qt=` forces an agent build when auto-detection is ambiguous (e.g. an app that ships two Qt
versions side by side).

## 3. `Application`

| Member | Notes |
| --- | --- |
| `.windows` | list of `Window`, live |
| `.window(title=None, index=0, timeout=...)` | waits for a matching window |
| `.wait_for_window(title=..., timeout=...)` | explicit wait |
| `.screenshot(path=None) -> bytes` | full app, all windows |
| `.logs` | captured qDebug/qWarning stream |
| `.info` | qt version, pid, platform |
| `.close(timeout=5)` / `.kill()` | graceful then forceful |
| `.on(event, callback)` | subscribe to protocol events |

## 4. `Window`

`Window` is a `Locator` root, so everything a locator does works on it.

| Member | Notes |
| --- | --- |
| `.title`, `.geometry`, `.is_active` | properties |
| `.activate()`, `.close()`, `.resize(w, h)`, `.move(x, y)` | |
| `.locator(...)`, `.obj("name.from.map")` | element lookup |
| `.screenshot(path=None)` | |
| `.keyboard`, `.mouse` | window-scoped low-level input |

## 5. `Locator` — lazy, chainable, auto-waiting

Query:

```python
loc.locator(sel)            # descendant
loc.child(sel)              # direct child
loc.filter(has=..., has_text=..., has_not=...)
loc.first / .last / .nth(i)
loc.all() -> list[Locator]  # resolves now
loc.count -> int
```

Actions (each auto-waits for actionability, then waits for idle):

```python
loc.click(button="left", modifiers=None, position=None, count=1)
loc.double_click(); loc.right_click(); loc.hover()
loc.fill(text)              # clear + set + commit  (fast, no per-key events)
loc.type(text, delay=0)     # real key events, for apps with keystroke handlers
loc.press("Ctrl+S")
loc.clear()
loc.check(); loc.uncheck(); loc.set_checked(bool)
loc.select_option(text=... | index=...)     # combo boxes
loc.select_item(text=... | row=..., column=...)   # item views
loc.scroll_into_view(); loc.wheel(dy=...)
loc.drag_to(other_locator)
loc.screenshot(path=None)
```

State (each is a single round trip, no retry — use `expect` for retrying checks):

```python
loc.text; loc.value; loc.is_visible; loc.is_enabled; loc.is_checked
loc.geometry; loc.class_name; loc.object_name
loc["someProperty"]              # read any Q_PROPERTY
loc["someProperty"] = 42         # write any Q_PROPERTY
loc.invoke("myInvokableSlot", 1, "a")
loc.evaluate("model.count")      # QML only
```

Convenience wrappers where the raw property model is too fiddly:

```python
table = win.locator("QTableView#orders")
table.row(has_text="INV-1042")
table.cell(row=3, column="Total").text
table.to_records()               # whole model as list[dict], for data assertions

tree = win.locator("QTreeView#project")
tree.item("src/main.cpp").click()        # .expand() is NOT IMPLEMENTED

menu = win.menu("File > Recent > foo.txt").trigger()
```

## 6. Assertions

```python
from liberaqt import expect

expect(loc).to_be_visible(timeout=5)
expect(loc).to_be_enabled()
expect(loc).to_be_checked()
expect(loc).to_have_text("Done")            # exact, whitespace-normalised
expect(loc).to_contain_text("Done")
expect(loc).to_have_property("value", 42)
expect(loc).to_have_count(3)
expect(win).to_have_title("Settings")
expect(app).to_have_no_window(title="Splash")   # NOT IMPLEMENTED
```

Each retries on a 50 ms poll until timeout, then raises `AssertionError` with the actual value,
the selector, and (on `not_found`) the near-miss list.

Visual assertion, opt-in and off by default:

```python
expect(win).to_match_screenshot("login.png", threshold=0.02)   # NOT IMPLEMENTED
```

## 7. Synchronisation escape hatches

Auto-wait covers most cases. When it does not:

```python
app.wait_for_idle(quiet_ms=200)
loc.wait_for_signal("clicked", timeout=3)
qd.set_default_timeout(10)
with qd.timeout(30):
    slow_thing()
```

## 8. pytest integration

```python
# The plugin loads itself through its pytest11 entry point once the package is
# installed. Do NOT add pytest_plugins = ["liberaqt.pytest_plugin"] to a conftest: that
# registers it twice and pytest aborts.

# test_login.py
def test_login(app):                 # fixture launches from liberaqt.toml
    win = app.window(title="Login")
    ...
```

Fixtures: `liberaqt` (session), `app` (function, fresh process), `app_session` (session-scoped for
speed), `win`, `liberaqt_config`. Options: `--liberaqt-exe`, `--liberaqt-qt`, `--liberaqt-headless`,
`--liberaqt-slowmo`, `--liberaqt-trace`, `--liberaqt-timeout`, `--liberaqt-input-mode`. On failure the plugin attaches a screenshot and the last
N protocol messages to the report — the single most useful debugging feature a UI tool can have.

Config file `liberaqt.toml`:

```toml
[liberaqt]
executable = "build/myapp"
args = ["--test-mode"]
qt = "6.7"
object_map = "objects.yaml"
timeout = 5.0
headless = true          # Linux: run under Xvfb / offscreen QPA
```

## 9. CLI

```
liberaqt doctor                  # env check: Qt found, agent ABI match, ptrace scope, display
liberaqt agents list|install|remove   # manage installed agent binaries
liberaqt agents kits|build       # Qt kits on this machine, and build an agent from one
liberaqt docs                    # serve this documentation locally
liberaqt inspect ./myapp         # launch + interactive object browser (REPL + tree dump)
liberaqt record ./myapp -o test_x.py
liberaqt run tests/              # thin pytest wrapper with sane defaults
```

## 10. Error taxonomy

```
LiberaQtError
├── LaunchError            (agent never connected; carries AUT stderr)
├── AgentMismatchError     (ABI / Qt version mismatch, with fix command)
├── ConnectionLostError    (socket dropped mid-run; carries crash info)
├── SelectorError
│   ├── ObjectNotFoundError  (+ near-miss candidates)
│   ├── AmbiguousSelectorError (+ all matches with distinguishing props)
│   └── StaleObjectError
├── NotActionableError     (visible=False / enabled=False / obscured-by)
├── TimeoutError
└── UnsupportedOperationError
```

Every message includes the selector, the resolved handle if any, and a one-line "try this" hint.
