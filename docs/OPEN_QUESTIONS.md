# Open questions

Things I could not decide for you. Answers here will change the plan; none of them block
Milestone 0.

## Product

1. **Who is the user?** Your own QA team (you control the app's build -> the in-app embed option
   in `INJECTION.md` becomes the default and a lot of pain disappears), or the public (injection
   must work on binaries you have never seen)?
2. **Is Squish migration a goal?** If teams are moving off Squish, a `squish2qtdriver` script that
   converts `.py` Squish scripts and object maps mechanically is worth roughly a milestone of its
   own, and is the single strongest adoption lever.
3. **Do you need a recorder in v1**, or is hand-written test code acceptable at first? The recorder
   is ~3 weeks and is the most-demoed, least-used feature in most tools of this class.
4. **Is Qt Commercial or Qt Open Source in play?** Only affects which Qt builds you must test the
   agent ABI against.

## Technical

5. **Do your apps use a custom `QAbstractItemModel` heavily?** If so, model-level assertions
   (`to_records()`) matter more than pixel-level ones and should move earlier.
6. **Any custom-painted widgets** (no child QObjects, everything drawn in `paintEvent`)? Those need
   a coordinate/property fallback strategy, and possibly a small `QAccessible` contribution on the
   app side.
7. **Multi-process / out-of-process rendering?** Chromium-based `QtWebEngine` views need a separate
   strategy (CDP over the embedded devtools port) — worth knowing early.
8. **Static or dynamic Qt link?** Static kills the plugin mechanism; the in-app embed becomes
   mandatory.
9. **Compiler/toolchain on Windows** — MSVC 2019 or 2022? The agent must match.
10. **Headless CI?** Linux `offscreen`/`xvfb` is easy; Windows headless needs a real session
    (interactive service or a VM with autologon).

## Governance

11. **Licence:** Apache-2.0 assumed. MIT if you want maximum permissiveness with no patent grant;
    LGPL only if you plan to link Qt statically and distribute.
12. **Project name:** `qtdriver` is used as a placeholder throughout. Check PyPI availability and
    that it does not collide with the Qt Company's trademarks — a name containing "Qt" is
    acceptable descriptive use, but "QtDriver by the Qt Company"-style branding is not.
13. **Repo layout:** monorepo (client + agent, as scaffolded) or split repos? Monorepo is
    recommended: the protocol is versioned in lockstep.
