# How it works

Five ideas explain almost everything LiberaQT does, and knowing them turns most surprises into
expected behaviour.

## 1. The agent runs inside your application

```
pytest / script (Python) ──JSON over TCP──► liberaqt agent (C++ Qt plugin) ──► QObject tree
```

The agent is a Qt **generic plugin**. When `QGuiApplication` is constructed, Qt instantiates every
plugin named in `QT_QPA_GENERIC_PLUGINS`, and the agent is one of them. The launcher sets a
handful of environment variables on a copy of the environment:

```
QT_PLUGIN_PATH=<agent prefix>/plugins       # plus every other installed agent's plugins dir
QT_QPA_GENERIC_PLUGINS=<one key per installed agent>,liberaqt   # e.g. liberaqt_5_15_32_msvc,liberaqt_6_7_64_gnu,liberaqt
LIBERAQT_TOKEN=<per-launch random token>
LIBERAQT_PORT=0                             # 0 => bind an ephemeral port
LIBERAQT_PORT_DIR=<temp dir>                # each agent writes <pid>.port here
```

The agent stays completely dormant unless `LIBERAQT_TOKEN` is set, so an application that ships
with the plugin present is inert.

!!! danger "This is a remote-code-execution surface by design"
    Loopback only, token required, one client at a time, dormant without the token. It must never
    ship in a production build.

Three consequences:

- **A statically-linked Qt application can never be instrumented this way.** There is no plugin
  loader to hook, and a second copy of Qt in one process is undefined behaviour.
- **A process that never builds a GUI is equally unreachable.** Generic plugins are instantiated
  by the QPA platform plugin, which only exists once a `QGuiApplication` does. A
  `QCoreApplication`-only process reads the injection environment and acts on none of it — no
  agent, no port file, no error.
- **Every Qt child the application spawns inherits this and loads an agent too.** That is not a
  leak; it is the only way to reach a window that is not in the main application at all. See
  [Child processes](../guide/child-processes.md).

## 2. Locators are lazy

A `Locator` holds a **selector**, not an object. Nothing goes over the wire until you act on it
or read state from it.

```python
button = win.locator("QPushButton[text='Apply']")   # no I/O at all
button.click()                                       # resolves now, retrying
```

This is why a locator built before a dialog exists still works once the dialog opens — and why
chaining is free:

```python
dock = win.locator("QDockWidget").first
dock.locator("QPushButton").click()    # "a button inside that one dock"
```

!!! warning "Chaining off a narrowed locator scopes to it"
    `win.locator("X").first.locator("Y")` means *Y inside that one X*. This was once not true,
    and the silent widening it caused is now pinned by a test.

## 3. Every action auto-waits

```
resolve to exactly one object (retry at 50 ms)
   → check actionability: exists → visible → enabled → not obscured → has geometry
      → act
         → wait for idle: event queue drained, animations done, one more event-loop turn
```

Locators are **strict** by default: if a selector matches several objects, resolving it is an
error rather than a silent pick of the first. Choose deliberately with `.first`, `.last`, or
`.nth(i)`.

!!! note "A not-found locator surfaces as a *timeout*"
    `retry()` re-raises the last retryable error as `LiberaQtTimeoutError`, with the real cause on
    `__cause__`. So assert on the cause:

    ```python
    from liberaqt import LiberaQtTimeoutError          # (not liberaqt.errors)
    from liberaqt.errors import ObjectNotFoundError

    def root_cause(exc):
        while exc.__cause__ is not None:
            exc = exc.__cause__
        return exc

    with pytest.raises(LiberaQtTimeoutError) as excinfo:
        win.locator("QPushButton#nope").click(timeout=1)
    assert isinstance(root_cause(excinfo.value), ObjectNotFoundError)
    ```

    **The chain depth varies**, so walk it rather than assuming. An action nests two retries --
    the action's around the resolution's -- while resolving directly nests one:

    | Call | Chain |
    |------|-------|
    | `loc.click()` | `TimeoutError -> TimeoutError -> ObjectNotFoundError` |
    | `loc.resolve()` | `TimeoutError -> ObjectNotFoundError` |

    `timeout=0` gives exactly one attempt, which is what you want when you *expect* a failure.

## 4. Selecting is clicking

Input is delivered through `QWindowSystemInterface`, the seam a platform plugin pushes real input
through. Qt routes it exactly as it routes a person's: hit-testing, hover, the implicit grab
between press and release, double-click derivation, popup dismissal, focus-on-click, and modal
blocking. Widgets see `spontaneous()` events.

So the convenience methods are genuinely clicks:

| Method | What it really does |
|--------|---------------------|
| `select_tab` | clicks the tab's rectangle on the bar |
| `select_item` | clicks the row |
| `select_option` | clicks the combo open, then clicks the entry in its popup |
| `menu().trigger()` | walks the path, clicking each menu open |
| `spin()` | clicks the spin box's arrows, via `QStyle::subControlRect` |

That means they **fail when a user could not do it either** — a tab scrolled off the bar, an entry
in an overlong menu. When you want the state written regardless, pass `mode="synthetic"`.

Three things that are easy to rediscover the hard way:

- **A press activates an inactive window first.** Without it the application stays in the
  background however hard the test clicks, and window-context shortcuts match nothing.
- **`focusWidget()` is null whenever the application is not the foreground one**, which is normal
  under test. Keys are therefore aimed at a *window*, and tests should assert on where typing
  landed rather than on `hasFocus()`.
- **Every `input.*` command is asynchronous**, because it queues rather than delivers. The reply
  waits for the queue to drain, so the next command cannot race the click.

## 5. Ask the agent what it can do

The client knows more command names than any single agent registers, and an agent compiled
without Qt Quick registers fewer still. Rather than trusting documentation, ask:

```python
app.capabilities            # {'commands': [...], 'quick': False, 'abi': 64, ...}
app.supports("quick.evaluate")
app.agent_version
```

An unregistered command fails cleanly with `UnsupportedOperationError` rather than hanging, so
this is a convenience rather than a safety requirement — but it is the only answer that is true
for the binary actually loaded.

## Where the pieces live

| Module | Purpose |
|--------|---------|
| `liberaqt/__init__.py` | `LiberaQt` / `liberaqt()` entry point: `launch()`, `connect()`, cleanup |
| `session.py` | The one object that talks to the wire; also object maps |
| `transport.py` | Framed JSON over TCP, request/response correlation, event demux |
| `locator.py` | Lazy resolution, chaining, actions, auto-wait retry |
| `selectors.py` | Parses the selector mini-language into a JSON query |
| `expect.py` | Retrying assertions with readable failure text |
| `launcher.py` | Spawns the application with injection env, port-file handshake |
| `agent_registry.py` | Resolves an agent binary by Qt version, platform and compiler |

The C++ side mirrors it: `selector_engine` walks the tree, `widget_backend` handles geometry and
actionability, `input_synth` delivers events, `dispatcher` maps commands to handlers.
