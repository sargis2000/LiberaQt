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

The GitHub remote is `sargis2000/LiberaQt`; everything inside the repo is `liberaqt`. Naming convention: `liberaqt` lowercase for the package, C++ namespace, CMake target,
plugin key, CLI, config section and trace dir; `LiberaQT` in prose; `LiberaQt` in Python class
names (`LiberaQtError`); `LIBERAQT_*` for env vars and C++ macros.

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

**QML discovery works; QML *input* does not.** Everything below is measured against `qmleasing`,
a real Qt application that puts a QWidget shell and a live Quick window in one process, and is
pinned by `e2e/test_qml_live.py`.

* **Locators reach QML objects, and so does `object.tree`.** `QQuickRectangle`, `QQuickText` and
  QML-defined types (`Button_QMLTYPE_0`) all resolve and their properties read back. Both the
  engine and the tree walk go through `SelectorEngine::visualChildren`, which steps from a
  `QQuickWindow` into its `contentItem`.

  This was the long-standing "QML is deferred" impression, and it was three separate bugs rather
  than a missing feature. `dumpTree` had its own child walk that never made that step, *and*
  dropped anything that was neither `QWidget` nor `QWindow` (a `QQuickItem` is neither), so the
  tree reported one bare node where the engine found forty objects — which is why `liberaqt
  inspect` used to print "no objects found" for a window full of them. `Window.kind` answered
  `"widget"` for every window in existence, because only `window.list` ever sent `window_type`
  and `kind` reads `object.info`. And `rootObjects()` enumerated only top-level *windows*, so a
  rootless search found zero widgets in an application full of them.

* **Input at a Quick item is still unsupported**, and that is the real remaining gap: the input
  backend is QWidget-only, so clicking or typing at a `QQuickItem` raises `unsupported`.
  `highlight()` is widget-only for the same reason. The `quick.*` commands are unregistered.

* **Quick support is optional at build time.** `agent/CMakeLists.txt` compiles `quick_backend.cpp`
  only when `find_package(Qt Quick Qml)` succeeds; without it `LIBERAQT_HAVE_QUICK` is undefined
  and the Quick splice compiles out entirely. CI's Qt 5.15 legs install no `qtdeclarative`, so
  those agents have no Quick support at all. Check this before debugging any QML failure.

* **Object counts in a Quick scene track the window size.** qmleasing renders a grid of curve
  previews: 155 objects at 669px tall, 355 at 720px. Never assert an exact count against it.

One consequence of the `rootObjects()` fix worth knowing: `topLevelWidgets()` means "is a window",
not "has no owner". A dialog, a menu and a popup are all windows *and* QObject children of the
widget that owns them, so they are skipped as roots — otherwise their subtrees get walked twice
and every widget inside is reported two times over.

## Two suites: `tests/` proves the client, `e2e/` proves the agent

**CI runs `tests/` only** — pure-Python unit tests, no Qt, no agent. A green run there says the
client parses selectors and maps errors correctly and says *nothing* about whether a click lands.
Not one line of `agent/src/` executes. CI additionally builds the agent on four ABIs, which is a
compile check and nothing more.

**`e2e/` drives real applications**, is not run by CI, and is where every agent bug this project
has had was found. It skips cleanly when the application it needs is absent.

| File | Target | What it is for |
|------|--------|----------------|
| `e2e/test_actions_live.py` | Qt Assistant | Text input, chords, window shortcuts, checkboxes, item views, wheel |
| `e2e/test_selectors_live.py` | Qt Assistant | Every selector rule, operator, pseudo-class, strictness, scoped chaining |
| `e2e/test_qml_live.py` | qmleasing | The Quick tree, `Window.kind`, and rootless searches across both trees |
| `e2e/test_libero_smartdesign.py` | Libero SoC | Reading/writing a self-painting widget through its slots |
| `e2e/test_libero_configurator.py` | Libero SoC | Driving an out-of-process dialog through a child agent |
| `e2e/test_libero_smarttime.py` | Libero SoC | Opening SmartTime from the Design Flow and driving it — a **64-bit** child of a 32-bit parent |
| `e2e/test_docs_examples.py` | Qt Assistant | The getting-started page's code, run as a test so the tutorial cannot rot |
| `e2e/test_libero_nlview.py` | Libero SoC | Reading a schematic out of an NLview canvas through `call_native` |
| `e2e/test_libero_selectors.py` | Libero SoC | `:has`, `:parent`, `parent()`, `ancestor()` on a **different ABI** (Qt 5.15, MSVC, 32-bit); read-only |
| `e2e/test_libero_synthesis.py` | Libero SoC | One end-to-end flow: new project → SmartDesign → import HDL → synthesise. Writes to disk, ~75s |

There is deliberately **no purpose-built sample application**, and there should never be one. A toy
agrees with whatever the driver happens to do; three separate bugs that made LiberaQT unusable on
real software sat undetected behind a green sample suite (see git history for `sync.wait_idle`,
namespaced class names, and objectNames that are not bare identifiers). Every one of these suites
drives software written with no knowledge of this project.

That keeps paying. `e2e/` has since caught, among others: `:has()` and `:parent()` being parsed by
the client and silently dropped by the agent; chaining off `.first` widening the search back out to
every match; two Libero buttons identical in class, text *and* objectName; and a `QCompleter` popup
whose mouse grab swallows the click that follows it.

**Two ABIs matter, not one.** Assistant is Qt 6.7 / MinGW / x86_64 and ships inside the same Qt the
agent is built from. Libero is Qt 5.15 / MSVC / **x86**, from a vendor who has never heard of this
project. A selector engine shipping as a Qt plugin is only proven by passing on both, and the two
have genuinely disagreed — `ancestor("QDockWidget")` returns a plain `QDockWidget` in Assistant and
Libero's namespaced `Aqwidget::AQDockWidget` subclass. **Compare handles, not class names.**

An older suite covering Designer, Linguist, qdbusviewer and qmleasing was deleted on 2026-08-17 and
is still in git history at `b51cfa0:integration/`; take anything useful from there rather than
rewriting it.

## Commands

```bash
pip install -e ".[dev]"        # re-run after renaming or moving anything under src/

# What CI runs, verbatim:
ruff check src tests           # note: src tests, not agent/ (that is C++)
pytest tests/ -q               # unit tests, no Qt needed

pytest tests/test_selectors.py::test_parse_type_with_nth -v   # single test
ruff format src tests

# Not in CI, and not currently clean — six pre-existing errors, mostly missing stubs.
# Advisory only; do not read a red run as something you broke.
mypy src/liberaqt

# The live suites. Not in CI. They skip when the application is missing.
pytest e2e/test_actions_live.py e2e/test_selectors_live.py   # needs Qt's assistant

# Every Qt-based suite resolves its application through conftest's `qt_tool`, so one flag
# points the whole set at a particular Qt -- which is how you verify a new Qt version:
pytest e2e/test_actions_live.py e2e/test_selectors_live.py e2e/test_qml_live.py        e2e/test_docs_examples.py --liberaqt-qt-bin "C:/Qt/6.5.9/mingw_64/bin"
# The choice is exclusive: a path with no Qt in it skips rather than falling back to another
# installation, because silently testing 6.7 while believing you tested 6.5 is worse than a skip.
pytest e2e/test_libero_selectors.py                          # read-only, ~10s
pytest e2e/test_libero_synthesis.py                          # writes a project, ~75s
ruff check src tests e2e
```

`pytest` on its own collects `tests/` only: pyproject pins `testpaths`. The live suites have to be
asked for by path.

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
cmake -S agent -B build/agent -DCMAKE_PREFIX_PATH=$QTDIR -DCMAKE_BUILD_TYPE=Release
cmake --build build/agent --parallel
cmake --install build/agent --prefix ~/.cache/liberaqt/agents/qt6.7-linux-x86_64-gcc
```

Windows / MinGW — Qt 6.7 targets, PowerShell:

```powershell
$env:PATH = "C:\Qt\Tools\mingw1120_64\bin;C:\Qt\6.7.3\mingw_64\bin;$env:PATH"
cmake -S agent -B build/agent -G Ninja -DCMAKE_PREFIX_PATH=C:/Qt/6.7.3/mingw_64 `
      -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_COMPILER=C:/Qt/Tools/mingw1120_64/bin/g++.exe
cmake --build build/agent --parallel
cmake --install build/agent --prefix "$env:LOCALAPPDATA\liberaqt\agents\qt6.7-windows-x86_64-mingw"
```

Windows / MSVC 32-bit — what Libero SoC needs, and what CLAUDE.md long claimed was impossible on
this machine. Visual Studio 2022 BuildTools **is** installed, and Qt 5.15's 32-bit kit is
`C:/Qt/5.15.0/msvc2019` (Qt names 32-bit without a suffix; `msvc2019_64` is the 64-bit one). MSVC
toolsets v140–v143 are binary compatible, so VS2022's compiler links fine against a Qt built with
2019. Run from `cmd`, or a `.bat`, because `vcvarsall` sets the environment for the shell it runs
in:

```bat
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvarsall.bat" x86
cmake -S agent -B build/agent-msvc32 -G Ninja -DCMAKE_PREFIX_PATH=C:/Qt/5.15.0/msvc2019 -DCMAKE_BUILD_TYPE=Release
cmake --build build/agent-msvc32 --parallel
cmake --install build/agent-msvc32 --prefix "%LOCALAPPDATA%\liberaqt\agents\qt5.15-windows-x86-msvc2019"
```

Use a separate build directory per ABI: the two configurations cannot share one.

Always `-G Ninja`: the "MinGW Makefiles" generator chokes on drive-letter colons.

Driving an application by hand does not need Qt on `PATH` — the application loads its own DLLs
from its own directory. `LiberaQt.launch()` only needs the path to the executable and a matching
agent installed under the tag `liberaqt doctor` reports.

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

**A widget that paints its own contents has nothing in the object tree**, so no selector will
reach inside it — Libero's SmartDesign canvas (`Aqnlvcanvas::NlvSDWidget`, wrapping NLview) has no
`QGraphicsView`, no per-item objects and no properties of its own. The meta-object is the only way
in: `object.list_methods` to discover the slots, `object.invoke` to call them. That includes
`std::string` parameters, which QVariant cannot convert into and which the agent therefore
materialises itself — legitimate only because the plugin already has to match the application's
compiler and stdlib. See `e2e/test_libero_smartdesign.py`.

**Adding a command:** handler in `dispatcher.cpp` (runs on the GUI thread) → name in
`protocol.Cmd` → client method → `docs/PROTOCOL.md` entry → a test in `e2e/` that drives it
against a real application. (Older text here and in `docs/CONTRIBUTING.md` asks for a "conformance
suite entry" and "a test against the sample app": neither exists, and neither should be looked
for.) Nothing in
the repository will exercise the handler, so drive it against a real application by hand before
believing it works: every agent bug this project has had was found that way and by nothing else.

Three registration helpers, and picking the wrong one is how a command comes to race the
application: `registerCommand` for anything that answers immediately, `registerAsyncCommand` for
anything that lets the event loop turn, and `registerInputCommand` — which every `input.*` command
uses — for anything that only *queues* events and must reply once the queue has drained.

**A command that lets the application run must be asynchronous.** Use `registerAsyncCommand` and
resolve from the event loop. Blocking inside a handler — in particular pumping it with
`QCoreApplication::processEvents` — strands the reply the moment the application enters a nested
loop of its own, and a modal dialog during startup is enough to do that. `sync.wait_idle` is the
worked example; `sync.wait_signal` will need the same treatment.

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

The agent is a Qt plugin, so it must match the AUT's Qt **minor** version (6.7, not 6.8) and its
compiler/stdlib ABI.

**The version rule is asymmetric, and `resolve()` relies on it.** Qt keeps plugins forward
compatible within a major release, so an agent built against 6.5 loads into 6.5, 6.6 and 6.7 —
but one built against 6.7 is refused by a 6.5 host, silently, in the usual way (no agent, no port
file, no error). So the fallback only ever looks *downwards*, taking the newest installed agent at
or below the application's version, and refuses upwards with a message naming the agent it would
not use. Building against the oldest Qt you support therefore covers the whole major series with
one binary — **but only if it compiles there**, and the private API it uses does move. Qt renamed
`handleWindowActivated` to `handleFocusWindowChanged` *within* Qt 6, not at the 5-to-6 boundary,
so `compat.h` gates that one at 6.6 rather than on the major version. A 6.5 build found it, as a
compile error rather than a silent failure, which is the good case. 6.6 itself is untested.

Exercised: **Qt 5.15, 6.5.3 and 6.7.3**. The Qt suites pass against 6.5 and 6.7 (63 tests each),
selected with `--liberaqt-qt-bin`. Fallbacks (LD_PRELOAD, Windows DLL injection, opt-in embedding) are described
in `docs/INJECTION.md` but **not implemented**: `agent/preload/` and `agent/injector_win/` hold a
README each and no source. Generic-plugin injection is the only mode that works today.

**Every Qt child the application spawns inherits this and loads an agent too.** That is not a
leak, it is the only way to reach a window that is not in the application at all: Libero's IP core
configurator is a separate `coreconfig.exe`, invisible to Libero's own agent. `app.child_agents`
lists them and `app.attach_child()` connects with the same inherited token — see
`e2e/test_libero_configurator.py`. The port file is keyed by pid for exactly this reason; one
shared path meant the child silently overwrote the parent's port. Two things to remember: every
such child stands up its own server, and a child may outlive the parent, since cleanup only
terminates what the launcher started.

`liberaqt agents kits` lists the Qt kits on this machine an agent can be built from, and
`liberaqt agents build --tag <tag>` builds and installs one: the tag is derived from the kit
that was actually configured, MinGW is matched to the kit's own version, and the installed
binary is checked for its plugin key before the command reports success.

`liberaqt docs` serves the documentation site locally (mkdocs when the `docs` extra is installed, else the last built `site/`); it prints the real address, which is under the `site_url` path rather than the root.

`liberaqt doctor <exe>` is the first thing to run against any new target. It classifies the binary
via `agent_registry.inspect_binary()` into three outcomes, and the distinction matters:

* **dynamic Qt** → injectable; it then reports whether a matching agent exists, and names the
  toolchain when one does not (an agent for the right Qt but the wrong compiler will not load)
* **static Qt** → *never* injectable by any mode, because there is no plugin loader and a second
  Qt copy in one process is undefined behaviour. Only the embedded build (INJECTION.md §4) works.

A fourth outcome `doctor` cannot see: **a dynamically-linked Qt process that never builds a GUI**.
Generic plugins are instantiated by the QPA platform plugin, which only exists once a
`QGuiApplication` is constructed, so a `QCoreApplication`-only process reads the injection
environment and acts on none of it — no agent, no port file, no error. Libero's Netlist Viewer is
the worked example: the same binary attaches when launched with `-s <script>` (QtGui and QtWidgets
load) and never does when Libero starts it (QtCore alone). Launch such a tool yourself rather than
attaching to the one the application spawned; `Win32_Process.CommandLine` gives the parent's
arguments.

**Do not assume one ABI per application tree.** Libero is 32-bit and spawns a 64-bit
`NetlistViewer.exe` and a 64-bit SmartTime; of the 54 Qt applications it ships, 44 are x86 and 10
x86_64, all Qt 5.15. Install an agent per ABI; the launcher puts every installed plugin directory
on `QT_PLUGIN_PATH` so a child of either architecture can find one.

**One plugin key cannot serve two ABIs**, and getting this wrong fails completely silently. Qt
binds a plugin key to exactly one library — `qLoadPlugin` takes the first match on the path and
never tries a second — so while every build advertised only `liberaqt`, the first directory listed
owned the key and every other architecture was locked out: environment delivered, plugin present,
no agent, no port file, no error anywhere. **Pointer size alone does not identify an ABI**, which cost a second round of
exactly this bug: a 64-bit MinGW build and a 64-bit MSVC build both claimed `liberaqt_64`, and
installing the former stopped SmartTime — an MSVC application — from finding the latter. What
distinguishes two mutually unloadable plugins is Qt minor version, pointer size *and* compiler,
so the key carries all three: `liberaqt_5_15_64_msvc`, `liberaqt_6_7_64_gnu`,
`liberaqt_5_15_32_msvc`.

`agent/CMakeLists.txt` generates it into `liberaqt_plugin.json` via `configure_file`, and
`plugin_key_for()` in `agent_registry.py` derives the same string from an install tag — the two
must stay in step. The launcher names every installed agent's key in `QT_QPA_GENERIC_PLUGINS`, so
each process asks for the one it can load and is refused the rest harmlessly. **After changing
anything here, check the key is really in the binary**; `liberaqt doctor` does it for every
installed agent and marks a build that lacks its own key `STALE`. `liberaqt doctor` checks this for every
installed agent and flags one that predates the keys as `STALE`; agents live in the cache and are
not versioned, so a checkout is not enough — they have to be rebuilt per ABI.

Diagnosing a child that loads no agent: `QT_DEBUG_PLUGINS=1` makes Qt narrate every plugin it
considers and why it refuses one. Note the child's output does *not* reach `app.logs` — it does
not inherit the parent's pipe — so read it from the child itself.

Attaching works today for `coreconfig.exe`, SmartTime (`smartsta.exe`, and the first
cross-architecture case proven), `IOEditor`, `FPExpress`, `sdbg`, `pa5pllgui`, `IOAdvisor` and
`stce`; the rest mostly want arguments, as `coreconfig.exe` does.
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
animations done, extra event-loop turn). Failures raise structured exceptions carrying near-miss
suggestions.

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
  the silent widening it caused is covered by `tests/test_locator_scoping.py`.

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
| `docs/ACTIONS.md` | Every action method: native vs synthetic, covered vs not, per-mode limits |
| `docs/PROTOCOL.md` | Adding commands, wire format |
| `docs/SELECTORS.md` | Selector grammar, matching rules, object maps |
| `docs/API.md` | Python API design philosophy |
| `docs/INJECTION.md` | Injection trade-offs and fallbacks |
| `docs/ROADMAP.md` | Milestones, sizing, risk register |
| `docs/OPEN_QUESTIONS.md` | Unresolved design decisions |
| `docs/CONTRIBUTING.md` | Repo layout and house rules |
