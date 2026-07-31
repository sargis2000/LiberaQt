# qtdriver — Selectors and the Object Map

## 1. Two equivalent forms

```python
win.locator("QPushButton#okButton")                     # string mini-language
win.locator(type="QPushButton", objectName="okButton")  # keyword form
win.locator({"type": "QPushButton", "props": {"objectName": "okButton"}})  # raw node
```

All three compile to the same `SelectorNode` JSON that goes over the wire. The string form exists
because it is compact and greppable; the keyword form because it handles values that would need
escaping.

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
QLabel[text~='^Total: \d+']
QListView:has(QLabel[text='Inbox'])
```

Type matching uses `QMetaObject::inherits`, so `QAbstractButton` matches `QPushButton`,
`QCheckBox`, and your `MyFancyButton`. Exact-class matching is `QPushButton!` (trailing bang).

## 3. Attribute keys

Any `Q_PROPERTY` on the object is addressable, plus these synthetic keys:

| Key | Meaning |
| --- | --- |
| `text` | Normalised display text: `text`, `title`, `windowTitle`, `plainText`, or `currentText`, whichever the class provides. Mnemonic `&` is stripped. |
| `accessibleName` | `QAccessible` name, falling back to `accessibleName` property. |
| `qmlId` | The QML `id` inside its declaring `QQmlContext` (Quick only). |
| `path` | Slash-joined `objectName` chain from the window root — a last-resort stable-ish key. |
| `index` | Index among siblings of the same type. |
| `role` | Coarse semantic role: `button`, `input`, `list`, `checkbox`, ... Useful for cross-toolkit-style tests that survive a widget-to-QML port. |

Text comparison is exact by default, whitespace-normalised, and `&`-stripped. Use `*=` for
substring or `~=` for regex.

## 4. QWidget vs QML — one API

The same selector language covers both trees. `qtdriver` splices the Quick scene graph into the
object tree, so a `QQuickWidget` embedded in a widget hierarchy is traversed transparently:

```python
win.locator("QQuickWidget Button[text='Save']").click()
```

QML types report their QML type name (`Button`, `TextField`, `MyDelegate`), not the C++ class,
because that is what the test author sees in the .qml file. The C++ class is still matchable
(`QQuickButton`) for cases where the QML name is generic.

## 5. Deterministic ordering and strictness

* Candidates are collected depth-first, in child-insertion order — stable across runs.
* A locator resolving to >1 object raises `AmbiguousSelector`. Opt out with `.first`, `.last`,
  `.nth(i)`, or `strict=False`.
* `.filter(has=..., has_text=...)` narrows client-side without a second round trip: the filter is
  compiled into the same selector node.

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

The recorder writes new entries into this file instead of inlining strings, and `qtdriver inspect`
can validate that every entry still resolves against a running app — a cheap way to find selectors
broken by a refactor before the whole suite goes red.

## 8. Anti-patterns the tool actively discourages

* Coordinate-only clicks — supported (`win.mouse.click(x, y)`) but flagged by the linter.
* Index-only selectors (`QPushButton:nth(3)`) — the recorder emits a `# TODO: brittle` comment.
* Depending on `path` when no `objectName` is set anywhere.

The single highest-leverage thing a team can do is set `objectName` on interactive widgets. The
docs should say this loudly, and `qtdriver inspect --coverage` reports the percentage of
interactive objects that have one.
