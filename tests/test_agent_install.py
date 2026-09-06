"""Installing an agent from an archive.

Everything here runs against a zip built in a temp directory, so it needs no network and no Qt --
which is the point: the install path is the one piece of plumbing a user hits before anything else
works, and it should be provable without the thing it installs.
"""

import hashlib
import io
import zipfile

import pytest

from liberaqt import agent_registry
from liberaqt.agent_install import AgentInstallError, archive_name, install

TAG = "qt5.15-windows-x86_64-msvc2019"


def _archive(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, body in members.items():
            archive.writestr(name, body)
    return buffer.getvalue()


def _good_archive(key: bytes = b"liberaqt_5_15_64_msvc", prefix: str = "") -> bytes:
    return _archive({
        f"{prefix}plugins/generic/liberaqt.dll": b"..liberaqt.." + key + b"..",
        f"{prefix}include/liberaqt/embed.h": b"// header",
    })


@pytest.fixture
def cache(tmp_path, monkeypatch):
    """Point the registry's cache at a temp directory."""
    monkeypatch.setattr(agent_registry, "cache_dir", lambda: tmp_path / "cache")
    monkeypatch.setattr(agent_registry, "search_paths", lambda: [tmp_path / "cache" / "agents"])
    return tmp_path


def _write(path, data):
    path.write_bytes(data)
    return str(path)


# ------------------------------------------------------------------ the happy path


def test_an_archive_is_unpacked_into_the_tagged_prefix(cache, tmp_path):
    source = _write(tmp_path / "a.zip", _good_archive())
    prefix = install(TAG, source=source)
    assert prefix == cache / "cache" / "agents" / TAG
    assert (prefix / "plugins" / "generic" / "liberaqt.dll").is_file()


def test_a_wrapping_top_level_directory_is_stripped(cache, tmp_path):
    """Release tooling usually wraps everything in one folder; that must not nest the prefix."""
    source = _write(tmp_path / "a.zip", _good_archive(prefix=f"{TAG}/"))
    prefix = install(TAG, source=source)
    assert (prefix / "plugins" / "generic" / "liberaqt.dll").is_file()


def test_reinstalling_replaces_what_was_there(cache, tmp_path):
    source = _write(tmp_path / "a.zip", _good_archive())
    install(TAG, source=source)
    stale = cache / "cache" / "agents" / TAG / "leftover.txt"
    stale.write_text("from the previous install")
    install(TAG, source=source)
    assert not stale.exists(), "a reinstall must not leave the old tree behind"


# ------------------------------------------------------------------ verification


def test_a_matching_checksum_is_accepted(cache, tmp_path):
    data = _good_archive()
    source = _write(tmp_path / "a.zip", data)
    (tmp_path / "a.zip.sha256").write_text(f"{hashlib.sha256(data).hexdigest()}  a.zip")
    assert install(TAG, source=source).is_dir()


def test_a_wrong_checksum_refuses_the_install(cache, tmp_path):
    source = _write(tmp_path / "a.zip", _good_archive())
    (tmp_path / "a.zip.sha256").write_text("0" * 64)
    with pytest.raises(AgentInstallError, match="checksum mismatch"):
        install(TAG, source=source)
    assert not (cache / "cache" / "agents" / TAG).exists()


def test_a_missing_checksum_is_not_fatal(cache, tmp_path):
    """Not every host publishes one, and refusing would push people around this entirely."""
    source = _write(tmp_path / "a.zip", _good_archive())
    assert install(TAG, source=source).is_dir()


def test_an_agent_without_its_abi_key_is_rejected(cache, tmp_path):
    """The failure this whole mechanism exists to prevent, caught at install time."""
    source = _write(tmp_path / "a.zip", _good_archive(key=b"nothing"))
    with pytest.raises(AgentInstallError, match="liberaqt_5_15_64_msvc"):
        install(TAG, source=source)


def test_an_archive_for_the_wrong_layout_is_rejected(cache, tmp_path):
    source = _write(tmp_path / "a.zip", _archive({"readme.txt": b"no plugin here"}))
    with pytest.raises(AgentInstallError, match="plugins/generic"):
        install(TAG, source=source)


def test_a_corrupt_archive_says_so(cache, tmp_path):
    source = _write(tmp_path / "a.zip", b"this is not a zip file")
    with pytest.raises(AgentInstallError, match="zip"):
        install(TAG, source=source)


def test_a_member_escaping_the_prefix_is_refused(cache, tmp_path):
    """An archive is remote input; `..` in a member name must not write outside the prefix."""
    source = _write(tmp_path / "a.zip", _archive({"../escaped.txt": b"nope"}))
    with pytest.raises(AgentInstallError, match="unsafe path"):
        install(TAG, source=source)


# ------------------------------------------------------------------ where it looks


def test_the_base_url_supplies_the_archive_name(cache, tmp_path, monkeypatch):
    (tmp_path / "host").mkdir()
    _write(tmp_path / "host" / archive_name(TAG), _good_archive())
    assert install(TAG, base_url=str(tmp_path / "host")).is_dir()


def test_the_environment_supplies_the_base_url(cache, tmp_path, monkeypatch):
    (tmp_path / "host").mkdir()
    _write(tmp_path / "host" / archive_name(TAG), _good_archive())
    monkeypatch.setenv("LIBERAQT_AGENT_BASE_URL", str(tmp_path / "host"))
    assert install(TAG).is_dir()


def test_with_nowhere_to_look_it_says_how_to_build_one(cache, monkeypatch):
    """The message has to be actionable: there is no public download location yet."""
    monkeypatch.delenv("LIBERAQT_AGENT_BASE_URL", raising=False)
    with pytest.raises(AgentInstallError) as excinfo:
        install(TAG)
    text = str(excinfo.value)
    assert "LIBERAQT_AGENT_BASE_URL" in text
    assert "cmake" in text


def test_a_missing_archive_is_reported_not_swallowed(cache, tmp_path):
    with pytest.raises(AgentInstallError, match="could not read"):
        install(TAG, source=str(tmp_path / "nothing.zip"))
