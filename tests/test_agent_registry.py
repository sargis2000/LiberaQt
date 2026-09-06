"""Target inspection: telling "no agent for this ABI" apart from "cannot be instrumented".

The parsers take bytes rather than paths precisely so this runs with no Qt and no real binaries.
"""

from liberaqt.agent_registry import (
    _pe_imported_dlls,
    _pe_machine,
    _qt_build_stamp,
    current_platform_tag,
    inspect_binary,
    target_platform_tag,
)


def stamp(detail):
    """A Qt build string as it appears in a binary, NUL-terminated."""
    return b"\x00\x00Qt 6.7.2 (" + detail.encode() + b")\x00padding"


def test_static_build_string_is_recognised():
    """The exact stamp carried by Microchip's Libero installer."""
    assert _qt_build_stamp(
        stamp("x86_64-little_endian-llp64 static release build; by MSVC 2019")
    ) == ("6.7", "static", "msvc2019")


def test_shared_build_string_is_recognised():
    assert _qt_build_stamp(
        stamp("x86_64-little_endian-llp64 shared (dynamic) release build; by GCC 11.2.0")
    ) == ("6.7", "dynamic", "gcc")


def test_mingw_is_distinguished_from_plain_gcc():
    _, _, toolchain = _qt_build_stamp(stamp("shared (dynamic) release build; by MinGW 11.2.0"))
    assert toolchain == "mingw"


def test_qt5_build_string():
    assert _qt_build_stamp(stamp("x86 static build; by MSVC 2017").replace(b"6.7.2", b"5.15.2")) \
        == ("5.15", "static", "msvc2017")


def test_no_stamp_at_all():
    assert _qt_build_stamp(b"an ordinary file with no Qt in it") is None


def test_version_without_a_build_stamp_is_not_mistaken_for_one():
    """A bare version number in some unrelated string must not be read as a Qt build."""
    assert _qt_build_stamp(b"Qt 6.7.2 without the parenthesised detail") is None


def test_pe_parser_rejects_non_pe_input():
    assert _pe_imported_dlls(b"") == []
    assert _pe_imported_dlls(b"#!/bin/sh\necho hello\n") == []
    assert _pe_imported_dlls(b"MZ" + b"\x00" * 200) == []


def test_missing_file_is_reported_not_guessed():
    report = inspect_binary("no/such/binary.exe")
    assert not report.injectable
    assert report.reason == "file not found"
    assert report.qt_version is None


def test_a_non_qt_file_is_reported_as_such(tmp_path):
    target = tmp_path / "plain.exe"
    target.write_bytes(b"MZ" + b"\x00" * 4096)
    report = inspect_binary(str(target))
    assert not report.injectable
    assert report.linkage == "none"
    assert "may not be a Qt application" in report.reason


def test_a_static_qt_binary_is_never_injectable(tmp_path):
    """The whole point: this must not be reported as a mere ABI mismatch."""
    target = tmp_path / "installer.exe"
    target.write_bytes(b"MZ" + b"\x00" * 512 +
                       stamp("x86_64-little_endian-llp64 static release build; by MSVC 2019"))
    report = inspect_binary(str(target))
    assert report.linkage == "static"
    assert report.qt_version == "6.7"
    assert report.toolchain == "msvc2019"
    assert not report.injectable
    assert "statically" in report.reason


def pe_header(machine):
    """Minimal PE far enough for the machine field to be readable."""
    data = bytearray(b"MZ" + b"\x00" * 0x3E)
    data[0x3C:0x40] = (0x40).to_bytes(4, "little")
    data += b"PE\x00\x00" + machine.to_bytes(2, "little") + b"\x00" * 64
    return bytes(data)


def test_machine_field_distinguishes_32_and_64_bit():
    assert _pe_machine(pe_header(0x14C)) == "x86"
    assert _pe_machine(pe_header(0x8664)) == "x86_64"
    assert _pe_machine(pe_header(0xAA64)) == "aarch64"


def test_machine_field_of_a_non_pe():
    assert _pe_machine(b"not a binary") is None


def test_target_tag_follows_the_binary_not_the_host(tmp_path):
    """A 32-bit AUT on a 64-bit host needs a 32-bit agent -- Libero is exactly this case."""
    target = tmp_path / "libero.exe"
    target.write_bytes(pe_header(0x14C))
    assert target_platform_tag(str(target)).endswith("-x86")


def test_target_tag_falls_back_to_the_host_when_unreadable():
    assert target_platform_tag("no/such/file.exe") == current_platform_tag()


def test_arch_is_reported_for_a_32_bit_target(tmp_path):
    target = tmp_path / "app.exe"
    target.write_bytes(pe_header(0x14C) + b"\x00" * 512 +
                       stamp("i386-little_endian-ilp32 shared (dynamic) release build; "
                             "by MSVC 2019"))
    assert inspect_binary(str(target)).arch == "x86"
