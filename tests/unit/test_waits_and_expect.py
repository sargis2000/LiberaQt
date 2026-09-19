"""Retry engine and assertion behaviour, exercised with fake locators."""

import time

import pytest

from liberaqt.errors import LiberaQtError, ObjectNotFoundError, StaleObjectError
from liberaqt.errors import TimeoutError as LiberaQtTimeoutError
from liberaqt.expect import _root_cause, expect
from liberaqt.waits import TimeoutPolicy, retry, wait_until


class FakeSession:
    def __init__(self):
        self.timeouts = TimeoutPolicy(0.5)


class FakeLocator:
    """Becomes visible / gains text after N reads, to model an async UI."""

    def __init__(self, ready_after=0, text="Done"):
        self._session = FakeSession()
        self._reads = 0
        self._ready_after = ready_after
        self._text = text

    def _ready(self):
        self._reads += 1
        return self._reads > self._ready_after

    @property
    def is_visible(self):
        return self._ready()

    @property
    def is_enabled(self):
        return self._ready()

    @property
    def text(self):
        return self._text if self._ready() else ""

    @property
    def count(self):
        return 1 if self._ready() else 0

    def __repr__(self):
        return "<FakeLocator>"


def test_retry_succeeds_after_transient_failures():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise LiberaQtError("not yet")
        return "ok"

    assert retry(flaky, timeout=2.0, poll=0.01) == "ok"
    assert calls["n"] == 3


def test_retry_attempts_at_least_once_with_zero_timeout():
    calls = {"n": 0}

    def once():
        calls["n"] += 1
        return "ok"

    assert retry(once, timeout=0) == "ok"
    assert calls["n"] == 1


def test_retry_raises_timeout_with_the_last_error_attached():
    def always_fails():
        raise LiberaQtError("boom")

    with pytest.raises(LiberaQtTimeoutError) as excinfo:
        retry(always_fails, timeout=0.1, poll=0.01, description="thing")
    assert "boom" in str(excinfo.value)
    assert "thing" in str(excinfo.value)


def test_wait_until():
    start = time.monotonic()
    state = {"ready": False}

    def flip():
        if time.monotonic() - start > 0.05:
            state["ready"] = True
        return state["ready"]

    wait_until(flip, timeout=1.0, poll=0.01)
    assert state["ready"]


def test_expect_waits_for_an_async_ui():
    expect(FakeLocator(ready_after=3)).to_be_visible()


def test_expect_failure_message_contains_actual_and_timeout():
    class NeverVisible(FakeLocator):
        @property
        def is_visible(self):
            return False

    with pytest.raises(AssertionError) as excinfo:
        expect(NeverVisible()).to_be_visible(timeout=0.1)
    message = str(excinfo.value)
    assert "actual" in message and "timeout" in message


def test_expect_text_normalises_whitespace_and_mnemonics():
    loc = FakeLocator(text="  Log   in  ")
    expect(loc).to_have_text("Log in")


def test_expect_negation():
    class NeverVisible(FakeLocator):
        @property
        def is_visible(self):
            return False

    expect(NeverVisible()).not_.to_be_visible()


def test_timeout_policy_override():
    policy = TimeoutPolicy(5.0)
    with policy.override(1.0):
        assert policy.resolve(None) == 1.0
    assert policy.resolve(None) == 5.0
    assert policy.resolve(2.0) == 2.0


# ------------------------------------------------------- the timeout is the one you asked for


class SlowResolvingLocator:
    """A locator whose reads honour the *session* timeout, as a real one does.

    A real ``is_visible`` goes through ``resolve()``, which retries for the session timeout.
    That is the thing that used to make a per-assertion ``timeout=`` cosmetic: one poll blocked
    for the session default before the assertion's own deadline was ever consulted.
    """

    def __init__(self, session_timeout=2.0):
        self._session = FakeSession()
        self._session.timeouts = TimeoutPolicy(session_timeout)
        self.blocked_for = None

    def _read(self):
        # However long the session currently allows -- which the assertion must drive to 0.
        self.blocked_for = self._session.timeouts.default
        raise ObjectNotFoundError("no object matched selector: QPushButton#nope")

    @property
    def is_visible(self):
        return self._read()

    @property
    def count(self):
        return self._read()

    def __repr__(self):
        return "<Locator QPushButton#nope>"


def test_a_per_assertion_timeout_is_not_outlasted_by_one_poll():
    loc = SlowResolvingLocator(session_timeout=30.0)
    started = time.time()
    with pytest.raises(AssertionError):
        expect(loc).to_be_visible(timeout=0.3)
    elapsed = time.time() - started

    assert elapsed < 5, f"asked for 0.3s, took {elapsed:.1f}s"
    assert loc.blocked_for == 0, (
        "the probe must resolve with one attempt and let the assertion do the waiting; "
        f"it was allowed {loc.blocked_for}s"
    )


def test_the_session_timeout_is_restored_afterwards():
    loc = SlowResolvingLocator(session_timeout=7.0)
    with pytest.raises(AssertionError):
        expect(loc).to_be_visible(timeout=0.1)
    assert loc._session.timeouts.default == 7.0


# ------------------------------------------------------- the diagnostic survives to the message


def test_the_failure_message_carries_the_agents_diagnostic():
    """The near-miss list is the most useful thing in a red run; a type name alone threw it away."""
    loc = SlowResolvingLocator()
    with pytest.raises(AssertionError) as excinfo:
        expect(loc).to_be_visible(timeout=0.1)

    message = str(excinfo.value)
    assert "no object matched selector: QPushButton#nope" in message
    assert "ObjectNotFoundError" in message, "the root cause, not the TimeoutError wrapping it"


def test_the_root_cause_is_reported_not_the_wrapper():
    root = ObjectNotFoundError("gone")
    wrapped = LiberaQtTimeoutError("timed out")
    wrapped.__cause__ = root
    outer = LiberaQtTimeoutError("timed out again")
    outer.__cause__ = wrapped

    assert _root_cause(outer) is root


def test_root_cause_survives_a_cyclic_chain():
    a = LiberaQtError("a")
    b = LiberaQtError("b")
    a.__cause__ = b
    b.__cause__ = a
    assert _root_cause(a) in (a, b)


# ------------------------------------------------------- hidden and not-visible must agree


class VanishedLocator:
    def __init__(self):
        self._session = FakeSession()

    @property
    def is_visible(self):
        raise StaleObjectError("[stale] handle 'o4' no longer refers to a live object")

    def __repr__(self):
        return "<Locator QProgressBar>"


def test_a_destroyed_object_is_hidden():
    """`to_be_hidden` and `not_.to_be_visible` are read as synonyms; they must behave alike."""
    expect(VanishedLocator()).to_be_hidden(timeout=0.2)
    expect(VanishedLocator()).not_.to_be_visible(timeout=0.2)


def test_a_visible_object_is_not_hidden():
    with pytest.raises(AssertionError):
        expect(FakeLocator()).to_be_hidden(timeout=0.2)
