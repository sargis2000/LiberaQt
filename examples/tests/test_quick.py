"""The same scenario against the Qt Quick sample.

Only the selector strings differ -- the API, the waiting model and the assertions are identical.
That is the point of splicing the Quick scene graph into the same object tree.
"""

import sys
from pathlib import Path

import pytest

from liberaqt import expect


@pytest.fixture
def quick_app(liberaqt, liberaqt_config):
    exe = liberaqt_config.get("quick_executable", "build/sample/sample_quick")
    if sys.platform == "win32" and not Path(exe).exists() and Path(f"{exe}.exe").exists():
        exe = f"{exe}.exe"
    app = liberaqt.launch(exe, headless=bool(liberaqt_config.get("headless")))
    yield app
    app.close()


def test_login_shows_welcome(quick_app):
    win = quick_app.window(title="Login")

    win.locator("TextField#usernameField").fill("sargis")
    win.locator("Button#submitButton").click()

    expect(win.locator("Label#statusLabel")).to_have_text("Welcome, sargis")


def test_list_view_becomes_visible(quick_app):
    win = quick_app.window(title="Login")
    win.locator("TextField#usernameField").fill("sargis")
    win.locator("Button#submitButton").click()

    orders = win.locator("ListView#ordersList")
    expect(orders).to_be_visible()
    expect(win.locator("Label[objectName='orderRow']")).to_have_count(3)


def test_qml_evaluate(quick_app):
    win = quick_app.window(title="Login")
    orders = win.locator("ListView#ordersList")
    assert orders.evaluate("model.count") == 3
