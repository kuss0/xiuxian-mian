# R118 Yinluo Anchored Legacy Completion Migration

Status: scoped offline candidate verified; not production acceptance.
Parent: R65, R115-R117 and `rebuild-stability-20260907.md`.

## Required Behavior

Old slot pending records have a send time and slot/target, but generally no
message ID. The old sender also retained a shared pending row with the actual
chat/message/command, send interval and `yinluo[-auto]-action-time` operation
tag. Native terminal receipts can therefore resolve some old records without
inventing a new send or treating a current balance as an outcome.

This work must make that existing evidence usable, not merely replace one
permanent hold with another. Unproved no-ID work is still a separate gate.

## Review Findings

The first migration reproduction had 16 failures and 12 passing controls.
It covered native completion, retained hot/cold evidence, scheduler/manual
reachability and rollback. The follow-up boundary suite reproduced 21 failing
cases: an empty-book cultivation shortcut ignored malformed legacy commands,
and conflicting or mistyped slot aliases could falsely release a hold.

Commit review additionally reproduced mismatched optional transport message
IDs/commands and staged slot/key types changed from integer to boolean/float.
Ordinary Python dictionary equality treated those changes as unchanged state.
Snapshots now compare nested types and keys, not just equal scalar values.
The final tests also use the real `auto_config.collect` switch to cover a
boolean-to-integer configuration change; an earlier fixture had incorrectly
named that field `collect_enabled`.

## Contract

1. Recognized old Yinluo command tags are legacy evidence even with an existing
   valid book. Their unresolved spending must not be ignored at admission.
2. A migration needs a current identity/account, exact original shared pending
   row, native original command interval and a retained terminal receipt. Use
   the existing hot/cold receipt validator; no family/text/name-only matching.
   Modern UUID operations are not reclassified as old work to clear a hold.
3. Each nonempty old slot payload must match exactly one original command per
   slot, with the same send time and action/target. Conflicting explicit owner,
   chat, message or operation metadata, malformed payloads, ambiguous matches
   and missing originals prevent migration. Slot aliases and transport message
   keys must retain their integer types; optional command/message aliases
   cannot contradict their primary source. Never restore pre-send snapshots.
4. Release `legacy_pending` only when at least one concrete legacy completion
   is proved and all remaining legacy causes are accounted for. Empty-looking
   old holds, the invalid-root marker, unowned pending summaries and unresolved
   provider/beneficiary work remain held. Stronger holds/gaps are not cleared.
5. The original facts, pending removal, observation update and hold transition
   share the normal resource transaction for incoming replies. Recovery from
   existing retained receipts uses the same proof and transaction. Failed saves
   restore all staged fields; source/config/owner changes invalidate a staged
   migration, including equal-looking but differently typed nested values or
   keys. Commit revalidates the retained hot/cold proof and current Wanxin
   pending evidence. No financial delta is replayed just to clean up lifecycle
   state.
6. Normal passive replies, bounded local-log recovery, due scheduler recovery
   and manual admission reach this path. The log window uses original pending
   times and keeps the existing 24-hour/two-tail bounds. There is no Telegram
   history request, new retry controller or automatic consuming resend.
7. Completed migration requests normal banner calibration for old slot display
   state. It cannot promote MiniApp display values or old resource snapshots
   into verified financial balances, enable a switch, or bypass the sender.

## Validation

- Initial migration: `/tmp/xiuxian-r118-initial-20260914.xml`.
- Slot/admission reproduction: 21 failed, 53 passed;
  `/tmp/xiuxian-r118-boundary-repro-20260914.xml`.
- Commit-boundary reproduction: six code failures, one fixture naming error,
  102 passing cases; `/tmp/xiuxian-r118-commit-repro-20260914.xml`.
- Intermediate legacy/held-read suite: 290 passed;
  `/tmp/xiuxian-r118-commit-fixed-20260914.xml`.
- Final migration suite: 128 passed, 9.17s;
  `/tmp/xiuxian-r118-focused-final-20260914.xml`.
- Associated Yinluo/Wanxin/resource/send/recovery/persistence/UI suite:
  2058 passed, 372 subtests, 100.82s;
  `/tmp/xiuxian-r118-related-final-20260914.xml`.
- Configured repository Ruff, scoped fatal/unused-name Ruff, compilation,
  dependency consistency and whitespace checks pass.
- Full network-isolated regression: 12652 passed, 1275 subtests, 230.45s;
  `/tmp/xiuxian-r118-full-20260914.xml`.

The 128 added cases cover bootstrap/existing books, native pending-to-terminal
edits and separate replies, SQLite reload, hot/cold local recovery, each old
action tag, multi-slot/cross-chat ambiguity, optional metadata and strict types,
stale staged observations/configuration/pending, owner replacement/rebind/delete,
new Wanxin work, corrupt/deleted archive proof, atomic native and recovery save
failure, resource income preservation, idempotence and unchanged controls.
Focused, associated and full reports have no failures, errors or skips; all
test sessions completed. Candidate HEAD remains
`cbf152cd61325ca9218c74ee995d643038b20905`. Inherited dirty work, including
the quiz bank and UI-key inspection tool, was not edited by this checkpoint.

## Remaining Gates

No persisted column or new accounting controller is proposed. R117's marker
and the existing resource/hot-cold schema remain authoritative. Missing source
evidence, general legacy summary reconciliation, forced-stop/storage/capacity,
R07 and whole-project Final Review remain open. Production/state/config,
listeners, skills and remotes remain untouched. World Boss/refinement controls,
inventory UI-only policy, CommandAttempt shadow-only status and independent
Tianxing/deep-retreat behavior remain unchanged. R67 production validation is
not claimed.
