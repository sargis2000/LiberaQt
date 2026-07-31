"""Retry engine shared by locators and assertions."""

from __future__ import annotations

import contextlib
import time
from typing import Callable, Optional, TypeVar

from .errors import QtDriverError
from .errors import TimeoutError as QtTimeoutError
from .protocol import DEFAULT_TIMEOUT, POLL_INTERVAL

T = TypeVar("T")


class TimeoutPolicy:
    """Mutable default timeout, overridable per call or via a context manager."""

    def __init__(self, default: float = DEFAULT_TIMEOUT):
        self.default = default

    def resolve(self, timeout: Optional[float]) -> float:
        return self.default if timeout is None else timeout

    @contextlib.contextmanager
    def override(self, timeout: float):
        previous = self.default
        self.default = timeout
        try:
            yield
        finally:
            self.default = previous


def retry(
    fn: Callable[[], T],
    *,
    timeout: float,
    poll: float = POLL_INTERVAL,
    retry_on: tuple = (QtDriverError,),
    description: str = "condition",
) -> T:
    """Call ``fn`` until it returns without raising a retryable error.

    Always attempts at least once, so a zero timeout still does useful work.
    """
    deadline = time.monotonic() + timeout
    last: Optional[BaseException] = None
    attempts = 0
    while True:
        attempts += 1
        try:
            return fn()
        except retry_on as exc:
            last = exc
            if time.monotonic() >= deadline:
                break
            time.sleep(poll)

    raise QtTimeoutError(
        f"{description} not satisfied within {timeout:.1f}s after {attempts} attempts:\n  {last}",
        data={"attempts": attempts, "last_error": repr(last)},
    ) from last


def wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float,
    poll: float = POLL_INTERVAL,
    description: str = "condition",
) -> None:
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return
        if time.monotonic() >= deadline:
            raise QtTimeoutError(f"{description} was still false after {timeout:.1f}s")
        time.sleep(poll)
