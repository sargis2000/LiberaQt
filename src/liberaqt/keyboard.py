# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Sargis Khachatryan
"""Window-scoped keyboard input, for cases where no locator is the right target."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import TYPE_CHECKING

from .protocol import Cmd

if TYPE_CHECKING:
    from .session import Session

_KEY_TOKEN = re.compile(r"<(?P<chord>[A-Za-z0-9_]+(?:\+[A-Za-z0-9_]+)*)>")
_KEY_SPLIT = re.compile(r"(<[A-Za-z0-9_]+(?:\+[A-Za-z0-9_]+)*>)")


def split_chords(text: str) -> Iterator[tuple[str, str]]:
    """Split text into runs to type and chords to press.

    ``"abc<Ctrl+A>xyz"`` yields ``("text", "abc")``, ``("chord", "Ctrl+A")``, ``("text", "xyz")``.
    A ``<`` that does not open a chord-shaped token -- ``"a < b"`` -- stays literal text; one that
    does -- ``"<b>"`` -- is a chord. Text that must stay literal regardless belongs in ``fill()``.

    Shared by :meth:`Keyboard.type` and :meth:`liberaqt.locator.Locator.type` so that the same
    string means the same thing whichever one is handed it. They used to disagree: the locator
    parsed chords and the keyboard typed the angle brackets as characters.

    Args:
        text: Text with optional ``<Key>`` chords embedded.

    Yields:
        ``(kind, value)`` pairs where kind is ``"text"`` or ``"chord"``.
    """
    for segment in _KEY_SPLIT.split(text):
        if not segment:
            continue
        token = _KEY_TOKEN.fullmatch(segment)
        yield ("chord", token.group("chord")) if token else ("text", segment)


class Keyboard:
    """Raw keyboard input aimed at a window rather than a specific widget.

    Reach it through :attr:`Window.keyboard`. Input goes to whatever currently has focus, so
    prefer a locator's own :meth:`~liberaqt.locator.Locator.press` when there is an object to
    target: it waits for that object to be ready first.

    Args:
        session: The wire session used for every command.
        window_handle: Window to deliver input to. ``None`` targets the focused window.
    """

    def __init__(self, session: Session, window_handle: str | None = None):
        self._session = session
        self._window = window_handle

    def press(self, key: str, count: int = 1, mode: str | None = None) -> None:
        """Press and release a key or chord, then wait for the UI to settle.

        Args:
            key: Key name or chord, e.g. ``"Ctrl+S"``, ``"Enter"``, ``"F5"``, ``"Alt+Shift+Tab"``.
            count: How many times to press it.
            mode: ``"native"`` or ``"synthetic"`` for this call only, overriding the session
                default set by :meth:`~liberaqt.application.Application.set_input_mode`.
        """
        self._session.call(Cmd.KEY, self._params(key=key, count=count, mode=mode))
        self._session.wait_for_idle()

    def type(self, text: str, delay: float = 0.0, mode: str | None = None) -> None:
        """Type text character by character, then wait for the UI to settle.

        Key chords may be embedded in the text, exactly as in
        :meth:`liberaqt.locator.Locator.type`: ``type("hello<Ctrl+A>replaced")`` types, selects
        all, and types over the selection.

        Args:
            text: Text to type, with optional ``<Key>`` chords.
            delay: Seconds between keystrokes, for applications that debounce input.
            mode: ``"native"`` or ``"synthetic"`` for this call only.
        """
        for kind, value in split_chords(text):
            if kind == "chord":
                self._session.call(Cmd.KEY, self._params(key=value, mode=mode))
            else:
                self._session.call(
                    Cmd.TYPE_TEXT,
                    self._params(text=value, delay_ms=int(delay * 1000), mode=mode),
                )
        self._session.wait_for_idle()

    def down(self, key: str, mode: str | None = None) -> None:
        """Press a key and hold it.

        Pair with :meth:`up` for interactions that need a modifier held across several actions,
        such as ctrl-clicking a series of rows. The key stays held until released, including if
        the test fails in between.

        Args:
            key: Key name, e.g. ``"Shift"``.
            mode: ``"native"`` or ``"synthetic"`` for this call only.
        """
        self._session.call(Cmd.PRESS, self._params(key=key, mode=mode))

    def up(self, key: str, mode: str | None = None) -> None:
        """Release a held key.

        Args:
            key: Key name, e.g. ``"Shift"``.
            mode: ``"native"`` or ``"synthetic"`` for this call only.
        """
        self._session.call(Cmd.RELEASE, self._params(key=key, mode=mode))

    def _params(self, **kw) -> dict:
        """Command parameters, dropping a `mode` that was not asked for.

        Omitted rather than sent as null, so the agent falls back to the session mode instead of
        having to treat an explicit null as "unset".
        """
        params = {"handle": self._window}
        params.update({k: v for k, v in kw.items() if not (k == "mode" and v is None)})
        return params