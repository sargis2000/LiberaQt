"""Deciding whether an installed agent is behind what is published.

An installed agent used to be an anonymous ``.dll``: nothing on disk recorded which source built
it, so "I fixed the Qt 6.5 agent and pushed -- is mine current?" had no answer and ``doctor``
could only detect the single historical case of a missing plugin key. A manifest at the install
prefix root, plus the 64-byte checksum published beside each archive, makes the question
answerable without downloading anything.
"""

import json

import pytest

from liberaqt import agent_install, agent_registry
from liberaqt.cli import _BEHIND, _CURRENT, _LOCAL, _UNKNOWN, _update_state

TAG = "qt6.5-windows-x86_64-mingw"


@pytest.fixture
def build(tmp_path):
    """An installed agent with no manifest yet."""
    prefix = tmp_path / TAG
    (prefix / "plugins" / "generic").mkdir(parents=True)
    (prefix / "plugins" / "generic" / "liberaqt.dll").write_bytes(b"..liberaqt_6_5_64_gnu..")
    return agent_registry.AgentBuild(qt="6.5", platform_tag="windows-x86_64",
                                     compiler="mingw", root=prefix)


def _manifest(build, **fields):
    payload = {"schema": 1, "revision": "abc1234", **fields}
    with open(build.root / agent_registry.MANIFEST_NAME, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)


# ------------------------------------------------------------------ reading the manifest


def test_an_install_without_a_manifest_reports_unknown(build):
    assert build.manifest == {}
    assert build.revision == "unknown"


def test_a_manifest_supplies_the_revision(build):
    _manifest(build, revision="443bf85")
    assert build.revision == "443bf85"


def test_unreadable_json_does_not_raise(build):
    """A corrupt manifest must degrade to "unknown", not take the whole command down."""
    (build.root / agent_registry.MANIFEST_NAME).write_text("{not json", encoding="utf-8")
    assert build.manifest == {}
    assert build.revision == "unknown"


def test_a_manifest_that_is_not_an_object_is_ignored(build):
    (build.root / agent_registry.MANIFEST_NAME).write_text("[1, 2, 3]", encoding="utf-8")
    assert build.manifest == {}


# ------------------------------------------------------------------ deciding what to do


def test_a_build_with_no_manifest_cannot_be_judged(build):
    state, detail = _update_state(build, base_url=None)
    assert state == _UNKNOWN
    assert "reinstall" in detail


def test_a_local_build_is_left_alone(build, monkeypatch):
    """It has no published counterpart, and is usually newer rather than older."""
    _manifest(build, revision="443bf85-dirty")
    monkeypatch.setattr(agent_install, "published_digest",
                        lambda *a, **k: pytest.fail("a local build must not be polled"))
    state, detail = _update_state(build, base_url=None)
    assert state == _LOCAL
    assert "agents build" in detail


def test_a_matching_digest_is_current(build, monkeypatch):
    _manifest(build, archive_sha256="a" * 64)
    monkeypatch.setattr(agent_install, "published_digest", lambda *a, **k: "a" * 64)
    assert _update_state(build, base_url=None)[0] == _CURRENT


def test_a_different_digest_means_an_update(build, monkeypatch):
    """The case this exists for: the agent was fixed and republished."""
    _manifest(build, archive_sha256="a" * 64)
    monkeypatch.setattr(agent_install, "published_digest", lambda *a, **k: "b" * 64)
    state, detail = _update_state(build, base_url=None)
    assert state == _BEHIND
    assert "differs" in detail


def test_nothing_published_for_this_abi_is_not_an_update(build, monkeypatch):
    """Qt 5.15 msvc2015 and the arm64 kits have no runner; that is not "out of date"."""
    _manifest(build, archive_sha256="a" * 64)
    monkeypatch.setattr(agent_install, "published_digest", lambda *a, **k: "")
    state, detail = _update_state(build, base_url=None)
    assert state == _UNKNOWN
    assert "nothing published" in detail


def test_a_case_difference_is_not_an_update(build, monkeypatch):
    """Some tools write the digest in upper case and some in lower; same archive either way."""
    _manifest(build, archive_sha256="A" * 64)
    monkeypatch.setattr(agent_install, "published_digest", lambda *a, **k: "a" * 64)
    assert _update_state(build, base_url=None)[0] == _CURRENT
