# Waiting and assertions

GUI tests fail intermittently for one reason above all others: they check something before the
application is ready. LiberaQT's answer is that **everything retries by default**, so you should
almost never write a sleep.

## `expect` — assertions that retry

```python
from liberaqt import expect

expect(win.locator("QLabel#status")).to_have_text("Ready")
```

That polls until the label says `Ready` or the timeout expires. A plain `assert` reads the value
once, which is a race you will eventually lose.

### Available assertions

| Assertion | Passes when |
|-----------|-------------|
| `to_exist()` | the object resolves |
| `to_be_visible()` | it is visible |
| `to_be_hidden()` | it is not visible |
| `to_be_enabled()` | it accepts input |
| `to_be_disabled()` | it does not |
| `to_be_checked(checked=True)` | its check state matches |
| `to_have_text("...")` | its text equals exactly |
| `to_contain_text("...")` | its text contains |
| `to_match_text(r"...")` | its text matches a regular expression |
| `to_have_value(v)` | its value equals |
| `to_have_property("name", v)` | that `Q_PROPERTY` equals |
| `to_have_count(n)` | the selector matches exactly `n` objects |
| `to_have_title("...")` | (windows) the title equals |
| `to_be_active()` | (windows) it is the active window |

### Negation

```python
expect(win.locator("QProgressBar")).not_.to_be_visible()
```

`not_` inverts the assertion **and keeps the retrying**: it waits for the condition to stop being
true, rather than checking once that it is already false.

### Per-assertion timeouts

```python
expect(win.locator("QLabel#status")).to_have_text("Done", timeout=60)
```

Useful when one step is legitimately slow — a build, an export — and you do not want to raise the
timeout for the whole suite.

## Timeouts

```python
with liberaqt(default_timeout=15.0) as lq:      # for everything
    ...

loc.click(timeout=30)                            # for one call
loc.click(timeout=0)                             # exactly one attempt, no retry
```

`timeout=0` is the right choice when you *expect* a failure — probing whether something exists,
or asserting that an action is refused. Without it you wait out the full timeout for a result you
already predicted.

## How a failure surfaces

`retry()` retries any error whose class sets `retryable = True`, and re-raises the last one as
`LiberaQtTimeoutError` with the original on `__cause__`.

!!! warning "A not-found locator surfaces as a *timeout*"
    This is the single most confusing thing for newcomers. To assert on the real problem, look at
    the cause:

    ```python
    import pytest
    from liberaqt import LiberaQtTimeoutError            # (1)!
    from liberaqt.errors import ObjectNotFoundError

    def root_cause(exc):
        while exc.__cause__ is not None:
            exc = exc.__cause__
        return exc

    with pytest.raises(LiberaQtTimeoutError) as excinfo:
        win.locator("QPushButton#nope").click(timeout=1)
    assert isinstance(root_cause(excinfo.value), ObjectNotFoundError)
    ```

    1.  `LiberaQtTimeoutError` is exported from the package root, **not** from `liberaqt.errors`
        -- where the class is named `TimeoutError`, shadowing the builtin.

    **The depth of the chain depends on what you called**, so walk it instead of indexing into it:

    | Call | Chain |
    |------|-------|
    | `loc.click()` | `TimeoutError -> TimeoutError -> ObjectNotFoundError` |
    | `loc.resolve()` | `TimeoutError -> ObjectNotFoundError` |

    An action retries the action *and* the resolution inside it, so it is one level deeper than
    people expect.

Errors carry context so you can debug without re-running: the selector, the resolved handle if
there was one, near-miss suggestions, and a one-line remediation hint.

| Exception | Retryable | Means |
|-----------|-----------|-------|
| `ObjectNotFoundError` | yes | nothing matched |
| `AmbiguousSelectorError` | yes | several matched a strict locator |
| `NotActionableError` | yes | found, but not clickable yet |
| `StaleObjectError` | yes | the object was destroyed |
| `UnsupportedOperationError` | **no** | the agent does not implement it |
| `InvalidSelectorError` | **no** | the selector does not parse |

A non-retryable error fails immediately rather than burning the timeout, which is why an
unimplemented command reports in milliseconds instead of thirty seconds.

## Explicit waits, when you need them

```python
app.wait_for_idle(timeout=30)                    # queue drained, no animations
app.wait_for_window(title="Export", timeout=60)
win.wait_for_signal("clicked", timeout=5)
```

`wait_for_idle` is the one to reach for after triggering something long-running. It is what every
action already does internally.

## The lower-level helpers

```python
from liberaqt.waits import retry, wait_until

wait_until(lambda: some_condition(), timeout=10)
```

Use these when you are waiting on something LiberaQT cannot see — a file appearing on disk, an
external process finishing.

!!! tip "Watch a directory rather than guessing a duration"
    When driving a tool that writes output, waiting for its output directory to stop changing is
    far more robust than a fixed sleep, and adapts to a slow machine.
