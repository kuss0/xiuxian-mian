# Native Fishing Evidence Report

## Scope

`tools/native_fishing_report.py` reads the local state DB in a single read-only
transaction, including committed WAL data. It does not create or migrate a DB,
import runtime state/persistence, make game requests, reset timers or alter
notifications. Missing or malformed snapshots fail visibly, without printing
configuration secrets or raw receipts.

```sh
.venv/bin/python tools/native_fishing_report.py
```

Use `--db /absolute/path/to/isolated.db` for an offline copy. This is an operator
acceptance aid, not another scheduler, health fuse or automatic notification.
Selection is configuration, not proof of login, availability or execution.

## Evidence Boundaries

- Report each identity's public selection, standalone switch and saved deadline.
  Frozen group identities can still be public-selected; this report never grants
  permission to send Telegram commands or run the MiniApp.
- Validate native cast and supply receipts with the existing pure validators.
  Check identity/account ownership and expose only their phase, not session,
  proof or transport credentials. Unresolved and invalid receipts remain visible
  even for disabled identities. A receipt proves at most one rod, not a whole day.
- Show today's saved quota and daily game-resource summary separately. Do not
  add the latest receipt to that cumulative summary or count a quota as catches.
  Prior-day summaries do not become today's results.
- Distinguish no rod, no companion, quota exhausted and legacy entrance skip.
  A future timer does not turn any of these into a successful settlement.
- Flag summary rods above saved used quota, malformed clocks/counts/receipts and
  legacy pending results. These are investigation signals, not replay triggers.

## October 2 Checkpoint

At 11:26 Asia/Shanghai, 24 identities were read and 23 were fishing-selected:

- 22 still had the pre-repair `legacy_unavailable_skip` evidence. Native routing
  is deployed, but those identities have not yet reached their saved next run.
- Lpprceqei had an accounted native receipt and quota 5/5. Daily saved gains were
  one carp and two waterweed. Its six summary rods versus five used quota is the
  already-documented previous-day recovery counting defect, fixed in `492d54aa`.
  No historical row was rewritten; no sixth cast is inferred from this counter.
- All 23 timers remained October 3 midnight plus their existing jitter. Baji/WA
  return clocks remained 13:33:03/14:11:28; these alone do not authorize an extra
  fishing run today. The inactive historical channel voyage clock is likewise
  only saved evidence, not a new return action.
- No native or legacy fishing receipt was unresolved. Production health and
  watchdog were OK after the earlier checkpoint warning expired naturally.
  Main PID 145376, NRestarts 0, unchanged since the 10:24 runtime deployment.

Validation covers isolated WAL reads, absent DB refusal, native/supply validators,
ownership, corrupt/disabled receipts, old-day summaries, false completion,
strict counts/flags, input immutability and a network-denied CLI process that
asserts runtime state/persistence were not imported. No production restart is
required for this operator-only tool.

Results: 37 new report tests; the combined report/journal/supply/store/voyage/
daily-report suite passed 209 tests in 9.57 seconds. Ruff, py_compile and
`git diff --check` passed. The full runtime suite was not repeated for this
tool-only change; its last deployed baseline remains 15,436 passed.

The next natural scheduling/return handoff and new-day summary remain open.
The native gift Lab remains blocked on durable/idempotent queue handoff.
