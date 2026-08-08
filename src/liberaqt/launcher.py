"""Start the AUT with the agent injected, and wait for it to call home.

Primary injection path is the Qt generic-plugin mechanism (see docs/INJECTION.md), which needs no
source changes, no ptrace and no elevated privileges -- just three environment variables.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from . import agent_registry
from .errors import LaunchError
from .protocol import STARTUP_TIMEOUT


class LaunchedProcess:
    """A running application we started, and the details needed to talk to its agent.

    Attributes:
        popen: The underlying process.
        port: Loopback port the agent bound.
        token: Shared secret the agent will require.
        log_lines: Captured stdout and stderr, trimmed as it grows.
    """

    def __init__(self, popen: subprocess.Popen, port: int, token: str,
                 log_lines: list[str], port_file: Path):
        self.popen = popen
        self.port = port
        self.token = token
        self.log_lines = log_lines
        self._port_file = port_file

    @property
    def pid(self) -> int:
        """Process id."""
        return self.popen.pid

    @property
    def is_running(self) -> bool:
        """Whether the process is still alive."""
        return self.popen.poll() is None

    def terminate(self, timeout: float = 5.0) -> int:
        """Terminate the process, killing it if it does not go quietly.

        Args:
            timeout: Seconds to allow for a graceful exit before killing, and again after.

        Returns:
            The process exit code.
        """
        if self.is_running:
            self.popen.terminate()
            try:
                self.popen.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.popen.kill()
                self.popen.wait(timeout=timeout)
        try:
            self._port_file.unlink(missing_ok=True)
        except OSError:
            pass
        return self.popen.returncode or 0


def build_environment(agent: agent_registry.AgentBuild, token: str, port_file: Path,
                      base_env: dict[str, str] | None = None,
                      record: bool = False) -> dict[str, str]:
    """Build the child environment that makes Qt load the agent.

    The whole injection mechanism is these few variables: Qt instantiates every plugin named in
    ``QT_QPA_GENERIC_PLUGINS`` during ``QGuiApplication`` construction, and the agent stays inert
    unless ``LIBERAQT_TOKEN`` is set. Existing values are prepended to rather than replaced, so
    an application that needs its own plugin path keeps working.

    Args:
        agent: The agent build to load.
        token: Per-launch secret the agent will require from clients.
        port_file: File the agent writes its chosen port to.
        base_env: Environment to extend. Defaults to the current one.
        record: Whether to start the agent in recorder mode.

    Returns:
        The environment for the child process.
    """
    env = dict(base_env or os.environ)

    existing = env.get("QT_PLUGIN_PATH", "")
    plugin_path = str(agent.plugin_dir)
    env["QT_PLUGIN_PATH"] = f"{plugin_path}{os.pathsep}{existing}" if existing else plugin_path

    generic = env.get("QT_QPA_GENERIC_PLUGINS", "")
    env["QT_QPA_GENERIC_PLUGINS"] = f"{generic},liberaqt" if generic else "liberaqt"

    env["LIBERAQT_TOKEN"] = token
    env["LIBERAQT_PORT"] = "0"                     # 0 -> agent binds an ephemeral port
    env["LIBERAQT_PORT_FILE"] = str(port_file)
    if record:
        env["LIBERAQT_RECORD"] = "1"

    # Deterministic-ish UI: disable OS animations and scale rounding surprises.
    env.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
    return env


def launch(executable: str, args: list[str] | None = None, cwd: str | None = None,
           env: dict[str, str] | None = None, qt: str | None = None,
           timeout: float = STARTUP_TIMEOUT, headless: bool = False,
           record: bool = False) -> LaunchedProcess:
    """Start an application with the agent injected and wait for it to report its port.

    Args:
        executable: Path to the binary, or a name on ``PATH``.
        args: Arguments for the application itself.
        cwd: Working directory for the new process.
        env: Base environment to extend.
        qt: Force a Qt version instead of detecting it from the binary.
        timeout: Seconds to wait for the agent to report its port.
        headless: Use the offscreen QPA platform. Linux only.
        record: Start the agent in recorder mode.

    Returns:
        The running process, with the port and token needed to connect.

    Raises:
        LaunchError: The executable is missing, exited before the agent connected, the agent
            never reported a port, or headless was requested off Linux.
        AgentMismatchError: No installed agent matches the application's Qt build.
    """
    exe = Path(executable)
    if not exe.exists() and not _on_path(executable):
        raise LaunchError(f"executable not found: {executable}")

    agent = agent_registry.resolve(str(exe), qt=qt)
    token = secrets.token_hex(16)
    port_file = Path(tempfile.mkdtemp(prefix="liberaqt-")) / "port"

    child_env = build_environment(agent, token, port_file, env, record=record)
    if headless:
        if sys.platform.startswith("linux"):
            child_env.setdefault("QT_QPA_PLATFORM", "offscreen")
        else:
            raise LaunchError("headless mode is only supported on Linux (offscreen QPA)")

    cmd = [str(exe), *(args or [])]
    popen = subprocess.Popen(
        cmd, cwd=cwd, env=child_env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )

    log_lines: list[str] = []
    threading.Thread(target=_pump, args=(popen, log_lines), daemon=True).start()

    port = _await_port(popen, port_file, timeout, log_lines)
    return LaunchedProcess(popen, port, token, log_lines, port_file)


def _on_path(name: str) -> bool:
    from shutil import which
    return which(name) is not None


def _pump(popen: subprocess.Popen, sink: list[str]) -> None:
    if popen.stdout is None:
        return
    for line in popen.stdout:
        sink.append(line.rstrip("\n"))
        if len(sink) > 2000:
            del sink[:1000]


def _await_port(popen: subprocess.Popen, port_file: Path, timeout: float,
                log_lines: list[str]) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port_file.exists():
            text = port_file.read_text().strip()
            if text.isdigit():
                return int(text)
        if popen.poll() is not None:
            raise LaunchError(
                f"the application exited with code {popen.returncode} before the agent connected",
                hint="Run the executable by hand to check it starts at all.",
                data={"log": log_lines[-40:]},
            )
        time.sleep(0.05)

    popen.kill()
    raise LaunchError(
        f"the liberaqt agent did not report a port within {timeout:.0f}s",
        hint=(
            "Most likely causes, in order:\n"
            "    1. Agent/Qt ABI mismatch  -> liberaqt doctor\n"
            "    2. A bundled qt.conf overrides QT_PLUGIN_PATH -> use LD_PRELOAD mode\n"
            "    3. The app never constructs a QGuiApplication -> use the in-app embed API\n"
            "  Re-run with QT_DEBUG_PLUGINS=1 for Qt's full plugin loader trace."
        ),
        data={"log": log_lines[-40:]},
    )
