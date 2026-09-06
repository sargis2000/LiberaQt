"""One port file per process, and the child discovery that falls out of it.

The agent used to write its port to a single path named by ``LIBERAQT_PORT_FILE``. That cannot
survive an application which spawns Qt children of its own: they inherit the injection
environment, load the agent too, and each one overwrites what the last wrote.

Measured on Libero SoC, whose core configurator is a separate ``coreconfig.exe``:

    libero agent port          = 62118
    port file after Configure  = 62158     <- the child's port, over the parent's

Nothing noticed, because the launcher reads the file once during startup and never again.
Anything that re-read it would have been talking to a different process without knowing.

Keying the file on the pid fixes that, and turns the children from a hazard into a feature: the
directory is a list of every agent in the process tree, which is what makes an out-of-process
dialog reachable at all.
"""

import time

import pytest

from liberaqt.launcher import AgentEndpoint, LaunchedProcess, build_environment, read_endpoints


class FakeAgentBuild:
    """Just enough of an AgentBuild for build_environment.

    ``abi_key`` is part of that minimum: the launcher asks every installed agent for its own
    plugin key, because one key cannot serve two ABIs.
    """

    plugin_dir = "/agents/qt6.7/plugins"
    abi_key = "liberaqt_6_7_64_gnu"


class FakePopen:
    def __init__(self, pid=1000, returncode=None):
        self.pid = pid
        self.returncode = returncode

    def poll(self):
        return self.returncode


def _publish(port_dir, pid, port, age=0.0):
    """Write a <pid>.port file as an agent would."""
    path = port_dir / f"{pid}.port"
    path.write_text(str(port), encoding="utf-8")
    if age:
        stamp = time.time() - age
        import os
        os.utime(path, (stamp, stamp))
    return path


# ------------------------------------------------------------------ the environment


def test_the_environment_names_a_directory_not_a_file(tmp_path):
    """A directory is what lets a child publish without overwriting its parent."""
    env = build_environment(FakeAgentBuild(), "tok", tmp_path, base_env={})
    assert env["LIBERAQT_PORT_DIR"] == str(tmp_path)
    assert "LIBERAQT_PORT_FILE" not in env, (
        "the launcher must not set the single-file variable, or children inherit and clobber it"
    )


def test_the_injection_variables_are_still_set(tmp_path):
    """The rest of the contract is unchanged; children still inherit an agent."""
    env = build_environment(FakeAgentBuild(), "tok", tmp_path, base_env={})
    assert env["LIBERAQT_TOKEN"] == "tok"
    assert env["LIBERAQT_PORT"] == "0"
    assert "liberaqt" in env["QT_QPA_GENERIC_PLUGINS"]


# ------------------------------------------------------------------ reading the directory


def test_endpoints_are_read_per_process(tmp_path):
    _publish(tmp_path, 1000, 62118)
    _publish(tmp_path, 2000, 62158)
    assert set(read_endpoints(tmp_path)) == {
        AgentEndpoint(1000, 62118),
        AgentEndpoint(2000, 62158),
    }


def test_a_child_does_not_overwrite_its_parent(tmp_path):
    """The whole point. Both ports survive, where one file would have kept only the last."""
    _publish(tmp_path, 1000, 62118)
    _publish(tmp_path, 2000, 62158)
    ports = {e.pid: e.port for e in read_endpoints(tmp_path)}
    assert ports == {1000: 62118, 2000: 62158}


def test_the_oldest_file_comes_first(tmp_path):
    """The launcher takes the first publisher as the application it started."""
    _publish(tmp_path, 2000, 62158, age=1.0)
    _publish(tmp_path, 1000, 62118, age=60.0)
    assert read_endpoints(tmp_path)[0] == AgentEndpoint(1000, 62118)


def test_half_written_and_junk_files_are_ignored(tmp_path):
    """A file being written right now must not crash the poll; it will be there next time."""
    _publish(tmp_path, 1000, 62118)
    (tmp_path / "2000.port").write_text("", encoding="utf-8")
    (tmp_path / "notapid.port").write_text("62999", encoding="utf-8")
    (tmp_path / "unrelated.txt").write_text("62999", encoding="utf-8")
    assert read_endpoints(tmp_path) == [AgentEndpoint(1000, 62118)]


def test_a_missing_directory_is_empty_not_an_error(tmp_path):
    assert read_endpoints(tmp_path / "does-not-exist") == []


# ------------------------------------------------------------------ the process wrapper


def test_child_agents_exclude_the_application_itself(tmp_path):
    _publish(tmp_path, 1000, 62118)
    _publish(tmp_path, 2000, 62158)
    proc = LaunchedProcess(FakePopen(pid=1000), 62118, "tok", [], tmp_path)

    assert [e.pid for e in proc.agents()] == [1000, 2000] or \
           sorted(e.pid for e in proc.agents()) == [1000, 2000]
    assert proc.child_agents() == [AgentEndpoint(2000, 62158)]


def test_the_agent_pid_may_differ_from_the_process_we_spawned(tmp_path):
    """An application is free to re-exec itself, so the agent can live in a different pid.

    Whatever published first is the application; everything else is a child of it.
    """
    _publish(tmp_path, 1234, 62118)
    _publish(tmp_path, 2000, 62158)
    proc = LaunchedProcess(FakePopen(pid=1000), 62118, "tok", [], tmp_path, agent_pid=1234)

    assert proc.agent_pid == 1234
    assert proc.child_agents() == [AgentEndpoint(2000, 62158)]


def test_no_children_when_nothing_else_published(tmp_path):
    _publish(tmp_path, 1000, 62118)
    proc = LaunchedProcess(FakePopen(pid=1000), 62118, "tok", [], tmp_path)
    assert proc.child_agents() == []


def test_terminate_removes_the_whole_directory(tmp_path):
    """Every process's file goes, not just ours -- children are cleaned up with the tree."""
    _publish(tmp_path, 1000, 62118)
    _publish(tmp_path, 2000, 62158)
    LaunchedProcess(FakePopen(pid=1000, returncode=0), 62118, "tok", [], tmp_path).terminate()
    assert not tmp_path.exists()


# ------------------------------------------------------------------ waiting for a child


class FakeApp:
    """Application.wait_for_child_agent, without a session behind it."""

    def __init__(self, endpoints):
        from liberaqt.application import Application

        self._endpoints = endpoints
        self.wait_for_child_agent = Application.wait_for_child_agent.__get__(self)

    @property
    def child_agents(self):
        return self._endpoints


def test_waiting_returns_a_child_once_it_appears():
    app = FakeApp([AgentEndpoint(2000, 62158)])
    assert app.wait_for_child_agent(timeout=0) == AgentEndpoint(2000, 62158)


def test_waiting_can_demand_a_particular_pid():
    app = FakeApp([AgentEndpoint(2000, 62158), AgentEndpoint(3000, 62199)])
    assert app.wait_for_child_agent(timeout=0, pid=3000).port == 62199


def test_waiting_times_out_when_no_child_arrives():
    from liberaqt.errors import TimeoutError as LiberaQtTimeoutError

    with pytest.raises(LiberaQtTimeoutError):
        FakeApp([]).wait_for_child_agent(timeout=0)
