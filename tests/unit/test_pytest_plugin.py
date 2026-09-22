"""The pytest plugin: configuration precedence, failure diagnostics, and what they contain.

Nothing covered this module before, and a QA pass found four high-severity defects in it that had
been there since the first commit: the command line losing to ``liberaqt.toml``, ``app_session``
failures leaving no evidence at all, a misspelt config key silently skipping a whole suite, and
diagnostics filenames that collided -- or, on Windows, vanished into NTFS alternate data streams.

The configuration tests run real inner pytest sessions through ``pytester``, since precedence is
a property of how pytest, the plugin and the file interact, not of any one function.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from liberaqt import pytest_plugin
from liberaqt.pytest_plugin import TRACE_DIR, _stem, _write_diagnostics
from liberaqt.transport import Transport

pytest_plugins = ["pytester"]

REPORTS_TIMEOUT = "def test_x(liberaqt_config):\n    print('TIMEOUT', liberaqt_config['timeout'])\n"


def _run(pytester, *args):
    return pytester.runpytest("-s", "-p", "no:cacheprovider", *args)


# ------------------------------------------------------------------ precedence


def test_the_command_line_beats_the_config_file(pytester):
    """The standard CI move: a generous checked-in config, shortened on the command line."""
    pytester.makefile(".toml", liberaqt="[liberaqt]\ntimeout = 10\n")
    pytester.makepyfile(REPORTS_TIMEOUT)
    _run(pytester, "--liberaqt-timeout", "1").stdout.fnmatch_lines(["*TIMEOUT 1.0*"])


def test_the_config_file_beats_the_default(pytester):
    pytester.makefile(".toml", liberaqt="[liberaqt]\ntimeout = 10\n")
    pytester.makepyfile(REPORTS_TIMEOUT)
    _run(pytester).stdout.fnmatch_lines(["*TIMEOUT 10*"])


def test_with_neither_the_default_applies(pytester):
    pytester.makepyfile(REPORTS_TIMEOUT)
    _run(pytester).stdout.fnmatch_lines([f"*TIMEOUT {pytest_plugin.DEFAULT_TIMEOUT}*"])


# ------------------------------------------------------------------ a config that is ignored


def test_an_unknown_key_is_named(pytester):
    """A misspelt `executable` turned a whole live suite into skips, which read as a pass."""
    pytester.makefile(".toml", liberaqt='[liberaqt]\nexecutible = "app.exe"\n')
    pytester.makepyfile(REPORTS_TIMEOUT)
    result = _run(pytester)
    result.stdout.fnmatch_lines(["*unknown key(s) executible*"])


def test_a_tool_section_is_pointed_out(pytester):
    pytester.makefile(".toml", liberaqt='[tool.liberaqt]\nexecutable = "app.exe"\n')
    pytester.makepyfile(REPORTS_TIMEOUT)
    # fnmatch reads [...] as a character class, so the literal brackets are escaped as [[] and []].
    wanted = "*has [[]tool.liberaqt[]]; this file wants [[]liberaqt[]]*"
    _run(pytester).stdout.fnmatch_lines([wanted])


def test_the_skip_names_the_file_it_looked_at(pytester):
    """Identical messages for "no file" and "file without an executable" helped nobody."""
    pytester.makefile(".toml", liberaqt="[liberaqt]\ntimeout = 3\n")
    pytester.makepyfile("def test_x(app):\n    pass\n")
    result = _run(pytester, "-rs")
    result.stdout.fnmatch_lines(["*no `executable` in *liberaqt.toml*"])


def test_the_skip_says_when_there_is_no_file_at_all(pytester):
    pytester.makepyfile("def test_x(app):\n    pass\n")
    result = _run(pytester, "-rs")
    result.stdout.fnmatch_lines(["*no *liberaqt.toml, no --liberaqt-exe, no LIBERAQT_EXE*"])


# ------------------------------------------------------------------ the executable path


def test_a_relative_executable_is_relative_to_the_config(tmp_path):
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "app.exe").write_bytes(b"")
    (tmp_path / "liberaqt.toml").write_text('[liberaqt]\nexecutable = "bin/app.exe"\n')
    cfg = pytest_plugin._load_config(tmp_path)
    assert Path(cfg["executable"]) == tmp_path / "bin" / "app.exe"


def test_a_relative_object_map_is_relative_to_the_config(tmp_path):
    (tmp_path / "objects.yaml").write_text("login:\n  ok: QPushButton\n")
    (tmp_path / "liberaqt.toml").write_text('[liberaqt]\nobject_map = "objects.yaml"\n')
    cfg = pytest_plugin._load_config(tmp_path)
    assert Path(cfg["object_map"]) == tmp_path / "objects.yaml"


def test_a_bare_name_is_left_for_path_lookup(tmp_path):
    (tmp_path / "liberaqt.toml").write_text('[liberaqt]\nexecutable = "someapp"\n')
    assert pytest_plugin._load_config(tmp_path)["executable"] == "someapp"


# ------------------------------------------------------------------ diagnostics filenames


@pytest.mark.parametrize("nodeid", [
    "tests/test_a.py::test_x[C:/Qt/6.7.3/mingw_64/bin/assistant.exe]",
    "tests/test_a.py::test_x[a|b]",
    "tests/test_a.py::test_x[<>\"?*]",
])
def test_a_stem_is_safe_on_every_filesystem(nodeid):
    """A `:` in a parametrize id sent the artifacts into an NTFS alternate data stream."""
    stem = _stem(nodeid)
    assert stem
    assert not set(stem) - set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-")


def test_two_tests_with_the_same_name_do_not_collide():
    """The bare test name collided between files, and the second screenshot replaced the first."""
    assert _stem("pkg_a/test_x.py::test_same") != _stem("pkg_b/test_x.py::test_same")


def test_a_very_long_id_is_bounded():
    assert len(_stem("t.py::test[" + "x" * 1000 + "]")) <= 150


# ------------------------------------------------------------------ what gets captured


class FakeApp:
    def __init__(self, pid, closed=False, fail_screenshot=False):
        self.pid = pid
        self._closed = closed
        self._fail = fail_screenshot
        self._session = SimpleNamespace(transport=SimpleNamespace(history=[{"pid": pid}]))

    def screenshot(self, path):
        if self._fail:
            raise RuntimeError("nothing to grab")
        Path(path).write_bytes(b"png")


def _item(tmp_path, nodeid="tests/test_a.py::test_x"):
    return SimpleNamespace(config=SimpleNamespace(rootdir=tmp_path), nodeid=nodeid)


def test_every_running_application_is_captured(tmp_path):
    """app_session got nothing, and a second process's failure was reported from the first."""
    driver = SimpleNamespace(_apps=[FakeApp(101), FakeApp(202)])
    _write_diagnostics(_item(tmp_path), driver)
    written = sorted(p.name for p in (tmp_path / TRACE_DIR).iterdir())
    assert any("pid101" in n and n.endswith(".png") for n in written)
    assert any("pid202" in n and n.endswith(".png") for n in written)
    assert sum(n.endswith(".jsonl") for n in written) == 2


def test_a_closed_application_is_skipped(tmp_path):
    driver = SimpleNamespace(_apps=[FakeApp(101, closed=True), FakeApp(202)])
    _write_diagnostics(_item(tmp_path), driver)
    written = [p.name for p in (tmp_path / TRACE_DIR).iterdir()]
    assert written and not any("pid" in n for n in written), "one app running needs no suffix"


def test_a_failed_capture_is_reported_not_swallowed(tmp_path):
    """A silent `except: pass` made a missing screenshot indistinguishable from a passing test."""
    driver = SimpleNamespace(_apps=[FakeApp(101, fail_screenshot=True)])
    with pytest.warns(UserWarning, match="no screenshot"):
        _write_diagnostics(_item(tmp_path), driver)
    # The protocol log is still worth having when the screenshot is not.
    assert list((tmp_path / TRACE_DIR).glob("*.jsonl"))


def test_the_previous_runs_evidence_is_cleared_at_start(tmp_path):
    """A green run left the last red run's screenshot in place, indistinguishable from a new one."""
    trace = tmp_path / TRACE_DIR
    trace.mkdir()
    (trace / "old.png").write_bytes(b"png")
    (trace / "old.jsonl").write_text("{}")
    (trace / "notes.txt").write_text("the user's, not ours")

    session = SimpleNamespace(config=SimpleNamespace(rootdir=tmp_path))
    pytest_plugin.pytest_sessionstart(session)

    assert sorted(p.name for p in trace.iterdir()) == ["notes.txt"]


def test_an_xdist_worker_clears_nothing(tmp_path):
    trace = tmp_path / TRACE_DIR
    trace.mkdir()
    (trace / "other_worker.png").write_bytes(b"png")
    session = SimpleNamespace(config=SimpleNamespace(rootdir=tmp_path, workerinput={}))
    pytest_plugin.pytest_sessionstart(session)
    assert (trace / "other_worker.png").exists()


# ------------------------------------------------------------------ what the trace contains


def test_the_auth_token_never_reaches_the_trace():
    """liberaqt-trace/ is uploaded by CI; the token is a credential for an RCE surface."""
    transport = Transport(token="s3cret-per-launch-token")
    transport._record({"type": "auth", "token": "s3cret-per-launch-token", "protocol": 1}, True)
    recorded = transport.history[-1]["msg"]
    assert recorded["token"] == "<redacted>"
    assert "s3cret" not in repr(transport.history)


def test_redaction_does_not_touch_what_is_sent():
    """The history gets a copy; the auth message itself must still carry the real token."""
    message = {"type": "auth", "token": "s3cret"}
    Transport(token="s3cret")._record(message, True)
    assert message["token"] == "s3cret"


# ------------------------------------------------------------------ headless, both ways


REPORTS_HEADLESS = (
    "def test_x(liberaqt_config):\n    print('HEADLESS', liberaqt_config['headless'])\n"
)


def test_the_command_line_can_turn_headless_off(pytester):
    """`headless = true` hard-failed every test on Windows, and nothing could switch it off."""
    pytester.makefile(".toml", liberaqt="[liberaqt]\nheadless = true\n")
    pytester.makepyfile(REPORTS_HEADLESS)
    _run(pytester, "--no-liberaqt-headless").stdout.fnmatch_lines(["*HEADLESS False*"])


def test_the_command_line_can_turn_headless_on(pytester):
    pytester.makepyfile(REPORTS_HEADLESS)
    _run(pytester, "--liberaqt-headless").stdout.fnmatch_lines(["*HEADLESS True*"])


def test_every_known_key_is_present_without_a_config_file(pytester):
    """liberaqt_config["input_mode"] raised KeyError whenever there was no liberaqt.toml."""
    pytester.makepyfile(
        "from liberaqt.pytest_plugin import CONFIG_KEYS\n"
        "def test_x(liberaqt_config):\n"
        "    assert CONFIG_KEYS <= set(liberaqt_config), set(liberaqt_config)\n"
        "    assert liberaqt_config['input_mode'] is None\n"
    )
    _run(pytester).assert_outcomes(passed=1)


# ------------------------------------------------------------------ what the history keeps


def test_the_session_prologue_survives_a_long_retry_loop():
    """A default-timeout retry loop is exactly 200 messages; "the last 200" kept nothing else."""
    transport = Transport()
    transport._record({"type": "auth", "protocol": 1}, True)
    transport._record({"id": 1, "cmd": "window.list"}, True)
    for i in range(5000):
        transport._record({"id": 100 + i, "cmd": "object.find"}, True)

    kept = [e["msg"] for e in transport.history]
    assert len(kept) == transport.history_limit
    # The handshake is what makes the rest of a trace readable.
    assert kept[0] == {"type": "auth", "protocol": 1}
    assert kept[1]["cmd"] == "window.list"
    assert kept[-1]["id"] == 100 + 4999, "and the end is still the end"


def test_a_screenshot_is_not_stored_a_second_time():
    """The PNG was already on disk beside the trace, and was 126 KB of base64 inside it too."""
    transport = Transport()
    transport._record({"id": 7, "result": {"png": "A" * 200_000}}, False)
    stored = transport.history[-1]["msg"]["result"]["png"]
    assert stored == "<200000 chars elided>"


def test_a_huge_model_is_bounded():
    """A 34,517-row to_records() is short strings, which the length limit alone never touches."""
    transport = Transport()
    transport._record({"id": 8, "result": {"rows": [{"0": "x"}] * 34_517}}, False)
    rows = transport.history[-1]["msg"]["result"]["rows"]
    assert len(rows) < 100
    assert rows[-1] == "<34467 more items elided>"


def test_elision_never_touches_what_was_received():
    transport = Transport()
    message = {"id": 9, "result": {"png": "A" * 10_000}}
    transport._record(message, False)
    assert len(message["result"]["png"]) == 10_000


def test_a_limit_below_the_head_still_bounds_the_history():
    """With the head larger than the limit, the trim started past the end and never happened."""
    transport = Transport()
    transport.history_limit = 10
    for i in range(500):
        transport._record({"id": i}, True)
    assert len(transport.history) == 10
