# Read-Only Duel Closure Preflight

## Scope

The October 7 daily-close incident left the process and pending queue healthy
while the business schedule was wrong. The existing one-minute defensive
preflight now checks the durable closure fact introduced by `51c2e348`.

For enabled identities with duel enabled and a completion marker for the current
UTC+8 game day, report `at_risk` if progress reopens, a battle anchor remains,
or the next schedule falls before the next game-day boundary (including a
missing timer). Valid equipment restoration phases remain `watch`, but cannot
hide another pending battle. `restored` is a terminal phase, not an exemption.
Previous-day markers, explicitly disabled modules/identities and old databases
without the marker are not judged against this new invariant.

This is a state consistency check, not an independent historical-send audit,
proof of tomorrow's exact schedule, or verification of every equipment item.
It does not authorize a retry, reset counters, change switches, send Telegram
notifications, or take over CommandAttempt. It uses the existing systemd timer;
no daemon or new background loop is added. SQLite opens with `mode=ro`, so a
missing path fails instead of silently creating an empty database.

## Validation

- Initial fixtures: 12 failures / 2 passing controls (the helper and integrated
  warning were absent). These are missing-coverage tests, not new live failures.
- Focused preflight, duel, semantic-report, health-observer and watchdog suite:
  393 passed / 24 subtests.
- Separate review selection covers routing, daily reports, persistence, Tianxing
  timeline lifecycle/evidence and preflight: 400 passed / 10 subtests.
- Temporary SQLite integration verifies unchanged file bytes and legacy schema
  compatibility. Missing-file and UTC+8 midnight tests cover the read boundary.
- Ruff, compileall and diff checks pass. Initial acceptance used the scoped
  suites above; all tests isolate game state.
- A read-only Lab invocation against the actual production paths at 07:08:45
  reports Lpprceqei closure healthy, pending queue empty, and only the existing
  inactive standalone-listener note. No main service restart is required.

Main runtime remains `51c2e348`; Lpprceqei's switch is restored on, completed
day October 7, progress zero, observed baseline five, next October 8 01:38:41.

## Loaded Observation

`8aac83fe` merged and pushed to `xiuxian-mian/main`. Production-directory
isolated checks passed 34 tests. The existing systemd timer ran the new probe
at 07:10:59; its journal explicitly shows the healthy duel closure check and
empty pending queue, with exit success. Follow-up CLI checks remain healthy
for this invariant. Main supervisor/worker and observer/watchdog PIDs did not
change. This helper adds no Telegram delivery and no automatic response to an
invariant violation. Tomorrow's natural batch boundary remains to be observed.

After both patches were integrated, the full isolated regression completed:
**16764 passed / 1486 subtests**, 457.09 seconds. JUnit:
`/tmp/xiuxian-duel-and-preflight-integrated-20261007.xml`.
No service restart or game action was performed for this verification.
