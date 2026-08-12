"""Input that behaves like a person's, checked against Qt Assistant.

Every step below is something a user does with a mouse and a keyboard. Nothing here writes a
property or invokes a slot to get its result -- that is the whole point. Assistant is a good
subject because its find bar is a real text field that only appears in response to a shortcut, so
reaching it at all requires the input to work.

The delivery mode matters and is asserted on directly:

* **native** hands each event to Qt where a platform plugin would, so Qt hit-tests for the
  receiver, tracks hover, holds the implicit grab between press and release, derives double
  clicks and moves focus to whatever was clicked.
* **synthetic** delivers straight to one widget. The widget's handler runs, but none of the
  routing does -- most visibly, a click does not focus what it hits, so typing afterwards goes
  wherever focus already was.

The second is kept because it still reaches a target that is off-screen or covered. Assistant
demonstrates why that matters: its Index, Search and Bookmark docks are `isVisible()` but parked
at negative coordinates while their tabs are not current, and a real click cannot reach those.
"""

from __future__ import annotations

import pytest

from liberaqt import ProtocolError, expect


@pytest.fixture
def find_bar(assistant):
    """Assistant's find bar, revealed with Ctrl+F and left empty.

    Yields the field rather than the bar: the field is the only text input reliably on screen,
    because every other one in Assistant lives in a dock whose tab is not current.
    """
    assistant.set_input_mode("native")
    win = next(w for w in assistant.windows if "Assistant" in w.title)
    bar = win.locator("FindWidget")
    if not bar.is_visible:
        assistant._session.call("input.key", {"key": "Ctrl+F"})
        assistant.wait_for_idle()
    field = bar.locator("QLineEdit")
    field.fill("")
    yield win, field


def test_a_shortcut_with_nothing_targeted_reaches_the_application(assistant):
    """Ctrl+F, pressed at the application rather than at a widget, opens the find bar.

    A shortcut belongs to the window, not to whatever happens to hold focus, so this sends no
    handle at all -- exactly as a user presses a key without nominating a receiver.
    """
    assistant.set_input_mode("native")
    win = next(w for w in assistant.windows if "Assistant" in w.title)
    bar = win.locator("FindWidget")
    bar["visible"] = False
    assistant.wait_for_idle()

    assistant._session.call("input.key", {"key": "Ctrl+F"})
    assistant.wait_for_idle()

    expect(bar).to_be_visible()


def test_clicking_a_field_makes_it_the_typing_target(find_bar, assistant):
    """The chain a user relies on: click, then type, with no handle on the typing.

    This is the whole argument for native delivery. Focus-on-click lives in ``QWidgetWindow``,
    which only runs for input that arrived through the window system, so a synthetic click leaves
    focus where it was and the text goes somewhere else entirely.

    Asserted on where the text lands rather than on ``hasFocus()``. ``QWidget::hasFocus()`` is
    ``QApplication::focusWidget() == this``, and that is null whenever the application is not the
    foreground one -- the normal state of an application under test, and guaranteed when the
    suite has several running at once.
    """
    win, field = find_bar
    win.locator("QLiteHtmlWidget").invoke("setFocus")
    assistant.wait_for_idle()

    field.click()
    assistant._session.call("input.type_text", {"text": "widget"})
    assistant.wait_for_idle()

    expect(field).to_have_text("widget")


def test_double_click_selects_a_word(find_bar, assistant):
    """Two presses close together, which is all a double click is.

    Qt derives it exactly as it does for a user; the agent never sends a ``MouseButtonDblClick``
    of its own on the native path.
    """
    _, field = find_bar
    field.click()
    assistant._session.call("input.type_text", {"text": "counter"})
    assistant.wait_for_idle()

    field.double_click()
    assistant.wait_for_idle()

    assert field["selectedText"] == "counter"


def test_select_all_and_retype_using_only_the_keyboard(find_bar, assistant):
    """Ctrl+A then typing, which needs the shortcut to reach the focused field."""
    _, field = find_bar
    field.click()
    assistant._session.call("input.type_text", {"text": "discard me"})
    assistant.wait_for_idle()

    assistant._session.call("input.key", {"key": "Ctrl+A"})
    assistant._session.call("input.type_text", {"text": "Qt"})
    assistant.wait_for_idle()

    expect(field).to_have_text("Qt")


def test_clicking_the_menu_bar_opens_a_menu(assistant):
    """A popup is a new top-level window, so there is no ambiguity about whether it landed.

    Opening a menu also runs a nested event loop, which is why every input command replies from
    the event loop rather than from inside its own handler.
    """
    assistant.set_input_mode("native")
    win = next(w for w in assistant.windows if "Assistant" in w.title)
    before = len(assistant.windows)

    assistant._session.call("input.click",
                            {"handle": win.locator("QMenuBar").resolve(), "pos": [20, 12]})
    assistant.wait_for_idle()
    opened = len(assistant.windows)

    assistant._session.call("input.key", {"key": "Esc"})
    assistant.wait_for_idle()

    assert opened > before, "clicking the menu bar opened no popup"


def test_synthetic_delivery_reaches_the_widget_but_skips_the_routing(find_bar, assistant):
    """The trade the second mode makes, asserted rather than described.

    A synthetic click runs the widget's own handler -- so it is still useful for reaching
    something a user could not -- but Qt never sees it, so focus does not follow it and the
    typing afterwards goes wherever focus already was. Anything that depends on what happens
    *around* an event needs the native mode.
    """
    win, field = find_bar
    try:
        assistant.set_input_mode("synthetic")
        win.locator("QLiteHtmlWidget").invoke("setFocus")
        assistant.wait_for_idle()

        field.click()
        assistant._session.call("input.type_text", {"text": "widget"})
        assistant.wait_for_idle()

        assert field.text == "", \
            "synthetic delivery moved the typing target; it is not supposed to route the event"
    finally:
        assistant.set_input_mode("native")


def test_the_input_mode_is_reported_as_applied(assistant):
    """An agent that ignored the option would leave tests silently in the wrong mode."""
    assistant.set_input_mode("native")  # raises if the agent did not apply it
    with pytest.raises(ProtocolError, match="sideways"):
        assistant.set_input_mode("sideways")
