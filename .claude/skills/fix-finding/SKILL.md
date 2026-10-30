---
name: fix-finding
description: Fix a LiberaQT defect the way this repository does - reproduce and measure it against a real application, pin it with a test that fails on the old code, fix it at the right layer, rebuild agents if C++ changed, re-measure, sweep for the same bug class elsewhere, and update every place that described the old behaviour (CLAUDE.md, docs/, the QA agent's known list, the user plugin). Use for a QA finding, a bug report, or a defect noticed while doing something else.
---

# Fixing a finding

Every fix in this repository's history follows the same shape, and the commit messages say so:
"each reproduced before the fix and measured after", "pinned by a test that fails on the old
code". Defects here usually produce a *wrong answer*, not an error. So a fix that has not been
seen to change a measured number has not been shown to work.

## 1. Reproduce it live, and write down the measurement

Drive a real application (Qt Assistant or qmleasing; Libero only if the finding is about Libero).
Keep the script, because step 6 reruns it unchanged.

```python
from liberaqt import liberaqt

with liberaqt(default_timeout=10.0) as lq:
    app = lq.launch(r"C:/Qt/6.7.3/mingw_64/bin/assistant.exe", timeout=90.0)
    ...  # the smallest sequence that shows the wrong answer, printed verbatim
```

Record the exact output, plus the timing when the finding is about waiting: "a `timeout=2` call
took 20.00s" is a finding, while "the timeout seems ignored" is a guess. If it does not reproduce,
stop and say so, quoting what you ran.

Classify it before fixing it. The cure differs for each:

| Verdict | Fix |
|---|---|
| library defect | the code |
| documentation defect | the page, and possibly an error message that says the same wrong thing |
| design friction | an API change, which is the user's call, so propose it before building it |

## 2. Pin it with a test that fails on the old code

- **Client-side** (selector parsing, retry, error mapping, `expect`, the pytest plugin): a unit
  test in `tests/unit/`. Script the agent's replies rather than running Qt:
  `ItemViewSession` in `test_item_views.py` fakes `Session.call`, `FakeAgent` in
  `test_transport.py` is a real in-process socket agent, and `test_pytest_plugin.py` runs inner
  pytest sessions through `pytester`.
- **Agent-side** (anything in `agent/src/`): nothing in `tests/unit` executes a line of C++, so the
  pin is a test in `tests/e2e/qt/`, using the `qt_tool` fixture and skipping cleanly when the tool
  is absent.

Then **prove the test pins the bug**: run it against the unfixed code and watch it fail for the
reason you expect (`git stash` the fix, or write the test first). A regression test that passes on
the broken code is decoration.

## 3. Fix it at the right layer

Anything that can live in Python does. In C++, the constraints in CLAUDE.md apply (`QStringLiteral`,
`compat.h` for every `QT_VERSION`, no throwing across the event loop, the right `register*Command`
helper). A fix that makes something "work" by succeeding where a user could not (writing a
read-only field, typing into whatever has focus) is a new false pass, not a fix.

## 4. Sweep for the same bug class

This repository's bugs recur in pairs, because the same pattern was copied. A fix that repairs one
instance and leaves its sibling is half a fix. Examples:

- `resolveOrNull` turning a dead handle into "search everything" was fixed for `object.find` and
  `object.tree` with `resolveRoot()`. `Screenshot::grab` still uses `resolveOrNull` and falls back
  to `activeWindow()`: measured, `dlg.screenshot()` after the dialog closed returns the main
  window's 1536x864 image, with no error.
- F11 made `type()` refuse a non-focusable *widget*; a `QQuickItem` target still reports success.
- `dumpTree` had its own child walk that skipped the Quick splice `visualChildren` does.

Grep for the pattern you just fixed, and either fix each sibling or list it in your report.

## 5. Rebuild agents if `agent/` changed

An installed agent is a binary in the cache, and a checkout does not update it. Rebuild every ABI
you will verify against: `liberaqt agents build --tag <tag>` for each tag in
`liberaqt agents list`. That command writes the agent cache, so it is not pre-approved in
`.claude/settings.json`; ask before running it. Then run `liberaqt doctor`, which flags a build that
lacks its plugin key as `STALE`.

## 6. Re-measure

Rerun the step 1 script, unchanged, and record the new output. Then run the suites:

```bash
python -m ruff check src tests
python -m pytest tests/unit -q
python -m pytest tests/e2e/qt -q --liberaqt-qt-bin "C:/Qt/6.7.3/mingw_64/bin"
python -m pytest tests/e2e/qt -q --liberaqt-qt-bin "C:/Qt/6.5.3/mingw_64/bin"   # for agent changes
python -m mkdocs build --strict                                                # if docs/ changed
```

Run the Libero suites only if the change can affect them, and say so: they write projects to disk
and are `ask` in settings.

## 7. Update everything that described the old behaviour

A fixed bug that is still documented as broken will be routed around for months. Check each of:

- **CLAUDE.md**: the project-state gaps, the rules, the counts.
- **The topic skill for the area** (`qml-support`, `pytest-internals`, `agent-loading`,
  `agent-release`, `docs-site`), which holds the detail and history CLAUDE.md only summarises.
  Keep each fact in one place: if CLAUDE.md and a skill both said it, fix both.
- **`docs/`**: the guide page, `troubleshooting.md`, `ACTIONS.md` or `SELECTORS.md`.
- **`.claude/agents/qt-automation-tester.md`**: move the item from "Open defects" to "Fixed, so
  treat a recurrence as a regression".
- **`.claude/agents/live-probe.md`**: its "Things that will mislead you" list.
- **`plugins/liberaqt/`**: delete the `references/limitations.md` entry (its "Fixed yet?" check
  now passes), fix any skill text that warned about it, and **bump `version` in
  `.claude-plugin/plugin.json`**. Users are pinned to that version, so without the bump nobody
  receives the change.
- **The error message**, if it gave the wrong advice.

`pytest tests/unit/test_claude_plugin.py` catches a renamed or removed API in the plugin and in
`.claude/`. It cannot catch advice that has become wrong; only this step does.

## 8. The commit

Match the existing messages: what was wrong, as measured; the fix; how it is pinned; what was
verified, with counts ("63 live tests against Qt 6.7.3 and 6.5.3, 514 unit tests, ruff clean").
Commits in this repository carry no `Co-Authored-By` trailer.
