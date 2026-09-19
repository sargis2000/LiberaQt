"""Serving the documentation locally.

The command has to work in two quite different situations: a source checkout with the ``docs``
extra installed, where mkdocs rebuilds a page as you edit it, and a checkout without it, where
the best that can be done is serving whatever was built last. Neither branch needs mkdocs present
to be tested, which is the point of splitting the decision out of the command.
"""

import sys
from pathlib import Path

import pytest

from liberaqt.cli import (
    build_parser,
    check_site_dir_is_disposable,
    docs_command,
    docs_root,
    docs_url,
)
from liberaqt.errors import LiberaQtError


@pytest.fixture
def project(tmp_path):
    """A directory that looks like a checkout: mkdocs.yml and a docs/ folder."""
    (tmp_path / "mkdocs.yml").write_text("site_name: Test\n")
    (tmp_path / "docs").mkdir()
    return tmp_path


def _no_mkdocs(monkeypatch):
    monkeypatch.setattr("liberaqt.cli.importlib.util.find_spec", lambda name: None)


def _with_mkdocs(monkeypatch):
    monkeypatch.setattr("liberaqt.cli.importlib.util.find_spec", lambda name: object())


# ------------------------------------------------------------------ finding the sources


def test_an_explicit_directory_is_used(project):
    assert docs_root(str(project)) == project


def test_an_explicit_directory_without_mkdocs_yml_is_refused(tmp_path):
    with pytest.raises(LiberaQtError, match="mkdocs.yml"):
        docs_root(str(tmp_path))


def test_the_working_directory_is_searched(project, monkeypatch):
    monkeypatch.chdir(project)
    assert docs_root() == project


def test_an_install_with_no_sources_anywhere_says_what_to_do(tmp_path, monkeypatch):
    """Neither a checkout nor a complete install: an explanation, not a stack trace."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("liberaqt.cli.Path.is_file", lambda self: False)
    with pytest.raises(LiberaQtError) as excinfo:
        docs_root()
    assert "--source" in str(excinfo.value), "the message has to name a way out"


# ------------------------------------------------------------------ choosing how to serve


def test_mkdocs_serves_with_live_reload_when_available(project, monkeypatch):
    _with_mkdocs(monkeypatch)
    argv, how = docs_command(project, "127.0.0.1", 8000)
    assert argv[:4] == [sys.executable, "-m", "mkdocs", "serve"]
    assert "127.0.0.1:8000" in argv
    assert "mkdocs" in how


def test_the_bind_address_and_port_are_passed_through(project, monkeypatch):
    _with_mkdocs(monkeypatch)
    argv, _ = docs_command(project, "0.0.0.0", 9001)
    assert "0.0.0.0:9001" in argv


def test_build_runs_strict(project, monkeypatch):
    """Strict turns a broken reference or an undocumented parameter into a failure."""
    _with_mkdocs(monkeypatch)
    argv, _ = docs_command(project, "127.0.0.1", 8000, build=True)
    assert argv[2:] == ["mkdocs", "build", "--strict"]


def test_without_mkdocs_a_built_site_is_still_served(project, monkeypatch):
    """Someone who only wants to read the documentation should not have to install a builder."""
    site = project / "site"
    site.mkdir()
    (site / "index.html").write_text("<h1>docs</h1>")
    _no_mkdocs(monkeypatch)

    argv, how = docs_command(project, "127.0.0.1", 8000)
    assert argv[:3] == [sys.executable, "-m", "http.server"]
    assert "--directory" in argv and str(site) in argv
    assert "no live reload" in how, "the weaker mode must say so"


def test_without_mkdocs_and_without_a_site_it_says_what_to_install(project, monkeypatch):
    _no_mkdocs(monkeypatch)
    with pytest.raises(LiberaQtError) as excinfo:
        docs_command(project, "127.0.0.1", 8000)
    assert "[docs]" in str(excinfo.value)


def test_building_without_mkdocs_is_refused_rather_than_faked(project, monkeypatch):
    """http.server can serve a site; it cannot build one."""
    site = project / "site"
    site.mkdir()
    (site / "index.html").write_text("<h1>docs</h1>")
    _no_mkdocs(monkeypatch)

    with pytest.raises(LiberaQtError, match="mkdocs"):
        docs_command(project, "127.0.0.1", 8000, build=True)


def test_an_empty_site_directory_is_not_mistaken_for_a_built_one(project, monkeypatch):
    (project / "site").mkdir()
    _no_mkdocs(monkeypatch)
    with pytest.raises(LiberaQtError):
        docs_command(project, "127.0.0.1", 8000)


# ------------------------------------------------------------------ the address it prints


def test_the_url_carries_the_site_url_path(project):
    """The site is mounted under site_url's path, locally as well as when published.

    Printing the bare root would send the reader one redirect off, and --open would land on it.
    """
    (project / "mkdocs.yml").write_text("site_name: Test\nsite_url: https://example.org/Thing/\n")
    assert docs_url(project, "127.0.0.1", 8000) == "http://127.0.0.1:8000/Thing/"


def test_a_site_url_without_a_path_serves_at_the_root(project):
    (project / "mkdocs.yml").write_text("site_name: Test\nsite_url: https://example.org/\n")
    assert docs_url(project, "127.0.0.1", 8000) == "http://127.0.0.1:8000/"


def test_no_site_url_serves_at_the_root(project):
    assert docs_url(project, "127.0.0.1", 8000) == "http://127.0.0.1:8000/"


def test_a_quoted_site_url_is_read_the_same(project):
    (project / "mkdocs.yml").write_text('site_name: Test\nsite_url: "https://example.org/Thing/"\n')
    assert docs_url(project, "127.0.0.1", 8000) == "http://127.0.0.1:8000/Thing/"


def test_the_static_fallback_serves_at_the_root(project):
    """http.server serves site/ directly, with no prefix, whatever site_url says."""
    (project / "mkdocs.yml").write_text("site_name: Test\nsite_url: https://example.org/Thing/\n")
    assert docs_url(project, "127.0.0.1", 8000, with_prefix=False) == "http://127.0.0.1:8000/"


def test_an_unreadable_mkdocs_yml_does_not_break_the_url(tmp_path):
    assert docs_url(tmp_path, "127.0.0.1", 8000) == "http://127.0.0.1:8000/"


# ------------------------------------------------------------------ the copy inside the wheel


def test_the_copy_inside_the_wheel_is_found(tmp_path, monkeypatch):
    """A pip install never sees a checkout, so the packaged copy is the only candidate left."""
    packaged = tmp_path / "packaged"
    (packaged / "docs").mkdir(parents=True)
    (packaged / "mkdocs.yml").write_text("site_name: Packaged")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("liberaqt.cli.CHECKOUT_DOCS", tmp_path / "nowhere")
    monkeypatch.setattr("liberaqt.cli.PACKAGED_DOCS", packaged)

    assert docs_root() == packaged


def test_a_checkout_wins_over_the_packaged_copy(tmp_path, monkeypatch):
    """Editing docs/ and serving them has to stay one step.

    Deliberately run from a neutral directory. Standing *inside* the fake checkout would let
    ``Path.cwd()`` answer first, and the test would pass whatever order the two constants are
    searched in -- which is no test of precedence at all.
    """
    checkout = tmp_path / "checkout"
    (checkout / "docs").mkdir(parents=True)
    (checkout / "mkdocs.yml").write_text("site_name: Checkout")
    packaged = tmp_path / "packaged"
    (packaged / "docs").mkdir(parents=True)
    (packaged / "mkdocs.yml").write_text("site_name: Packaged")

    neutral = tmp_path / "elsewhere"
    neutral.mkdir()
    monkeypatch.chdir(neutral)
    monkeypatch.setattr("liberaqt.cli.CHECKOUT_DOCS", checkout)
    monkeypatch.setattr("liberaqt.cli.PACKAGED_DOCS", packaged)

    assert docs_root() == checkout


# ------------------------------------------------------------------ where the build lands


def test_build_writes_where_it_is_told(project, monkeypatch, tmp_path):
    _with_mkdocs(monkeypatch)
    out = tmp_path / "out"
    argv, _ = docs_command(project, "127.0.0.1", 8000, build=True, site_dir=out)
    assert argv[2:] == ["mkdocs", "build", "--strict", "--site-dir", str(out)]


def test_serving_ignores_the_site_dir(project, monkeypatch, tmp_path):
    """Mkdocs serve builds into a temp directory of its own; --site-dir would be a lie."""
    _with_mkdocs(monkeypatch)
    argv, _ = docs_command(project, "127.0.0.1", 8000, site_dir=tmp_path / "out")
    assert "--site-dir" not in argv


def test_the_static_fallback_prefers_the_site_that_was_built(project, monkeypatch, tmp_path):
    """From a wheel, root/site is inside site-packages and can never exist."""
    built = tmp_path / "built"
    built.mkdir()
    (built / "index.html").write_text("<h1>docs</h1>")
    _no_mkdocs(monkeypatch)

    argv, _ = docs_command(project, "127.0.0.1", 8000, site_dir=built)
    assert str(built) in argv


def test_the_hint_for_a_packaged_install_is_a_command_that_can_work(monkeypatch):
    """Nothing is published on PyPI, so `pip install liberaqt[docs]` would resolve to nothing."""
    _no_mkdocs(monkeypatch)
    with pytest.raises(LiberaQtError) as excinfo:
        docs_command(Path("/does/not/matter"), "127.0.0.1", 8000, build=True)
    assert 'pip install -e ".[docs]"' in str(excinfo.value)


def test_the_hint_from_the_wheel_names_the_repository(monkeypatch):
    from liberaqt.cli import PACKAGED_DOCS

    _no_mkdocs(monkeypatch)
    with pytest.raises(LiberaQtError) as excinfo:
        docs_command(PACKAGED_DOCS, "127.0.0.1", 8000, build=True)
    assert "git+https://github.com/sargis2000/LiberaQt.git" in str(excinfo.value)


# ------------------------------------------------------------------ the argument shape


def _docs_args(*argv):
    return build_parser().parse_args(["docs", *argv])


def test_docs_build_is_a_verb():
    assert _docs_args("build").action == "build"


def test_docs_serve_is_a_verb():
    assert _docs_args("serve").action == "serve"


def test_bare_docs_still_serves():
    assert _docs_args().action == "serve"


def test_the_build_flag_still_works():
    """The older spelling stays, because it is in the published CLI guide."""
    assert _docs_args("--build").build is True


def test_an_unknown_verb_is_rejected_with_the_choices(capsys):
    with pytest.raises(SystemExit):
        _docs_args("buld")
    assert "choose from" in capsys.readouterr().err


# ------------------------------------------------------------------ not erasing the user's work


def test_a_directory_of_the_users_is_not_erased(tmp_path):
    """Mkdocs cleans its destination, so a `site/` holding anything else is a refusal."""
    site = tmp_path / "site"
    site.mkdir()
    (site / "NOTES.txt").write_text("my precious notes")

    with pytest.raises(LiberaQtError) as excinfo:
        check_site_dir_is_disposable(site, explicit=False)
    assert str(site) in str(excinfo.value)
    assert "--site-dir" in str(excinfo.value)


def test_a_previous_build_is_overwritten_without_complaint(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<h1>old</h1>")
    (site / "404.html").write_text("<h1>gone</h1>")

    check_site_dir_is_disposable(site, explicit=False)


def test_naming_the_directory_is_consent(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "NOTES.txt").write_text("my precious notes")

    check_site_dir_is_disposable(site, explicit=True)


def test_an_empty_or_absent_directory_is_fine(tmp_path):
    check_site_dir_is_disposable(tmp_path / "nothing-here", explicit=False)
    (tmp_path / "empty").mkdir()
    check_site_dir_is_disposable(tmp_path / "empty", explicit=False)
