# LiberaQT 0.1.x API

The public calls, taken from the signatures of the installed package. `timeout=None` means "use
the session default" (5 s unless `default_timeout`, `liberaqt.toml` or `--liberaqt-timeout`
changes it), and `timeout=0` means exactly one attempt. `mode=` is `"native"` (the default) or
`"synthetic"`. To check any signature against the installed version, run
`python -c "import liberaqt; help(liberaqt.Locator)"`.

## Entry points

```python
from liberaqt import liberaqt, LiberaQt, expect, LiberaQtError, LiberaQtTimeoutError

with liberaqt(default_timeout=15.0) as lq:        # or LiberaQt(default_timeout=..., slowmo=..., trace=..., input_mode=...)
    app = lq.launch(exe, args=None, cwd=None, env=None, qt=None,
                    object_map=None, timeout=30.0, headless=False)
    app = lq.connect(port, token="", object_map=None)   # attach to an agent that is already running
    with lq.timeout(60):                                 # raise the default for a block
        ...
    lq.set_default_timeout(10.0)
```

Under pytest, use the plugin's `liberaqt` fixture (a session-scoped `LiberaQt`) rather than
constructing one yourself.

## Application

| Call | Notes |
|---|---|
| `app.window(title=None, index=0, timeout=None)` | an existing window. `title` must match exactly |
| `app.wait_for_window(title=None, timeout=None)` | a window that is about to appear |
| `app.windows` | **property**, a list. `app.windows()` raises `TypeError` |
| `app.wait_for_idle()` | event queue drained. Does not wait for worker threads |
| `app.set_input_mode("native" \| "synthetic")` | session default for input |
| `app.capabilities`, `app.supports("cmd.name")` | what the loaded agent actually implements |
| `app.qt_version`, `app.agent_version`, `app.pid`, `app.is_running`, `app.info` | |
| `app.logs` | the application's stdout/stderr lines (not its child processes') |
| `app.screenshot(path=None)` | PNG of the *active* window only. Raises `UnsupportedOperationError` when the application is in the background, so prefer `win.screenshot()` |
| `app.on("window.opened", callback)` | events: `"window.opened"`, `"window.closed"`, `"object.destroyed"`, `"app.message"`, `"app.about_to_quit"` |
| `app.child_agents`, `app.wait_for_child_agent(timeout=30.0, pid=None)` | agents in child processes |
| `app.attach_child(endpoint=None, *, pid=None, object_map=None, timeout=30.0)` | returns an `Application` for that child |
| `app.close(timeout=5.0)`, `app.kill()` | return the exit code |

## Window

A `Window` is also a `Locator`, so every call in the next section works on it as well. A window
does not know which `Application` it belongs to: there is no `win.app`, so keep a reference to
`app` if you need it later.

| Call | Notes |
|---|---|
| `win.locator(selector)` | search inside the window |
| `win.obj("section.name")` | a locator from the object map |
| `win.menu("File > Export > PDF...").trigger(mode=None)` | exact path, **no retry**. `.is_enabled` |
| `win.title`, `win.kind` | `kind` is `"widget"` or `"quick"` |
| `win.is_active`, `win.activate()`, `win.close()`, `win.move(x, y)`, `win.resize(w, h)` | |
| `win.tree(depth=-1, visual_only=True)` | the raw object tree as a dict. Large |
| `win.mouse.click(x, y, button="left", modifiers=None, count=1, mode=None)` | window coordinates. Last resort |
| `win.mouse.move / down / up / drag(from_xy, to_xy) / wheel(x, y, dx, dy)` | |
| `win.keyboard.type(text) / press(key, count=1) / down(key) / up(key)` | keys go to whatever has focus |

## Locator

Locators are lazy: nothing is sent to the agent until you act on one or read from it.

**Narrowing and navigation**

| Call | Notes |
|---|---|
| `loc.locator(selector)` | search further down. Scoped to one object after `.first`, `.last` or `.nth()` |
| `loc.first`, `loc.last`, `loc.nth(i)` | properties / method. Choosing one match turns strictness off |
| `loc.all()` | resolves now: one locator per match |
| `loc.count` | property. Asks the engine |
| `loc.filter(has=..., has_text=...)` | `has_not=` does nothing in 0.1.x (see limitations.md) |
| `loc.child(selector)` | a direct child |
| `loc.parent()`, `loc.ancestor(selector)` | move up the tree. Resolve immediately |
| `loc.resolve(timeout=None)` | the handle string. Compare handles, not class names |

**Actions** (each one resolves, checks actionability, acts and then waits for idle)

| Call | Notes |
|---|---|
| `loc.click(button="left", modifiers=None, position=None, count=1, timeout=None, mode=None)` | `modifiers=["ctrl", "shift"]` |
| `loc.double_click()`, `loc.right_click()`, `loc.hover()` | |
| `loc.type(text, delay=0.0)` | key events, and accepts chords inline: `"abc<Ctrl+A>xyz"` |
| `loc.press(key, count=1)` | `"Return"`, `"Ctrl+A"`, `"Escape"` |
| `loc.fill(text)` | writes the property directly. No key events, no validator |
| `loc.clear()` | |
| `loc.check()`, `loc.uncheck()`, `loc.set_checked(checked=True)` | |
| `loc.select_option(text=None, index=None)` | combo box: clicks it open, then clicks the entry |
| `loc.select_tab(text=None, index=None)` | call on the `QTabBar` (or the tab widget) |
| `loc.select_item(text=None, row=None, column=None)` | item view row |
| `loc.spin(steps)` | clicks a spin box's arrows |
| `loc.wheel(dx=0, dy=0)` | a positive `dy` scrolls down |
| `loc.drag_to(target, steps=10)` | |
| `loc.context_menu("Delete")` | right-click, then choose the entry |
| `loc.scroll_into_view()` | only scrolls a `QScrollArea` ancestor; does nothing silently if there is none |
| `loc.highlight(duration=1.0)` | draws a box around the object, to check you have the right one. Widgets only |

**Reading state** (properties, except where shown)

`loc.text`, `loc.value` (a slider or spin box; `None` for a text field), `loc.is_visible`,
`loc.is_enabled`, `loc.is_checked`, `loc.exists`, `loc.geometry` (`(x, y, w, h)` relative to the
parent), `loc.class_name`, `loc.object_name`, `loc.properties(declared_only=False)`,
`loc.methods(declared_only=False, callable_only=False)`, `loc.screenshot(path=None)`,
`loc.accessible(depth=-1)`.

**Item views**

| Call | Notes |
|---|---|
| `view.row(has_text=None, index=None, timeout=None)` | retries until the model has the row |
| `view.cell(row, column, timeout=None)` | `column` is a 0-based int or a header text |
| `view.item(text, timeout=None)` | a tree or list item by its text |
| `view.headers()` | header labels |
| `view.to_records(max_rows=None)` | rows as dicts keyed by header. Pass `max_rows` for a large model |

**Meta-object** (for widgets that draw their own contents)

| Call | Notes |
|---|---|
| `loc.methods()` | slots and `Q_INVOKABLE`s |
| `loc.invoke("method", *args, queued=False)` | call one |
| `loc.wait_for_signal("signalName", timeout=None)` | |
| `loc.call_native(module, symbol, argument, signature=...)` | resolves an exported C symbol. A wrong signature crashes the application |

## Assertions

```python
expect(loc).to_exist()
expect(loc).to_be_visible()
expect(loc).to_be_hidden()
expect(loc).to_be_enabled()
expect(loc).to_be_disabled()
expect(loc).to_be_checked(checked=True)
expect(loc).to_have_text("exact")
expect(loc).to_contain_text("part")
expect(loc).to_match_text(r"regex")
expect(loc).to_have_value(42)                    # sliders and spin boxes, not text fields
expect(loc).to_have_property("name", value)
expect(loc).to_have_count(0)                     # the reliable way to say "is gone"
expect(win).to_have_title("...")
expect(win).to_be_active()
expect(loc).not_.to_be_visible()                 # negation keeps retrying
expect(loc, timeout=60).to_have_text("Done")     # or timeout= on the assertion itself
```

## Waiting outside the application

```python
from liberaqt.waits import wait_until
wait_until(lambda: out_file.exists(), timeout=120, description="export written")   # timeout is required
```

## Errors

| Exception (`from liberaqt import ...`) | Retried | Meaning |
|---|---|---|
| `ObjectNotFoundError` | yes | nothing matched |
| `AmbiguousSelectorError` | yes | a strict locator matched several objects |
| `NotActionableError` | yes | found, but hidden, disabled, covered or without geometry |
| `StaleObjectError` | yes | the object was destroyed, for example a dialog that closed |
| `UnsupportedOperationError` | no | the loaded agent does not implement the command |
| `InvalidSelectorError` | no | the selector does not parse |
| `LiberaQtTimeoutError` | | what a retried error turns into. The real error is on `__cause__` |
| `LaunchError`, `AgentMismatchError`, `ConnectionLostError` | no | the environment; see the `troubleshoot` skill |

All of them derive from `LiberaQtError`.
