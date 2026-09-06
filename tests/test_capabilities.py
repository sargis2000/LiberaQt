"""What the agent says it can do, and how the client uses it.

Two questions get confused easily, so they are answered separately. The **protocol version** is a
strict gate on the wire format: a mismatch refuses the connection outright, because an agent left
over from an older install would otherwise fail much later and much more confusingly.
**Capabilities** describe features within that format, and move independently -- ``Cmd`` lists more
names than any one agent registers, and Quick support is decided when the agent is compiled by
whether Qt Quick was found.
"""

import pytest

from liberaqt.protocol import Cmd
from liberaqt.session import Session


class FakeTransport:
    """Just enough transport to carry a hello payload."""

    def __init__(self, hello):
        self.hello = hello


def _session(hello):
    return Session(FakeTransport(hello))


CAPABLE = {
    "protocol": 1,
    "agent": "0.1.0",
    "qt": "5.15.1",
    "capabilities": {
        "commands": ["object.find", "input.click", "sync.wait_idle"],
        "quick": False,
        "abi": 64,
        "abi_key": "liberaqt_64",
    },
}


# ------------------------------------------------------------------ reading them


def test_capabilities_come_through_from_the_hello():
    caps = _session(CAPABLE).capabilities
    assert caps["abi"] == 64
    assert caps["quick"] is False


def test_commands_are_a_set_of_names():
    assert _session(CAPABLE).commands == frozenset(
        {"object.find", "input.click", "sync.wait_idle"})


def test_the_agent_version_is_exposed_for_diagnostics():
    """Reported, deliberately not enforced: the protocol version is the compatibility gate."""
    assert _session(CAPABLE).agent_version == "0.1.0"


def test_capabilities_are_a_copy_not_the_live_payload():
    """A caller poking at the returned dict must not corrupt what the transport holds."""
    session = _session(CAPABLE)
    session.capabilities["abi"] = 32
    assert session.capabilities["abi"] == 64


# ------------------------------------------------------------------ acting on them


def test_a_registered_command_is_supported():
    assert _session(CAPABLE).supports(Cmd.FIND) is True


def test_an_unregistered_command_is_not():
    """The 38-versus-32 gap, answerable now without calling and being refused."""
    assert _session(CAPABLE).supports(Cmd.QUICK_EVALUATE) is False


@pytest.mark.parametrize("hello", [
    {"protocol": 1},                                  # agent predating capabilities
    {"protocol": 1, "capabilities": {}},              # present but empty
    {"protocol": 1, "capabilities": {"quick": True}},  # no command list in it
])
def test_an_agent_that_does_not_say_is_assumed_capable(hello):
    """"Unknown" must not read as "missing".

    An older agent reports nothing, and refusing to call it would break a working setup; the call
    itself still fails cleanly with UnsupportedOperationError if the command really is absent.
    """
    assert _session(hello).supports(Cmd.QUICK_EVALUATE) is True


def test_missing_capabilities_read_as_empty_rather_than_raising():
    session = _session({"protocol": 1})
    assert session.capabilities == {}
    assert session.commands == frozenset()
    assert session.agent_version == ""


def test_a_null_capability_block_is_tolerated():
    """The wire is JSON, so a null is possible where a map is expected."""
    session = _session({"protocol": 1, "capabilities": None})
    assert session.capabilities == {}
    assert session.commands == frozenset()
