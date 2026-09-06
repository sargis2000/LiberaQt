"""Plugin keys, and detecting an installed agent that lacks its own.

Qt binds a plugin key to exactly one library, so every build that can sit on the same
``QT_PLUGIN_PATH`` needs a key nothing else claims. Getting the *granularity* of that key wrong is
not a loud failure: the loser is simply handed a plugin it cannot load, and reports no agent, no
port file and no error.

This was got wrong twice. First every build advertised only ``liberaqt``, so the first directory
on the path locked out every other architecture. Then the key named the pointer size alone, and a
64-bit MinGW build installed beside a 64-bit MSVC one took ``liberaqt_64`` from it -- which
stopped SmartTime, an MSVC application, finding its agent. Qt version, pointer size and compiler
together are what actually distinguish two loadable plugins.
"""

import pytest

from liberaqt import agent_registry
from liberaqt.agent_registry import AgentBuild, abi_key_for, plugin_key_for


def _install(tmp_path, tag, body):
    qt, platform, arch, compiler = tag.split("-")
    build = AgentBuild(qt=qt[2:], platform_tag=f"{platform}-{arch}", compiler=compiler,
                       root=tmp_path / tag)
    build.library.parent.mkdir(parents=True, exist_ok=True)
    build.library.write_bytes(body)
    return build


# ------------------------------------------------------------------ what a key contains


@pytest.mark.parametrize("qt, platform_tag, compiler, expected", [
    ("5.15", "windows-x86", "msvc2019", "liberaqt_5_15_32_msvc"),
    ("5.15", "windows-x86_64", "msvc2019", "liberaqt_5_15_64_msvc"),
    ("5.15", "windows-x86_64", "mingw", "liberaqt_5_15_64_gnu"),
    ("6.7", "windows-x86_64", "mingw", "liberaqt_6_7_64_gnu"),
    ("6.7", "windows-x86_64", "llvm-mingw", "liberaqt_6_7_64_clang"),
    ("6.7", "linux-x86_64", "gcc", "liberaqt_6_7_64_gnu"),
    ("6.7", "windows-arm64", "msvc2019", "liberaqt_6_7_64_msvc"),
])
def test_a_key_names_qt_version_bitness_and_compiler(qt, platform_tag, compiler, expected):
    assert plugin_key_for(qt, platform_tag, compiler) == expected


def test_same_bitness_different_compiler_are_not_the_same_key():
    """The regression that stopped SmartTime finding its agent.

    Both builds are 64-bit, and a MinGW plugin is as unloadable in an MSVC application as a
    32-bit one would be.
    """
    msvc = plugin_key_for("5.15", "windows-x86_64", "msvc2019")
    mingw = plugin_key_for("5.15", "windows-x86_64", "mingw")
    assert msvc != mingw


def test_same_everything_but_qt_version_are_not_the_same_key():
    assert plugin_key_for("6.7", "windows-x86_64", "mingw") != \
           plugin_key_for("5.15", "windows-x86_64", "mingw")


def test_msvc_toolset_years_share_a_key():
    """v140-v143 are binary compatible, so separating them would refuse a plugin that loads."""
    assert plugin_key_for("5.15", "windows-x86_64", "msvc2019") == \
           plugin_key_for("5.15", "windows-x86_64", "msvc2022")


def test_an_unknown_compiler_falls_back_to_the_gnu_family():
    assert plugin_key_for("6.7", "linux-x86_64", "something") == "liberaqt_6_7_64_gnu"


def test_the_superseded_pointer_size_key_still_answers():
    """Kept so an older installed agent can still be recognised for what it is."""
    assert abi_key_for("windows-x86_64") == "liberaqt_64"
    assert abi_key_for("windows-x86") == "liberaqt_32"


# ------------------------------------------------------------------ reading it off the binary


def test_a_current_build_advertises_its_key(tmp_path):
    build = _install(tmp_path, "qt5.15-windows-x86_64-msvc2019",
                     b"..liberaqt..liberaqt_5_15_64_msvc..")
    assert build.advertises_abi_key()


def test_a_build_predating_the_keys_is_detected(tmp_path):
    build = _install(tmp_path, "qt5.15-windows-x86_64-msvc2019", b"....liberaqt....")
    assert not build.advertises_abi_key()


def test_a_build_with_only_the_old_pointer_size_key_is_detected(tmp_path):
    """The intermediate scheme is stale too, and for a reason worth catching."""
    build = _install(tmp_path, "qt5.15-windows-x86_64-msvc2019", b"..liberaqt_64..")
    assert not build.advertises_abi_key()


def test_another_abis_key_does_not_count(tmp_path):
    """A MinGW key in an MSVC build would mean CMake generated the wrong metadata."""
    build = _install(tmp_path, "qt5.15-windows-x86_64-msvc2019", b"..liberaqt_5_15_64_gnu..")
    assert not build.advertises_abi_key()


def test_a_missing_binary_is_not_reported_as_current(tmp_path):
    build = AgentBuild(qt="5.15", platform_tag="windows-x86_64", compiler="msvc2019",
                       root=tmp_path / "gone")
    assert not build.advertises_abi_key()


# ------------------------------------------------------------------ what doctor says


def test_doctor_names_a_stale_agent_and_the_key_it_lacks(tmp_path, monkeypatch, capsys):
    from liberaqt import cli

    old = _install(tmp_path, "qt5.15-windows-x86-msvc2019", b"..liberaqt..")
    new = _install(tmp_path, "qt5.15-windows-x86_64-msvc2019", b"..liberaqt_5_15_64_msvc..")
    monkeypatch.setattr(agent_registry, "installed", lambda: [old, new])

    cli.cmd_doctor(type("Args", (), {"exe": None})())
    out = capsys.readouterr().out

    assert "STALE" in out
    assert "liberaqt_5_15_32_msvc" in out, "the message must name the key that is missing"
    assert out.count("STALE") == 1, "the current build must not be flagged"
