# The action surface: native vs synthetic

Every interactive method the client offers, and what it actually does in each delivery mode.
`integration/test_actions.py` and `integration/test_input.py` assert the claims below against
real applications; when this table and those tests disagree, the tests are the ones that ran.

The two modes, in one sentence each:

* **native** (the default) hands events to Qt at the seam a platform plugin uses, so Qt routes
  them exactly as it routes a real user's input — hit-testing, hover, focus-on-click, the
  implicit grab, double-click derivation, window activation, modal blocking.
* **synthetic** delivers an event straight to one widget with `QApplication::sendEvent`; the
  widget's handler runs and nothing around it does.

The session default is set with `Application.set_input_mode()` / `--liberaqt-input-mode` /
`LiberaQt(input_mode=...)`; every method below also takes a per-call `mode=` override. Wire
details live in `docs/PROTOCOL.md`.

## Pointer and keyboard actions

| Method | native (default) | `mode="synthetic"` | The difference that matters |
| --- | --- | --- | --- |
| `click` / `double_click` | yes | yes | Native focuses what it clicks, activates an inactive window first, and derives the double click from two presses. A synthetic click runs the handler only: focus does **not** follow it, so `click()` then `type()` types wherever focus already was. |
| `right_click` | yes | yes | Both post the separate `QContextMenuEvent` a real right-click produces, so context menus open in either mode. |
| `hover` | yes | partial | Native updates enter/leave and hover state; synthetic delivers one move event and no bookkeeping, so hover-dependent UI (tooltips, hover highlights) may not react. |
| `type` | yes | yes | Both send per-character key events, and Squish-style `<Ctrl+A>` chords embedded in the text work in both. Native aims at the *window* and Qt gives the keys to its focus widget; synthetic aims at the named widget directly. Keyboard shortcuts fire in both (measured). |
| `press` | yes | yes | Same delivery split as `type`. |
| `wheel` | yes | yes | Synthetic is rerouted to a scroll area's viewport by hand; native hit-tests there itself. |
| `drag_to` / `Mouse.drag` | yes | yes | Native holds the implicit press-to-release grab, as a real drag does. |
| `Mouse.*` / `Keyboard.*` | yes | yes | Follow the **session** mode; no per-call `mode=` yet. |

## Selection actions — clicking on the native path

| Method | native (default) | `mode="synthetic"` | The difference that matters |
| --- | --- | --- | --- |
| `select_tab` | clicks the tab's rectangle on the bar | writes `setCurrentIndex` | Native refuses a tab the bar has scrolled out of reach — synthetic is the documented way to switch to one anyway. Works on `QTabWidget`, `QTabBar`, and the bar Qt creates for tabified docks. |
| `select_option` (combo) | clicks the combo open, clicks the entry in its popup | writes `setCurrentIndex` | Native fires everything a user's choice fires, `activated` included; `setCurrentIndex` emits `currentIndexChanged` but **not** `activated`, and no popup ever opens. |
| `select_item` (view) | scrolls into view and clicks the item | writes current index + selects the row | The native selection is whatever the view's own click policy produces (rows, cells, toggle); synthetic always selects the full row. |
| `spin` | clicks the arrow subcontrol | clicks the same point via `sendEvent` | The arrow position comes from the widget's `QStyle` in both modes. |
| `set_checked` / `check` / `uncheck` | clicks when the state differs | same, with a synthetic click | Pure sugar over `click`; a radio button cannot be *un*checked by clicking in either mode, because a user cannot either. Checkboxes and radio buttons are aimed at the style's *click rect*, not the widget centre — a layout routinely stretches the widget far wider than its clickable region, and the geometric centre of Designer's 529px-wide startup checkbox is a dead zone a user's click would miss too. |
| `menu(...).trigger()` | clicks each menu open along the path | queues `QAction::trigger` | Native runs `aboutToShow` and hover for real; synthetic opens nothing on screen. **Both** resolve the path before anything opens, so entries a menu only creates inside `aboutToShow` (e.g. "Recent Files" lists) are not addressable yet in either mode. |

## Deliberately not input, in any mode

| Method | Mechanism | Why |
| --- | --- | --- |
| `fill` / `clear` | writes the `text` property | One change signal instead of one per key, for setting up state that is not itself under test. Refuses a read-only field — succeeding where a user could not type is a false pass. |
| `locator[...] = value` | writes any property | Escape hatch by design. |
| `invoke` / `evaluate` | calls slots / `Q_INVOKABLE` | Introspection and setup, not interaction. |

## Not usable yet — either mode

| Surface | State |
| --- | --- |
| `scroll_into_view` | Unimplemented; raises `UnsupportedOperationError`. Scroll with `wheel()` or address items through `row()` / `cell()`, which scroll their view themselves. |
| Any input on Qt Quick / QML items | The input backend is QWidget-only (milestone 2). `object.find` and property reads work on Quick objects; clicking or typing at them raises `unsupported`. |
| `quick.*` commands (`evaluate`, `find_by_id`, ...) | Not registered; raise `UnsupportedOperationError`. |
| `record.start` / `record.stop`, `liberaqt record` | Not registered. |
| OS-level input (Squish's `nativeType` / `nativeMouseClick`) | No equivalent third mode. Would need `SendInput`, screen coordinates and a foreground window — incompatible with headless runs and with driving several applications at once, so it waits for a widget that ignores the platform seam in practice. |
| Touch, gestures, IME composition | Milestone 2. |
| Obscured-target detection | Actionability does not yet notice a covering widget (`TODO(m1)`). A native click on a covered point lands on whatever is really in front — a visible failure; a synthetic click bypasses the covering entirely — a silent one. |

## Choosing a mode

Stay native. It is the default because a test that passes natively is evidence a user can do the
same thing. Reach for `mode="synthetic"` per call, not per session, and for exactly two reasons:
a target a user genuinely cannot reach (a tab scrolled off the bar, an entry in an overlong
scrolling menu, a widget in a dock parked off-screen), or state setup where fidelity is not the
point and `fill()` has no equivalent.
