# The action surface: native vs synthetic

Every interactive method the client offers, in one table.

> Enforced again, in part, by `e2e/test_actions_live.py` and `e2e/test_selectors_live.py`, which
> drive a live Qt Assistant: text input, chords, window shortcuts, checkboxes, item views and the
> wheel are asserted there, as is every selector rule. Run them with
> `pytest e2e/test_actions_live.py e2e/test_selectors_live.py`; they skip when Qt is absent.
>
> Rows not covered by that suite -- `select_option`, `spin`, `drag_to`, `menu`, `context_menu`,
> `hover` -- remain **documentation, not enforcement**, recording what was measured on Qt 6.7
> (Assistant, Designer, qmleasing) and Qt 5.15 (Libero SoC). The end-to-end Libero flow in
> `e2e/test_libero_synthesis.py` does exercise `menu`, `context_menu` and `select_item` against a
> real application, though only along the one path it walks.

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
| `right_click` | Click to open a context menu | yes | yes | Context menus open in either mode, by different routes: `QWidgetWindow` derives the `QContextMenuEvent` from a natively delivered right button, exactly as it does for the platform's own input, so the agent must *not* add one — doing so opens the menu twice. Synthetic delivery never reaches `QWidgetWindow`, so there the agent posts it. |
| `context_menu` | Right-click, then activate an entry in the menu that appears | yes | no | Opening the menu and activating an entry in one call. Works on item rows and cells (`row(...).context_menu(...)`), and `>` walks a submenu. Native only, by nature: the menu is built inside `contextMenuEvent`, so there is no action to trigger without genuinely opening it. A wrong entry lists what the menu really offers, which is also how to discover one. |
| `hover` | Move the pointer over the object | yes | part | Native updates enter/leave and hover state; synthetic delivers one move event and no bookkeeping, so tooltips and hover highlights may not react. |
| `type` | Type character by character; `<Ctrl+A>` chords may ride in the text (`Keyboard.type` too) | yes | yes | Native aims at the *window* and Qt gives the keys to its focus widget; synthetic aims at the named widget directly. Keyboard shortcuts fire in both (measured). |
| `press` | Press one key or chord | yes | yes | Same delivery split as `type`. |
| `wheel` | Scroll the mouse wheel over the object | yes | yes | Synthetic is rerouted to a scroll area's viewport by hand; native hit-tests there itself. |
| `drag_to` / `Mouse.drag` | Press, move in steps, release | yes | yes | Native holds the implicit press-to-release grab, as a real drag does. |
| `Mouse.*` / `Keyboard.*` | Raw input at window coordinates | yes | yes | Every method takes a per-call `mode=`, like the locator actions; omitting it falls back to the session mode. `Keyboard.type` parses the same `<Ctrl+A>` chords as `Locator.type` -- the two share one splitter, so a given string means the same thing whichever is handed it. |
| `select_tab` | Switch to a tab by caption or index | yes | yes | Native clicks the tab's rectangle on the bar and refuses a tab the bar has scrolled out of reach; synthetic writes `setCurrentIndex` and reaches it anyway. Works on `QTabWidget`, `QTabBar`, and the bar Qt creates for tabified docks. |
| `select_option` | Choose a combo-box entry | yes | yes | Native clicks the combo open and clicks the entry in its popup, so `activated` fires as for a user; synthetic writes `setCurrentIndex`, which emits `currentIndexChanged` but **not** `activated`, and no popup ever opens. |
| `select_item` | Select a row or cell in an item view | yes | yes | Native scrolls the item into view and clicks it — the resulting selection is the view's own click policy; synthetic writes the current index and selects the full row. |
| `highlight` | Draw a coloured box over the object for a moment | n/a | n/a | A debugging aid, not an action: nothing in the application changes. Returns as soon as the marker is up, so its duration is not added to the test's runtime. The overlay is invisible to the selector engine and to `object.tree`, transparent to the mouse, and refuses focus — a tool for finding out what a selector matched must not change the answer or block the click. A composite handle highlights the cell, not the view. Widgets only; Quick items raise `unsupported`. Actionability is deliberately not required, since a covered or parked object is the one worth looking at. |
| `parent` / `ancestor` | Step up to the containing object | n/a | n/a | Navigation, not actions, and the only way to move *up* the tree. `parent()` is one level; `ancestor(sel)` climbs until something matches, which is usually what is wanted because Qt interposes layouts and viewports. Both resolve immediately rather than lazily, since the answer is a specific object. Ancestor matching walks the inheritance chain, so the class name of the result may be a subclass. |
| `row` / `item` / `cell` | Address a row, an item by exact text, or one cell | n/a | n/a | Locators, not actions. `row(has_text=)` means *containing*; `item(text)` is exact — reach for it whenever one label is a substring of another (searching a Libero flow for "Synthesize" with `row()` finds "Verify Pre-Synthesized Design"). Both descend the whole tree and fetch lazily populated branches. |
| `spin` | Step a spin box via its arrow buttons | yes | yes | The arrow position comes from the widget's `QStyle` in both modes; synthetic sends the click to the same point. |
| `set_checked` / `check` / `uncheck` | Put a checkbox or toggle into a state | yes | yes | Sugar over `click`, clicking only when the state differs. Aimed at the style's *click rect*, not the widget centre — a stretched checkbox's centre is a dead zone a user's click would miss too. A radio button cannot be *un*checked in either mode, because a user cannot either. |
| `menu(...).trigger()` | Activate a `"File > Import > ..."` menu path | yes | yes | Native clicks each menu open along the path and resolves every level only **after** its menu is genuinely on screen — so entries a menu creates inside `aboutToShow` ("Recent Files" lists) are addressable there. Synthetic queues `QAction::trigger` without opening anything, so it (and `is_enabled`, which probes without opening) can only see entries that exist while the menus are closed. |
| `fill` / `clear` | Set or clear text in one shot | prop | prop | Deliberately not input, in any mode: writes the `text` property so change signals fire once, for state that is not itself under test. Refuses a read-only field — succeeding where a user could not type is a false pass. |
| `locator[...] = value` | Write any property | prop | prop | Escape hatch by design. |
| `invoke` / `evaluate` | Call slots / `Q_INVOKABLE` methods | prop | prop | Introspection and setup, not interaction. |
| `scroll_into_view` | Scroll ancestors until visible | prop | prop | Asks every `QScrollArea` ancestor to `ensureWidgetVisible` — programmatic by design, like `fill`. Item cells never need it: `row()` / `cell()` scroll their own view. A non-`QScrollArea` scroll ancestor is refused with a hint. |
| Input on QML / Qt Quick items | Click or type at a Quick item | no | no | The input backend is QWidget-only (milestone 2). `object.find` and property reads work on Quick objects; input at them raises `unsupported`. |
| `quick.*` commands | QML evaluate / find-by-id / list items | no | no | Not registered; raise `UnsupportedOperationError`. |
| `record.start` / `record.stop` | The recorder, `liberaqt record` | no | no | Not registered. |
| OS-level input | Input delivered by the operating system rather than by Qt | no | no | No third mode yet. Would need `SendInput`, screen coordinates and a foreground window — incompatible with headless runs and driving several applications at once, so it waits for a widget that ignores the platform seam in practice. |
| Touch, gestures, IME | Taps, flicks, composition | no | no | Milestone 2. |

One rule that spans every pointer row: on the native path, actionability also demands the target
be **reachable**, and the refusal names what is in the way — `blocked by the modal dialog 'New
Form' (QDialog)`, `covered by QLabel 'overlay'`, `outside its window's on-screen area` (the
parked-dock case). Synthetic delivery skips those checks deliberately; bypassing them is what it
is for. The check is same-window only — another *application's* window in front never counts,
because an application under test is routinely covered by a terminal without being any less
drivable.

## Choosing a mode

Stay native. It is the default because a test that passes natively is evidence a user can do the
same thing. Reach for `mode="synthetic"` per call, not per session, and for exactly two reasons:
a target a user genuinely cannot reach (a tab scrolled off the bar, an entry in an overlong
scrolling menu, a widget in a dock parked off-screen), or state setup where fidelity is not the
point and `fill()` has no equivalent.
