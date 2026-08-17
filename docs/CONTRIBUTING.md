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
cmake --install build/agent --prefix ~/.cache/liberaqt/agents/qt6.7-linux-gcc
```

The install layout must be `<prefix>/plugins/generic/libliberaqt.so` — that is what
`QT_PLUGIN_PATH` expects.

## Running the client tests

```bash
pip install -e ".[dev]"
pytest tests/            # unit tests of the client, no Qt required
```

That is the entire automated suite, and it exercises **no** agent code. Nothing here starts a Qt
application, so an agent change is unverified until you drive a real program by hand — see
"Testing" in the README. There is deliberately no purpose-built sample application to test
against: a toy agrees with whatever the driver happens to do, and three bugs that made LiberaQT
unusable on real software once sat undetected behind a green sample suite.

A suite that drove Qt's own shipped programs (Designer, Assistant, Linguist, qdbusviewer,
qmleasing) and Microchip Libero SoC lived in `integration/` until 2026-08-17. If live-application
coverage is wanted again, restore it rather than starting over: `git checkout b51cfa0 --
integration/`.

## Rules of thumb

* Any logic that can live in Python lives in Python. The C++ agent stays small and boring: it is
  the hardest thing to debug, the slowest to build, and the part that can crash the user's app.
* Never let an agent handler throw across the Qt event loop boundary. Catch, convert to a protocol
  error, return.
* Every new command needs: a `PROTOCOL.md` entry, a dispatcher handler, a client method, a test
  against the sample app, and an entry in the conformance suite.
* Guard Qt 5/6 differences in `compat.h`, never inline `#if QT_VERSION` in feature code.
