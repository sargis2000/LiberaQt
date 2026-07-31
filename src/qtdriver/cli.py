"""Command line interface: ``qtdriver <command>``."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import List, Optional

from . import QtDriver, __version__, agent_registry
from .errors import QtDriverError


def cmd_doctor(args: argparse.Namespace) -> int:
    """Environment check. First thing to run when something does not work."""
    print(f"qtdriver {__version__}  (protocol 1)")
    print(f"python   {sys.version.split()[0]}  on {agent_registry.current_platform_tag()}")
    print(f"cache    {agent_registry.cache_dir()}")

    builds = agent_registry.installed()
    if builds:
        print("agents installed:")
        for b in builds:
            print(f"  - {b}  ->  {b.library}")
    else:
        print("agents installed: NONE")
        print("  fix: qtdriver agents install --qt 6.7")

    ok = bool(builds)
    if args.exe:
        detected = agent_registry.detect_qt_version(args.exe)
        print(f"target   {args.exe}")
        print(f"  detected Qt: {detected or 'unknown'}")
        try:
            build = agent_registry.resolve(args.exe)
            print(f"  matching agent: {build}")
        except QtDriverError as exc:
            ok = False
            print(f"  NO MATCHING AGENT\n{exc}")

    if sys.platform.startswith("linux"):
        display = bool(__import__("os").environ.get("DISPLAY") or
                       __import__("os").environ.get("WAYLAND_DISPLAY"))
        print(f"display  {'present' if display else 'absent (use --headless or xvfb-run)'}")
        scope = Path("/proc/sys/kernel/yama/ptrace_scope")
        if scope.exists():
            print(f"ptrace_scope {scope.read_text().strip()} (attach mode needs 0)")

    print("\nOK" if ok else "\nPROBLEMS FOUND (see above)")
    return 0 if ok else 1


def cmd_agents(args: argparse.Namespace) -> int:
    if args.agents_command == "list":
        builds = agent_registry.installed()
        if not builds:
            print("no agents installed")
            return 1
        for b in builds:
            print(f"{b}\t{b.library}")
        return 0

    if args.agents_command == "install":
        # Real implementation downloads from GitHub Releases and verifies a checksum.
        target = agent_registry.cache_dir() / "agents" / (
            f"qt{args.qt}-{agent_registry.current_platform_tag()}-{args.compiler}"
        )
        print(f"would download agent for Qt {args.qt} into {target}")
        print("not implemented yet -- build from source instead:")
        print("  cmake -S agent -B build/agent -DCMAKE_PREFIX_PATH=$QTDIR")
        print("  cmake --build build/agent --parallel")
        print(f"  cmake --install build/agent --prefix {target}")
        return 2

    if args.agents_command == "remove":
        for b in agent_registry.installed():
            if str(b) == args.name:
                shutil.rmtree(b.root)
                print(f"removed {b.root}")
                return 0
        print(f"no such agent: {args.name}")
        return 1
    return 1


def cmd_inspect(args: argparse.Namespace) -> int:
    """Launch the app and dump (or browse) its object tree."""
    with QtDriver(trace=args.trace) as qd:
        app = qd.launch(args.exe, args=args.app_args, qt=args.qt)
        app.wait_for_idle()
        for window in app.windows:
            print(f"=== {window!r} ===")
            print(json.dumps(window.tree(depth=args.depth), indent=2)[:200000])
        if args.interactive:
            import code
            code.interact(
                banner="qtdriver inspect -- `app`, `win` are bound. Ctrl-D to quit.",
                local={"app": app, "win": app.windows[0] if app.windows else None, "qd": qd},
            )
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    from .spy import Recorder

    with QtDriver() as qd:
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
    import subprocess
    cmd = [sys.executable, "-m", "pytest", *args.pytest_args]
    return subprocess.call(cmd)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="qtdriver", description=__doc__)
    parser.add_argument("--version", action="version", version=f"qtdriver {__version__}")
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
    pr = asub.add_parser("remove")
    pr.add_argument("name")
    p.set_defaults(func=cmd_agents)

    p = sub.add_parser("inspect", help="dump or browse the object tree of a running app")
    p.add_argument("exe")
    p.add_argument("app_args", nargs="*", help="arguments passed to the application")
    p.add_argument("--qt")
    p.add_argument("--depth", type=int, default=-1)
    p.add_argument("--interactive", "-i", action="store_true")
    p.add_argument("--trace", action="store_true")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("record", help="record interactions into a pytest file")
    p.add_argument("exe")
    p.add_argument("app_args", nargs="*")
    p.add_argument("--qt")
    p.add_argument("-o", "--output")
    p.add_argument("--test-name", default="test_recorded")
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("run", help="thin pytest wrapper")
    p.add_argument("pytest_args", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_run)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except QtDriverError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
