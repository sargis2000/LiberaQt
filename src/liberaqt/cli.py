# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Sargis Khachatryan
"""Command line interface: ``liberaqt <command>``."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import textwrap
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

from . import LiberaQt, __version__, agent_build, agent_install, agent_registry
from .errors import LiberaQtError


def _wrap(text: str, indent: str = "    ", width: int = 94) -> str:
    """Wrap an explanation to the terminal without losing the indent."""
    return "\n".join(textwrap.wrap(text, width=width, initial_indent=indent,
                                   subsequent_indent=indent))


def _report_target(exe: str) -> bool:
    """Diagnose one target executable.

    Distinguishes "no agent installed for this ABI", which the user can fix by building one, from
    "this binary cannot be instrumented at all", which no agent will ever fix.

    Args:
        exe: Path to the application binary.

    Returns:
        True when the agent could be injected into it.
    """
    report = agent_registry.inspect_binary(exe)
    print(f"target   {exe}")

    if report.linkage == "none" or not report.path.exists():
        print(f"  Qt: {'not found in this binary' if report.path.exists() else 'unreadable'}")
        print("  NOT INSTRUMENTABLE")
        print(_wrap(report.reason))
        return False

    described = report.qt_version or "unknown version"
    if report.toolchain:
        described += f", {report.toolchain}"
    if report.arch:
        described += f", {report.arch}"
    print(f"  detected Qt: {described} ({report.linkage} linkage)")
    if report.arch and report.arch != agent_registry.current_platform_tag().split("-")[-1]:
        # "an": x86, x86_64 and arm64 all start with a vowel sound.
        print(_wrap(f"This is an {report.arch} binary on an "
                    f"{agent_registry.current_platform_tag().split('-')[-1]} host, so the agent "
                    f"must be built for {report.arch} too."))

    if report.linkage == "static":
        print("  NOT INSTRUMENTABLE")
        print(_wrap(report.reason))
        return False

    try:
        print(f"  matching agent: {agent_registry.resolve(exe)}")
        return True
    except LiberaQtError as exc:
        print(f"  NO MATCHING AGENT\n{exc}")
        if report.toolchain:
            print(_wrap(f"This binary was built with {report.toolchain}, so the agent has to be "
                        f"too -- an agent for the right Qt version but the wrong compiler will "
                        f"not load."))
        return False


def cmd_doctor(args: argparse.Namespace) -> int:
    """Environment check. First thing to run when something does not work."""
    print(f"liberaqt {__version__}  (protocol 1)")
    print(f"python   {sys.version.split()[0]}  on {agent_registry.current_platform_tag()}")
    print(f"cache    {agent_registry.cache_dir()}")

    builds = agent_registry.installed()
    stale = []
    if builds:
        print("agents installed:")
        for b in builds:
            note = ""
            if not b.advertises_abi_key():
                stale.append(b)
                note = f"   [STALE: no {b.abi_key} key]"
            print(f"  - {b}  ->  {b.library}{note}")
    else:
        print("agents installed: NONE")
        print("  fix: liberaqt agents install --qt 6.7")

    if stale:
        print(_wrap(
            "A stale agent above predates per-ABI plugin keys. It still works for a process of "
            "its own architecture, but Qt binds a plugin key to exactly one library, so while it "
            "is on the path it can lock a child process of a different architecture out of "
            "loading its own agent -- with no agent, no port file and no error to show for it. "
            "Rebuild and reinstall it before driving an application that spawns children of "
            "another bitness."))

    ok = bool(builds)
    if args.exe:
        ok = _report_target(args.exe) and ok

    if sys.platform.startswith("linux"):
        display = bool(__import__("os").environ.get("DISPLAY") or
                       __import__("os").environ.get("WAYLAND_DISPLAY"))
        print(f"display  {'present' if display else 'absent (use --headless or xvfb-run)'}")
        scope = Path("/proc/sys/kernel/yama/ptrace_scope")
        if scope.exists():
            print(f"ptrace_scope {scope.read_text().strip()} (attach mode needs 0)")

    print("\nOK" if ok else "\nPROBLEMS FOUND (see above)")
    return 0 if ok else 1


#: What `agents update` decided about one installed build.
_CURRENT = "current"
_BEHIND = "update available"
_DAMAGED = "damaged"
_DAMAGED_LOCAL = "damaged (local)"
_LOCAL = "local build"
_MANAGED = "not managed"
_UNKNOWN = "cannot tell"
_UNREACHABLE = "could not check"

#: States `update` acts on. Damaged bits are refreshed along with outdated ones: the archive
#: they came from is known, and re-fetching it is exactly the repair.
_ACTIONABLE = (_BEHIND, _DAMAGED)


def _update_state(build, base_url: str | None) -> tuple[str, str]:
    """Decide whether an installed agent is behind what is published.

    Answered from the 64-byte checksum rather than the archive, so checking every ABI costs
    almost nothing. A locally built agent has no published counterpart to compare against and is
    deliberately left alone: overwriting someone's own build with a release would be rude, and
    it is usually *newer*, not older.

    Args:
        build: The installed :class:`~liberaqt.agent_registry.AgentBuild`, which must be the one
            a launch would use (see :func:`~liberaqt.agent_registry.effective`).
        base_url: Where archives live, or None for the configured default.

    Returns:
        ``(state, detail)``.
    """
    # Never touch a directory the user manages. update used to read state from a build on
    # LIBERAQT_AGENT_PATH and then write to the cache, which that very directory shadows -- so
    # it reported "updated" on every run and never converged.
    if not agent_registry.is_cached(build):
        return _MANAGED, f"lives in {build.root.parent}, outside the cache; left as it is"

    manifest = build.manifest
    if not manifest:
        # Name the command, as every other branch does. "Reinstall" was the wrong word for most
        # of these anyway: an agent that predates manifests was usually built here, not installed.
        return _UNKNOWN, (f"no manifest; predates version stamping -- "
                          f"`liberaqt agents build --tag {build}` to stamp one")

    # Lower-cased on both sides: published_digest normalises what it fetches, but the manifest
    # may have been written by something else, and a case difference is not an update.
    installed_digest = str(manifest.get("archive_sha256") or "").lower()

    # The bits on disk, not the record of where they came from. Comparing only the recorded
    # archive checksum answered "has the release moved?" and never "are these bits intact?", so
    # a truncated or patched binary reported "current".
    if not build.advertises_abi_key():
        if not installed_digest:
            # Someone's own build: say so, but still never overwrite it with a release.
            return _DAMAGED_LOCAL, (f"the binary lacks its {build.abi_key} plugin key -- "
                                    f"`liberaqt agents build --tag {build}` to rebuild it")
        return _DAMAGED, f"the binary lacks its {build.abi_key} plugin key -- reinstalling fixes it"

    if not installed_digest:
        return _LOCAL, f"built here at {build.revision}; `liberaqt agents build` to refresh"

    try:
        published = agent_install.published_digest(str(build), base_url=base_url)
    except LiberaQtError as exc:
        return _UNREACHABLE, str(exc).splitlines()[0]
    if not published:
        return _UNKNOWN, "nothing published for this ABI at that location"
    if published == installed_digest:
        return _CURRENT, build.revision
    return _BEHIND, f"published archive differs from the installed one ({build.revision})"


def _agents_update(args: argparse.Namespace) -> int:
    """Report which installed agents are behind what is published, and refresh them.

    Args:
        args: Parsed arguments.

    Returns:
        Process exit code: ``0`` when nothing needs doing (or everything was done), ``1`` from
        ``--check`` when something does -- the ``ruff format --check`` convention, so a CI step
        can gate on it -- and ``2`` if any update failed.
    """
    # One build per tag, the one a launch would actually use. installed() lists a tag once per
    # directory it appears in, which is how update came to act on two copies of one agent.
    builds = agent_registry.effective()
    if args.tag:
        builds = [b for b in builds if str(b) == args.tag]
        if not builds:
            print(f"no agent installed for {args.tag}")
            return 1
    if not builds:
        print("no agents installed")
        return 1

    print(f"checking {len(builds)} agent(s) against "
          f"{agent_install.resolve_base_url(args.base_url)}")
    behind, failed = [], []
    for build in builds:
        state, detail = _update_state(build, args.base_url)
        print(f"  {str(build):38} {state:18} {detail}")
        if state in _ACTIONABLE:
            behind.append(build)

    if not behind:
        print("nothing to update")
        return 0
    if args.check:
        print(f"{len(behind)} agent(s) could be updated; re-run without --check to do it")
        return 1

    for build in behind:
        tag = str(build)
        try:
            prefix = agent_install.install(tag, base_url=args.base_url)
        except LiberaQtError as exc:
            print(f"  {tag}: FAILED -- {exc}")
            failed.append(tag)
            continue
        print(f"  {tag}: updated -> {prefix}")
    return 2 if failed else 0


def cmd_agents(args: argparse.Namespace) -> int:
    """List, install or remove agent binaries.

    Args:
        args: Parsed arguments, carrying the ``agents`` subcommand.

    Returns:
        Process exit code; ``2`` if an install could not be completed.
    """
    if args.agents_command == "list":
        builds = agent_registry.installed()
        if not builds:
            print("no agents installed")
            return 1
        # By location, not identity: effective() lists afresh, so comparing id()s matched nothing
        # and marked every agent on the machine "shadowed".
        in_use = {b.root.resolve() for b in agent_registry.effective()}
        print(f"{'TAG':38} {'REVISION':18} ORIGIN")
        for b in builds:
            manifest = b.manifest
            origin = "local build"
            if manifest.get("archive_sha256"):
                origin = manifest.get("source", "archive")
            elif not manifest:
                origin = "unknown (predates manifests)"
            if not agent_registry.is_cached(b):
                origin += f"  [{b.root.parent}]"
            # A tag listed twice with no word on which copy wins is its own small defect.
            if b.root.resolve() not in in_use:
                origin += "  (shadowed -- not used)"
            print(f"{str(b):38} {b.revision:18} {origin}")
        return 0

    if args.agents_command == "update":
        return _agents_update(args)

    if args.agents_command == "install":
        tag = args.tag or (
            f"qt{args.qt}-{agent_registry.current_platform_tag()}-{args.compiler}"
        )
        try:
            prefix = agent_install.install(tag, source=args.source, base_url=args.base_url)
        except LiberaQtError as exc:
            print(f"install failed: {exc}")
            return 2
        print(f"installed {tag} -> {prefix}")
        return 0

    if args.agents_command == "kits":
        kits = agent_build.discover_kits()
        if not kits:
            print("no Qt kits found")
            print("  looked in: " + ", ".join(str(r) for r in agent_build.DEFAULT_QT_ROOTS))
            return 1
        installed = {str(b) for b in agent_registry.installed()}
        print(f"{'TAG':38} {'AGENT':12} KIT")
        for kit in kits:
            state = "installed" if kit.tag in installed else "-"
            print(f"{kit.tag:38} {state:12} {kit.prefix}")
        return 0

    if args.agents_command == "build":
        try:
            source = agent_build.source_dir(args.source)
            if args.all:
                kits = agent_build.discover_kits()
            else:
                kits = [agent_build.find_kit(qt=args.qt, compiler=args.compiler,
                                             arch=args.arch, tag=args.tag)]
        except LiberaQtError as exc:
            print(f"{exc}")
            return 2

        failures = 0
        for kit in kits:
            print(f"=== {kit.tag} ===")
            try:
                plan = agent_build.plan_build(kit, source, prefix=(
                    Path(args.prefix) if args.prefix else None))
                if args.dry_run:
                    for step in plan.steps:
                        print(f"  $ {subprocess.list2cmdline(step)}")
                    continue
                prefix = agent_build.run_plan(plan)
                print(f"  installed -> {prefix}")
            except LiberaQtError as exc:
                print(f"  FAILED: {exc}")
                failures += 1
        return 2 if failures else 0

    if args.agents_command == "remove":
        for b in agent_registry.installed():
            if str(b) == args.name:
                shutil.rmtree(b.root)
                print(f"removed {b.root}")
                return 0
        print(f"no such agent: {args.name}")
        return 1
    return 1


def _validate_object_map(app, path: str) -> int:
    """Check every entry in an object map still resolves to exactly one object."""
    from .session import ObjectMap
    from .suggest import resolver_for

    object_map = ObjectMap.load(path)
    windows = app.windows
    if not windows:
        print("no windows to validate against", file=sys.stderr)
        return 1

    failures = 0
    for name, selector in sorted(object_map.items()):
        # An entry is valid if it resolves uniquely in *some* window: a dialog's objects are not
        # in the main window, and that is not an error.
        counts = [len(resolver_for(app._session, w.resolve())(selector)) for w in windows]
        best = max(counts) if counts else 0
        if best == 1:
            print(f"  ok         {name:28} {selector}")
        elif best == 0:
            print(f"  MISSING    {name:28} {selector}")
            failures += 1
        else:
            print(f"  AMBIGUOUS  {name:28} {selector}  ({best} matches)")
            failures += 1

    total = len(list(object_map.items()))
    print(f"\n{total - failures}/{total} entries resolve uniquely")
    return 1 if failures else 0


def cmd_inspect(args: argparse.Namespace) -> int:
    """Launch the app and show how to address the objects in it."""
    from .suggest import format_table, resolver_for, suggest_all, summarize

    with LiberaQt(trace=args.trace) as qd:
        app = qd.launch(args.exe, args=args.app_args, qt=args.qt)
        app.wait_for_idle()

        if args.validate:
            return _validate_object_map(app, args.validate)

        for window in app.windows:
            print(f"=== {window!r} ===")
            tree = window.tree(depth=args.depth)
            if args.json:
                print(json.dumps(tree, indent=2)[:200000])
                continue
            suggestions = suggest_all(tree, resolver_for(app._session, window.resolve()),
                                      include_internal=args.all)
            print(format_table(suggestions))
            print()
            print(summarize(suggestions))

        if args.interactive:
            import code
            code.interact(
                banner="liberaqt inspect -- `app`, `win` are bound. Ctrl-D to quit.",
                local={"app": app, "win": app.windows[0] if app.windows else None, "qd": qd},
            )
    return 0


#: The source checkout this module was loaded from, if it was loaded from one.
#:
#: ``src/liberaqt/cli.py`` -> the repository root. From a wheel this lands somewhere harmless
#: (the parent of site-packages), which is never a LiberaQT checkout.
#:
#: Named rather than inlined so a test can monkeypatch it. Inlined, a test of the search order
#: passes under a wheel install and fails under an editable one, because there ``parents[2]``
#: really is a checkout with a real mkdocs.yml.
CHECKOUT_DOCS = Path(__file__).resolve().parents[2]

#: The copy of the documentation sources that travels inside the wheel.
#:
#: ``force-include`` in pyproject.toml puts ``docs/`` and ``mkdocs.yml`` here, so an install that
#: never saw the repository can still serve and build the real documentation.
PACKAGED_DOCS = Path(__file__).resolve().parent / "_docs"

#: Written into every site ``liberaqt docs build`` produces, and the only thing that makes a
#: directory ours to erase or to serve as LiberaQT's.
#:
#: The first version decided by file names instead -- any folder holding a ``404.html`` or a
#: ``sitemap.xml`` -- and mkdocs then erased everything else in it. Hidden, so mkdocs' own clean
#: step (which skips dotfiles) leaves it in place for the next build to find.
SITE_STAMP = ".liberaqt-docs-build"

#: What the ``docs`` extra provides, as ``{distribution: module to import}``. Checking mkdocs
#: alone let a machine with plain mkdocs get as far as a raw "Unrecognised theme name" error.
_DOCS_MODULES = {
    "mkdocs": "mkdocs",
    "mkdocs-material": "material",
    "mkdocstrings": "mkdocstrings",
    "mkdocstrings-python": "mkdocstrings_handlers.python",
}


def is_checkout(path: Path) -> bool:
    """Whether a directory is a LiberaQT source checkout, as opposed to any mkdocs project.

    Args:
        path: A candidate directory.

    Returns:
        True when it holds both ``mkdocs.yml`` and ``src/liberaqt/``.
    """
    return (path / "mkdocs.yml").is_file() and (path / "src" / "liberaqt" / "__init__.py").is_file()


def docs_root(explicit: str | None = None) -> Path:
    """Locate the documentation source: the directory holding ``mkdocs.yml``.

    Searched in order: a directory given on the command line, the nearest LiberaQT checkout at or
    above the working directory, the checkout this module lives in, and the copy packaged into
    the wheel. A checkout is preferred over the packaged copy, so editing ``docs/`` and serving
    them stays one step.

    Only a LiberaQT checkout is found by searching. Any ``mkdocs.yml`` used to count, so a user's
    own documentation was served, and built, under LiberaQT's name; and from inside a checkout's
    ``docs/`` the checkout itself was missed, which built a site into the documentation sources.

    Args:
        explicit: A directory given on the command line, which wins and may be any mkdocs project.

    Returns:
        The directory containing ``mkdocs.yml``, resolved.

    Raises:
        LiberaQtError: It could not be found anywhere, which means this is neither a checkout
            nor a complete install; or the explicit directory is unusable.
    """
    if explicit:
        candidate = Path(explicit).resolve()
        if candidate.is_file():
            raise LiberaQtError(
                f"{candidate} is a file; --source takes the directory that holds mkdocs.yml")
        if not candidate.is_dir():
            raise LiberaQtError(f"{candidate} does not exist")
        if not (candidate / "mkdocs.yml").is_file():
            raise LiberaQtError(f"{candidate} has no mkdocs.yml")
        return candidate

    here = Path.cwd().resolve()
    for base in (here, *here.parents):
        if is_checkout(base):
            return base
    if is_checkout(CHECKOUT_DOCS):
        return CHECKOUT_DOCS
    if (PACKAGED_DOCS / "mkdocs.yml").is_file():
        return PACKAGED_DOCS
    raise LiberaQtError(
        "could not find the documentation sources",
        hint=f"Pass --source <dir>, or run from a source checkout. A wheel carries its own copy "
             f"at {PACKAGED_DOCS}; this install has none, so it is incomplete.",
    )


def _is_ours(site: Path, root: Path) -> bool:
    """Whether a built site is one this command made: stamped, or a checkout's own ``site/``.

    A checkout's ``site/`` is the gitignored output its own mkdocs.yml names, so it is ours by
    construction -- and trusting it keeps a build from before the stamp existed usable.
    """
    return (site / SITE_STAMP).is_file() or (is_checkout(root) and site == root / "site")


def check_site_dir_is_disposable(site_dir: Path, force: bool = False) -> None:
    """Refuse to hand mkdocs a directory it would erase and someone would miss.

    ``mkdocs build`` cleans its destination unconditionally, keeping only dotfiles. So the
    destination must be empty, or a site this command built -- which carries :data:`SITE_STAMP`.
    Naming it with ``--site-dir`` is not consent: ``--site-dir .`` in a project directory used to
    erase every visible file in it, exit 0. ``--force`` is consent, for a directory whose
    contents really are disposable -- but never for a filesystem root, the home directory, or
    anything above the working directory, which no build is ever meant to replace.

    A link is judged by what it points at, and named by it too: through a junction, the files
    erased used to live outside the working tree entirely, while the message named only the link.

    Args:
        site_dir: Where the build would be written, as given (not yet resolved).
        force: The caller passed ``--force``.

    Raises:
        LiberaQtError: The directory is not safe to erase.
    """
    given = Path(site_dir).absolute()
    target = given.resolve()
    shown = str(target) if os.path.normcase(str(target)) == os.path.normcase(str(given)) \
        else f"{given} (a link to {target})"

    cwd = Path.cwd().resolve()
    if target == Path(target.anchor) or target == Path.home().resolve() or target in cwd.parents:
        raise LiberaQtError(
            f"refusing to build into {shown}: mkdocs erases its destination first",
            hint="Choose an empty directory with --site-dir <dir>. --force does not apply here.",
        )
    if not target.exists():
        if os.path.lexists(given):
            raise LiberaQtError(f"{shown} is a link to something that does not exist")
        return
    if not target.is_dir():
        raise LiberaQtError(f"{shown} is a file, not a directory",
                            hint="Choose another destination with --site-dir <dir>.")
    visible = [entry for entry in target.iterdir() if not entry.name.startswith(".")]
    if not visible or (target / SITE_STAMP).is_file() or force:
        return
    raise LiberaQtError(
        f"{shown} holds files that no documentation build put there",
        hint="mkdocs erases its destination. Move them aside, choose an empty directory with "
             "--site-dir <dir>, or pass --force if replacing them is really what you want.",
    )


def _docs_requirements() -> list[str]:
    """The ``docs`` extra's requirements, read from the installed metadata when possible."""
    try:
        from importlib.metadata import requires

        declared = requires("liberaqt") or []
    except Exception:  # noqa: BLE001 - metadata is a convenience; the fallback is always right
        declared = []
    extra = []
    for requirement in declared:
        spec, _, marker = requirement.partition(";")
        if "extra" in marker and '"docs"' in marker.replace("'", '"'):
            extra.append(spec.strip())
    return extra or ["mkdocs-material>=9.5", "mkdocstrings[python]>=0.24"]


def _docs_extra_hint() -> str:
    """A command that installs what the ``docs`` extra provides, and runs as printed.

    Built from the running interpreter and the requirements themselves. The earlier hints asked
    pip for ``liberaqt[docs]``: from a wheel that re-cloned the repository -- impossible offline,
    and it replaced a pinned version with ``main`` -- and ``pip install -e ".[docs]"`` from
    anywhere but a checkout's root installed the user's own project instead. A bare ``pip`` could
    reach a different Python entirely.

    Returns:
        A pasteable command.
    """
    exe = sys.executable
    shown = f'"{exe}"' if " " in exe else exe
    packages = " ".join(f'"{requirement}"' for requirement in _docs_requirements())
    return f"{shown} -m pip install {packages}   (the [docs] extra)"


def _missing_docs_modules() -> list[str]:
    """Distributions of the ``docs`` extra that cannot be imported here."""
    missing = []
    for distribution, module in _DOCS_MODULES.items():
        try:
            found = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            missing.append(distribution)
    return missing


def docs_command(root: Path, host: str, port: int, build: bool = False,
                 site_dir: Path | None = None) -> tuple[list[str], str]:
    """Work out how to serve or build the documentation, and say which way it went.

    Prefers mkdocs, which rebuilds a page as you edit it. Falls back to serving an already-built
    site over :mod:`http.server` -- but only one this command built, so a stranger's ``./site``
    is not served under LiberaQT's name.

    ``site_dir`` exists because ``root`` is not necessarily the caller's to write to: ``site_dir:``
    in mkdocs.yml is resolved against the config file, so a packaged install would otherwise build
    into site-packages. It has to be **absolute**, because mkdocs resolves a relative
    ``--site-dir`` against the config file as well, not against the working directory.

    Args:
        root: Directory holding ``mkdocs.yml``.
        host: Address to bind.
        port: Port to bind.
        build: Build and exit, rather than serving.
        site_dir: Absolute directory to build into. ``None`` leaves mkdocs to its own default.

    Returns:
        ``(argv, description)``.

    Raises:
        LiberaQtError: Neither the ``docs`` extra nor a site of ours is available.
    """
    missing = _missing_docs_modules()
    if not missing:
        if build:
            argv = [sys.executable, "-m", "mkdocs", "build", "--strict"]
            if site_dir is not None:
                argv += ["--site-dir", str(site_dir)]
            return (argv, "mkdocs build")
        return ([sys.executable, "-m", "mkdocs", "serve", "-a", f"{host}:{port}"], "mkdocs serve")

    if build:
        raise LiberaQtError(
            f"building the documentation needs {', '.join(missing)}",
            hint=_docs_extra_hint(),
        )

    candidates = [site_dir] if site_dir is not None else []
    candidates.append(root / "site")
    for site in candidates:
        if (site / "index.html").is_file() and _is_ours(site, root):
            return ([sys.executable, "-m", "http.server", str(port),
                     "--bind", host, "--directory", str(site)],
                    "http.server on the last built site (no live reload)")

    raise LiberaQtError(
        f"serving the documentation needs {', '.join(missing)}, and there is no built site to "
        f"fall back on",
        hint=f"{_docs_extra_hint()}  --  then `liberaqt docs` rebuilds as you edit",
    )


#: ``site_url:`` in mkdocs.yml, whose path is where mkdocs mounts the site -- locally too.
_SITE_URL = re.compile(r"^\s*site_url:\s*(?P<url>\S+)", re.MULTILINE)


def _url_host(host: str) -> str:
    """The host part of a URL a browser can open, for an address a server binds."""
    if host in ("0.0.0.0", "::", ""):
        return "localhost"
    return f"[{host}]" if ":" in host else host


def docs_url(root: Path, host: str, port: int, with_prefix: bool = True) -> str:
    """The address the documentation is actually reachable at.

    mkdocs serves under the path of ``site_url``, so a project published at
    ``https://example.github.io/Thing/`` is served locally at ``/Thing/`` and the bare root only
    redirects there. Printing the root would send a reader one hop off, and ``--open`` would land
    on a redirect. A wildcard bind address is shown as ``localhost``, and an IPv6 one in brackets.

    Args:
        root: Directory holding ``mkdocs.yml``.
        host: Address being bound.
        port: Port being bound.
        with_prefix: False for the static fallback, which serves ``site/`` at the root.

    Returns:
        A URL ending in a slash.
    """
    prefix = ""
    if with_prefix:
        try:
            match = _SITE_URL.search((root / "mkdocs.yml").read_text(encoding="utf-8"))
        except OSError:
            match = None
        if match:
            prefix = urlparse(match.group("url").strip("\"'")).path.strip("/")
    return f"http://{_url_host(host)}:{port}/" + (f"{prefix}/" if prefix else "")


def _port_is_free(host: str, port: int) -> bool:
    """Whether nothing is listening on ``host:port`` yet.

    Exclusive on Windows, where an ordinary bind succeeds beside a server that is already there
    -- which is how two ``liberaqt docs`` came to share port 8000 without a word.
    """
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def cmd_docs(args: argparse.Namespace) -> int:
    """Serve the documentation for local viewing, or build it into a directory.

    Args:
        args: Parsed arguments.

    Returns:
        Process exit code.
    """
    if args.action == "serve" and args.build:
        print("`serve` and --build ask for opposite things; `liberaqt docs build` builds")
        return 2
    build = args.build or args.action == "build"
    try:
        root = docs_root(args.source)
    except LiberaQtError as exc:
        print(f"{exc}")
        return 2

    here = Path.cwd().resolve()
    if not args.source and (here / "mkdocs.yml").is_file() and root != here:
        print(f"note: ignoring {here / 'mkdocs.yml'}, which is not LiberaQT's; "
              f"`--source .` builds it", flush=True)

    # Where `build` writes: a checkout's own site/, or ./site for the packaged copy, which lives
    # in site-packages and must not be written into.
    if args.site_dir:
        site_dir = Path(args.site_dir).absolute()
    elif is_checkout(root):
        site_dir = root / "site"
    else:
        site_dir = here / "site"

    try:
        if build:
            # A site of ours counts as consent to replace it, like --force -- which still never
            # overrides the refusals for a filesystem root, home, or anything above here.
            check_site_dir_is_disposable(site_dir, force=args.force or _is_ours(site_dir, root))
            site_dir = site_dir.resolve()
        elif not _port_is_free(args.host, args.port):
            raise LiberaQtError(f"port {args.port} on {args.host} is already in use",
                                hint="Pass another with --port <n>.")
        argv, how = docs_command(root, args.host, args.port, build=build, site_dir=site_dir)
    except LiberaQtError as exc:
        print(f"{exc}")
        return 2

    # flush=True: mkdocs inherits this stdout and writes to it for as long as it runs, so
    # without it a redirected or piped run shows nothing until the server stops.
    print(f"documentation sources: {root}", flush=True)
    url = docs_url(root, args.host, args.port, with_prefix=how.startswith("mkdocs"))
    if build:
        print(f"building into {site_dir}", flush=True)
    else:
        if "--directory" in argv:
            print(f"serving the site in {argv[argv.index('--directory') + 1]}")
        print(f"serving the documentation with {how}")
        print(f"  {url}")
        print("  press Ctrl+C to stop", flush=True)
        if args.open:
            # Opened before the server is up on purpose: the browser retries, and waiting for a
            # server that never returns would mean never opening it.
            webbrowser.open(url)

    # The child writes paths to the same stream, so it gets the same tolerance for characters the
    # console cannot encode -- and without Material's multi-paragraph MkDocs 2.0 notice.
    env = {**os.environ, "NO_MKDOCS_2_WARNING": "1"}
    env.setdefault("PYTHONIOENCODING", ":backslashreplace")
    try:
        code = subprocess.call(argv, cwd=str(root), env=env)
    except FileNotFoundError as exc:
        print(f"could not run {argv[0]}: {exc}")
        return 2
    except KeyboardInterrupt:
        return 0
    if build and code == 0:
        try:
            (site_dir / SITE_STAMP).write_text(
                "Built by `liberaqt docs build`; safe for it to erase and rebuild.\n",
                encoding="utf-8")
        except OSError:
            pass
    return code


def cmd_record(args: argparse.Namespace) -> int:
    """Record interaction with an application and emit it as a pytest module.

    Runs until the application closes or the user interrupts, then writes the generated test to
    ``--output`` or to stdout.

    **Not working yet**, and it fails immediately rather than recording nothing: the agent does
    not register ``record.start``, so this reports an unsupported-operation error. The client,
    the code generator and the agent's event filter all exist; the wiring does not.

    Args:
        args: Parsed arguments.

    Returns:
        Process exit code.
    """
    from .spy import Recorder

    with LiberaQt() as qd:
        app = qd.launch(args.exe, args=args.app_args, qt=args.qt, record=True)
        recorder = Recorder(app)
        recorder.start()
        print("Recording. Interact with the application, then close it to finish.")
        try:
            while app.is_running:
                import time
                time.sleep(0.2)
        except KeyboardInterrupt:
            pass
        recorder.stop()
        source = recorder.to_python(test_name=args.test_name)
    if args.output:
        Path(args.output).write_text(source, encoding="utf-8")
        print(f"wrote {args.output} ({len(recorder.actions)} actions)")
    else:
        print(source)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Run pytest, so a suite can be started without knowing it is pytest underneath.

    Args:
        args: Parsed arguments; everything after ``run`` is forwarded verbatim.

    Returns:
        Pytest's exit code.
    """
    import subprocess
    cmd = [sys.executable, "-m", "pytest", *args.pytest_args]
    return subprocess.call(cmd)


def build_parser() -> argparse.ArgumentParser:
    """Build the full command line parser.

    Returns:
        A parser whose subcommands each set ``func`` to their handler.
    """
    parser = argparse.ArgumentParser(
        prog="liberaqt",
        # Plain text: this used to be the module docstring, so --help showed raw reST markup.
        description="LiberaQT: UI automation for Qt applications. Check the environment, "
                    "manage agents, inspect a running application, and read the documentation.",
    )
    parser.add_argument("--version", action="version", version=f"liberaqt {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor", help="check the environment")
    p.add_argument("exe", nargs="?", help="optional: application to check against")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("agents", help="manage agent binaries")
    asub = p.add_subparsers(dest="agents_command", required=True)
    asub.add_parser("list")
    pi = asub.add_parser("install")
    pi.add_argument("--qt", default="6.7", choices=list(agent_registry.SUPPORTED_QT))
    pi.add_argument("--compiler", default="gcc")
    pi.add_argument("--tag", help="full build tag, instead of --qt/--compiler")
    pi.add_argument("--from", dest="source", metavar="URL|PATH",
                    help="archive to install from, instead of looking under the base URL")
    pi.add_argument("--base-url",
                    help=f"where <tag>.zip lives (default ${agent_install.BASE_URL_ENV})")
    pu = asub.add_parser("update", help="refresh installed agents from published builds")
    pu.add_argument("--tag", help="only this build tag (default: every installed agent)")
    pu.add_argument("--check", action="store_true",
                    help="report what is behind without downloading anything")
    pu.add_argument("--base-url",
                    help=f"where <tag>.zip lives (default ${agent_install.BASE_URL_ENV}, "
                         "else the public releases page)")
    asub.add_parser("kits", help="Qt kits on this machine an agent can be built from")
    pb = asub.add_parser("build", help="build an agent from an installed Qt kit")
    pb.add_argument("--qt", help="Qt minor version, e.g. 6.7")
    pb.add_argument("--compiler", help="e.g. mingw, msvc2019")
    pb.add_argument("--arch", help="x86, x86_64, arm64")
    pb.add_argument("--tag", help="full kit tag, fixing all three at once")
    pb.add_argument("--all", action="store_true", help="build for every kit found")
    pb.add_argument("--source", help="the agent/ source directory")
    pb.add_argument("--prefix", help="install here instead of the cache")
    pb.add_argument("--dry-run", action="store_true", help="print the commands, run nothing")

    pr = asub.add_parser("remove")
    pr.add_argument("name")
    p.set_defaults(func=cmd_agents)

    p = sub.add_parser("inspect", help="show how to address the objects in a running app")
    p.add_argument("exe")
    p.add_argument("app_args", nargs="*", help="arguments passed to the application")
    p.add_argument("--qt")
    p.add_argument("--depth", type=int, default=-1)
    p.add_argument("--interactive", "-i", action="store_true",
                   help="drop into a REPL with `app` and `win` bound")
    p.add_argument("--json", action="store_true", help="dump the raw object tree instead")
    p.add_argument("--all", action="store_true",
                   help="include Qt's internal objects (qt_scrollarea_viewport and friends)")
    p.add_argument("--validate", metavar="MAP",
                   help="check every entry in an object map still resolves uniquely")
    p.add_argument("--trace", action="store_true")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("record", help="record interactions into a pytest file")
    p.add_argument("exe")
    p.add_argument("app_args", nargs="*")
    p.add_argument("--qt")
    p.add_argument("-o", "--output")
    p.add_argument("--test-name", default="test_recorded")
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("docs", help="serve or build the documentation for local viewing")
    # A verb, because every other noun in this CLI takes one ("agents list", "agents build").
    # A plain positional with choices rather than a nested subparser: nested would force
    # --host/--port/--open/--source to be declared once per verb, and would make bare
    # `liberaqt docs` a special case.
    p.add_argument("action", nargs="?", choices=("serve", "build"), default=None,
                   help="serve the documentation (the default), or build it and exit")
    p.add_argument("--host", default="127.0.0.1", help="address to bind (default 127.0.0.1)")
    p.add_argument("--port", type=int, default=8000, help="port to bind (default 8000)")
    p.add_argument("--open", action="store_true", help="open a browser at the served address")
    p.add_argument("--build", action="store_true",
                   help="older spelling of `liberaqt docs build`")
    p.add_argument("--site-dir",
                   help="where `build` writes the HTML. It is ERASED first, so it must be empty or "
                        "a previous build (default: site/ in a checkout, else ./site)")
    p.add_argument("--force", action="store_true",
                   help="let `build` erase a --site-dir that holds other files")
    p.add_argument("--source", help="directory holding mkdocs.yml")
    p.set_defaults(func=cmd_docs)

    p = sub.add_parser("run", help="thin pytest wrapper")
    p.add_argument("pytest_args", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``liberaqt`` command.

    Driver errors are printed as a one-line message rather than a traceback: a stack trace from
    inside the tool is noise when the actual problem is a missing agent or a bad path.

    Args:
        argv: Arguments to parse. Defaults to ``sys.argv``.

    Returns:
        Process exit code. ``1`` on a driver error, ``130`` on Ctrl-C.
    """
    # A path the console's code page cannot encode -- a non-Latin directory name, with output
    # piped as it is in CI or an IDE -- raised UnicodeEncodeError from a plain print() and killed
    # the command before it did anything. Escape what cannot be encoded rather than dying on it.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):
            pass

    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except LiberaQtError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
