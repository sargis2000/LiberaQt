"""Transport tests against a fake agent -- a plain socket server speaking the protocol."""

import json
import socket
import threading

import pytest

from qtdriver.errors import ObjectNotFoundError, QtDriverError
from qtdriver.protocol import PROTOCOL_VERSION
from qtdriver.transport import Transport


class FakeAgent:
    """Minimal in-process agent: hello, auth, and a scripted response table."""

    def __init__(self, responses=None, events=None):
        self.responses = responses or {}
        self.events = events or []
        self.requests = []
        self._server = socket.socket()
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(1)
        self.port = self._server.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        conn, _ = self._server.accept()
        with conn:
            conn.sendall(json.dumps({
                "type": "hello", "protocol": PROTOCOL_VERSION, "agent": "test",
                "qt": "6.7.0", "platform": "linux", "pid": 1,
            }).encode() + b"\n")
            for event in self.events:
                conn.sendall(json.dumps(event).encode() + b"\n")
            buf = b""
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    return
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    msg = json.loads(line)
                    if msg.get("type") == "auth":
                        continue
                    self.requests.append(msg)
                    reply = dict(self.responses.get(msg["cmd"],
                                                    {"ok": True, "result": {}}))
                    reply["id"] = msg["id"]
                    conn.sendall(json.dumps(reply).encode() + b"\n")


@pytest.fixture
def agent():
    return FakeAgent()


def test_handshake_reports_agent_metadata(agent):
    t = Transport(port=agent.port, token="tok")
    hello = t.connect()
    assert hello["qt"] == "6.7.0"
    assert hello["protocol"] == PROTOCOL_VERSION
    t.close()


def test_round_trip_call(agent):
    agent.responses["session.ping"] = {"ok": True, "result": {"pong": True}}
    t = Transport(port=agent.port, token="tok")
    t.connect()
    assert t.call("session.ping") == {"pong": True}
    assert agent.requests[0]["cmd"] == "session.ping"
    t.close()


def test_agent_error_becomes_a_typed_exception(agent):
    agent.responses["object.find"] = {
        "ok": False,
        "error": {"code": "not_found", "message": "nope",
                  "data": {"near_misses": [{"class": "QPushButton", "text": "Ok"}]}},
    }
    t = Transport(port=agent.port, token="tok")
    t.connect()
    with pytest.raises(ObjectNotFoundError) as excinfo:
        t.call("object.find", {"selector": {}})
    assert "Ok" in str(excinfo.value)
    t.close()


def test_events_reach_listeners():
    agent = FakeAgent(events=[{"type": "event", "event": "window.opened",
                               "data": {"title": "Login"}}])
    seen = []
    t = Transport(port=agent.port, token="tok")
    t.on("window.opened", seen.append)
    t.connect()
    t.call("session.ping")           # round trip guarantees the event was processed first
    assert seen and seen[0]["title"] == "Login"
    t.close()


def test_call_without_connection_raises():
    t = Transport(port=1, token="tok")
    with pytest.raises(QtDriverError):
        t.call("session.ping")


def test_history_is_bounded(agent):
    t = Transport(port=agent.port, token="tok")
    t.connect()
    t.history_limit = 10
    for _ in range(30):
        t.call("session.ping")
    assert len(t.history) <= 10
    t.close()
