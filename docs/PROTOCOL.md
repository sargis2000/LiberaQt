# qtdriver — Wire Protocol v1

Transport: **TCP over `127.0.0.1`**, newline-delimited UTF-8 JSON (one JSON object per line, no
embedded raw newlines). Chosen over shared memory / named pipes because it is identical on Windows
and Linux, trivially debuggable with `nc`, and lets the agent live behind a container boundary later.

The agent binds an ephemeral port and writes it to `stdout` as a handshake banner *and* to
`$QTDRIVER_PORT_FILE` if set, so the launcher never has to guess.

## 0. Handshake

On connect the agent sends:

```json
{"type":"hello","protocol":1,"agent":"0.1.0","qt":"6.7.2","platform":"linux","pid":48211,
 "app":{"name":"sample_app","widgets":true,"quick":true}}
```

The client replies:

```json
{"type":"auth","token":"<QTDRIVER_TOKEN>","protocol":1}
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
| `session.set_options` | `{"idle_poll_ms", "animation_wait", "network_wait"}` | ok |

### Discovery

| Command | Params | Result |
| --- | --- | --- |
| `object.find` | `{"selector": <SelectorNode>, "root": handle?, "limit": int}` | `{"handles": [...]}` |
| `object.tree` | `{"root": handle?, "depth": int, "visual_only": bool}` | nested node dump |
| `object.info` | `{"handle"}` | class, objectName, geometry, visible, enabled, key props |
| `object.exists` | `{"selector"}` | `{"count": n}` |
| `window.list` | – | `[{handle, title, geometry, active, type: "widget"\|"quick"}]` |

### Properties and invocation

| Command | Params | Result |
| --- | --- | --- |
| `object.get_property` | `{"handle", "name"}` | `{"value": <JSON>}` |
| `object.set_property` | `{"handle", "name", "value"}` | ok |
| `object.list_properties` | `{"handle"}` | `[{name, type, writable}]` |
| `object.invoke` | `{"handle", "method", "args": []}` | `{"value": ...}` (Q_INVOKABLE / slots only) |
| `quick.evaluate` | `{"handle", "expression"}` | `{"value": ...}` (QML/JS in the item's context) |

`QVariant` <-> JSON conversion covers primitives, `QString`, `QDateTime` (ISO 8601), `QPoint`,
`QSize`, `QRect`, `QColor` (`#rrggbb`), `QUrl`, lists, maps, and enums (emitted as
`{"__enum": "Qt::AlignLeft", "value": 1}`). Anything else becomes
`{"__opaque": "QFont", "repr": "..."}` — readable but not round-trippable. Documented explicitly so
users are never surprised by a silent lossy conversion.

### Input

| Command | Params |
| --- | --- |
| `input.click` | `{"handle", "button", "modifiers", "pos"?, "count": 1\|2}` |
| `input.press` / `input.release` | `{"handle", "button", "pos"?}` |
| `input.hover` | `{"handle", "pos"?}` |
| `input.drag` | `{"from_handle", "to_handle"\|"to_pos", "steps"}` |
| `input.wheel` | `{"handle", "dx", "dy", "modifiers"}` |
| `input.key` | `{"handle"?, "key", "modifiers", "count"}` |
| `input.type_text` | `{"handle"?, "text", "delay_ms"}` |
| `input.set_text` | `{"handle", "text"}` (fast path: clear + set + commit signals) |

Default click position is the object's visual centre, mapped to window coordinates via
`QWidget::mapTo` / `QQuickItem::mapToScene`. If the centre is obscured by another item, the agent
picks the largest visible sub-rect and reports the adjustment in the result.

### Widgets and models

| Command | Purpose |
| --- | --- |
| `widget.item_rect` | `QAbstractItemView` (row, column, parent path) -> rect + handle |
| `widget.model_data` | read model contents as a table (rows x roles) |
| `widget.select_item` | select/activate a view item by index or by displayed text |
| `widget.menu_trigger` | trigger a `QAction` by menu path (`"File/Recent/foo.txt"`) |
| `widget.tab_select` | select a `QTabWidget` tab by text or index |

### Quick

| Command | Purpose |
| --- | --- |
| `quick.find_by_id` | resolve a QML `id` inside its `QQmlContext` |
| `quick.list_view_item` | `ListView`/`GridView`/`Repeater` delegate at index (forces creation if virtualised) |
| `quick.wait_animations` | block until no running animations in the window |

### Visual

| Command | Params | Result |
| --- | --- | --- |
| `screen.grab` | `{"handle"?, "window"?}` | `{"png": "<base64>", "size": [w,h]}` |

### Synchronisation

| Command | Params |
| --- | --- |
| `sync.wait_idle` | `{"quiet_ms": 100, "animations": true, "network": false}` |
| `sync.wait_signal` | `{"handle", "signal", "timeout_ms"}` |

### Recorder

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
exact `qtdriver agents install` command to fix it. Additive fields are allowed within a version;
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
