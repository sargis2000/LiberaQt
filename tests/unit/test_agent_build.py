"""Discovering Qt kits and planning an agent build.

All of it against a fabricated Qt tree, so it needs no Qt, no compiler and no network. The parts
worth pinning are the ones that fail *silently* when wrong: a kit read as the wrong architecture
installs an agent under a tag that lies about it, and a MinGW chosen by version-as-text links an
agent against a different standard library than the Qt it must load beside.
"""

from pathlib import Path

import pytest

from liberaqt import agent_registry
from liberaqt.agent_build import (
    AgentBuildError,
    QtKit,
    discover_kits,
    find_kit,
    find_mingw,
    parse_kit_dir,
    plan_build,
    qt_minor,
)


def _make_kit(root: Path, version: str, kit: str) -> Path:
    path = root / version / kit
    (path / "lib" / "cmake").mkdir(parents=True)
    (path / "bin").mkdir(parents=True, exist_ok=True)
    return path


def _make_mingw(tools: Path, name: str) -> Path:
    path = tools / name / "bin"
    path.mkdir(parents=True)
    (path / "g++.exe").write_text("")
    return path


# ------------------------------------------------------------------ reading a kit name


@pytest.mark.parametrize("name, expected", [
    ("msvc2019", ("msvc2019", "x86")),          # Qt names 32-bit without a suffix
    ("msvc2019_64", ("msvc2019", "x86_64")),
    ("msvc2022_64", ("msvc2022", "x86_64")),
    ("msvc2019_arm64", ("msvc2019", "arm64")),
    ("mingw_64", ("mingw", "x86_64")),
    ("mingw81_64", ("mingw", "x86_64")),
    ("mingw81_32", ("mingw", "x86")),
    ("llvm-mingw_64", ("llvm-mingw", "x86_64")),
    ("gcc_64", ("gcc", "x86_64")),
])
def test_desktop_kits_are_recognised(name, expected):
    assert parse_kit_dir(name) == expected


def test_the_unsuffixed_msvc_kit_is_32_bit():
    """The single most common way to install an agent under a tag that lies about it."""
    assert parse_kit_dir("msvc2019")[1] == "x86"
    assert parse_kit_dir("msvc2019_64")[1] == "x86_64"


@pytest.mark.parametrize("name", [
    "android_arm64_v8a", "wasm_multithread", "winrt_x64_msvc2019", "Src", "sha1s.txt",
])
def test_non_desktop_kits_are_skipped(name):
    assert parse_kit_dir(name) is None


@pytest.mark.parametrize("version, expected", [
    ("6.7.3", "6.7"), ("5.15.0", "5.15"), ("6.5.9", "6.5"), ("6.7", "6.7"),
])
def test_the_patch_level_is_dropped(version, expected):
    """A plugin matches the Qt *minor* version, so 6.7.3 and 6.7.1 share one agent."""
    assert qt_minor(version) == expected


# ------------------------------------------------------------------ discovery


@pytest.fixture
def qt_root(tmp_path):
    root = tmp_path / "Qt"
    _make_kit(root, "6.7.3", "mingw_64")
    _make_kit(root, "6.7.3", "msvc2019_64")
    _make_kit(root, "6.7.3", "android_arm64_v8a")
    _make_kit(root, "5.15.0", "msvc2019")
    (root / "5.15.0" / "half_removed").mkdir(parents=True)     # no lib/cmake
    return root


def test_every_desktop_kit_is_found(qt_root):
    tags = {k.tag for k in discover_kits([qt_root])}
    assert "qt6.7-windows-x86_64-mingw" in tags or "qt6.7-linux-x86_64-mingw" in tags
    assert len(tags) == 3, tags


def test_a_kit_without_cmake_files_is_ignored(qt_root):
    """A half-removed kit would otherwise become a build that cannot configure."""
    assert all("half_removed" not in str(k.prefix) for k in discover_kits([qt_root]))


def test_a_missing_root_is_not_an_error(tmp_path):
    assert discover_kits([tmp_path / "nowhere"]) == []


def test_find_kit_narrows_to_one(qt_root):
    kit = find_kit(qt="5.15", roots=[qt_root])
    assert kit.qt == "5.15"
    assert kit.arch == "x86"


def test_find_kit_refuses_an_ambiguous_request(qt_root):
    with pytest.raises(AgentBuildError, match="kits match"):
        find_kit(qt="6.7", roots=[qt_root])


def test_find_kit_lists_what_exists_when_nothing_matches(qt_root):
    with pytest.raises(AgentBuildError) as excinfo:
        find_kit(qt="6.5", roots=[qt_root])
    assert "6.7" in str(excinfo.value), "the error must show what is actually installed"


# ------------------------------------------------------------------ choosing a MinGW


def test_the_newest_mingw_is_chosen_numerically(tmp_path):
    """Sorting these as text picks 8.1 over 11.2, because '8' > '1'.

    That mismatch is not a build error: it links the agent against a different libstdc++ than the
    Qt it has to load beside.
    """
    tools = tmp_path / "Tools"
    _make_mingw(tools, "mingw810_64")
    _make_mingw(tools, "mingw1120_64")
    kit = QtKit(prefix=tmp_path / "6.7.3" / "mingw_64", qt="6.7",
                platform_tag="windows-x86_64", compiler="mingw")
    assert find_mingw(kit, tools_root=tools).parent.name == "mingw1120_64"


def test_a_versioned_kit_takes_its_own_toolchain(tmp_path):
    """Qt 5.15's mingw81 kit was built with MinGW 8.1 and must not be built with 11.2."""
    tools = tmp_path / "Tools"
    _make_mingw(tools, "mingw810_64")
    _make_mingw(tools, "mingw1120_64")
    kit = QtKit(prefix=tmp_path / "5.15.0" / "mingw81_64", qt="5.15",
                platform_tag="windows-x86_64", compiler="mingw")
    assert find_mingw(kit, tools_root=tools).parent.name == "mingw810_64"


def test_a_versioned_kit_with_no_matching_toolchain_is_refused(tmp_path):
    """Refusing beats silently substituting an incompatible compiler."""
    tools = tmp_path / "Tools"
    _make_mingw(tools, "mingw1120_64")
    kit = QtKit(prefix=tmp_path / "5.15.0" / "mingw81_64", qt="5.15",
                platform_tag="windows-x86_64", compiler="mingw")
    assert find_mingw(kit, tools_root=tools) is None


def test_bitness_is_respected(tmp_path):
    tools = tmp_path / "Tools"
    _make_mingw(tools, "mingw810_64")
    _make_mingw(tools, "mingw810_32")
    kit = QtKit(prefix=tmp_path / "5.15.0" / "mingw81_32", qt="5.15",
                platform_tag="windows-x86", compiler="mingw")
    assert find_mingw(kit, tools_root=tools).parent.name == "mingw810_32"


# ------------------------------------------------------------------ the plan


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "agent"
    path.mkdir()
    (path / "CMakeLists.txt").write_text("")
    return path


def test_each_abi_gets_its_own_build_directory(source, tmp_path, monkeypatch):
    """Two configurations cannot share one build directory."""
    monkeypatch.setattr(agent_registry, "cache_dir", lambda: tmp_path / "cache")
    tools = tmp_path / "Tools"
    _make_mingw(tools, "mingw1120_64")
    kit = QtKit(prefix=tmp_path / "6.7.3" / "mingw_64", qt="6.7",
                platform_tag="windows-x86_64", compiler="mingw")
    monkeypatch.setattr("liberaqt.agent_build.find_mingw", lambda k, tools_root=None: tools /
                        "mingw1120_64" / "bin")

    plan = plan_build(kit, source)
    assert kit.tag in str(plan.build_dir)


def test_the_install_prefix_is_the_tag_the_kit_describes(source, tmp_path, monkeypatch):
    """Derived, never taken on trust: a mismatched tag is invisible after the fact."""
    monkeypatch.setattr(agent_registry, "cache_dir", lambda: tmp_path / "cache")
    tools = tmp_path / "Tools"
    _make_mingw(tools, "mingw1120_64")
    monkeypatch.setattr("liberaqt.agent_build.find_mingw", lambda k, tools_root=None: tools /
                        "mingw1120_64" / "bin")
    kit = QtKit(prefix=tmp_path / "6.7.3" / "mingw_64", qt="6.7",
                platform_tag="windows-x86_64", compiler="mingw")

    plan = plan_build(kit, source)
    assert plan.prefix == tmp_path / "cache" / "agents" / "qt6.7-windows-x86_64-mingw"


def test_ninja_is_always_the_generator(source, tmp_path, monkeypatch):
    """The MinGW Makefiles generator chokes on drive-letter colons."""
    monkeypatch.setattr(agent_registry, "cache_dir", lambda: tmp_path / "cache")
    tools = tmp_path / "Tools"
    _make_mingw(tools, "mingw1120_64")
    monkeypatch.setattr("liberaqt.agent_build.find_mingw", lambda k, tools_root=None: tools /
                        "mingw1120_64" / "bin")
    kit = QtKit(prefix=tmp_path / "6.7.3" / "mingw_64", qt="6.7",
                platform_tag="windows-x86_64", compiler="mingw")

    flat = " ".join(" ".join(step) for step in plan_build(kit, source).steps)
    assert "-G Ninja" in flat


def test_an_msvc_build_runs_everything_inside_one_vcvars_shell(source, tmp_path, monkeypatch):
    """Every step has to happen inside one shell.

    ``vcvarsall`` sets the environment for the shell it runs in, so splitting the steps loses it,
    and it cannot be invoked from PowerShell at all.
    """
    monkeypatch.setattr(agent_registry, "cache_dir", lambda: tmp_path / "cache")
    vcvars = tmp_path / "vcvarsall.bat"
    vcvars.write_text("")
    monkeypatch.setattr("liberaqt.agent_build.find_vcvarsall", lambda: vcvars)
    kit = QtKit(prefix=tmp_path / "5.15.0" / "msvc2019", qt="5.15",
                platform_tag="windows-x86", compiler="msvc2019")

    plan = plan_build(kit, source)
    assert plan.steps == [], "an MSVC build runs from a script, not an argument list"
    assert plan.script is not None
    assert "vcvarsall" in plan.script
    assert " x86 " in plan.script, "32-bit kits must ask vcvarsall for x86"
    assert plan.script.count("cmake") == 3, "configure, build and install in the one shell"
    assert plan.script.count("exit /b 1") == 4, "every step must stop the script when it fails"


def test_a_64_bit_msvc_kit_asks_vcvars_for_x64(source, tmp_path, monkeypatch):
    monkeypatch.setattr(agent_registry, "cache_dir", lambda: tmp_path / "cache")
    vcvars = tmp_path / "vcvarsall.bat"
    vcvars.write_text("")
    monkeypatch.setattr("liberaqt.agent_build.find_vcvarsall", lambda: vcvars)
    kit = QtKit(prefix=tmp_path / "5.15.0" / "msvc2019_64", qt="5.15",
                platform_tag="windows-x86_64", compiler="msvc2019")

    assert " x64 " in plan_build(kit, source).script


def test_the_batch_script_quotes_the_vcvarsall_path_plainly(source, tmp_path, monkeypatch):
    r"""The path must be quoted plainly, not escaped.

    Quoting through an argument list does not survive: cmd re-parses it and the quotes come back
    escaped as ``\"``, which it then cannot find. A file avoids that second parse entirely.
    """
    monkeypatch.setattr(agent_registry, "cache_dir", lambda: tmp_path / "cache")
    vcvars = tmp_path / "Program Files" / "vcvarsall.bat"
    vcvars.parent.mkdir(parents=True)
    vcvars.write_text("")
    monkeypatch.setattr("liberaqt.agent_build.find_vcvarsall", lambda: vcvars)
    kit = QtKit(prefix=tmp_path / "5.15.0" / "msvc2019", qt="5.15",
                platform_tag="windows-x86", compiler="msvc2019")

    script = plan_build(kit, source).script
    assert f'call "{vcvars}"' in script
    assert '\\"' not in script, "an escaped quote means the path will not be found"


def test_msvc_without_visual_studio_says_so(source, tmp_path, monkeypatch):
    monkeypatch.setattr(agent_registry, "cache_dir", lambda: tmp_path / "cache")
    monkeypatch.setattr("liberaqt.agent_build.find_vcvarsall", lambda: None)
    kit = QtKit(prefix=tmp_path / "5.15.0" / "msvc2019", qt="5.15",
                platform_tag="windows-x86", compiler="msvc2019")

    with pytest.raises(AgentBuildError, match="Visual Studio"):
        plan_build(kit, source)
