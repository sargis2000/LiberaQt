"""Reading and writing a widget that paints its own contents.

Libero's SmartDesign canvas is `Aqnlvcanvas::NlvSDWidget`, which wraps NLview. It renders the
schematic itself, so there is no Qt scene graph and no per-item objects: `QGraphicsView` matches
zero, `to_records()` refuses it, and no selector will ever reach a pin or a net. Everything the
driver normally relies on is absent.

What it does have is slots -- and that is the general lesson, not a Libero one. For any widget
like this the meta-object is the entire way in, which is why `object.list_methods` exists.

Two things have to keep working for that to be true, and this file pins both:

* the methods must be *discoverable*, since nobody has the headers;
* `std::string` parameters must be *callable*. Qt has no metatype for `std::string`, so QVariant
  cannot convert into one and every such slot was refused outright until the agent learned to
  build the value itself. That is where this canvas keeps its whole pin and net API, and an
  application written in ordinary C++ hides much of itself the same way.

Creates a project and a SmartDesign on disk, so it is not read-only, but it configures nothing
and spawns no configurator: roughly a minute.
"""

from __future__ import annotations

import glob
import os
import shutil
import time
from pathlib import Path

import pytest

from liberaqt import LiberaQt, LiberaQtError

LIBERO_GLOB = "C:/Microchip/Libero_SoC_*/Libero_SoC/Designer/bin/libero.exe"
PROJECT_DIR = Path(r"C:\Users\Public\lqt_sd_test")
PROJECT_NAME = f"sdt_{os.getpid()}"
PART = "M2S005-1TQ144"
OBJECTS = str(Path(__file__).with_name("objects.yaml"))

#: The canvas itself. `nlv` for NLview, which is what it wraps.
CANVAS = "Aqnlvcanvas::NlvSDWidget"


def _libero() -> str:
    found = sorted(glob.glob(LIBERO_GLOB))
    if not found:
        pytest.skip("Libero is not installed")
    return found[-1]


@pytest.fixture(scope="module")
def canvas():
    """A SmartDesign canvas, reached through a freshly created project."""
    shutil.rmtree(PROJECT_DIR, ignore_errors=True)
    PROJECT_DIR.mkdir(parents=True, exist_ok=True)

    with LiberaQt(default_timeout=20.0) as lq:
        app = lq.launch(_libero(), timeout=240.0, object_map=OBJECTS)
        app.set_input_mode("native")
        try:
            app.wait_for_window(title="Information", timeout=60.0).obj("startup.dismiss").click()
        except LiberaQtError:
            pass
        app.wait_for_window(title="Libero", timeout=120.0)
        app.wait_for_idle(timeout=60.0)
        time.sleep(2)

        def main():
            return next(w for w in app.windows if w.title.startswith("Libero"))

        main().menu("Project > New Project").trigger()
        time.sleep(4)
        dlg = app.window(title="New project")
        for key, value in (("wizard.project_name", PROJECT_NAME),
                           ("wizard.project_location", str(PROJECT_DIR))):
            field = dlg.obj(key)
            field.click()
            field.type(f"<Ctrl+A>{value}")
        dlg.obj("wizard.next").click()
        app.wait_for_idle(timeout=60.0)
        time.sleep(2)
        dlg.obj("wizard.part_list").select_item(text=PART)
        app.wait_for_idle(timeout=30.0)
        time.sleep(1)
        dlg.obj("wizard.finish").click()
        app.wait_for_idle(timeout=90.0)
        time.sleep(8)

        main().menu("File > New > SmartDesign").trigger()
        time.sleep(4)
        create = app.window(title="Create New SmartDesign")
        name = create.locator("QLineEdit#componentName")
        name.click()
        name.type("<Ctrl+A>top_sd")
        create.locator("QPushButton[text='OK']").click()
        app.wait_for_idle(timeout=90.0)
        time.sleep(8)

        win = next(w for w in app.windows if w.title.startswith("Libero"))
        yield win.locator(CANVAS).first


# ------------------------------------------------------------------ nothing to select


def test_the_canvas_contains_nothing_selectable(canvas):
    """The premise. NLview paints its own scene, so pins and nets are not QObjects at all.

    Stated as a test rather than a comment, because if this ever stops being true -- a Qt-native
    canvas replacing NLview -- everything below becomes the wrong approach and someone should
    find out from a failure rather than by reading.
    """
    assert canvas.is_visible
    with pytest.raises(LiberaQtError):
        canvas.to_records()      # not a model-backed view


# ------------------------------------------------------------------ discovering the API


def test_the_canvas_declares_no_properties_of_its_own(canvas):
    """So `properties()` cannot be the way in, however reasonable that sounds."""
    assert canvas.properties(declared_only=True) == []
    assert canvas.properties(), "inherited properties should still be listed"


def test_properties_returns_a_list_of_entries(canvas):
    """Regression: this used to hand back the whole {"properties": [...]} envelope.

    Iterating it yielded the string "properties", so the first caller to read an entry got an
    AttributeError -- against an annotation promising list[dict].
    """
    found = canvas.properties()
    assert isinstance(found, list)
    assert all(isinstance(entry, dict) and "name" in entry for entry in found)


def test_the_canvas_declares_many_callable_slots(canvas):
    """The meta-object is the whole surface, and `declared_only` is what makes it readable."""
    everything = canvas.methods()
    own = canvas.methods(declared_only=True)
    callable_own = canvas.methods(declared_only=True, callable_only=True)

    assert len(own) > 20, f"expected a substantial slot API, got {len(own)}"
    assert len(own) < len(everything), "declared_only did not drop anything inherited"
    assert callable_own, "none of its own methods are callable"
    assert all(m["kind"] != "signal" for m in callable_own), "a signal was marked callable"


def test_the_pin_and_net_api_is_discoverable(canvas):
    """You cannot invoke what you cannot find, and nobody has Libero's headers."""
    names = {m["name"] for m in canvas.methods(declared_only=True, callable_only=True)}
    assert {"isNetHidden", "isItemHidden", "doHideNet"} <= names, (
        f"the pin/net API is not where it was; found {sorted(names)[:20]}"
    )


# ------------------------------------------------------------------ calling it


def test_slots_with_ordinary_arguments_are_callable(canvas):
    """The baseline: bool arguments and bool returns work through the metatype system."""
    assert canvas.invoke("doUnhighlightAll") is None          # void
    assert canvas.invoke("doGridEnable", True) is None        # bool argument
    assert isinstance(canvas.invoke("doRatsNestNets", False), bool)


def test_a_std_string_argument_is_accepted(canvas):
    """The fix this file exists for.

    Qt has no metatype for `std::string`, so QVariant cannot convert into one and the argument
    was refused before it was ever passed:

        [invalid_params] argument 0 of 'isNetHidden' must be std::string

    A direct invocation only forwards a pointer, so the agent builds the value itself. Safe only
    because it is compiled against the same standard library as the application -- which loading
    as a Qt plugin already required.
    """
    assert canvas.invoke("isNetHidden", "no_such_net_exists") is False


def test_a_std_string_reference_argument_is_accepted(canvas):
    """`std::string&` and `const std::string&` have to be treated the same as the value form."""
    assert canvas.invoke("isExpandedInPlace", "no_such_instance") is False


def test_writing_through_a_std_string_slot(canvas):
    """Not only reading: the same path is how you change what the canvas is showing."""
    result = canvas.invoke("doHideNet", "no_such_net_exists", True)
    assert isinstance(result, bool)


def test_a_wrong_argument_type_still_fails_clearly(canvas):
    """Widening the conversion must not turn a mistake into a silent success."""
    with pytest.raises(LiberaQtError) as excinfo:
        canvas.invoke("doGridEnable", "not_a_bool", "extra", "args")
    assert "doGridEnable" in str(excinfo.value)
