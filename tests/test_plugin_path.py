"""What goes on QT_PLUGIN_PATH, and why more than one directory belongs there.

Every Qt child of the application under test inherits this environment, and a child is not
necessarily the parent's architecture: Libero SoC is 32-bit and spawns a 64-bit SmartTime. Qt
skips a plugin it cannot load without a word, so an agent-less child looks exactly like a child
that never built a GUI. Offering every installed ABI is what tells those two apart.
"""

import os
from pathlib import Path

import pytest

from liberaqt import agent_registry
from liberaqt.launcher import build_environment, sibling_plugin_dirs


def _build(tag, root):
    qt, platform, arch, compiler = tag.split("-")
    build = agent_registry.AgentBuild(
        qt=qt[2:], platform_tag=f"{platform}-{arch}", compiler=compiler, root=root
    )
    library = build.library
    library.parent.mkdir(parents=True, exist_ok=True)
    library.write_bytes(b"not really a plugin")
    return build


@pytest.fixture
def agents(tmp_path, monkeypatch):
    """Three installed agents, as this machine actually has."""
    builds = [
        _build("qt5.15-windows-x86-msvc2019", tmp_path / "a"),
        _build("qt5.15-windows-x86_64-msvc2019", tmp_path / "b"),
        _build("qt6.7-windows-x86_64-mingw", tmp_path / "c"),
    ]
    monkeypatch.setattr(agent_registry, "installed", lambda: builds)
    return builds


def _paths(env):
    return env["QT_PLUGIN_PATH"].split(os.pathsep)


def test_the_chosen_agent_comes_first(agents, tmp_path):
    """The parent must not have to search past its own plugin to find it."""
    env = build_environment(agents[0], "tok", tmp_path, base_env={})
    assert _paths(env)[0] == str(agents[0].plugin_dir)


def test_every_other_installed_abi_is_offered(agents, tmp_path):
    """The 64-bit child's plugin has to be reachable from the 32-bit parent's environment."""
    env = build_environment(agents[0], "tok", tmp_path, base_env={})
    assert str(agents[1].plugin_dir) in _paths(env)
    assert str(agents[2].plugin_dir) in _paths(env)


def test_the_chosen_agent_is_not_repeated(agents, tmp_path):
    env = build_environment(agents[0], "tok", tmp_path, base_env={})
    assert _paths(env).count(str(agents[0].plugin_dir)) == 1


def test_an_existing_plugin_path_is_kept_and_comes_last(agents, tmp_path):
    """Applications that need their own plugin path keep working; ours are searched first."""
    env = build_environment(agents[0], "tok", tmp_path, base_env={"QT_PLUGIN_PATH": "/app/plugins"})
    assert _paths(env)[-1] == "/app/plugins"


def test_a_half_installed_agent_is_not_offered(agents, tmp_path, monkeypatch):
    """A directory whose plugin binary is missing would send Qt looking at nothing."""
    agents[1].library.unlink()
    assert str(agents[1].plugin_dir) not in sibling_plugin_dirs(agents[0])


def test_an_unreadable_cache_does_not_break_launching(tmp_path, monkeypatch):
    """Offering the extras is a bonus; failing to enumerate them must not stop the launch."""
    def boom():
        raise OSError("cache is unreadable")

    monkeypatch.setattr(agent_registry, "installed", boom)
    build = agent_registry.AgentBuild(
        qt="5.15", platform_tag="windows-x86", compiler="msvc2019", root=Path("/nowhere")
    )
    env = build_environment(build, "tok", tmp_path, base_env={})
    assert _paths(env) == [str(build.plugin_dir)]
