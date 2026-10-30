---
name: write-tests
description: Write, extend, review or fix UI tests for a Qt desktop application with LiberaQT - the `liberaqt` Python package, which injects an agent into a Qt 5.15 / 6.5 / 6.7 application and drives its live QObject tree from Python and pytest. Use whenever the work touches `from liberaqt import ...`, `win.locator(...)`, `expect(...)`, a `liberaqt.toml`, an `objects.yaml` object map or the `liberaqt` CLI, or when asked to automate or test a Qt Widgets or QML application and the project already depends on liberaqt.
---

# Writing LiberaQT tests

LiberaQT drives a real Qt application. A C++ agent is loaded into the application as a Qt plugin,
and the test talks to it over a loopback socket. Tests address objects with CSS-like selectors
over the live `QObject` tree, and every action waits for the application on its own.

This skill describes LiberaQT **0.1.x**. When it disagrees with the installed package, the
installed package wins. Check anything surprising against it:

```bash
python -c "import liberaqt; print(liberaqt.__version__)"
python -c "from liberaqt.cli import docs_root; print(docs_root() / 'docs')"   # docs for this version
python -c "import liberaqt; help(liberaqt.Locator.click)"                     # any signature
```

Every public call, verified against 0.1.x, is listed in [references/api.md](references/api.md).
Behaviour that is broken or surprising in 0.1.x is in
[references/limitations.md](references/limitations.md). Read that file before working with QML,
regular expressions in selectors, `filter(has_not=...)` or the recorder.

## The loop: look, write, run, read the failure

1. **Confirm the application can be driven.** Run `liberaqt doctor "<path to exe>"`. If it does
   not report a matching agent, no test code will help; switch to the `troubleshoot` skill.
2. **Look before you write.** Get every selector from the live application:

   ```bash
   liberaqt inspect "<exe>" --depth 3
   ```

   Each row is checked against the same engine the test uses. `unique` is safe to use.
   `positional (N of this type)` means the object has no stable handle (see "Choosing a
   selector" below). The application's source code is a good hint for `objectName`s, but confirm
   every selector live: type matching follows inheritance, so a selector that looks unique in the
   source can match several objects at runtime.

   `inspect` only sees windows that are open at startup, and its output runs to tens of
   kilobytes. When you only need a few selectors, use the **`explorer` agent**, which returns a
   short verified table. For a dialog you have to navigate to, rank its selectors from a
   throwaway script with the same machinery `inspect` uses:

   ```python
   from liberaqt import liberaqt
   from liberaqt.suggest import format_table, resolver_for, suggest_all, summarize

   with liberaqt(default_timeout=15.0) as lq:
       app = lq.launch(EXE, timeout=120.0)
       app.window(title="Main Window").menu("Edit > Preferences...").trigger()
       dlg = app.wait_for_window(title="Preferences", timeout=10)
       rows = suggest_all(dlg.tree(depth=4), resolver_for(app._session, dlg.resolve()))
       print(format_table(rows))
       print(summarize(rows))
   ```

   `app._session` is private. If the installed version has removed it, check candidate selectors
   one at a time with `.count` instead.
3. **Write the test** following the rules below.
4. **Run it.** When it fails, read the root cause and `liberaqt-trace/` before you change
   anything (see "When it fails").
5. **Prove it can fail.** Once the test is green, change one expected value, confirm the test goes
   red with a readable message, then restore the value. It is easy to write a GUI assertion that
   passes whatever the application does.

## Project setup

The pytest plugin loads itself once `liberaqt` is installed. **Do not** add
`pytest_plugins = ["liberaqt.pytest_plugin"]` to a conftest: that registers the plugin a second
time and pytest aborts.

```toml
# liberaqt.toml, at the pytest rootdir. Relative paths are relative to this file.
[liberaqt]
executable = "C:/Program Files/MyApp/myapp.exe"
object_map = "objects.yaml"        # optional; enables win.obj("dotted.name")
timeout = 10.0
```

| Fixture | Scope | Use |
|---|---|---|
| `app` | function | a fresh application for each test |
| `app_session` | session | one shared application; get windows with `app_session.window(...)` |
| `win` | function | first window of a **fresh** `app`. Requesting it alongside `app_session` launches a second process |
| `liberaqt` | session | the driver; build your own fixtures on it |

Launching a large application for every test is slow, so suites usually use a module-scoped
fixture. **Build it on the `liberaqt` fixture, not on a new `LiberaQt()`.** Only applications
launched through that fixture get a failure screenshot and protocol log, and only they honour
`--liberaqt-timeout` and `--liberaqt-slowmo`:

```python
import pytest
from liberaqt import LiberaQtError

@pytest.fixture(scope="module")
def app(liberaqt):
    application = liberaqt.launch(EXE, timeout=120.0, object_map="objects.yaml")
    try:
        application.wait_for_window(title="Tip of the Day", timeout=5).close()
    except LiberaQtError:
        pass  # not shown on every start
    yield application
    application.close()
```

A fixture for something that may be missing (an application, a licence, a file) must call
`pytest.skip`, never raise: a fixture error is not covered by `xfail`.

## Choosing a selector

Prefer the most stable handle the application gives you, in this order:

1. **`objectName`.** `QPushButton#okButton`. If the name contains spaces or slashes, use
   `[objectName='comment/context view']`, because `#` only takes an identifier.
2. **Visible text.** `QPushButton[text='Apply']`. Breaks when the product is translated or the
   wording changes.
3. **Structure.** `QDockWidget#IndexWindow QLineEdit`, `QListView:has(QLabel[text='Inbox'])`.
4. **Position.** `.nth(3)`. Last resort, and needs a comment. If the application is yours, add an
   `objectName` instead.

Four rules that catch people out:

- **Type matching includes subclasses.** `QPushButton` also matches `MyButton : QPushButton`. Use
  `QPushButton!` for the exact class. You cannot work out uniqueness by reading a tree dump. Ask
  the engine (`.count`, `inspect`, `explorer`).
- **A window is not inside its own subtree.** Get a window with `app.window(title=...)` or
  `app.wait_for_window(...)`, not with a locator.
- **Class names may be namespaced.** `Aqwidget::AQDockWidget` works as written.
- **`:has(X)` and `:parent(X)` are filters.** They return the object on the left.
  `.parent()` and `.ancestor("QDockWidget")` move up the tree and return a different object.
  `ancestor()` can return a subclass, so compare handles (`loc.resolve()`), not class names.

Selectors that keep breaking belong in an object map (`objects.yaml`, used through
`win.obj("dialog.ok")`). Check the map against the live application with
`liberaqt inspect "<exe>" --validate objects.yaml`. It exits non-zero on a missing or ambiguous
entry, so it also works as a CI check after the application is upgraded.

## Rules that make GUI tests reliable

**Assert with `expect`, never with a single read.** `expect(loc).to_have_text("Ready")` retries
until the text matches or the timeout expires. `assert loc.text == "Ready"` reads once and races
the application. `expect(loc).not_.to_be_visible()` also retries, until the condition stops
being true. Pass `timeout=` on one assertion when that step is genuinely slow.

**Never `sleep`.** Every action resolves its target, checks it is actionable, acts and then
waits for the application to go idle. If you seem to need a sleep, you are waiting for something
specific, so wait for that instead:

| Waiting for | Use |
|---|---|
| a window or dialog | `app.wait_for_window(title=..., timeout=...)` |
| a widget's state | `expect(...)` |
| a model filled by a worker thread | `view.row(index=0, timeout=30)`, `view.cell(0, "Status", timeout=30)` |
| a signal | `win.wait_for_signal("finished", timeout=...)` |
| something outside the application | `liberaqt.waits.wait_until(lambda: path.exists(), timeout=...)` |

`app.wait_for_idle()` returns as soon as the event queue is empty, so it does **not** cover a
model that a worker thread fills later.

**Locators are strict.** A selector that matches two objects raises `AmbiguousSelectorError`;
it does not pick one for you. Write a better selector first. Use `.first` or `.nth(i)` only when
the order really means something. When several objects match and only one is visible, pick that
one:

```python
bar = next(b for b in win.locator("QTabBar").all() if b.is_visible)
bar.select_tab(text="Index")
```

**Chaining from a narrowed locator searches inside it.**
`win.locator("QDockWidget#IndexWindow").first.locator("QLineEdit")` means the line edit *inside
that dock*.

**Click before you type.** The application under test is usually not the foreground window, so
nothing has keyboard focus until a click gives it focus. Assert on where the text ended up
(`expect(field).to_have_text(...)`), not on which widget has focus.

**`type()` tests behaviour; `fill()` sets up state.** `type()` sends key events the way a user
does. `fill()` writes the text property directly. That makes it fast, and it works on a field in
a tab page that is not shown, but no key events are sent: `textEdited` handlers do not run and a
`QLineEdit` validator is not consulted. Use `fill()` to prepare a test and `type()` for the input
the test is about. `fill()` refuses read-only and disabled widgets, as a user would.

**Keep native input, which is the default.** It goes through Qt's real input path (hit-testing,
focus-on-click, popup dismissal, modal blocking), so it fails exactly where a user would.
`mode="synthetic"` reaches widgets that are covered, off-screen or on a hidden tab page. Use it
for a single call, with a comment explaining why the user's path cannot be tested.

**Selecting means clicking.** `select_tab`, `select_item`, `select_option`, `spin` and
`win.menu(...).trigger()` all click the way a user does. A tab scrolled off its bar, or an entry
at the bottom of an overlong menu, therefore fails in native mode. `mode="synthetic"` sets the
state directly instead.

**Menus do not retry.** `win.menu("File > Export > PDF...")` must match every level exactly,
including case, and it fails at once. The error lists the entries that do exist, so copy the
path from there.

**Expect a refusal quickly.** When a test asserts that an action fails, pass `timeout=0`.
Otherwise the test waits out the whole timeout before raising the error it predicted.

**Check that a window closed by asking the application.** `expect(win)` only offers
`to_have_title` and `to_be_active`, and has no `not_`. Once a window has closed, every read
through its old `Window`, `exists` included, raises `StaleObjectError`. Wait on the list of
window titles instead:

```python
from liberaqt.waits import wait_until

wait_until(lambda: "Preferences" not in [w.title for w in app.windows],
           timeout=10, description="Preferences closed")
```

## When it fails

A locator that cannot be resolved raises **`LiberaQtTimeoutError`**, with the real error
attached as `__cause__`. How deep the chain goes depends on the call (`click()` nests one level
more than `resolve()`), so walk to the end of it:

```python
import pytest
from liberaqt import LiberaQtTimeoutError     # in liberaqt.errors this class is named TimeoutError
from liberaqt.errors import ObjectNotFoundError

def root_cause(exc):
    while exc.__cause__ is not None:
        exc = exc.__cause__
    return exc

with pytest.raises(LiberaQtTimeoutError) as excinfo:
    win.locator("QPushButton#nope").click(timeout=0)
assert isinstance(root_cause(excinfo.value), ObjectNotFoundError)
```

The root cause names the problem and usually lists near misses. Next, look at
`liberaqt-trace/` (a screenshot of every running application, plus the last protocol messages).
Then rerun with `--liberaqt-slowmo 0.5` to watch the test. The `troubleshoot` skill has a
checklist for each error type.

## Things that are not widgets

- **A widget that draws its own contents** (a chart, a schematic, a canvas) contains no child
  objects, so no selector can reach inside it. Use its meta-object instead: `loc.methods()` lists
  what can be called, and `loc.invoke("setZoom", 2.0)` calls it. Coordinates
  (`win.mouse.click(x=..., y=...)`) are the last resort.
- **Item views.** A cell has no object of its own, so `row()`, `cell()` and `item()` address one
  by position or text. They retry and scroll the view themselves:
  `table.row(has_text="Widget A").context_menu("Delete")`.
- **A window that belongs to a child process** (a configurator or viewer the application starts)
  has its own agent. Reach it with `app.wait_for_child_agent()` and then
  `app.attach_child(endpoint)`. A child process can outlive the application, so terminate it in
  your fixture teardown if necessary.
- **QML.** Locators find Quick items and can read their properties. Input is only partly
  supported: see [references/limitations.md](references/limitations.md) before writing a QML test.
