"""Retry engine and assertion behaviour, exercised with fake locators."""

import time

import pytest

from liberaqt.errors import LiberaQtError
from liberaqt.errors import TimeoutError as LiberaQtTimeoutError
from liberaqt.expect import expect
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
