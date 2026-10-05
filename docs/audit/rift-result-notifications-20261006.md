# Rift Result Notification Deduplication

Base: `9488f9e8`. Lab: `/root/xiuxian-rift-notification-20261006`.
Status: validated candidate; main-worker loading deferred to normal maintenance.

## Natural Evidence

WA's single rift command `1283915` received final official edit `1283916`
at 06:49:58 CST on October 6. Tianji +1, contribution +30, space fragment x1,
fourth-grade demon core x5 and nine-heavens thunder wood x1 were recorded.
Reply pending and the previous preparation error cleared, with no duplicate
game send. Existing change protection remained available for 18h58m.

The same result produced two notification layers:

- High-priority receipt `ca26034db9d04d60a92dc76e8a17c50e` confirmed at
  06:49:59: 96 UTF-16 units, three lines and one mention link.
- An ordinary summary row for the identical materials, sequence 7. The
  06:57:39 digest confirmed as `fd3476ad56fe4dd282ab90145b21522f` with
  186 UTF-16 units, four lines and no mention links; pending became zero.

These have different titles and rendered payload hashes. An exact-text
duplicate counter alone cannot detect this event-level duplication.

## Narrow Change

After the existing atomic result/inventory save, choose one notification:
the original high-priority Tianxing result when the official text contains
Tianxing evidence, otherwise the original ordinary result. Delete the helper
that existed solely for the extra high-priority send. No reducer, reward,
cooldown, evidence ledger, identity admission or game transport changes.

False, None, exceptions and cancellation do not trigger a second ordinary
copy. Committed game state survives, and replay of the same official result
does not reapply rewards or resend the notification. This is one module-level
notification call, not an exactly-once transport guarantee: urgent sender
Bot/account fallback and durable notification retry remain separate TG-06
debts. An unconfirmed notification may remain unreported; do not blindly
replay gameplay or use another title as a delivery recovery mechanism.

Non-Tianxing success, storm, cooldown, fatal and escape paths retain their
existing notification behavior. Identity replacement during notification
cannot write terminal state into a new identity.

## Verification And Second Review

- Focused: 203 passed, five subtests.
- Full isolated suite: 16033 passed, 1461 subtests, 461.44 seconds.
  JUnit: `/tmp/xiuxian-rift-notification-full-20261006.xml`.
- Separate maintainer regression: 933 passed, 16 subtests, covering rift
  lifecycle/recovery, Tianxing effects and notification delivery policy.
- Ruff, byte compilation and diff whitespace validation pass.
- Tests include successful/false/None notification results, real isolated
  SQLite reload, exceptions/cancellation, original non-Tianxing paths,
  identity replacement, result replay, inventory and cooldown invariants.
- All tests set `XIUXIAN_ALLOW_LIVE_TEST_DB=0`; no synthetic game action or
  Telegram test message. Initial and second review are separate passes by
  the same maintainer, not independent external review.

## Release Boundary

Merge code only and preserve the runtime-learned quiz bank. No new restart
for this duplicate copy: the main worker still runs `f7958deb`; previously
staged startup wording and this patch load at the next normal maintenance.
Do not claim the existing 06:57 digest verifies new-code behavior. Natural
single-result acceptance remains pending after loading. Existing queued and
held notifications are untouched; rollback is code-only, never game state.
