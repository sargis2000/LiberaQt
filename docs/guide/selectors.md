# Selectors

A selector describes objects in the application's live `QObject` tree. The syntax is CSS-shaped,
but it matches Qt classes and properties rather than HTML.

```python
win.locator("QPushButton#okButton")                          # type + objectName
win.locator("QDialog#settings > QPushButton[text='Apply']")  # hierarchy
win.locator("QTableView QLineEdit:visible:nth(1)")           # pseudo-classes
win.locator("*[accessibleName^='Volume']:enabled")           # any type, prefix match
win.locator("QListView:has(QLabel[text='Inbox'])")           # containment filter
```

## Grammar

```
selector    := step ( combinator step )*
combinator  := '>' | whitespace
step        := [type[!]] ['#' objectName] attr* pseudo*
attr        := '[' key op value ']'
op          := '=' | '*=' | '^=' | '$=' | '~=' | '!='
pseudo      := ':visible' | ':enabled' | ':checked' | ':focused'
             | ':first' | ':last' | ':nth(N)'
             | ':has(SELECTOR)' | ':parent(SELECTOR)'
```

| Piece | Meaning |
|-------|---------|
| `QPushButton` | class name, matching subclasses too |
| `QPushButton!` | **exact** class, no subclasses |
| `#name` | `objectName` |
| `[key='v']` | property equals |
| `[key*='v']` | contains |
| `[key^='v']` | starts with |
| `[key$='v']` | ends with |
| `[key~='v']` | matches a regular expression |
| `[key!='v']` | not equal |
| `A > B` | B is a **direct** child of A |
| `A B` | B is a descendant of A at any depth |

Parsing happens client-side, so a syntax error is a Python traceback rather than an opaque agent
error.

## Four rules that trip people up

### Type matching walks the inheritance chain

`matchesType` follows `superClass()`, so `QWidget` matches every widget, and a custom
`MyButton : QPushButton` is matched by `QPushButton`.

!!! warning "Uniqueness cannot be computed from a tree dump"
    A dump showing one `QPushButton` and one `MyButton` still means `QPushButton` matches **two**
    objects. Always ask the engine — which is what `liberaqt inspect` and
    [`suggest`](../reference/api.md) do.

Use `!` when you mean the class and nothing derived from it:

```python
win.locator("QPushButton!")     # not MyButton
```

### A window is never inside its own subtree

A search rooted at a window handle **excludes** that window, so the tree root always reports zero
matches for it. Reach windows through the application:

```python
app.window(title="Settings")            # correct
win.locator("QDialog#settings")         # will not find the window itself
```

### A type name may be namespaced

`QMetaObject::className()` reports the fully qualified name, so real applications need `ns::Class`:

```python
win.locator("qdesigner_internal::NewFormWidget")
win.locator("Aqwidget::AQDockWidget")
```

A single `:` still starts a pseudo-class; only `::` followed by an identifier continues the type
name.

### An objectName is not necessarily an identifier

Applications ship names with spaces and slashes, which cannot follow a `#`. Use the attribute form:

```python
win.locator("FormWidget[objectName='comment/context view']")
```

## Filters versus navigation

These look similar and do opposite things.

**`:has(X)` and `:parent(X)` are filters** — they return the object on the *left*:

```python
win.locator("QPushButton:parent(QDockWidget)")   # a button, not a dock
win.locator("QListView:has(QLabel[text='Inbox'])")  # a list view, not a label
```

**`parent()` and `ancestor()` navigate upwards** — they return a different object:

```python
row.parent()                       # one level up
view.ancestor("QDockWidget")       # climbs until something matches
```

Every selector searches downwards; these two are the only way up. Both resolve immediately rather
than lazily, because the answer is one specific object.

!!! tip "`parent()` is usually not what you want"
    One level up from a view is typically a layout or a viewport — not something you would point
    at. `ancestor("QDockWidget")` climbs until it matches, which is normally the intent.

!!! warning "Ancestor matching walks the inheritance chain too"
    `ancestor("QDockWidget")` may return a subclass — `Aqwidget::AQDockWidget` in Libero SoC.
    **Compare handles, not class names.**

## Chaining and scoping

```python
dock = win.locator("QDockWidget").first
dock.locator("QPushButton")        # buttons inside that one dock
```

Chaining off a **narrowed** locator (`.first`, `.last`, `.nth(i)`, or an element of `.all()`)
scopes the search to the object it found. Chaining off a locator that has not been narrowed
appends a step to the selector instead.

## Choosing among matches

```python
win.locator("QPushButton").count       # how many
win.locator("QPushButton").first       # deliberate choice
win.locator("QPushButton").nth(2)
win.locator("QPushButton").all()       # eager: one locator per match
```

Locators are strict by default: resolving one that matches several objects raises
`AmbiguousSelectorError` rather than silently picking the first. Narrowing turns strictness off,
because the ambiguity is now intended.

## Filtering without a round trip

```python
win.locator("QListView").filter(has_text="Inbox")
win.locator("QWidget").filter(has="QPushButton[text='OK']")
win.locator("QWidget").filter(has_not="QLabel")
```

The conditions are folded into the selector and evaluated by the agent, so filtering costs nothing
beyond the search that was already going to happen.

## Finding selectors for a real application

```bash
liberaqt inspect "C:\Path\To\app.exe"
liberaqt inspect "C:\Path\To\app.exe" --depth 3
liberaqt inspect "C:\Path\To\app.exe" --json
```

`inspect` ranks candidate selectors per object and verifies uniqueness **with the engine**, so
what it prints actually resolves.

See also the full [selector grammar reference](../SELECTORS.md).
