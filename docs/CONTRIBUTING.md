# Contributing

## Layout

```
liberaqt/
├── src/liberaqt/        Python client (pure Python, no Qt dependency)
├── agent/               C++ Qt agent (CMake)
├── integration/         Tests driving Qt's own shipped applications
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
pytest integration/      # drives Qt Designer/Assistant/Linguist; needs a built agent
```

The integration suite deliberately uses no purpose-built sample application. It drives Qt's own
programs, which are large, were written with no knowledge of this project, and ship inside the Qt
installation -- so they are guaranteed to match the agent's Qt version and compiler ABI. Point it
at a Qt installation with `--liberaqt-qt-bin=<dir>`, `LIBERAQT_QT_BIN` or `QTDIR`; otherwise it
looks beside `qmake` on `PATH`. Each fixture skips when its application is missing.

## Rules of thumb

* Any logic that can live in Python lives in Python. The C++ agent stays small and boring: it is
  the hardest thing to debug, the slowest to build, and the part that can crash the user's app.
* Never let an agent handler throw across the Qt event loop boundary. Catch, convert to a protocol
  error, return.
* Every new command needs: a `PROTOCOL.md` entry, a dispatcher handler, a client method, a test
  against the sample app, and an entry in the conformance suite.
* Guard Qt 5/6 differences in `compat.h`, never inline `#if QT_VERSION` in feature code.
