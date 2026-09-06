# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Sargis Khachatryan
"""Exception hierarchy for liberaqt.

Every error carries enough context to debug a failing test without re-running it:
the selector, the resolved handle (if any), and a one-line remediation hint.
"""

from __future__ import annotations

from typing import Any


class LiberaQtError(Exception):
    """Base class for everything this package raises."""

    hint: str = ""

    #: Whether the auto-wait loop should keep trying after this error.
    #:
    #: Most failures are transient by nature -- the object has not been created yet, the button
    #: is still disabled -- and retrying is the whole point of the waiting model. A few are
    #: permanent: no amount of polling makes an unimplemented command exist or an unparseable
    #: selector parse. Retrying those burns the caller's entire timeout and then reports a
    #: TimeoutError, hiding the real cause.
    retryable: bool = True

    def __init__(self, message: str, *, hint: str = "", data: dict | None = None):
        self.data = data or {}
        if hint:
            self.hint = hint
        full = message
        if self.hint:
            full = f"{message}\n  hint: {self.hint}"
        super().__init__(full)


class LaunchError(LiberaQtError):
    """The AUT started but the agent never connected."""

    hint = (
        "Run with QT_DEBUG_PLUGINS=1 to see whether Qt found the liberaqt plugin, "
        "and check `liberaqt doctor` for an agent/Qt ABI mismatch."
    )
    # The process is already up or already dead; polling changes nothing.
    retryable = False


class AgentMismatchError(LiberaQtError):
    """No prebuilt agent matches the AUT's Qt build."""

    retryable = False


class ConnectionLostError(LiberaQtError):
    """The socket dropped mid-run, usually because the AUT crashed."""

    retryable = False


class ProtocolError(LiberaQtError):
    """The agent said something we do not understand."""

    retryable = False


class SelectorError(LiberaQtError):
    """Base for selector resolution problems."""


class InvalidSelectorError(SelectorError):
    """The selector string could not be parsed."""

    retryable = False


class ObjectNotFoundError(SelectorError):
    """Nothing matched the selector.

    Carries the near misses the agent found -- objects that matched some but not all of the
    selector -- which usually turns "not found" into a visible typo.

    Args:
        selector: The selector that matched nothing.
        near_misses: Descriptions of the closest candidates.
        **kw: Passed to :class:`LiberaQtError`.

    Attributes:
        selector: The selector that matched nothing.
        near_misses: Descriptions of the closest candidates.
    """

    hint = "Check `liberaqt inspect` for the live object tree; near-misses are listed below."

    def __init__(self, selector: Any, near_misses: list | None = None,
                 message: str | None = None, **kw: Any):
        msg = message or f"no object matched selector: {selector}"
        if near_misses:
            lines = "\n".join(f"    - {m}" for m in near_misses[:5])
            msg += f"\n  near misses:\n{lines}"
        super().__init__(msg, **kw)
        self.selector = selector
        self.near_misses = near_misses or []


class AmbiguousSelectorError(SelectorError):
    """The selector matched several objects where exactly one was required.

    Args:
        selector: The selector that matched too much.
        matches: Descriptions of the matches, capped for readability.
        total: True number of matches, which may exceed ``len(matches)``.
        **kw: Passed to :class:`LiberaQtError`.

    Attributes:
        selector: The selector that matched too much.
        matches: Descriptions of the matches shown.
    """

    hint = "Narrow it with .filter(...), or pick one explicitly with .first / .nth(i)."

    def __init__(self, selector: Any, matches: list | None = None,
                 total: int | None = None, **kw: Any):
        # `matches` is capped for readability, so the true count has to be passed separately.
        n = total if total is not None else len(matches or [])
        msg = f"selector matched {n} objects, expected exactly 1: {selector}"
        if matches:
            lines = "\n".join(f"    - {m}" for m in matches[:8])
            msg += f"\n  matches:\n{lines}"
            if n > len(matches):
                msg += f"\n    ... and {n - len(matches)} more"
        super().__init__(msg, **kw)
        self.selector = selector
        self.matches = matches or []


class StaleObjectError(SelectorError):
    """The handle refers to an object that has since been destroyed."""

    hint = "The object was destroyed. Re-resolve the locator instead of caching handles."


class NotActionableError(LiberaQtError):
    """The object exists but cannot be acted on: hidden, disabled, or zero-sized."""

    hint = "Wait for the precondition explicitly, or check whether a modal dialog is covering it."


class TimeoutError(LiberaQtError):  # noqa: A001 - intentionally shadows builtin within package
    """A wait or an agent command exceeded its deadline."""


class UnsupportedOperationError(LiberaQtError):
    """Valid request, wrong object type or Qt version."""

    retryable = False


ERROR_CODE_MAP = {
    "not_found": ObjectNotFoundError,
    "ambiguous": AmbiguousSelectorError,
    "stale": StaleObjectError,
    "not_actionable": NotActionableError,
    "unsupported": UnsupportedOperationError,
    "invalid_params": ProtocolError,
    "internal": LiberaQtError,
    "timeout": TimeoutError,
}


def from_agent_error(payload: dict, selector: Any = None) -> LiberaQtError:
    """Translate a protocol error object into a Python exception.

    Args:
        payload: The ``error`` object from a failed response: ``code``, ``message``, ``data``.
        selector: The selector being resolved when it failed, carried into the exception so a
            failure names what was being looked for.

    Returns:
        The subclass matching ``code``, or :class:`LiberaQtError` for a code this client does not
        know -- an agent newer than the client must not crash it.
    """
    code = payload.get("code", "internal")
    message = payload.get("message", "agent error")
    data = payload.get("data", {}) or {}
    cls = ERROR_CODE_MAP.get(code, LiberaQtError)
    context = selector or data.get("context")
    # Only rebuild the selector-shaped messages when there really is a selector. `not_found` also
    # covers lookups that have nothing to do with the object tree -- a menu entry, a tab, a row --
    # and for those the agent's own message is the whole diagnosis and must not be thrown away.
    if cls is ObjectNotFoundError:
        if context is None:
            return ObjectNotFoundError(None, data.get("near_misses"),
                                       message=f"[{code}] {message}", data=data)
        return ObjectNotFoundError(context, data.get("near_misses"), data=data)
    if cls is AmbiguousSelectorError and context is not None:
        return AmbiguousSelectorError(context, data.get("matches"), data=data)
    return cls(f"[{code}] {message}", data=data)
