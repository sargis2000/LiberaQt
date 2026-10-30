

# LiberaQT — Selectors and the Object Map

## 1. Two equivalent forms

```python
win.locator("QPushButton#okButton")                       # string mini-language
win.locator(type="QPushButton", objectName="okButton")    # keyword form
win.locator({"type": "QPushButton", "objectName": "okButton"})   # the same keywords, as a dict
```

All three compile to the same selector JSON that goes over the wire
(`{"steps": [{"type": "QPushButton", "objectName": "okButton"}], ...}`, see `PROTOCOL.md`). The
string form exists because it is compact and greppable; the keyword form because it needs no
quoting. In the keyword and dict forms, `type` and `objectName` are structural, `index=` means
`:nth()`, a boolean `visible=True` / `enabled=True` / `checked=True` / `focused=True` is the
pseudo-class, and every other key is an exact property match. A single step only: use the string
form for `>` and descendant chains.

## 2. Grammar

```
selector    := step ( combinator step )*
combinator  := ">"   (direct child)
             | " "   (any descendant)
step        := [ type ] [ "#" objectName ] { attr } { pseudo }
type        := ClassName | "*"          ; QMetaObject class name, e.g. QPushButton, MyCustomView
attr        := "[" key op value "]"
op          := "="   exact
             | "*="  contains
             | "^="  starts with
             | "$="  ends with
             | "~="  regex
             | "!="  not equal
pseudo      := ":visible" | ":enabled" | ":checked" | ":focused"
             | ":nth(N)" | ":first" | ":last"
             | ":has(SELECTOR)" | ":parent(SELECTOR)"
```

Examples:

```
QDialog#settings > QPushButton[text='Apply']
QTableView QLineEdit:visible
*[accessibleName^='Volume']:enabled
QLabel[text~='^Total: \\d+']
QListView:has(QLabel[text='Inbox'])
```

Type matching walks `QMetaObject::superClass()`, so `QAbstractButton` matches `QPushButton`,
`QCheckBox`, and your `MyFancyButton`. Exact-class matching is `QPushButton!` (trailing bang).

**Quoted values and backslashes.** A value may be quoted with `'` or `"`; inside the quotes a
backslash escapes the next character and is removed. That is why the regular expression above
doubles its backslash: written `\d+`, it would reach the agent as `d+` and match nothing, with no
error. In a Python string, use a raw string: `r"QLabel[text~='^Total: \\d+']"`.

## 3. Attribute keys

Two keys are special, and every other key is read as a Qt property with `QObject::property()`:

| Key | Meaning |
| --- | --- |
| `text` | Display text: the first non-empty of `text`, `title`, `windowTitle`, `plainText`, `currentText`, `displayText` and `placeholderText`. Mnemonic `&` is stripped and whitespace collapsed. Because of the `placeholderText` fallback, an empty field whose placeholder is "Search" has the text "Search". |
| `objectName` | `QObject::objectName()`. Same as `#name`, for names that are not identifiers. |
| anything else | `QObject::property(key)`, converted to a string. Any `Q_PROPERTY` works (`toolTip`, `accessibleName`, `checkable`, `placeholderText`, `windowTitle`), and so does a dynamic property. A key that is not a property reads as empty and matches nothing -- no error. |

`=` and `!=` compare with whitespace collapsed on both sides; `*=`, `^=` and `$=` are exact
substring tests; `~=` is a `QRegularExpression` search (anchor it with `^` and `$` yourself).

!!! failure "Planned keys that do not exist yet"
    Earlier revisions of this document listed `qmlId`, `path`, `index` and `role` as synthetic
    keys. None is implemented: each is looked up as a Qt property, finds none, and matches nothing.
    For position, use `:nth(N)` or `index=` in the keyword form.

## 4. QWidget vs QML — one API

The same selector language covers both trees. `liberaqt` splices the Quick scene graph into the
object tree, so a `QQuickWidget` embedded in a widget hierarchy is traversed transparently:

```python
win.locator("QQuickWidget Button[text='Save']")
```

A QML-defined type's C++ class name is mangled (`Button_QMLTYPE_0`), so the engine also matches
the declared QML type name (`Button`, `TextField`, `MyDelegate`) -- what the test author sees in
the .qml file. The C++ class, and every class it inherits (`QQuickItem`), still matches too.
This needs an agent built with Qt Quick: check `app.capabilities["quick"]`.

!!! warning "Finding a Quick item is not the same as driving it"
    Discovery and property reads work. Mouse input, `highlight()` and screenshots at a Quick item
    raise `UnsupportedOperationError` in 0.1.x, keys return without checking where they went,
    and `geometry` is `()`. See [Troubleshooting](troubleshooting.md#qml-discovery-works-input-mostly-does-not).

## 5. Deterministic ordering and strictness

* Candidates are collected depth-first, in child-insertion order — stable across runs.
* A locator resolving to >1 object raises `AmbiguousSelectorError`. Opt out with `.first`,
  `.last` or `.nth(i)`. There is no `strict=False`: keyword arguments to `locator()` are an
  alternative *to* a selector string, so passing both raises `InvalidSelectorError`.
* `.filter(has=..., has_text=...)` narrows without a second round trip: the filter is compiled
  into the same selector and evaluated by the agent. `has_text` means *contains*.
  `.filter(has_not=...)` is accepted but **ignored** in 0.1.x -- the agent skips it, so it matches
  what the unfiltered locator matches.

## 6. Chaining

```python
row = win.locator("QTableView#orders").row(has_text="INV-1042")
row.locator("QPushButton[text='Void']").click()
```

Chained locators produce nested `SelectorNode`s with `root` set to the parent's resolved handle at
action time, so intermediate resolution failures are retried too.

## 7. Object map

Large suites should not embed selectors inline. `objects.yaml`:

```yaml
# objects.yaml
login:
  username: "QLineEdit#usernameField"
  password: "QLineEdit#passwordField"
  submit:   "QPushButton[text='Log in']"
  error:    "QLabel#loginError"

orders:
  table:  "QTableView#ordersTable"
  new:    "QToolButton[toolTip*='New order']"
```

```python
app = qd.launch("./app", object_map="objects.yaml")
win.obj("login.submit").click()
```

Object maps need PyYAML: `pip install "liberaqt[yaml] @ git+..."`. `liberaqt inspect --validate
objects.yaml` checks that every entry still resolves uniquely against a running app, and exits
non-zero when one does not -- a cheap way to find selectors broken by a refactor before the whole
suite goes red. (The recorder is designed to write entries here too, but is not wired up yet.)

## 8. Anti-patterns

* Coordinate-only clicks -- supported (`win.mouse.click(x, y)`) for widgets that paint their own
  contents, and nothing else.
* Index-only selectors (`QPushButton:nth(3)`) -- `liberaqt inspect` marks them
  `positional (N of this type)`, because the order of siblings is a layout detail.

The single highest-leverage thing a team can do is set `objectName` on interactive widgets.
`liberaqt inspect` ends with a summary that counts the objects addressable by `objectName` and by
text, and says how many have no stable identifier at all.
