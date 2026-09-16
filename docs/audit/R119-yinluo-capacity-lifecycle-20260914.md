# R119 Yinluo Capacity And Native Calibration

Status: scoped candidate repair verified by focused, associated and full
offline regression. No production acceptance is implied.
Parent: R65 operational retention and `rebuild-stability-20260907.md`.

## Reproduction

Completed native sacrifices can occupy every mutation slot, or reach the
receipt admission limit, while the known balances and business observation
remain current. Automatic scheduling then repeats a capacity refusal instead
of using the reserved read slot. Independent native banner coverage immediately
allows the existing compactor to retire the same completed operations.

The isolated reproduction fails six scheduler/restart/no-reply cases; the
independent-native-coverage control passes. It uses real temporary SQLite,
owned transport receipts and native reducers with smaller capacity constants.
The second run anchors the due time to the real next game-day deadline, not a
24-hour stale-panel boundary. No live identity or game request is involved.

Further reproduction found that repeated unrecognized reads fill the receipt
book and create a sticky capacity gap. Deleting their empty receipts regained
space but lost late/contextless edit ownership; four native lifecycle cases
reproduced absent archives, including a previously useful panel withdrawn by
an edit. A separate full-receipt test reproduced dispatch into a book with no
room to retain a failed query. These are capacity/lifecycle defects, not a
reason to raise the limits or clear a missing-evidence gap.

## Required Contract

1. Capacity inspection is read-only. It uses the same ownership, financial
   coverage and hot/cold integrity rules as actual compaction, without adding
   a synthetic balance or persisting a diagnostic projection.
2. Automatic work selected by the existing controls may use the reserved
   native banner slot only if complete owned commands can free enough capacity
   when missing banner resources are supplied. Missing cultivation coverage,
   unknown souls/outcomes, unsafe ownership and full read slots do not justify
   that query. Hard holds and already-missing-receipt gaps are not cleared.
3. A newer banner that still lacks the necessary resource field is not a
   reason to repeat the same query indefinitely. Native sources and the last
   banner business point distinguish useful fresh coverage from an unchanged
   unresolved dependency. A read without any reply retains the existing owned
   read/timeout/backoff lifecycle.
4. Automatic query failures respect the existing backoff even if a daily
   mutation remains due. Disabled modules/actions, global/account admission,
   active operations and stale owners/configurations retain their controls.
   Manual mutations are not silently replaced with another command.
5. A successful read, financial coverage and archival retirement still share
   the existing resource transaction. SQLite, archive and concurrent-state
   failures cannot consume a mutation slot, remove evidence or claim recovery.
6. Validate the full scheduler -> owned send -> native result -> archive ->
   next permitted mutation chain, including restart, edits, no response and
   known-unsent outcomes. Capacity must actually become reusable; changing a
   message or simply holding every mutation is not a repair.
7. An expired read with retained replies moves to the existing cold archive.
   Its original operation and all native result revisions remain attributable;
   expiry is not terminal success. Balances, conflicts, business points and
   unrelated pending records remain unchanged. No-reply read metadata retains
   the existing R114 lifecycle. No consuming unknown operation is eligible.

## Candidate Implementation

- Capacity inspection simulates the normal compactor with the same coverage
  and in-flight rules. It never saves that projection or invents a balance.
  Only missing sha/named-soul coverage can justify the native banner; missing
  cultivation, unknown soul names/outcomes, holds and full read slots cannot.
  The retained latest banner must still have a valid hot/cold outcome before
  an incomplete panel suppresses an identical repeat. Withdrawn panels do not
  provide that proof.
- Automatic calibration is inserted after the existing configured plan is
  selected. A known-unsent query honors its backoff even while daily work is
  due. Existing global/identity/module/sect/configuration guards and owned
  send-time checks remain in force. Explicit manual mutations report the
  capacity dependency instead of silently sending a different command.
- Full receipt capacity now rejects new owned reads as well as mutations.
  An independent valid native panel can still supply coverage and allow
  retirement before its own reply is inserted. Existing gaps stay held.
- Archive payload v1 remains the terminal-operation contract. Payload v2 is
  strictly an owned `CMD_YINLUO_BANNER` operation in `read_expired`, with exact
  native command/time/result ownership, an unknown/conflicting outcome and no
  financial effects, requirements or scopes. It is not generic unresolved
  mutation archival. No new SQL column or hot-state field was added.
- Expiry resolves exact pending ownership from the staged archive insertion;
  cold insertion, hot removal and shared pending cleanup commit together.
  Preparation and capacity simulation also handle previously saved expired
  read records. Query baselines, including withdrawn/conflicting values, are
  retained independently of hot receipts. A late qualified edit restores only
  its old operation; a contextless edit can invalidate old facts but cannot
  apply its new text as a balance. Exact duplicate transport cannot reopen it.

## Verification

- Initial scheduler reproduction: six failures and one passing native-coverage
  control, `/tmp/xiuxian-r119-native-clock-repro-20260914.xml`.
- Repeated unrecognized-read reproduction: one failure, 12 controls passing,
  `/tmp/xiuxian-r119-read-retention-repro-20260914.xml`.
- Lost read archive/late-edit ownership: four failures,
  `/tmp/xiuxian-r119-expired-archive-repro-20260914.xml`.
- Full read-slot reproduction: one failure, two withdrawn-banner controls
  passing, `/tmp/xiuxian-r119-read-slot-repro-20260914.xml`.
- Final focused capacity suite: **82 passed**, 20.71s,
  `/tmp/xiuxian-r119-focused-final-20260914.xml`. Tests use temporary SQLite,
  real owned transport receipts, native reducers and reloads with external
  networking disabled. They cover the default 64-operation limit, smaller
  receipt limits, read-only UI/inspection, actual automatic continuation,
  unknown/backoff cycles, active callers, disabled and queued controls,
  late/contextless edits, malformed cold payloads, corrupted archives,
  rebound/stale owners and SQLite/false/exception rollback at expiry,
  preparation and restoration. The fault fixture is reloaded before comparing
  pending rows so normal loader-added defaults are not mistaken for data loss.
- Associated Yinluo/Wanxin/resource/persistence/transport/recovery suite:
  **2003 passed, 323 subtests**, 120.58s,
  `/tmp/xiuxian-r119-related-final-20260914.xml`.
- Final full network-isolated suite: **12734 passed, 1275 subtests**, 252.78s,
  `/tmp/xiuxian-r119-full-20260914.xml`. Parsed final JUnit artifacts have zero
  failures, errors and skips; all test sessions completed.
- Configured project Ruff, scoped `F,E9`, compilation, dependency consistency
  and whitespace checks pass. These results do not close the whole-project
  Final Review or any unvalidated production gate.

## Compatibility And Remaining Gates

V1 financial evidence is unchanged. Older candidate validators reject v2 cold
read payloads, so a later code rollback cannot simply ignore or delete those
records. Preserve the database/archive snapshot and review reconciliation
before any production transition. No live database has been migrated.

The 120-point business bound, beneficiary churn, pre-existing missing-receipt
gaps, fully occupied uncovered books, general no-ID migration and complete
forced-stop/storage recovery remain open. Cold evidence has no automatic TTL;
disk growth and per-command history/restore limits still need operational
acceptance. This repair does not supply infinite storage or permit blind
retries when evidence is missing.

No shared transport/CommandAttempt controller, production change, retrying
mutation or flag enablement is allowed. Business-point retirement, pre-existing
capacity gaps, full forced-stop/storage recovery and whole-project Final Review
remain separate open gates. R67 production channel cultivation remains
unvalidated. World Boss/refinement controls, inventory API UI-only policy and
independent Tianxing/deep-retreat behavior remain unchanged.
