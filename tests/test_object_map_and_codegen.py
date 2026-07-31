import pytest

from qtdriver.codegen import render
from qtdriver.errors import QtDriverError
from qtdriver.session import ObjectMap


def test_object_map_flattens_nested_yaml_structure():
    om = ObjectMap({"login": {"submit": "QPushButton#ok", "nested": {"deep": "QLabel"}}})
    assert om.resolve("login.submit") == "QPushButton#ok"
    assert om.resolve("login.nested.deep") == "QLabel"
    assert len(om) == 2


def test_object_map_unknown_name_suggests_alternatives():
    om = ObjectMap({"login": {"submit": "QPushButton#ok"}})
    with pytest.raises(QtDriverError) as excinfo:
        om.resolve("login.missing")
    assert "login.submit" in str(excinfo.value)


def test_codegen_renders_a_runnable_module():
    source = render([
        {"action": "window_opened", "title": "Login"},
        {"action": "fill", "selector": "QLineEdit#usernameField", "text": "sargis"},
        {"action": "click", "selector": "QPushButton#submitButton"},
        {"action": "assert_text", "selector": "QLabel#statusLabel", "text": "Welcome"},
    ], test_name="test_login")

    compile(source, "<recorded>", "exec")      # must be syntactically valid Python
    assert "def test_login(app):" in source
    assert '.fill(\'sargis\')' in source
    assert "expect(" in source


def test_codegen_flags_brittle_selectors():
    source = render([{"action": "click", "selector": "QPushButton", "brittle": True}])
    assert "TODO: brittle selector" in source
    compile(source, "<recorded>", "exec")
