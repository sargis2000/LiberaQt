# Command line

```
liberaqt [-h] [--version] {doctor,agents,inspect,record,docs,run} ...
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
| `--base-url URL` | where `<tag>.zip` lives; defaults to `$LIBERAQT_AGENT_BASE_URL`, else the public releases page |

A published `<tag>.zip.sha256` is verified when present. The install is refused if the unpacked
agent does not advertise its architecture's plugin key.

With nothing configured, archives come from
`https://github.com/sargis2000/LiberaQt/releases/latest/download`, where CI attaches one per ABI
on every tagged release. Set `LIBERAQT_AGENT_BASE_URL` to host your own -- a vendor-specific
agent need not leave your network.

Not every ABI is published: Qt 5.15 msvc2015 and the arm64 kits have no runner to build them on.
For those, `liberaqt agents build` from a local Qt kit, which the install error names.

### update

Keeps installed agents in step with what has been published -- the answer to "the agent was
fixed and released; how do I get it?":

```bash
liberaqt agents update --check     # report only, downloads nothing; exits 1 if anything is behind
liberaqt agents update             # refresh whatever is behind
liberaqt agents update --tag qt6.5-windows-x86_64-mingw
```

```title="Output"
checking 4 agent(s) against https://github.com/sargis2000/LiberaQt/releases/latest/download
  qt6.5-windows-x86_64-mingw             update available   published archive differs from the installed one (a1b2c3d)
  qt6.7-windows-x86_64-mingw             local build        built here at 443bf85-dirty; `liberaqt agents build` to refresh
  qt5.15-windows-x86_64-msvc2015         cannot tell        nothing published for this ABI at that location
  qt6.7-windows-x86_64-msvc2022          not managed        lives in D:\ci\agents, outside the cache; left as it is
```

The comparison is against the 64-byte checksum published beside each archive, so checking every
ABI you have installed costs almost nothing. `--check` exits 1 when something is behind, so a CI
step can gate on it.

| State | Acted on? | Means |
|-------|-----------|-------|
| update available | yes | the published archive is not the one installed |
| damaged | yes | the binary on disk lacks its plugin key; reinstalling repairs it |
| current | -- | nothing to do |
| local build | never | you built it; nothing to compare against, and usually newer than a release |
| damaged (local) | never | your own build is broken; `liberaqt agents build` rebuilds it |
| not managed | never | it lives on `LIBERAQT_AGENT_PATH`, which is yours, not the cache's |
| cannot tell | -- | nothing is published for that ABI, or the install predates version stamping |
| could not check | -- | the location was unreachable, or served something that is not a checksum |

`liberaqt agents list` shows the same provenance as a table: tag, revision, and where each set
of bits came from. A tag on `LIBERAQT_AGENT_PATH` that shadows a cached copy is listed twice,
with the copy a launch does not use marked as shadowed.

#### The manifest

What `list` and `update` read is `liberaqt-agent.json`, at the root of every install prefix:

| Field | Written by | Meaning |
|-------|-----------|---------|
| `revision` | the build | `git describe` of the source it was built from |
| `qt`, `plugin_key`, `compiler`, `abi_bits`, `quick` | the build | what ABI the binary is |
| `agent_version`, `protocol`, `schema` | the build | versions |
| `source`, `archive_sha256` | `agents install` | where these bits came from |

An agent built before manifests existed has none, and reports its revision as `unknown`.
`liberaqt agents build --tag <tag>` gives it one.

Installing never replaces a working agent with a broken one: the archive is unpacked beside the
cache, checked, and only then swapped in. A refused install leaves whatever was there untouched.

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

Serves this documentation for local reading, or builds it:

```bash
liberaqt docs                           # http://127.0.0.1:8000/LiberaQt/
liberaqt docs serve --port 9000 --open  # a different port, and open a browser
liberaqt docs build                     # build and exit
liberaqt docs build --site-dir out      # build into an empty directory of your choosing
liberaqt docs --host 0.0.0.0            # reachable from another machine
```

| Option | Effect |
|--------|--------|
| `serve` / `build` | what to do. `serve` is the default, so bare `liberaqt docs` still serves |
| `--host` | address to bind (default `127.0.0.1`) |
| `--port` | port to bind (default `8000`); refused up front if something is already listening |
| `--open` | open a browser at the served address |
| `--build` | older spelling of `liberaqt docs build` |
| `--site-dir` | where `build` writes the HTML -- **erased first**, so it must be empty or a previous build |
| `--force` | let `build` erase a `--site-dir` that holds other files |
| `--source` | directory holding `mkdocs.yml`, for any mkdocs project |

**You do not need a checkout, but you do need the `docs` extra.** The pages travel inside the
wheel at `liberaqt/_docs/`, as Markdown; turning them into a site takes mkdocs, Material and
mkdocstrings. If any of those is missing the command names which, and prints an install command
that runs as printed -- through the Python you are running, for the extra's own packages, so it
never reinstalls or replaces LiberaQT itself.

When you are inside a LiberaQT checkout -- anywhere in it, `docs/` included -- the checkout's own
pages win, so editing `docs/` and serving them stays one step. Another project's `mkdocs.yml` in
the working directory is ignored, with a note; `--source .` builds it on purpose.

With the extra installed it runs `mkdocs serve`, which **rebuilds a page as you edit it**.
Without it, it falls back to serving a site this command built earlier, over `http.server`, and
says so -- no live reload. `build` has no fallback, because `http.server` can serve a site and
cannot build one.

!!! warning "`build` erases its destination"
    mkdocs empties the directory it builds into, sparing only hidden files. So `build` writes
    into a directory only if it is empty, or is a site it built itself -- every build leaves a
    hidden `.liberaqt-docs-build` marker behind to say so. Anything else is refused, naming the
    directory, rather than deleting your files; that includes `--site-dir .` in a project, and a
    folder that merely contains a `404.html`. A link is judged by what it points at.

    `--force` overrides that, for a directory whose contents really are disposable. Nothing
    overrides the refusal for a filesystem root, your home directory, or a directory above the
    one you are standing in.

!!! note "The address is not the root"
    mkdocs mounts the site under the path in `site_url`, locally as well as when published, so
    this project serves at `/LiberaQt/`. The command prints the real address rather than the root,
    which merely redirects -- and prints a wildcard bind like `0.0.0.0` as `localhost`, which is
    what a browser can open.

A built site links pages as folders (`ACTIONS/`), so open it through a web server -- `liberaqt
docs` serves one -- rather than by opening `index.html` from disk.

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
