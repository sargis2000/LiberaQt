# Child processes and ABIs

Real applications launch other applications. A configuration dialog, a viewer, an analysis tool —
these are often separate processes, invisible to the main application's agent and to any selector
you write against it.

LiberaQT reaches them, including when they are a **different CPU architecture** from their parent.

## Why it works at all

The launcher sets the injection variables on a copy of the environment, and **every Qt child
inherits them**. So a child loads an agent of its own, and publishes its own port file:

```
LIBERAQT_PORT_DIR=<temp dir>     # each agent writes <pid>.port here
```

The port file is keyed by pid for exactly this reason. One shared path meant a child silently
overwrote its parent's port.

## Attaching

```python
endpoint = app.wait_for_child_agent(timeout=120.0)
child = app.attach_child(endpoint)

for window in child.windows:
    print(window.title)

child.window(title="Configurator").locator("QPushButton[text='OK']").click()
```

`app.child_agents` lists every agent in the process tree that is not the one you launched. The
same inherited token authenticates all of them.

```python
for endpoint in app.child_agents:
    print(endpoint.pid, endpoint.port)
```

!!! note "Two things to remember"
    - Each child stands up its **own** server; you attach per process.
    - A child may **outlive the parent**, because cleanup only terminates what the launcher
      started. Kill it yourself in a fixture teardown if it should not survive.

!!! warning "A child's output does not reach `app.logs`"
    It does not inherit the parent's pipe. If you need a child's Qt diagnostics, read them from
    the child process itself.

## One plugin key cannot serve two ABIs

This is the subtle part, and getting it wrong fails **completely silently**.

Qt binds a plugin key to exactly one library — `qLoadPlugin` takes the first match on
`QT_PLUGIN_PATH` and never tries a second:

```cpp
if (QObject *object = qLoadPlugin<QObject, QGenericPlugin>(loader(), driver, specification))
    return object;
return nullptr;                       // no fallback to a second candidate
```

So while every agent build advertised only the key `liberaqt`, the first directory on the path
owned it and **every other architecture was locked out**. The symptom was a process that looked
perfectly injectable and simply had no agent: environment delivered with its token, plugin present
on its path, platform plugin loaded, the DLL loadable — and nothing anywhere reporting a problem.

The fix, and what happens now:

- each build advertises a key naming its Qt version, pointer size **and** compiler --
  `liberaqt_5_15_64_msvc`, `liberaqt_6_7_64_gnu`;
- the launcher puts **every** installed agent's plugin directory on `QT_PLUGIN_PATH`;
- and it asks for every installed key, so each process loads the one it can.

!!! warning "Pointer size alone is not an ABI"
    An earlier version keyed on bitness only, and a 64-bit MinGW agent installed beside a 64-bit
    MSVC one took the key from it -- which stopped SmartTime, an MSVC application, finding its
    agent. A MinGW plugin is exactly as unloadable in an MSVC process as a 32-bit one.

Each process then loads the one it can and is refused the others harmlessly.

### What you have to do

**Install an agent per architecture you expect to meet.**

```bash
liberaqt doctor
```

```title="Output"
agents installed:
  - qt5.15-windows-x86-msvc2019     ->  ...\liberaqt.dll
  - qt5.15-windows-x86_64-msvc2019  ->  ...\liberaqt.dll
```

If an agent predates the per-ABI keys, `doctor` marks it:

```
  - qt5.15-windows-x86-msvc2019  ->  ...\liberaqt.dll   [STALE: no liberaqt_32 key]
```

A stale agent still works for its own architecture, but while it is on the path it can take the
key from a child of a different one. Rebuild it.

!!! danger "After changing anything in `agent/`, rebuild every ABI"
    Agents live in a cache and are not versioned with the source. Pulling a new revision does not
    update them, and the failure mode is silence.

## When a child cannot be reached

There is one case no injection mode can fix: **a process that never builds a GUI**.

Generic plugins are instantiated by the QPA platform plugin, which only exists once a
`QGuiApplication` has been constructed. A `QCoreApplication`-only process reads the injection
environment and acts on none of it.

Telling the two apart takes one command:

```powershell
(Get-Process -Id <pid>).Modules |
    Where-Object { $_.ModuleName -match 'Qt5Gui|qwindows|liberaqt' } |
    Select-Object -Expand ModuleName
```

| What you see | Meaning |
|--------------|---------|
| No `Qt5Gui`, no `qwindows` | headless process — unreachable by any injection mode |
| `qwindows` present, `liberaqt` absent | the plugin was refused — see below |
| `liberaqt` present | the agent loaded; look at the port file next |

For the middle case, make Qt explain itself:

```bash
QT_DEBUG_PLUGINS=1
```

Qt then narrates every plugin it considers, prints the keys it extracted, and says why it refused
one. This is the tool that found the plugin-key collision above, and it is the first thing to
reach for.

### The workaround for a headless child

Launch the tool **yourself** rather than attaching to the one the application spawned — you then
control the environment by construction, and the tool usually builds a GUI when invoked directly.
`Win32_Process.CommandLine` gives you the arguments the parent used.
