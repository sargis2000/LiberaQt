"""Unit tests for the client. No Qt, no agent, no display required."""

import pytest

from liberaqt import selectors as S
from liberaqt.errors import InvalidSelectorError


def test_type_and_object_name():
    node = S.parse("QPushButton#ok").to_json()
    assert node["steps"] == [{"type": "QPushButton", "objectName": "ok"}]


def test_descendant_and_child_combinators():
    steps = S.parse("QDialog#settings > QPushButton QLabel").to_json()["steps"]
    assert len(steps) == 3
    assert steps[1]["direct_child"] is True
    assert "direct_child" not in steps[2]


@pytest.mark.parametrize("op", ["=", "*=", "^=", "$=", "~=", "!="])
def test_all_attribute_operators(op):
    step = S.parse(f"QLabel[text{op}'x']").to_json()["steps"][0]
    assert step["attrs"] == [{"key": "text", "op": op, "value": "x"}]


def test_quoted_value_with_spaces_and_escapes():
    step = S.parse(r"QPushButton[text='Log\'s in now']").to_json()["steps"][0]
    assert step["attrs"][0]["value"] == "Log's in now"


def test_state_pseudos():
    step = S.parse("QPushButton:visible:enabled").to_json()["steps"][0]
    assert step["states"] == ["visible", "enabled"]


def test_index_pseudos():
    assert S.parse("QLabel:first").to_json()["steps"][0]["index"] == 0
    assert S.parse("QLabel:last").to_json()["steps"][0]["index"] == -1
    assert S.parse("QLabel:nth(3)").to_json()["steps"][0]["index"] == 3


def test_exact_type_bang():
    assert S.parse("QPushButton!").to_json()["steps"][0]["exact_type"] is True


def test_has_pseudo_nests_a_selector():
    step = S.parse("QListView:has(QLabel[text='Inbox'])").to_json()["steps"][0]
    assert step["has"]["steps"][0]["type"] == "QLabel"


def test_pseudo_is_not_swallowed_into_the_identifier():
    """Regression: ':' must not be part of an identifier, or ':visible:nth(2)' is one name."""
    step = S.parse("QLineEdit:visible:nth(2)").to_json()["steps"][0]
    assert step["states"] == ["visible"]
    assert step["index"] == 2


def test_from_kwargs():
    node = S.from_kwargs(type="QPushButton", text="OK").to_json()
    assert node["steps"][0]["type"] == "QPushButton"
    assert node["steps"][0]["attrs"] == [{"key": "text", "op": "=", "value": "OK"}]


def test_coerce_accepts_every_form():
    assert S.coerce("QLabel").to_json() == S.coerce({"type": "QLabel"}).to_json()
    assert S.coerce(type="QLabel").to_json() == S.coerce("QLabel").to_json()
    already = S.parse("QLabel")
    assert S.coerce(already) is already


def test_join_concatenates_steps():
    joined = S.join(S.parse("QDialog"), S.parse("QPushButton"))
    assert [s["type"] for s in joined.to_json()["steps"]] == ["QDialog", "QPushButton"]


@pytest.mark.parametrize("bad", [
    "",
    "QPushButton[text 'x']",
    "QPushButton[text='x'",
    "QPushButton:bogus",
    "QDialog >",
])
def test_invalid_selectors_raise_with_position(bad):
    with pytest.raises(InvalidSelectorError) as excinfo:
        S.parse(bad)
    assert "offset" in str(excinfo.value)
