"""Recorder client: drive the agent's event filter and collect semantic actions."""

from __future__ import annotations

from typing import Any, Dict, List

from .protocol import Cmd, Event


class Recorder:
    def __init__(self, app):
        self._app = app
        self._session = app._session
        self.actions: List[Dict[str, Any]] = []

    def start(self, granularity: str = "semantic") -> None:
        self._session.transport.on(Event.RECORD_ACTION, self._on_action)
        self._session.call(Cmd.RECORD_START, {"granularity": granularity})

    def stop(self) -> List[Dict[str, Any]]:
        self._session.call(Cmd.RECORD_STOP, {})
        return self.actions

    def _on_action(self, data: dict) -> None:
        self.actions.append(data)
        target = data.get("selector", "?")
        print(f"  recorded: {data.get('action')} on {target}")

    def to_python(self, test_name: str = "test_recorded") -> str:
        from .codegen import render
        return render(self.actions, test_name=test_name)
