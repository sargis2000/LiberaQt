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
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from liberaqt import liberaqt
from liberaqt.agent_registry import plugin_key_for


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
        print(f"{exe} does not exist; the kit has no Qt Assistant to drive")
        return 2

    # Offscreen on Linux, where the runner has no display; Windows runners have a desktop, and
    # the launcher refuses headless there.
    headless = sys.platform.startswith("linux")
    with liberaqt(default_timeout=30.0) as lq:
        app = lq.launch(str(exe), timeout=180.0, headless=headless)
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
    for p in problems:
        print(f"FAIL: {p}")
    if not problems:
        print(f"{tag}: agent loads and answers")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
