---
name: qml-support
description: The measured state of LiberaQT's Qt Quick / QML support - what works (locators, object.tree, Window.kind, rootless searches across both trees), what does not (mouse input, highlight and screenshots at a QQuickItem, and typing that silently "succeeds"), why the old "QML is deferred" impression was really three bugs, the build-time Quick switch (LIBERAQT_HAVE_QUICK), where to start on Quick input, and how to test against qmleasing without flaky counts. Use whenever work or a question touches QML, Qt Quick, QQuickItem, QQuickWindow, quick_backend.cpp, the quick.* commands, qmleasing or tests/e2e/qt/test_qml_live.py - including "why does click raise unsupported on this QML button", "does LiberaQT support QML" and any plan to make Quick input work.
---

# Qt Quick support, as measured

Everything here is measured against `qmleasing` (Qt 6.7.3), a real Qt tool that puts a QWidget
shell and a live Quick window in one process, and is pinned by `tests/e2e/qt/test_qml_live.py`.
QML support has moved faster than its documentation more than once, so re-measure before relying
on any line of this after a change.

## First: does this agent have Quick at all?

`agent/CMakeLists.txt` compiles `quick_backend.cpp` only when `find_package(Qt Quick Qml)`
succeeds. Without it `LIBERAQT_HAVE_QUICK` is undefined, the Quick splice compiles out, and every
QML failure looks like a bug in code that was never built. CI's Qt 5.15 legs install no
`qtdeclarative`, so those agents have no Quick support at all. An agent built without Quick also
registers fewer commands, so check `app.capabilities["commands"]` before debugging anything else.

## Discovery works

- Locators reach Quick objects: `QQuickRectangle`, `QQuickText` and QML-defined types such as
  `Button_QMLTYPE_0` resolve, and their properties read back.
- `object.tree` reaches them too, and so does `liberaqt inspect`.
- `Window.kind` reports `"quick"` for a `QQuickWindow` and `"widget"` for a QWidget window.
- Rootless searches find objects across both trees.

Both the engine and the tree walk step from a `QQuickWindow` into its `contentItem` through one
function, `SelectorEngine::visualChildren`. Any new walk must go through it rather than around it,
because every discovery bug below was a walk that did not.

### Why it used to look unsupported

The long-standing "QML is deferred" impression was three separate bugs, not a missing feature.
Each is the kind of thing a new walk or query can reintroduce:

1. `dumpTree` had its own child walk that never stepped into `contentItem`, *and* dropped anything
   that was neither `QWidget` nor `QWindow` (a `QQuickItem` is neither). The tree reported one
   bare node where the engine found forty objects, so `liberaqt inspect` printed "no objects
   found" for a window full of them.
2. `Window.kind` answered `"widget"` for every window in existence, because only `window.list`
   ever sent `window_type`, and `kind` reads `object.info`.
3. `rootObjects()` enumerated only top-level *windows*, so a rootless search found zero widgets in
   an application full of them.

Fixing the third left a rule worth keeping: `topLevelWidgets()` means "is a window", not "has no
owner". A dialog, a menu and a popup are all windows *and* QObject children of the widget that
owns them, so they are skipped as roots. Otherwise their subtrees are walked twice and every
widget inside is reported two times over.

## Input does not work, and it fails unevenly

This is the real remaining gap. Its unevenness matters more than the gap itself, because part of
it fails silently:

| At a `QQuickItem` | Result |
|---|---|
| `click`, `hover`, `wheel`, `drag_to`, also with `mode="synthetic"` | raise `unsupported` at once (not retried, so no timeout wrapper) |
| `highlight()` | raises `unsupported` |
| `type()`, `press()`, and `Window.keyboard.type()` at a Quick window | **return normally and type into a different window** (below) |
| screenshot of a Quick item | **returns the active window's image**, no error |
| screenshot of a Quick window, no handle | raises `unsupported` ("nothing to grab") |
| `geometry` | `()` |

**Typing at a Quick item edits the widget window.** `keyTarget()` (`agent/src/input_synth.cpp`)
handles a `QWidget`; for anything else it falls back to `implicitKeyWindow()`, which only knows
widget windows, and sends the keys to that window's focus widget. Measured on qmleasing (6.7.3
MinGW and 5.15.0 MSVC x86): `type("9")` aimed at a `QQuickText` changed the widget shell's
`QSpinBox` from 500 to 9500, and the call reported success. F11's refusal of a non-focusable
target is widget-only, so it never sees this. Treat any passing keyboard call at a Quick item as
silent corruption of another window, never as evidence that keyboard input works.

**A screenshot of a Quick item** goes through `Screenshot::grab`, which resolves with
`resolveOrNull` and, finding no widget, falls back to `QApplication::activeWindow()`: you get the
shell's image. This is the `resolveRoot` bug class.

### Where to start on Quick input

The click is refused first in `targetWidget()` (`agent/src/input_synth.cpp`), which casts the
target to `QWidget*` and throws `unsupported` before the input mode is even read. Behind that
gate everything on the mouse path is widget-only: `Target`/`locate`, `requireWindow`,
`ensureActive`, `enterWindow`, and the click-point helper in `agent/src/widget_backend.cpp`
(`TODO(m2): QQuickItem -> mapToScene(boundingRect().center())`). The `compat::post*` input
functions already take a `QWindow*`, and a `QQuickWindow` is one, so no protocol or `compat.h`
change is needed to post the events. `QuickBackend::describe` computes geometry but nothing calls
it, which is why `geometry` is `()`; note it uses scene coordinates where widgets report
parent-relative ones.

Qt Quick hit-tests differently from widgets: an item that accepts no buttons does not block a
click, Qt 6 pointer handlers (`TapHandler`) are invisible to an item-only hit test, and Quick does
not clip hit-testing to the window. So copying the widget "obscured" check would refuse clicks a
user can make, and an off-window check is needed that widgets get for free.

Fix the keyboard leak and the screenshot fallback in the same change. This repository's bugs come
in pairs because patterns get copied, and a Quick mouse path next to a keyboard path that still
types into another window is half a fix.

The four `quick.*` commands are unregistered. `add-agent-command` has how far each is from done
(`quick.evaluate` is one `registerCommand` line away).

## Testing against qmleasing

- Follow the `easing` fixture in `tests/e2e/qt/test_qml_live.py`: it launches qmleasing through
  `qt_tool`, and skips when the tool is absent or does not open both its windows.
- **Never assert an exact object count.** qmleasing renders a grid of curve previews whose number
  tracks the window size: 155 objects at 669 px tall, 355 at 720 px. Assert relationships instead
  ("the tree finds at least what the engine finds", "more than one").
- `--liberaqt-qt-bin` selects the Qt, as for every Qt suite. Against an agent built without
  `qtdeclarative`, the Quick tests fail for the build reason above, not because of your change.

## After changing QML behaviour

Update every description of the old state: the QML summary in CLAUDE.md's project state, this
skill, `docs/troubleshooting.md`, the open-defect list in `.claude/agents/qt-automation-tester.md`,
and `plugins/liberaqt/skills/write-tests/references/limitations.md`, then bump the plugin version.
`fix-finding` step 7 has the full list.
