# qtdriver — Getting the agent into the process

Four mechanisms, in order of preference. The launcher tries them in order and `qtdriver doctor`
explains which one applies to a given app.

## 1. Qt generic plugin (primary, default)

`QGuiApplication`'s constructor instantiates every plugin named in the `QT_QPA_GENERIC_PLUGINS`
environment variable (or the `-plugin` command-line argument) through `QGenericPluginFactory`.
This is a documented, supported Qt extension point that exists on Qt 5.15 and 6.7, on every
desktop platform.

The launcher sets:

```
QT_PLUGIN_PATH  = <cache>/agents/qt6.7-linux-gcc/plugins   (prepended to existing value)
QT_QPA_GENERIC_PLUGINS = qtdriver
QTDRIVER_TOKEN  = <32 random hex chars>
QTDRIVER_PORT   = 0            # 0 = ephemeral, agent reports back
QTDRIVER_PORT_FILE = <tmpfile> # agent writes the chosen port here
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
| 6.7 | Linux x86_64 | gcc 11 (manylinux_2_34) | `libqtdriver.so` |
| 6.7 | Windows x64 | MSVC 2019/2022 | `qtdriver.dll` |
| 5.15 | Linux x86_64 | gcc 11 | `libqtdriver.so` |
| 5.15 | Windows x64 | MSVC 2019 | `qtdriver.dll` |

`agent_registry.py` detects the AUT's Qt build by inspecting the linked Qt libraries
(`ldd` / PE import table) before launch, then picks the matching artifact. On a mismatch it fails
loudly with a build-from-source command rather than letting Qt silently skip the plugin.

For static Qt builds, plugin loading is unavailable — use mechanism 4.

## 2. LD_PRELOAD (Linux fallback)

`LD_PRELOAD=libqtdriver_preload.so` with a constructor that dlopens the agent and hooks
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
#include <qtdriver/embed.h>
int main(int argc, char** argv) {
    QApplication app(argc, argv);
    qtdriver::startIfRequested();   // no-op unless QTDRIVER_TOKEN is set
    ...
}
```

Compiled into a test build of the app, this sidesteps every ABI and injection problem: the agent
is just a linked library. Recommended for teams that control the app's build — which, for an
in-house automation tool, is usually the case. Also the only option for statically-linked Qt.

## 5. Security posture

The agent is a remote-code-execution surface by design (`object.invoke`, `quick.evaluate`).
Mitigations, all on by default:

* Bind `127.0.0.1` only, never `0.0.0.0`.
* Require a per-launch token passed out-of-band via environment.
* Refuse to start unless `QTDRIVER_TOKEN` is present — so a shipped binary that accidentally
  contains the plugin does nothing in production.
* Single client connection; reject concurrent connects.
* Loud `qWarning` on startup: the process is automatable.

The README must state plainly that the agent should never be shipped in a production build.

## 6. Startup handshake sequence

```
launcher: pick port file, token, agent dir
launcher: spawn AUT with env
agent:    plugin constructed during QGuiApplication ctor
agent:    singleShot(0) -> bind 127.0.0.1:0 -> write port to QTDRIVER_PORT_FILE
launcher: poll port file (50 ms, up to --startup-timeout, default 30 s)
client:   connect -> receive hello -> send auth -> receive ready
client:   sync.wait_idle -> first window is up
```

If the port file never appears, the launcher reports the three likeliest causes (ABI mismatch,
`QT_PLUGIN_PATH` overridden by a bundled `qt.conf`, app crashed at startup) with the AUT's captured
stderr attached, plus the `QT_DEBUG_PLUGINS=1` command to run for the full loader trace.
