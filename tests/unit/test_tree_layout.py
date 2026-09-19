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


def test_every_live_test_lives_under_a_categorised_directory():
    """tests/conftest.py applies live/qt_app/libero from the directory.

    A live test dropped straight into tests/e2e/ would get `live` but neither application marker,
    so `-m libero` and `-m qt_app` would quietly miss it.
    """
    stray = [
        path.relative_to(TESTS).as_posix()
        for path in (TESTS / "e2e").glob("test_*.py")
    ]
    assert not stray, f"live tests must go in tests/e2e/qt or tests/e2e/libero, found: {stray}"
