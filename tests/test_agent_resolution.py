# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Sargis Khachatryan
"""Choosing an agent for an application, and refusing the ones that cannot load.

Qt keeps plugins forward compatible within a major release: one built against 6.5 loads into 6.5,
6.6 and 6.7. The reverse does not hold -- a plugin built against a *newer* Qt than the host is
refused, and refused without a word. The application starts, no agent loads, no port file appears
and nothing anywhere says why.

So the fallback may only ever look downwards. An earlier version took any agent in the same major
series, which meant a Qt 6.5 application was handed the 6.7 agent and failed in exactly that
silent way.
"""

from pathlib import Path

import pytest

from liberaqt import agent_registry
from liberaqt.agent_registry import AgentBuild
from liberaqt.errors import AgentMismatchError


def _build(tmp_path, qt, compiler="mingw", arch="x86_64"):
    tag = f"qt{qt}-windows-{arch}-{compiler}"
    build = AgentBuild(qt=qt, platform_tag=f"windows-{arch}", compiler=compiler,
                       root=tmp_path / tag)
    build.library.parent.mkdir(parents=True, exist_ok=True)
    build.library.write_bytes(b"..." + build.abi_key.encode() + b"...")
    return build


@pytest.fixture
def resolve_for(tmp_path, monkeypatch):
    """Resolve against a fabricated set of installed agents, for a stated application Qt."""
    def run(app_qt, installed_versions, arch="x86_64", app_arch=None):
        builds = [_build(tmp_path, v, arch=arch) for v in installed_versions]
        target = app_arch or arch
        monkeypatch.setattr(agent_registry, "installed", lambda: builds)
        monkeypatch.setattr(agent_registry, "detect_qt_version", lambda exe: app_qt)
        monkeypatch.setattr(agent_registry, "target_platform_tag", lambda exe: f"windows-{target}")
        return agent_registry.resolve("app.exe")
    return run


# ------------------------------------------------------------------ the exact match


def test_an_exact_match_wins(resolve_for):
    assert resolve_for("6.5", ["6.5", "6.7"]).qt == "6.5"


# ------------------------------------------------------------------ falling back downwards


def test_an_older_agent_is_offered_because_qt_accepts_it(resolve_for):
    """6.5 into a 6.7 host is fine: plugins are forward compatible within a major release."""
    assert resolve_for("6.7", ["6.5"]).qt == "6.5"


def test_the_newest_agent_at_or_below_the_application_is_chosen(resolve_for):
    assert resolve_for("6.7", ["6.2", "6.5"]).qt == "6.5"


def test_a_newer_agent_is_never_offered(resolve_for):
    """The regression this file exists for: 6.7 into a 6.5 host is refused by Qt, silently."""
    with pytest.raises(AgentMismatchError):
        resolve_for("6.5", ["6.7"])


def test_the_refusal_explains_why_the_newer_agent_was_not_used(resolve_for):
    """"Install one" is unhelpful when an agent is sitting right there, apparently matching."""
    with pytest.raises(AgentMismatchError) as excinfo:
        resolve_for("6.5", ["6.7"])
    message = str(excinfo.value)
    assert "qt6.7-windows-x86_64-mingw" in message
    assert "newer Qt" in message


def test_the_major_version_is_a_hard_boundary(resolve_for):
    """A Qt 5 agent cannot serve a Qt 6 application however the numbers compare."""
    with pytest.raises(AgentMismatchError):
        resolve_for("6.5", ["5.15"])


def test_minor_versions_are_compared_numerically(resolve_for):
    """Textually, "6.10" sorts below "6.9"; it must not be treated as older."""
    assert resolve_for("6.10", ["6.9", "6.10"]).qt == "6.10"
    with pytest.raises(AgentMismatchError):
        resolve_for("6.9", ["6.10"])


def test_a_different_architecture_is_not_a_candidate(resolve_for):
    """A 64-bit agent cannot load into a 32-bit application, whatever the Qt versions say."""
    with pytest.raises(AgentMismatchError):
        resolve_for("6.7", ["6.7"], arch="x86_64", app_arch="x86")


# ------------------------------------------------------------------ the supported list


def test_the_cli_accepts_the_versions_agents_exist_for():
    """SUPPORTED_QT only validates --qt, but rejecting a version you can build for is a wall."""
    assert "6.5" in agent_registry.SUPPORTED_QT
    assert "6.7" in agent_registry.SUPPORTED_QT
    assert "5.15" in agent_registry.SUPPORTED_QT


def test_an_unreadable_application_still_reports_what_is_installed(tmp_path, monkeypatch):
    """Detection can fail; the error must still be actionable rather than empty."""
    builds = [_build(tmp_path, "6.7")]
    monkeypatch.setattr(agent_registry, "installed", lambda: builds)
    monkeypatch.setattr(agent_registry, "detect_qt_version", lambda exe: None)
    monkeypatch.setattr(agent_registry, "target_platform_tag", lambda exe: "windows-x86_64")
    with pytest.raises(AgentMismatchError) as excinfo:
        agent_registry.resolve(str(Path("app.exe")))
    assert "qt6.7-windows-x86_64-mingw" in str(excinfo.value)
