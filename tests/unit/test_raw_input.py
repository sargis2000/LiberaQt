"""Window-scoped `Mouse` and `Keyboard`: per-call mode, and chords that mean one thing.

Two gaps `docs/ACTIONS.md` used to admit to, both closed here:

* every `Locator` action took a per-call ``mode=`` while `Mouse.*` and `Keyboard.*` silently
  followed the session default, so there was no way to make one raw click synthetic;
* `Locator.type` parsed ``<Ctrl+A>`` chords and `Keyboard.type` typed the angle
  brackets as literal characters. Same method name, same-looking string, different meaning.

These assert on the params put on the wire, since that is the entire contract: the client's job
is to say what it wants, and the agent's to deliver it.
"""

import pytest

from liberaqt.keyboard import Keyboard, split_chords
from liberaqt.mouse import Mouse
from liberaqt.waits import TimeoutPolicy


class RecordingSession:
    """Records every command and its params, answering nothing in particular."""

    def __init__(self):
        self.timeouts = TimeoutPolicy(0.5)
        self.slowmo = 0.0
        self.calls = []

    def call(self, cmd, params=None, timeout=None, selector=None):
        self.calls.append((cmd, params or {}))
        return {}

    def wait_for_idle(self, **kw):
        pass


@pytest.fixture
def session():
    return RecordingSession()


# ------------------------------------------------------------------ per-call mode


MOUSE_CALLS = [
    ("click", (10, 20), "input.click"),
    ("move", (10, 20), "input.hover"),
    ("down", (), "input.press"),
    ("up", (), "input.release"),
    ("wheel", (10, 20), "input.wheel"),
]


@pytest.mark.parametrize(("method", "args", "cmd"), MOUSE_CALLS)
def test_mouse_forwards_an_explicit_mode(session, method, args, cmd):
    getattr(Mouse(session, "win"), method)(*args, mode="synthetic")
    sent_cmd, params = session.calls[-1]
    assert sent_cmd == cmd
    assert params["mode"] == "synthetic"


@pytest.mark.parametrize(("method", "args", "cmd"), MOUSE_CALLS)
def test_mouse_omits_mode_when_not_asked_for(session, method, args, cmd):
    """Omitted, not null: the agent falls back to the session mode on absence."""
    getattr(Mouse(session, "win"), method)(*args)
    _, params = session.calls[-1]
    assert "mode" not in params


def test_mouse_drag_forwards_mode(session):
    Mouse(session, "win").drag((0, 0), (5, 5), mode="native")
    cmd, params = session.calls[-1]
    assert cmd == "input.drag"
    assert params["mode"] == "native"


KEYBOARD_CALLS = [
    ("press", ("Ctrl+S",), "input.key"),
    ("type", ("abc",), "input.type_text"),
    ("down", ("Shift",), "input.press"),
    ("up", ("Shift",), "input.release"),
]


@pytest.mark.parametrize(("method", "args", "cmd"), KEYBOARD_CALLS)
def test_keyboard_forwards_an_explicit_mode(session, method, args, cmd):
    getattr(Keyboard(session, "win"), method)(*args, mode="synthetic")
    sent_cmd, params = session.calls[-1]
    assert sent_cmd == cmd
    assert params["mode"] == "synthetic"


@pytest.mark.parametrize(("method", "args", "cmd"), KEYBOARD_CALLS)
def test_keyboard_omits_mode_when_not_asked_for(session, method, args, cmd):
    getattr(Keyboard(session, "win"), method)(*args)
    _, params = session.calls[-1]
    assert "mode" not in params


def test_the_window_handle_always_rides_along(session):
    Mouse(session, "win-7").click(1, 2)
    assert session.calls[-1][1]["handle"] == "win-7"
    Keyboard(session, "win-7").press("A")
    assert session.calls[-1][1]["handle"] == "win-7"


# ------------------------------------------------------------------ chords


def test_keyboard_type_splits_chords_out_of_the_text(session):
    """The gap that mattered: this used to send one type_text containing "<Ctrl+A>"."""
    Keyboard(session, "win").type("hello<Ctrl+A>replaced")
    assert [c for c, _ in session.calls] == [
        "input.type_text", "input.key", "input.type_text",
    ]
    assert session.calls[0][1]["text"] == "hello"
    assert session.calls[1][1]["key"] == "Ctrl+A"
    assert session.calls[2][1]["text"] == "replaced"


def test_plain_text_is_still_one_call(session):
    Keyboard(session, "win").type("nothing special here")
    assert len(session.calls) == 1
    assert session.calls[0][1]["text"] == "nothing special here"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("abc", [("text", "abc")]),
        ("<Ctrl+A>", [("chord", "Ctrl+A")]),
        ("a<Enter>b", [("text", "a"), ("chord", "Enter"), ("text", "b")]),
        ("<Ctrl+Shift+S>", [("chord", "Ctrl+Shift+S")]),
        # A '<' that does not open a chord-shaped token stays literal.
        ("a < b", [("text", "a < b")]),
        ("2 <3 and 4> 1", [("text", "2 <3 and 4> 1")]),
        ("", []),
    ],
)
def test_split_chords_decides_what_is_a_chord(text, expected):
    assert list(split_chords(text)) == expected


def test_locator_and_keyboard_agree_on_the_same_string():
    """The whole reason the splitter is shared: one string, one meaning."""
    from liberaqt.locator import split_chords as from_locator

    assert from_locator is split_chords
