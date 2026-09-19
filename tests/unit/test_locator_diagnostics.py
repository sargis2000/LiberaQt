"""Locator error diagnostics.

Regression cover for a client/agent protocol mismatch: the ambiguity diagnostic used to ask
``object.info`` for a plural ``handles`` list, while PROTOCOL.md defines it as singular
``handle``. The agent resolved the missing key to an empty handle and raised StaleObjectError,
which then replaced the AmbiguousSelectorError it was supposed to describe.

These use ``timeout=0``, which the retry engine documents as exactly one attempt, so each test
observes a single pass rather than however many the poll loop happens to fit in.
"""

import pytest

from liberaqt.errors import AmbiguousSelectorError, StaleObjectError
from liberaqt.errors import TimeoutError as LiberaQtTimeoutError
from liberaqt.locator import Locator
from liberaqt.selectors import parse
from liberaqt.waits import TimeoutPolicy


class RecordingSession:
    """Answers object.find with fixed handles and records every call it receives."""

    def __init__(self, handles, info_fails=False):
        self.timeouts = TimeoutPolicy(0.5)
        self.slowmo = 0.0
        self.calls = []
        self._handles = handles
        self._info_fails = info_fails

    def call(self, cmd, params=None, timeout=None, selector=None):
        self.calls.append((cmd, params or {}))
        if cmd == "object.find":
            return {"handles": list(self._handles)}
        if cmd == "object.info":
            if self._info_fails:
                raise StaleObjectError("handle no longer refers to a live object")
            return {"handle": params["handle"], "class": "QPushButton",
                    "text": f"button {params['handle']}"}
        raise AssertionError(f"unexpected command {cmd}")

    def info_params(self):
        return [params for cmd, params in self.calls if cmd == "object.info"]


def _resolve_once(session):
    """Resolve with a single attempt, returning the underlying (unwrapped) error."""
    with pytest.raises(LiberaQtTimeoutError) as exc:
        Locator(session, parse("QPushButton"), None).resolve(timeout=0)
    return exc.value.__cause__


def test_ambiguous_selector_describes_each_candidate_singly():
    session = RecordingSession(["o1", "o2", "o3"])
    cause = _resolve_once(session)

    assert isinstance(cause, AmbiguousSelectorError)
    params = session.info_params()
    assert len(params) == 3, "each candidate needs its own object.info call"
    # The whole point: singular `handle`, per PROTOCOL.md -- never a plural `handles` list.
    assert all("handles" not in p and "handle" in p for p in params)
    assert [p["handle"] for p in params] == ["o1", "o2", "o3"]

    message = str(cause)
    assert "matched 3 objects" in message
    assert "button o2" in message, "candidate descriptions belong in the message"


def test_failing_diagnostic_does_not_replace_the_real_error():
    """A broken diagnostic must not mask the failure it exists to explain."""
    session = RecordingSession(["o1", "o2"], info_fails=True)
    cause = _resolve_once(session)

    assert isinstance(cause, AmbiguousSelectorError), "ambiguity must survive a failing describe"
    assert "matched 2 objects" in str(cause)


def test_candidate_list_is_capped_but_the_count_is_not():
    session = RecordingSession([f"o{i}" for i in range(12)])
    cause = _resolve_once(session)

    message = str(cause)
    assert "matched 12 objects" in message, "the reported count is real, not the display cap"
    assert "... and 4 more" in message
    assert len(session.info_params()) == 8, "only the shown candidates are fetched"


def test_nth_selects_without_asking_for_diagnostics():
    session = RecordingSession(["o1", "o2", "o3"])
    assert Locator(session, parse("QPushButton"), None).nth(1).resolve(timeout=0) == "o2"
    assert not session.info_params(), "nth() is unambiguous; no diagnostics needed"