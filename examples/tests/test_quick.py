"""The same scenario against the Qt Quick sample.

Only the selector strings differ -- the API, the waiting model and the assertions are identical.
That is the point of splicing the Quick scene graph into the same object tree.
"""

from pathlib import Path

import pytest

from liberaqt import expect

# The Quick scene graph is not reachable yet: the selector engine never steps from the
# QQuickWindow into its contentItem, so window.list finds the window but nothing under it
# resolves. See the TODO in agent/src/selector_engine.cpp and Milestone 2 in docs/ROADMAP.md.
# strict=True on purpose -- when the traversal lands these XPASS and CI says to drop the marker.
pytestmark = pytest.mark.xfail(
    reason="QML: QQuickWindow contentItem is not spliced into the object tree yet",
    strict=True,
)


def _find_quick_sample(configured: str | None) -> str | None:
    """Locate the built Quick sample, tolerating per-generator output layouts."""
    candidates = [configured] if configured else []
    # Multi-config generators (MSVC, as used on the Windows CI leg) nest by build type.
    candidates += ["build/sample/sample_quick", "build/sample/Release/sample_quick"]
    for name in candidates:
        for path in (Path(name), Path(f"{name}.exe")):
            if path.exists():
                return str(path)
    return None


@pytest.fixture
def quick_app(liberaqt, liberaqt_config):
    exe = _find_quick_sample(liberaqt_config.get("quick_executable"))
    if exe is None:
        # Skip rather than error: a missing sample is a build-coverage gap, not a test failure,
        # and a fixture error would escape the xfail marker and turn CI red.
        pytest.skip("the Qt Quick sample app is not built")
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
