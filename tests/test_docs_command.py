"""Serving the documentation locally.

The command has to work in two quite different situations: a source checkout with the ``docs``
extra installed, where mkdocs rebuilds a page as you edit it, and a checkout without it, where
the best that can be done is serving whatever was built last. Neither branch needs mkdocs present
to be tested, which is the point of splitting the decision out of the command.
"""

import sys

import pytest

from liberaqt.cli import docs_command, docs_root, docs_url
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


def test_a_wheel_install_says_why_it_cannot_find_them(tmp_path, monkeypatch):
    """The sources are not packaged, so this has to be an explanation rather than a stack trace."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("liberaqt.cli.Path.is_file", lambda self: False)
    with pytest.raises(LiberaQtError) as excinfo:
        docs_root()
    assert "wheel" in str(excinfo.value).lower()


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
