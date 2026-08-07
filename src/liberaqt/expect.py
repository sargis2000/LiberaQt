"""Retrying assertions, Playwright-style.

``expect(locator).to_be_visible()`` polls until the condition holds or the timeout expires, so
tests do not need explicit sleeps. Failure messages carry the actual value and the selector.
"""

from __future__ import annotations

import re
from typing import Any

from .errors import LiberaQtError
from .waits import retry


def _normalise(text: str) -> str:
    return " ".join((text or "").replace("&", "").split())


class LocatorAssertions:
    def __init__(self, locator, timeout: float | None = None, negated: bool = False):
        self._loc = locator
        self._timeout = timeout
        self._negated = negated

    @property
    def not_(self) -> LocatorAssertions:
        return LocatorAssertions(self._loc, self._timeout, not self._negated)

    # -- engine ---------------------------------------------------------------
    def _check(self, description: str, probe, expected=None, timeout: float | None = None):
        timeout = timeout if timeout is not None else (
            self._timeout if self._timeout is not None else self._loc._session.timeouts.default
        )

        state = {"actual": "<never evaluated>"}

        def once():
            try:
                ok, actual = probe()
            except LiberaQtError as exc:
                ok, actual = False, f"<{type(exc).__name__}>"
            state["actual"] = actual
            if ok is not self._negated:
                return True
            raise _Retry()

        try:
            retry(once, timeout=timeout, retry_on=(_Retry,), description=description)
        except Exception as exc:  # noqa: BLE001 - re-raised as an assertion below
            neg = "not " if self._negated else ""
            msg = (
                f"expected {self._loc!r} {neg}{description}"
                + (f"\n  expected: {expected!r}" if expected is not None else "")
                + f"\n  actual:   {state['actual']!r}"
                + f"\n  timeout:  {timeout:.1f}s"
            )
            raise AssertionError(msg) from exc

    # -- assertions -----------------------------------------------------------
    def to_be_visible(self, timeout: float | None = None):
        self._check("to be visible", lambda: (self._loc.is_visible, self._loc.is_visible),
                    timeout=timeout)

    def to_be_hidden(self, timeout: float | None = None):
        self._check("to be hidden", lambda: (not self._loc.is_visible, self._loc.is_visible),
                    timeout=timeout)

    def to_be_enabled(self, timeout: float | None = None):
        self._check("to be enabled", lambda: (self._loc.is_enabled, self._loc.is_enabled),
                    timeout=timeout)

    def to_be_disabled(self, timeout: float | None = None):
        self._check("to be disabled", lambda: (not self._loc.is_enabled, self._loc.is_enabled),
                    timeout=timeout)

    def to_be_checked(self, checked: bool = True, timeout: float | None = None):
        self._check(f"to be checked={checked}",
                    lambda: (self._loc.is_checked == checked, self._loc.is_checked),
                    expected=checked, timeout=timeout)

    def to_exist(self, timeout: float | None = None):
        self._check("to exist", lambda: (self._loc.count > 0, self._loc.count), timeout=timeout)

    def to_have_text(self, expected: str, timeout: float | None = None):
        def probe():
            actual = self._loc.text
            return _normalise(actual) == _normalise(expected), actual
        self._check("to have text", probe, expected=expected, timeout=timeout)

    def to_contain_text(self, expected: str, timeout: float | None = None):
        def probe():
            actual = self._loc.text
            return _normalise(expected) in _normalise(actual), actual
        self._check("to contain text", probe, expected=expected, timeout=timeout)

    def to_match_text(self, pattern: str, timeout: float | None = None):
        rx = re.compile(pattern)
        def probe():
            actual = self._loc.text
            return bool(rx.search(actual or "")), actual
        self._check("to match text pattern", probe, expected=pattern, timeout=timeout)

    def to_have_property(self, name: str, expected: Any, timeout: float | None = None):
        def probe():
            actual = self._loc[name]
            return actual == expected, actual
        self._check(f"to have property {name}", probe, expected=expected, timeout=timeout)

    def to_have_count(self, expected: int, timeout: float | None = None):
        def probe():
            actual = self._loc.count
            return actual == expected, actual
        self._check("to have count", probe, expected=expected, timeout=timeout)

    def to_have_value(self, expected: Any, timeout: float | None = None):
        def probe():
            actual = self._loc.value
            return actual == expected, actual
        self._check("to have value", probe, expected=expected, timeout=timeout)


class WindowAssertions:
    def __init__(self, window, timeout: float | None = None):
        self._win = window
        self._timeout = timeout

    def to_have_title(self, expected: str, timeout: float | None = None):
        LocatorAssertions(self._win, timeout or self._timeout)._check(
            "to have title", lambda: (self._win.title == expected, self._win.title),
            expected=expected, timeout=timeout,
        )

    def to_be_active(self, timeout: float | None = None):
        LocatorAssertions(self._win, timeout or self._timeout)._check(
            "to be active", lambda: (self._win.is_active, self._win.is_active), timeout=timeout,
        )


class _Retry(Exception):
    pass


def expect(target, timeout: float | None = None):
    """Entry point: ``expect(locator)`` or ``expect(window)``."""
    from .window import Window

    if isinstance(target, Window):
        return WindowAssertions(target, timeout)
    return LocatorAssertions(target, timeout)
