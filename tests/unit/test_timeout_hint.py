"""What a command timeout blames, which is not always the application.

The hint used to say, unconditionally, "The GUI thread is probably blocked. Check for a modal
loop in the AUT." That is one of two causes and it reads as a diagnosis, so it sends people
looking through an application that is working perfectly.

The other cause is that the reply arrived and this process never read it. Under a debugger that
is the usual one: pydevd suspends every Python thread at a breakpoint, socket readers included,
so every command fails identically no matter what the application is doing. Cost real time to
work out from the wrong end.
"""

import threading

from liberaqt.transport import Transport


def _transport_with_reader(alive: bool) -> Transport:
    """A transport whose reader thread is running, or is not."""
    transport = Transport()
    if alive:
        stop = threading.Event()
        thread = threading.Thread(target=stop.wait, daemon=True)
        thread.start()
        transport._reader = thread
        transport._stop_reader = stop  # kept alive for the test's duration
    else:
        transport._reader = None
    return transport


def test_a_dead_reader_is_reported_as_such():
    """No reader means no reply can ever arrive, whatever the application is doing."""
    hint = _transport_with_reader(alive=False)._timeout_hint()
    assert "reader thread is not running" in hint
    assert "GUI thread" not in hint, "a dead reader is not the application's fault"


def test_a_debugger_is_named_when_one_is_attached(monkeypatch):
    """The case that matters: pydevd suspends the reader, and the AUT is fine."""
    monkeypatch.setattr("sys.gettrace", lambda: object())
    hint = _transport_with_reader(alive=True)._timeout_hint()

    assert "debugger" in hint
    assert "PYDEVD_UNBLOCK_THREADS_TIMEOUT" in hint, "name the fix, not just the cause"
    assert "application is most likely fine" in hint, (
        "the whole point is to stop people debugging the wrong process"
    )


def test_the_application_is_blamed_only_when_nothing_else_explains_it(monkeypatch):
    """With a live reader and no debugger, a blocked GUI thread really is the likely cause."""
    monkeypatch.setattr("sys.gettrace", lambda: None)
    hint = _transport_with_reader(alive=True)._timeout_hint()

    assert "GUI thread" in hint
    assert "modal loop" in hint
    assert "debugger" not in hint


def test_the_hint_never_comes_back_empty(monkeypatch):
    """A timeout with no hint at all would be worse than a wrong one."""
    for trace in (None, object()):
        monkeypatch.setattr("sys.gettrace", lambda t=trace: t)
        for alive in (True, False):
            assert _transport_with_reader(alive)._timeout_hint().strip()
