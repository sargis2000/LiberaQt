# Contributing

## Layout

```
liberaqt/
├── src/liberaqt/        Python client (pure Python, no Qt dependency)
├── agent/               C++ Qt agent (CMake)
├── examples/            Sample widget + QML app and example tests
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
pytest tests/            # unit tests, no Qt required
pytest examples/tests/   # integration, needs a built agent + sample app
```

## Rules of thumb

* Any logic that can live in Python lives in Python. The C++ agent stays small and boring: it is
  the hardest thing to debug, the slowest to build, and the part that can crash the user's app.
* Never let an agent handler throw across the Qt event loop boundary. Catch, convert to a protocol
  error, return.
* Every new command needs: a `PROTOCOL.md` entry, a dispatcher handler, a client method, a test
  against the sample app, and an entry in the conformance suite.
* Guard Qt 5/6 differences in `compat.h`, never inline `#if QT_VERSION` in feature code.
