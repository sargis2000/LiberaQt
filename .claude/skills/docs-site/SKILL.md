---
name: docs-site
description: How LiberaQT's documentation is built, served and shipped - the liberaqt docs command (serve, build, the static fallback), docs/ and mkdocs.yml force-included into the wheel at liberaqt/_docs, why a build must pass an absolute --site-dir, the .liberaqt-docs-build stamp that decides what a build may erase, what counts as a checkout, the install hint for the docs extra, the CI packaging job, and the docs pages that run as tests. Use before editing the docs functions in src/liberaqt/cli.py, the force-include or docs extra in pyproject.toml, or mkdocs.yml; before changing where or how the site is built; when editing docs/getting-started/first-test.md or docstrings that mkdocstrings renders; and whenever a docs build writes somewhere unexpected, deletes files, serves the wrong documentation, or the wheel is missing its docs.
---

# The documentation site

`docs/` is two things at once: the design documents CLAUDE.md maps, and the mkdocs source of the
published site (`index.md`, `getting-started/`, `guide/`, `reference/`). Both ship inside the
wheel. The code that serves and builds the site lives in `src/liberaqt/cli.py` (`docs_root`,
`is_checkout`, `check_site_dir_is_disposable`, `docs_command`, `docs_url`, `cmd_docs`) and is
pinned by `tests/unit/test_docs_command.py`.

## The command

`liberaqt docs` serves the site locally and `liberaqt docs build` builds it (`--build` is the older
spelling, kept working). It uses mkdocs when the `docs` extra is installed, or else serves the last
built site over `http.server`. It prints the real address, which is under the `site_url` path
rather than the root, and which copy of the sources it is using.

`site/` is a local build and is gitignored. So the fallback to "the last built site" exists only on
a machine that has built one; on a fresh clone the command needs `pip install -e ".[docs]"`.

## The docs ship inside the wheel

`force-include` in `pyproject.toml` puts `docs/` and `mkdocs.yml` at `liberaqt/_docs/`, and
`docs_root()` looks there after the working directory and the checkout. That is what makes
`liberaqt docs` work from a plain `pip install`, not only from a checkout. It is also why anything
written into `docs/` ships to users. Each consequence below cost a real bug to find:

- **`build` must pass an absolute `--site-dir`.** mkdocs resolves both `site_dir:` and a
  *relative* `--site-dir` against the config file. From a wheel, the config file is inside
  site-packages, so an unguarded build wrote the whole site in there, with exit 0 and no warning.
- **mkdocs erases its destination, so ownership decides what `build` may write into, never file
  names.** Every build writes a hidden stamp, `SITE_STAMP` (`.liberaqt-docs-build`). mkdocs' clean
  step keeps it, because it skips dotfiles. A destination is disposable only if it has no visible
  entries or carries the stamp (`check_site_dir_is_disposable`). The first version trusted any
  folder holding a `404.html` or a `sitemap.xml`, erased everything else in it, and treated naming
  `--site-dir` as consent. So `--site-dir .` in a project deleted every visible file in it, exit 0.
  - `--force` is the only consent, and nothing overrides the refusal for a filesystem root, the
    home directory, or an ancestor of the working directory.
  - A link is judged, and named in the message, by its target. Through a junction, the erased
    files used to live outside the working tree.
  - A checkout's own `site/` is trusted unstamped, so builds from before the stamp still work.
- **Only a real checkout counts as one.** `is_checkout()` wants `mkdocs.yml` *and* `src/liberaqt/`,
  searched from the working directory upwards. Any `mkdocs.yml` used to do, so a user's own docs
  were served and built under LiberaQT's name. And from a checkout's `docs/` folder the checkout was
  missed: a site was built into the documentation sources, and `force-include` shipped it in the
  next wheel (+1.2 MB, 71 entries). The static fallback serves only a stamped site, for the same
  reason.
- **The install hint is built from `sys.executable` and the extra's own requirements**, read from
  the installed metadata (`_docs_requirements`, `_docs_extra_hint`). Asking pip for
  `liberaqt[docs]` re-cloned the repository, which is fatal offline and replaced a pinned version
  with `main`. And `pip install -e ".[docs]"` from anywhere but a checkout's root installed the
  user's own project. `docs_command` checks every module of the extra (`_missing_docs_modules`),
  not just mkdocs.
- **`pip install -e` copies `liberaqt/_docs` into site-packages too**, where nothing reads it,
  because the editable package resolves to `src/liberaqt`. It is inert but stale, and hatchling
  offers no pyproject-level way to skip force-include for editable builds.

Nothing in `tests/unit` can notice the docs falling out of the wheel, because those tests never
build one. So CI has a `packaging` job that builds a wheel and asserts that
`liberaqt/_docs/mkdocs.yml` and at least one page are in it. After touching `force-include`, run
the same check locally:

```bash
python -m build --wheel
python -c "import zipfile,glob; z=zipfile.ZipFile(sorted(glob.glob('dist/*.whl'))[-1]); print([n for n in z.namelist() if '_docs/' in n][:5])"
```

## Pages that are code

- `tests/e2e/qt/test_docs_examples.py` runs the code in `docs/getting-started/first-test.md` as a
  test against Qt Assistant, so editing that page can break a suite. That is deliberate: it is how
  the tutorial is kept from rotting. Run it after editing the page.
- `mkdocstrings` renders `docs/reference/api.md` from the docstrings in `src/`. That is the other
  reason the Google-docstring rules in CLAUDE.md's Coding style are enforced rather than advisory:
  an indented sample without an `Example:` header is corrupted on the published page.
- `python -m mkdocs build --strict` turns a broken link or a missing page into a failure. Run it
  after any change under `docs/`.
