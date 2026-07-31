"""Locate the agent binary whose ABI matches the application under test.

A Qt plugin must be built against the same Qt minor version and the same compiler/stdlib as the
host process. Guessing wrong means Qt silently refuses to load the plugin, which produces a
mystifying "agent never connected" failure. So: detect first, fail loudly with a fix command.
"""

from __future__ import annotations

import os
import platform
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .errors import AgentMismatchError

SUPPORTED_QT = ("6.7", "5.15")


def cache_dir() -> Path:
    env = os.environ.get("QTDRIVER_CACHE")
    if env:
        return Path(env)
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "qtdriver"


@dataclass
class AgentBuild:
    qt: str                 # "6.7"
    platform_tag: str       # "linux-x86_64" | "windows-x86_64"
    compiler: str           # "gcc" | "msvc2019" | "msvc2022"
    root: Path              # contains plugins/generic/<lib>

    @property
    def plugin_dir(self) -> Path:
        return self.root / "plugins"

    @property
    def library(self) -> Path:
        name = "qtdriver.dll" if "windows" in self.platform_tag else "libqtdriver.so"
        return self.plugin_dir / "generic" / name

    def exists(self) -> bool:
        return self.library.is_file()

    def __str__(self) -> str:
        return f"qt{self.qt}-{self.platform_tag}-{self.compiler}"


def current_platform_tag() -> str:
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


def detect_qt_version(executable: str) -> Optional[str]:
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


def _dll_minor_version(dll: Path) -> Optional[int]:
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
        ffi = ctypes.cast(value, ctypes.POINTER(ctypes.c_uint32 * 4)).contents
        return (ffi[2] >> 16) & 0xFFFF
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------ resolution

def search_paths() -> List[Path]:
    paths = [cache_dir() / "agents"]
    env = os.environ.get("QTDRIVER_AGENT_PATH")
    if env:
        paths = [Path(p) for p in env.split(os.pathsep) if p] + paths
    paths.append(Path(__file__).resolve().parent / "_agents")
    return paths


def installed() -> List[AgentBuild]:
    found: List[AgentBuild] = []
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


def resolve(executable: str, qt: Optional[str] = None) -> AgentBuild:
    """Pick the agent build for this AUT, or raise with actionable instructions."""
    wanted_qt = qt or detect_qt_version(executable)
    tag = current_platform_tag()
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
        f"no qtdriver agent for Qt {wanted_qt or 'unknown'} on {tag} (installed: {have})",
        hint=(
            f"qtdriver agents install --qt {wanted_qt or '6.7'}\n"
            "  or build one:  cmake -S agent -B build/agent -DCMAKE_PREFIX_PATH=$QTDIR "
            "&& cmake --build build/agent && cmake --install build/agent --prefix "
            f"{cache_dir() / 'agents' / f'qt{wanted_qt or 6.7}-{tag}-local'}"
        ),
        data={"detected_qt": wanted_qt, "platform": tag,
              "installed": [str(c) for c in candidates]},
    )
