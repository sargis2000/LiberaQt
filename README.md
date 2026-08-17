# LiberaQT

Playwright-style UI automation for Qt desktop applications. Python only, open source,
`pip install`-able.

An open alternative to Squish, scoped deliberately narrowly: **Python** as the only test language,
**Qt 5.15 and 6.7** as the supported versions, **Windows and Linux** as the platforms, and
**QWidget and QML** both supported from day one.

> Status: design + skeleton. The Python client, the protocol and the selector engine are
> implemented; the C++ agent is scaffolded with the structure and the hard decisions in place.
> See `docs/ROADMAP.md` for what lands when.

```python
from liberaqt import liberaqt, expect

with liberaqt() as qd:
    app = qd.launch("./build/myapp")
    win = app.window(title="Login")

    win.locator("QLineEdit#usernameField").fill("sargis")
    win.locator("QLineEdit#passwordField").fill("hunter2")
    win.locator("QPushButton[text='Log in']").click()

    expect(win.locator("QLabel#statusLabel")).to_have_text("Welcome, sargis")
```

No `sleep()`. Every action waits for the object to exist, become visible and become enabled, then
waits for the UI to settle afterwards.

## How it works

A small C++ agent is loaded into the application under test through Qt's **generic plugin**
mechanism — three environment variables, no source changes, no debugger, no elevated privileges.
The agent exposes the live `QObject` tree over a loopback JSON socket; the Python package turns
that into locators, assertions and a pytest plugin.

```
pytest  ──►  liberaqt (Python)  ──JSON/TCP──►  liberaqt agent (in your app)  ──►  QObject tree
```

Because the agent lives inside the process, it can read and write any `Q_PROPERTY`, call any slot,
evaluate QML expressions, and deliver real `QEvent`s — none of which accessibility-based tools can
do reliably on Qt.

## Install

```bash
pip install liberaqt                 # client + CLI
liberaqt agents install --qt 6.7     # matching prebuilt agent for your Qt build
liberaqt doctor ./build/myapp        # verify the pieces line up
```

The agent is a Qt plugin, so it must match your Qt minor version and compiler ABI. `doctor` tells
you if it does not, and prints the one-line CMake command to build one that does.

## Selectors

```python
win.locator("QPushButton#okButton")                  # type + objectName
win.locator("QDialog#settings > QPushButton[text='Apply']")
win.locator("QTableView QLineEdit:visible:nth(1)")
win.locator("*[accessibleName^='Volume']:enabled")
win.locator("QListView:has(QLabel[text='Inbox'])")
win.locator("QQuickWidget Button[text='Save']")      # QML, same language
```

### Finding out what to write

`liberaqt inspect ./app` launches the application and prints a selector for every object in it,
ranked by how well it will age — objectName first, then visible text, position only as a last
resort — with each one marked unique or ambiguous:

```
SELECTOR                        TEXT       STATUS
QLineEdit#usernameField                    unique
QLabel[text='User']             User       unique
QPushButton#submitButton        Log in     unique
QScrollBar:nth(0)                          positional (6 of this type)
```

Uniqueness is answered by the same selector engine your tests use, not guessed from the tree, so
`unique` means it. Add `-i` to keep the app open in a REPL and try selectors against it live.

For larger suites, keep selectors in an object map so a UI change is a one-line fix:

```yaml
# objects.yaml
login:
  submit: "QPushButton[text='Log in']"
```

```python
win.obj("login.submit").click()
```

Full grammar: `docs/SELECTORS.md`.

## pytest

```python
# conftest.py
pytest_plugins = ["liberaqt.pytest_plugin"]

# test_login.py
def test_login(app):
    win = app.window(title="Login")
    win.obj("login.submit").click()
```

```toml
# liberaqt.toml
[liberaqt]
executable = "build/myapp"
object_map = "objects.yaml"
headless = true
```

On failure the plugin writes a screenshot and the last 200 protocol messages to `liberaqt-trace/`,
so a red CI run is debuggable without reproducing it locally.

## CLI

```
liberaqt doctor [exe]        environment and ABI check
liberaqt agents list|install manage agent binaries
liberaqt inspect ./app       suggest a selector for every object, and say which are unique
liberaqt inspect ./app -i    same, then drop into a REPL with `app` and `win` bound
liberaqt inspect ./app --validate objects.yaml   check an object map still resolves
liberaqt record ./app -o test_x.py
liberaqt run tests/
```

## Security

The agent is a remote-code-execution surface by design. It binds `127.0.0.1` only, requires a
per-launch token, accepts one client, and stays completely dormant unless `LIBERAQT_TOKEN` is set.
**Never ship the agent plugin in a production build.**

## Documentation

| Document | Contents |
| --- | --- |
| `docs/ARCHITECTURE.md` | How the pieces fit together and why |
| `docs/PROTOCOL.md` | Wire protocol v1, full command surface |
| `docs/SELECTORS.md` | Selector grammar, matching rules, object maps |
| `docs/API.md` | Python API design |
| `docs/INJECTION.md` | Four ways to get the agent into a process, with trade-offs |
| `docs/ROADMAP.md` | Milestones, sizing, risk register |
| `docs/OPEN_QUESTIONS.md` | Decisions still to make |
| `docs/CONTRIBUTING.md` | Repo layout, build instructions, house rules |

## Building from source

```bash
pip install -e ".[dev]"
cmake -S agent -B build/agent -DCMAKE_PREFIX_PATH=$QTDIR -DCMAKE_BUILD_TYPE=Release
cmake --build build/agent --parallel
cmake --install build/agent --prefix ~/.cache/liberaqt/agents/qt6.7-linux-x86_64-gcc

pytest tests/          # unit tests of the client, no Qt needed
```

## Testing

`pytest tests/` is the whole suite: unit tests of the pure-Python client, with no Qt and no agent
involved. Nothing in the repository runs the agent, so a green suite says the client's parsing,
selectors and error handling are sound -- it says nothing about whether the agent works.

There is no sample application here either, and deliberately so: a purpose-built sample agrees
with whatever the driver happens to do, while a real application does not. Verify agent changes by
driving a real Qt program yourself, which needs nothing but a matching agent installed:

```python
from liberaqt import liberaqt

with liberaqt() as lq:
    app = lq.launch("/path/to/Qt/6.7.3/gcc_64/bin/assistant")
    win = app.window(title="Qt Assistant")
    print(win.locator("*").count)
```

`liberaqt doctor <exe>` first, to check the binary is injectable and an agent matches its ABI.

## Licence

Apache-2.0.
