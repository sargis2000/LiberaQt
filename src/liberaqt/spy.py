# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Sargis Khachatryan
"""Recorder client: drive the agent's event filter and collect semantic actions.

**The agent half of this does not exist yet.** ``record.start`` and ``record.stop`` are not
registered by ``agent/src/dispatcher.cpp``, so every method here raises
:class:`~liberaqt.errors.UnsupportedOperationError` against a current agent. Ask
``Application.supports(Cmd.RECORD_START)`` rather than assuming. What is written is this client,
:mod:`liberaqt.codegen`, the ``liberaqt record`` command and the agent's ``Recorder`` event
filter; what is missing is the wiring between them -- see the recorder notes in CLAUDE.md.
"""

from __future__ import annotations

from typing import Any

from .protocol import Cmd, Event


class Recorder:
    """Collects semantic actions streamed by the agent's event filter.

    Backs ``liberaqt record``. The intent is that the agent watches real interaction and reports
    what a user did (clicked this button, committed this text) rather than raw events, and this
    turns the stream into a runnable test.

    Not usable yet: the agent registers neither ``record.start`` nor ``record.stop``, so
    :meth:`start` raises :class:`~liberaqt.errors.UnsupportedOperationError`.

    Args:
        app: The application to record.

    Attributes:
        actions: Actions received so far, in order.
    """

    def __init__(self, app):
        self._app = app
        self._session = app._session
        self.actions: list[dict[str, Any]] = []

    def start(self, granularity: str = "semantic") -> None:
        """Subscribe to action events and tell the agent to start recording.

        Args:
            granularity: ``"semantic"`` for inferred user actions, or ``"raw"`` for individual
                events, which is far more verbose and rarely what a test wants.

        Raises:
            UnsupportedOperationError: Always, against a current agent: ``record.start`` is not
                registered. Check ``app.supports(Cmd.RECORD_START)`` first.
        """
        self._session.transport.on(Event.RECORD_ACTION, self._on_action)
        self._session.call(Cmd.RECORD_START, {"granularity": granularity})

    def stop(self) -> list[dict[str, Any]]:
        """Stop recording.

        Returns:
            Every action collected, in order.
        """
        self._session.call(Cmd.RECORD_STOP, {})
        return self.actions

    def _on_action(self, data: dict) -> None:
        self.actions.append(data)
        target = data.get("selector", "?")
        print(f"  recorded: {data.get('action')} on {target}")

    def to_python(self, test_name: str = "test_recorded") -> str:
        """Render the recorded actions as a runnable pytest module.

        Args:
            test_name: Name for the generated test function.

        Returns:
            Python source.
        """
        from .codegen import render
        return render(self.actions, test_name=test_name)
