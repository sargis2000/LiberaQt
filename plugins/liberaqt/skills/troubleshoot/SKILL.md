---
name: troubleshoot
description: Diagnose why LiberaQT cannot drive a Qt application or why a LiberaQT test fails - "the liberaqt agent did not report a port", LaunchError, AgentMismatchError, a child process with no agent, LiberaQtTimeoutError, ObjectNotFoundError, AmbiguousSelectorError, NotActionableError, StaleObjectError, UnsupportedOperationError, a click that does nothing, typing that goes nowhere, or a flaky UI test. Use as soon as a liberaqt launch or test misbehaves, before changing test code.
---

# Troubleshooting LiberaQT

Most LiberaQT failures are silent. The symptom rarely names the cause, so work through the
checks in order and do not jump to a fix. First decide which kind of failure you have:

- **The application cannot be driven at all.** The launch fails, there is no port, or there is no
  agent. Go to part A. Changing test code will not help.
- **The application is driven, but a test fails.** Go to part B.

## A. The agent never attaches

The application starts, but `launch()` raises `LaunchError` ("the liberaqt agent did not report a
port") or `AgentMismatchError`. Check these in order:

1. **`liberaqt doctor "<exe>"`.** It reads the Qt version, compiler and architecture from the
   binary itself, then says whether a matching agent is installed:
   - **No matching agent.** An agent has to match the application's Qt *minor* version (6.7, not
     6.8), its compiler family (MSVC and MinGW cannot be swapped) and its architecture (x86 or
     x86_64). Get one with `liberaqt agents install --tag <tag>`, using the exact tag `doctor`
     names (`--qt`/`--compiler` assume this machine's architecture, wrong for a 32-bit
     application on 64-bit Windows), or build it from
     a local Qt kit with `liberaqt agents build --tag <tag>` (`liberaqt agents kits` lists the
     kits on this machine). An agent built for an older minor version in the same major release
     loads into a newer one (6.5 serves 6.7); an agent built for a newer one is refused.
   - **`STALE`.** The installed binary does not carry its own plugin key. Rebuild or reinstall
     it.
   - **Static Qt.** The application cannot be instrumented this way, by any means. Only an
     application built with the agent embedded can be driven.
   - **No Qt.** This is not a Qt application.
2. **The process never creates a GUI.** The agent is started by Qt's platform plugin, which only
   exists once a `QGuiApplication` has been constructed. A `QCoreApplication`-only tool ignores
   the injection environment entirely: no agent, no port file, no error. Some tools only build a
   GUI when given certain arguments.
3. **A bundled `qt.conf`** can redirect plugin paths away from the agent.
4. **Make Qt explain itself.** With `QT_DEBUG_PLUGINS=1` set, Qt lists every plugin it considers
   and why it refuses one. This is the most useful diagnostic there is. Pass it with
   `lq.launch(exe, env={"QT_DEBUG_PLUGINS": "1"})`. The `LaunchError` then carries the
   application's last 40 output lines in `exc.data["log"]`. That output is long, so for the full
   trace run the application by hand with the variable set.

The `LaunchError` hint in 0.1.x also suggests "LD_PRELOAD mode" and "the in-app embed API".
Neither is implemented. Generic-plugin injection is the only mode that works.

If `doctor` reports a match but the launch still fails, also check `liberaqt agents list` for the
tag in use, and look for a second copy of the agent shadowing it from `LIBERAQT_AGENT_PATH`.

### A child process has no agent

Qt child processes inherit the injection environment and load their own agents. Reach them with
`app.wait_for_child_agent()` and `app.attach_child(endpoint)`. When none appears:

- **The child has a different architecture** (a 64-bit viewer started by a 32-bit application,
  say). Install an agent for each ABI. The launcher offers all of them, and each process picks the
  one it can load.
- **The child never builds a GUI** (see A.2). Launch the tool yourself with the arguments the
  parent would use.
- **Inspect the process.** On Windows,
  `(Get-Process -Id <pid>).Modules | ? ModuleName -match 'Qt.Gui|qwindows|liberaqt'`. No GUI
  module means the process has no GUI. `qwindows` without `liberaqt` means the agent was found
  and refused, so use `QT_DEBUG_PLUGINS`.
- A child's output does **not** reach `app.logs`.

## B. A test fails

### 1. Find the real error

A failure that was retried is raised as `LiberaQtTimeoutError`, with the real error on
`__cause__`, sometimes two levels down. Walk to the end of the chain before you reason about the
failure:

```python
def root_cause(exc):
    while exc.__cause__ is not None:
        exc = exc.__cause__
    return exc
```

Then gather the evidence before changing anything:

- **`liberaqt-trace/`** under the pytest rootdir: a screenshot of every running application at
  the moment of failure, and the last protocol messages. It is only written for applications
  launched through the plugin's `liberaqt` / `app` / `app_session` fixtures. The screenshot can be
  missing if the application was not the foreground window; look for a
  `liberaqt: no screenshot` warning.
- **`--liberaqt-slowmo 0.5`** pauses before every command, so you can watch the test.
- **`--liberaqt-trace`** logs every protocol message.

### 2. Act on the root cause

**`ObjectNotFoundError`**: nothing matched. Read the near misses in the message first. Then:

- Is the search in the right window? A dialog's widgets are not inside the main window. Get the
  dialog with `app.wait_for_window(title=...)`.
- Did the window close? A `Window` kept after its dialog closed raises `StaleObjectError`.
  Fetch it again.
- Is the class namespaced? Write `ns::Class`, as `QMetaObject::className()` reports it.
- Is the `objectName` really an identifier? Names containing spaces or `/` need
  `[objectName='...']`, not `#`.
- Is a regex in the selector losing its backslashes? See `write-tests` →
  `references/limitations.md`.
- Does the object exist yet? It may only be created when a menu or tab is opened. Do that first.
- Take the selector from the live application (`liberaqt inspect`, or the `explorer` agent).
  Do not guess it.

**`AmbiguousSelectorError`**: several objects matched a strict locator. Type matching includes
subclasses, so `QPushButton` also matches `MyButton`. Narrow the selector: add an `objectName`,
use `Type!` for the exact class, scope it under a parent, or use `:has(...)`. Use `.nth()` only
when the order really means something.

**`NotActionableError`**: found, but a user could not act on it. The message gives the reason:

| Reason | Fix |
|---|---|
| not visible | bring it on screen: open its tab, menu or dock first |
| outside the window ("dock whose tab is not current") | `select_tab` on the dock's tab bar first |
| covered by a widget | close or move the widget that covers it. A `QCompleter` popup holds a mouse grab; press Escape first |
| blocked by a modal dialog | deal with that dialog first |
| disabled | wait for it to become enabled: `expect(loc).to_be_enabled()` |
| read-only (`fill`) | a user could not type there either; the test is wrong |
| cannot take focus (`type`) | aim at the field, not at a label or status bar |

Use `mode="synthetic"` only when you have decided that a target a user cannot reach is still
acceptable to test, and say why in a comment.

**`StaleObjectError`**: the object was destroyed. This is usually a dialog that closed, or a
view that rebuilt its rows. Fetch the `Window` or locator again rather than reusing the old one.

**`UnsupportedOperationError`**: the loaded agent does not implement the command. This error is
not retried, so it arrives within milliseconds. Check `app.capabilities["commands"]` and
`app.supports("...")`. In 0.1.x that means input at QML items, `evaluate()`, the recorder, and
screenshots of a Quick or background window.

**`InvalidSelectorError`**: the selector does not parse. The message gives the offset and
puts a `^` under the problem.

### 3. The test does the wrong thing without failing

- **The click does nothing.** Is a popup holding the mouse grab? Is the window inactive? A native
  press activates the window first, a synthetic one does not. Was the target still animating?
  Waiting for idle does not cover animations in 0.1.x, so assert the end state with `expect`.
- **Typing goes nowhere.** Nothing has focus while the application is in the background. Click
  the field first, then assert on the field's text rather than on focus.
- **The test is flaky.** Look for a plain `assert` reading state once (use `expect`), a model
  filled by a worker thread (`row(..., timeout=)`), a missing `wait_for_window` for a dialog, or
  `.first` picking a different object depending on timing. A `sleep` is never the fix.
- **The test passes when it should not.** Check the assertion can fail: change the expected value
  and watch it go red. A negative assertion such as "this has gone" should use
  `expect(loc).to_have_count(0)` or `.not_.to_exist()`.

## Things that look like bugs and are not

| Observation | Why |
|---|---|
| a search from a window never finds the window itself | a window is not inside its own subtree |
| `ancestor("QDockWidget")` returns another class name | it matches subclasses too; compare `resolve()` handles |
| QML object counts change between runs | a Quick scene's object count depends on the window size |
| a child process outlives the test | cleanup only stops what the launcher started |
| `win.menu("View > Zoom In")` fails | menu paths match exactly, including case |
