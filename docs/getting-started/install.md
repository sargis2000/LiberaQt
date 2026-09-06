# Install

Two pieces have to be in place: the **Python client**, and an **agent binary** whose ABI matches
the application you want to drive. The client is easy. The agent is where the care goes.

## 1. The Python client

Needs **Python 3.9 or newer**. The client has no Qt dependency at all — that is deliberate, and it
is what lets its own unit tests run on a machine with no Qt installed.

!!! tip "Use a virtual environment"
    LiberaQT installs a `liberaqt` command and a pytest plugin, both of which you want scoped to
    a project rather than to the whole machine.

    === "Windows"

        ```powershell
        python -m venv .venv
        .venv\Scripts\activate
        ```

    === "Linux / macOS"

        ```bash
        python3 -m venv .venv
        source .venv/bin/activate
        ```

### Pick the way that matches what you are doing

=== "Use it in your own tests"

    Straight from the repository — no checkout to manage:

    ```bash
    pip install "liberaqt[yaml] @ git+https://github.com/sargis2000/LiberaQt.git"
    ```

    Pin a revision so a later change cannot alter your test runs:

    ```bash
    pip install "liberaqt @ git+https://github.com/sargis2000/LiberaQt.git@<commit-sha>"
    ```

=== "Work on LiberaQT itself"

    An editable install from a checkout, with the development tools:

    ```bash
    git clone https://github.com/sargis2000/LiberaQt.git
    cd LiberaQt
    pip install -e ".[dev]"
    ```

    Editable means your edits take effect without reinstalling — but see the warning below,
    which catches people out.

=== "Install a checkout, not linked"

    A normal install from source, for when you want a fixed copy rather than a live one:

    ```bash
    pip install .
    ```

=== "Offline or air-gapped"

    Build a wheel once, carry it in, install it anywhere:

    ```bash
    pip wheel . --no-deps -w dist/
    pip install dist/liberaqt-*.whl
    ```

!!! note "Not on PyPI yet"
    `pip install liberaqt` does not work — nothing is published under that name. Use one of the
    forms above. When a release is published this page will say so.

### Optional extras

| Extra | Install | What it adds |
|-------|---------|--------------|
| `yaml` | `pip install "liberaqt[yaml] @ git+..."` | [object maps](../guide/object-maps.md) — needed for `win.obj("name")` |
| `dev` | `pip install -e ".[dev]"` | pytest, ruff, mypy — for working on LiberaQT |
| `docs` | `pip install -e ".[docs]"` | mkdocs, for [`liberaqt docs`](../guide/cli.md#docs) |

Combine them: `pip install -e ".[dev,docs]"`.

!!! warning "Re-run the install after moving or renaming anything under `src/`"
    The package registers a `pytest11` entry point. If the metadata is installed but the module
    is not importable, **pytest dies during startup**, before collecting a single test:

    ```
    ModuleNotFoundError: No module named 'liberaqt'
    ```

    It looks nothing like an install problem. `pip install -e ".[dev]"` fixes it.

### Check it worked

```bash
liberaqt --version
liberaqt doctor
```

`doctor` prints the client version, your Python, the agent cache location, and every agent
installed. With no agents yet it will say so — that is step 2.

## 2. An agent for your application's ABI

The agent is a Qt plugin loaded into the application under test, so it must match:

- the application's Qt **minor** version — 6.7, not 6.8;
- its **compiler and standard library** — an MSVC agent will not load into a MinGW application;
- its **CPU architecture** — a 32-bit application needs a 32-bit agent.

Get all three from the application itself rather than guessing:

```bash
liberaqt doctor "C:\Program Files\YourApp\yourapp.exe"
```

```title="Output"
target   C:\Program Files\YourApp\yourapp.exe
  detected Qt: 5.15.1, msvc2019, x86 (dynamic linkage)
  NO MATCHING AGENT
  no liberaqt agent for Qt 5.15 on windows-x86 (installed: qt6.7-windows-x86_64-mingw)
```

None of that is guessed: linkage, version and compiler are read from the `QLibraryInfo::build()`
stamp that Qt writes into whichever binary carries QtCore.

### Installing a prebuilt agent

```bash
liberaqt agents install --qt 5.15 --compiler msvc2019
```

!!! note "There is no public download location yet"
    Point `LIBERAQT_AGENT_BASE_URL` at any location holding `<tag>.zip` — a releases page, an
    internal file share, or a directory on disk — or pass one archive directly:

    ```bash
    export LIBERAQT_AGENT_BASE_URL=https://example.internal/liberaqt-agents
    liberaqt agents install --tag qt5.15-windows-x86-msvc2019

    # or, one-off:
    liberaqt agents install --tag qt5.15-windows-x86-msvc2019 --from ./agent.zip
    ```

    A published `<tag>.zip.sha256` is verified when present. The install is refused outright if
    the unpacked agent does not advertise its architecture's plugin key — see
    [Child processes](../guide/child-processes.md#one-plugin-key-cannot-serve-two-abis) for why
    that matters.

### Building an agent from source

Until releases are published, this is the normal path — and there is a command for it, so you do
not have to get the toolchain incantation right by hand:

```bash
liberaqt agents kits                                    # what this machine can build from
liberaqt agents build --tag qt6.7-windows-x86_64-mingw  # build, install, verify
liberaqt agents build --qt 6.7 --compiler mingw         # or narrow without the full tag
liberaqt agents build --all                             # every kit found
liberaqt agents build --tag ... --dry-run               # print the commands, run nothing
```

`agents kits` lists every desktop Qt kit it can find, marking the ones you already have an agent
for:

```title="Output"
TAG                                    AGENT        KIT
qt5.15-windows-x86-msvc2019            installed    C:\Qt\5.15.0\msvc2019
qt5.15-windows-x86_64-mingw            -            C:\Qt\5.15.0\mingw81_64
qt6.7-windows-x86_64-mingw             installed    C:\Qt\6.7.3\mingw_64
```

It is worth preferring over running cmake yourself, because it removes three ways to get this
silently wrong:

- **the tag is derived from the kit that was configured**, not typed — Qt names its 32-bit MSVC
  kit `msvc2019` and its 64-bit one `msvc2019_64`, and an agent installed under the wrong tag is
  handed to an application that cannot load it;
- **MinGW is matched to the kit's own version** — Qt 5.15's `mingw81_64` kit was built with MinGW
  8.1, and building its agent with MinGW 11 links two incompatible standard libraries;
- **`vcvarsall` runs in the same shell as cmake**, through a generated batch file, which is the
  part that cannot be done from PowerShell at all.

Afterwards the installed binary is checked for the plugin key naming its own ABI, so a build that
picked up the wrong metadata fails immediately instead of surfacing later as a child process that
mysteriously has no agent.

#### Doing it by hand

The same steps, if you would rather run them yourself or need a flag the command does not expose.

=== "Linux / GCC"

    ```bash
    cmake -S agent -B build/agent -DCMAKE_PREFIX_PATH=$QTDIR -DCMAKE_BUILD_TYPE=Release
    cmake --build build/agent --parallel
    cmake --install build/agent \
        --prefix ~/.cache/liberaqt/agents/qt6.7-linux-x86_64-gcc
    ```

=== "Windows / MinGW"

    ```powershell
    $env:PATH = "C:\Qt\Tools\mingw1120_64\bin;C:\Qt\6.7.3\mingw_64\bin;$env:PATH"
    cmake -S agent -B build/agent -G Ninja `
          -DCMAKE_PREFIX_PATH=C:/Qt/6.7.3/mingw_64 `
          -DCMAKE_BUILD_TYPE=Release `
          -DCMAKE_CXX_COMPILER=C:/Qt/Tools/mingw1120_64/bin/g++.exe
    cmake --build build/agent --parallel
    cmake --install build/agent `
          --prefix "$env:LOCALAPPDATA\liberaqt\agents\qt6.7-windows-x86_64-mingw"
    ```

=== "Windows / MSVC 32-bit"

    `vcvarsall.bat` sets the environment for the shell it runs in, so run this from `cmd` or a
    `.bat` file — not from PowerShell.

    ```bat
    call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvarsall.bat" x86
    cmake -S agent -B build/agent-msvc32 -G Ninja ^
          -DCMAKE_PREFIX_PATH=C:/Qt/5.15.0/msvc2019 -DCMAKE_BUILD_TYPE=Release
    cmake --build build/agent-msvc32 --parallel
    cmake --install build/agent-msvc32 ^
          --prefix "%LOCALAPPDATA%\liberaqt\agents\qt5.15-windows-x86-msvc2019"
    ```

    Qt names its 32-bit kit without a suffix: `msvc2019` is 32-bit, `msvc2019_64` is 64-bit.
    MSVC toolsets v140–v143 are binary compatible, so VS2022's compiler links fine against a Qt
    built with 2019.

=== "Windows / MSVC 64-bit"

    ```bat
    call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvarsall.bat" x64
    cmake -S agent -B build/agent-msvc64 -G Ninja ^
          -DCMAKE_PREFIX_PATH=C:/Qt/5.15.0/msvc2019_64 -DCMAKE_BUILD_TYPE=Release
    cmake --build build/agent-msvc64 --parallel
    cmake --install build/agent-msvc64 ^
          --prefix "%LOCALAPPDATA%\liberaqt\agents\qt5.15-windows-x86_64-msvc2019"
    ```

!!! tip "Two rules that save an hour"
    - **Use a separate build directory per ABI.** Two configurations cannot share one.
    - **Always `-G Ninja` on Windows.** The "MinGW Makefiles" generator chokes on drive-letter
      colons.

## 3. Where agents live

| Platform | Location |
|----------|----------|
| Windows | `%LOCALAPPDATA%\liberaqt\agents\` |
| Linux, macOS | `$XDG_CACHE_HOME/liberaqt/agents/`, else `~/.cache/liberaqt/agents/` |

`LIBERAQT_AGENT_PATH` overrides the cache with a directory of `<tag>/` install prefixes, which is
useful in CI.

The tag format is `qt<minor>-<platform>-<arch>-<compiler>`, and the install layout **must** be:

```
<prefix>/plugins/generic/liberaqt.dll     # or libliberaqt.so
```

`QT_PLUGIN_PATH` is pointed at `<prefix>/plugins`, so a plugin anywhere else is simply not found.

!!! danger "Agents are not versioned with the repository"
    They live in the cache, so pulling a new revision of the source does **not** update them.
    After changing anything in `agent/`, rebuild every ABI you use. `liberaqt doctor` flags an
    agent that predates the per-ABI plugin keys as `STALE`.

## 4. Verify

```bash
liberaqt doctor "C:\Program Files\YourApp\yourapp.exe"
```

```title="Output"
liberaqt 0.1.0.dev0  (protocol 1)
python   3.12.7  on windows-x86_64
cache    C:\Users\you\AppData\Local\liberaqt
agents installed:
  - qt5.15-windows-x86-msvc2019  ->  ...\plugins\generic\liberaqt.dll
  - qt6.7-windows-x86_64-mingw   ->  ...\plugins\generic\liberaqt.dll
target   C:\Program Files\YourApp\yourapp.exe
  detected Qt: 5.15.1, msvc2019, x86 (dynamic linkage)
  matching agent: qt5.15-windows-x86-msvc2019

OK
```

You are ready for [your first test](first-test.md).
