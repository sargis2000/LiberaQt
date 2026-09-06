# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Sargis Khachatryan
"""Retrying assertions.

``expect(locator).to_be_visible()`` polls until the condition holds or the timeout expires, so
tests do not need explicit sleeps. Failure messages carry the actual value and the selector.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from .errors import LiberaQtError
from .waits import retry

if TYPE_CHECKING:
    from .locator import Locator
    from .window import Window


def _normalise(text: str) -> str:
    return " ".join((text or "").replace("&", "").split())


class LocatorAssertions:
    """Assertions about an object, each retried until it holds or the timeout expires.

    Every assertion re-reads the value on each attempt, so an application that updates a label
    asynchronously needs no sleep in the test. Failures raise ``AssertionError`` so pytest
    reports them as assertion failures rather than driver errors.

    Args:
        locator: The locator to assert about.
        timeout: Seconds to keep retrying. Defaults to the session timeout.
        negated: Whether the sense of every assertion is inverted.
    """

    def __init__(self, locator: Locator, timeout: float | None = None,
                 negated: bool = False):
        self._loc = locator
        self._timeout = timeout
        self._negated = negated

    @property
    def not_(self) -> LocatorAssertions:
        """Invert the next assertion, as in ``expect(loc).not_.to_be_visible()``.

        The condition must hold in the negative for the whole poll, so this waits for the
        timeout when the assertion is going to fail.
        """
        return LocatorAssertions(self._loc, self._timeout, not self._negated)

    # -- engine ---------------------------------------------------------------
    def _check(self, description: str, probe, expected=None, timeout: float | None = None):
        """Poll ``probe`` until it agrees with the expected sense, then raise if it never does.

        Errors from reading the value are treated as "not yet true" rather than propagated: an
        object that does not exist yet is the normal case this whole class exists for.

        Args:
            description: Phrase completing "expected <locator> ...", used in the message.
            probe: Callable returning ``(ok, actual)``.
            expected: Expected value, shown in the failure message when given.
            timeout: Seconds to keep retrying. Falls back to this instance's, then the session's.

        Raises:
            AssertionError: The condition never held, with the last actual value and the timeout.
        """
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
        """Assert the object is visible.

        Args:
            timeout: Seconds to keep retrying.
        """
        self._check("to be visible", lambda: (self._loc.is_visible, self._loc.is_visible),
                    timeout=timeout)

    def to_be_hidden(self, timeout: float | None = None):
        """Assert the object is not visible.

        Args:
            timeout: Seconds to keep retrying.
        """
        self._check("to be hidden", lambda: (not self._loc.is_visible, self._loc.is_visible),
                    timeout=timeout)

    def to_be_enabled(self, timeout: float | None = None):
        """Assert the object accepts input.

        Args:
            timeout: Seconds to keep retrying.
        """
        self._check("to be enabled", lambda: (self._loc.is_enabled, self._loc.is_enabled),
                    timeout=timeout)

    def to_be_disabled(self, timeout: float | None = None):
        """Assert the object rejects input.

        Args:
            timeout: Seconds to keep retrying.
        """
        self._check("to be disabled", lambda: (not self._loc.is_enabled, self._loc.is_enabled),
                    timeout=timeout)

    def to_be_checked(self, checked: bool = True, timeout: float | None = None):
        """Assert a checkable object is in the given state.

        Args:
            checked: Expected state.
            timeout: Seconds to keep retrying.
        """
        self._check(f"to be checked={checked}",
                    lambda: (self._loc.is_checked == checked, self._loc.is_checked),
                    expected=checked, timeout=timeout)

    def to_exist(self, timeout: float | None = None):
        """Assert at least one object matches the selector.

        Args:
            timeout: Seconds to keep retrying.
        """
        self._check("to exist", lambda: (self._loc.count > 0, self._loc.count), timeout=timeout)

    def to_have_text(self, expected: str, timeout: float | None = None):
        """Assert the display text matches exactly, ignoring whitespace and mnemonics.

        Runs of whitespace collapse and ``&`` accelerator markers are stripped, so ``"&Save"``
        and ``"Save"`` compare equal.

        Args:
            expected: Expected text.
            timeout: Seconds to keep retrying.
        """
        def probe():
            actual = self._loc.text
            return _normalise(actual) == _normalise(expected), actual
        self._check("to have text", probe, expected=expected, timeout=timeout)

    def to_contain_text(self, expected: str, timeout: float | None = None):
        """Assert the display text contains a substring, normalised as in :meth:`to_have_text`.

        Args:
            expected: Substring to look for.
            timeout: Seconds to keep retrying.
        """
        def probe():
            actual = self._loc.text
            return _normalise(expected) in _normalise(actual), actual
        self._check("to contain text", probe, expected=expected, timeout=timeout)

    def to_match_text(self, pattern: str, timeout: float | None = None):
        """Assert the display text matches a regular expression.

        Searches rather than anchoring, and matches the raw text without normalisation.

        Args:
            pattern: Regular expression.
            timeout: Seconds to keep retrying.
        """
        rx = re.compile(pattern)

        def probe():
            actual = self._loc.text
            return bool(rx.search(actual or "")), actual
        self._check("to match text pattern", probe, expected=pattern, timeout=timeout)

    def to_have_property(self, name: str, expected: Any, timeout: float | None = None):
        """Assert a ``Q_PROPERTY`` equals a value.

        Args:
            name: Property name.
            expected: Expected value.
            timeout: Seconds to keep retrying.
        """
        def probe():
            actual = self._loc[name]
            return actual == expected, actual
        self._check(f"to have property {name}", probe, expected=expected, timeout=timeout)

    def to_have_count(self, expected: int, timeout: float | None = None):
        """Assert the selector matches exactly this many objects.

        Args:
            expected: Expected number of matches.
            timeout: Seconds to keep retrying.
        """
        def probe():
            actual = self._loc.count
            return actual == expected, actual
        self._check("to have count", probe, expected=expected, timeout=timeout)

    def to_have_value(self, expected: Any, timeout: float | None = None):
        """Assert a value-bearing widget holds this value.

        Args:
            expected: Expected value.
            timeout: Seconds to keep retrying.
        """
        def probe():
            actual = self._loc.value
            return actual == expected, actual
        self._check("to have value", probe, expected=expected, timeout=timeout)


class WindowAssertions:
    """Assertions about a window rather than an object inside it.

    Args:
        window: The window to assert about.
        timeout: Seconds to keep retrying. Defaults to the session timeout.
    """

    def __init__(self, window: Window, timeout: float | None = None):
        self._win = window
        self._timeout = timeout

    def to_have_title(self, expected: str, timeout: float | None = None):
        """Assert the window title matches exactly.

        Args:
            expected: Expected title.
            timeout: Seconds to keep retrying.
        """
        LocatorAssertions(self._win, timeout or self._timeout)._check(
            "to have title", lambda: (self._win.title == expected, self._win.title),
            expected=expected, timeout=timeout,
        )

    def to_be_active(self, timeout: float | None = None):
        """Assert the window has window-manager focus.

        Headless and unmanaged displays may never grant focus, so this can fail for reasons that
        have nothing to do with the application.

        Args:
            timeout: Seconds to keep retrying.
        """
        LocatorAssertions(self._win, timeout or self._timeout)._check(
            "to be active", lambda: (self._win.is_active, self._win.is_active), timeout=timeout,
        )


class _Retry(Exception):
    """Internal signal that a probe did not hold yet. Never escapes :meth:`_check`."""


def expect(target: Locator | Window,
           timeout: float | None = None) -> LocatorAssertions | WindowAssertions:
    """Begin an assertion about a locator or a window.

    Args:
        target: A :class:`~liberaqt.locator.Locator` or :class:`~liberaqt.window.Window`.
        timeout: Seconds to keep retrying. Defaults to the session timeout.

    Returns:
        :class:`WindowAssertions` for a window, otherwise :class:`LocatorAssertions`.
    """
    from .window import Window

    if isinstance(target, Window):
        return WindowAssertions(target, timeout)
    return LocatorAssertions(target, timeout)
