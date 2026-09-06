# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Sargis Khachatryan
"""Retry engine shared by locators and assertions."""

from __future__ import annotations

import contextlib
import time
from collections.abc import Iterator
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
        """Pick the timeout to use for one call.

        Args:
            timeout: Explicit timeout, or ``None`` to use the default. An explicit ``0`` is
                honoured and means "one attempt", not "use the default".

        Returns:
            Seconds to allow.
        """
        return self.default if timeout is None else timeout

    @contextlib.contextmanager
    def override(self, timeout: float) -> Iterator[None]:
        """Temporarily replace the default timeout.

        Args:
            timeout: Seconds to use inside the block.

        Yields:
            None.
        """
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

    Args:
        fn: Called repeatedly until it succeeds. Must be safe to call more than once.
        timeout: Seconds to keep trying. Zero still attempts once.
        poll: Seconds between attempts.
        retry_on: Exception types worth retrying, before the ``retryable`` flag is consulted.
        description: Names the operation in the timeout message.

    Returns:
        Whatever ``fn`` returned on the attempt that succeeded.

    Raises:
        LiberaQtTimeoutError: No attempt succeeded in time. The last error is on ``__cause__``.
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
    """Poll a predicate until it is true.

    For conditions with no value to return and no exception to interpret, where :func:`retry`
    would be the wrong shape.

    Args:
        predicate: Called repeatedly; polling stops when it returns true.
        timeout: Seconds to keep polling.
        poll: Seconds between attempts.
        description: Phrase naming the condition, used in the timeout message.

    Raises:
        TimeoutError: The predicate was still false when the timeout expired.
    """
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return
        if time.monotonic() >= deadline:
            raise LiberaQtTimeoutError(f"{description} was still false after {timeout:.1f}s")
        time.sleep(poll)
