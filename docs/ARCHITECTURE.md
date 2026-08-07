# LiberaQT — Architecture

`liberaqt` is an open-source, Python-only UI automation tool for Qt desktop applications.
Think Playwright, but the "browser" is a Qt process and the "DOM" is the `QObject` tree.

Target Qt versions for v0: **Qt 6.7** and **Qt 5.15**. Target platforms: **Windows** and **Linux**.
Both **QWidget** and **QML / Qt Quick** are supported from day one.

---

## 1. Big picture

```
+------------------------------+         +--------------------------------------+
|  Python test process         |         |  Application Under Test (AUT)        |
|                              |         |                                      |
|  pytest / your script        |         |   +--------------------------------+ |
|    `-- liberaqt (client)     |  JSON   |   | liberaqt agent (C++ Qt plugin) | |
|          `-- Transport ------+-------->|   |  * TCP/JSON server             | |
|              (TCP loopback)  |<--------+---|  * Object registry             | |
|                              |  events |   |  * Selector engine             | |
|                              |         |   |  * Input synthesis             | |
|                              |         |   |  * Widget + Quick backends     | |
|                              |         |   |  * Recorder (event filter)     | |
|                              |         |   +--------------------------------+ |
+------------------------------+         +--------------------------------------+
```

Three components:

1. **`liberaqt` (Python package)** — what users `pip install`. Public API, selector parsing,
   auto-waiting, assertions, process launching, pytest plugin, recorder/codegen, CLI.
2. **`liberaqt-agent` (C++ Qt library)** — loaded inside the AUT. It is the only piece that
   touches Qt internals. Compiled once per (Qt version, platform, compiler ABI) combination and
   shipped as a downloadable binary.
3. **Wire protocol** — line-delimited JSON over a loopback TCP socket. See `PROTOCOL.md`.

### Why an in-process agent rather than OS-level automation?

Accessibility-based drivers (UIAutomation / AT-SPI) are flaky on Qt: QML exposes very little,
custom-painted widgets expose nothing, and there is no way to read arbitrary properties or call
slots. Squish solves this by hooking the process; we do the same. The in-process agent gives us:

* the full `QObject` tree, including non-visual objects,
* every `Q_PROPERTY` on every object, read **and** write,
* invokable slots and QML methods,
* real `QEvent` delivery with correct posting semantics,
* the Qt event loop for reliable "is the UI idle?" synchronisation.

---

## 2. Component breakdown

### 2.1 Python client (`src/liberaqt/`)

| Module | Responsibility |
| --- | --- |
| `transport.py` | Framed JSON over TCP, request/response correlation, event demultiplexing, timeouts. |
| `protocol.py` | Command name constants, payload helpers, protocol version check. |
| `launcher.py` | Spawns the AUT with the correct injection environment; handshake, stdout/stderr capture, teardown. |
| `agent_registry.py` | Resolves which prebuilt agent binary matches the AUT Qt version/platform/ABI. |
| `application.py` | `Application` handle: windows, screenshots, close, logs, `evaluate()`. |
| `window.py` | Window handle: geometry, activate, close, root locator. |
| `selectors.py` | Parses the selector mini-language into a JSON query object. |
| `locator.py` | Lazy `Locator`: chaining, `.nth()`, `.filter()`, actions, property access. All actions auto-wait. |
| `expect.py` | Retrying assertions (`expect(loc).to_be_visible()`). |
| `waits.py` | Retry engine, timeout policy, `wait_for_idle()`. |
| `mouse.py` / `keyboard.py` | Low-level input for cases where locator actions are not enough. |
| `spy.py` / `codegen.py` | Recorder client + Python source generation. |
| `cli.py` | `liberaqt doctor / inspect / record / agents`. |
| `pytest_plugin.py` | Fixtures, screenshot-on-failure, `--liberaqt-*` options. |

Design rules for the client:

* **Synchronous API only.** Test authors write plain, blocking code. Concurrency lives in the
  transport thread. (An async API can be layered later; the protocol is already async underneath.)
* **Lazy locators.** `window.locator(...)` performs no I/O. Resolution happens at action time and
  is retried until the auto-wait timeout expires. This removes most of the flakiness that
  `find_element`-style APIs have.
* **No Qt dependency in the client.** `pip install liberaqt` must not need PyQt/PySide.

### 2.2 C++ agent (`agent/`)

| Unit | Responsibility |
| --- | --- |
| `agent_plugin` | Entry point. Instantiated by Qt during app startup; arms a zero-delay timer so the server starts once the event loop runs. |
| `server` | `QTcpServer` on `127.0.0.1`, newline-delimited JSON, one client at a time, token auth. |
| `dispatcher` | Command table; validates params; runs handlers on the GUI thread via queued invocation. |
| `object_registry` | Assigns stable string handles to `QObject*`; cleans up on `destroyed()`; resolves handle -> object. |
| `selector_engine` | Walks the object tree, evaluates predicates, orders matches deterministically. |
| `widget_backend` | QWidget specifics: geometry mapping, item views (index -> rect), menus, model data. |
| `quick_backend` | Qt Quick specifics: `QQuickItem` tree, `mapToScene`, attached properties, QML ids, JS evaluation. |
| `input_synth` | Mouse/key/touch/wheel synthesis, drag and drop, IME text input. |
| `screenshot` | `QWidget::grab`, `QQuickWindow::grabWindow`, item-level crops, PNG encode. |
| `recorder` | Global event filter that streams semantic user actions to the client for codegen. |
| `idle_tracker` | Heuristic "UI is settled" signal: event queue drained, no active animations, no pending timers. |

**Threading rule:** every handler that touches a `QObject` runs on the GUI thread. The socket lives
on a worker thread; requests are marshalled with `QMetaObject::invokeMethod(..., Qt::QueuedConnection)`
so a blocked GUI thread produces a clean client-side timeout instead of a crash.

**ABI rule:** the agent is a Qt plugin, so it must be built with the same Qt minor version and the
same compiler/stdlib as the AUT. `agent_registry.py` picks the right binary; `liberaqt doctor`
diagnoses mismatches. See `INJECTION.md`.

---

## 3. Injection

Primary mechanism (no source changes, no ptrace, no admin rights): **Qt generic plugins**.

Qt instantiates every plugin named in `QT_QPA_GENERIC_PLUGINS` during `QGuiApplication`
construction. The launcher sets:

```
QT_QPA_GENERIC_PLUGINS=liberaqt
QT_PLUGIN_PATH=<dir containing generic/liberaqt.{so,dll}>
LIBERAQT_PORT=<port>
LIBERAQT_TOKEN=<random>
```

This works for Qt 5.15 and 6.7, on Windows and Linux, for both widget and QML apps, and requires
nothing from the application. Fallbacks (`LD_PRELOAD`, Windows DLL injection for attach-to-running,
and an opt-in in-app `LiberaQt::start()` call) are described in `INJECTION.md`.

---

## 4. Object identification

Two layers, mirroring what makes Squish usable at scale:

1. **Selectors** — an inline mini-language (`QPushButton#okButton[text='OK']`) or a dict.
   See `SELECTORS.md` for grammar and matching rules.
2. **Object map** — an optional `objects.yaml` mapping symbolic names to selectors, so a UI change
   is fixed in one file instead of fifty tests:

   ```yaml
   login.username: "QLineEdit#usernameField"
   login.submit:   "QPushButton[text='Log in']"
   ```

   ```python
   window.obj("login.submit").click()
   ```

Matching is **deterministic**: candidates are collected in depth-first tree order, so `.nth(0)`
means the same element on every run.

---

## 5. Synchronisation model

Every action follows the same loop, bounded by `timeout` (default 5 s):

1. Resolve the selector to exactly one object (retry while 0 matches; fail on ambiguity unless
   `.nth()` / `.first` was used).
2. Check actionability: exists -> visible -> enabled -> not obscured -> non-empty geometry.
3. Perform the action.
4. Wait for idle: event queue drained, no running animations, no pending network replies (opt-in),
   one extra event-loop turn.

Steps 1-2 retry on a 50 ms poll. Failures raise structured exceptions with a dump of near-miss
candidates ("found 3 QPushButton, none with text 'OK'; closest was 'Ok'").

---

## 6. Recording

`liberaqt record ./app` launches the AUT with the agent in recorder mode. The agent installs an
application-level event filter, converts raw events into semantic actions (click on the widget that
actually handled it, text committed on focus-out, item-view row selected), computes the most robust
selector for the target, and streams them. The Python side renders a runnable pytest file.

Selector ranking during recording: `objectName` > accessible name > unique type+text >
type + index within a named ancestor. Anything that resolves to a position-only match is emitted
with a `# TODO: brittle selector` comment.

---

## 7. Packaging and distribution

* `pip install liberaqt` -> pure-Python client + CLI, no agent binaries.
* `liberaqt agents install --qt 6.7` -> downloads the matching prebuilt agent from GitHub Releases
  into a user cache dir.
* Optional platform wheels (`liberaqt-agent-qt67-manylinux`, `-win64`) for air-gapped CI.
* Building the agent from source is one CMake invocation against any Qt install; documented for
  users whose Qt build has a custom ABI.

License: **Apache-2.0** (permissive, patent grant, safe for commercial adoption — the main reason
teams look for a Squish alternative in the first place).

---

## 8. Non-goals for v0

* Languages other than Python.
* Mobile (Android/iOS) and embedded targets.
* Qt versions other than 5.15 and 6.7.
* Image/OCR-based matching (deliberately deferred — object-based matching first).
* Remote/distributed execution grid.
