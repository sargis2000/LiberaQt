"""Prove a freshly built agent loads into a real Qt application and answers.

CI used to stop at "it compiled and the archive installs". That says nothing about whether Qt
will load the plugin -- the failure mode here is silent: wrong compiler, wrong bitness or a
missing plugin key all end with no agent, no port file and no error. So every leg launches the
Qt Assistant that ships in the very kit it built against, with the agent it just installed, and
checks that the agent:

* attached at all (the launch itself waits for its port file),
* is loaded under the plugin key this tag is meant to advertise,
* registers a full command table, and
* resolves a selector against the live widget tree.

Usage: python agent_smoke.py <tag>     (QT_ROOT_DIR names the kit, as install-qt-action sets it)

On failure it reports *why* as GitHub annotations, because annotations are readable without
signing in while job logs are not: the launch error and its log, `liberaqt doctor`, unresolved
shared libraries of the agent (Linux), and Qt's own plugin-loader narration.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from liberaqt import agent_registry, liberaqt
from liberaqt.agent_registry import plugin_key_for
from liberaqt.errors import LiberaQtError
from liberaqt.launcher import build_environment


def annotate(title: str, text: str) -> None:
    """Print a block, as a GitHub error annotation when running in Actions."""
    if os.environ.get("GITHUB_ACTIONS"):
        body = text.replace("%", "%25").replace("\r", "").replace("\n", "%0A")
        print(f"::error title={title}::{body[:8000]}")
    else:
        print(f"== {title}\n{text}")


def diagnose(exe: Path, headless: bool) -> None:
    """Explain a launch that produced no agent, in the order the causes are likely."""
    doctor = subprocess.run([sys.executable, "-m", "liberaqt.cli", "doctor", str(exe)],
                            capture_output=True, text=True)
    annotate("liberaqt doctor", (doctor.stdout + doctor.stderr)[-6000:])

    try:
        agent = agent_registry.resolve(str(exe))
    except LiberaQtError as exc:
        annotate("resolve() refused", f"{exc}\n{exc.hint or ''}")
        return
    if sys.platform.startswith("linux"):
        ldd = subprocess.run(["ldd", str(agent.library)], capture_output=True, text=True)
        annotate(f"ldd {agent.library.name}", ldd.stdout + ldd.stderr)

    # Run the application ourselves with the launcher's own environment plus Qt's plugin-loader
    # narration, and keep every line that concerns generic plugins or the agent.
    env = build_environment(agent, "smoke", Path(tempfile.mkdtemp()), dict(os.environ))
    env["QT_DEBUG_PLUGINS"] = "1"
    if headless:
        env["QT_QPA_PLATFORM"] = "offscreen"
    proc = subprocess.Popen([str(exe)], env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, errors="replace")
    time.sleep(25)
    proc.kill()
    out = proc.communicate()[0]
    wanted = [ln for ln in out.splitlines()
              if re.search(r"liberaqt|generic|cannot|failed|error|not found", ln, re.I)]
    annotate("Qt plugin loader (filtered)", "\n".join(wanted[-120:]) or out[-6000:])


def main(tag: str) -> int:
    """Run the smoke test for one build tag, returning a process exit code."""
    match = re.fullmatch(r"qt(\d+\.\d+)-(\w+-\w+)-(\w+)", tag)
    if not match:
        print(f"not a build tag: {tag}")
        return 2
    qt, platform_tag, compiler = match.groups()
    expected_key = plugin_key_for(qt, platform_tag, compiler)

    root = Path(os.environ["QT_ROOT_DIR"])
    exe = root / "bin" / ("assistant.exe" if sys.platform == "win32" else "assistant")
    if not exe.is_file():
        listing = "\n".join(sorted(p.name for p in exe.parent.iterdir())) if exe.parent.is_dir() \
            else f"{exe.parent} does not exist"
        annotate(f"{tag}: no Qt Assistant in the kit", f"{exe} is missing. bin/ holds:\n{listing}")
        return 2

    # Offscreen on Linux, where the runner has no display; Windows runners have a desktop, and
    # the launcher refuses headless there.
    headless = sys.platform.startswith("linux")
    with liberaqt(default_timeout=30.0) as lq:
        try:
            app = lq.launch(str(exe), timeout=120.0, headless=headless)
        except LiberaQtError as exc:
            log = "\n".join((getattr(exc, "data", None) or {}).get("log", []))
            annotate(f"{tag}: launch failed", f"{exc}\n{exc.hint or ''}\n--- log ---\n{log}")
            diagnose(exe, headless)
            return 1
        caps = app.capabilities
        key = caps.get("abi_key")
        commands = caps.get("commands", [])
        windows = app.windows
        widgets = windows[0].locator("QWidget").count if windows else 0
        print(f"{tag}: abi_key={key} commands={len(commands)} windows={len(windows)} "
              f"widgets={widgets}")

    problems = []
    if key != expected_key:
        problems.append(f"loaded under {key!r}, expected {expected_key!r}")
    if len(commands) < 30:
        problems.append(f"only {len(commands)} commands registered")
    if not windows:
        problems.append("no window reported")
    if widgets < 5:
        problems.append(f"selector engine found only {widgets} widgets")
    if problems:
        annotate(f"{tag}: checks failed", "\n".join(problems) +
                 f"\nabi_key={key} commands={len(commands)} windows={len(windows)} "
                 f"widgets={widgets}")
    else:
        print(f"{tag}: agent loads and answers")
    return 1 if problems else 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1]))
    except Exception:  # noqa: BLE001 - whatever broke, say so where it can be read
        import traceback
        annotate(f"{sys.argv[1]}: smoke test crashed", traceback.format_exc())
        sys.exit(1)
