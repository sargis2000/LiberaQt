# Contributing

## Layout

```
liberaqt/
├── src/liberaqt/        Python client (pure Python, no Qt dependency)
├── agent/               C++ Qt agent (CMake)
├── docs/                Design documents (this folder)
└── tests/               Unit tests for the client (no Qt needed)
```

## Building the agent

```bash
cmake -S agent -B build/agent -DCMAKE_PREFIX_PATH=$QTDIR -DCMAKE_BUILD_TYPE=Release
cmake --build build/agent --parallel
cmake --install build/agent --prefix ~/.cache/liberaqt/agents/qt6.7-linux-x86_64-gcc
```

The install layout must be `<prefix>/plugins/generic/libliberaqt.so` — that is what
`QT_PLUGIN_PATH` expects.

## Running the client tests

```bash
pip install -e ".[dev]"
pytest tests/            # unit tests of the client, no Qt required
```

That is what CI runs, and it exercises **no** agent code. Nothing there starts a Qt application.

Agent changes are covered by `e2e/`, which drives real programs and is *not* run by CI:

```bash
pytest e2e/test_actions_live.py e2e/test_selectors_live.py   # Qt Assistant
pytest e2e/test_libero_selectors.py                          # Libero SoC, read-only
pytest e2e/test_libero_synthesis.py                          # Libero SoC, writes a project
```

Each skips when its application is absent. `pytest` alone collects `tests/` only, because
pyproject pins `testpaths`.

There is deliberately no purpose-built sample application: a toy agrees with whatever the driver
happens to do, and three bugs that made LiberaQT unusable on real software once sat undetected
behind a green sample suite. Prefer adding to `e2e/` over inventing a fixture app.

An older suite covering Designer, Linguist, qdbusviewer and qmleasing lived in `integration/`
until 2026-08-17; take what is useful from `git show b51cfa0:integration/...` rather than
rewriting it.

## Rules of thumb

* Any logic that can live in Python lives in Python. The C++ agent stays small and boring: it is
  the hardest thing to debug, the slowest to build, and the part that can crash the user's app.
* Never let an agent handler throw across the Qt event loop boundary. Catch, convert to a protocol
  error, return.
* Every new command needs: a `PROTOCOL.md` entry, a dispatcher handler, a client method, and a
  test in `e2e/` that drives it against a real application. There is no sample app and no
  conformance suite; earlier revisions of this file asked for both.
* Guard Qt 5/6 differences in `compat.h`, never inline `#if QT_VERSION` in feature code.

## Documentation

The site is MkDocs Material, built from `docs/` and configured in `mkdocs.yml`.

```bash
pip install -e ".[docs]"
mkdocs serve          # http://127.0.0.1:8000, live reload
mkdocs build --strict # fails on a broken reference or an undocumented parameter
mkdocs gh-deploy      # publish to GitHub Pages
```

`--strict` is the one that matters: it turns griffe's warnings about missing annotations into
build failures, so the API reference cannot quietly rot.

**The tutorial's examples are executed.** `e2e/test_docs_examples.py` runs the getting-started
code against a real Qt Assistant. Change an example, run that suite. It exists because the first
draft of those pages contained two selectors that never existed, a menu entry with the wrong
capitalisation, and an import path that does not work -- all of which read perfectly well.
