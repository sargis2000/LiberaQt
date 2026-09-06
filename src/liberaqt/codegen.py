# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Sargis Khachatryan
"""Turn recorded actions into a runnable pytest module."""

from __future__ import annotations

from typing import Any

HEADER = '''"""Recorded by `liberaqt record`. Review before committing:
selectors marked TODO are position-dependent and will break on the next layout change.
"""

from liberaqt import expect


def {test_name}(app):
'''


def _literal(value: Any) -> str:
    return repr(value)


def render_action(action: dict[str, Any]) -> list[str]:
    """Render one recorded action as Python source lines.

    Unrecognised actions become a comment rather than being dropped, so a recording never
    silently loses a step.

    Args:
        action: One action from the recorder.

    Returns:
        Source lines, already indented for a test body.
    """
    kind = action.get("action")
    selector = action.get("selector", "")
    brittle = action.get("brittle", False)
    comment = "  # TODO: brittle selector" if brittle else ""
    loc = f"win.locator({_literal(selector)})"

    if kind == "click":
        button = action.get("button", "left")
        count = action.get("count", 1)
        if button == "left" and count == 1:
            return [f"    {loc}.click(){comment}"]
        if count == 2:
            return [f"    {loc}.double_click(){comment}"]
        return [f"    {loc}.click(button={_literal(button)}){comment}"]
    if kind == "fill":
        return [f"    {loc}.fill({_literal(action.get('text', ''))}){comment}"]
    if kind == "key":
        return [f"    {loc}.press({_literal(action.get('key', ''))}){comment}"]
    if kind == "check":
        verb = "check" if action.get("checked") else "uncheck"
        return [f"    {loc}.{verb}(){comment}"]
    if kind == "select_item":
        return [f"    {loc}.select_item(text={_literal(action.get('text'))}){comment}"]
    if kind == "menu":
        return [f"    win.menu({_literal(action.get('path'))}).trigger(){comment}"]
    if kind == "window_opened":
        return [f"    win = app.window(title={_literal(action.get('title'))})"]
    if kind == "assert_text":
        return [f"    expect({loc}).to_have_text({_literal(action.get('text'))}){comment}"]
    return [f"    # unhandled recorded action: {action!r}"]


def render(actions: list[dict[str, Any]], test_name: str = "test_recorded") -> str:
    """Render a whole recording as a runnable pytest module.

    Args:
        actions: Recorded actions, in order.
        test_name: Name for the generated test function.

    Returns:
        Complete Python source, including imports and the test signature.
    """
    lines = [HEADER.format(test_name=test_name)]
    if not actions or actions[0].get("action") != "window_opened":
        lines.append("    win = app.window()")
    for action in actions:
        lines.extend(render_action(action))
    lines.append("")
    return "\n".join(lines)
