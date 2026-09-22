"""Guards on the shape of the test tree itself.

These are cheap, and they protect a failure mode that is genuinely hard to read when it happens.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]


def test_no_two_test_modules_share_a_basename():
    """Two same-named test files in a tree without __init__.py is a collection error.

    pytest imports them under the same module name and refuses, and it blames whichever file it
    reached first -- so a duplicate added under tests/e2e reports as an error in an innocent
    tests/unit module. The convention is unique basenames across the whole tree; this asserts it
    rather than trusting it, because the error it prevents does not point at the real cause.
    """
    by_name = defaultdict(list)
    for path in TESTS.rglob("test_*.py"):
        if "__pycache__" in path.parts:
            continue
        by_name[path.name].append(path.relative_to(TESTS).as_posix())

    clashes = {name: paths for name, paths in by_name.items() if len(paths) > 1}
    assert not clashes, f"test modules share a basename: {clashes}"


#: The only directories a test module may live in. Each is what tests/conftest.py knows how to
#: mark, and what CI and the live-suite gate know how to treat.
CATEGORISED = ("unit", "e2e/qt", "e2e/libero")


def test_every_test_lives_in_a_categorised_directory():
    """tests/conftest.py applies live/qt_app/libero from the directory, and CI runs tests/unit.

    A module anywhere else falls through the gaps, each a different way. Straight in tests/e2e/,
    it is `live` but neither `qt_app` nor `libero`, so `-m libero` quietly misses it; in a new
    tests/e2e/designer/, the same. In tests/ or a new tests/integration/, it has no markers at all
    and CI -- which runs tests/unit -- never runs it. This checked only the first of those.
    """
    stray = sorted(
        path.relative_to(TESTS).as_posix()
        for path in TESTS.rglob("test_*.py")
        if "__pycache__" not in path.parts
        and path.parent.relative_to(TESTS).as_posix() not in CATEGORISED
    )
    assert not stray, (
        f"test modules must live in one of tests/{{{', '.join(CATEGORISED)}}}, found: {stray}"
    )


def test_every_libero_module_carries_the_consent_backstop():
    """The gate lives in a conftest, and `--noconftest` switches every conftest off.

    Each Libero module therefore refuses to start Libero on its own unless the gate recorded
    consent for it -- module-scoped and autouse, so it runs before any fixture that launches the
    application. A new Libero module without it would be the one door `--noconftest` opens.
    """
    missing = [
        path.name
        for path in sorted((TESTS / "e2e" / "libero").glob("test_*.py"))
        if '@pytest.fixture(scope="module", autouse=True)\ndef _asked_for_by_path(request):'
        not in path.read_text(encoding="utf-8")
    ]
    assert not missing, f"Libero modules without the consent backstop: {missing}"
