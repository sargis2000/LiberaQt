"""The live-suite gate in tests/conftest.py, checked against the invocations that once got past it.

The gate is what stands between an ordinary command and a run of Libero SoC that writes projects
to disk and takes minutes. Its first version scanned the raw command line and shipped with five
bypasses, found only by a QA pass, because nothing tested it -- including ``--ignore tests/e2e/qt``,
an *exclusion*, opting the run into all of Libero.

Every case runs a real inner pytest session through ``pytester``, over a miniature tree that uses
the real ``tests/conftest.py`` read from disk, so what is tested is the gate that ships.
"""

import os
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

REAL_CONFTEST = Path(__file__).resolve().parents[1] / "conftest.py"

#: Enough of the real Libero modules' backstop to prove the gate records consent where it looks.
BACKSTOP = '''
import pytest
from pathlib import Path

@pytest.fixture(scope="module", autouse=True)
def _asked_for_by_path(request):
    consented = getattr(request.config, "_liberaqt_live_consent", frozenset())
    if Path(request.node.path).resolve() not in consented:
        pytest.skip("backstop")
'''


@pytest.fixture
def tree(pytester):
    """unit, e2e/qt and e2e/libero, each with one test, and the real gate over them."""
    pytester.makepyprojecttoml(
        '[tool.pytest.ini_options]\ntestpaths = ["tests/unit"]\nmarkers = [\n'
        '  "live: x", "qt_app: x", "libero: x", "writes_disk: x", "slow: x",\n]\n'
    )
    tests = pytester.path / "tests"
    for sub in ("unit", "e2e/qt", "e2e/libero"):
        (tests / sub).mkdir(parents=True)
    (tests / "conftest.py").write_text(REAL_CONFTEST.read_text(encoding="utf-8"), encoding="utf-8")
    (tests / "unit" / "test_u.py").write_text("def test_unit(): pass\n")
    (tests / "e2e" / "qt" / "test_q.py").write_text(
        "def test_qt(): pass\n\ndef test_qt_other(): pass\n")
    (tests / "e2e" / "libero" / "test_l.py").write_text(
        BACKSTOP + "\npytestmark = pytest.mark.writes_disk\n\ndef test_libero(): pass\n")
    return pytester


def _selected(pytester, *args, cwd=None):
    if cwd is not None:
        os.chdir(pytester.path / cwd)
    result = pytester.runpytest("--collect-only", "-q", "-p", "no:cacheprovider", *args)
    lines = result.stdout.lines
    return {name for name in ("test_unit", "test_qt", "test_qt_other", "test_libero")
            if any(line.endswith("::" + name) for line in lines)}, result


# ------------------------------------------------------------------ nothing live, however asked


@pytest.mark.parametrize("args", [
    ("tests/",),
    ("tests/", "--ignore", "tests/e2e/qt"),          # the headline: an exclusion used to opt in
    ("tests/", "--deselect", "tests/e2e/qt/test_q.py::test_qt"),
    ("--rootdir", "tests/e2e"),
    ("tests/", "-m", "not slow"),                    # a marker is never consent
    ("tests/", "-k", "libero"),
    (),                                              # testpaths is not consent
])
def test_a_run_that_did_not_name_the_live_tree_gets_nothing_live(tree, args):
    selected, _ = _selected(tree, *args)
    assert not selected & {"test_qt", "test_qt_other", "test_libero"}, selected


@pytest.mark.parametrize("cwd, args", [
    ("tests", ("-k", "e2e")),
    ("tests/e2e/libero", ("-m", "not slow")),        # an option value is not a path
    ("tests/e2e/libero", ("--tb", "short")),
    ("tests/e2e/libero", ()),                        # nor is the working directory
])
def test_standing_inside_the_live_tree_is_not_consent(tree, cwd, args):
    selected, _ = _selected(tree, *args, cwd=cwd)
    assert "test_libero" not in selected and "test_qt" not in selected, selected


# ------------------------------------------------------------------ exactly what was named


def test_naming_one_live_path_does_not_open_the_rest(tree):
    """`pytest tests/ tests/e2e/qt` asked for Qt, and used to get Libero too."""
    selected, _ = _selected(tree, "tests/", "tests/e2e/qt")
    assert selected == {"test_unit", "test_qt", "test_qt_other"}


def test_a_node_id_that_matches_nothing_grants_nothing(tree):
    selected, _ = _selected(tree, "tests/", "tests/e2e/qt/test_q.py::no_such_test")
    assert selected == {"test_unit"}


def test_a_node_id_does_not_cover_a_longer_name(tree):
    """`::test_qt` must not also select `test_qt_other`."""
    selected, _ = _selected(tree, "tests/", "tests/e2e/qt/test_q.py::test_qt")
    assert selected == {"test_unit", "test_qt"}


@pytest.mark.parametrize("args, expected", [
    (("tests/e2e",), {"test_qt", "test_qt_other", "test_libero"}),
    (("tests/e2e/qt",), {"test_qt", "test_qt_other"}),
    (("tests/e2e/libero",), {"test_libero"}),
    (("tests/e2e", "-m", "libero"), {"test_libero"}),
    (("tests/e2e", "-m", "libero and not writes_disk"), set()),
])
def test_what_is_named_is_what_runs(tree, args, expected):
    selected, _ = _selected(tree, *args)
    assert selected == expected


def test_dot_from_inside_the_live_tree_is_a_path(tree):
    selected, _ = _selected(tree, ".", cwd="tests/e2e/qt")
    assert selected == {"test_qt", "test_qt_other"}


def test_an_args_file_is_a_path(tree):
    """An @file is pytest's own way of listing paths; the first version refused it."""
    (tree.path / "live.txt").write_text("tests/e2e/qt\n")
    selected, _ = _selected(tree, "@live.txt")
    assert selected == {"test_qt", "test_qt_other"}


# ------------------------------------------------------------------ the backstop


def test_without_the_gate_the_libero_backstop_skips(tree):
    """--noconftest switches the gate off; the module's own backstop must then refuse."""
    result = tree.runpytest("tests/e2e/libero", "--noconftest", "-rs", "-p", "no:cacheprovider")
    result.assert_outcomes(skipped=1)
    result.stdout.fnmatch_lines(["*backstop*"])


def test_with_the_gate_a_consented_libero_module_runs(tree):
    tree.runpytest("tests/e2e/libero", "-p", "no:cacheprovider").assert_outcomes(passed=1)


# ------------------------------------------------------------------ pinning a Qt


def test_a_pin_with_no_qt_test_selected_is_refused(tree):
    _, result = _selected(tree, "tests/unit", f"--liberaqt-qt-bin={tree.path}")
    assert result.ret == pytest.ExitCode.USAGE_ERROR


def test_a_pin_naming_no_directory_is_refused(tree):
    """A Qt version not installed used to skip every Qt test and exit 0."""
    _, result = _selected(tree, "tests/e2e/qt", f"--liberaqt-qt-bin={tree.path / 'Qt/6.5.9'}")
    assert result.ret == pytest.ExitCode.USAGE_ERROR


def test_an_exported_pin_is_a_standing_default(tree, monkeypatch):
    """LIBERAQT_QT_BIN broke `pytest tests/ -m "not live"`, the README's own unit command."""
    monkeypatch.setenv("LIBERAQT_QT_BIN", str(tree.path))
    selected, result = _selected(tree, "tests/", "-m", "not live")
    assert result.ret == pytest.ExitCode.OK
    assert selected == {"test_unit"}


def test_the_hint_names_the_command_from_where_you_stand(tree):
    _, result = _selected(tree, cwd="tests/e2e/libero")
    result.stdout.fnmatch_lines(["*1 driving Libero SoC (1 write projects to disk): pytest .*"])
