# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is LiberaQT?

A modern UI automation library for Qt desktop applications, scoped narrowly: Python as the
only test language, Qt 5.15 / 6.5 / 6.7, Windows and Linux,
QWidget and QML. A C++ agent (Qt generic plugin) is injected into the application under test and
exposes the live `QObject` tree over JSON/TCP to a pure-Python client.

```
pytest/script (Python) ──JSON/TCP──► liberaqt agent (C++ Qt plugin) ──► QObject tree
```

The GitHub remote is `sargis2000/LiberaQt`; everything inside the repo is `liberaqt`. Naming
convention: `liberaqt` lowercase for the package, C++ namespace, CMake target, plugin key, CLI,
config section and trace dir; `LiberaQT` in prose; `LiberaQt` in Python class names
(`LiberaQtError`); `LIBERAQT_*` for env vars and C++ macros.

## Topic skills — load the one for the area you are changing

This file holds the rules every session needs. The depth an area needs, and the measured bug
behind each of its rules, live in skills under `.claude/skills/`. **Load the skill before changing
its area, or before answering a question about it**: most rules here exist because a change that
looked right silently broke something, and only the skill says how.

| Skill | Load it for |
|---|---|
| `qml-support` | Qt Quick / QML: what works, what fails silently, `quick_backend.cpp`, qmleasing |
| `pytest-internals` | `tests/conftest.py`, `tests/e2e/conftest.py`, `pytest_plugin.py`, options, markers, live modules; live tests collected or skipped unexpectedly |
| `agent-loading` | an agent that never attaches; `launcher.py`, `agent_registry.py`, plugin keys, child processes, a new Qt version, compiler or architecture |
| `agent-release` | building, installing, updating or publishing agent binaries; the Windows build recipes; `agent_build.py`, `agent_install.py`, the CI agent matrix |
| `docs-site` | the `liberaqt docs` command, `docs/` in the wheel, `mkdocs.yml`, pages that run as tests |
| `add-agent-command` | adding or finishing a protocol command, end to end |
| `fix-finding` | fixing any defect, QA finding or bug report |

## Project state — read this before planning work

The Python client is written ahead of the agent. `protocol.Cmd` lists **42** commands;
`agent/src/dispatcher.cpp` registers **36**. An unregistered command
returns `code: "unsupported"`, which maps to `UnsupportedOperationError` (`retryable = False`), so
the client API for it exists and fails cleanly rather than hanging.

**Do not count these by hand — ask the agent.** It reports every command it registered in the
hello banner, so `app.capabilities["commands"]`, `app.supports(Cmd.X)` and `session.commands`
answer this against the binary actually loaded, which is the only answer that is true for a given
build (an agent compiled without Qt Quick registers fewer). The counts above are a snapshot and
will drift; the handshake will not.

Consequences when adding a feature: the client method usually already exists and the work is the
C++ handler. Check `dispatcher.cpp`'s `registerCommand` calls before assuming something is missing
from the client.

Known gaps, in the order they unblock the most client surface:

* `quick.evaluate`, `quick.find_by_id`, `quick.list_view_item`, `quick.wait_animations` — all QML
* `record.start` / `record.stop` — the recorder, and therefore `liberaqt record`. Closer than
  "unregistered" suggests: `spy.py`, `codegen.py`, the CLI command and the agent's `Recorder`
  event filter are all written. What is missing is the wiring and two `TODO(m4)`s inside
  `recorder.cpp`:
  1. register the two commands in `dispatcher.cpp` and emit `record.action` — the `Recorder`
     class has `start`/`stop`/`eventFilter` but nothing ever calls them;
  2. **typing is not captured at all.** Only `MouseButtonRelease` produces an action; the
     `FocusOut` case is empty where it should emit a `fill()` carrying the committed text;
  3. selector uniqueness is not verified before emitting, so a recording can generate a test
     whose selectors do not resolve. Re-run each through the engine, as `suggest.py` does.

  It records *semantic* actions ("clicked this button"), not raw events, and not screen video.
* `session.set_options` honours `input_mode` and nothing else; `idle_poll_ms`, `animation_wait`
  and `network_wait` are accepted and dropped
* actionability *detects* an obscuring widget, a blocking modal or an off-window position and
  names it (native mode only), but does not yet *adjust* the click point to an uncovered part of
  the target

Item views, menus, tabs, the remaining input events, property enumeration and `sync.wait_signal`
are all implemented; a cell is addressed by the composite handle described in `docs/PROTOCOL.md`.

**QML discovery works; QML *input* does not.** Measured on qmleasing 6.7.3 and pinned by
`tests/e2e/qt/test_qml_live.py`; `qml-support` has the detail and where to start on input.

* Locators, `object.tree`, `Window.kind` and rootless searches all reach Quick objects, through
  `SelectorEngine::visualChildren`. Any new tree walk must go through it.
* `click`/`hover`/`wheel`/`drag_to` and `highlight()` at a Quick item raise `unsupported`.
  **`type()`/`press()` at one report success and type into the widget window's focused field
  instead** (measured: a qmleasing spin box went from 500 to 9500), and a screenshot of a Quick
  item silently returns the active window's image. `geometry` is `()`.
* **Quick support is optional at build time** (`LIBERAQT_HAVE_QUICK`), and CI's Qt 5.15 agents
  have none. Check this before debugging any QML failure.
* Object counts in a Quick scene track the window size. Never assert an exact count.

## Two suites: `tests/unit` proves the client, `tests/e2e` proves the agent

Both live under `tests/`, split by **what has to be installed** for a test to do anything:
`tests/unit` needs nothing, `tests/e2e/qt` needs a Qt installation, `tests/e2e/libero` needs
Libero SoC and a 32-bit MSVC agent. That is the axis you point pytest at, so it is the axis the
filesystem encodes. Orthogonal properties are markers instead, because they cut across the
directories: `live`, `qt_app` and `libero` are applied automatically from the directory (so a new
live test cannot forget one), `writes_disk` and `slow` are declared per file.

**CI runs `tests/unit` only** — pure-Python unit tests, no Qt, no agent. A green run there says
the client parses selectors and maps errors correctly and says *nothing* about whether a click
lands. Not one line of `agent/src/` executes. CI additionally builds the agent on ten ABIs, which
is a compile check and nothing more, plus one job asserting the wheel still carries the docs.

**The live suites are opted into by path, and only by path.** `pytest` and `pytest tests/unit`
never collect them; `pytest tests/` collects and deselects all 103, and says which command asks
for each part. A marker is never consent, and neither is an option's value
(`--ignore tests/e2e/qt` once opted a run into all of Libero). `tests/conftest.py` enforces this
and `tests/unit/test_live_gate.py` pins it with real inner sessions. The first version shipped
with five bypasses because nothing tested it, so load `pytest-internals` before touching either.

**`tests/e2e/` drives real applications**, is not run by CI, and is where every agent bug this
project has had was found. It skips cleanly when the application it needs is absent.

| File | Target | What it is for |
|------|--------|----------------|
| `tests/e2e/qt/test_actions_live.py` | Qt Assistant | Text input, chords, window shortcuts, checkboxes, item views, wheel |
| `tests/e2e/qt/test_selectors_live.py` | Qt Assistant | Every selector rule, operator, pseudo-class, strictness, scoped chaining |
| `tests/e2e/qt/test_qml_live.py` | qmleasing | The Quick tree, `Window.kind`, and rootless searches across both trees |
| `tests/e2e/qt/test_docs_examples.py` | Qt Assistant | The getting-started page's code, run as a test so the tutorial cannot rot |
| `tests/e2e/libero/test_libero_smartdesign.py` | Libero SoC | Reading/writing a self-painting widget through its slots |
| `tests/e2e/libero/test_libero_configurator.py` | Libero SoC | Driving an out-of-process dialog through a child agent |
| `tests/e2e/libero/test_libero_smarttime.py` | Libero SoC | Opening SmartTime from the Design Flow and driving it — a **64-bit** child of a 32-bit parent |
| `tests/e2e/libero/test_libero_nlview.py` | Libero SoC | Reading a schematic out of an NLview canvas through `call_native` |
| `tests/e2e/libero/test_libero_selectors.py` | Libero SoC | `:has`, `:parent`, `parent()`, `ancestor()` on a **different ABI** (Qt 5.15, MSVC, 32-bit); read-only |
| `tests/e2e/libero/test_libero_synthesis.py` | Libero SoC | One end-to-end flow: new project → SmartDesign → import HDL → synthesise. Writes to disk, ~75s |

There is deliberately **no purpose-built sample application**, and there should never be one. A toy
agrees with whatever the driver happens to do; three separate bugs that made LiberaQT unusable on
real software sat undetected behind a green sample suite (see git history for `sync.wait_idle`,
namespaced class names, and objectNames that are not bare identifiers). Every one of these suites
drives software written with no knowledge of this project. That keeps paying: `tests/e2e/` has
since caught `:has()` and `:parent()` being parsed by the client and silently dropped by the agent,
chaining off `.first` widening the search back out to every match, two Libero buttons identical in
class, text *and* objectName, and a `QCompleter` popup whose mouse grab swallows the next click.

**Two ABIs matter, not one.** Assistant is Qt 6.7 / MinGW / x86_64 and ships inside the same Qt the
agent is built from. Libero is Qt 5.15 / MSVC / **x86**, from a vendor who has never heard of this
project. A selector engine shipping as a Qt plugin is only proven by passing on both, and the two
have genuinely disagreed — `ancestor("QDockWidget")` returns a plain `QDockWidget` in Assistant and
Libero's namespaced `Aqwidget::AQDockWidget` subclass. **Compare handles, not class names.**

The Qt suites are green against 6.5 and 6.7. Qt 5.15's Assistant is not a drop-in target: its help
viewer is a `QTextBrowser`, and `test_wheel_scrolls_the_help_viewer` fails there through every
target tried — an unexplained gap in 5.15 wheel delivery, deliberately left red rather than
skipped (62 of 63 pass).

An older suite covering Designer, Linguist, qdbusviewer and qmleasing was deleted on 2026-08-17 and
is still in git history at `b51cfa0:integration/`; take anything useful from there rather than
rewriting it.

## Commands

```bash
pip install -e ".[dev]"        # re-run after renaming or moving anything under src/

# What CI runs, verbatim:
ruff check src tests           # note: src tests, not agent/ (that is C++)
pytest tests/unit -q           # unit tests, no Qt needed

pytest tests/unit/test_selectors.py::test_parse_type_with_nth -v   # single test
ruff format src tests

# Not in CI, and not currently clean — a handful of pre-existing errors, mostly missing stubs.
# Advisory only; do not read a red run as something you broke.
mypy src/liberaqt

# The live suites. Not in CI. They skip when the application is missing.
pytest tests/e2e/qt/test_actions_live.py tests/e2e/qt/test_selectors_live.py   # needs Qt's assistant

# Every Qt-based suite resolves its application through conftest's `qt_tool`, so one flag
# points the whole set at a particular Qt -- which is how you verify a new Qt version:
pytest tests/e2e/qt --liberaqt-qt-bin "C:/Qt/6.5.3/mingw_64/bin"   # all four Qt suites
# The choice is exclusive: a path with no Qt in it skips rather than falling back to another
# installation, because silently testing 6.7 while believing you tested 6.5 is worse than a skip.
pytest tests/e2e/libero/test_libero_selectors.py                          # read-only, ~10s
pytest tests/e2e/libero/test_libero_synthesis.py                          # writes a project, ~75s

# Or by category, once inside the live tree. Measured counts:
pytest tests/e2e             # 103   everything live
pytest tests/e2e/qt          #  63   Qt's own tools
pytest tests/e2e/libero      #  40   Libero SoC
pytest tests/e2e -m "libero and not writes_disk"   # 10, the read-only Libero suite
pytest tests/e2e -m "live and not slow"            # 102, all but the synthesis run

liberaqt doctor <exe>          # first thing to run against any new target
liberaqt agents list           # installed agents: tag, revision, provenance
liberaqt agents update --check # which are behind a published release; downloads nothing
```

`pytest` on its own collects `tests/unit` only: pyproject pins `testpaths`. The live suites have
to be asked for **by path** — a marker alone will not reach them, on purpose.

**A broken editable install takes pytest down at startup, not at collection.** The package
registers a `pytest11` entry point, so if the metadata is installed but the module is not
importable, `pytest` dies during config parse with `ModuleNotFoundError: No module named
'liberaqt'` and no test ever runs. It looks nothing like an install problem. Fix with
`pip install -e ".[dev]"`.

The client finds the agent via `LIBERAQT_AGENT_PATH` (a directory of `<tag>/` install
prefixes, which shadows the download cache), else the cache: `%LOCALAPPDATA%\liberaqt\agents\`
on Windows, `$XDG_CACHE_HOME/liberaqt/agents/` elsewhere. Tag format is
`qt<minor>-<platform>-<arch>-<compiler>`, e.g. `qt6.7-linux-x86_64-gcc`,
`qt6.7-windows-x86_64-mingw`. Install layout must be `<prefix>/plugins/generic/liberaqt.{dll,so}`
— `QT_PLUGIN_PATH` is pointed at `<prefix>/plugins`.

### Building the agent

```bash
liberaqt agents kits                 # Qt kits on this machine an agent can be built from
liberaqt agents build --tag <tag>    # configure, build, install, and check the plugin key

# By hand (Linux):
cmake -S agent -B build/agent -DCMAKE_PREFIX_PATH=$QTDIR -DCMAKE_BUILD_TYPE=Release
cmake --build build/agent --parallel
cmake --install build/agent --prefix ~/.cache/liberaqt/agents/qt6.7-linux-x86_64-gcc
```

Use a separate build directory per ABI, and always `-G Ninja` on Windows (the "MinGW Makefiles"
generator chokes on drive-letter colons). The Windows recipes for this machine — MinGW for Qt 6.x,
and MSVC 32-bit for Libero's Qt 5.15, which needs `vcvarsall x86` from `cmd` — are in
`agent-release`. An installed agent is a binary in the cache, so a checkout never updates it:
after changing `agent/`, rebuild every ABI you will verify against. Driving an application does
not need Qt on `PATH`; it loads its own DLLs from its own directory.

## Python client (`src/liberaqt/`)

| Module | Purpose |
|--------|---------|
| `__init__.py` | `LiberaQt` / `liberaqt()` entry point: `launch()`, `connect()`, timeout scope, cleanup |
| `session.py` | `Session` — the one object that talks to the wire; also `ObjectMap` (YAML → dotted names) |
| `transport.py` | Framed JSON over TCP, request/response correlation, event demux, timeouts |
| `protocol.py` | `Cmd` / `Event` name tables, `decode_value`, `OpaqueValue` |
| `errors.py` | Exception hierarchy, agent-code → class map, the `retryable` flag |
| `launcher.py` | Spawn AUT with injection env, port-file handshake, output capture, cleanup |
| `agent_registry.py` | Resolve an agent binary by Qt version / platform / compiler ABI; derive its plugin key |
| `agent_build.py` | Discover installed Qt kits and build an agent from one (`liberaqt agents kits` / `build`) |
| `agent_install.py` | Fetch, verify and unpack an agent archive (`liberaqt agents install`) |
| `nlview.py` | Drive an embedded NLview schematic canvas through its own command language |
| `application.py` / `window.py` | Windows, screenshots, geometry, activate, close, event callbacks |
| `locator.py` | Lazy resolution, chaining, actions, auto-wait retry (by far the biggest module) |
| `selectors.py` | Parse the selector mini-language into a JSON query |
| `expect.py` | Retrying assertions with human-friendly failure text |
| `waits.py` | `retry()` loop and `TimeoutPolicy` |
| `mouse.py` / `keyboard.py` | Low-level input for interactions locators do not cover |
| `spy.py` / `codegen.py` | Event recording → generated pytest file |
| `suggest.py` | Rank candidate selectors per object; uniqueness answered by the agent, not guessed |
| `cli.py` | `doctor`, `agents list\|install\|update\|kits\|build\|remove`, `inspect`, `record`, `docs`, `run` |
| `pytest_plugin.py` | Fixtures, CLI options, `liberaqt.toml`, failure diagnostics |

Design rules: synchronous API only; locators do no I/O until action time; **no Qt dependency in
the client** (that is what keeps `pytest tests/unit` runnable with no Qt at all).

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
| `menu_walker` | Staged, async walk of a menu path — one click per level |
| `signal_waiter` | One-shot runtime-named signal connection behind `sync.wait_signal` |
| `screenshot` | `QWidget::grab`, `QQuickWindow::grabWindow`, PNG encode |
| `recorder` | Global event filter feeding codegen |
| `idle_tracker` | "UI settled" heuristic: queue drained, no animations/timers |
| `compat.h` | **Every** `#if QT_VERSION` in the agent. Header only, no `.cpp` |

Guidelines: anything that can live in Python does; never throw across the Qt event loop boundary
(catch, convert to a protocol error, return); guard Qt 5/6 differences in `compat.h`, never inline
`#if QT_VERSION` in feature code.

Three compile-time constraints the build imposes, each of which turns ordinary-looking C++ into an
error:

* **`QT_NO_CAST_FROM_ASCII` is on.** A bare `"string"` will not convert to a `QString`, so every
  literal needs `QStringLiteral(...)` or `QLatin1String(...)` — which is why `dispatcher.cpp`
  registers command names as `QStringLiteral`, and why a grep for `registerCommand("` finds
  nothing.
* **Warnings are turned up** (`-Wall -Wextra -Wpedantic -fvisibility=hidden`, `/W4 /permissive-`),
  not to errors — but a new warning in a file that had none is worth reading.
* **The agent links `Qt::GuiPrivate`** for `QWindowSystemInterface`. A Qt installation without
  private headers cannot build it, and the private API moves within a major release
  (`handleFocusWindowChanged` was renamed in 6.6; `agent-loading` has the story).

`agent/CMakeLists.txt` compiles a fixed source list, so **a new `.cpp` has to be added to
`LIBERAQT_SOURCES`** — there is no glob.

Four rules for agent C++, each of which closed a bug that produced a wrong answer, not an error:

* **A dead root must never become `nullptr`**, because `nullptr` as a search root means "the whole
  application". `ObjectRegistry::resolveOrNull` answers `nullptr` both for "no handle given" and
  "object gone", so use `resolveRoot()` for anything that takes an optional root: empty stays
  `nullptr`, dead throws `Stale`. Reusing a `Window` after its dialog closed used to search the
  main window instead, resolving real foreign objects and offering their near misses.
* **Two commands own preconditions the generic actionability probe gets wrong.** The probe checks
  visibility even in synthetic mode, which is wrong for `input.set_text` (filling a field on a page
  not currently shown): `fill()` passes `actionable=False` and `InputSynth::setText` enforces only
  not-read-only and not-disabled. And `keyTarget` refuses a non-window widget that cannot take
  focus, because native keys go to the window — typing at a `QStatusBar` used to edit an unrelated
  field and report success.
* **`headerData`'s default is the 1-based section number as an int**, never empty. Detect "no
  header" by that int type, not `isEmpty()`, or `to_records()` keys become `"1", "2"` while
  `cell(row, column)` stays 0-based.
* **A widget that paints its own contents has nothing in the object tree** (Libero's SmartDesign
  canvas, wrapping NLview). The meta-object is the only way in: `object.list_methods` to discover
  the slots, `object.invoke` to call them — including `std::string` parameters, which the agent
  materialises itself, legitimately only because the plugin already matches the application's
  compiler and stdlib. See `tests/e2e/libero/test_libero_smartdesign.py`.

**Adding a command** follows `add-agent-command`: handler in `dispatcher.cpp` (runs on the GUI
thread) → name in `protocol.Cmd` → client method → `docs/PROTOCOL.md` entry → a test in
`tests/e2e/` that drives it against a real application → `plugins/liberaqt/` and its version, if
users can now do something new. Nothing in the repository will exercise the handler, so drive it
against a real application by hand before believing it works. Pick the registration helper by what
the handler does — `registerCommand` answers immediately, `registerAsyncCommand` lets the event
loop turn, `registerInputCommand` (every `input.*`) only queues events and replies once they drain
— and never block or pump `QCoreApplication::processEvents` inside a handler: the reply is
stranded the moment the application enters a nested loop of its own. Older text in
`docs/CONTRIBUTING.md` asks for a "conformance suite entry" and "a test against the sample app":
neither exists, and neither should be looked for.

## Injection

The launcher sets, on a copy of the environment (existing values are prepended to, not replaced):

```
QT_PLUGIN_PATH=<agent prefix>/plugins       # then every other installed agent's plugins dir
QT_QPA_GENERIC_PLUGINS=<one key per installed agent>,liberaqt   # e.g. liberaqt_5_15_32_msvc,liberaqt_6_7_64_gnu,liberaqt
LIBERAQT_TOKEN=<per-launch random token>
LIBERAQT_PORT=0                 # 0 => bind an ephemeral port
LIBERAQT_PORT_DIR=<temp dir>    # each agent writes <pid>.port here; the launcher polls the dir
LIBERAQT_RECORD=1               # only in recorder mode
```

Getting injection wrong fails silently — no agent, no port file, no error — so these rules are
enforced in code rather than left to the user. `agent-loading` has why each exists and how to
diagnose an agent that never attaches.

* **An agent must match the application's Qt minor version, pointer size and compiler family**
  (MSVC 2015–2022 are one family, MinGW another). `resolve()` reads all three from the binary's Qt
  build stamp, so none of it is guessed.
* **The version fallback only looks downwards**: an agent built against 6.5 loads into 6.5–6.7,
  but one built against 6.7 is refused by a 6.5 host.
* **The plugin key carries all three** (`liberaqt_5_15_32_msvc`, `liberaqt_6_7_64_gnu`), because Qt
  binds a key to the first library on the path that claims it. `agent/CMakeLists.txt` generates it
  into `liberaqt_plugin.json` and `plugin_key_for()` derives it from a tag; **the two must stay in
  step**. After changing either, `liberaqt doctor` must not report `STALE`.
* **Every Qt child the application spawns loads an agent too.** That is how out-of-process windows
  are reached (`app.child_agents`, `app.attach_child()`); port files are keyed by pid, each child
  runs its own server, and a child may outlive the parent.
* **A statically linked Qt is never injectable, and neither is a process that never constructs a
  `QGuiApplication`** — `liberaqt doctor` sees the first, not the second.

Exercised: Qt 5.15, 6.5.3 and 6.7.3; 6.6 is untested. Generic-plugin injection is the only mode
that works: the fallbacks in `docs/INJECTION.md` are not implemented.

**Distributing agents.** Each installed agent carries a `liberaqt-agent.json` manifest (revision,
Qt, plugin key, compiler, bitness, protocol). The revision is fixed at **configure** time, so a
bare `cmake --build` after new commits keeps the old one; `liberaqt agents build` reconfigures
every time. A fix reaches other machines by release: pushing a `v*` tag runs CI's `publish` job,
and everyone's `liberaqt agents update` picks it up. `agent-release` has the update states, the
staged install, and the two ABIs CI builds but never ships.

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
client action takes a per-call `mode=` — including `Window.mouse.*` and `Window.keyboard.*`, which
once silently followed the session default and no longer do. The session default comes from
`set_input_mode` / `--liberaqt-input-mode`. `Locator.type` **and** `Keyboard.type` accept
embedded key chords (`"abc<Ctrl+A>xyz"`) through one shared splitter in `keyboard.py`, so
the same string means the same thing whichever is handed it.

`input.set_text` is the deliberate exception: it writes the property, for cheap setup. It refuses
a read-only widget, because succeeding where a user could not type is a false pass.

## Auto-wait model

Every action: resolve the selector to exactly one object (retry at 50 ms) → check actionability
(exists → visible → enabled → not obscured → has geometry) → act → wait for idle (queue drained,
extra event-loop turn). Failures raise structured exceptions carrying near-miss suggestions.
**Idle does not include animations**, whatever `docs/` says: `IdleTracker::hasRunningAnimations()`
is a `TODO(m2)` that returns `false`, and `hasPendingNetwork()` a `TODO(m4)` that does the same.

`retry()` retries any `LiberaQtError` whose class sets `retryable = True` and re-raises as
`LiberaQtTimeoutError` (exported from the package root; the class in `errors.py` is named
`TimeoutError`) with the real error on `__cause__`. So a not-found or ambiguous locator surfaces
as a *timeout*. **The chain depth depends on the call**, measured: an action nests the action's
retry around the resolution's, so `loc.click()` gives `TimeoutError -> TimeoutError ->
ObjectNotFoundError` while `loc.resolve()` gives `TimeoutError -> ObjectNotFoundError`. Walk to
the root cause rather than asserting on `exc.value.__cause__`, which is right for only one of
them. `timeout=0` gives exactly one attempt.

## pytest plugin

Auto-loaded via the `pytest11` entry point once the package is installed. **Do not add
`pytest_plugins = ["liberaqt.pytest_plugin"]` to a conftest** — that registers the module twice
and pytest aborts. Only declare it when running against a source checkout that is not
pip-installed.

These fixtures are built around a *single* configured `executable`. A suite that needs several
different applications in one session has to build its own on top of `LiberaQt` directly — which
is what the deleted `integration/` suite did, and the reason it used none of these.

Fixtures: `app` (per-test Application), `app_session` (session-scoped), `win`, `liberaqt`,
`liberaqt_config`. Options: `--liberaqt-exe`, `--liberaqt-qt`, `--liberaqt-headless`,
`--liberaqt-slowmo`, `--liberaqt-timeout`, `--liberaqt-trace`, `--liberaqt-input-mode`. Config
comes from `<rootdir>/liberaqt.toml` under `[liberaqt]`. On failure the plugin writes a screenshot
and the last protocol messages to `liberaqt-trace/`.

An option the toml can also set has `default=None` (a default cannot lose to the config file);
diagnostics are written from `pytest_runtest_makereport`, not a fixture teardown; and a config that
exists and is ignored always warns. `pytest-internals` has each rule and the bug it closed;
`tests/unit/test_pytest_plugin.py` pins them with real inner sessions through `pytester`.

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

**Going upwards.** Every selector searches downwards; `Locator.parent()` and
`Locator.ancestor(selector)` are the only way up, both backed by `object.ancestor`. `parent()` is
one level — usually a layout or viewport rather than anything you would point at — and
`ancestor("QDockWidget")` climbs until something matches, which is normally what is wanted. Both
resolve immediately rather than lazily, since the answer is one specific object.

Do not confuse them with `:has(X)` and `:parent(X)`, which are **filters**: both return the object
on the *left*. `QPushButton:parent(QDockWidget)` is a button, not a dock.

Two things worth knowing before asserting on any of this:

* **Ancestor matching walks the inheritance chain too**, so the result's `class_name` may be a
  subclass — `ancestor("QDockWidget")` returns `Aqwidget::AQDockWidget` in Libero. Compare
  handles, not class names.
* **Chaining off a narrowed locator scopes to it.** `win.locator("X").first.locator("Y")` means
  "Y inside that one X", and `.all()` elements behave the same way. This was once not true, and
  the silent widening it caused is covered by `tests/unit/test_locator_scoping.py`.

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

## Claude Code: two audiences, two directories

There are two sets of Claude material, for two different readers, and they must not be confused:

* **`plugins/liberaqt/`** ships to **LiberaQT users**, whose Claude writes tests for *their*
  application. It is a Claude Code plugin, listed by `.claude-plugin/marketplace.json` at the
  repo root, so a user runs `/plugin marketplace add sargis2000/LiberaQt` and
  `/plugin install liberaqt@liberaqt`. It must never mention this repository's internals.
* **`.claude/`** is for **LiberaQT developers**, working in this checkout. It may assume the
  checkout, the Qt installations on this machine and the agent cache.

### The user plugin (`plugins/liberaqt/`)

| Thing | What it is for |
|-------|----------------|
| `skills/write-tests/` | The loop (doctor → inspect → write → run → read the failure → prove it can fail), selector choice, fixtures built on the `liberaqt` fixture, and the reliability rules. `references/api.md` is every public call taken from real signatures; `references/limitations.md` is the measured 0.1.x behaviour, each entry with a "Fixed yet?" check |
| `skills/troubleshoot/` | Agent never attaches (part A), test fails (part B), ordered by likelihood |
| `agents/explorer.md` | The user-side twin of `live-probe`: launches *their* application and returns verified selectors, including for dialogs `inspect` cannot reach |
| `agents/test-author.md` | Scenario in, tests out, following the project's own conventions, and proving each test can fail |

Both agents preload both skills through `skills:` in their frontmatter. Inside a plugin a skill
registers under its scoped name, so the list says `liberaqt:write-tests`, not `write-tests`.
Measured: with the scoped names, the agent starts with both skills' full text in context.

**Keeping it true.** `tests/unit/test_claude_plugin.py` parses every Python block and every
inline code span in `plugins/` and `.claude/`, infers the receiver type (`app.`, `win.`, `loc.`,
`expect(...)`, or a variable assigned from one), and checks each attribute against the real class.
It also resolves every `from liberaqt... import`, checks manifests, frontmatter and relative
links, and checks that what the material calls absent (`win.app`, `expect(win).not_`) is still
absent. So a rename fails CI. It cannot tell whether *advice* is still right; when a fix changes
behaviour, delete the matching `limitations.md` entry yourself. Then **bump `version` in
`plugins/liberaqt/.claude-plugin/plugin.json`**: an installed plugin is pinned to that version,
so without a bump no user receives the change. The test holds its major.minor to the library's.

`claude plugin validate --strict .` and `claude plugin validate --strict plugins/liberaqt` check
the manifests (not agent frontmatter, which is what the unit test is for).
`claude --plugin-dir plugins/liberaqt plugin details liberaqt` shows the inventory and token cost
(about 550 tokens always-on). To use the plugin while working here, run
`claude --plugin-dir plugins/liberaqt`.

### The developer configuration (`.claude/`)

**Each fact lives in one place.** Rules every session needs are in this file; an area's depth and
the history behind its rules are in its topic skill; procedures are in the procedure skills. When
a skill and this file disagree, one of them is stale — fix it rather than picking one. Topic
skills carry no `paths:` restriction on purpose: a conditional skill is hidden until a matching
file is touched, and most of them answer questions ("why doesn't the agent attach?") asked before
any file is.

| Thing | What it is for |
|-------|----------------|
| `agents/qt-automation-tester.md` | A QA engineer who learns LiberaQT **from the docs only**, then reads the user plugin as a second documentation surface, writes and runs real scenarios against real Qt applications, and reports what the developer experience was actually like. Carries the measured open-defect list and the fixed list to treat as regressions |
| `agents/live-probe.md` | Answers one question against a running application without the object tree landing in the caller's context -- an Assistant tree dump is ~16 KB, `liberaqt inspect` another ~11 KB |
| `skills/add-agent-command/` | The end-to-end command procedure, with how far each of the six unregistered commands really is from done (`quick.evaluate` is one `registerCommand` line away) |
| `skills/fix-finding/` | The repo's own fix loop: measure live, pin with a test seen failing on the old code, fix, rebuild per ABI, re-measure, sweep for the same bug class, update every description of the old behaviour including the user plugin |
| `skills/qml-support/` | The measured Quick state, the three bugs behind the old "QML is deferred" impression, where to start on Quick input, testing against qmleasing |
| `skills/pytest-internals/` | The live-suite gate's rules and the bypass each closed; the pytest plugin's precedence, diagnostics, trace names and redaction |
| `skills/agent-loading/` | Diagnosing an agent that never attaches, `resolve()`'s matching rules, per-ABI plugin keys, child agents, `doctor`'s classes |
| `skills/agent-release/` | Per-ABI build recipes, the manifest, every `agents update` state, staged installs, the CI matrix and publishing |
| `skills/docs-site/` | `liberaqt docs`, the docs in the wheel, the site-dir ownership rules, pages that run as tests |
| `settings.json` | Permission allowlist for the read-only and verification commands a session runs constantly. `liberaqt agents build` is **not** allowed (it writes the agent cache) and the Libero suites are `ask` (they write projects and take minutes) |

The docs-only rule in `qt-automation-tester` is the point of it: a reviewer who has read
`src/liberaqt/` can no longer tell you whether the documentation teaches the thing. It has
already found, among others, that `filter(has_not=...)` is a silent no-op, that the selector
lexer strips backslashes so every regex character class breaks, and that QML input is partly
supported rather than uniformly unsupported as the docs claim.

**No MCP server.** Considered and rejected: this is an offline repo whose only "external
service" is a loopback socket the launcher itself opened. A filesystem or git server duplicates
the built-in tools, a GitHub server has nothing to do, and wrapping the liberaqt protocol itself
would re-expose `Session.call` as a tool surface -- which is what `live-probe` already is, at the
right altitude.

## Documentation map

| Document | When to read |
|----------|--------------|
| `docs/ARCHITECTURE.md` | How the pieces fit together and why |
| `docs/ACTIONS.md` | Every action method: native vs synthetic, covered vs not, per-mode limits |
| `docs/PROTOCOL.md` | Adding commands, wire format |
| `docs/SELECTORS.md` | Selector grammar, matching rules, object maps |
| `docs/API.md` | Python API design philosophy |
| `docs/INJECTION.md` | Injection trade-offs and fallbacks |
| `docs/ROADMAP.md` | Milestones, sizing, risk register |
| `docs/OPEN_QUESTIONS.md` | Unresolved design decisions |
| `docs/CONTRIBUTING.md` | Repo layout and house rules |
| `docs/troubleshooting.md` | Symptom → cause, for the failures that look like nothing |

`docs/` is also the mkdocs source (`mkdocs.yml`): `index.md`, `getting-started/`, `guide/` and
`reference/` are the user-facing half, and the table above is the reference half. **Both ship
inside the wheel** (`force-include` puts them at `liberaqt/_docs/`), so anything written into
`docs/` reaches users. `tests/e2e/qt/test_docs_examples.py` runs the code in
`docs/getting-started/first-test.md` as a test, and `mkdocstrings` renders `reference/api.md` from
the docstrings in `src/` — the other reason the docstring rules above are enforced. A docs build
erases its destination, so load `docs-site` before touching the `liberaqt docs` command, the
`force-include` or `mkdocs.yml`.
