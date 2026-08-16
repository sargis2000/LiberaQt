# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is LiberaQT?

A Playwright-style UI automation library for Qt desktop applications — an open alternative to
Squish, scoped narrowly: Python as the only test language, Qt 5.15 / 6.7, Windows and Linux,
QWidget and QML. A C++ agent (Qt generic plugin) is injected into the application under test and
exposes the live `QObject` tree over JSON/TCP to a pure-Python client.

```
pytest/script (Python) ──JSON/TCP──► liberaqt agent (C++ Qt plugin) ──► QObject tree
```

The working directory is still called `qtdriver` and the GitHub remote is still
`sargis2000/QT-Automation`; everything inside the repo is `liberaqt`. Naming convention: `liberaqt` lowercase for the package, C++ namespace, CMake target,
plugin key, CLI, config section and trace dir; `LiberaQT` in prose; `LiberaQt` in Python class
names (`LiberaQtError`); `LIBERAQT_*` for env vars and C++ macros.

## Project state — read this before planning work

The Python client is written ahead of the agent. `protocol.Cmd` lists the **planned** command
surface (~30 commands); `agent/src/dispatcher.cpp` registers **~18**. An unregistered command
returns `code: "unsupported"`, which maps to `UnsupportedOperationError` (`retryable = False`), so
the client API for it exists and fails cleanly rather than hanging.

Consequences when adding a feature: the client method usually already exists and the work is the
C++ handler. Check `dispatcher.cpp`'s `registerCommand` calls before assuming something is missing
from the client.

Known gaps, in the order they unblock the most client surface:

* `quick.evaluate`, `quick.find_by_id`, `quick.list_view_item`, `quick.wait_animations` — all QML
* `record.start` / `record.stop` — the recorder, and therefore `liberaqt record`
* `session.set_options` honours `input_mode` and nothing else; `idle_poll_ms`, `animation_wait`
  and `network_wait` are accepted and dropped
* `__scroll_into_view` is unimplemented; actionability does not detect obscuring widgets or modal
  dialogs (`TODO(m1)` in `widget_backend.cpp`)

Item views, menus, tabs, the remaining input events, property enumeration and `sync.wait_signal`
are all implemented; a cell is addressed by the composite handle described in `docs/PROTOCOL.md`.

**QML works better than it looks, and the gap is narrower than "deferred" suggests.** Measured
against `qmleasing` (a real Qt app with a Quick window):

* **Locators do reach QML objects.** `object.find` descends into the Quick scene, so
  `QQuickRectangle`, `QQuickText` and QML-defined types (`Button_QMLTYPE_0`) all resolve, and
  their properties read back. 40 objects found under the Quick window.
* **`object.tree` does not.** It returns zero children for the same window — the `TODO(m0)` at
  `selector_engine.cpp:150`, where the walk never steps from a `QQuickWindow` into its
  `contentItem`. That is why `liberaqt inspect` prints "no objects found" for a QML window and
  cannot suggest a QML selector.
* `Window.kind` reports a Quick top-level as `"widget"`, which is also wrong.

`integration/test_qmleasing.py` covers the working half and carries an `xfail(strict=True)` on the
tree walk, so fixing `TODO(m0)` turns it XPASS and CI demands the marker be removed.

## No sample application — test against real Qt programs

There is deliberately no purpose-built sample app in this repository. It was removed because a toy
application agrees with whatever the driver happens to do: three separate bugs that made LiberaQT
unusable on real software sat undetected behind a green sample suite (see the git history for
`sync.wait_idle`, namespaced class names, and objectNames that are not bare identifiers).

`integration/` drives **Qt's own shipped applications** instead — Designer, Assistant and Linguist.
They are large (Designer's main window alone holds ~190 objects, 56 scroll bars, 18 menus), were
written with no knowledge of this project, and — decisively — ship inside the Qt installation, so
they are built with exactly the Qt minor version and compiler ABI the matching agent needs. No
other large Qt application is injectable without being rebuilt against the right toolchain.

Each app has a session-scoped fixture in `integration/conftest.py` that **skips** rather than fails
when the application is absent. Qt is located from `--liberaqt-qt-bin=`, `LIBERAQT_QT_BIN`,
`QTDIR`, then beside `qmake` on `PATH`. Pass the option with `=`: as a bare argument pytest treats
the path as a positional test path and mis-detects the rootdir.

What each target is worth keeping for:

| App | Why it earns its place |
|-----|------------------------|
| Designer | Modal startup dialog over a live main window; namespaced classes (`qdesigner_internal::NewFormWidget`); four named toolbars; the largest tree |
| Assistant | Four named `QDockWidget`s, a real `QLineEdit` to type into, nested-scope searches |
| Linguist | objectNames containing spaces and slashes (`comment/context view`), which only resolve through the quoted attribute form |
| qdbusviewer | A main window with an **empty title**, a `QTabWidget`, and an application sitting in an error state (no session bus on Windows) |
| qmleasing | The only QML coverage: a QWidget shell plus a separate Qt Quick top-level. Also the only `QSpinBox` |

## Commands

```bash
pip install -e ".[dev]"        # re-run after renaming or moving anything under src/

# What CI runs, verbatim:
ruff check src tests           # note: src tests, not agent/ (that is C++)
pytest tests/ -q               # unit tests, no Qt needed
pytest integration/ -q --liberaqt-qt-bin="C:/Qt/6.7.3/mingw_64/bin"   # real Qt applications

pytest tests/test_selectors.py::test_parse_type_with_nth -v   # single test
ruff format src tests
mypy src/liberaqt
```

Integration tests find the agent via `LIBERAQT_AGENT_PATH` (a directory of `<tag>/` install
prefixes, which shadows the download cache), else the cache: `%LOCALAPPDATA%\liberaqt\agents\`
on Windows, `$XDG_CACHE_HOME/liberaqt/agents/` elsewhere. Tag format is
`qt<minor>-<platform>-<arch>-<compiler>`, e.g. `qt6.7-linux-x86_64-gcc`,
`qt6.7-windows-x86_64-mingw`. Install layout must be `<prefix>/plugins/generic/liberaqt.{dll,so}`
— `QT_PLUGIN_PATH` is pointed at `<prefix>/plugins`.

### Building the agent

```bash
cmake -S agent -B build/agent -DCMAKE_PREFIX_PATH=$QTDIR -DCMAKE_BUILD_TYPE=Release
cmake --build build/agent --parallel
cmake --install build/agent --prefix ~/.cache/liberaqt/agents/qt6.7-linux-x86_64-gcc
```

Windows / MinGW (the dev machine here — Qt 6.7.3 MinGW, no MSVC), PowerShell:

```powershell
$env:PATH = "C:\Qt\Tools\mingw1120_64\bin;C:\Qt\6.7.3\mingw_64\bin;$env:PATH"
cmake -S agent -B build/agent -G Ninja -DCMAKE_PREFIX_PATH=C:/Qt/6.7.3/mingw_64 `
      -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_COMPILER=C:/Qt/Tools/mingw1120_64/bin/g++.exe
cmake --build build/agent --parallel
cmake --install build/agent --prefix "$env:LOCALAPPDATA\liberaqt\agents\qt6.7-windows-x86_64-mingw"
```

Always `-G Ninja`: the "MinGW Makefiles" generator chokes on drive-letter colons. Running the
integration tests needs `C:\Qt\6.7.3\mingw_64\bin` on `PATH`.

## Python client (`src/liberaqt/`)

| Module | Purpose |
|--------|---------|
| `__init__.py` | `LiberaQt` / `liberaqt()` entry point: `launch()`, `connect()`, timeout scope, cleanup |
| `session.py` | `Session` — the one object that talks to the wire; also `ObjectMap` (YAML → dotted names) |
| `transport.py` | Framed JSON over TCP, request/response correlation, event demux, timeouts |
| `protocol.py` | `Cmd` / `Event` name tables, `decode_value`, `OpaqueValue` |
| `errors.py` | Exception hierarchy, agent-code → class map, the `retryable` flag |
| `launcher.py` | Spawn AUT with injection env, port-file handshake, output capture, cleanup |
| `agent_registry.py` | Resolve an agent binary by Qt version / platform / compiler ABI |
| `application.py` / `window.py` | Windows, screenshots, geometry, activate, close, event callbacks |
| `locator.py` | Lazy resolution, chaining, actions, auto-wait retry (the biggest module, ~660 lines) |
| `selectors.py` | Parse the selector mini-language into a JSON query |
| `expect.py` | Retrying assertions with human-friendly failure text |
| `waits.py` | `retry()` loop and `TimeoutPolicy` |
| `mouse.py` / `keyboard.py` | Low-level input for interactions locators do not cover |
| `spy.py` / `codegen.py` | Event recording → generated pytest file |
| `suggest.py` | Rank candidate selectors per object; uniqueness answered by the agent, not guessed |
| `cli.py` | `doctor`, `agents list|install|remove`, `inspect`, `record`, `run` |
| `pytest_plugin.py` | Fixtures, CLI options, `liberaqt.toml`, failure diagnostics |

Design rules: synchronous API only; locators do no I/O until action time; **no Qt dependency in
the client** (that is what keeps `pytest tests/` runnable with no Qt at all).

## C++ agent (`agent/src/`)

| Unit | Purpose |
|------|---------|
| `agent_plugin` | Generic-plugin entry point; stays dormant unless `LIBERAQT_TOKEN` is set |
| `agent` | Reads the env vars, starts the server, writes the port file |
| `server` | `QTcpServer` on 127.0.0.1, NDJSON, token auth, single client |
| `dispatcher` | Command table, param validation, GUI-thread dispatch, error mapping |
| `object_registry` | Stable string handles for `QObject*`, invalidated on `destroyed()` |
| `value_codec` | QVariant ⇄ JSON, tagged encoding for enums and opaque types |
| `meta_invoke` | Calls slots / `Q_INVOKABLE` by name; diagnoses methods moc cannot reach |
| `selector_engine` | Tree walk, predicate evaluation, deterministic ordering |
| `widget_backend` | QWidget geometry, actionability, item views, menus, model data |
| `quick_backend` | QML item tree, attached properties, JS eval |
| `input_synth` | Mouse/key/touch/wheel, drag-drop, IME text |
| `screenshot` | `QWidget::grab`, `QQuickWindow::grabWindow`, PNG encode |
| `recorder` | Global event filter feeding codegen |
| `idle_tracker` | "UI settled" heuristic: queue drained, no animations/timers |

Guidelines: anything that can live in Python does; never throw across the Qt event loop boundary
(catch, convert to a protocol error, return); guard Qt 5/6 differences in `compat.h`, never inline
`#if QT_VERSION` in feature code.

**Adding a command:** handler in `dispatcher.cpp` (runs on the GUI thread) → name in
`protocol.Cmd` → client method → `docs/PROTOCOL.md` entry → test in `integration/` against
whichever Qt application actually exercises it → conformance suite entry.

**A command that lets the application run must be asynchronous.** Use `registerAsyncCommand` and
resolve from the event loop. Blocking inside a handler — in particular pumping it with
`QCoreApplication::processEvents` — strands the reply the moment the application enters a nested
loop of its own, and a modal dialog during startup is enough to do that. `sync.wait_idle` is the
worked example; `sync.wait_signal` will need the same treatment.

## Injection

The launcher sets, on a copy of the environment (existing values are prepended to, not replaced):

```
QT_PLUGIN_PATH=<agent prefix>/plugins
QT_QPA_GENERIC_PLUGINS=liberaqt
LIBERAQT_TOKEN=<per-launch random token>
LIBERAQT_PORT=0                 # 0 => bind an ephemeral port
LIBERAQT_PORT_FILE=<temp path>  # agent writes the real port here; the launcher polls it
LIBERAQT_RECORD=1               # only in recorder mode
```

The agent is a Qt plugin, so it must match the AUT's Qt **minor** version (6.7, not 6.8) and its
compiler/stdlib ABI. Fallbacks (LD_PRELOAD, Windows DLL injection, opt-in embedding) are in
`docs/INJECTION.md`.

`liberaqt doctor <exe>` is the first thing to run against any new target. It classifies the binary
via `agent_registry.inspect_binary()` into three outcomes, and the distinction matters:

* **dynamic Qt** → injectable; it then reports whether a matching agent exists, and names the
  toolchain when one does not (an agent for the right Qt but the wrong compiler will not load)
* **static Qt** → *never* injectable by any mode, because there is no plugin loader and a second
  Qt copy in one process is undefined behaviour. Only the embedded build (INJECTION.md §4) works.
* **no Qt** → not a Qt application

Linkage, version and compiler all come from the `QLibraryInfo::build()` stamp Qt writes into
whichever binary carries QtCore, so none of it is guessed. The PE import parser and the stamp
parser both take bytes rather than paths, which is what lets `tests/test_agent_registry.py` cover
them with no Qt installed.

The agent is an RCE surface by design: loopback only, token required, one client, dormant without
the token. It must never ship in a production build.

## Input model

Input is delivered through `QWindowSystemInterface`, the seam a platform plugin pushes real input
through, so Qt routes it exactly as it routes a user's: hit-testing, hover, the implicit grab
between press and release, double-click derivation, popup dismissal, focus-on-click, and modal
blocking. Widgets see `spontaneous()` events. `session.set_options({"input_mode": "synthetic"})`
switches back to `QApplication::sendEvent` aimed at one widget, which skips all of that but still
reaches a target a user could not — off-screen, covered, or in a tabified dock parked at negative
coordinates while its tab is not current.

Three consequences that are easy to rediscover the hard way:

* **A press activates an inactive window first.** Without it the application stays in the
  background however hard the test clicks, `QApplication::activeWindow()` stays null, and
  window-context shortcuts — nearly all of them — match nothing.
* **`QApplication::focusWidget()` is null whenever the application is not the foreground one**,
  which is normal for an application under test and guaranteed when several are running. So keys
  are aimed at a *window* (Qt hands them to its focus object), and tests must assert on where
  typing lands rather than on `hasFocus()`.
* **Every `input.*` command is asynchronous**, because it queues rather than delivers. Replying
  before the queue drains lets the next command race the click.

**Selecting is clicking.** `select_tab` clicks the tab rect; `select_item` clicks the row;
`select_option` clicks a combo open and clicks the entry in its popup; `menu().trigger()` walks
the path by clicking each menu open (`menu_walker.cpp` — staged, async, resolves when the final
click is posted). `spin()` clicks a spin box's arrows via `QStyle::subControlRect`. All take
`mode="synthetic"` to fall back to writing the state, which is also the escape hatch for targets
a user cannot reach (a tab scrolled off the bar, an entry in an overlong menu). Every input-ish
client action takes a per-call `mode=`; the session default comes from `set_input_mode` /
`--liberaqt-input-mode`. `Locator.type` accepts Squish-style embedded chords: `"abc<Ctrl+A>xyz"`.

`input.set_text` is the deliberate exception: it writes the property, for cheap setup. It refuses
a read-only widget, because succeeding where a user could not type is a false pass.

## Auto-wait model

Every action: resolve the selector to exactly one object (retry at 50 ms) → check actionability
(exists → visible → enabled → not obscured → has geometry) → act → wait for idle (queue drained,
animations done, extra event-loop turn). Failures raise structured exceptions carrying near-miss
suggestions.

`retry()` retries any `LiberaQtError` whose class sets `retryable = True` and re-raises as
`LiberaQtTimeoutError` with the real error on `__cause__`. So a not-found or ambiguous locator
surfaces as a *timeout*, and tests must assert on `exc.value.__cause__`. `timeout=0` gives exactly
one attempt.

## pytest plugin

Auto-loaded via the `pytest11` entry point once the package is installed. **Do not add
`pytest_plugins = ["liberaqt.pytest_plugin"]` to a conftest** — that registers the module twice
and pytest aborts. Only declare it when running against a source checkout that is not
pip-installed.

Note that `integration/` does **not** use these fixtures: it needs several different applications
in one session, while `app` is built around a single configured `executable`. It defines its own
per-application fixtures on top of `LiberaQt` directly.

Fixtures: `app` (per-test Application), `app_session` (session-scoped), `win`, `liberaqt`,
`liberaqt_config`. Options: `--liberaqt-exe`, `--liberaqt-qt`, `--liberaqt-headless`,
`--liberaqt-slowmo`, `--liberaqt-timeout`, `--liberaqt-trace`, `--liberaqt-input-mode`. Config comes from
`<rootdir>/liberaqt.toml` under `[liberaqt]`. On failure the plugin writes a screenshot and the
last protocol messages to `liberaqt-trace/`.

## Selectors

```python
win.locator("QPushButton#okButton")                          # type + objectName
win.locator("QDialog#settings > QPushButton[text='Apply']")  # hierarchy
win.locator("QTableView QLineEdit:visible:nth(1)")           # pseudo-selectors
win.locator("*[accessibleName^='Volume']:enabled")
win.locator("QListView:has(QLabel[text='Inbox'])")
win.locator("qdesigner_internal::NewFormWidget")             # namespaced C++ class
win.locator("FormWidget[objectName='comment/context view']") # objectName that is not an identifier
win.obj("login.submit")                                      # via an object map
```

Grammar in `docs/SELECTORS.md`. Four rules that trip people up:

* **Type matching walks the inheritance chain** (`matchesType` follows `superClass()`), so
  `QWidget` matches every widget and `MyButton : QPushButton` is matched by `QPushButton`.
  Uniqueness therefore cannot be computed from an `object.tree` dump — always ask the engine via
  `object.find`. `suggest.py` is built around this and takes a `resolve` callable for testability.
* **A window is never inside its own subtree**: a search rooted at a window handle excludes the
  window, so the tree root always reports zero matches for it. Reach windows via
  `app.window(title=...)`.
* **A type name may be namespaced.** `QMetaObject::className()` reports the qualified name, so
  real applications need `ns::Class` to lex. A single `:` still starts a pseudo-class; only `::`
  followed by an identifier continues the type name (`_IDENT` in `selectors.py`).
* **An objectName is not necessarily an identifier.** Applications ship names with spaces and
  slashes, which cannot follow a `#`. `suggest.py` falls back to `[objectName='...']`, and so
  should anything else that builds a selector from a live object.

## Coding style

* Python: ruff, line length 100, `E/F/I/UP/B/D` selected, `D105`/`D107` ignored. Google docstrings
  on every public class, method and function; constructor args documented under `Args:` on the
  class. Tests and examples are exempt from the missing-docstring rules.
* Indented code samples in docstrings need a Google section header (`Example:` + `::`), or D208
  strips the indentation and silently corrupts them.
* Every module carries `from __future__ import annotations`, so pyupgrade fires despite the py39
  target and `X | None` is fine **in annotations only**. Runtime positions — type aliases like
  `SelectorLike`, `isinstance`, `cast` — must keep `Union`, or the 3.9 CI leg breaks.
* A fixture *error* escapes an `xfail` marker; only call-phase failures are covered. Fixtures for
  optional or unbuilt artifacts must `pytest.skip`.
* C++: Qt conventions; Qt 5/6 differences behind `compat.h`.
* Commits in this repo do not carry a `Co-Authored-By` trailer.

## Documentation map

| Document | When to read |
|----------|--------------|
| `docs/ARCHITECTURE.md` | How the pieces fit together and why |
| `docs/PROTOCOL.md` | Adding commands, wire format |
| `docs/SELECTORS.md` | Selector grammar, matching rules, object maps |
| `docs/API.md` | Python API design philosophy |
| `docs/INJECTION.md` | Injection trade-offs and fallbacks |
| `docs/ROADMAP.md` | Milestones, sizing, risk register |
| `docs/OPEN_QUESTIONS.md` | Unresolved design decisions |
| `docs/CONTRIBUTING.md` | Repo layout and house rules |
