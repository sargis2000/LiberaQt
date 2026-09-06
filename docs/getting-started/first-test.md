# Your first test

We will drive **Qt Assistant**, the documentation browser that ships with every Qt installation.
It is a real application written with no knowledge of this project, which is exactly the point —
a toy application agrees with whatever the driver happens to do.

=== "Windows"

    ```
    C:\Qt\6.7.3\mingw_64\bin\assistant.exe
    ```

=== "Linux"

    ```
    /usr/lib/qt6/bin/assistant
    ```

!!! success "Everything on this page is executed as a test"
    The code below lives in `e2e/test_docs_examples.py` and runs against a real Qt Assistant.
    Writing that file caught six mistakes in an earlier draft of this page — including two
    selectors that never existed.

## Step 1 — look before you write

Never write selectors from imagination. Ask the application what is in it:

```bash
liberaqt inspect "C:\Qt\6.7.3\mingw_64\bin\assistant.exe" --depth 3
```

This launches the application, walks its object tree, and prints candidate selectors with a
verdict on each — verified by the selector engine, not guessed:

```title="Output (abridged)"
=== <Window 'Qt Assistant'> ===
SELECTOR                                        TEXT                  STATUS
CentralWidget                                                         unique
QToolButton[text='Previous']                    Previous              unique
QCheckBox[text='Case Sensitive']                Case Sensitive        unique
HelpViewer                                                            unique
QDockWidget#IndexWindow                         Index                 unique
QDockWidget[objectName='Open Pages']            Open Pages            unique
QToolButton:nth(0)                                                    positional (30 of this type) - set an objectName
QLineEdit:nth(0)                                                      positional (5 of this type) - set an objectName
```

Read the **STATUS** column before anything else:

- `unique` — safe to use.
- `positional (N of this type)` — the only way to address it is by index, which breaks the moment
  the layout changes. Prefer a different anchor, or ask the application's developers for an
  `objectName`.

!!! tip "Uniqueness cannot be eyeballed"
    Type matching walks the inheritance chain, so `QPushButton` also matches every subclass of it.
    A tree dump showing one `QPushButton` and one `MyButton` still means `QPushButton` matches
    **two** objects. `inspect` asks the engine, which is the only reliable answer.

## Step 2 — the smallest thing that works

```python title="test_assistant.py"
from liberaqt import liberaqt, expect

ASSISTANT = r"C:\Qt\6.7.3\mingw_64\bin\assistant.exe"


def test_assistant_opens():
    with liberaqt(default_timeout=15.0) as lq:
        app = lq.launch(ASSISTANT, timeout=90.0)
        win = app.window(title="Qt Assistant")

        expect(win.locator("HelpViewer")).to_be_visible()
```

```bash
pytest test_assistant.py -v
```

`liberaqt()` is a context manager: it launches the application with the agent injected, waits for
the agent to call home, and terminates everything on the way out — even if the test fails.

## Step 3 — a fixture, so each test does not relaunch

```python
import pytest
from liberaqt import liberaqt, expect

ASSISTANT = r"C:\Qt\6.7.3\mingw_64\bin\assistant.exe"


@pytest.fixture(scope="module")
def app():
    with liberaqt(default_timeout=15.0) as lq:
        application = lq.launch(ASSISTANT, timeout=90.0)
        application.set_input_mode("native")   # (1)!
        yield application


@pytest.fixture
def win(app):
    return app.window(title="Qt Assistant")    # (2)!
```

1. Native input goes through Qt's own input path, so the application reacts as it would to a
   person. It is the default; setting it explicitly documents the intent.
2. A `Window` does not know its `Application`, so keep both fixtures if you need `app` later —
   for `app.capabilities` or a second window.

## Step 4 — do something

```python
def test_the_find_bar_opens_and_its_checkbox_toggles(win):
    win.menu("Edit > Find in Text...").trigger()   # (1)!

    box = win.locator("QCheckBox[text='Case Sensitive']")
    box.check()
    expect(box).to_be_checked()
    box.uncheck()
    expect(box).to_be_checked(False)
```

1. The Find bar does not exist on screen until it is asked for, so its checkbox is **not
   actionable** before this line. That is not a limitation — a user could not click it either.

Menu paths are matched exactly:

```python
win.menu("View > Zoom in").trigger()      # correct
win.menu("View > Zoom In").trigger()      # fails: no such entry
```

When it fails, the agent lists what really is there, so you rarely have to go looking:

```
no menu entry 'Zoom In' under View ; there is: Zoom in, Zoom out, Normal Size, Contents, ...
```

## Step 5 — the gotcha worth learning early

```python
def test_type_into_the_index_filter(win):
    # The Index dock is tabified with Contents and Search. A widget in a dock whose tab is not
    # current has no on-screen position, so it is not actionable. Bring it to the front first.
    for bar in win.locator("QTabBar").all():   # (1)!
        if bar.is_visible:
            bar.select_tab(text="Index")
            break

    field = win.locator("QDockWidget#IndexWindow").first.locator("QLineEdit").first
    field.click()                              # (2)!
    field.type("signal")
    assert "signal" in field.text
```

1. Not `.first` — Assistant has two `QTabBar`s and the first one is **hidden**. Iterating for a
   visible one is the pattern that survives contact with real applications.
2. Clicking first is not ceremony. A press activates an inactive window, and
   `QApplication::focusWidget()` is null while the application is not in the foreground — so
   typing without clicking lands nowhere.

Note `.first.locator(...)` scopes the inner search to that one dock, rather than searching the
whole window again.

## Step 6 — assert like a user

```python
expect(win.locator("HelpViewer")).to_be_visible()
expect(win.locator("QDockWidget#ContentWindow")).to_be_visible()
expect(win.locator("QToolButton[text='Next']")).to_exist()
```

`expect` **retries** until the condition holds or the timeout expires. A plain `assert` reads the
value once, which in a GUI is a race you will eventually lose.

## What you never had to write

No `sleep`. No coordinates. No polling loop. Every action already:

1. resolves the selector to exactly one object, retrying at 50 ms;
2. checks it is actionable — exists, visible, enabled, not obscured, has geometry;
3. performs the action;
4. waits for the UI to settle before returning.

## Next

- [How it works](concepts.md) — the mental model behind the auto-waiting
- [Selectors](../guide/selectors.md) — the full grammar and the four rules that trip people up
- [Actions](../guide/actions.md) — native versus synthetic, and what each can reach
- [Running under pytest](../guide/pytest.md) — fixtures, configuration, failure diagnostics
