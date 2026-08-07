# LiberaQT — Implementation plan

Sizes assume one experienced C++/Qt developer plus one Python developer, or one person doing both
at roughly double the elapsed time.

## Milestone 0 — Walking skeleton (2 weeks)

Goal: `click a button in a QWidget app from Python`, end to end, Linux + Qt 6.7 only.

* Agent: generic plugin loads, TCP server, JSON framing, `session.ping`, `session.info`.
* Agent: object registry, `object.find` with type + objectName only, `input.click`.
* Client: transport, launcher, `Application`, `Window`, minimal `Locator.click()`.
* Sample widget app + one passing test.
* CI: Linux build of the agent, run the sample test under `xvfb-run`.

Exit criterion: `pytest examples/tests/test_widgets.py` is green in CI.

## Milestone 1 — Usable for real widget tests (4 weeks)

* Full selector grammar + parser (client) and evaluator (agent).
* Property get/set, invoke; QVariant <-> JSON converter with the full type table.
* Input: type/fill/press/hover/double-click/right-click/wheel/drag.
* Auto-wait + actionability checks + `sync.wait_idle`.
* `expect()` assertion suite.
* Item views: `widget.item_rect`, `widget.model_data`, `select_item`, `to_records()`.
* Menus, tabs, dialogs, modal handling.
* Screenshots.
* Structured errors with near-miss diagnostics.
* Windows build of the agent (MSVC) + Windows CI.

Exit criterion: a realistic 30-test suite against a nontrivial widget app, green three times in a
row on both platforms.

## Milestone 2 — QML / Qt Quick parity (3 weeks)

* Splice `QQuickItem` tree into the object tree; QML type names; `qmlId` resolution.
* `mapToScene` geometry, input delivery to `QQuickWindow`.
* `quick.evaluate` (JS in item context), `quick.list_view_item` with forced delegate creation.
* Animation-aware idle detection.
* `QQuickWidget` / `QQuickView` embedding cases.
* Sample QML app + test suite.

Exit criterion: the same test scenario passes against a widget and a QML implementation of the
same UI, with only selector strings differing.

## Milestone 3 — Qt 5.15 support (1.5 weeks)

* Compatibility shims (`QRegExp` vs `QRegularExpression`, `QVariant` API changes,
  `QWheelEvent`/`QMouseEvent` constructor differences, `qAsConst`, moc differences).
* Second CI matrix dimension; agent artifacts for 5.15 on both platforms.

Exit criterion: identical test suite green on 5.15 and 6.7 without test-side changes.

## Milestone 4 — Tooling and DX (3 weeks)

* Recorder: event filter, semantic action inference, selector ranking, codegen.
* `liberaqt inspect`: live object tree browser + REPL + `--coverage` report on `objectName` usage.
* `liberaqt doctor`.
* pytest plugin polish: screenshot + protocol trace on failure, `--slowmo`, `--headless`.
* Agent distribution: GitHub Releases + `liberaqt agents install`.
* Documentation site, migration guide from Squish.

Exit criterion: a developer with no context can go from `pip install` to a passing recorded test
in under 15 minutes on a fresh machine.

## Milestone 5 — Hardening (ongoing)

* Attach-to-running-process (Linux ptrace, Windows CreateRemoteThread).
* Visual comparison assertions with masking.
* Video/trace recording of a run.
* Multi-process apps (child process auto-attach).
* Qt 6.8/6.9 as they land; keep the matrix to N-2 minor versions.
* Performance: batch commands, client-side object cache with invalidation on `object.destroyed`.

---

## Risk register

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Agent ABI mismatch with user's Qt build | High — nothing works | Auto-detect, fail loudly with a fix command; ship build-from-source path; recommend in-app embed for teams that build their own Qt |
| Windows EDR flags injection | High for enterprise adoption | Prefer generic-plugin mechanism (no injection at all); sign binaries; document the in-app embed alternative |
| Flaky idle detection (custom event loops, threads) | Medium — flaky tests | Layered heuristics + explicit escape hatches; make idle policy configurable per action |
| Virtualised views (ListView delegates not created) | Medium | Explicit `list_view_item` that scrolls/forces creation rather than pretending the item exists |
| Custom-painted widgets with no child objects | Medium | Document coordinate + property fallbacks; encourage `QAccessible` in the AUT |
| Scope creep into image matching / multi-language | Medium — never ships | Written non-goals; revisit only after Milestone 4 |
| Bus factor on the C++ agent | Medium | Keep the agent small; push all logic that can live in Python into Python |

## Definition of done for v1.0

* Widget + QML, Qt 5.15 + 6.7, Windows + Linux, all green in CI on every commit.
* Recorder produces runnable tests for the sample apps.
* Documented protocol with a conformance test suite (so alternative agents are possible).
* Zero known crashes of the AUT caused by the agent.
* `pip install liberaqt && liberaqt doctor` gives a clean bill of health on a stock machine.
