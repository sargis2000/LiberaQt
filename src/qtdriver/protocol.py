"""Protocol constants and payload helpers. Mirrors docs/PROTOCOL.md."""

from __future__ import annotations

PROTOCOL_VERSION = 1

DEFAULT_TIMEOUT = 5.0        # seconds, per action
STARTUP_TIMEOUT = 30.0       # seconds, launch -> agent connected
POLL_INTERVAL = 0.05         # seconds, retry granularity


class Cmd:
    """Command names. Keep in sync with the agent's dispatcher table."""

    # session
    PING = "session.ping"
    INFO = "session.info"
    QUIT = "session.quit"
    SET_OPTIONS = "session.set_options"

    # discovery
    FIND = "object.find"
    TREE = "object.tree"
    OBJ_INFO = "object.info"
    EXISTS = "object.exists"
    WINDOW_LIST = "window.list"

    # properties / invocation
    GET_PROPERTY = "object.get_property"
    SET_PROPERTY = "object.set_property"
    LIST_PROPERTIES = "object.list_properties"
    INVOKE = "object.invoke"
    QUICK_EVALUATE = "quick.evaluate"

    # input
    CLICK = "input.click"
    PRESS = "input.press"
    RELEASE = "input.release"
    HOVER = "input.hover"
    DRAG = "input.drag"
    WHEEL = "input.wheel"
    KEY = "input.key"
    TYPE_TEXT = "input.type_text"
    SET_TEXT = "input.set_text"

    # widgets / models
    ITEM_RECT = "widget.item_rect"
    MODEL_DATA = "widget.model_data"
    SELECT_ITEM = "widget.select_item"
    MENU_TRIGGER = "widget.menu_trigger"
    TAB_SELECT = "widget.tab_select"

    # quick
    QUICK_FIND_BY_ID = "quick.find_by_id"
    QUICK_LIST_VIEW_ITEM = "quick.list_view_item"
    QUICK_WAIT_ANIMATIONS = "quick.wait_animations"

    # visual
    GRAB = "screen.grab"

    # sync
    WAIT_IDLE = "sync.wait_idle"
    WAIT_SIGNAL = "sync.wait_signal"

    # recorder
    RECORD_START = "record.start"
    RECORD_STOP = "record.stop"


class Event:
    WINDOW_OPENED = "window.opened"
    WINDOW_CLOSED = "window.closed"
    OBJECT_DESTROYED = "object.destroyed"
    APP_MESSAGE = "app.message"
    APP_ABOUT_TO_QUIT = "app.about_to_quit"
    RECORD_ACTION = "record.action"


# ---------------------------------------------------------------- QVariant <-> JSON

def decode_value(value):
    """Convert an agent-encoded value into a plain Python value.

    The agent encodes non-primitive QVariants as tagged dicts so that round-tripping is explicit
    rather than silently lossy. See PROTOCOL.md section 3.
    """
    if isinstance(value, dict):
        if "__enum" in value:
            return value["value"]
        if "__opaque" in value:
            return OpaqueValue(value["__opaque"], value.get("repr", ""))
        return {k: decode_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode_value(v) for v in value]
    return value


class OpaqueValue:
    """A QVariant type with no JSON representation (QFont, QPixmap, ...)."""

    __slots__ = ("type_name", "repr_")

    def __init__(self, type_name: str, repr_: str = ""):
        self.type_name = type_name
        self.repr_ = repr_

    def __repr__(self) -> str:
        return f"<{self.type_name} {self.repr_}>"

    def __eq__(self, other) -> bool:
        return (
            isinstance(other, OpaqueValue)
            and other.type_name == self.type_name
            and other.repr_ == self.repr_
        )
