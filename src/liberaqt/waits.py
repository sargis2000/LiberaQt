"""Retry engine shared by locators and assertions."""

from __future__ import annotations

import contextlib
import time
from typing import Callable, TypeVar

from .errors import LiberaQtError
from .errors import TimeoutError as LiberaQtTimeoutError
from .protocol import DEFAULT_TIMEOUT, POLL_INTERVAL

T = TypeVar("T")


class TimeoutPolicy:
    """Mutable default timeout, overridable per call or via a context manager."""

    def __init__(self, default: float = DEFAULT_TIMEOUT):
        self.default = default

    def resolve(self, timeout: float | None) -> float:
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
    retry_on: tuple = (LiberaQtError,),
    description: str = "condition",
) -> T:
    """Call ``fn`` until it returns without raising a retryable error.

    Always attempts at least once, so a zero timeout still does useful work. Errors that mark
    themselves ``retryable = False`` are re-raised immediately: polling cannot make an
    unimplemented command exist, and swallowing the cause into a TimeoutError would hide it.
    """
    deadline = time.monotonic() + timeout
    last: BaseException | None = None
    attempts = 0
    while True:
        attempts += 1
        try:
            return fn()
        except retry_on as exc:
            if not getattr(exc, "retryable", True):
                raise
            last = exc
            if time.monotonic() >= deadline:
                break
            time.sleep(poll)

    raise LiberaQtTimeoutError(
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
            raise LiberaQtTimeoutError(f"{description} was still false after {timeout:.1f}s")
        time.sleep(poll)
