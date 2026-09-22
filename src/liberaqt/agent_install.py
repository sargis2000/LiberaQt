# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Sargis Khachatryan
"""Fetch and unpack an agent build into the cache.

An agent is a Qt plugin that has to match the application's Qt minor version *and* its
compiler/stdlib ABI, so there is no single binary to ship: there is one per tag, and a machine
driving applications of several ABIs needs several. This module puts one in place, given an
archive to take it from.

Where archives come from is deliberately configurable rather than hard-wired to a release URL.
Set ``LIBERAQT_AGENT_BASE_URL`` to point at any directory-like location -- a releases page, an
internal file share, a directory of files on disk -- laid out as::

    <base>/<tag>.zip
    <base>/<tag>.zip.sha256      (optional; verified when present)

so a team can host builds of its own without a fork. ``--from`` overrides it entirely for a
one-off install, which is also how this is tested: a local zip needs no network.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from . import agent_registry
from .errors import LiberaQtError

#: Where archives are fetched from unless ``--from`` says otherwise. Overridable so that an
#: organisation can host its own builds -- a vendor-specific agent need not leave the network.
BASE_URL_ENV = "LIBERAQT_AGENT_BASE_URL"

#: Public builds, used when nothing else is configured.
#:
#: ``releases/latest/download`` is an alias GitHub resolves to the newest published release, so
#: this does not have to be bumped per release and an install always gets current bits. A tag
#: with no published archive 404s, which ``install`` reports as "not published for this ABI"
#: rather than as a broken default.
DEFAULT_BASE_URL = "https://github.com/sargis2000/LiberaQt/releases/latest/download"


def resolve_base_url(base_url: str | None = None) -> str:
    """Where to look for archives: the argument, then the environment, then public releases.

    Args:
        base_url: An explicit location, usually from ``--base-url``.

    Returns:
        A base URL with no trailing slash.
    """
    return (base_url or os.environ.get(BASE_URL_ENV) or DEFAULT_BASE_URL).rstrip("/")


def published_digest(tag: str, base_url: str | None = None) -> str:
    """The sha256 published alongside an archive, or ``""`` when there is none.

    A checksum file is 64 bytes where the archive is megabytes, which is what lets
    ``liberaqt agents update`` answer "am I behind?" without downloading anything.

    Args:
        tag: Build tag.
        base_url: Where ``<tag>.zip.sha256`` lives.

    Returns:
        The hex digest in lower case, or ``""`` when nothing is published for this tag.

    Raises:
        AgentInstallError: The location could not be reached, or answered with something that is
            not a checksum. Neither is the same as "not published", and the caller has to be able
            to tell.
    """
    source = f"{resolve_base_url(base_url)}/{archive_name(tag)}.sha256"
    try:
        text = _read(source).decode("utf-8", "replace")
    except NotPublishedError:
        return ""
    # Accept a bare digest and the "<digest>  <name>" / "<digest> *<name>" shasum formats alike.
    parts = text.strip().split()
    digest = parts[0].lower() if parts else ""
    if not _DIGEST.match(digest):
        # An HTML error page served with a 200, or a truncated upload. Comparing its first word
        # against a real digest reported "update available" forever.
        raise AgentInstallError(
            f"{source} is not a sha256 checksum",
            hint=f"It starts {text.strip()[:40]!r}. The release is half-published or mis-served.",
        )
    return digest


#: Files an agent archive must contain, relative to its root, for the install to be usable.
REQUIRED_MEMBER = "plugins/generic"


class AgentInstallError(LiberaQtError):
    """An agent archive could not be fetched, verified, or unpacked."""

    retryable = False


class NotPublishedError(AgentInstallError):
    """The location answered, and there is nothing there: a 404, or a missing local file.

    Kept distinct from every other fetch failure because the two mean opposite things to
    ``agents update``. "Nothing published for this ABI" is a settled answer; "could not reach the
    releases page" is not an answer at all, and reporting the one as the other told a user with no
    network that their ABI had never been built.
    """


#: What a build tag may look like: ``qt<major>.<minor>-<platform>-<arch>-<compiler>``.
#:
#: The tag becomes a directory name under the cache, so it is remote-influenced input as far as
#: the filesystem is concerned -- ``--tag ../../x`` used to unpack outside the cache entirely.
_TAG = re.compile(r"^qt\d+\.\d+-[A-Za-z0-9_]+-[A-Za-z0-9_]+-[A-Za-z0-9_-]+$")

#: A SHA-256 digest, as published beside an archive.
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def install_prefix(tag: str) -> Path:
    """The cache directory a tag installs into, after checking the tag is safe to use as one.

    Args:
        tag: A build tag.

    Returns:
        ``<cache>/agents/<tag>``.

    Raises:
        AgentInstallError: The tag is not tag-shaped, or would resolve outside the cache.
    """
    agents = agent_registry.cache_dir() / "agents"
    if not _TAG.match(tag) or ".." in tag:
        raise AgentInstallError(
            f"{tag!r} is not a build tag",
            hint="A tag looks like qt6.7-windows-x86_64-mingw; `liberaqt agents kits` lists them.",
        )
    prefix = agents / tag
    # Belt and braces: the pattern already rules out separators, but the containment check is
    # the property that actually matters, so assert it directly.
    if prefix.resolve().parent != agents.resolve():
        raise AgentInstallError(f"{tag!r} would install outside {agents}")
    return prefix


def archive_name(tag: str) -> str:
    """Archive file name for a build tag.

    Args:
        tag: A tag such as ``"qt6.7-linux-x86_64-gcc"``.

    Returns:
        The file name, e.g. ``"qt6.7-linux-x86_64-gcc.zip"``.
    """
    return f"{tag}.zip"


def _read(location: str) -> bytes:
    """Read a URL or a local path as bytes.

    Args:
        location: An URL, or a filesystem path.

    Returns:
        The contents.

    Raises:
        AgentInstallError: It could not be read.
    """
    parsed = urllib.parse.urlparse(location)
    if parsed.scheme in ("http", "https", "file"):
        try:
            with urllib.request.urlopen(location, timeout=60) as response:  # noqa: S310
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise NotPublishedError(f"nothing published at {location}") from exc
            raise AgentInstallError(f"could not fetch {location}: {exc}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise AgentInstallError(f"could not fetch {location}: {exc}") from exc
    try:
        return Path(location).read_bytes()
    except FileNotFoundError as exc:
        raise NotPublishedError(f"could not read {location}: nothing is there") from exc
    except OSError as exc:
        raise AgentInstallError(f"could not read {location}: {exc}") from exc


def _verify_checksum(data: bytes, expected: str, source: str) -> None:
    """Check an archive against its published SHA-256.

    Args:
        data: The archive bytes.
        expected: Expected digest; a leading ``<hash>  <name>`` form is accepted.
        source: Where it came from, for the error message.

    Raises:
        AgentInstallError: The digest does not match.
    """
    wanted = expected.split()[0].strip().lower()
    if not _DIGEST.match(wanted):
        # Otherwise an HTML error page served with a 200 reads as "the download is corrupt",
        # which sends the reader after the archive when it is the checksum that is broken.
        raise AgentInstallError(
            f"{source}.sha256 is not a sha256 checksum",
            hint=f"It starts {expected.strip()[:40]!r}. The release is half-published or "
                 "mis-served; the archive itself may be fine.",
        )
    actual = hashlib.sha256(data).hexdigest()
    if actual != wanted:
        raise AgentInstallError(
            f"checksum mismatch for {source}: expected {wanted}, got {actual}",
            hint="The download is corrupt or the archive was replaced. Do not use it.",
        )


def _unpack(data: bytes, staging: Path) -> Path:
    """Unpack an agent archive into a staging directory, and find the prefix inside it.

    Deliberately touches nothing but ``staging``. It used to remove the live install prefix and
    move the new bits onto it *before* ``install`` had checked them, so a refused reinstall
    replaced a working agent with the refused one -- and ``doctor`` then vouched for it.

    Members are checked for path traversal before anything is written: an archive is remote input,
    and ``..`` in a member name would otherwise write outside the prefix.

    Args:
        data: Archive bytes.
        staging: An empty directory to extract into.

    Returns:
        The directory within ``staging`` that holds ``plugins/generic/``.

    Raises:
        AgentInstallError: The archive is malformed or has an unsafe member.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for member in archive.namelist():
                target = (staging / member).resolve()
                if not str(target).startswith(str(staging.resolve())):
                    raise AgentInstallError(f"unsafe path in archive: {member!r}")
            archive.extractall(staging)
    except zipfile.BadZipFile as exc:
        raise AgentInstallError(f"not a readable zip archive: {exc}") from exc

    # Tolerate an archive that wraps everything in one top-level directory, which is what most
    # release tooling produces.
    root = staging
    entries = list(staging.iterdir())
    if len(entries) == 1 and entries[0].is_dir() and not (staging / "plugins").exists():
        root = entries[0]
    if not (root / REQUIRED_MEMBER).is_dir():
        raise AgentInstallError(
            f"archive has no {REQUIRED_MEMBER}/ directory",
            hint="An agent install prefix must contain plugins/generic/liberaqt.{dll,so}.",
        )
    return root


def _stamp_manifest(prefix: Path, source: str, digest: str) -> None:
    """Add provenance to the manifest the archive carried.

    The agent's CMake records what the build *is*; this adds where this copy of it came from,
    which is what lets a later ``agents update`` compare against the published checksum without
    downloading the archive again. An archive predating manifests has none, and gets a minimal
    one rather than nothing, so the install is still identifiable.

    Args:
        prefix: The install prefix.
        source: URL or path the archive was read from.
        digest: sha256 of the archive as installed.
    """
    path = prefix / agent_registry.MANIFEST_NAME
    try:
        with open(path, encoding="utf-8") as handle:
            manifest = json.load(handle)
        if not isinstance(manifest, dict):
            manifest = {}
    except (OSError, ValueError):
        manifest = {"schema": 1, "revision": "unknown"}

    manifest["source"] = source
    manifest["archive_sha256"] = digest
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except OSError:
        # Provenance is a convenience; a read-only cache should not fail an otherwise good
        # install over it.
        pass


def install(tag: str, source: str | None = None, base_url: str | None = None) -> Path:
    """Install an agent build into the cache and check it is usable.

    Args:
        tag: Build tag, e.g. ``"qt6.7-linux-x86_64-gcc"``.
        source: An explicit archive URL or path, overriding ``base_url`` entirely.
        base_url: Where to look for ``<tag>.zip``. Defaults to ``$LIBERAQT_AGENT_BASE_URL``.

    Returns:
        The install prefix.

    Raises:
        AgentInstallError: Nothing to fetch from, the download failed its checksum, the archive
            was malformed, or what it unpacked to is not a working agent.
    """
    prefix = install_prefix(tag)
    if source is None:
        source = f"{resolve_base_url(base_url)}/{archive_name(tag)}"

    try:
        data = _read(source)
    except AgentInstallError as exc:
        raise AgentInstallError(
            f"could not fetch an agent archive for {tag}",
            hint=(
                f"Tried {source}\n"
                f"  {exc}\n"
                "Not every ABI is published -- Qt 5.15 msvc2015 and some 32-bit kits "
                "have no runner to build them on. Build from a local Qt kit instead:\n"
                f"  liberaqt agents build --tag {tag}\n"
                f"Or point {BASE_URL_ENV} at somewhere that holds <tag>.zip."
            ),
        ) from exc

    # A checksum is verified when published and not demanded when it is not: an internal file
    # share may not have one, and refusing to install would push people to bypass this entirely.
    try:
        digest = _read(f"{source}.sha256").decode("utf-8", "replace")
    except AgentInstallError:
        digest = ""
    if digest.strip():
        _verify_checksum(data, digest, source)

    # Unpacked beside the live prefix, under a dotted name the registry skips, so nothing ever
    # sees a half-installed agent and the final swap is a rename on one filesystem.
    agents_dir = prefix.parent
    agents_dir.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".liberaqt-install-", dir=agents_dir))
    try:
        root = _unpack(data, staging)

        # Check *these* bits. Looking the tag up with installed() found whichever build came
        # first on the search path -- a copy on LIBERAQT_AGENT_PATH, not the one just unpacked.
        build = agent_registry.build_for(tag, root)
        if not build.exists():
            raise AgentInstallError(
                f"the archive for {tag} has no {build.library.name}",
                hint="The archive does not match the tag it is named for.",
            )
        if not build.advertises_abi_key():
            raise AgentInstallError(
                f"the archive for {tag} does not carry the {build.abi_key} plugin key",
                hint=(
                    "It is built for a different ABI than its name says, predates per-ABI plugin "
                    "keys, or is damaged. Whichever it is, it would load into nothing -- Qt binds "
                    "a plugin key to one library -- so it was not installed, and anything already "
                    "installed under this tag is untouched."
                ),
            )
        _check_manifest_agrees(build, tag)

        # Only now, with the bits accepted: record where they came from, so a later `agents
        # update` can tell whether the release moved by fetching 64 bytes, not the archive.
        _stamp_manifest(root, source, hashlib.sha256(data).hexdigest())

        if prefix.exists():
            shutil.rmtree(prefix)
        shutil.move(str(root), str(prefix))
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return prefix


def _check_manifest_agrees(build: agent_registry.AgentBuild, tag: str) -> None:
    """Refuse an archive whose own manifest names a different ABI than its tag.

    The binary has already been checked for the right plugin key, so a disagreement here means a
    mislabelled release: the bits load, but ``agents list`` would repeat the wrong Qt version or
    key back to anyone asking what is installed.

    Args:
        build: The staged build, whose binary already passed the plugin-key check.
        tag: The tag it is being installed as.

    Raises:
        AgentInstallError: The manifest's plugin key contradicts the tag.
    """
    claimed = build.manifest.get("plugin_key")
    if claimed and claimed != build.abi_key:
        raise AgentInstallError(
            f"the archive for {tag} carries a manifest for {claimed}, not {build.abi_key}",
            hint="The release is mislabelled. Report it rather than installing it.",
        )
