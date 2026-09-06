# Object maps

An object map gives selectors symbolic names in a YAML file, so a UI change is a one-line fix
instead of a hunt through the suite.

## The file

```yaml title="objects.yaml"
# The update prompt shown before the main window exists.
startup:
  dismiss: "QPushButton[text='No']"

wizard:
  project_name: "QLineEdit#projectName"
  project_location: "QLineEdit#projectLocation"
  next: "QPushButton[text='Next']"
  finish: "QPushButton[text='Finish']"

main:
  hierarchy: "QTreeView#hierarchyView"
  flow: "Flowview::View"
```

Nested keys are flattened with dots, so `wizard.next` addresses the entry above.

## Using it

```python
app = lq.launch(EXE, object_map="objects.yaml")
win = app.window(title="My Application")

win.obj("wizard.project_name").fill("demo")
win.obj("wizard.next").click()
```

`obj()` returns an ordinary `Locator`, so everything else works on it unchanged:

```python
win.obj("main.hierarchy").first.row(has_text="counter.v").context_menu("Set As Root")
```

Selectors are resolved **against the window you ask**, so the same short name works for whichever
dialog is in front — a dialog's objects are not in the main window, and that is not an error.

## Why bother

- **One place to fix.** When a vendor renames a button, you edit one line.
- **Tests read like intent.** `win.obj("wizard.finish").click()` says what it is doing;
  `win.locator("QPushButton[text='Finish']").click()` says how.
- **The map can be checked.** See below.

## Validating a map

Selectors rot when the application changes. Check them against a live application rather than
finding out mid-suite:

```bash
liberaqt inspect "C:\Path\To\app.exe" --validate objects.yaml
```

```title="Output"
  ok         startup.dismiss              QPushButton[text='No']
  ok         wizard.project_name          QLineEdit#projectName
  AMBIGUOUS  wizard.next                  QPushButton[text='Next']  (2 matches)
  MISSING    main.flow                    Flowview::View

10/12 entries resolve uniquely
```

An entry counts as valid if it resolves uniquely in **some** window. The command exits non-zero
when anything is missing or ambiguous, so it works in CI as a canary against a vendor upgrade.

## Writing a good map

!!! tip "Prefer stable attributes"
    `objectName` is the most stable thing an application gives you. Visible text changes with
    translations and product decisions; position changes with layout.

!!! warning "An objectName is not necessarily an identifier"
    Applications ship names with spaces and slashes, which cannot follow a `#`. Use the attribute
    form in the map:

    ```yaml
    context_view: "FormWidget[objectName='comment/context view']"
    ```

!!! note "Comment the awkward ones"
    A map is the right place to record *why* a selector is strange — a duplicate button that
    needed disambiguating, a namespaced vendor class. Future-you will not remember.

## Generating a starting point

```bash
liberaqt inspect "C:\Path\To\app.exe" --depth 4
```

`inspect` ranks candidate selectors per object and asks the engine whether each one is actually
unique, so what it prints resolves. Paste the useful lines into a map and give them names.
