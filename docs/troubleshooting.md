# Troubleshooting

Most LiberaQT problems fail *silently* rather than loudly, so the symptom rarely names the cause.
This page is organised by what you actually see.

## pytest dies before running anything

```
ModuleNotFoundError: No module named 'liberaqt'
```

...during config parsing, with no test collected.

The package registers a `pytest11` entry point. If the metadata is installed but the module is not
importable — you moved or renamed something under `src/` — pytest dies at startup. It looks
nothing like an install problem.

```bash
pip install -e ".[dev]"
```

## "the liberaqt agent did not report a port"

The application launched but no agent called home. In order of likelihood:

1. **No agent for that ABI.** Run `liberaqt doctor <exe>`. An agent for the right Qt version but
   the wrong compiler or architecture will not load.
2. **The application is statically linked.** `doctor` says so. It cannot be driven: there is no
   plugin loader to inject through, and the embed API sketched in
   [INJECTION.md §4](INJECTION.md#4-in-app-opt-in-most-robust-requires-app-cooperation) is
   declared in `embed.h` but not implemented.
3. **A stale agent is shadowing the right one.** `doctor` marks it `STALE`. Rebuild it.
4. **A bundled `qt.conf`** redirecting plugin paths.
5. **The process never constructs a `QGuiApplication`.** A `QCoreApplication`-only tool ignores
   the injection environment entirely. Launch it with whatever arguments make it build a GUI.

Only generic-plugin injection is implemented; the other modes in
[INJECTION.md](INJECTION.md) are designs.

Make Qt explain itself:

```bash
QT_DEBUG_PLUGINS=1
```

Pass it through the launch -- `lq.launch(exe, env={"QT_DEBUG_PLUGINS": "1"})` -- and the
`LaunchError` carries the application's last 40 lines of output in `exc.data["log"]`. The trace
is long, so for all of it run the application by hand with the variable set.

Qt then narrates every plugin it considers, prints the keys it extracted from each, and says why
it refused one. This is the highest-value diagnostic in the whole system — reach for it early.

## A child process has no agent

Check what is actually loaded in it:

```powershell
(Get-Process -Id <pid>).Modules |
    Where-Object { $_.ModuleName -match 'Qt5Gui|Qt6Gui|qwindows|liberaqt' } |
    Select-Object -Expand ModuleName
```

| What you see | Meaning | Fix |
|--------------|---------|-----|
| no `Gui`, no `qwindows` | the process never built a GUI | none — launch the tool yourself instead |
| `qwindows` but no `liberaqt` | the plugin was found and refused | `QT_DEBUG_PLUGINS=1`; check the ABI keys |
| `liberaqt` present | the agent loaded | look for its `<pid>.port` file |

See [Child processes](guide/child-processes.md) for the full story, including why one plugin key
cannot serve two architectures.

## A test times out on a locator that clearly exists

Remember that **a not-found locator surfaces as a timeout**, with the real error on `__cause__`:

```python
from liberaqt import LiberaQtTimeoutError     # exported here, not from liberaqt.errors

def root_cause(exc):
    while exc.__cause__ is not None:
        exc = exc.__cause__
    return exc

try:
    win.locator("QPushButton#save").click()
except LiberaQtTimeoutError as exc:
    print(root_cause(exc))    # ObjectNotFoundError, AmbiguousSelectorError, ...
```

Walk the chain rather than reading `__cause__` once: an action nests two retries
(`TimeoutError -> TimeoutError -> ObjectNotFoundError`) while `resolve()` nests one.

Then check, in this order:

1. **Is it ambiguous?** `win.locator("...").count`. Type matching walks the inheritance chain, so
   `QPushButton` also matches every subclass.
2. **Are you searching the right root?** A window is never inside its own subtree.
3. **Is the objectName really an identifier?** Names with spaces or slashes need
   `[objectName='...']`, not `#`.
4. **Is the class namespaced?** `QMetaObject::className()` reports the qualified name.

`liberaqt inspect --interactive` answers all four in about a minute.

## The click does nothing

- **Is something covering it?** In native mode the actionability check names an obscuring widget
  or a blocking modal. It detects but does not yet route around it.
- **Is a popup holding a mouse grab?** A `QCompleter` popup is a top-level window with an
  application-wide grab, and it will swallow the click that follows it. Dismiss it with
  ++escape++ first.
- **Is the target off-screen or in a hidden tab?** A user could not click it either. Use
  `mode="synthetic"` if reaching it anyway is what you want.
- **Is the window inactive?** A native press activates it first, but a *synthetic* one does not.
- **Was the target still moving?** Waiting for idle covers the event queue, not animations, so an
  action right after one that starts a slide or an expansion can land mid-animation. Assert the
  end state with `expect(...)` before acting on what the animation revealed.

## A regular expression in a selector matches nothing

Inside a quoted selector value a backslash escapes the next character and is then dropped, so
`[text~='^\w+$']` reaches the agent as `^w+$` -- no error, just no match. Double the backslash in
the selector text, or avoid it:

```python
win.locator(r"QLabel[text~='^\\w+$']")           # the agent receives ^\w+$
win.locator("QLabel[text~='^[A-Za-z0-9_]+$']")    # no backslash at all
```

## Typing goes nowhere

`QApplication::focusWidget()` is null whenever the application is not the foreground one, which is
normal under test and guaranteed when several are running. Keys are therefore aimed at a
**window**, and Qt hands them to its focus object.

- Click the field first — that is what sets focus.
- Assert on **where the text landed**, not on `hasFocus()`.

## `UnsupportedOperationError`

The agent does not implement that command. Ask what it does implement:

```python
app.capabilities["commands"]
app.supports("quick.evaluate")
```

Currently unimplemented, and expected to raise: all `quick.*` commands (so `evaluate()` on a
locator or an application), and `record.start` / `record.stop` (so `liberaqt record`).

The same error also means "not for this kind of object": mouse input and `highlight()` at Qt
Quick items, a screenshot of a Qt Quick window, and `app.screenshot()` while the application is
in the background (next section).

Note this error is **not retryable**, so it fails in milliseconds rather than burning the timeout.

## A screenshot is missing, or shows the wrong window

- **`app.screenshot()` raises `nothing to grab`** whenever the application is not the active one
  -- common with several applications running, or after a click in another. It grabs the active
  window only. `win.screenshot()` grabs that window whatever is in front, so use it.
- **The failure screenshot in `liberaqt-trace/` is missing**, with a
  `liberaqt: no screenshot for ...` warning: the same cause, since the plugin uses
  `app.screenshot()`. The protocol log beside it is still written.
- **`win.screenshot()` returned the main window, not the dialog.** The dialog had already closed:
  a screenshot through a `Window` whose window is gone falls back to whatever is active instead of
  raising. Take it while the dialog is open.

## Checking that a window closed

`expect(win)` has only `to_have_title()` and `to_be_active()` -- no `not_`, no visibility -- and
once a window has closed every read through its old `Window` raises `StaleObjectError`, `exists`
included. Wait on the application's window list instead:

```python
from liberaqt.waits import wait_until

wait_until(lambda: "Preferences" not in [w.title for w in app.windows],
           timeout=10, description="Preferences closed")
```

## QML: discovery works, input mostly does not

Locators reach `QQuickItem`s and read their properties, and `object.tree` walks into a
`QQuickWindow`'s `contentItem`. Input is uneven, measured against qmleasing on Qt 6.7.3:

| At a Quick item | Result |
|-----------------|--------|
| `click`, `double_click`, `hover`, `wheel`, `drag_to` | `UnsupportedOperationError`: the mouse path is QWidget-only |
| `type`, `press`, `win.keyboard.type` at a Quick window | **return normally and type into a different window**: the keys go to the application's *widget* window and land in whatever field has focus there. Measured: typing `9` at a `QQuickText` turned qmleasing's spin box from 500 into 9500. Do not type at a Quick item or window at all |
| `geometry` | `()` rather than `(x, y, w, h)` |
| `highlight()`, `win.screenshot()` of a Quick window | `UnsupportedOperationError` |
| `screenshot()` of a Quick item | **returns the active window's image**, with no error |
| reading properties, `expect(...).to_have_property(...)` | works |

!!! note "Quick support is optional at build time"
    `agent/CMakeLists.txt` compiles the Quick backend only when `find_package(Qt Quick Qml)`
    succeeds. Without it, `LIBERAQT_HAVE_QUICK` is undefined and Quick support compiles out
    entirely. Check `app.capabilities["quick"]` before debugging any QML failure.

## A widget has nothing inside it

A widget that paints its own contents has no child objects at all — no per-item objects, no
accessibility children. No selector will ever reach inside it.

The meta-object is the way in:

```python
loc.methods()                 # what can be called
loc.invoke("someSlot", 1, 2)
```

## Things that look like bugs and are not

| Observation | Explanation |
|-------------|-------------|
| A search from the tree root finds zero windows | a window is never inside its own subtree |
| `ancestor("QDockWidget")` returns a different class name | ancestor matching walks the inheritance chain; compare handles |
| Object counts change between runs of a QML app | a Quick scene's object count tracks the window size |
| A child process outlives the test | cleanup only terminates what the launcher started |
| `scroll_into_view()` succeeded but nothing scrolled | only a `QScrollArea` ancestor is scrolled; with none, it does nothing silently |
| `filter(has_not=...)` returns everything | in 0.1.x the condition never reaches the agent's matching; filter the `.all()` list in Python |
| `[role=...]`, `[path=...]`, `[qmlId=...]`, `[index=...]` match nothing | any key other than `text` and `objectName` is read as a Qt property, and those are not properties |
| `[text='Search']` matches an empty line edit | `text` falls back to `placeholderText` when the text is empty |
