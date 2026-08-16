# LiberaQT — Wire Protocol v1

Transport: **TCP over `127.0.0.1`**, newline-delimited UTF-8 JSON (one JSON object per line, no
embedded raw newlines). Chosen over shared memory / named pipes because it is identical on Windows
and Linux, trivially debuggable with `nc`, and lets the agent live behind a container boundary later.

The agent binds an ephemeral port and writes it to `stdout` as a handshake banner *and* to
`$LIBERAQT_PORT_FILE` if set, so the launcher never has to guess.

## 0. Handshake

On connect the agent sends:

```json
{"type":"hello","protocol":1,"agent":"0.1.0","qt":"6.7.2","platform":"linux","pid":48211,
 "app":{"name":"sample_app","widgets":true,"quick":true}}
```

The client replies:

```json
{"type":"auth","token":"<LIBERAQT_TOKEN>","protocol":1}
```

Wrong token or unsupported protocol -> agent sends `{"type":"error",...}` and closes. The token is
generated per-launch by the client and passed via environment; it prevents another local process
from driving the AUT.

## 1. Message shapes

**Request** (client -> agent)

```json
{"id": 42, "cmd": "object.find", "params": { ... }, "timeout_ms": 5000}
```

**Response** (agent -> client)

```json
{"id": 42, "ok": true, "result": { ... }}
{"id": 42, "ok": false, "error": {"code": "not_found", "message": "...", "data": { ... }}}
```

**Event** (agent -> client, unsolicited, no `id`)

```json
{"type": "event", "event": "window.opened", "data": {"handle": "w7", "title": "Settings"}}
```

`id` is a monotonically increasing integer chosen by the client. Responses may arrive out of order.

### Asynchronous commands

Most commands are answered from inside the handler. A command that has to **let the application
run** must not be: the agent's socket lives on the GUI thread, so a handler that blocks holds its
own reply hostage to whatever the application does next. If the application enters a nested event
loop — a modal `QDialog::exec()` is enough, and one during startup is common — the reply is never
written, while every *other* command keeps being serviced by that nested loop. The failure looks
like a single hung command on a healthy connection.

Such commands resolve later, from the event loop:

* `sync.wait_idle` — driven by chained zero-delay timers rather than pumping the loop. A prompt
  tick is itself the proof the queue drained; a late one restarts the quiet window. Timers keep
  firing inside a nested loop, so the reply still arrives with a modal dialog open.
* `sync.wait_signal` — the point is to let the application run until it emits.

* `input.*` — every one of them except `input.set_text`. They queue their events rather than
  delivering them, so they answer two zero-delay timer turns later, once the queue has drained.
  Replying sooner would race the application: the client's next command could arrive before the
  click had been handled, which is exactly how a synthesised click ends up looking as though
  nothing happened. Two turns rather than one because whether the window-system queue is drained
  before or after zero-timers fire within a single pass is a property of the platform dispatcher.

* `widget.select_item`, `widget.tab_select`, `widget.menu_trigger` — selecting *is* clicking on
  the native path (§ Widgets), and a menu or combo needs several clicks with a popup appearing
  between them, so these run as staged walks that resolve when the final click has been posted.

One more command avoids the same trap by *posting* its work and answering immediately, which
necessarily discards the result: `object.invoke` with `"queued": true` — required for anything
that opens a modal dialog. Returns `{"queued": true}` and no value. (`widget.menu_trigger` in
synthetic mode queues its trigger the same way.)

Nothing else may block.

## 2. Error codes

| Code | Meaning |
| --- | --- |
| `not_found` | Selector matched zero objects. |
| `ambiguous` | Selector matched more than one and no index was given. |
| `stale` | Handle refers to a destroyed object. |
| `not_actionable` | Object exists but is hidden/disabled/zero-sized/obscured. |
| `unsupported` | Command not valid for this object type or Qt version. |
| `invalid_params` | Malformed request. |
| `internal` | Unexpected agent-side failure; `data.trace` carries detail. |
| `timeout` | Handler exceeded `timeout_ms` (usually a blocked GUI thread). |

`not_found` also covers lookups unrelated to the object tree — a menu entry, a tab, a row — and in
those cases the message is the whole diagnosis and lists what *was* there. Clients must not discard
it in favour of a selector-shaped message of their own.

Errors always carry `data.context` with the selector and, for `not_found`, `data.near_misses`:
a short list of candidates that matched some but not all predicates. This is what makes failures
debuggable without re-running under a spy.

## 3. Command surface (v1)

### Session

| Command | Params | Result |
| --- | --- | --- |
| `session.info` | – | qt version, platform, pid, loaded modules |
| `session.ping` | – | `{"pong": true}` |
| `session.quit` | `{"force": bool}` | closes the AUT |
| `session.set_options` | `{"input_mode": "native"\|"synthetic"}` | `{"accepted": [...]}` |

`accepted` lists the keys that were actually **applied**, not the ones that were sent: unknown keys
are ignored rather than rejected, so a newer client can talk to an older agent, and comparing the
two is how it finds out. Only `input_mode` is honoured so far — `idle_poll_ms`, `animation_wait`
and `network_wait` are accepted by older clients and silently dropped. An unparseable value is an
`invalid_params` error rather than a silent fallback, because the alternative is a suite that
believes it is testing one thing and is testing another.

### Discovery

| Command | Params | Result |
| --- | --- | --- |
| `object.find` | `{"selector": <SelectorNode>, "root": handle?, "limit": int}` | `{"handles": [...]}` |
| `object.tree` | `{"root": handle?, "depth": int, "visual_only": bool}` | nested node dump |
| `object.info` | `{"handle"}` | class, objectName, geometry, visible, enabled, key props |
| `object.exists` | `{"handle"}` | `{"exists": bool}` — a question with a false answer, never an error |
| `window.list` | – | `[{handle, title, geometry, active, type: "widget"\|"quick"}]` |

### Properties and invocation

| Command | Params | Result |
| --- | --- | --- |
| `object.get_property` | `{"handle", "name"}` | `{"value": <JSON>}` |
| `object.set_property` | `{"handle", "name", "value"}` | ok |
| `object.list_properties` | `{"handle"}` | `{"properties": [{name, type, writable, readable, declared_in, value}]}` |
| `object.invoke` | `{"handle", "method", "args": [], "queued"?}` | `{"value": ...}` (Q_INVOKABLE / slots only) |
| `quick.evaluate` | `{"handle", "expression"}` | `{"value": ...}` (QML/JS in the item's context) |

`declared_in` names the class that introduced each property, which is what makes the list usable:
a custom `QTextBrowser` subclass inherits some eighty properties, and the handful it declared
itself are the interesting ones.

`QVariant` <-> JSON conversion covers primitives, `QString`, `QDateTime` (ISO 8601), `QPoint`,
`QSize`, `QRect`, `QColor` (`#rrggbb`), `QUrl`, lists, maps, and enums (emitted as
`{"__enum": "Qt::AlignLeft", "value": 1}`). Anything else becomes
`{"__opaque": "QFont", "repr": "..."}` — readable but not round-trippable. Documented explicitly so
users are never surprised by a silent lossy conversion.

Incoming values are shaped to the destination type before use, because JSON cannot express the
geometry types: `set_property` coerces against the declared property type and `invoke` against each
parameter type, so `[x, y]` becomes a `QPoint`, `[w, h]` a `QSize`, and `[x, y, w, h]` a `QRect`.
This is what makes geometry writable — `QWidget` exposes `size` and `pos` as properties whose
setters are `resize()` and `move()`.

#### What `object.invoke` can reach

Only **slots and `Q_INVOKABLE` methods**. Everything else on a `QObject` is invisible to `moc`, so
plain public functions such as `QWidget::resize`, `QWidget::move` and `QWidget::activateWindow`
cannot be called however they are spelled. Signals are excluded deliberately: emitting one would
fake an event the application never produced. A failed lookup returns `unsupported` with the
class's invokable signatures in `data.invokable`, or `invalid_params` with `data.overloads` when
the name exists but the argument count does not match.

Operations Qt does not expose as slots are available as **synthetic methods**, named with a `__`
prefix and handled before meta-object lookup:

| Synthetic method | Effect |
| --- | --- |
| `__activate` | `raise()` + `activateWindow()` (or `QWindow::requestActivate`) |

### Input

| Command | Params |
| --- | --- |
| `input.click` | `{"handle", "button", "modifiers", "pos"?, "part"?, "count": 1\|2}` |
| `input.press` / `input.release` | `{"handle", "key"}` for a keyboard chord, or `{"handle", "button", "pos"?}` for a mouse button |
| `input.hover` | `{"handle", "pos"?}` |
| `input.drag` | `{"handle", "to_handle"\|"to_pos", "from_pos"?, "steps"}` |
| `input.wheel` | `{"handle", "dx", "dy", "modifiers"}` — one step is a wheel notch; positive `dy` scrolls down |
| `input.key` | `{"handle"?, "key", "modifiers", "count"}` |
| `input.type_text` | `{"handle"?, "text", "delay_ms"}` |
| `input.set_text` | `{"handle", "text"}` (fast path: clear + set + commit signals) |

Every command above also accepts `"mode"`, overriding the session default for one call.

#### Delivery modes

`native`, the default, hands each event to Qt through `QWindowSystemInterface` — the seam a
platform plugin pushes real input through. Qt then does everything it does for a user: it
hit-tests for the receiver, tracks hover and enter/leave, holds the implicit grab between press
and release, derives a double click from two nearby presses, dismisses popups, moves focus to
whatever was clicked, and refuses input to a window a modal dialog has disabled. Widgets see
`spontaneous()` events.

Consequences worth knowing:

* A press on an inactive window **activates it first**, as the platform would. Without this the
  application stays permanently in the background whatever the test clicks: `activeWindow()` stays
  null and window-context shortcuts — nearly all of them — silently match nothing. Not done past a
  modal dialog, where a user could not do it either.
* A chord is pressed the way a hand presses it: modifiers down, key, key up, modifiers up.
* `input.type_text` sends a key code as well as the text for each character, because widgets read
  whichever they need — a `QLineEdit` inserts `text()`, while shortcuts, type-ahead and every
  `keyPressEvent` override switch on `key()`.
* No `MouseButtonDblClick` is ever sent: `"count": 2` queues two press/release pairs and lets Qt
  derive the double click, as it does for a user.
* Keys are aimed at a *window*, and Qt hands them to its focus object. `QApplication::focusWidget()`
  is deliberately not consulted — it is null whenever the application is not the foreground one,
  which is the normal state of one under test.

`synthetic` sends each event straight at one widget with `QApplication::sendEvent`. The widget's
own handler runs, but none of the routing above does — most visibly, a click does not focus what
it hits, so typing afterwards goes wherever focus already was. It is kept because it still reaches
a target a user could not: one scrolled out of view, covered, or on a window parked off-screen
(Qt does that to a tabified `QDockWidget` whose tab is not current, and such a widget still reports
`isVisible()`).

`session.set_options({"input_mode": ...})` sets the session default.

`input.set_text` is in neither mode: it writes the `text` / `plainText` / `currentText` property
directly. It is the one deliberately non-user-input command, for setting up state cheaply — but it
refuses a read-only widget, because writing where a user could not type is a false pass.

`input.press` and `input.release` carry both halves of the API deliberately: `Keyboard.down/up`
sends `key`, `Mouse.down/up` sends `button`, and to the caller they are one idea — hold something
down. The agent picks by which parameter is present. `input.drag` likewise accepts a target object
(`Locator.drag_to`) or raw coordinates (`Mouse.drag`).

Default click position is the object's visual centre, mapped to window coordinates via
`QWidget::mapTo` / `QQuickItem::mapToScene`. When the handle names a cell (see below), the centre
is the cell's, and the view is scrolled to bring it on screen first. `part` aims at a named
sub-part instead — `"spin_up"` / `"spin_down"` for a spin box's arrows — located through the
widget's `QStyle`, so the point is right for whatever style the application uses. An explicit
`pos` wins over both.

On the synthetic path `input.wheel` and mouse events are redirected to a scroll area's viewport,
because `QAbstractScrollArea` ignores events sent to the frame. The native path needs no such
special case: Qt hit-tests to the viewport itself.

> Not yet true: obscured-centre adjustment. The agent does not currently detect an obscuring
> sibling, so a covered object is clicked at its centre regardless (`TODO(m1)`). In native mode
> the click then lands on whatever is actually in front, which at least fails visibly.

### Widgets and models

| Command | Params | Result |
| --- | --- | --- |
| `widget.item_rect` | `{"handle", "text"\|"row", "column"?}` | `{"handle", "row", "column", "text", "rect"}` |
| `widget.model_data` | `{"handle", "max_rows"?}` | `{"rows": [...], "headers": [...]}` |
| `widget.select_item` | `{"handle", "text"\|"row"\|"index", "column"?, "mode"?}` | `{"row", "column", "text"}` (`{"index", "text"}` for a combo box) |
| `widget.menu_trigger` | `{"window", "path", "probe"?, "mode"?}` | `{"enabled", "checked", "text", "clicked"\|"queued"}` |
| `widget.tab_select` | `{"handle", "text"\|"index", "mode"?}` | `{"index"}` |

`column` accepts an index or a header caption, since a caller thinks in terms of "the Part Number
column" rather than column 17. `text` lookups search the whole grid, recursively, and call
`fetchMore` on the way down — a lazily populated tree reports no children until something asks.

**Selecting is clicking.** On the native path these three are user actions built out of real
clicks, and only the aiming is programmatic:

* `widget.tab_select` clicks the tab's rectangle on its bar. A tab the bar has scrolled out of
  reach is refused rather than switched behind the user's back.
* `widget.select_item` on a view scrolls the item into view and clicks it; the selection that
  results is the view's own click policy. On a **combo box** it clicks the box open, waits for
  the popup, and clicks the entry inside it — a staged walk, since the popup's view only exists
  while it is open.
* `widget.menu_trigger` walks a `>`-separated path from the window's menu bar (`"File > Import >
  HDL Source Files"`, captions matched with any `&` accelerator removed) by clicking each menu
  open and clicking the entry inside it, so `aboutToShow` population and hover state happen as
  they would for a user. The reply resolves once the final click is posted, not after its effect
  — a menu entry routinely opens a modal dialog (§1).

With `"mode": "synthetic"` each falls back to writing the state: `setCurrentIndex`, an explicit
selection, a queued `QAction::trigger`. That still reaches what a user cannot — an entry scrolled
out of an overlong menu, a tab past the bar's edge. `probe: true` on `menu_trigger` reports
whether an entry is enabled without activating anything in either mode.

Staged walks reject with `timeout` if a popup fails to appear within 1.5 s of the click that
should have opened it, and close whatever they did manage to open, so a failed walk does not
leave a menu hanging over the next action.

#### Addressing a cell

A cell is not a `QObject`, so it cannot hold a handle of its own. `widget.item_rect` returns a
**composite** handle:

```
o17~0/4~2        view o17, root row 0 -> child row 4, column 2
```

`ObjectRegistry` resolves any such handle to the view, so every existing command keeps working on
it; only the commands that care about position read the suffix. `object.info` on one describes the
cell rather than the view, and `input.click` clicks the cell's centre.

The row part is a **path**, not a single number, because a tree keeps its children under their
parent — "row 4" is meaningless without knowing whose row 4 it is. A flat encoding silently
resolved nested items to the wrong top-level row.

### Quick

**None of these are implemented yet** — they return `unsupported`. Note that ordinary locators
*do* reach QML objects through `object.find`; it is `object.tree` that stops at a `QQuickWindow`
without descending into its `contentItem`, which is why `liberaqt inspect` cannot describe a QML
window (`TODO(m0)` in `selector_engine.cpp`).

| Command | Purpose |
| --- | --- |
| `quick.find_by_id` | resolve a QML `id` inside its `QQmlContext` |
| `quick.list_view_item` | `ListView`/`GridView`/`Repeater` delegate at index (forces creation if virtualised) |
| `quick.wait_animations` | block until no running animations in the window |
| `quick.evaluate` | evaluate a JS expression in the item's QML context |

### Visual

| Command | Params | Result |
| --- | --- | --- |
| `screen.grab` | `{"handle"?, "window"?}` | `{"png": "<base64>", "size": [w,h]}` |

### Synchronisation

| Command | Params |
| --- | --- |
| `sync.wait_idle` | `{"quiet_ms": 50, "animations": true, "network": false, "timeout_ms": 10000}` |
| `sync.wait_signal` | `{"handle", "signal", "timeout_ms"}` |

Both are asynchronous (§1). `signal` may be a full signature (`"valueChanged(int)"`) or the bare
name (`"valueChanged"`); an unknown one reports `unsupported` and lists the signals the class does
have. A signal that fires before the command is sent is missed — this waits, it does not look
back.

### Recorder

**Not implemented yet** — both return `unsupported`.

| Command | Params |
| --- | --- |
| `record.start` | `{"granularity": "semantic"\|"raw"}` |
| `record.stop` | – |

Emits `record.action` events: `{"action":"click","selector":"...","target_info":{...},"ts":...}`.

## 4. Events

| Event | Data |
| --- | --- |
| `window.opened` / `window.closed` | handle, title, type |
| `object.destroyed` | handle |
| `app.message` | qDebug/qWarning/qCritical output with category |
| `app.about_to_quit` | – |
| `record.action` | recorded semantic action |

## 5. Versioning

`protocol` is a single integer. The client refuses to run against a different major and prints the
exact `liberaqt agents install` command to fix it. Additive fields are allowed within a version;
removals and renames are not.

## 6. Worked example

```
C-> {"id":1,"cmd":"window.list","params":{}}
A<- {"id":1,"ok":true,"result":[{"handle":"w1","title":"Login","type":"widget","geometry":[100,100,400,300],"active":true}]}
C-> {"id":2,"cmd":"object.find","params":{"root":"w1","selector":{"type":"QLineEdit","props":{"objectName":"username"}}}}
A<- {"id":2,"ok":true,"result":{"handles":["o17"]}}
C-> {"id":3,"cmd":"input.set_text","params":{"handle":"o17","text":"sargis"}}
A<- {"id":3,"ok":true,"result":{}}
C-> {"id":4,"cmd":"object.find","params":{"root":"w1","selector":{"type":"QPushButton","props":{"text":"Log in"}}}}
A<- {"id":4,"ok":true,"result":{"handles":["o22"]}}
C-> {"id":5,"cmd":"input.click","params":{"handle":"o22","button":"left"}}
A<- {"id":5,"ok":true,"result":{"pos":[45,12]}}
C-> {"id":6,"cmd":"sync.wait_idle","params":{"quiet_ms":100,"animations":true}}
A<- {"id":6,"ok":true,"result":{"idle_after_ms":38}}
```
