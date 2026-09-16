# Stability Rebuild Stage Handoff

Release preparation on 2026-09-16 supersedes the operational restrictions of
this historical offline handoff only for the user-authorized bounded release.
See [release record](rebuild-release-20260916.md); unresolved acceptance areas
below are not declared complete by deployment.

## Delivery Status

The user requested a staged closeout on 2026-09-15. This delivers the current
offline candidate and its recorded evidence without expanding implementation.
It is not whole-project completion, production recovery, or release approval.
The overall Review -> Rebuild -> Test -> Final Review objective remains open.

| Item | Checkpoint |
| --- | --- |
| Workspace | `/root/xiuxian-main-rebuild-20260907` |
| Branch | `rebuild/stability-20260907` |
| Current HEAD | `cbf152cd61325ca9218c74ee995d643038b20905` |
| Candidate | HEAD plus the existing dirty worktree, including untracked implementation and test files |
| Latest scoped repair | C4 owned-JSON codec follow-up; original Nanlong checkpoint retained below |
| Verification | Latest full regression: 14809 tests + 1376 subtests passed; C4 evidence linked below |
| Production | Not deployed, migrated, probed, restarted, or reconfigured by this handoff |
| Commit/push | Not performed by this handoff; no immutable release artifact created |

The [master ledger](rebuild-stability-20260907.md) remains the authoritative
inventory of accumulated candidate repairs, acceptance contracts and residuals.
This handoff does not certify every module simply because the latest suite is
green. HEAD alone, or a tracked-only diff, is not the complete candidate.
Preserve the untracked files and unrelated user changes when reviewing or
preparing any later commit; do not reset or clean this worktree.

## Nanlong Checkpoint

The [Nanlong pause/recovery contract](C1-nanlong-pause-recovery-20260915.md)
and [orphan-receipt contract](C1-nanlong-orphan-recovery-20260915.md) cover:

- Retaining unresolved placement/recall children instead of starting another
  exchange, repeating recall, or deleting evidence at prompt expiry.
- Restoring missing current-step fields only from matching native ownership,
  original chat, prompt, chain, operation, command and dispatch evidence.
- Replaying known receipts through the existing result handler, with bounded
  local reads and rollback when a recovery save fails.
- Preserving these facts through actual module toggles and temporary SQLite
  reload, without clearing another chat's same-message-ID pending record.
- Sharing Nanlong numeric-state parsing with the UI so malformed retained
  values do not break the identity snapshot.

The latest implementation slice is in `model/features/nanlong.py` and the
Nanlong part of `model/ui.py`; regressions are in
`tests/test_nanlong_route_lifecycle.py` and
`tests/test_persistence_delta_lab.py`. The full candidate is much broader.
The new test fixture's leaked `_bot_waiting_since` was isolated and restored;
runtime bot-health protection was not weakened to make the suite pass.

## Original Verification

| Run | Result | Report |
| --- | --- | --- |
| Focused native caller/control/reload/UI and runtime send coverage | 340 tests + 494 subtests passed; 11.44s | `/tmp/xiuxian-c1-orphan-focused-20260915.xml` |
| Final full regression | 14467 tests + 1376 subtests passed; 406.98s | `/tmp/xiuxian-c1-orphan-full-final-20260915.xml` |
| Previous full checkpoint | Comparison baseline, not the latest verification | `/tmp/xiuxian-c1-nanlong-full-20260915.xml` |

The final full run began at 2026-09-15 14:45:40 UTC+8. JUnit was reread for
this handoff: zero failures, errors or skips; 12 added cases, none removed
relative to the previous full report, and every focused case present in the
full report. The counts above separate test cases from subtests; JUnit's
aggregate `tests` attribute includes both.

Configured Ruff, scoped full-F checks, compileall, `pip check` and whitespace
checks passed at that implementation checkpoint. Tests used temporary state,
`XIUXIAN_ALLOW_LIVE_TEST_DB=0` and a private network namespace; only full-suite
local HTTP fixtures had namespace loopback enabled. This documentation-only
handoff reused that evidence without another full-suite execution. The later
C4 repair reran regression as recorded below.
Reports in `/tmp` are temporary evidence, not a durable release archive.

The initial failing full report and ordered isolation reproducer are retained
as documented in the orphan-receipt contract. Do not substitute the initial
`/tmp/xiuxian-c1-orphan-full-20260915.xml` for the final full report.

Tracked `model/tests` diff SHA-256, rechecked at handoff:
`ee76a4275c1b1bc08307cbe45ad4db1690bbdbdb82fc89a78abdb1ca1471d4c9`.
This hashes `git diff --binary HEAD -- model tests` only: it excludes
untracked files and is not a complete candidate fingerprint. It identifies
the original handoff's tracked diff, not the subsequent C4 repair.

User-owned files remain unchanged at their recorded SHA-256 values:

- `data/quiz/quiz_bank.json`:
  `89268c5517eaace4deaefd7e47d17cbc5de1162506e993b7a65a6336784f37ec`.
- `tools/dump_ui_write_keys.py`:
  `9e72d7ddceea2e03907d2c45c5dd8b3c9078f5c1dd7fd1e8383b4b0937d42b77`.

## C4 Follow-Up

Post-handoff review reproduced invalid owned JSON becoming idle after reload.
The [bounded codec repair](C4-owned-json-review-20260915.md#verified-repair)
now retains malformed evidence for nine concubine records and the second-soul
and Tianti command journals. Existing module guards still block those records;
healthy empty records and unrelated modules continue to work.

The original native reproducer now reports zero mock sends. Final regression:
234 focused cases, 3736 related cases plus 87 subtests, and 14701 full-suite
cases plus 1376 subtests passed. Reports, current scoped fingerprints and
remaining boundaries are in the C4 report. This changes only the offline
persistence codec and adds its regression file; no production operation or
commit/push occurred. It does not close C4 as a whole.

## Latest Offline Follow-Up

The subsequent C4 slice covers the rift result ledger, rebirth operation, and
Wanxin observation. Invalid retained values now block new dispatch while
preserving evidence; Wanxin module toggles retain owned cooldowns and pending
reply anchors. Focused coverage passes 108 cases, related coverage passes 4419
cases plus 103 subtests, and the isolated full suite passes 14809 tests plus
1376 subtests with
zero failures, errors, or skips. Reports are recorded in
`/tmp/xiuxian-c4-rift-wanxin-focused-final-20260915.xml`,
`/tmp/xiuxian-c4-rift-wanxin-related-20260915.xml`, and
`/tmp/xiuxian-c4-rift-wanxin-full-20260915.xml`.

This is still an offline candidate. No production operation, deployment,
commit, push, or live per-role validation occurred; C1-C6 remain open.

## Remaining Work

All six packages remain open. They describe acceptance areas, not six bugs or
an effort estimate. Existing scoped repairs stay accepted unless new failing
evidence contradicts their documented contract.

| Package | Residual to carry forward |
| --- | --- |
| C1 Sending and reply ownership | Remaining scalar/legacy and manual/replayed ownership, Nanlong business-text authority and general post-result save failures; shared R07 no-ID recovery remains separately unapproved |
| C2 Gameplay state and accounting | Remaining reducers, authoritative cooldown/switch admission and once-only profile/resource changes across Tianxing, duel, Yinluo/Wanxin and legacy small-world flows |
| C3 MiniApp completion | Original-round/history and unresolved-outcome reconciliation, plus remaining native caller acceptance; a retained unknown operation is not completed gameplay |
| C4 Persistence and capacity | Legacy evidence disposition, migration, bounded hot/cold retention and remaining cross-flow forced-stop recovery |
| C5 UI and operations | Remaining API/browser/control contracts, diagnostics, remote CI evidence, two missing real-message families and five monitor/control contracts |
| C6 Final review and production reconciliation | Complete-candidate review, integration acceptance, approved deployment/rollback plan and actual per-role production validation |

For R67 specifically, the 19 `jfdffdddd` subordinate roles have candidate
selected-player cultivation fixes only. This rebuild has not verified their
production cultivation one by one or deployed those fixes. The user's report
that they have not been cultivating is therefore not closed as a live incident.

## Operational Boundaries

- No production/config/live-database writes, gameplay requests, listeners,
  service changes, skill changes, commits or pushes are part of this delivery.
- Preserve World Boss and resource-refinement switches, user identities and
  equipment. Inventory API reads remain explicit UI operations only.
- Public MiniApp entry and group commands coexist; a group-send restriction
  does not itself authorize globally disabling MiniApps.
- Deep retreat does not consume, weaken or block Tianxing effects.
- CommandAttempt stays shadow-only. No Gate 4 control takeover or shared R07
  recovery controller is authorized by this handoff.

The candidate includes persistent schema and record-format changes. The
[deployment constraints](rebuild-stability-20260907.md#deployment-constraint)
must be reviewed before any release. Chat-scoped pending state, new command
and operation records, result/accounting journals and Yinluo archives cannot
be handed back to old readers by a code-only rollback. Preserve unresolved
evidence; deleting holds or guessing missing ownership is not reconciliation.
Only temporary test databases have exercised these migrations.

## Resume From Here

1. Read this handoff and the master ledger's C1-C6 acceptance table; inspect
   current tracked and untracked changes before editing. This is a working
   candidate, not a frozen artifact or production baseline.
2. Agree on the next bounded acceptance slice. Do not automatically resume
   expanding Nanlong or reopen already verified slices without new evidence.
3. Complete whole-candidate review and a data-compatible migration/rollback
   plan before requesting production approval. Missing live/CI evidence must
   remain explicit rather than being replaced with mock success.
4. Only after separate authorization, reconcile deployed state and validate
   R67 per role using selected-player requests, owned replies and resulting
   cultivation state. Offline passing tests do not establish live recovery.

This handoff does not itself authorize further implementation or production
operations. The bounded offline follow-up above is recorded separately.
No elapsed-time estimate or completion percentage is asserted.
