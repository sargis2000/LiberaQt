# LiberaQT — Getting the agent into the process

Four mechanisms, in order of preference. The launcher tries them in order and `liberaqt doctor`
explains which one applies to a given app.

## 1. Qt generic plugin (primary, default)

`QGuiApplication`'s constructor instantiates every plugin named in the `QT_QPA_GENERIC_PLUGINS`
environment variable (or the `-plugin` command-line argument) through `QGenericPluginFactory`.
This is a documented, supported Qt extension point that exists on Qt 5.15 and 6.7, on every
desktop platform.

The launcher sets:

```
QT_PLUGIN_PATH  = <cache>/agents/qt6.7-linux-gcc/plugins   (prepended to existing value,
                  followed by every other installed agent's plugins directory)
QT_QPA_GENERIC_PLUGINS = <one key per installed agent>,liberaqt
                         e.g. liberaqt_5_15_32_msvc,liberaqt_6_7_64_gnu,liberaqt
LIBERAQT_TOKEN  = <32 random hex chars>
LIBERAQT_PORT   = 0            # 0 = ephemeral, agent reports back
LIBERAQT_PORT_DIR  = <tmpdir>  # each agent writes <pid>.port here
```

Properties:

* No source changes, no recompilation, no debugger, no elevated privileges.
* Works for console-less GUI apps and for apps that re-exec themselves (env is inherited).
* Loads *before* `main()` creates any windows, so nothing is missed.
* The plugin returns `nullptr` from `create()` — it is a pure side-effect plugin — and defers real
  work to a `QTimer::singleShot(0, ...)` so the event loop is running before the server binds.

Constraint: **ABI compatibility**. A Qt plugin must be built against the same Qt minor version and
the same compiler/C++ stdlib as the host. Hence one agent binary per matrix cell:

| Qt | Platform | Compiler | Artifact |
| --- | --- | --- | --- |
| 6.7 | Linux x86_64 | gcc 11 (manylinux_2_34) | `libliberaqt.so` |
| 6.7 | Windows x64 | MSVC 2019/2022 | `liberaqt.dll` |
| 5.15 | Linux x86_64 | gcc 11 | `libliberaqt.so` |
| 5.15 | Windows x64 | MSVC 2019 | `liberaqt.dll` |

`agent_registry.py` detects the AUT's Qt build by inspecting the linked Qt libraries
(`ldd` / PE import table) before launch, then picks the matching artifact. On a mismatch it fails
loudly with a build-from-source command rather than letting Qt silently skip the plugin.

For static Qt builds, plugin loading is unavailable — use mechanism 4.

## 2. LD_PRELOAD (Linux fallback)

`LD_PRELOAD=libliberaqt_preload.so` with a constructor that dlopens the agent and hooks
`QCoreApplication` construction. Useful when the app clears `QT_QPA_GENERIC_PLUGINS`, uses a
custom plugin loader, or bundles its own `qt.conf` that overrides `QT_PLUGIN_PATH`. Same ABI
constraint applies.

## 3. Runtime injection into a running process (attach mode)

* **Linux:** `ptrace`-based `dlopen` injection (GammaRay/`injector` style), or `gdb -p` with a
  `call dlopen(...)` one-liner where gdb is available. Requires `ptrace_scope` permission.
* **Windows:** `CreateRemoteThread` + `LoadLibraryW` in the target. Requires matching bitness and
  usually triggers EDR/AV heuristics — document this prominently, it is the #1 support question
  for tools in this space.

Attach mode is inherently more fragile (windows already exist, some state is unobservable). It is
a v0.3 feature, not v0.1, and the docs should steer people to launch mode.

## 4. In-app opt-in (most robust, requires app cooperation)

Ship a tiny header-only shim:

```cpp
#include <liberaqt/embed.h>
int main(int argc, char** argv) {
    QApplication app(argc, argv);
    liberaqt::startIfRequested();   // no-op unless LIBERAQT_TOKEN is set
    ...
}
```

Compiled into a test build of the app, this sidesteps every ABI and injection problem: the agent
is just a linked library. Recommended for teams that control the app's build — which, for an
in-house automation tool, is usually the case. Also the only option for statically-linked Qt.

## 4a. When none of this can work: statically-linked Qt

Modes 1–3 all end with the agent running inside the process and calling into the host's Qt. A
statically-linked Qt defeats that in two independent ways:

* There is no plugin loader to hook. `QT_QPA_GENERIC_PLUGINS` works by having Qt search for and
  load external plugin binaries at runtime; a static build resolves its plugins at link time.
* An agent that brought its own Qt would put two independent copies of Qt in one process — two
  `QCoreApplication::instance()`, two type registries, two event dispatchers. That is undefined
  behaviour, not a workaround, so DLL injection does not rescue it either.

Mode 4 is the only route, and it requires building the application yourself.

`liberaqt doctor` detects this and says so, rather than reporting a missing agent. It reads the
`QLibraryInfo::build()` stamp that Qt writes into whichever binary carries QtCore — the shared
library for a normal build, the executable itself for a static one:

```
Qt 6.7.2 (x86_64-little_endian-llp64 static release build; by MSVC 2019)
```

That single string gives the Qt version, the linkage and the compiler, so no part of the verdict
is inferred. A worked example is Microchip's Libero online installer, which is Qt Installer
Framework: static Qt 6.7.2, MSVC 2019, and correctly reported as not instrumentable. Note that a
vendor's *installer* being static says nothing about the application it installs — large
applications almost always ship Qt as DLLs, because their own plugin architectures need it.

## 5. Security posture

The agent is a remote-code-execution surface by design (`object.invoke`, `quick.evaluate`).
Mitigations, all on by default:

* Bind `127.0.0.1` only, never `0.0.0.0`.
* Require a per-launch token passed out-of-band via environment.
* Refuse to start unless `LIBERAQT_TOKEN` is present — so a shipped binary that accidentally
  contains the plugin does nothing in production.
* Single client connection; reject concurrent connects.
* Loud `qWarning` on startup: the process is automatable.

The README must state plainly that the agent should never be shipped in a production build.

## 6. Startup handshake sequence

```
launcher: pick port dir, token, agent dir
launcher: spawn AUT with env
agent:    plugin constructed during QGuiApplication ctor
agent:    singleShot(0) -> bind 127.0.0.1:0 -> write port to <LIBERAQT_PORT_DIR>/<pid>.port
launcher: poll the port dir (50 ms, up to --startup-timeout, default 30 s);
          the first file to appear is the application we launched
client:   connect -> receive hello -> send auth -> receive ready
client:   sync.wait_idle -> first window is up
```

### One file per process, and why

The environment above is inherited by **every child the application spawns**, so a Qt child loads
the agent and publishes a port of its own. A single shared path could not survive that: each
process overwrote the last.

Measured on Libero SoC, whose IP core configurator is a separate `coreconfig.exe`:

```
libero agent port         = 62118
port file after Configure = 62158     <- the child's port, over the parent's
```

Nothing noticed, because the launcher reads the port once during startup and never again. Naming
each file after the pid removes the collision, and turns the children into something findable:
the directory is a list of every agent in the process tree.

`LIBERAQT_PORT_FILE` is still honoured when set explicitly -- the embedded build in section 4 uses
it -- but the launcher no longer sets it, precisely so that nothing inherits it.

### A Qt process with no GUI cannot be reached

`doctor` classifies a binary by its Qt linkage, and a dynamically-linked Qt application is
reported injectable. That is necessary but not sufficient. Generic plugins are instantiated by
the **QPA platform plugin**, and the platform plugin only exists once a `QGuiApplication` has
been constructed. A Qt process that creates only a `QCoreApplication` reads the environment and
acts on none of it: no platform plugin, no generic plugin, no agent. There is nothing to connect
to, and no error either -- the port file simply never appears.

Measured on Libero's Netlist Viewer, which is the same binary in both cases:

```
launched by us with -s <script>   Qt5Core.dll, Qt5Gui.dll, Qt5Widgets.dll   agent on port 64523
launched by Libero                Qt5Core.dll                               no agent, ever
```

The environment was inherited perfectly in both -- `QT_QPA_GENERIC_PLUGINS=liberaqt`, both agent
plugin directories on `QT_PLUGIN_PATH`, token and port directory all present. Libero simply starts
that tool in a mode where it never builds a GUI.

The practical answer when this happens is to launch the tool yourself rather than attach to the
one the application spawned. `Win32_Process.CommandLine` gives the arguments the parent used, which
is usually all that is needed to reproduce the invocation.

### One plugin key cannot serve two ABIs

A process tree can span architectures -- Libero SoC is 32-bit and spawns a 64-bit SmartTime -- and
every Qt child inherits the injection environment. Two things are needed for that to work, and the
second is easy to miss because getting it wrong produces no error at all.

**Every installed agent's plugin directory goes on `QT_PLUGIN_PATH`**, so a child of either
architecture can find a plugin it is able to load.

**And each build must advertise its own key.** Qt binds a plugin key to exactly one library:

```cpp
QObject *QGenericPluginFactory::create(const QString &key, const QString &specification)
{
    const QString driver = key.toLower();
    if (QObject *object = qLoadPlugin<QObject, QGenericPlugin>(loader(), driver, specification))
        return object;
    return nullptr;                       // no fallback to a second candidate
}
```

While every build advertised only `liberaqt`, the first directory on the path owned that key and
every other architecture was locked out. The symptom is a process that looks perfectly injectable
and simply has no agent: environment delivered with its token, plugin present on its path,
`qwindows.dll` loaded, the DLL loadable under `LoadLibrary` -- and nothing anywhere reporting a
problem. Reordering the path does not fix it, it only moves the failure: with the 64-bit directory
first, 32-bit Libero could no longer load *its* agent and never reported a port.

So each build advertises a key of its own. **Pointer size alone is not enough**, which cost a
second round of the same bug: a 64-bit MinGW plugin and a 64-bit MSVC plugin both claimed
`liberaqt_64`, and installing one stopped an MSVC application finding the other. The key names Qt
minor version, pointer size and compiler together -- `liberaqt_5_15_64_msvc`,
`liberaqt_6_7_64_gnu` -- and is generated by `agent/CMakeLists.txt` into `liberaqt_plugin.json`,
with `plugin_key_for()` in `agent_registry.py` deriving the same string from an install tag. The launcher names them all in
`QT_QPA_GENERIC_PLUGINS`, so each process asks for the one it can load and is refused the others
harmlessly. Arming twice is safe: `Agent::start()` guards on `m_started`, and the plugin is a
singleton behind `Agent::instance()`.

Two practical notes:

* **Verify the key reached the binary.** moc selects the metadata file, and if it does not see the
  define it silently takes the wrong one. `liberaqt_64` must appear in the bytes of a 64-bit build.
* **`QT_DEBUG_PLUGINS=1` is how this was found**, and is the first thing to reach for when a child
  loads no agent: Qt narrates every plugin it considers, prints the keys it extracted, and says why
  it refused one. The child's output does not reach `Application.logs` -- it does not inherit the
  parent's pipe -- so read it from the child.

### What can be attached to, in practice

Libero SoC 2026.1 ships **54 Qt applications**, all Qt 5.15 and all dynamically linked: 44 32-bit
in `Designer/bin` and 10 64-bit in `Designer/bin64`. A parent and its children need not share an
architecture -- Libero is 32-bit and spawns a 64-bit `NetlistViewer.exe` -- so **install an agent
for every ABI in the tree**, and put each one's plugin directory on `QT_PLUGIN_PATH`; Qt skips the
ones it cannot load.

Launched directly, with no arguments:

| Attaches | Does not |
| --- | --- |
| `IOAdvisor`, `stce`, `IOEditor`, `FPExpress`, `sdbg` (SmartDebug), `pa5pllgui` | `ChipPlanner`, `pa_shell`, `pa4mssgpio` — exit or fault without arguments |
| | `SSNAnalyzer`, `smartpower`, `smartsta`, `CoresViewApplication` — publish a port but do not complete the handshake in 8 s |

The second row is not a refusal to be driven: those tools are meant to be started with arguments,
exactly as `coreconfig.exe` is. The third is most likely a handshake timeout on a slow-starting
application rather than anything structural.

### Driving an out-of-process dialog

That inheritance is the only way to reach a window that is not in the application at all:

```python
app = lq.launch(libero)
...                                        # trigger the action that spawns the child
cfg = app.attach_child(timeout=90.0)       # waits for a child agent, connects with the same token
cfg.window(title="Configurator").locator("QPushButton[text='OK']").click()
```

`app.child_agents` lists them, `app.wait_for_child_agent()` blocks until one appears. The token is
shared by inheritance, so authentication needs nothing extra. Two caveats: every Qt child now
stands up a server, and a child may outlive the parent -- `coreconfig.exe` does, and cleanup only
terminates what the launcher started directly.

If no port file appears, the launcher reports the three likeliest causes (ABI mismatch,
`QT_PLUGIN_PATH` overridden by a bundled `qt.conf`, app crashed at startup) with the AUT's captured
stderr attached, plus the `QT_DEBUG_PLUGINS=1` command to run for the full loader trace.
