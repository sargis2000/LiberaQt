"""Example suite against the sample QWidget application.

Run with:
    pytest examples/tests/test_widgets.py --liberaqt-exe build/sample/sample_widgets
"""

import pytest

from liberaqt import expect


def test_login_shows_welcome(app):
    win = app.window(title="Login")

    win.locator("QLineEdit#usernameField").fill("sargis")
    win.locator("QLineEdit#passwordField").fill("hunter2")
    win.locator("QPushButton#submitButton").click()

    # The sample app updates the status asynchronously after 300 ms. No sleep here: the assertion
    # retries until it passes or the timeout expires.
    expect(win.locator("QLabel#statusLabel")).to_have_text("Welcome, sargis")


def test_validation_error(app):
    win = app.window(title="Login")
    win.locator("QPushButton#submitButton").click()
    expect(win.locator("QLabel#statusLabel")).to_have_text("User name is required")


def test_orders_table_contents(app):
    win = app.window(title="Login")
    win.locator("QLineEdit#usernameField").fill("sargis")
    win.locator("QPushButton#submitButton").click()

    table = win.locator("QTableView#ordersTable")
    expect(table).to_be_visible()

    rows = table.to_records()
    assert len(rows) == 3
    assert rows[1]["Order"] == "INV-1042"


def test_object_map(app):
    """Same test, written against objects.yaml instead of inline selectors."""
    win = app.window(title="Login")
    win.obj("login.username").fill("sargis")
    win.obj("login.submit").click()
    expect(win.obj("login.status")).to_contain_text("Welcome")


def test_property_access(app):
    win = app.window(title="Login")
    password = win.locator("QLineEdit#passwordField")

    assert password["echoMode"] == 2          # QLineEdit::Password
    password["echoMode"] = 0                  # make it readable for a debugging screenshot
    assert password["echoMode"] == 0


def test_helpful_failure_message(app):
    """Not a real test -- documents what a failure is supposed to look like."""
    win = app.window(title="Login")
    try:
        win.locator("QPushButton[text='Sign in']").click(timeout=0.5)
    except Exception as exc:
        message = str(exc)
        assert "QPushButton" in message
        # The near-miss list should point at the button that does exist.
        assert "Log in" in message or "near misses" in message


def test_invoke_calls_a_slot(app):
    win = app.window(title="Login")
    user = win.locator("QLineEdit#usernameField")
    user.fill("sargis")
    user.invoke("clear")                 # QLineEdit::clear is a slot
    assert user.text == ""


def test_invoke_returns_the_slots_return_value(app):
    win = app.window(title="Login")
    user = win.locator("QLineEdit#usernameField")
    # QWidget::close() is a bool-returning slot; on a child widget it just hides it.
    assert user.invoke("close") is True
    assert not user.is_visible


def test_invoke_passes_arguments(app):
    win = app.window(title="Login")
    user = win.locator("QLineEdit#usernameField")
    user.invoke("setText", "typed via invoke")     # setText(QString) is a slot
    assert user.text == "typed via invoke"


def test_invoke_explains_why_a_non_slot_is_unreachable(app):
    """QWidget::resize is a plain public function, so moc never sees it."""
    win = app.window(title="Login")
    with pytest.raises(Exception) as exc:          # noqa: B017 - UnsupportedOperationError
        win.locator("QPushButton#submitButton").invoke("resize", 10, 10)

    message = str(exc.value)
    assert "no invokable method 'resize'" in message
    assert "Q_INVOKABLE" in message, "the error should say what *is* callable"


def test_window_geometry_is_writable(app):
    """resize/move go through the size and pos properties, whose setters they are.

    Reading back through those same properties also proves the agent coerced the JSON arrays
    into QSize/QPoint. Position is checked via `pos` rather than `geometry`: for a top-level
    window `pos` includes the frame and `geometry` excludes it, so they differ by the title bar.
    """
    win = app.window(title="Login")
    win.resize(640, 480)
    win.move(120, 90)

    assert win["size"] == [640, 480]
    assert win["pos"] == [120, 90]

    _, _, width, height = win.geometry
    assert (width, height) == (640, 480)


def test_window_activate(app):
    # Whether the window manager honours activation is its business; what matters here is that
    # the call reaches the agent and succeeds rather than raising "unknown method".
    app.window(title="Login").activate()


def test_ambiguous_selector_names_the_candidates(app):
    """A selector matching several objects must say which ones, against the real agent.

    Regression: the diagnostic used to call object.info with a plural `handles` key the agent
    does not accept, so this surfaced as "handle '' no longer refers to a live object".
    """
    win = app.window(title="Login")
    with pytest.raises(Exception) as exc:          # noqa: B017 - surfaces as TimeoutError
        win.locator("QLineEdit").click(timeout=0)

    message = str(exc.value)
    assert "matched 2 objects" in message
    assert "usernameField" in message and "passwordField" in message
    assert "no longer refers to a live object" not in message
