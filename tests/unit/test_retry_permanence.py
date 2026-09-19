"""Permanent errors must not be retried.

The auto-wait loop retries anything deriving from LiberaQtError, which is right for transient
failures (object not created yet, button still disabled) and wrong for permanent ones. An
unimplemented agent command used to be polled for the caller's whole timeout and then reported
as a TimeoutError, hiding "unknown command: input.wheel" behind five seconds of waiting.
"""

import time

import pytest

from liberaqt.errors import (
    ConnectionLostError,
    InvalidSelectorError,
    LiberaQtError,
    NotActionableError,
    ObjectNotFoundError,
    ProtocolError,
    UnsupportedOperationError,
)
from liberaqt.errors import TimeoutError as LiberaQtTimeoutError
from liberaqt.waits import retry

PERMANENT = [UnsupportedOperationError, InvalidSelectorError, ProtocolError, ConnectionLostError]
TRANSIENT = [ObjectNotFoundError, NotActionableError]


@pytest.mark.parametrize("error_cls", PERMANENT)
def test_permanent_errors_surface_immediately(error_cls):
    attempts = []

    def always_fails():
        attempts.append(1)
        raise error_cls("nope")

    with pytest.raises(error_cls):          # not wrapped into a TimeoutError
        retry(always_fails, timeout=5.0, description="permanent")

    assert len(attempts) == 1, "a permanent error must not be polled"


@pytest.mark.parametrize("error_cls", TRANSIENT)
def test_transient_errors_are_still_retried(error_cls):
    attempts = []

    def always_fails():
        attempts.append(1)
        raise error_cls("later")

    with pytest.raises(LiberaQtTimeoutError):
        retry(always_fails, timeout=0.2, poll=0.01, description="transient")

    assert len(attempts) > 1, "transient errors are exactly what the wait loop exists for"


def test_permanent_failure_does_not_burn_the_timeout():
    started = time.monotonic()
    with pytest.raises(UnsupportedOperationError):
        retry(lambda: (_ for _ in ()).throw(UnsupportedOperationError("unknown command")),
              timeout=5.0, description="fast fail")
    assert time.monotonic() - started < 0.5, "should fail fast, not wait out the timeout"


def test_transient_error_that_clears_still_succeeds():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise ObjectNotFoundError("not yet")
        return "ready"

    assert retry(flaky, timeout=2.0, poll=0.01, description="flaky") == "ready"
    assert len(calls) == 3


def test_base_error_is_retryable_by_default():
    assert LiberaQtError("x").retryable is True
    assert UnsupportedOperationError("x").retryable is False