"""Addressing item views: auto-wait, column numbering, and reading a bounded slice.

Regression cover for four defects found by driving Qt Assistant, all of which produced a wrong
answer rather than an error:

* ``row()`` / ``item()`` / ``cell()`` did not retry, so a view still filling from a worker
  thread raised immediately -- the one corner of the API that did not wait.
* ``to_records()`` could not be bounded, so polling a 34,517-row view cost a megabyte a time.
* the ``headers`` the agent already returns were discarded by the client.
* the column keys ``to_records()`` produced were 1-based while ``cell()`` is 0-based.
"""

import pytest

from liberaqt.errors import ObjectNotFoundError
from liberaqt.errors import TimeoutError as LiberaQtTimeoutError
from liberaqt.locator import Locator
from liberaqt.selectors import parse
from liberaqt.waits import TimeoutPolicy


class ItemViewSession:
    """Resolves one handle, then answers item-view commands from a script."""

    def __init__(self, *, fills_after=0, rows=None, headers=None):
        self.timeouts = TimeoutPolicy(2.0)
        self.slowmo = 0.0
        self.calls = []
        self._attempts = 0
        self._fills_after = fills_after
        self._rows = rows if rows is not None else [{"0": "first"}]
        self._headers = headers if headers is not None else ["0"]

    def call(self, cmd, params=None, timeout=None, selector=None):
        params = params or {}
        self.calls.append((cmd, params))
        if cmd == "object.find":
            return {"handles": ["view1"]}
        if cmd == "widget.item_rect":
            self._attempts += 1
            if self._attempts <= self._fills_after:
                raise ObjectNotFoundError("row 0 is out of range; the view has 0")
            return {"handle": "item1"}
        if cmd == "widget.model_data":
            rows = self._rows
            limit = params.get("max_rows")
            if limit is not None:
                rows = rows[:limit]
            return {"rows": rows, "headers": self._headers}
        raise AssertionError(f"unexpected command {cmd}")

    def wait_for_idle(self, **kw):
        pass


def _view(session):
    return Locator(session, parse("QTreeView"))


# ------------------------------------------------------------------ auto-wait


@pytest.mark.parametrize("call", [
    lambda v: v.row(index=0),
    lambda v: v.item("Synthesize"),
    lambda v: v.cell(0, 0),
])
def test_an_item_view_lookup_waits_for_the_model_to_fill(call):
    """A view populated from a worker thread is the commonest async thing a desktop app does."""
    session = ItemViewSession(fills_after=3)
    located = call(_view(session))

    assert located is not None
    attempts = sum(1 for cmd, _ in session.calls if cmd == "widget.item_rect")
    assert attempts == 4, f"expected retries until the model filled, made {attempts} attempts"


def test_a_lookup_that_never_succeeds_still_gives_up():
    session = ItemViewSession(fills_after=10_000)
    with pytest.raises(LiberaQtTimeoutError) as excinfo:
        _view(session).row(index=0, timeout=0.2)
    assert "row" in str(excinfo.value)


def test_timeout_zero_means_one_attempt():
    session = ItemViewSession(fills_after=10_000)
    with pytest.raises(LiberaQtTimeoutError):
        _view(session).cell(0, 0, timeout=0)
    attempts = sum(1 for cmd, _ in session.calls if cmd == "widget.item_rect")
    assert attempts == 1, f"timeout=0 must not retry, made {attempts} attempts"


# ------------------------------------------------------------------ reading the model


def test_to_records_can_be_bounded():
    session = ItemViewSession(rows=[{"0": str(i)} for i in range(100)])
    assert len(_view(session).to_records(max_rows=5)) == 5
    sent = [p for c, p in session.calls if c == "widget.model_data"][0]
    assert sent["max_rows"] == 5, "the bound has to reach the agent, not just trim locally"


def test_to_records_unbounded_sends_no_limit():
    session = ItemViewSession()
    _view(session).to_records()
    sent = [p for c, p in session.calls if c == "widget.model_data"][0]
    assert "max_rows" not in sent


def test_headers_are_reachable():
    """The agent has always returned them; the client used to drop them on the floor."""
    session = ItemViewSession(headers=["Name", "Value"])
    assert _view(session).headers() == ["Name", "Value"]


def test_reading_headers_does_not_drag_the_whole_model_across():
    session = ItemViewSession(rows=[{"0": str(i)} for i in range(50_000)])
    _view(session).headers()
    sent = [p for c, p in session.calls if c == "widget.model_data"][0]
    assert sent["max_rows"] == 0
