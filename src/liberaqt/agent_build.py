# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Sargis Khachatryan
"""Discover the Qt kits on this machine, and build an agent for one.

An agent is a Qt plugin, so there is one binary per Qt minor version, compiler and architecture --
and picking the wrong combination fails *silently*, which is why this exists rather than a page of
copy-paste cmake. Two mistakes in particular are easy to make by hand and impossible to see
afterwards:

* installing a build under a tag that does not describe it, so the registry hands it to an
  application it cannot load;
* on Windows, building a MinGW agent with a different MinGW than the Qt kit was built with.

So the tag is *derived* from the kit that was actually configured, never taken on trust, and the
installed binary is checked for the plugin key naming its own architecture before the command
reports success.

The kit layout this reads is Qt's own installer layout::

    C:/Qt/6.7.3/mingw_64          <- <root>/<version>/<kit>
    C:/Qt/5.15.0/msvc2019
    /opt/Qt/6.7.2/gcc_64
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import agent_registry
from .errors import LiberaQtError

#: Where Qt installs itself by default, in the order they are searched.
DEFAULT_QT_ROOTS = (
    Path("C:/Qt"),
    Path("/opt/Qt"),
    Path.home() / "Qt",
)

#: Kit directory prefixes that are not desktop targets. An agent for them would never be loaded
#: by anything this project can drive.
SKIPPED_KITS = ("android", "wasm", "winrt", "ios", "Src", "Tools")

#: Visual Studio editions, newest-looking first, under both Program Files roots.
_VS_EDITIONS = ("BuildTools", "Community", "Professional", "Enterprise")

#: Kit directory name -> (compiler, architecture). Ordered: the first match wins, so the more
#: specific patterns come first.
_KIT_PATTERNS = (
    (re.compile(r"^msvc(?P<year>\d{4})_arm64$"), "msvc{year}", "arm64"),
    (re.compile(r"^msvc(?P<year>\d{4})_64$"), "msvc{year}", "x86_64"),
    (re.compile(r"^msvc(?P<year>\d{4})$"), "msvc{year}", "x86"),
    (re.compile(r"^llvm-mingw(?P<ver>\d*)_64$"), "llvm-mingw", "x86_64"),
    (re.compile(r"^mingw(?P<ver>\d*)_64$"), "mingw", "x86_64"),
    (re.compile(r"^mingw(?P<ver>\d*)_32$"), "mingw", "x86"),
    (re.compile(r"^gcc_64$"), "gcc", "x86_64"),
    (re.compile(r"^gcc_arm64$"), "gcc", "arm64"),
    (re.compile(r"^clang_64$"), "clang", "x86_64"),
    (re.compile(r"^macos$"), "clang", "x86_64"),
)

#: Line ending for a generated batch file. cmd is happiest with CRLF, and writing it as a
#: named constant keeps the escape out of an f-string that is already quote-heavy.
_BAT_EOL = "\r\n"

#: Architecture -> the argument vcvarsall.bat wants.
_VCVARS_ARCH = {"x86": "x86", "x86_64": "x64", "arm64": "arm64"}


class AgentBuildError(LiberaQtError):
    """A kit could not be found, or building an agent from it failed."""

    retryable = False


@dataclass(frozen=True)
class QtKit:
    """One installed Qt kit an agent can be built against.

    Attributes:
        prefix: The kit directory, which is what ``CMAKE_PREFIX_PATH`` is pointed at.
        qt: Qt minor version, e.g. ``"6.7"``.
        platform_tag: Platform and architecture, e.g. ``"windows-x86_64"``.
        compiler: Toolchain as it appears in an agent tag, e.g. ``"msvc2019"``, ``"mingw"``.
    """

    prefix: Path
    qt: str
    platform_tag: str
    compiler: str

    @property
    def tag(self) -> str:
        """The agent tag a build from this kit must be installed under."""
        return f"qt{self.qt}-{self.platform_tag}-{self.compiler}"

    @property
    def arch(self) -> str:
        """Architecture alone, e.g. ``"x86_64"``."""
        return self.platform_tag.rsplit("-", 1)[-1]

    @property
    def is_msvc(self) -> bool:
        """Whether building this kit needs a Visual Studio environment."""
        return self.compiler.startswith("msvc")

    def __str__(self) -> str:
        return self.tag


def qt_minor(version: str) -> str:
    """Reduce a full Qt version to the minor version an agent is keyed by.

    A plugin must match the host's Qt *minor* version -- 6.7, not 6.8 -- while the patch level
    does not matter, so ``6.7.3`` and ``6.7.1`` share one agent.

    Args:
        version: A directory name such as ``"6.7.3"``.

    Returns:
        ``"6.7"``. An unrecognisable name is returned unchanged.
    """
    parts = version.split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else version


def parse_kit_dir(name: str) -> tuple[str, str] | None:
    """Read a kit directory name as a compiler and architecture.

    Qt names its 32-bit MSVC kit without a suffix -- ``msvc2019`` is 32-bit and ``msvc2019_64``
    is 64-bit -- which is the single most common way to install an agent under the wrong tag.

    Args:
        name: A kit directory name such as ``"mingw_64"`` or ``"msvc2019"``.

    Returns:
        ``(compiler, architecture)``, or None if this is not a desktop kit.
    """
    if name.startswith(SKIPPED_KITS):
        return None
    for pattern, compiler, arch in _KIT_PATTERNS:
        match = pattern.match(name)
        if match:
            return compiler.format(**match.groupdict()), arch
    return None


def discover_kits(roots: list[Path] | None = None) -> list[QtKit]:
    """Every desktop Qt kit installed on this machine.

    Args:
        roots: Directories holding ``<version>/<kit>`` trees. Defaults to the usual install
            locations, plus ``$QTDIR``'s grandparent when that is set.

    Returns:
        Kits sorted by Qt version then tag. Directories without a ``lib/cmake`` are skipped, so a
        half-removed kit does not turn into a build that cannot configure.
    """
    search = list(roots) if roots is not None else list(DEFAULT_QT_ROOTS)
    platform = "windows" if sys.platform.startswith("win") else (
        "linux" if sys.platform.startswith("linux") else "macos")

    found: list[QtKit] = []
    for root in search:
        if not root.is_dir():
            continue
        for version_dir in sorted(root.iterdir()):
            if not version_dir.is_dir() or not version_dir.name[:1].isdigit():
                continue
            for kit_dir in sorted(version_dir.iterdir()):
                if not kit_dir.is_dir():
                    continue
                parsed = parse_kit_dir(kit_dir.name)
                if parsed is None or not (kit_dir / "lib" / "cmake").is_dir():
                    continue
                compiler, arch = parsed
                found.append(QtKit(prefix=kit_dir, qt=qt_minor(version_dir.name),
                                   platform_tag=f"{platform}-{arch}", compiler=compiler))
    return sorted(found, key=lambda k: (k.qt, k.tag))


def find_kit(qt: str | None = None, compiler: str | None = None, arch: str | None = None,
             tag: str | None = None, roots: list[Path] | None = None) -> QtKit:
    """Pick exactly one installed kit, or explain what is available instead.

    Args:
        qt: Qt minor version to require, e.g. ``"6.7"``.
        compiler: Compiler to require, e.g. ``"mingw"``.
        arch: Architecture to require, e.g. ``"x86"``.
        tag: A full agent tag, which fixes all three at once.
        roots: Passed to :func:`discover_kits`.

    Returns:
        The one matching kit.

    Raises:
        AgentBuildError: Nothing matched, or several did.
    """
    kits = discover_kits(roots)
    matches = [
        k for k in kits
        if (tag is None or k.tag == tag)
        and (qt is None or k.qt == qt)
        and (compiler is None or k.compiler == compiler)
        and (arch is None or k.arch == arch)
    ]
    if len(matches) == 1:
        return matches[0]

    available = "\n".join(f"  {k.tag:38} {k.prefix}" for k in kits) or "  (none found)"
    if not matches:
        raise AgentBuildError(
            "no installed Qt kit matches that request",
            hint=f"Installed kits:\n{available}",
        )
    ambiguous = "\n".join(f"  {k.tag:38} {k.prefix}" for k in matches)
    raise AgentBuildError(
        f"{len(matches)} kits match; narrow it with --tag",
        hint=f"Matching:\n{ambiguous}",
    )


def find_vcvarsall() -> Path | None:
    """Locate ``vcvarsall.bat``.

    Returns:
        Its path, or None when no Visual Studio is installed.
    """
    roots = [Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
             Path(os.environ.get("ProgramFiles", r"C:\Program Files"))]
    for root in roots:
        base = root / "Microsoft Visual Studio"
        if not base.is_dir():
            continue
        for year in sorted((d for d in base.iterdir() if d.is_dir()), reverse=True):
            for edition in _VS_EDITIONS:
                candidate = year / edition / "VC" / "Auxiliary" / "Build" / "vcvarsall.bat"
                if candidate.is_file():
                    return candidate
    return None


def find_mingw(kit: QtKit, tools_root: Path | None = None) -> Path | None:
    """Find the MinGW ``bin`` directory to build a MinGW kit with.

    Matching the *version* matters: Qt 5.15's ``mingw81_64`` kit was built with MinGW 8.1, and
    linking an agent for it with MinGW 11 mixes two incompatible C++ standard libraries. So a kit
    whose name carries a version prefix is matched against the tools of that version, and only a
    kit with no version in its name (Qt 6's plain ``mingw_64``) takes the newest available.

    Args:
        kit: The kit being built.
        tools_root: Qt's ``Tools`` directory. Defaults to the kit's own installation.

    Returns:
        The ``bin`` directory containing ``g++``, or None if none was found.
    """
    tools = tools_root or (kit.prefix.parent.parent / "Tools")
    if not tools.is_dir():
        return None

    wanted_bits = "32" if kit.arch == "x86" else "64"
    kit_version = re.sub(r"[^\d]", "", kit.prefix.name.split("_")[0])  # mingw81_64 -> "81"

    def version_of(entry: Path) -> int:
        """The tool's version as a number.

        Sorting these as text is wrong: ``mingw810`` sorts above ``mingw1120`` because
        ``'8' > '1'``, which picks MinGW 8.1 as the newest.
        """
        digits = re.sub(r"[^\d]", "", entry.name.split("_")[0])
        return int(digits) if digits else 0

    candidates = []
    for entry in tools.iterdir():
        if not entry.is_dir() or not entry.name.startswith("mingw"):
            continue
        if not entry.name.endswith(f"_{wanted_bits}"):
            continue
        if not (entry / "bin" / "g++.exe").is_file():
            continue
        candidates.append(entry)
    candidates.sort(key=version_of, reverse=True)

    if kit_version:
        for entry in candidates:
            if re.sub(r"[^\d]", "", entry.name.split("_")[0]).startswith(kit_version):
                return entry / "bin"
        return None      # a versioned kit with no matching tools is a refusal, not a fallback
    return (candidates[0] / "bin") if candidates else None


def source_dir(explicit: str | None = None) -> Path:
    """Locate the ``agent/`` source tree.

    Args:
        explicit: A directory given on the command line, which wins.

    Returns:
        The directory containing ``CMakeLists.txt``.

    Raises:
        AgentBuildError: It could not be found, which is normal for a non-editable install.
    """
    if explicit:
        candidate = Path(explicit)
        if (candidate / "CMakeLists.txt").is_file():
            return candidate
        raise AgentBuildError(f"{candidate} has no CMakeLists.txt")

    for base in (Path.cwd(), Path(__file__).resolve().parents[2]):
        candidate = base / "agent"
        if (candidate / "CMakeLists.txt").is_file():
            return candidate
    raise AgentBuildError(
        "could not find the agent/ source tree",
        hint="Run from a source checkout, or pass --source <dir>. A wheel does not ship it.",
    )


@dataclass
class BuildPlan:
    """What building one agent will actually run.

    Kept separate from running it so the commands can be shown with ``--dry-run`` and asserted on
    in tests without a compiler present.

    Attributes:
        kit: The kit being built against.
        build_dir: Where cmake will work. One per ABI: two configurations cannot share one.
        prefix: Where the finished agent is installed.
        steps: Commands to run in order.
        env: Environment additions, used by the MinGW path.
        script: A batch file to run instead of ``steps``, used for MSVC. Quoting a call to
            ``vcvarsall.bat`` through an argument list does not survive ``cmd``'s parsing -- the
            inner quotes come back escaped -- and a file also lets a failed build be inspected.
    """

    kit: QtKit
    build_dir: Path
    prefix: Path
    steps: list[list[str]]
    env: dict[str, str]
    script: str | None = None


def plan_build(kit: QtKit, source: Path, build_dir: Path | None = None,
               prefix: Path | None = None) -> BuildPlan:
    """Work out the commands that build and install an agent for a kit.

    Args:
        kit: The kit to build against.
        source: The ``agent/`` source directory.
        build_dir: Build directory. Defaults to ``build/agent-<tag>``, which keeps each ABI's
            configuration apart as cmake requires.
        prefix: Install prefix. Defaults to the cache entry for the kit's tag.

    Returns:
        The plan.

    Raises:
        AgentBuildError: The toolchain this kit needs is not installed.
    """
    build_dir = build_dir or Path("build") / f"agent-{kit.tag}"
    prefix = prefix or (agent_registry.cache_dir() / "agents" / kit.tag)

    configure = [
        "cmake", "-S", str(source), "-B", str(build_dir), "-G", "Ninja",
        f"-DCMAKE_PREFIX_PATH={kit.prefix.as_posix()}",
        "-DCMAKE_BUILD_TYPE=Release",
    ]
    build = ["cmake", "--build", str(build_dir), "--parallel"]
    install = ["cmake", "--install", str(build_dir), "--prefix", str(prefix)]
    env: dict[str, str] = {}

    if kit.is_msvc:
        vcvarsall = find_vcvarsall()
        if vcvarsall is None:
            raise AgentBuildError(
                f"{kit.tag} needs Visual Studio, which is not installed",
                hint="Install the VS Build Tools with the C++ workload.",
            )
        arch = _VCVARS_ARCH.get(kit.arch)
        if arch is None:
            raise AgentBuildError(f"no vcvarsall architecture for {kit.arch}")
        # vcvarsall sets the environment for the shell it runs in, so every step has to happen
        # inside that one shell. This is the part people get wrong by hand -- it cannot be done
        # from PowerShell at all. It goes in a batch file rather than an argument list because
        # cmd re-parses the latter and the quotes around the path do not survive.
        lines = ["@echo off", f'call "{vcvarsall}" {arch} || exit /b 1']
        lines += [subprocess.list2cmdline(step) + " || exit /b 1"
                  for step in (configure, build, install)]
        return BuildPlan(kit=kit, build_dir=build_dir, prefix=prefix, steps=[],
                         env=env, script=_BAT_EOL.join(lines) + _BAT_EOL)
    else:
        if kit.compiler in ("mingw", "llvm-mingw"):
            mingw_bin = find_mingw(kit)
            if mingw_bin is None:
                raise AgentBuildError(
                    f"no MinGW matching {kit.prefix.name} was found under Qt's Tools directory",
                    hint="Qt 5.15's mingw81 kits need MinGW 8.1; mixing versions mixes two "
                         "incompatible standard libraries.",
                )
            configure.append(f"-DCMAKE_CXX_COMPILER={(mingw_bin / 'g++.exe').as_posix()}")
            env["PATH"] = os.pathsep.join(
                [str(mingw_bin), str(kit.prefix / "bin"), os.environ.get("PATH", "")])
        steps = [configure, build, install]

    return BuildPlan(kit=kit, build_dir=build_dir, prefix=prefix, steps=steps, env=env)


def run_plan(plan: BuildPlan, echo: bool = True) -> Path:
    """Execute a build plan and verify what it installed.

    Args:
        plan: From :func:`plan_build`.
        echo: Whether to print each command before running it.

    Returns:
        The install prefix.

    Raises:
        AgentBuildError: A step failed, or the installed agent is not usable.
    """
    if shutil.which("cmake") is None:
        raise AgentBuildError("cmake is not on PATH")
    if shutil.which("ninja") is None:
        raise AgentBuildError(
            "ninja is not on PATH",
            hint="Always -G Ninja here: the MinGW Makefiles generator chokes on drive-letter "
                 "colons. Install it with `pip install ninja`.",
        )

    environ = dict(os.environ)
    environ.update(plan.env)

    if plan.script is not None:
        script_path = Path(tempfile.mkdtemp(prefix="liberaqt-build-")) / "build.bat"
        script_path.write_text(plan.script, encoding="utf-8")
        if echo:
            for line in plan.script.splitlines():
                if line and not line.startswith("@echo"):
                    print(f"  $ {line.removesuffix(' || exit /b 1')}")
        result = subprocess.run(["cmd", "/c", str(script_path)], env=environ, check=False)
        if result.returncode != 0:
            raise AgentBuildError(
                f"the build failed with exit code {result.returncode}",
                hint=f"The script is at {script_path} if you want to run it by hand. A locked "
                     "DLL is a common cause on Windows: close any application still running "
                     "with the agent loaded, since cmake --install cannot overwrite it.",
            )
        return verify_installed(plan.kit.tag)

    for step in plan.steps:
        if echo:
            print(f"  $ {subprocess.list2cmdline(step)}")
        result = subprocess.run(step, env=environ, check=False)
        if result.returncode != 0:
            raise AgentBuildError(
                f"{subprocess.list2cmdline(step[:2])} failed with exit code {result.returncode}",
                hint="A locked DLL is a common cause on Windows: close any application still "
                     "running with the agent loaded, since cmake --install cannot overwrite it.",
            )
    return verify_installed(plan.kit.tag)


def verify_installed(tag: str) -> Path:
    """Check that a freshly installed agent is one the launcher can actually use.

    Args:
        tag: The tag it was installed under.

    Returns:
        The install prefix.

    Raises:
        AgentBuildError: Nothing was installed under that tag, or the binary does not advertise
            the plugin key naming its own architecture.
    """
    build = next((b for b in agent_registry.installed() if str(b) == tag), None)
    if build is None or not build.exists():
        raise AgentBuildError(f"nothing was installed under the tag {tag}")
    if not build.advertises_abi_key():
        raise AgentBuildError(
            f"the agent installed as {tag} does not advertise the {build.abi_key} plugin key",
            hint="CMake generates the metadata into liberaqt_plugin.json at configure time, so a "
                 "stale build directory can carry another ABI's key. Delete it and configure "
                 "again rather than trusting an incremental build.",
        )
    return build.root
