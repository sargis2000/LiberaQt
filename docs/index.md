# LiberaQT

Modern UI automation for **Qt desktop applications**, scoped narrowly on purpose: Python as
the only test language, Qt 5.15 and 6.7, Windows and Linux, QWidget and QML.

Qt 6.5 works too: the Qt suites pass against 6.5.3 and 6.7.3 alike. Pick which one a run
drives with `--liberaqt-qt-bin`.

```python
from liberaqt import liberaqt, expect

with liberaqt() as lq:
    app = lq.launch(r"C:\Qt\6.7.3\mingw_64\bin\assistant.exe")
    win = app.window(title="Qt Assistant")

    win.menu("Edit > Find in Text...").trigger()
    win.locator("QCheckBox[text='Case Sensitive']").check()

    expect(win.locator("HelpViewer")).to_be_visible()
```

No sleeps, no coordinates, no screen scraping. Every action waits for the object to exist, become
visible, become enabled, and stop moving before it touches it.

!!! tip "Every example on this site is executed"
    `e2e/test_docs_examples.py` runs them against a real Qt Assistant, so a selector that stops
    existing fails a test rather than wasting your afternoon.

## How it is different from a screen-level tool

LiberaQT does not look at pixels. A small C++ agent is loaded **into** the application under test
through Qt's own generic-plugin mechanism, and exposes the live `QObject` tree over a loopback
JSON socket:

```
pytest / script (Python) ──JSON over TCP──► liberaqt agent (C++ Qt plugin) ──► QObject tree
```

That has three consequences worth understanding before you write a test:

- **Selectors address real objects.** `QPushButton[text='Apply']` matches an actual widget, so a
  test survives the window being moved, resized, or restyled.
- **Input goes through Qt's own input path.** Clicks are delivered via `QWindowSystemInterface`,
  the same seam a platform plugin pushes real input through, so hit-testing, hover, the implicit
  grab between press and release, popup dismissal and modal blocking all behave as they do for a
  human. Widgets even see `spontaneous()` events.
- **The application must be able to load a plugin.** A statically-linked Qt application cannot be
  instrumented this way at all. [`liberaqt doctor`](guide/cli.md#doctor) tells you in one command.

## What is proven, and what is not

This project is honest about its state, because a test framework that overstates itself wastes
your afternoon rather than its own.

| Area | State |
|------|-------|
| Widget discovery, selectors, actions, item views, menus | Working, exercised against real applications |
| Auto-waiting and retrying assertions | Working |
| Out-of-process children, including a different CPU architecture | Working — see [Child processes](guide/child-processes.md) |
| QML **discovery** — locators reach Quick items, read properties | Working |
| QML **input** — clicking or typing at a `QQuickItem` | Not implemented; raises `UnsupportedOperationError` |
| `quick.evaluate` and the other `quick.*` commands | Not implemented |
| The recorder (`liberaqt record`) | Not wired up; fails immediately |

Ask the agent rather than trusting this table, which is a snapshot:

```python
app.capabilities["commands"]        # every command this agent registered
app.supports("quick.evaluate")      # False on a current build
```

## Where to go next

<div class="grid cards" markdown>

- :material-download: **[Install](getting-started/install.md)**

    Get the client and an agent matching your application's Qt build.

- :material-rocket-launch: **[Your first test](getting-started/first-test.md)**

    Drive a real application end to end, in about ten minutes.

- :material-lightbulb: **[How it works](getting-started/concepts.md)**

    Injection, the object tree, auto-waiting — the mental model.

- :material-help-circle: **[Troubleshooting](troubleshooting.md)**

    Silent failures and what they actually mean.

</div>
