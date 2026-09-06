# Command line

```
liberaqt [-h] [--version] {doctor,agents,inspect,record,run} ...
```

## doctor

**The first thing to run against any new target**, and the first thing to run when something does
not work.

```bash
liberaqt doctor                      # the environment and installed agents
liberaqt doctor "C:\Path\app.exe"    # ...and whether that binary can be driven
```

It classifies a binary into three outcomes, and the distinction matters:

| Outcome | Meaning |
|---------|---------|
| **dynamic Qt** | injectable — it then reports whether a matching agent exists, and names the toolchain when one does not |
| **static Qt** | *never* injectable by any mode: no plugin loader, and a second Qt in one process is undefined behaviour |
| **no Qt** | not a Qt application |

None of it is guessed: linkage, version and compiler come from the `QLibraryInfo::build()` stamp
Qt writes into whichever binary carries QtCore.

`doctor` also flags an agent that predates the per-ABI plugin keys:

```
  - qt5.15-windows-x86-msvc2019  ->  ...\liberaqt.dll   [STALE: no liberaqt_32 key]
```

!!! note "A fourth outcome doctor cannot see"
    A dynamically-linked Qt process that never constructs a `QGuiApplication` reads the injection
    environment and acts on none of it. See
    [Child processes](child-processes.md#when-a-child-cannot-be-reached).

## agents

```bash
liberaqt agents list
liberaqt agents install --qt 5.15 --compiler msvc2019
liberaqt agents install --tag qt5.15-windows-x86-msvc2019 --from ./agent.zip
liberaqt agents remove qt6.7-windows-x86_64-mingw
```

| Option | Meaning |
|--------|---------|
| `--qt`, `--compiler` | build the tag from parts, using the current platform |
| `--tag` | give the full tag instead |
| `--from URL\|PATH` | install this archive, ignoring the base URL |
| `--base-url URL` | where `<tag>.zip` lives; defaults to `$LIBERAQT_AGENT_BASE_URL` |

A published `<tag>.zip.sha256` is verified when present. The install is refused if the unpacked
agent does not advertise its architecture's plugin key.

### kits

What this machine can build an agent from, and whether one is installed already:

```bash
liberaqt agents kits
```

```title="Output"
TAG                                    AGENT        KIT
qt5.15-windows-x86-msvc2019            installed    C:\Qt\5.15.0\msvc2019
qt5.15-windows-x86_64-mingw            installed    C:\Qt\5.15.0\mingw81_64
qt6.7-windows-x86_64-mingw             installed    C:\Qt\6.7.3\mingw_64
qt6.7-windows-x86_64-msvc2022          -            C:\Qt\6.7.3\msvc2022_64
```

Android, WebAssembly and WinRT kits are skipped — nothing this project drives could load an agent
for them.

### build

Builds an agent from an installed kit and puts it in the cache:

```bash
liberaqt agents build --tag qt5.15-windows-x86-msvc2019
liberaqt agents build --qt 6.7 --compiler mingw
liberaqt agents build --all
liberaqt agents build --tag ... --dry-run      # print the commands, run nothing
```

| Option | Meaning |
|--------|---------|
| `--tag` | the kit to build, from `agents kits` |
| `--qt`, `--compiler`, `--arch` | narrow to one kit without naming the full tag |
| `--all` | build for every kit found |
| `--source DIR` | the `agent/` source tree, if it is not alongside |
| `--prefix DIR` | install somewhere other than the cache |
| `--dry-run` | show the exact commands |

It is worth more than a shell alias because it removes three ways to get this silently wrong:

- **The tag is derived from the kit that was configured**, never taken on trust. Qt names its
  32-bit MSVC kit `msvc2019` and its 64-bit one `msvc2019_64`, and an agent installed under the
  wrong tag is handed to an application that cannot load it.
- **MinGW is matched by version.** Qt 5.15's `mingw81_64` kit was built with MinGW 8.1, and
  building its agent with MinGW 11 links two incompatible standard libraries. A versioned kit
  with no matching toolchain is refused rather than substituted.
- **`vcvarsall` runs in the same shell as cmake**, via a generated batch file. It sets the
  environment only for the shell it runs in, so it cannot be invoked from PowerShell, and its
  quoted path does not survive being passed through an argument list.

Afterwards the installed binary is checked for the plugin key naming its own architecture, so a
moc mis-selection fails the build instead of surfacing later as a child process that mysteriously
has no agent.

!!! tip "Close the application first"
    `cmake --install` cannot overwrite a DLL that a running application has loaded, and on
    Windows it fails in a way that is easy to read as success.

## inspect

Launches an application and shows how to address the objects in it. **Use this before writing
selectors**, not after they fail.

```bash
liberaqt inspect "C:\Path\app.exe"
liberaqt inspect "C:\Path\app.exe" --depth 4
liberaqt inspect "C:\Path\app.exe" --json
liberaqt inspect "C:\Path\app.exe" --all
liberaqt inspect "C:\Path\app.exe" --validate objects.yaml
liberaqt inspect "C:\Path\app.exe" --interactive
```

| Option | Effect |
|--------|--------|
| `--depth N` | how deep to walk the tree |
| `--json` | dump the raw object tree instead of ranked selectors |
| `--all` | include Qt's internal objects (`qt_scrollarea_viewport` and friends) |
| `--validate MAP` | check every entry in an [object map](object-maps.md) still resolves uniquely |
| `--interactive`, `-i` | drop into a REPL with `app` and `win` bound |
| `--qt VERSION` | force a Qt version instead of detecting it |
| `--trace` | log every protocol message |

`--interactive` is the fastest way to explore an unfamiliar application:

```python
>>> win.locator("QDockWidget").count
5
>>> [d.text for d in win.locator("QDockWidget").all()]
['Index', 'Contents', 'Search', 'Bookmarks', 'Open Pages']
>>> win.locator("QDockWidget#IndexWindow").highlight()
```

`highlight()` draws a box over the object for a moment — the quickest way to confirm you have the
one you meant.

Arguments after the executable are passed to the application:

```bash
liberaqt inspect "C:\Path\app.exe" --depth 3 -- --some-app-flag
```

## docs

Serves this documentation for local reading:

```bash
liberaqt docs                      # http://127.0.0.1:8000/LiberaQt/
liberaqt docs --port 9000 --open   # a different port, and open a browser
liberaqt docs --build              # build into site/ and exit
liberaqt docs --host 0.0.0.0       # reachable from another machine
```

| Option | Effect |
|--------|--------|
| `--host` | address to bind (default `127.0.0.1`) |
| `--port` | port to bind (default `8000`) |
| `--open` | open a browser at the served address |
| `--build` | build into `site/` and exit, instead of serving |
| `--source` | directory holding `mkdocs.yml`, if it is not alongside |

With the `docs` extra installed it runs `mkdocs serve`, which **rebuilds a page as you edit it**.
Without it, it falls back to serving whatever was last built into `site/` over `http.server`, and
says so — no live reload, but you can still read the documentation without installing a builder.
`--build` has no fallback, because `http.server` can serve a site and cannot build one.

!!! note "The address is not the root"
    mkdocs mounts the site under the path in `site_url`, locally as well as when published, so
    this project serves at `/LiberaQt/`. The command prints the real address rather than the root,
    which merely redirects.

## run

A thin pytest wrapper that passes the configured executable through:

```bash
liberaqt run tests/
```

## record

```bash
liberaqt record "C:\Path\app.exe" --output test_recorded.py
```

!!! failure "Not working yet"
    The agent does not register `record.start`, so this fails immediately with an
    unsupported-operation error rather than recording nothing. The client, the code generator and
    the agent's event filter all exist; the wiring between them does not.

    It is designed to record **semantic** actions ("clicked this button", "committed this text"),
    not raw events, and not screen video.
