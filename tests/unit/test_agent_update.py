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
from liberaqt.cli import (
    _BEHIND,
    _CURRENT,
    _DAMAGED,
    _DAMAGED_LOCAL,
    _LOCAL,
    _MANAGED,
    _UNKNOWN,
    _UNREACHABLE,
    _update_state,
)
from liberaqt.errors import LiberaQtError

TAG = "qt6.5-windows-x86_64-mingw"


@pytest.fixture
def cache(tmp_path, monkeypatch):
    """A throwaway download cache, so nothing here can reach the real installed agents."""
    root = tmp_path / "cache"
    monkeypatch.setenv("LIBERAQT_CACHE", str(root))
    monkeypatch.delenv("LIBERAQT_AGENT_PATH", raising=False)
    return root


def _install_at(prefix, key=b"liberaqt_6_5_64_gnu"):
    (prefix / "plugins" / "generic").mkdir(parents=True)
    (prefix / "plugins" / "generic" / "liberaqt.dll").write_bytes(b".." + key + b"..")
    return agent_registry.build_for(TAG, prefix)


@pytest.fixture
def build(cache):
    """An agent installed in the cache, with no manifest yet."""
    return _install_at(cache / "agents" / TAG)


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
    assert f"liberaqt agents build --tag {TAG}" in detail, "the way out has to be named"


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


# ------------------------------------------------------------------ found by the QA pass


def test_a_damaged_binary_is_not_reported_as_current(build, monkeypatch):
    """Comparing only the recorded archive checksum can never notice the bits changed on disk."""
    _manifest(build, archive_sha256="a" * 64)
    (build.library).write_bytes(b"truncated")
    monkeypatch.setattr(agent_install, "published_digest", lambda *a, **k: "a" * 64)
    state, detail = _update_state(build, base_url=None)
    assert state == _DAMAGED
    assert "plugin key" in detail


def test_a_damaged_local_build_is_reported_but_never_replaced(build):
    """Damage does not license overwriting someone's own build with a release."""
    _manifest(build, revision="443bf85")
    build.library.write_bytes(b"truncated")
    state, detail = _update_state(build, base_url=None)
    assert state == _DAMAGED_LOCAL
    assert "agents build" in detail


def test_an_unreachable_location_is_not_nothing_published(build, monkeypatch):
    """No network used to read as "this ABI was never built"."""
    _manifest(build, archive_sha256="a" * 64)

    def unreachable(*a, **k):
        raise LiberaQtError("could not fetch https://example.invalid/x.zip.sha256: timed out")

    monkeypatch.setattr(agent_install, "published_digest", unreachable)
    state, detail = _update_state(build, base_url=None)
    assert state == _UNREACHABLE
    assert "timed out" in detail


def test_a_build_outside_the_cache_is_never_touched(cache, tmp_path, monkeypatch):
    """Reading from LIBERAQT_AGENT_PATH but writing to the cache it shadows never converged."""
    mine = _install_at(tmp_path / "mine" / TAG)
    _manifest(mine, archive_sha256="a" * 64)
    monkeypatch.setattr(agent_install, "published_digest",
                        lambda *a, **k: pytest.fail("a managed build must not be polled"))
    state, detail = _update_state(mine, base_url=None)
    assert state == _MANAGED
    assert str(tmp_path / "mine") in detail


def test_only_the_copy_a_launch_uses_is_considered(cache, tmp_path, monkeypatch):
    shadowing = _install_at(tmp_path / "mine" / TAG)
    _install_at(cache / "agents" / TAG)
    monkeypatch.setenv("LIBERAQT_AGENT_PATH", str(tmp_path / "mine"))

    assert len([b for b in agent_registry.installed() if str(b) == TAG]) == 2
    effective = [b for b in agent_registry.effective() if str(b) == TAG]
    assert [b.root for b in effective] == [shadowing.root]


def test_list_marks_only_the_shadowed_copy(cache, tmp_path, monkeypatch, capsys):
    """It compared id()s of two separate listings, so every agent read "shadowed -- not used"."""
    from liberaqt import cli

    _install_at(tmp_path / "mine" / TAG)
    _install_at(cache / "agents" / TAG)
    other = "qt6.7-windows-x86_64-mingw"
    prefix = cache / "agents" / other
    (prefix / "plugins" / "generic").mkdir(parents=True)
    (prefix / "plugins" / "generic" / "liberaqt.dll").write_bytes(b"..liberaqt_6_7_64_gnu..")
    monkeypatch.setenv("LIBERAQT_AGENT_PATH", str(tmp_path / "mine"))

    assert cli.main(["agents", "list"]) == 0
    rows = [line for line in capsys.readouterr().out.splitlines() if line.startswith("qt")]
    assert len(rows) == 3, rows
    # The LIBERAQT_AGENT_PATH copy carries its directory in brackets and is the one a launch
    # uses; the cached copy of the same tag is the shadowed one; the other tag is simply in use.
    user_copy = [row for row in rows if row.startswith(TAG) and "mine" in row]
    cached_copy = [row for row in rows if row.startswith(TAG) and "mine" not in row]
    assert len(user_copy) == 1 and "shadowed" not in user_copy[0], rows
    assert len(cached_copy) == 1 and "shadowed" in cached_copy[0], rows
    assert not any("shadowed" in row for row in rows if row.startswith(other)), rows
