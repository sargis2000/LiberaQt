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

Two more commands avoid the same trap by *posting* their work and answering immediately, which
necessarily discards the result:

* `object.invoke` with `"queued": true` — required for anything that opens a modal dialog, such as
  triggering a menu `QAction`. Returns `{"queued": true}` and no value.
* `widget.menu_trigger` — always queued, for the same reason.

Nothing else may block. `input.*` currently delivers events synchronously with
`QApplication::sendEvent`, which means clicking a button whose handler opens a modal dialog will
strand that reply (`TODO(m1)`).

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
| `session.set_options` | `{"idle_poll_ms", "animation_wait", "network_wait"}` | `{"accepted": [...]}` — registered but currently a no-op: it echoes the keys and changes nothing. Unknown keys are ignored rather than rejected, so a newer client can talk to an older agent. |

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
| `input.click` | `{"handle", "button", "modifiers", "pos"?, "count": 1\|2}` |
| `input.press` / `input.release` | `{"handle", "key"}` for a keyboard chord, or `{"handle", "button", "pos"?}` for a mouse button |
| `input.hover` | `{"handle", "pos"?}` |
| `input.drag` | `{"handle", "to_handle"\|"to_pos", "from_pos"?, "steps"}` |
| `input.wheel` | `{"handle", "dx", "dy", "modifiers"}` — one step is a wheel notch; positive `dy` scrolls down |
| `input.key` | `{"handle"?, "key", "modifiers", "count"}` |
| `input.type_text` | `{"handle"?, "text", "delay_ms"}` |
| `input.set_text` | `{"handle", "text"}` (fast path: clear + set + commit signals) |

`input.press` and `input.release` carry both halves of the API deliberately: `Keyboard.down/up`
sends `key`, `Mouse.down/up` sends `button`, and to the caller they are one idea — hold something
down. The agent picks by which parameter is present. `input.drag` likewise accepts a target object
(`Locator.drag_to`) or raw coordinates (`Mouse.drag`).

Default click position is the object's visual centre, mapped to window coordinates via
`QWidget::mapTo` / `QQuickItem::mapToScene`. When the handle names a cell (see below), the centre
is the cell's, and the view is scrolled to bring it on screen first.

`input.wheel` is delivered to a scroll area's viewport rather than the area itself, because
`QAbstractScrollArea` ignores wheel events sent to the frame.

> Not yet true: obscured-centre adjustment. The agent does not currently detect an obscuring
> sibling, so a covered object is clicked at its centre regardless (`TODO(m1)`).

### Widgets and models

| Command | Params | Result |
| --- | --- | --- |
| `widget.item_rect` | `{"handle", "text"\|"row", "column"?}` | `{"handle", "row", "column", "text", "rect"}` |
| `widget.model_data` | `{"handle", "max_rows"?}` | `{"rows": [...], "headers": [...]}` |
| `widget.select_item` | `{"handle", "text"\|"row"\|"index", "column"?}` | `{"row", "column", "text"}` |
| `widget.menu_trigger` | `{"window", "path", "probe"?}` | `{"enabled", "checked", "text", "queued"?}` |
| `widget.tab_select` | `{"handle", "text"\|"index"}` | `{"index"}` |

`column` accepts an index or a header caption, since a caller thinks in terms of "the Part Number
column" rather than column 17. `text` lookups search the whole grid, recursively, and call
`fetchMore` on the way down — a lazily populated tree reports no children until something asks.

`widget.menu_trigger` walks a `>`-separated path from the window's menu bar (`"File > Import > HDL
Source Files"`), matching captions with any `&` accelerator removed. `probe: true` reports whether
the entry is enabled without activating it. Activation is **queued**, because a menu entry
routinely opens a modal dialog; see §1 on asynchronous commands.

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
