"""Locate the agent binary whose ABI matches the application under test.

A Qt plugin must be built against the same Qt minor version and the same compiler/stdlib as the
host process. Guessing wrong means Qt silently refuses to load the plugin, which produces a
mystifying "agent never connected" failure. So: detect first, fail loudly with a fix command.
"""

from __future__ import annotations

import os
import platform
import re
import struct
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .errors import AgentMismatchError

SUPPORTED_QT = ("6.7", "5.15")


def cache_dir() -> Path:
    """Where downloaded agent binaries live.

    Honours ``LIBERAQT_CACHE``, then the platform convention: ``%LOCALAPPDATA%`` on Windows,
    ``XDG_CACHE_HOME`` elsewhere.

    Returns:
        The cache directory, which may not exist yet.
    """
    env = os.environ.get("LIBERAQT_CACHE")
    if env:
        return Path(env)
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "liberaqt"


@dataclass
class AgentBuild:
    """One installed agent binary, identified by the build it is compatible with.

    A Qt plugin must be built against the same Qt minor version and the same compiler and
    standard library as its host process, so all three parts matter when choosing one.

    Attributes:
        qt: Qt minor version, e.g. ``"6.7"``.
        platform_tag: Platform and architecture, e.g. ``"windows-x86_64"``.
        compiler: Toolchain, e.g. ``"gcc"``, ``"msvc2022"``, ``"mingw"``.
        root: Install prefix, containing ``plugins/generic/``.
    """

    qt: str                 # "6.7"
    platform_tag: str       # "linux-x86_64" | "windows-x86_64"
    compiler: str           # "gcc" | "msvc2019" | "msvc2022"
    root: Path              # contains plugins/generic/<lib>

    @property
    def plugin_dir(self) -> Path:
        """Directory to put on ``QT_PLUGIN_PATH``."""
        return self.root / "plugins"

    @property
    def library(self) -> Path:
        """Full path to the plugin binary, whether or not it exists."""
        name = "liberaqt.dll" if "windows" in self.platform_tag else "libliberaqt.so"
        return self.plugin_dir / "generic" / name

    def exists(self) -> bool:
        """Whether the plugin binary is actually present.

        Returns:
            True if the file exists.
        """
        return self.library.is_file()

    def __str__(self) -> str:
        return f"qt{self.qt}-{self.platform_tag}-{self.compiler}"


def current_platform_tag() -> str:
    """Platform tag for the machine we are running on.

    Returns:
        A tag such as ``"linux-x86_64"`` or ``"windows-x86_64"``, matching the naming used for
        installed agent directories.
    """
    machine = platform.machine().lower()
    machine = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "aarch64"}.get(machine, machine)
    if sys.platform.startswith("linux"):
        return f"linux-{machine}"
    if sys.platform == "win32":
        return f"windows-{machine}"
    if sys.platform == "darwin":
        return f"macos-{machine}"
    return f"{sys.platform}-{machine}"


# ------------------------------------------------------------------ AUT inspection

_QT_LIB_RE = re.compile(r"libQt(\d)(Core)\.so\.(\d+)\.(\d+)", re.I)
_QT_DLL_RE = re.compile(r"Qt(\d)Core\.dll", re.I)


def detect_qt_version(executable: str) -> str | None:
    """Best-effort detection of the Qt version an executable links against.

    Linux: parse ``ldd`` output. Windows: scan the PE import table for ``Qt5Core.dll`` /
    ``Qt6Core.dll`` and, when found, read the version resource of the resolved DLL.
    Returns e.g. ``"6.7"`` or None if it cannot tell.
    """
    exe = Path(executable)
    if not exe.exists():
        return None
    try:
        if sys.platform.startswith("linux"):
            out = subprocess.run(["ldd", str(exe)], capture_output=True, text=True,
                                 timeout=15).stdout
            for line in out.splitlines():
                m = _QT_LIB_RE.search(line)
                if m:
                    return f"{m.group(3)}.{m.group(4)}"
            # Fall back to reading the SONAME strings directly out of the binary.
            data = exe.read_bytes()
            m = _QT_LIB_RE.search(data.decode("latin-1", "ignore"))
            if m:
                return f"{m.group(3)}.{m.group(4)}"
        elif sys.platform == "win32":
            data = exe.read_bytes().decode("latin-1", "ignore")
            m = _QT_DLL_RE.search(data)
            if m:
                # Major only from the import name; minor comes from the DLL beside the exe.
                major = m.group(1)
                for dll in exe.parent.glob(f"Qt{major}Core.dll"):
                    minor = _dll_minor_version(dll)
                    if minor is not None:
                        return f"{major}.{minor}"
                return f"{major}.?"
    except Exception:  # noqa: BLE001 - detection is best effort by design
        return None
    return None


def _dll_minor_version(dll: Path) -> int | None:
    """Read a DLL's FileVersion minor number (Windows only). Placeholder for a PE parser."""
    try:
        import ctypes
        import ctypes.wintypes as wt

        size = ctypes.windll.version.GetFileVersionInfoSizeW(str(dll), None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        ctypes.windll.version.GetFileVersionInfoW(str(dll), 0, size, buf)
        value = ctypes.c_void_p()
        length = wt.UINT()
        if not ctypes.windll.version.VerQueryValueW(buf, "\\", ctypes.byref(value),
                                                    ctypes.byref(length)):
            return None
        # VS_FIXEDFILEINFO: [0] signature, [1] struct version, [2] dwFileVersionMS.
        # dwFileVersionMS packs HIWORD=major, LOWORD=minor.
        ffi = ctypes.cast(value, ctypes.POINTER(ctypes.c_uint32 * 4)).contents
        return ffi[2] & 0xFFFF
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------ target inspection

#: Qt stamps QLibraryInfo::build() into the binary that carries QtCore -- the shared library for a
#: normal build, the executable itself for a static one. It states the version, the linkage and the
#: compiler outright, which beats inferring any of them:
#:   Qt 6.7.2 (x86_64-little_endian-llp64 static release build; by MSVC 2019)
_QT_BUILD_RE = re.compile(rb"Qt (\d+)\.(\d+)\.(\d+) \(([^\x00]{0,220})")

_COMPILERS = ((b"MSVC 2022", "msvc2022"), (b"MSVC 2019", "msvc2019"),
              (b"MSVC 2017", "msvc2017"), (b"MSVC", "msvc"),
              (b"MinGW", "mingw"), (b"GCC", "gcc"), (b"Clang", "clang"))


@dataclass
class BinaryReport:
    """What a target executable says about whether the agent can be loaded into it.

    Attributes:
        path: The executable inspected.
        qt_version: Qt minor version such as ``"6.7"``, or None if it could not be determined.
        linkage: ``"dynamic"``, ``"static"``, ``"none"`` (no Qt at all) or ``"unknown"``.
        toolchain: Compiler that built Qt, e.g. ``"msvc2019"``, when the binary says so.
        arch: Architecture of the *target*, e.g. ``"x86"``. A 32-bit application on a 64-bit host
            needs a 32-bit agent, so this must never be inferred from the host.
        injectable: Whether the generic-plugin mechanism can work at all.
        reason: Why not, when ``injectable`` is False.
    """

    path: Path
    qt_version: str | None = None
    linkage: str = "unknown"
    toolchain: str | None = None
    arch: str | None = None
    injectable: bool = False
    reason: str = ""


#: PE COFF machine values worth distinguishing.
_PE_MACHINES = {0x14C: "x86", 0x8664: "x86_64", 0xAA64: "aarch64"}


def _pe_machine(data: bytes) -> str | None:
    """Architecture a PE binary was built for.

    Args:
        data: The first kilobyte of the file is enough.

    Returns:
        ``"x86"``, ``"x86_64"``, ``"aarch64"``, or None if this is not a PE.
    """
    try:
        if data[:2] != b"MZ":
            return None
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe:pe + 4] != b"PE\0\0":
            return None
        return _PE_MACHINES.get(struct.unpack_from("<H", data, pe + 4)[0])
    except Exception:  # noqa: BLE001 - inspection is best effort by design
        return None


def target_platform_tag(executable: str) -> str:
    """Platform tag an agent must carry to be loadable into this executable.

    Deliberately not :func:`current_platform_tag`: a 32-bit application runs perfectly well on a
    64-bit host, and pointing its user at an x86_64 agent produces a plugin that silently refuses
    to load. Falls back to the host tag when the target cannot be read.

    Args:
        executable: Path to the application binary.

    Returns:
        A tag such as ``"windows-x86"``.
    """
    try:
        arch = _pe_machine(Path(executable).read_bytes()[:0x400])
    except OSError:
        arch = None
    if not arch:
        return current_platform_tag()
    system = current_platform_tag().split("-")[0]
    return f"{system}-{arch}"


def _pe_imported_dlls(data: bytes) -> list[str]:
    """DLL names in a PE import table.

    Args:
        data: Whole file contents.

    Returns:
        The imported DLL names, or an empty list if this is not a parseable PE.
    """
    try:
        if data[:2] != b"MZ":
            return []
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe:pe + 4] != b"PE\0\0":
            return []
        n_sections = struct.unpack_from("<H", data, pe + 6)[0]
        opt_size = struct.unpack_from("<H", data, pe + 20)[0]
        opt = pe + 24
        pe32plus = struct.unpack_from("<H", data, opt)[0] == 0x20B

        sections = []
        for i in range(n_sections):
            off = opt + opt_size + i * 40
            v_size, v_addr, r_size, r_addr = struct.unpack_from("<IIII", data, off + 8)
            sections.append((v_addr, max(v_size, r_size), r_addr))

        def to_offset(rva: int) -> int | None:
            for v_addr, size, r_addr in sections:
                if v_addr <= rva < v_addr + size:
                    return r_addr + (rva - v_addr)
            return None

        import_rva = struct.unpack_from("<I", data, opt + (112 if pe32plus else 96) + 8)[0]
        cursor = to_offset(import_rva)
        if not cursor:
            return []
        names = []
        while len(names) < 512:
            name_rva = struct.unpack_from("<I", data, cursor + 12)[0]
            if name_rva == 0:
                break
            at = to_offset(name_rva)
            if at is None:
                break
            names.append(data[at:data.index(b"\0", at)].decode("latin-1"))
            cursor += 20
        return names
    except Exception:  # noqa: BLE001 - inspection is best effort by design
        return []


def _qt_build_stamp(data: bytes) -> tuple[str, str, str | None] | None:
    """Parse Qt's embedded build string.

    Args:
        data: Whole file contents.

    Returns:
        ``(qt_minor, linkage, toolchain)`` or None when the stamp is absent.
    """
    match = _QT_BUILD_RE.search(data)
    if not match:
        return None
    detail = match.group(4)
    linkage = "static" if b"static" in detail else "dynamic"
    toolchain = next((tag for needle, tag in _COMPILERS if needle in detail), None)
    return f"{int(match.group(1))}.{int(match.group(2))}", linkage, toolchain


def inspect_binary(executable: str) -> BinaryReport:
    """Work out whether the agent can be injected into an executable, and why not if it cannot.

    A statically linked Qt is the one case no injection mechanism can rescue: the generic-plugin
    hook does not exist, and loading an agent that brings its own Qt would put two independent
    copies of Qt in one process. Saying that plainly is far more useful than reporting an
    unmatched ABI.

    Args:
        executable: Path to the application binary.

    Returns:
        What could be determined. Absent evidence is reported as ``"unknown"``, never guessed.
    """
    path = Path(executable)
    report = BinaryReport(path=path)
    if not path.exists():
        report.reason = "file not found"
        return report

    try:
        data = path.read_bytes()
    except OSError as exc:
        report.reason = f"could not read the file: {exc}"
        return report

    report.arch = _pe_machine(data)
    imports = _pe_imported_dlls(data) if sys.platform == "win32" else []
    if imports:
        report.toolchain = ("msvc" if any(n.lower().startswith(("msvcp1", "vcruntime"))
                                          for n in imports)
                            else "mingw" if any("libgcc" in n.lower() or "libstdc++" in n.lower()
                                                for n in imports)
                            else None)

    # A dynamically linked Qt names its Qt libraries as dependencies.
    linked_qt = detect_qt_version(str(path))
    if linked_qt:
        report.qt_version = linked_qt
        report.linkage = "dynamic"
        report.injectable = True
        return report

    # No Qt dependency. Either Qt is compiled in, or this is not a Qt application at all.
    stamp = _qt_build_stamp(data)
    if stamp:
        report.qt_version, report.linkage, toolchain = stamp
        report.toolchain = toolchain or report.toolchain
        if report.linkage == "static":
            report.reason = (
                "Qt is linked statically into this executable, so it has no plugin loader for "
                "the agent to use, and an agent carrying its own Qt would put two copies of Qt "
                "in one process. No injection mode can work; the application has to be built "
                "with the agent embedded (LiberaQt::start(), docs/INJECTION.md section 4)."
            )
        return report

    report.linkage = "none"
    report.reason = "no Qt could be found in this binary; it may not be a Qt application"
    return report


# ------------------------------------------------------------------ resolution

def search_paths() -> list[Path]:
    """Directories to search for installed agents, highest priority first.

    ``LIBERAQT_AGENT_PATH`` comes first so a locally built agent can shadow a downloaded one,
    which is what CI uses to test the agent it just built.

    Returns:
        The directories to search, which need not exist.
    """
    paths = [cache_dir() / "agents"]
    env = os.environ.get("LIBERAQT_AGENT_PATH")
    if env:
        paths = [Path(p) for p in env.split(os.pathsep) if p] + paths
    paths.append(Path(__file__).resolve().parent / "_agents")
    return paths


def installed() -> list[AgentBuild]:
    """Find every usable agent on the search path.

    Directories whose plugin binary is missing are skipped, so a half-finished install is
    invisible rather than a confusing failure at launch time.

    Returns:
        The agent builds found, in search-path order.
    """
    found: list[AgentBuild] = []
    for base in search_paths():
        if not base.is_dir():
            continue
        for entry in sorted(base.iterdir()):
            parts = entry.name.split("-")
            if not entry.is_dir() or not parts[0].startswith("qt"):
                continue
            qt = parts[0][2:]
            plat = "-".join(parts[1:3]) if len(parts) >= 3 else current_platform_tag()
            compiler = parts[3] if len(parts) > 3 else "unknown"
            build = AgentBuild(qt=qt, platform_tag=plat, compiler=compiler, root=entry)
            if build.exists():
                found.append(build)
    return found


def resolve(executable: str, qt: str | None = None) -> AgentBuild:
    """Pick the agent build for this AUT, or raise with actionable instructions."""
    wanted_qt = qt or detect_qt_version(executable)
    tag = target_platform_tag(executable)
    candidates = installed()

    if wanted_qt:
        exact = [c for c in candidates if c.qt == wanted_qt and c.platform_tag == tag]
        if exact:
            return exact[0]
        series = wanted_qt.split(".")[0]
        near = [c for c in candidates if c.qt.split(".")[0] == series and c.platform_tag == tag]
        if near:
            return near[0]

    have = ", ".join(str(c) for c in candidates) or "none"
    raise AgentMismatchError(
        f"no liberaqt agent for Qt {wanted_qt or 'unknown'} on {tag} (installed: {have})",
        hint=(
            f"liberaqt agents install --qt {wanted_qt or '6.7'}\n"
            "  or build one:  cmake -S agent -B build/agent -DCMAKE_PREFIX_PATH=$QTDIR "
            "&& cmake --build build/agent && cmake --install build/agent --prefix "
            f"{cache_dir() / 'agents' / f'qt{wanted_qt or 6.7}-{tag}-local'}"
        ),
        data={"detected_qt": wanted_qt, "platform": tag,
              "installed": [str(c) for c in candidates]},
    )
