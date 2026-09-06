# Actions

Every action resolves its locator, checks the object is actionable, performs the action, and waits
for the UI to settle. You never add a sleep.

## Mouse

```python
loc.click()
loc.click(button="right")
loc.click(modifiers=["ctrl", "shift"])
loc.double_click()
loc.hover()
loc.wheel(dy=-3)                    # positive scrolls down
loc.drag_to(other)
```

## Keyboard

```python
loc.type("hello")                   # types character by character
loc.fill("hello")                   # clears, then types
loc.press("Return")
loc.press("Ctrl+A")
```

Both `Locator.type` and `Keyboard.type` accept **embedded key chords** through one shared
splitter, so the same string means the same thing whichever you hand it to:

```python
loc.type("abc<Ctrl+A>xyz")          # types abc, selects all, types xyz
```

Ordinary text containing `<` types literally — only sequences that look like key chords are
treated as keys.

## Widget conveniences

```python
loc.check()                         # checkbox / radio
loc.uncheck()
loc.select_option(text="Large")     # combo box
loc.select_tab(text="Index")        # tab bar
loc.select_item(text="Inbox")       # item view row
loc.spin(3)                         # spin box, +3 steps
win.menu("File > Export > PDF...").trigger()
```

!!! info "These are clicks, not state writes"
    `select_tab` clicks the tab's rectangle; `select_option` clicks the combo open and clicks the
    entry in its popup; `menu().trigger()` walks the path by clicking each menu open. They
    therefore **fail where a user would fail** — a tab scrolled out of reach, an entry in an
    overlong menu.

## Native versus synthetic

| | Native (default) | Synthetic |
|---|---|---|
| Delivered via | `QWindowSystemInterface` | `QApplication::sendEvent` to one widget |
| Hit-testing, hover, implicit grab | yes | no |
| Popup dismissal, focus-on-click | yes | no |
| Modal blocking respected | yes | no |
| Widget sees `spontaneous()` | yes | no |
| Can reach an off-screen or covered widget | no | **yes** |

```python
app.set_input_mode("synthetic")     # session default
loc.click(mode="synthetic")         # this call only
```

Every input-ish action takes a per-call `mode=`, including `Window.mouse.*` and
`Window.keyboard.*`.

**Use native by default.** Reach for synthetic when the target is one a user could not reach and
you have decided that is acceptable — a tabified dock parked at negative coordinates while its tab
is not current, for instance.

### The deliberate exception

```python
loc.fill("some value")              # writes the property, for cheap setup
```

`fill` writes the property directly rather than typing -- `input.set_text` on the wire. It
**refuses a read-only widget**, because succeeding where a user could not type is a false
pass.

## Reading state

```python
loc.text
loc.value
loc.is_visible
loc.is_enabled
loc.is_checked
loc.count
loc.geometry                        # (x, y, width, height), relative to the parent
loc.properties()                    # every Q_PROPERTY
loc.methods()                       # everything the meta-object can call
```

## Item views, tables and trees

A cell is addressed by a composite handle rather than a selector, because cells are not objects:

```python
table = win.locator("QTableView")
table.row(has_text="Widget A").click()
table.cell(row=2, column=1).text
table.row(has_text="Widget A").context_menu("Delete")
```

`row()` and `cell()` scroll their own view as part of addressing a cell, so you do not need
`scroll_into_view()` for them.

## Actionability

Before acting, the agent checks: **exists → visible → enabled → not obscured → has geometry**.

A failure names what was wrong, and in native mode it will tell you *what* is covering the target
— an obscuring widget, a blocking modal, a position outside the window.

!!! note "It detects, it does not yet adjust"
    Actionability names an obscuring widget but does not currently move the click point to an
    uncovered part of the target.

## The escape hatches

When no locator is the right target — a custom-painted canvas, a chart area:

```python
win.mouse.click(x=420, y=310)
win.mouse.move(x=100, y=100)
win.keyboard.type("text")
win.keyboard.press("Escape")
```

Coordinate-based interaction is supported because sometimes there is genuinely no object. It is
deliberately not the ergonomic path — prefer locators, which survive layout changes.

## When the meta-object is the only way in

A widget that paints its own contents has **nothing in the object tree** — no per-item objects, no
properties of your interest. For those, the meta-object is the door:

```python
loc.methods()                       # discover slots and Q_INVOKABLEs
loc.invoke("setZoom", 2.0)          # call one
```

And when even that is not enough, `call_native()` resolves an exported symbol in a module the
application already loaded. It is genuinely sharp — a wrong signature is an access violation, not
an exception — and it exists for cases like driving an embedded NLview schematic canvas.

See the full [actions matrix](../ACTIONS.md) for per-method, per-mode detail.
