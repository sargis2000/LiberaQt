"""Serving the documentation locally.

The command has to work in two quite different situations: a source checkout with the ``docs``
extra installed, where mkdocs rebuilds a page as you edit it, and a checkout without it, where
the best that can be done is serving whatever was built last. Neither branch needs mkdocs present
to be tested, which is the point of splitting the decision out of the command.
"""

import sys
from pathlib import Path

import pytest

from liberaqt import cli
from liberaqt.cli import (
    SITE_STAMP,
    build_parser,
    check_site_dir_is_disposable,
    docs_command,
    docs_root,
    docs_url,
)
from liberaqt.errors import LiberaQtError


def _make_checkout(path):
    """What makes a directory a LiberaQT checkout -- mkdocs.yml alone is any mkdocs project."""
    (path / "docs").mkdir(parents=True, exist_ok=True)
    (path / "mkdocs.yml").write_text("site_name: Test\n")
    (path / "src" / "liberaqt").mkdir(parents=True, exist_ok=True)
    (path / "src" / "liberaqt" / "__init__.py").write_text("")
    return path


@pytest.fixture
def project(tmp_path):
    """A directory that looks like a LiberaQT checkout."""
    return _make_checkout(tmp_path)


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
    checkout = _make_checkout(tmp_path / "checkout")
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
    (built / SITE_STAMP).write_text("")
    _no_mkdocs(monkeypatch)

    argv, _ = docs_command(project, "127.0.0.1", 8000, site_dir=built)
    assert str(built) in argv


def test_someone_elses_site_is_not_served_as_liberaqts(project, monkeypatch, tmp_path):
    """Any ./site/index.html used to be served, and labelled LiberaQT's documentation."""
    foreign = tmp_path / "theirs"
    foreign.mkdir()
    (foreign / "index.html").write_text("<title>SOMEONE ELSES SITE</title>")
    _no_mkdocs(monkeypatch)
    with pytest.raises(LiberaQtError):
        docs_command(tmp_path / "packaged-root", "127.0.0.1", 8000, site_dir=foreign)


# ------------------------------------------------------------------ the install hint


def _missing_hint(monkeypatch):
    _no_mkdocs(monkeypatch)
    with pytest.raises(LiberaQtError) as excinfo:
        docs_command(Path("/does/not/matter"), "127.0.0.1", 8000, build=True)
    return str(excinfo.value)


def test_the_hint_runs_the_interpreter_that_is_running(monkeypatch):
    """A bare `pip` could belong to another Python entirely; this one cannot."""
    assert f"{sys.executable} -m pip install" in _missing_hint(monkeypatch).replace('"', "")


def test_the_hint_installs_the_extras_packages_not_liberaqt(monkeypatch):
    """The hint asks for the extra's own packages, never for liberaqt itself.

    Asking pip for liberaqt[docs] re-cloned the repository: impossible offline, and it replaced
    a pinned version with main.
    """
    hint = _missing_hint(monkeypatch)
    assert "mkdocs-material" in hint and "mkdocstrings" in hint
    assert "git+" not in hint
    assert "liberaqt[docs]" not in hint
    assert '-e ".[docs]"' not in hint, "installs the user's own project from anywhere else"


def test_a_partial_install_names_what_is_missing(monkeypatch):
    """Plain mkdocs installed for another project reached a raw "Unrecognised theme" error."""
    monkeypatch.setattr("liberaqt.cli.importlib.util.find_spec",
                        lambda name: object() if name == "mkdocs" else None)
    with pytest.raises(LiberaQtError) as excinfo:
        docs_command(Path("/x"), "127.0.0.1", 8000, build=True)
    assert "mkdocs-material" in str(excinfo.value)
    assert "needs mkdocs," not in str(excinfo.value), "mkdocs itself is present"


# ------------------------------------------------------------------ the argument shape


def _docs_args(*argv):
    return build_parser().parse_args(["docs", *argv])


def test_docs_build_is_a_verb():
    assert _docs_args("build").action == "build"


def test_docs_serve_is_a_verb():
    assert _docs_args("serve").action == "serve"


def test_bare_docs_still_serves():
    """No verb means serve; the default is left unset so an explicit `serve` can be told apart."""
    args = _docs_args()
    assert args.action is None and not args.build


def test_serve_and_build_together_are_refused(capsys):
    """`serve --build` used to build and exit 0, silently ignoring the explicit serve."""
    assert cli.main(["docs", "serve", "--build"]) == 2
    assert "opposite" in capsys.readouterr().out


def test_the_build_flag_still_works():
    """The older spelling stays, because it is in the published CLI guide."""
    assert _docs_args("--build").build is True


def test_an_unknown_verb_is_rejected_with_the_choices(capsys):
    with pytest.raises(SystemExit):
        _docs_args("buld")
    assert "choose from" in capsys.readouterr().err


# ------------------------------------------------------------------ not erasing the user's work


@pytest.fixture
def elsewhere(tmp_path, monkeypatch):
    """Stand somewhere harmless, so the refusals for the working directory do not fire."""
    here = tmp_path / "cwd"
    here.mkdir()
    monkeypatch.chdir(here)
    return tmp_path


def test_a_directory_of_the_users_is_not_erased(elsewhere):
    """Mkdocs cleans its destination, so a `site/` holding anything else is a refusal."""
    site = elsewhere / "site"
    site.mkdir()
    (site / "NOTES.txt").write_text("my precious notes")

    with pytest.raises(LiberaQtError) as excinfo:
        check_site_dir_is_disposable(site)
    assert str(site) in str(excinfo.value)
    assert "--site-dir" in str(excinfo.value)


def test_a_stray_404_does_not_make_a_directory_disposable(elsewhere):
    """Any folder holding a 404.html passed, and mkdocs erased everything else in it."""
    site = elsewhere / "site"
    (site / "photos").mkdir(parents=True)
    (site / "404.html").write_text("x")
    (site / "notes.txt").write_text("precious")
    (site / "photos" / "precious1.txt").write_text("precious")
    with pytest.raises(LiberaQtError):
        check_site_dir_is_disposable(site)


def test_a_real_old_build_with_the_users_notes_in_it_is_refused(elsewhere):
    site = elsewhere / "site"
    site.mkdir()
    for name in ("index.html", "404.html", "sitemap.xml", "my-notes.txt"):
        (site / name).write_text("x")
    with pytest.raises(LiberaQtError):
        check_site_dir_is_disposable(site)


def test_a_site_this_command_built_is_replaced_without_complaint(elsewhere):
    site = elsewhere / "site"
    site.mkdir()
    (site / "index.html").write_text("<h1>old</h1>")
    (site / SITE_STAMP).write_text("")
    check_site_dir_is_disposable(site)


def test_naming_the_directory_is_not_consent(elsewhere):
    """`--site-dir .` in a project erased every visible file in it and exited 0."""
    project_dir = elsewhere / "cwd"
    (project_dir / "README.md").write_text("mine")
    (project_dir / "src").mkdir()
    with pytest.raises(LiberaQtError):
        check_site_dir_is_disposable(project_dir)


def test_force_is_consent(elsewhere):
    site = elsewhere / "site"
    site.mkdir()
    (site / "NOTES.txt").write_text("disposable after all")
    check_site_dir_is_disposable(site, force=True)


@pytest.mark.parametrize("where", ["home", "root", "parent"])
def test_some_directories_are_never_built_into_even_with_force(elsewhere, where):
    target = {
        "home": Path.home(),
        "root": Path(Path.cwd().anchor),
        "parent": Path.cwd().parent,
    }[where]
    with pytest.raises(LiberaQtError, match="refusing"):
        check_site_dir_is_disposable(target, force=True)


def test_a_file_where_the_site_would_go_is_named(elsewhere):
    (elsewhere / "site").write_text("not a directory")
    with pytest.raises(LiberaQtError, match="is a file"):
        check_site_dir_is_disposable(elsewhere / "site")


def test_hidden_files_alone_do_not_block_a_build(elsewhere):
    """Mkdocs keeps dotfiles when it cleans, so a lone .keep is not at risk."""
    site = elsewhere / "site"
    site.mkdir()
    (site / ".keep").write_text("")
    check_site_dir_is_disposable(site)


def test_a_link_is_judged_by_what_it_points_at(elsewhere):
    """Through a junction, the files erased used to live outside the working tree entirely."""
    real = elsewhere / "somewhere-else"
    real.mkdir()
    (real / "404.html").write_text("x")
    (real / "precious-b.txt").write_text("precious")
    link = elsewhere / "cwd" / "site"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("creating a symlink needs Developer Mode or admin rights here")
    with pytest.raises(LiberaQtError) as excinfo:
        check_site_dir_is_disposable(link)
    assert str(real.resolve()) in str(excinfo.value), "the message must name the real target"


def test_an_empty_or_absent_directory_is_fine(elsewhere):
    check_site_dir_is_disposable(elsewhere / "nothing-here")
    (elsewhere / "empty").mkdir()
    check_site_dir_is_disposable(elsewhere / "empty")


# ------------------------------------------------------------------ whose documentation


def test_someone_elses_mkdocs_project_is_not_taken_for_the_checkout(tmp_path, monkeypatch):
    """A user's own mkdocs.yml was served, and built, as LiberaQT's documentation."""
    theirs = tmp_path / "theirs"
    theirs.mkdir()
    (theirs / "mkdocs.yml").write_text("site_name: My Own Product\n")
    packaged = tmp_path / "packaged"
    packaged.mkdir()
    (packaged / "mkdocs.yml").write_text("site_name: LiberaQT\n")
    monkeypatch.chdir(theirs)
    monkeypatch.setattr("liberaqt.cli.CHECKOUT_DOCS", tmp_path / "nowhere")
    monkeypatch.setattr("liberaqt.cli.PACKAGED_DOCS", packaged)
    assert docs_root() == packaged


def test_the_checkout_is_found_from_inside_it(tmp_path, monkeypatch):
    """From docs/ the checkout was missed, and a site was built into the documentation sources."""
    checkout = _make_checkout(tmp_path / "checkout")
    monkeypatch.chdir(checkout / "docs")
    monkeypatch.setattr("liberaqt.cli.CHECKOUT_DOCS", tmp_path / "nowhere")
    assert docs_root() == checkout.resolve()


def test_an_explicit_source_that_is_a_file_says_so(project):
    with pytest.raises(LiberaQtError, match="is a file"):
        docs_root(str(project / "mkdocs.yml"))


def test_an_explicit_source_that_does_not_exist_says_so(tmp_path):
    with pytest.raises(LiberaQtError, match="does not exist"):
        docs_root(str(tmp_path / "missing"))


# ------------------------------------------------------------------ addresses


@pytest.mark.parametrize("host, shown", [
    ("0.0.0.0", "localhost"),
    ("::", "localhost"),
    ("::1", "[::1]"),
    ("127.0.0.1", "127.0.0.1"),
])
def test_the_printed_address_can_be_opened(project, host, shown):
    """`http://::1:9002/` is not a URL, and 0.0.0.0 is a bind address, not a destination."""
    assert docs_url(project, host, 9002, with_prefix=False) == f"http://{shown}:9002/"


# ------------------------------------------------------------------ the stamp


def test_a_successful_build_stamps_its_site(project, monkeypatch, tmp_path):
    """The stamp is what makes the next build, and the fallback server, trust the directory."""
    _with_mkdocs(monkeypatch)
    out = tmp_path / "out"
    monkeypatch.setattr("liberaqt.cli.subprocess.call", lambda *a, **k: out.mkdir() or 0)
    assert cli.main(["docs", "build", "--source", str(project), "--site-dir", str(out)]) == 0
    assert (out / SITE_STAMP).is_file()


def test_a_failed_build_is_not_stamped(project, monkeypatch, tmp_path):
    _with_mkdocs(monkeypatch)
    out = tmp_path / "out"
    monkeypatch.setattr("liberaqt.cli.subprocess.call", lambda *a, **k: out.mkdir() or 1)
    assert cli.main(["docs", "build", "--source", str(project), "--site-dir", str(out)]) == 1
    assert not (out / SITE_STAMP).exists()
