"""Qt D-Bus Viewer: a main window with no title, and an application in an error state.

On Windows there is no session bus, so the viewer shows a connection error. Driving an
application that is unhappy is a normal thing to have to do, and the widget tree is built either
way.
"""

import pytest

from liberaqt import UnsupportedOperationError, expect


def test_a_window_with_an_empty_title_is_still_addressable(qdbusviewer):
    """`window(title="")` has to mean "the one with no title", not "any window"."""
    win = qdbusviewer.window(title="")
    assert win.title == ""
    assert win.locator("QWidget").count > 5


def test_menus_resolve_by_text(qdbusviewer):
    win = qdbusviewer.window(title="")
    expect(win.locator("QMenuBar")).to_exist()
    expect(win.locator("QMenu[text='File']")).to_exist()
    expect(win.locator("QMenu[text='Help']")).to_exist()


def test_tab_widget_is_present(qdbusviewer):
    """A QTabWidget, which none of the other targets in this suite has."""
    expect(qdbusviewer.window(title="").locator("QTabWidget")).to_exist()


def test_application_defined_classes_resolve(qdbusviewer):
    """QDBusViewer and LogViewer are the application's own C++ classes, not Qt's."""
    win = qdbusviewer.window(title="")
    expect(win.locator("QDBusViewer")).to_exist()
    expect(win.locator("LogViewer")).to_exist()


def test_error_text_is_readable(qdbusviewer):
    """Whatever the viewer is complaining about, we should be able to read it back."""
    assert isinstance(qdbusviewer.window(title="").locator("LogViewer").text, str)


# ---------------------------------------------------------- interacting with a custom class
#
# LogViewer is the application's own QTextBrowser subclass. Everything below goes through Qt's
# meta-object system, which is exactly the boundary of what any in-process tool can reach: a class
# needs Q_OBJECT to be named, Q_PROPERTY to be read, and a slot or Q_INVOKABLE to be called.


def log_viewer(app):
    return app.window(title="").locator("LogViewer")


def test_a_custom_class_is_matched_through_its_qt_base_classes(qdbusviewer):
    """Selecting by a base type must find the subclass, or `QWidget` would be useless."""
    win = qdbusviewer.window(title="")
    assert win.locator("LogViewer").count == 1
    assert win.locator("QTextBrowser").count >= 1
    assert win.locator("QWidget").count > win.locator("QAbstractScrollArea").count > 0


def test_properties_of_a_custom_class_are_readable(qdbusviewer):
    log = log_viewer(qdbusviewer)
    assert log["readOnly"] is True
    assert isinstance(log["openLinks"], bool)


def test_invoking_a_slot_on_a_custom_class_has_a_visible_effect(qdbusviewer):
    """Not just "the call returned" -- the application must actually have done the thing."""
    log = log_viewer(qdbusviewer)
    log.invoke("clear")
    assert log.text == ""


def test_a_method_the_meta_object_cannot_see_fails_clearly(qdbusviewer):
    """A plain C++ method is unreachable, and the error has to say so rather than hang."""
    with pytest.raises(UnsupportedOperationError) as exc:
        log_viewer(qdbusviewer).invoke("notASlotAtAll")
    assert "invokable" in str(exc.value)


def test_enumerating_what_a_custom_class_exposes(qdbusviewer):
    """Facing someone else's class, enumeration is what you actually want.

    ``declared_in`` is the part that makes this usable: a QTextBrowser subclass inherits some
    eighty properties, and the handful the class introduced itself are the interesting ones.
    """
    result = log_viewer(qdbusviewer).properties()
    properties = result["properties"] if isinstance(result, dict) else result
    by_name = {p["name"]: p for p in properties}

    assert len(properties) > 50
    assert by_name["objectName"]["writable"] is True
    assert by_name["readOnly"]["type"] == "bool"
    # Every entry says which class introduced it, so a custom widget's own additions stand out.
    assert {p["declared_in"] for p in properties} > {"QWidget"}
