# Known limitations in LiberaQT 0.1.x

Each item below was measured against LiberaQT 0.1.0.dev0 (agent revision 77c72cc, Qt 6.7.3). Some
of them contradict the project's own documentation; where they do, this file is the measured
behaviour. Each item also says how to tell whether a newer version has fixed it. Check that
before you build a workaround into a suite.

## QML: you can find Quick items, but input is only partly supported

Locators reach `QQuickItem`s, QML-defined types (`Button_QMLTYPE_0`) and their properties, and
`win.kind == "quick"` identifies a Quick window. Beyond that:

| Call on a Quick item or Quick window | 0.1.x behaviour |
|---|---|
| `click`, `double_click`, `hover`, `wheel`, `drag_to` | `UnsupportedOperationError` |
| `type`, `press`, `win.keyboard.type` | **report success and type into a different window**: the keys reach the focused field of the application's *widget* window, not the Quick item. Measured: typing `9` at a `QQuickText` turned qmleasing's spin box from 500 into 9500. Do not type at a Quick item or a Quick window at all |
| `geometry` | `()`, an empty tuple, not `(x, y, w, h)` |
| `highlight()` | `UnsupportedOperationError` |
| `win.screenshot()` on a Quick window | `UnsupportedOperationError: nothing to grab` |
| `loc.screenshot()` of a Quick item | returns the *active window's* image, with no error |
| `loc.evaluate(...)`, `app.evaluate(...)` | `UnsupportedOperationError` (`quick.evaluate` is not registered) |
| selector attribute `[qmlId='...']` | matches nothing |
| reading properties: `loc.text`, `to_have_property(...)`, `loc.properties()` | works |

So the reliable parts of a QML test are discovery and property assertions. To drive a Quick
item's logic, check what its meta-object exposes with `loc.methods()` and call it with
`loc.invoke(...)`.

The agent may also have been built without Qt Quick at all. In that case nothing inside a Quick
window can be found. Check `app.capabilities.get("quick")` before debugging any QML failure.

The number of objects in a Quick scene depends on the window size (a delegate grid creates more
items in a taller window), so never assert an exact count of QML objects.

**Fixed yet?** `app.supports("quick.evaluate")` returns True, or a `click()` on a Quick item no
longer raises.

## A backslash in a selector value is an escape character

Inside a quoted selector value, `\` escapes the next character and is then dropped. A regular
expression therefore loses its backslashes: `[text~='^\w+$']` reaches the agent as `^w+$` and
matches nothing, **without any error**.

Double each backslash in the selector text, or avoid backslashes altogether:

```python
win.locator(r"QLabel[text~='^\\w+$']")           # agent receives ^\w+$
win.locator("QLabel[text~='^[A-Za-z0-9_]+$']")    # no backslash needed
```

**Fixed yet?** This prints `\w` once it is fixed, and `w` while it is not:

```python
from liberaqt import selectors
print(selectors.parse(r"X[text~='\w']").steps[0].attrs[0].value)
```

## `filter(has_not=...)` is ignored

`loc.filter(has_not="QLabel")` matches exactly what `loc` matches: the condition never reaches
the agent's matching. `filter(has=...)` and `filter(has_text=...)` work. Until this is fixed,
filter in Python:

```python
without_label = [x for x in win.locator("QWidget#panel > QFrame").all()
                 if x.locator("QLabel").count == 0]
```

A locator taken from `.all()` searches only inside its own object, so the inner `count` is
scoped correctly.

**Fixed yet?** `loc.filter(has_not=X).count` is lower than `loc.count` for a selector where some
matches contain `X`.

## Some documented selector attributes do not exist

Apart from `text` and `objectName`, every attribute in `[key=...]` is looked up as a Qt property
(`QObject::property("key")`). `role`, `path` and `qmlId` are not Qt properties, so they match
nothing. Use real property names, which `loc.properties()` lists, such as `[accessibleName=...]`,
`[toolTip=...]`, `[placeholderText=...]` or `[windowTitle=...]`.

## Waiting for idle does not wait for animations

Every action waits for "idle", which in 0.1.x means only that the event loop has caught up.
Running `QPropertyAnimation`s, sliding panels and expanding trees are **not** detected, although
parts of the documentation say they are. After an action that starts an animation, assert the
final state with `expect(...)` rather than reading geometry or acting on something that is still
moving.

## Screenshots of an application in the background

`app.screenshot()` grabs only the *active* window, and raises
`UnsupportedOperationError: nothing to grab` when the application is not the foreground one. That
is common when several applications are running, or after a click in another one.
`win.screenshot()` works regardless, so use it.

The automatic failure screenshot in `liberaqt-trace/` uses the same call, so it can be missing
for the same reason. pytest then shows a `liberaqt: no screenshot for ...` warning. The protocol
log is still written.

`win.screenshot()` on a window that has since closed does **not** raise. It returns an image of
whichever window is active instead. Take screenshots while the window is open.

## Windows have their own, smaller set of assertions

`expect(win)` returns a different assertion object from `expect(loc)`. It has only
`to_have_title` and `to_be_active`, and no `not_`, so `expect(dlg).to_be_visible()` and
`expect(dlg).not_...` both raise `AttributeError`. After a window closes, `dlg.exists` and
`dlg.is_visible` raise `StaleObjectError` rather than returning `False`. To assert that a window
has gone, wait on `app.windows` as shown in the `write-tests` skill. To assert on what is inside a
window, use a locator: `expect(dlg.locator("QPushButton#ok")).to_be_visible()`.

**Fixed yet?** `hasattr(expect(win), "not_")`.

## Waiting and enumeration gaps

- **Menus never retry**, and there is no call that lists menu entries, tab captions or combo box
  entries. When a path is wrong, the error message lists the entries that do exist; that is the
  only way to enumerate them.
- **Actionability names whatever covers the target, but does not click around it.** The check
  always probes the widget's own interaction point (its centre, or a checkbox's indicator), and
  `click(position=...)` does not change which point is checked. Close or move the covering widget.
  `mode="synthetic"` skips the check, but then the test no longer proves a user could do it.
- **`scroll_into_view()` only scrolls a `QScrollArea` ancestor.** If there is none, it silently
  does nothing. `row()`, `cell()` and `item()` scroll their own view.

## The recorder does not work yet

`liberaqt record` fails immediately with `UnsupportedOperationError`, because the agent does not
register `record.start`. Write tests by hand, using `liberaqt inspect` or the `explorer` agent for
selectors.

**Fixed yet?** `app.supports("record.start")`.
