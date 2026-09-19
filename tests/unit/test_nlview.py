"""Parsing and driving an NLview canvas.

The parser is pure logic and needs no Qt, which is the point of testing it here: NLview's answer
format is the only part of this that can be got wrong quietly. A misparse does not fail, it
returns a plausible object with the wrong coordinates.

Every sample below is real output, captured from Libero SoC's SmartDesign canvas.
"""

import pytest

from liberaqt.errors import LiberaQtError, ObjectNotFoundError
from liberaqt.nlview import NlviewCanvas, NlviewObject, parse_result

#: One instance with its position, as `search -pos inst *` reports it.
WITH_POSITION = "{inst ahb2axi_inst_0 ahb2axi_inst::ahb2axi_inst_0 v -pg 1 -lvl 1 -x 20 -y 100}"

#: The same instance long-form, which is what carries the pin names.
LONG_FORM = (
    "{inst ahb2axi_inst_0 ahb2axi_inst::ahb2axi_inst_0 v "
    "-attr @cell ahb2axi_inst -attr @fillcolor F,#DBF3F5,#B0D0CD -attr @name ahb2axi_inst_0 "
    "-pinAttr HCLK @color #222222 -pinAttr HRESETn @color #222222 "
    "-pinAttr ACLK @color #222222 -pinAttr ARESETn @color #222222 "
    "-pinAttr AHBSlaveIF @color #666600 -pinAttr AXIMasterIF @color #666600}"
)


# ------------------------------------------------------------------ the parser


def test_an_empty_answer_is_no_objects():
    """NLview answers an empty search with an empty string, not an error."""
    assert parse_result("") == []


def test_positional_words_come_out_in_order():
    entry = parse_result(WITH_POSITION)[0]
    assert entry["words"] == [
        "inst", "ahb2axi_inst_0", "ahb2axi_inst::ahb2axi_inst_0", "v",
    ]


def test_options_are_collected_with_their_values():
    entry = parse_result(WITH_POSITION)[0]
    assert entry["options"] == [("pg", ["1"]), ("lvl", ["1"]), ("x", ["20"]), ("y", ["100"])]


def test_options_of_differing_arity():
    """`-x` takes one value and `-pinAttr` takes three, so arity cannot be assumed."""
    options = dict(parse_result(LONG_FORM)[0]["options"])
    assert options["pinAttr"] == ["AXIMasterIF", "@color", "#666600"]   # last of its repeats


def test_a_repeated_option_is_kept_rather_than_overwritten():
    """`-pinAttr` appears once per pin; collapsing them would lose every pin but one."""
    entry = parse_result(LONG_FORM)[0]
    pin_attrs = [values for key, values in entry["options"] if key == "pinAttr"]
    assert len(pin_attrs) == 6


def test_several_objects_are_split_apart():
    text = "{inst a x v -x 1 -y 2} {inst b y v -x 3 -y 4}"
    assert [e["words"][1] for e in parse_result(text)] == ["a", "b"]


def test_a_negative_number_is_not_mistaken_for_an_option():
    """Coordinates go negative off-page, and `-12` must stay a value."""
    entry = parse_result("{inst a b v -x -12 -y -40}")[0]
    assert dict(entry["options"])["x"] == ["-12"]
    assert dict(entry["options"])["y"] == ["-40"]


def test_nested_braces_do_not_split_a_group():
    assert len(parse_result("{inst a b v -attr {x y} -x 1}")) == 1


# ------------------------------------------------------------------ the object wrapper


def test_an_object_exposes_its_names_and_position():
    obj = NlviewObject(parse_result(WITH_POSITION)[0])
    assert obj.kind == "inst"
    assert obj.name == "ahb2axi_inst_0"
    assert obj.full_name == "ahb2axi_inst::ahb2axi_inst_0"
    assert (obj.x, obj.y, obj.page) == (20, 100, 1)


def test_pins_come_from_the_long_form():
    obj = NlviewObject(parse_result(LONG_FORM)[0])
    assert obj.pins == [
        "HCLK", "HRESETn", "ACLK", "ARESETn", "AHBSlaveIF", "AXIMasterIF",
    ]


def test_missing_position_reads_as_none_rather_than_zero():
    """Nought is a real coordinate, so absence must not look like the origin."""
    obj = NlviewObject(parse_result("{inst a b v}")[0])
    assert obj.x is None and obj.y is None


def test_pins_are_empty_without_the_long_form():
    assert NlviewObject(parse_result(WITH_POSITION)[0]).pins == []


# ------------------------------------------------------------------ the canvas


class FakeLocator:
    """Answers call_native from a scripted table, recording what was asked."""

    def __init__(self, answers):
        self.answers = answers
        self.sent = []

    def call_native(self, module, symbol, argument, signature="cstr(bool*,cstr)"):
        self.sent.append(argument)
        return self.answers.get(argument, (False, f'unknown command "{argument}"'))


class FakeWindow:
    def __init__(self, count):
        self._count = count

    def locator(self, selector):
        window = self

        class Found:
            count = window._count
            first = "the-canvas"

        return Found()


def test_a_rejected_command_raises_with_nlviews_own_complaint():
    """Its complaint names the right syntax, so replacing it would lose the useful part."""
    canvas = NlviewCanvas(FakeLocator({}))
    with pytest.raises(LiberaQtError) as excinfo:
        canvas.command("bbox")
    assert 'unknown command "bbox"' in str(excinfo.value)


def test_try_command_reports_failure_without_raising():
    """Asking for something invalid is how the command set is discovered."""
    ok, output = NlviewCanvas(FakeLocator({})).try_command("help")
    assert ok is False
    assert "help" in output


def test_instances_are_asked_for_with_positions_and_pins():
    """Both flags, because they carry different things and neither implies the other.

    `-pos` gives -x/-y, `-long` gives the -pinAttr runs. Asking for only `-long` returns an
    instance with no coordinates, which reads like a canvas that does not place its objects.
    """
    locator = FakeLocator({"search -pos -long inst *": (True, LONG_FORM)})
    instances = NlviewCanvas(locator).instances()
    assert locator.sent == ["search -pos -long inst *"]
    assert instances[0].pins[0] == "HCLK"


def test_search_asks_for_positions_when_not_long():
    locator = FakeLocator({"search -pos net *": (True, "")})
    NlviewCanvas(locator).search("net", long=False)
    assert locator.sent == ["search -pos net *"]


def test_coordinates_convert_both_ways():
    locator = FakeLocator({
        "convertcoords 20 100": (True, "-629 -152"),
        "convertcoords -reverse 1149 552": (True, "500 300"),
    })
    canvas = NlviewCanvas(locator)
    assert canvas.to_screen(20, 100) == (-629, -152)
    assert canvas.to_schematic(1149, 552) == (500, 300)


def test_a_malformed_coordinate_reply_is_an_error():
    canvas = NlviewCanvas(FakeLocator({"convertcoords 1 2": (True, "oops")}))
    with pytest.raises(LiberaQtError):
        canvas.to_screen(1, 2)


def test_page_size_is_parsed():
    assert NlviewCanvas(FakeLocator({"pagesize": (True, "210 200")})).page_size == (210, 200)


def test_find_locates_the_canvas_by_nlviews_own_class():
    """The host's subclass name is irrelevant; type matching walks the inheritance chain."""
    assert isinstance(NlviewCanvas.find(FakeWindow(count=1)), NlviewCanvas)


def test_find_says_so_when_there_is_no_canvas():
    with pytest.raises(ObjectNotFoundError):
        NlviewCanvas.find(FakeWindow(count=0))
