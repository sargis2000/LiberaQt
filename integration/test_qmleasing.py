"""QML Easing Curve Editor: a QWidget shell alongside a live Qt Quick scene.

This is the suite's only QML coverage, and it is worth being precise about what works, because
the state of QML support turned out to be narrower than "broken":

* Locators **do** reach Qt Quick objects. ``object.find`` descends into the Quick scene, so
  ``QQuickRectangle``, ``QQuickText`` and QML-defined types all resolve.
* ``object.tree`` does **not**. It returns no children for a Quick window, which is why
  ``liberaqt inspect`` prints "no objects found" for one and cannot suggest a QML selector.

The second point is the ``TODO(m0)`` in ``selector_engine.cpp``, and the xfail below is the signal
that it has been fixed.
"""

import pytest

from liberaqt import expect

# ------------------------------------------------------------------ the widget shell


def test_widget_shell_controls_resolve(qmleasing):
    win = qmleasing.window(title="QML Easing Curve Editor")
    expect(win.locator("QSpinBox#spinBox")).to_exist()
    expect(win.locator("QComboBox#comboBox")).to_exist()
    expect(win.locator("QPlainTextEdit#plainTextEdit")).to_exist()
    expect(win.locator("QGroupBox#groupBox")).to_exist()


def test_spin_box_value_round_trips(qmleasing):
    """A QSpinBox, which the rest of the suite does not cover."""
    spin = qmleasing.window(title="QML Easing Curve Editor").locator("QSpinBox#spinBox")
    original = spin["value"]
    spin["value"] = 750
    assert spin["value"] == 750
    spin["value"] = original


def test_combo_box_reports_its_items(qmleasing):
    combo = qmleasing.window(title="QML Easing Curve Editor").locator("QComboBox#comboBox")
    assert combo["count"] >= 2
    assert isinstance(combo["currentText"], str)


# ------------------------------------------------------------------ the Quick scene


def test_the_quick_scene_is_a_separate_top_level(qmleasing):
    """Two windows: the widget editor, and the Quick preview which carries no title."""
    titles = sorted(w.title for w in qmleasing.windows)
    assert "QML Easing Curve Editor" in titles
    assert "" in titles


def test_locators_reach_qml_objects(quick_window):
    """QML items resolve through the ordinary selector path, same as widgets."""
    assert quick_window.locator("*").count > 0
    expect(quick_window.locator("QQuickRootItem")).to_exist()
    expect(quick_window.locator("QQuickRectangle")).to_exist()


def test_qml_item_properties_are_readable(quick_window):
    """Reading a Q_PROPERTY off a QML item, which is the whole point of being in-process."""
    rect = quick_window.locator("QQuickRectangle").first
    assert isinstance(rect["width"], (int, float))
    assert rect["width"] > 0


def test_qml_types_participate_in_inheritance_matching(quick_window):
    """QQuickItem is the QML equivalent of QWidget: it must match far more than a leaf type."""
    items = quick_window.locator("QQuickItem").count
    rectangles = quick_window.locator("QQuickRectangle").count
    assert items > rectangles > 0


@pytest.mark.xfail(strict=True, reason="selector_engine.cpp TODO(m0): the tree walk never steps "
                                       "from a QQuickWindow into its contentItem")
def test_object_tree_descends_into_the_quick_scene(quick_window):
    """When this XPASSes, `liberaqt inspect` can describe a QML window and the marker should go."""
    assert quick_window.tree().get("children")
