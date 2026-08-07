






# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is LiberaQT?

A Playwright-style UI automation library for Qt desktop applications. It works by injecting a C++ agent (Qt plugin) into the application under test, which exposes the QObject tree over JSON/TCP to a Python client. Supports Qt 5.15 and 6.7, both QWidget and QML, Windows and Linux.

## Architecture at a glance

```
pytest/script (Python) ──JSON/TCP──► liberaqt agent (C++ Qt plugin) ──► QObject tree
```

**Three layers:**
1. **Python client** (`src/liberaqt/`) — User-facing API, selector parsing, pytest plugin, CLI, process launching
2. **Wire protocol** — Newline-delimited JSON over loopback TCP with token auth
3. **C++ agent** (`agent/src/`) — Runs inside the AUT; Qt plugin that exposes objects, events, input synthesis

**Threading model:** Agent handlers marshalled to GUI thread via `Qt::QueuedConnection` to prevent crashes on blocked GUI.

## Common development commands

```bash
# Setup
pip install -e ".[dev]"                    # Install client in dev mode with all test deps

# Unit tests (Python only, no Qt needed)
pytest tests/                              # Full suite
pytest tests/test_selectors.py -v          # Single file with verbose output
pytest tests/test_selectors.py::test_parse_type_with_nth -v  # Single test

# Integration tests (needs agent + sample app built)
pytest examples/tests/ --liberaqt-exe build/sample/sample_widgets

# Linting and type checking
ruff check src/ tests/ agent/               # Format and style (ruff is used in CI)
ruff format src/ tests/                    # Auto-format Python
mypy src/liberaqt                          # Type checking

# Build agent (C++)
cmake -S agent -B build/agent -DCMAKE_PREFIX_PATH=$QTDIR -DCMAKE_BUILD_TYPE=Release
cmake --build build/agent --parallel
cmake --install build/agent --prefix ~/.cache/liberaqt/agents/qt6.7-linux-x86_64-gcc

# Build sample app
cmake -S examples/sample_app -B build/sample -DCMAKE_PREFIX_PATH=$QTDIR
cmake --build build/sample --parallel

# Local development shortcuts
cmake --build build/agent --parallel && cmake --install build/agent --prefix ~/.cache/liberaqt/agents/qt6.7-linux-x86_64-gcc
```

## Key modules and responsibilities

### Python client (`src/liberaqt/`)

| Module | Purpose |
|--------|---------|
| `transport.py` | Framed JSON over TCP, request/response correlation, event demux, timeouts |
| `protocol.py` | Command constants, payload helpers, version negotiation |
| `launcher.py` | Spawn AUT with injection env, handshake, stdout/stderr capture, cleanup |
| `agent_registry.py` | Resolve prebuilt agent binary by Qt version/platform/ABI |
| `application.py` | `Application` class: windows, screenshots, close, logs |
| `window.py` | `Window` class: geometry, root locator, activate, close |
| `locator.py` | `Locator` class: lazy resolution, chaining, actions, auto-wait retry logic |
| `selectors.py` | Parse selector mini-language into JSON query |
| `expect.py` | Retrying assertions with human-friendly error messages |
| `waits.py` | Retry loop, timeout management, idle detection |
| `mouse.py` / `keyboard.py` | Low-level input for complex interactions |
| `spy.py` / `codegen.py` | Event recording + Python test generation |
| `suggest.py` | Rank candidate selectors per object; uniqueness verified by the agent |
| `cli.py` | Commands: `doctor`, `inspect` (+`--validate`), `record`, `agents` |
| `pytest_plugin.py` | Fixtures, screenshot-on-failure, CLI options |

**Design rules:**
* Synchronous API only (test code is blocking)
* Lazy locators: no I/O until action time
* No Qt dependency in client

### C++ agent (`agent/src/`)

| Unit | Purpose |
|------|---------|
| `agent_plugin` | Entry point, arms zero-delay timer for startup |
| `server` | `QTcpServer` on 127.0.0.1, JSON protocol, token auth, one client |
| `dispatcher` | Command table, param validation, handler dispatch to GUI thread |
| `object_registry` | Stable string handles for `QObject*`, cleanup on `destroyed()` |
| `meta_invoke` | Calls slots / `Q_INVOKABLE` methods by name; diagnoses unreachable ones |
| `selector_engine` | Walk object tree, evaluate predicates, deterministic ordering |
| `widget_backend` | QWidget geometry, item views, menus, model data |
| `quick_backend` | QML: `QQuickItem` tree, attached properties, JS eval |
| `input_synth` | Mouse/key/touch/wheel, drag-drop, IME text |
| `screenshot` | `QWidget::grab`, `QQuickWindow::grabWindow`, PNG encode |
| `recorder` | Global event filter for action recording → codegen |
| `idle_tracker` | "UI settled" heuristic: queue drained, no animations/timers |

**Guidelines for C++ changes:**
* Minimal logic here; anything that can live in Python does
* Never throw across Qt event loop boundary; convert to protocol error instead
* Every new command needs: PROTOCOL.md entry, dispatcher handler, client method, test + conformance suite entry
* Guard Qt 5/6 differences in `compat.h`, not inline in code

## Selector syntax and locator patterns

Selectors are inline mini-language or YAML object maps. Examples:

```python
win.locator("QPushButton#okButton")                    # type + objectName
win.locator("QDialog#settings > QPushButton[text='Apply']")  # hierarchy
win.locator("QTableView QLineEdit:visible:nth(1)")     # pseudo-selectors
win.locator("*[accessibleName^='Volume']:enabled")     # attribute matching
win.locator("QListView:has(QLabel[text='Inbox'])")     # :has() filter
```

Full grammar in `docs/SELECTORS.md`. Object maps in `objects.yaml` keep selectors DRY across tests.

## Auto-wait / synchronisation model

Every action (click, fill, evaluate):
1. Resolve selector to exactly one object (retry 50ms until timeout)
2. Check actionability: exists → visible → enabled → not obscured → has geometry
3. Perform action
4. Wait for idle: event queue drained + animations done + extra event-loop turn

Failures raise structured exceptions with near-miss suggestions.

## Wire protocol

Newline-delimited JSON over `127.0.0.1` loopback TCP. See `docs/PROTOCOL.md` for full command surface. Key commands:

* `inspect` — resolve selector, return objects
* `click` / `fill` / `select` — user actions
* `get_property` / `set_property` — Q_PROPERTY access
* `call_method` — invoke slots
* `screenshot` — grab window or item
* `evaluate_js` — QML expression eval

## Pytest plugin

Register in `conftest.py`:

```python
pytest_plugins = ["liberaqt.pytest_plugin"]
```

Configuration in `liberaqt.toml`:

```toml
[liberaqt]
executable = "build/myapp"
object_map = "objects.yaml"
headless = true
```

Fixtures: `app` (launched Application), `caplog_liberaqt` (protocol messages). On failure, writes screenshot + last 200 protocol messages to `liberaqt-trace/`.

## Injection mechanism

**Primary:** Qt generic plugins. Launcher sets:

```
QT_QPA_GENERIC_PLUGINS=liberaqt
QT_PLUGIN_PATH=<dir with generic/liberaqt.so>
LIBERAQT_PORT=<port>
LIBERAQT_TOKEN=<random token>
```

Works on Windows/Linux, Qt 5.15/6.7, widgets and QML, no app changes needed. Fallbacks (LD_PRELOAD, Windows DLL injection, opt-in `LiberaQt::start()`) in `docs/INJECTION.md`.

## Testing patterns

**Unit tests** (no Qt): `tests/test_*.py` — selector parsing, protocol, waits logic.

```bash
pytest tests/test_selectors.py -v
```

**Integration tests** (full stack): `examples/tests/test_*.py` — needs built agent + sample app.

```bash
pytest examples/tests/test_quick.py -v --liberaqt-exe build/sample/sample_qml
```

**Adding a new command:**
1. Add handler in `agent/src/dispatcher.cpp` (runs on GUI thread)
2. Add transport method in `src/liberaqt/transport.py`
3. Add client API in appropriate module (`application.py`, `window.py`, `locator.py`)
4. Update `docs/PROTOCOL.md`
5. Write test against `examples/sample_app`
6. Add to conformance suite

## ABI and agent binaries

The agent is a Qt plugin; it must match the AUT's Qt **minor version** (6.7 not 6.8) and **compiler/stdlib ABI**. `agent_registry.py` picks the right binary; `liberaqt doctor ./app` diagnoses mismatches. See `docs/INJECTION.md` for details.

Build layout: `<prefix>/plugins/generic/liberaqt.{so,dll,dylib}`

## Documentation map

| Document | When to read |
|----------|--------------|
| `docs/PROTOCOL.md` | Adding commands, understanding wire format |
| `docs/SELECTORS.md` | Selector syntax, matching rules, grammar details |
| `docs/API.md` | Python API design philosophy |
| `docs/INJECTION.md` | Injection trade-offs, fallback mechanisms |
| `docs/ROADMAP.md` | Planned features, milestones |
| `docs/OPEN_QUESTIONS.md` | Unresolved design decisions |

## Coding style

* Python: Ruff rules in `pyproject.toml` (line-length 100, E/F/I/UP/B checks)
* C++: Qt conventions, guard Qt 5/6 in `compat.h`
* No Qt dependency in Python client
* All handlers on GUI thread; use `Qt::QueuedConnection` for marshalling