"""Example suite against the sample QWidget application.

Run with:
    pytest examples/tests/test_widgets.py --qtdriver-exe build/sample/sample_widgets
"""

from qtdriver import expect


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
