---
name: agent-loading
description: Why the liberaqt agent does or does not load into a process, and the rules that decide which agent a launch picks - the injection environment, Qt minor-version and compiler-family matching in agent_registry.resolve(), the downward-only version fallback, per-ABI plugin keys (liberaqt_<qt>_<bits>_<compiler>) that CMake and plugin_key_for() must agree on, child processes that load their own agent, liberaqt doctor's classifications, and QCoreApplication-only processes that never load one. Use whenever an agent never attaches (no port file, no error, launch times out), app.child_agents is missing a child, doctor reports STALE or no matching agent, or you edit launcher.py, agent_registry.py, liberaqt_plugin.json.in or the plugin key, or add support for a new Qt version, compiler or architecture (including Libero's 32-bit and 64-bit tools).
---

# Agent loading: which agent, and why none

The agent is a Qt generic plugin. Qt either loads it or quietly does not, and almost every way of
getting this wrong ends the same way: **no agent, no port file, no error anywhere.** The launch
then times out waiting for a port file. So diagnose by elimination, in the order below, rather
than by reading error messages that do not exist.

## Diagnosing an agent that never attaches

1. **`liberaqt doctor <exe>`.** It reads the `QLibraryInfo::build()` stamp Qt writes into
   whichever binary carries QtCore, so linkage, Qt version and compiler are read, not guessed.
   It classifies the binary into one of three outcomes:
   - **dynamic Qt**: injectable. It reports whether a matching agent is installed and, when none
     is, names the toolchain needed. An agent for the right Qt but the wrong compiler will not
     load.
   - **static Qt**: *never* injectable by any mode. There is no plugin loader, and a second Qt
     copy in one process is undefined behaviour. Only the embedded build (`docs/INJECTION.md`
     §4) would work, and it is not implemented.
   - **no Qt**: not a Qt application.
2. **Does the process ever build a GUI?** This is the outcome `doctor` cannot see. Generic plugins
   are instantiated by the QPA platform plugin, which only exists once a `QGuiApplication` is
   constructed. So a `QCoreApplication`-only process reads the injection environment and acts on
   none of it. Libero's Netlist Viewer is the worked example: the same binary attaches when
   launched with `-s <script>` (QtGui and QtWidgets load) and never does when Libero starts it
   (QtCore alone). Launch such a tool yourself rather than attaching to the one the application
   spawned; `Win32_Process.CommandLine` gives the parent's arguments.
3. **Is there an agent for this exact ABI?** That means Qt minor version, pointer size *and*
   compiler family. Do not assume one ABI per application tree: Libero is 32-bit and spawns a
   64-bit `NetlistViewer.exe` and a 64-bit SmartTime. Of the 54 Qt applications it ships, 44 are
   x86 and 10 x86_64, all Qt 5.15. Install an agent per ABI. The launcher puts every installed
   plugin directory on `QT_PLUGIN_PATH`, so a child of either architecture can find one.
4. **Is the key really in the binary?** `liberaqt doctor` (no argument) checks every installed
   agent and marks one that lacks its own plugin key `STALE`. Agents live in the cache, not the
   checkout, so a fix that has been pulled is not installed until each ABI is rebuilt.
5. **Ask Qt.** `QT_DEBUG_PLUGINS=1` makes Qt narrate every plugin it considers and why it refuses
   one. A child's output does *not* reach `app.logs`, because it does not inherit the parent's
   pipe, so read it from the child itself.

## How `resolve()` picks an agent

The rules live in `agent_registry.resolve()`. Each one is there because its absence produced a
wrong agent that still looked like it worked.

- **The compiler family must match, not just the Qt version and platform.** `resolve()` reads the
  toolchain from the binary's build stamp (`inspect_binary().toolchain`) and keeps only agents of
  that family (`compiler_family()`: MSVC 2015, 2019 and 2022 are one family; MinGW is another).
  It used to ignore the compiler. With both `qt5.15-windows-x86-mingw` and `-msvc2019` installed,
  Libero (MSVC) got the MinGW agent, whichever sorted first. The Libero suite still passed,
  because the launcher lists every installed agent's key, the MinGW load failed first, and Qt fell
  through to the MSVC one. Meanwhile `doctor` reported the wrong agent outright. If only the wrong
  compiler is installed, `resolve()` now refuses and says so.
- **The version rule is asymmetric.** Qt keeps plugins forward compatible within a major release,
  so an agent built against 6.5 loads into 6.5, 6.6 and 6.7. One built against 6.7 is refused by
  a 6.5 host, silently. So the fallback only ever looks *downwards*: it takes the newest installed
  agent at or below the application's version, and refuses upwards with a message naming the agent
  it would not use.
- **Building against the oldest Qt therefore covers the series, but only if it compiles there.**
  The private API the agent links (`Qt::GuiPrivate`, for `QWindowSystemInterface`) moves *within*
  a major release. Qt renamed `handleWindowActivated` to `handleFocusWindowChanged` in 6.6, so
  `compat.h` gates that one at 6.6 rather than on the major version. A 6.5 build found it as a
  compile error rather than a silent failure, which is the good case.

Exercised: Qt 5.15, 6.5.3 and 6.7.3. The Qt suites pass against 6.5 and 6.7 (63 tests each),
selected with `--liberaqt-qt-bin`. **6.6 is untested.**

## Plugin keys: one key per ABI

Qt binds a plugin key to exactly one library: `qLoadPlugin` takes the first match on the path and
never tries a second. While every build advertised only `liberaqt`, the first directory listed
owned the key and every other architecture was locked out. The environment was delivered and the
plugin was present, but no agent loaded, no port file appeared and nothing reported an error.

**Pointer size alone does not identify an ABI**, which cost a second round of the same bug. A
64-bit MinGW build and a 64-bit MSVC build both claimed `liberaqt_64`, and installing the former
stopped SmartTime, an MSVC application, from finding the latter. What distinguishes two mutually
unloadable plugins is Qt minor version, pointer size *and* compiler, so the key carries all three:
`liberaqt_5_15_64_msvc`, `liberaqt_6_7_64_gnu`, `liberaqt_5_15_32_msvc`.

- `agent/CMakeLists.txt` generates the key into `liberaqt_plugin.json` (from
  `agent/src/liberaqt_plugin.json.in`) via `configure_file`. `plugin_key_for()` in
  `agent_registry.py` derives the same string from an install tag. **The two must stay in step.**
- The same CMake variable is compiled in as `LIBERAQT_PLUGIN_KEY`, so the hello banner's
  `capabilities["abi_key"]` names the key the plugin was really loaded under. It used to rebuild
  the key from pointer size alone and report `liberaqt_64`, so every agent described itself
  wrongly. An agent that still says `liberaqt_64` predates the fix.
- The launcher names every installed agent's key in `QT_QPA_GENERIC_PLUGINS`, so each process asks
  for the one it can load and is refused the rest harmlessly.
- **After changing anything here, check the key is really in the binary** with `liberaqt doctor`.

## Child processes

Every Qt child the application spawns inherits the injection environment and loads an agent too.
That is not a leak. It is the only way to reach a window that is not in the application at all:
Libero's IP core configurator is a separate `coreconfig.exe`, invisible to Libero's own agent.

- `app.child_agents` lists the children that wrote a port file, and `app.attach_child()`
  connects with the same inherited token. See `tests/e2e/libero/test_libero_configurator.py`.
- **The port file is keyed by pid** (`<pid>.port` in `LIBERAQT_PORT_DIR`) for exactly this
  reason. One shared path meant the child silently overwrote the parent's port.
- Every such child stands up its own server, and a child may outlive the parent, because cleanup
  only terminates what the launcher started.
- Attaching works today for `coreconfig.exe`, SmartTime (`smartsta.exe`, the first
  cross-architecture case proven), `IOEditor`, `FPExpress`, `sdbg`, `pa5pllgui`, `IOAdvisor` and
  `stce`. The rest of Libero's tools mostly want arguments, as `coreconfig.exe` does.

## Other injection modes

Fallbacks such as LD_PRELOAD, Windows DLL injection and opt-in embedding are described in
`docs/INJECTION.md` but **not implemented**: `agent/preload/` and `agent/injector_win/` hold a
README each and no source. Generic-plugin injection is the only mode that works today.

## Where it is tested

None of this needs Qt to unit-test, because the PE import parser and the build-stamp parser take
bytes rather than paths: `tests/unit/test_agent_registry.py`, `test_agent_resolution.py`,
`test_abi_keys.py`, `test_plugin_path.py` and `test_port_directory.py`. Whether a real process
then loads the agent is only answered by launching one; for a cross-architecture change, that
means a Libero child such as SmartTime (`tests/e2e/libero/test_libero_smarttime.py`).
