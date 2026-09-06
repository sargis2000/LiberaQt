# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Sargis Khachatryan
"""Start the AUT with the agent injected, and wait for it to call home.

Primary injection path is the Qt generic-plugin mechanism (see docs/INJECTION.md), which needs no
source changes, no ptrace and no elevated privileges -- only a handful of environment variables,
set by :func:`build_environment`. It is also the *only* mode implemented: the LD_PRELOAD and
Windows DLL-injection fallbacks that INJECTION.md describes have a README each and no source.
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import NamedTuple

from . import agent_registry
from .errors import LaunchError
from .protocol import STARTUP_TIMEOUT


class AgentEndpoint(NamedTuple):
    """An agent listening somewhere in the launched process tree.

    Attributes:
        pid: Process the agent lives in.
        port: Loopback port it is listening on.
    """

    pid: int
    port: int


def read_endpoints(port_dir: Path) -> list[AgentEndpoint]:
    """Every agent that has published a port under ``port_dir``.

    One file per process, named ``<pid>.port``, so a child that loads the agent through inherited
    environment shows up here instead of overwriting its parent.

    Args:
        port_dir: Directory the agents write into.

    Returns:
        Endpoints, oldest file first, skipping anything half-written.
    """
    if not port_dir.is_dir():
        return []
    found = []
    for path in sorted(port_dir.glob("*.port"), key=lambda p: p.stat().st_mtime):
        try:
            text = path.read_text().strip()
        except OSError:
            continue    # being written right now; it will be there next poll
        if text.isdigit() and path.stem.isdigit():
            found.append(AgentEndpoint(pid=int(path.stem), port=int(text)))
    return found


class LaunchedProcess:
    """A running application we started, and the details needed to talk to its agent.

    Attributes:
        popen: The underlying process.
        port: Loopback port the agent bound.
        token: Shared secret the agent will require.
        log_lines: Captured stdout and stderr, trimmed as it grows.
    """

    def __init__(self, popen: subprocess.Popen, port: int, token: str,
                 log_lines: list[str], port_dir: Path, agent_pid: int | None = None):
        self.popen = popen
        self.port = port
        self.token = token
        self.log_lines = log_lines
        self.port_dir = port_dir
        #: Process the agent we connected to actually runs in. Usually the process we spawned,
        #: but not necessarily: an application is free to re-exec itself on startup.
        self.agent_pid = agent_pid if agent_pid is not None else popen.pid

    def agents(self) -> list[AgentEndpoint]:
        """Every agent currently published in this process tree, ours included."""
        return read_endpoints(self.port_dir)

    def child_agents(self) -> list[AgentEndpoint]:
        """Agents in processes the application spawned, rather than the one we launched.

        A Qt child inherits the injection environment and so loads the agent itself. That is how
        an out-of-process dialog becomes drivable -- Libero's core configurator is a separate
        ``coreconfig.exe``, and this is what makes it reachable.
        """
        return [e for e in self.agents() if e.pid != self.agent_pid]

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
            shutil.rmtree(self.port_dir, ignore_errors=True)
        except OSError:
            pass
        return self.popen.returncode or 0


#: Asked for last, so an agent built before per-ABI keys existed still loads. It collides across
#: ABIs by definition, which is the whole reason the specific keys exist.
LEGACY_PLUGIN_SPEC = "liberaqt"


def generic_plugin_specs(agent: agent_registry.AgentBuild) -> list[str]:
    """Plugin specs to name in ``QT_QPA_GENERIC_PLUGINS``, in the order Qt should try them.

    Every installed agent's own key, so a child process of any ABI in the tree can ask for the
    plugin it is able to load, and each is refused the others harmlessly. The chosen agent goes
    first, and the bare ``liberaqt`` last for builds predating the keys.

    Args:
        agent: The agent chosen for the process being launched.

    Returns:
        The specs, without duplicates.
    """
    specs = [agent.abi_key]
    try:
        others = agent_registry.installed()
    except OSError:
        others = []
    for build in others:
        if build.exists() and build.abi_key not in specs:
            specs.append(build.abi_key)
    specs.append(LEGACY_PLUGIN_SPEC)
    return specs


def sibling_plugin_dirs(agent: agent_registry.AgentBuild) -> list[str]:
    """Plugin directories of every *other* installed agent.

    An application tree is not one ABI. Libero SoC is 32-bit and spawns a 64-bit SmartTime; every
    Qt child inherits this environment, so a path carrying only the parent's plugin leaves the
    child with a binary it cannot load. Qt skips a plugin of the wrong architecture in silence,
    which yields a GUI process with no agent, no port file and no error -- indistinguishable at a
    glance from a process that never built a GUI at all.

    **Listing them is only half of it**, and the other half is why they are keyed by pointer
    size. Qt binds a plugin key to exactly one library -- ``qLoadPlugin`` takes the first match
    on the path and never tries a second::

        if (QObject *object = qLoadPlugin<QObject, QGenericPlugin>(loader(), driver, spec))
            return object;
        return nullptr;

    So while every build claimed only ``liberaqt``, the first directory listed owned that key and
    every other architecture was locked out. Measured, not inferred: with the 64-bit directory
    placed first, 32-bit Libero could no longer load its own agent and never reported a port.

    Pointer size alone does not settle it either, which cost a second round of the same bug: a
    64-bit MinGW plugin and a 64-bit MSVC plugin are equally unloadable in each other's process.
    Each build therefore advertises a key naming its Qt version, pointer size *and* compiler --
    see :func:`~liberaqt.agent_registry.plugin_key_for` -- and :func:`generic_plugin_specs` names
    every installed one, so each process can ask for the plugin it is able to load.

    Args:
        agent: The agent chosen for the process being launched, which the caller lists first.

    Returns:
        The other agents' plugin directories.
    """
    try:
        others = agent_registry.installed()
    except OSError:
        return []
    return [str(build.plugin_dir) for build in others
            if build.plugin_dir != agent.plugin_dir and build.exists()]


def build_environment(agent: agent_registry.AgentBuild, token: str, port_dir: Path,
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
        port_dir: Directory each agent writes its ``<pid>.port`` into.
        base_env: Environment to extend. Defaults to the current one.
        record: Whether to start the agent in recorder mode.

    Returns:
        The environment for the child process.
    """
    env = dict(base_env or os.environ)

    existing = env.get("QT_PLUGIN_PATH", "")
    # The chosen agent first, then every other ABI installed: a child process of a different
    # bitness has to find a plugin it can actually load. See sibling_plugin_dirs.
    paths = [str(agent.plugin_dir), *sibling_plugin_dirs(agent)]
    if existing:
        paths.append(existing)
    env["QT_PLUGIN_PATH"] = os.pathsep.join(paths)

    # Ask for every ABI-specific key as well as the plain one. A process loads whichever it can
    # and refuses the rest harmlessly ("No such plugin for spec"), which is what lets a 32-bit
    # parent and a 64-bit child both find an agent on one shared path -- see sibling_plugin_dirs
    # for why a single key cannot do that. ``liberaqt`` stays last so agents installed before the
    # keys existed still load, and arming twice is safe: Agent::start() guards on m_started.
    specs = ",".join(generic_plugin_specs(agent))
    generic = env.get("QT_QPA_GENERIC_PLUGINS", "")
    env["QT_QPA_GENERIC_PLUGINS"] = f"{generic},{specs}" if generic else specs

    env["LIBERAQT_TOKEN"] = token
    env["LIBERAQT_PORT"] = "0"                     # 0 -> agent binds an ephemeral port
    # A directory rather than a file: children inherit this environment and publish their own
    # <pid>.port inside it, instead of overwriting a single shared path. See Agent::publishPort.
    env["LIBERAQT_PORT_DIR"] = str(port_dir)
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
    port_dir = Path(tempfile.mkdtemp(prefix="liberaqt-"))

    child_env = build_environment(agent, token, port_dir, env, record=record)
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

    endpoint = _await_port(popen, port_dir, timeout, log_lines)
    return LaunchedProcess(popen, endpoint.port, token, log_lines, port_dir,
                           agent_pid=endpoint.pid)


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


def _await_port(popen: subprocess.Popen, port_dir: Path, timeout: float,
                log_lines: list[str]) -> AgentEndpoint:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        # The first agent to publish is the application we launched; anything appearing later is
        # a child it spawned, and belongs to child_agents() rather than to this handshake.
        endpoints = read_endpoints(port_dir)
        if endpoints:
            return endpoints[0]
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
