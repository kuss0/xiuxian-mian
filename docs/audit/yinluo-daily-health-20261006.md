# Yinluo Daily Health

Base: `3fc77bdb`. Candidate only; main runtime is unchanged.

## Scope

The daily-priority fix has natural acceptance, but the existing observer had
no diagnostic for the starvation it fixed. Add one narrowly scoped daily check
to the existing module summary. It reads persisted snapshots only. It cannot
send, retry, recover, unlock, change timers, clear pending or change balances.

The warning requires daily sacrifice enabled and due, a fresh banner hint,
no unresolved resource operation/hold/gap, and the exact observed unaffordable
soothe state. It ignores auto_next_time for this independent daily obligation.
The overdue clock starts no earlier than today's 00:01 and warns only after
ten minutes. This is diagnostic grace, not a change to the game's cooldown.

Completed today, explicit future cooldown, disabled daily work, missing
evidence, actual pending, financial hold, channel freeze, global pause and
phaseful settlement risk have distinct diagnostic reasons. Running retreat
alone does not suppress this check when settlement is not near. All output
uses the existing observer cadence; no new TG notification path is added.

This is not a complete Yinluo auditor: a missing cultivation baseline is still
unresolved, and other possible starvation patterns are not claimed covered.
The module label is specifically Yinluo daily sacrifice, not overall health.

## Evidence

- Read-only replay of `/root/xiuxian-stopped-before-yinluo-daily-20261006.db`
  at 03:12 reports `soothe_starves_daily` for 8613500668. The persisted last
  daily was September 18, auto wait was future and soothe lacked cultivation.
- Read-only current production snapshot reports `completed` after the 03:14
  accepted daily reply; the current soothe calibration error remains visible.
- Initial focused tests: 188 passed, 4 subtests. Boundary fixtures cover daily
  rollover/grace, explicit CD, financial holds, resource operations, invalid
  evidence, disabled work, channel/global holds and near settlement.
- SQLite integration runs with query_only and verifies no changes. Inputs
  are checked for mutation. Full suite: 15988 passed, 1449 subtests, 467.73s,
  `/tmp/xiuxian-yinluo-health-20261006.xml`.
- Second maintainer pass: 488 passed, 15 subtests, including two additional
  malformed-operation regressions added after full-suite collection. Reviewed
  read-only DB integration and scheduler suppression; Ruff/compile/diff pass.
  This is a separate maintainer review, not an independent external audit.
- Final read-only integration against production has no module warning; only
  the existing frozen-channel informational record. The held notification
  warning is from separate delivery-health evidence and remains untouched.

## Activation

After validation, merge only observer/tests/docs, preserve runtime quiz data,
and restart only `xiuxian-health-observer.service`. Do not restart the game
worker, run gameplay probes or alter switches. Observe the persistent monitor
and read-only snapshots before marking this diagnostic debt complete.
