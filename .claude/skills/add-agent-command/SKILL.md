---
name: add-agent-command
description: Implement or finish a liberaqt protocol command end to end - dispatcher handler, protocol.Cmd name, client method, PROTOCOL.md entry, per-ABI agent rebuild, and live verification against a real application. Use when adding a new command, or finishing one of the six that protocol.Cmd names and no agent registers (quick.evaluate, quick.find_by_id, quick.list_view_item, quick.wait_animations, record.start, record.stop).
---

# Adding a protocol command

Six `Cmd` names have a client method and no handler, so they return `unsupported` and map to
`UnsupportedOperationError`. **The client side usually already exists**; check before writing it.

```bash
# What the client names, versus what the agent registers.
python -c "
import re,pathlib
p=pathlib.Path('src/liberaqt/protocol.py').read_text(encoding='utf-8')
cmds=re.findall(r'^\s+[A-Z_0-9]+\s*=\s*\"([^\"]+)\"', p[p.index('class Cmd'):p.index('class Event')], re.M)
d=pathlib.Path('agent/src/dispatcher.cpp').read_text(encoding='utf-8')
reg=re.findall(r'register(?:Async|Input)?Command\(QStringLiteral\(\"([^\"]+)\"\)', d)
print('Cmd:',len(cmds),' registered:',len(reg))
print('missing:',[c for c in cmds if c not in reg])
"
```

The authoritative answer for a *given build* is the handshake, not this script:
`app.capabilities["commands"]` and `app.supports(Cmd.X)`. An agent compiled without Qt Quick
registers fewer.

## Order of work

1. **Handler in `agent/src/dispatcher.cpp`**, on the GUI thread.
2. **Name in `protocol.Cmd`** (skip if present).
3. **Client method** (skip if present).
4. **Entry in `docs/PROTOCOL.md`.**
5. **A test in `tests/e2e/`** driving it against a real application.
6. **Rebuild and install the agent**, then verify by hand.

## Pick the right registration helper

Getting this wrong is how a command comes to race the application.

| Helper | For |
|---|---|
| `registerCommand` | Answers immediately, without letting the event loop turn |
| `registerAsyncCommand` | Lets the application run; resolves from the event loop |
| `registerInputCommand` | Only *queues* events; replies once the queue drains. Every `input.*` |

**A command that lets the application run must be asynchronous.** Blocking inside a handler --
especially pumping it with `QCoreApplication::processEvents` -- strands the reply the moment the
application enters a nested loop of its own, and a modal dialog during startup is enough to do
that. `sync.wait_idle` and `sync.wait_signal` are the worked examples.

Never let a handler throw across the Qt event loop boundary: catch, convert to a protocol error,
return.

## C++ constraints that bite immediately

- **`QT_NO_CAST_FROM_ASCII` is on.** A bare `"string"` will not convert to `QString`; use
  `QStringLiteral(...)`. This is why command names are registered as
  `registerCommand(QStringLiteral("x.y"), ...)`.
- **Add any new `.cpp` to `LIBERAQT_SOURCES`** in `agent/CMakeLists.txt`. There is no glob.
- **Every `#if QT_VERSION` lives in `compat.h`**, never inline in feature code.
- Qt Quick sources compile only when `find_package(Qt Quick Qml)` succeeds; guard Quick work
  behind `LIBERAQT_HAVE_QUICK`.

## Rebuild and install, per ABI

An agent is a Qt plugin: it must match the application's Qt **minor** version and its
compiler/stdlib. Use a separate build directory per ABI and always `-G Ninja`.

```powershell
# Qt 6.7 / MinGW / x86_64
$env:PATH = "C:\Qt\Tools\mingw1120_64\bin;C:\Qt\6.7.3\mingw_64\bin;$env:PATH"
cmake -S agent -B build/agent -G Ninja -DCMAKE_PREFIX_PATH=C:/Qt/6.7.3/mingw_64 `
      -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_COMPILER=C:/Qt/Tools/mingw1120_64/bin/g++.exe
cmake --build build/agent --parallel
cmake --install build/agent --prefix "$env:LOCALAPPDATA\liberaqt\agents\qt6.7-windows-x86_64-mingw"
```

Then `liberaqt doctor` -- it checks every installed agent really carries its own plugin key and
marks a stale build `STALE`. One plugin key cannot serve two ABIs, and getting that wrong fails
completely silently.

## Verify against a real application

**Nothing in the repository will exercise your handler.** Every agent bug this project has had
was found by driving real software, and by nothing else.

```python
from liberaqt import liberaqt
with liberaqt() as lq:
    app = lq.launch(r"C:/Qt/6.7.3/mingw_64/bin/assistant.exe")
    print("registered:", "your.command" in app.capabilities["commands"])
    win = app.window(title="Qt Assistant")
    ...
```

Then the suite:

```bash
python -m pytest tests/e2e/qt -q --liberaqt-qt-bin "C:/Qt/6.7.3/mingw_64/bin"
python -m pytest tests/unit -q
python -m ruff check src tests
```

If the command is unregistered because it was never built -- rather than never written -- check
`LIBERAQT_HAVE_QUICK` before assuming the handler is missing. CI's Qt 5.15 legs install no
`qtdeclarative`, so those agents have no Quick support at all.

## Do not look for these

Older text in `docs/CONTRIBUTING.md` asks for a "conformance suite entry" and "a test against
the sample app". Neither exists, and neither should: there is deliberately no sample application,
because a toy agrees with whatever the driver happens to do.
