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
2. **The application is statically linked.** `doctor` says so. Nothing can fix this except an
   embedded build.
3. **A stale agent is shadowing the right one.** `doctor` marks it `STALE`. Rebuild it.
4. **A bundled `qt.conf`** redirecting plugin paths.

Make Qt explain itself:

```bash
QT_DEBUG_PLUGINS=1
```

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

Currently unimplemented, and expected to raise: all `quick.*` commands, and `record.start` /
`record.stop`.

Note this error is **not retryable**, so it fails in milliseconds rather than burning the timeout.

## QML: discovery works, input does not

Locators reach `QQuickItem`s and read their properties, and `object.tree` walks into a
`QQuickWindow`'s `contentItem`. But the input backend is QWidget-only, so **clicking or typing at
a Quick item raises `UnsupportedOperationError`**, and `highlight()` is widget-only for the same
reason.

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
