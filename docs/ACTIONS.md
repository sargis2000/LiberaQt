# The action surface: native vs synthetic

Every interactive method the client offers, in one table. `integration/test_actions.py` and
`integration/test_input.py` assert the claims below against real applications; when the table
and those tests disagree, the tests are the ones that ran.

The two modes, in one sentence each:

* **native** (the default) hands events to Qt at the seam a platform plugin uses, so Qt routes
  them exactly as it routes a real user's input — hit-testing, hover, focus-on-click, the
  implicit grab, double-click derivation, window activation, modal blocking.
* **synthetic** delivers an event straight to one widget with `QApplication::sendEvent`; the
  widget's handler runs and nothing around it does.

The session default is set with `Application.set_input_mode()` / `--liberaqt-input-mode` /
`LiberaQt(input_mode=...)`; each method also takes a per-call `mode=` override unless the table
says otherwise. Wire details live in `docs/PROTOCOL.md`.

Legend: **yes** — works as user input · **prop** — writes state, no input involved ·
**part** — works with caveats · **no** — not available yet.

| Method | Description | Native | Synthetic | Details |
| --- | --- | --- | --- | --- |
| `click` / `double_click` | Click the object; `count=2` double-clicks | yes | yes | Native focuses what it clicks, activates an inactive window first, and derives the double click from two presses. A synthetic click runs the handler only: focus does **not** follow it, so `click()` then `type()` types wherever focus already was. |
| `right_click` | Click to open a context menu | yes | yes | Both post the separate `QContextMenuEvent` a real right-click produces, so context menus open in either mode. |
| `hover` | Move the pointer over the object | yes | part | Native updates enter/leave and hover state; synthetic delivers one move event and no bookkeeping, so tooltips and hover highlights may not react. |
| `type` | Type character by character; Squish-style `<Ctrl+A>` chords may ride in the text | yes | yes | Native aims at the *window* and Qt gives the keys to its focus widget; synthetic aims at the named widget directly. Keyboard shortcuts fire in both (measured). |
| `press` | Press one key or chord | yes | yes | Same delivery split as `type`. |
| `wheel` | Scroll the mouse wheel over the object | yes | yes | Synthetic is rerouted to a scroll area's viewport by hand; native hit-tests there itself. |
| `drag_to` / `Mouse.drag` | Press, move in steps, release | yes | yes | Native holds the implicit press-to-release grab, as a real drag does. |
| `Mouse.*` / `Keyboard.*` | Raw input at window coordinates | yes | yes | Follow the **session** mode; no per-call `mode=` yet. |
| `select_tab` | Switch to a tab by caption or index | yes | yes | Native clicks the tab's rectangle on the bar and refuses a tab the bar has scrolled out of reach; synthetic writes `setCurrentIndex` and reaches it anyway. Works on `QTabWidget`, `QTabBar`, and the bar Qt creates for tabified docks. |
| `select_option` | Choose a combo-box entry | yes | yes | Native clicks the combo open and clicks the entry in its popup, so `activated` fires as for a user; synthetic writes `setCurrentIndex`, which emits `currentIndexChanged` but **not** `activated`, and no popup ever opens. |
| `select_item` | Select a row or cell in an item view | yes | yes | Native scrolls the item into view and clicks it — the resulting selection is the view's own click policy; synthetic writes the current index and selects the full row. |
| `spin` | Step a spin box via its arrow buttons | yes | yes | The arrow position comes from the widget's `QStyle` in both modes; synthetic sends the click to the same point. |
| `set_checked` / `check` / `uncheck` | Put a checkbox or toggle into a state | yes | yes | Sugar over `click`, clicking only when the state differs. Aimed at the style's *click rect*, not the widget centre — a stretched checkbox's centre is a dead zone a user's click would miss too. A radio button cannot be *un*checked in either mode, because a user cannot either. |
| `menu(...).trigger()` | Activate a `"File > Import > ..."` menu path | yes | yes | Native clicks each menu open along the path (`aboutToShow` and hover happen for real); synthetic queues `QAction::trigger` and opens nothing. **Both** resolve the path before anything opens, so entries a menu only creates inside `aboutToShow` (e.g. "Recent Files") are not addressable yet in either mode. |
| `fill` / `clear` | Set or clear text in one shot | prop | prop | Deliberately not input, in any mode: writes the `text` property so change signals fire once, for state that is not itself under test. Refuses a read-only field — succeeding where a user could not type is a false pass. |
| `locator[...] = value` | Write any property | prop | prop | Escape hatch by design. |
| `invoke` / `evaluate` | Call slots / `Q_INVOKABLE` methods | prop | prop | Introspection and setup, not interaction. |
| `scroll_into_view` | Scroll ancestors until visible | no | no | Unimplemented; raises `UnsupportedOperationError`. Scroll with `wheel()`, or address items via `row()` / `cell()`, which scroll their view themselves. |
| Input on QML / Qt Quick items | Click or type at a Quick item | no | no | The input backend is QWidget-only (milestone 2). `object.find` and property reads work on Quick objects; input at them raises `unsupported`. |
| `quick.*` commands | QML evaluate / find-by-id / list items | no | no | Not registered; raise `UnsupportedOperationError`. |
| `record.start` / `record.stop` | The recorder, `liberaqt record` | no | no | Not registered. |
| OS-level input | Squish's `nativeType` / `nativeMouseClick` | no | no | No third mode yet. Would need `SendInput`, screen coordinates and a foreground window — incompatible with headless runs and driving several applications at once, so it waits for a widget that ignores the platform seam in practice. |
| Touch, gestures, IME | Taps, flicks, composition | no | no | Milestone 2. |

One caveat that spans every pointer row: actionability does not yet detect an *obscuring*
widget (`TODO(m1)`). A native click on a covered point lands on whatever is really in front — a
visible failure; a synthetic click bypasses the covering entirely — a silent one.

## Choosing a mode

Stay native. It is the default because a test that passes natively is evidence a user can do the
same thing. Reach for `mode="synthetic"` per call, not per session, and for exactly two reasons:
a target a user genuinely cannot reach (a tab scrolled off the bar, an entry in an overlong
scrolling menu, a widget in a dock parked off-screen), or state setup where fidelity is not the
point and `fill()` has no equivalent.
