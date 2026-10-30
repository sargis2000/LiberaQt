# LiberaQT for Claude Code

A [Claude Code](https://claude.com/claude-code) plugin that teaches Claude to write, run and
debug [LiberaQT](https://github.com/sargis2000/LiberaQt) UI tests for your Qt application.

## Install

```text
/plugin marketplace add sargis2000/LiberaQt
/plugin install liberaqt@liberaqt
```

The plugin teaches Claude to use LiberaQT; it does not install it. You still need the `liberaqt`
package in the environment your tests run in, and an agent for your application's ABI. Run
`liberaqt doctor "<your app>"` to check.

## What it adds

| Component | Kind | What it does |
|---|---|---|
| `liberaqt:write-tests` | skill | How to write reliable LiberaQT tests: the look → write → run → read-the-failure loop, selector choice, `expect` instead of `assert`, fixtures, native versus synthetic input, and the measured limitations of 0.1.x. Claude loads it whenever you work on LiberaQT tests |
| `liberaqt:troubleshoot` | skill | Diagnoses an agent that never attaches, and every LiberaQT error type, in order |
| `liberaqt:explorer` | agent | Launches your application, explores it, and returns verified selectors or a measured answer, so the large object tree stays out of your conversation |
| `liberaqt:test-author` | agent | Turns a plain-language scenario into tests that follow your project's conventions, runs them until they are green, and proves each one can fail |

Things to ask for:

- "Get me selectors for the fields in the Preferences dialog."
- "Write a test that switching units to metric changes the ruler to mm."
- "This test times out on the Save button. Why?"
- "`launch()` says the agent did not report a port."

## Versions

The plugin describes one LiberaQT release line, and its version follows the library's. If you
pin LiberaQT to an older revision, Claude is told to treat the installed package as the authority
whenever the two disagree.

Every API name in the plugin is checked against the library by
`tests/unit/test_claude_plugin.py`, so a renamed method fails LiberaQT's own CI.
