"""Microchip Libero SoC: a large third-party application on a different ABI.

Every other target in this suite ships with Qt, which makes them convenient but also makes them
share one ABI. Libero is the opposite in every respect: a commercial FPGA design suite from a
vendor with no knowledge of this project, built as **Qt 5.15.1, 32-bit, MSVC 2019** while the
rest of the suite is Qt 6.7 / x86_64 / MinGW.

That difference is the point. These tests only pass if the registry inspected the binary and
loaded the matching agent, rather than the one that happens to be installed -- and a 32-bit
plugin in a 64-bit process, or an MSVC plugin in a MinGW one, does not load at all.

Skipped unless Libero is installed. Startup is slow (~20s) and the fixture answers a modal
update prompt before the main window exists.
"""

from liberaqt import expect


def test_the_matching_agent_was_loaded(libero):
    """Qt 5.15 in-process, from a client whose other targets are Qt 6.7."""
    info = libero.info
    assert info["app"] == "libero"
    assert info["qt"].startswith("5.15")


def test_the_main_window_is_large(libero_main):
    """Around 1200 objects. Nothing here should degrade at that size."""
    assert libero_main.locator("*").count > 500


def test_namespaced_application_classes_resolve(libero_main):
    """Libero names every class inside a namespace, so `::` is not optional here."""
    expect(libero_main.locator("Aqstpage::StartPage")).to_exist()
    expect(libero_main.locator("Aqfind::FindToolBar")).to_exist()
    assert libero_main.locator("Aqwidget::AQDockWidget").count >= 5


def test_a_custom_dock_is_matched_by_its_qt_base_class(libero_main):
    """Aqwidget::AQDockWidget derives from QDockWidget, so both must find it."""
    custom = libero_main.locator("Aqwidget::AQDockWidget").count
    base = libero_main.locator("QDockWidget").count
    assert base >= custom > 0


def test_object_names_containing_spaces(libero_main):
    """Libero's docks are named "Design Hierarchy", "HDL Templates" and so on."""
    for name in ("Design Flow", "Design Hierarchy", "Files", "Log"):
        expect(libero_main.locator(f"QDockWidget[objectName='{name}']")).to_exist()


def test_docks_report_their_titles(libero_main):
    expect(libero_main.locator("QDockWidget[objectName='Design Flow']")).to_have_text("Design Flow")


def test_the_start_page_is_addressable_by_object_name(libero_main):
    """A bare objectName still works where the application provides one."""
    expect(libero_main.locator("Aqstpage::StartPage#startPage")).to_be_visible()
    expect(libero_main.locator("QTextBrowser#projectBrowser")).to_exist()
    expect(libero_main.locator("QLabel#labelProjects")).to_exist()


def test_properties_of_a_vendor_class_are_readable(libero_main):
    """Reading Q_PROPERTYs off a class we have no source for."""
    page = libero_main.locator("Aqstpage::StartPage#startPage")
    assert page["enabled"] is True
    assert page["visible"] is True


def test_the_menu_bar_and_toolbars_are_present(libero_main):
    expect(libero_main.locator("QMenuBar")).to_exist()
    expect(libero_main.locator("QStatusBar")).to_exist()
    assert libero_main.locator("QMenu").count >= 15
    assert libero_main.locator("QToolBar").count >= 3


def test_tree_views_are_found_across_the_docks(libero_main):
    """Ten tree views live inside the dock widgets; scoping has to reach them."""
    assert libero_main.locator("QTreeView").count >= 5


def test_a_screenshot_of_a_real_application_window(libero_main, tmp_path):
    png = libero_main.screenshot(str(tmp_path / "libero.png"))
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 10_000
