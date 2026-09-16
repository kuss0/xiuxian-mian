# C1 Nanlong Pause And Recovery Acceptance

Offline candidate only in `/root/xiuxian-main-rebuild-20260907`. This closes
the reproduced Nanlong pause/receipt and known-result recovery sub-scope of
C1. It does not close C1-C6, certify the whole Nanlong business protocol, or
establish production recovery.

## Accepted Contracts

| Contract | Candidate behavior | Executed evidence |
| --- | --- | --- |
| Pause after dispatch | Record a returned receipt even if the module or identity was disabled after dispatch. Preserve the existing identity-object, account and operation checks. | Native runtime receipt finalization across placement, exchange, recall and rejection; actual module-control calls; SQLite reload of the disabled receipt. |
| Passive completion while paused | Accept an owned result and persist the next local phase without sending its successor. A completed recall closes once. | Direct handlers, the app's routed reply path, disabled-identity broadcast dispatch, duplicate replies and SQLite reload of all three protected phases. |
| Resume existing cleanup | Enabling after the original prompt expires must retain an owed recall. An expired placed offer resumes recall, not exchange. | Actual `control.set_module_enabled`, saved placement/trade/recall outcomes, reloaded state and scheduler execution. |
| Missing result | Keep known receipt/route/phase evidence. Do not repeat a sent mutation, degrade an unconfirmed placement to unprotected trading, or erase the operation at prompt expiry. | Scheduler ticks before and after expiry and one day later; original receipt survives without another send. Existing proven-unsent backoff tests remain in force. |
| New prompt | A new prompt cannot replace an unresolved send, receipt or cleanup phase. Explicit operation replacement still invalidates an old returning caller. | Prompt arrival during dispatch and after receipt, plus the existing replacement/deletion/rebind regressions. |
| Local reconciliation | Examine the original bounded result window and the recent window, not every intervening day. Use original event evidence for admission and current recovery time for follow-up admission. | A one-day-late scheduler replays the original cross-group result. Explicit old/malformed server timestamps cannot be replaced by newer log receipt times. |
| Legacy rejection and visibility | Reject malformed receipt IDs, missing original routes and contradictory commands. An expired/disabled unresolved operation remains visible as pending. | Direct and unthreaded negative cases, real UI snapshot generation, and control tests retaining uncertain legacy state. |

The module still uses its existing persisted scalar fields and phases. No
schema, shared controller, game request, new endpoint or background listener
was added. CommandAttempt remains shadow-only.

## Removed Behavior

- Removed the scheduler's sent-command confirmation retry and automatic
  unprotected-exchange fallback after a placement timeout.
- Removed recall-result timeout deletion and the now-unused retry-limit/log
  branch. Existing retry counters remain readable for old operation metadata;
  an unsent attempt does not consume them.
- Module enable/automatic-disable no longer clears an unresolved action based
  only on an expired prompt. Expired prompts with no action still clear.
- Status text describes the next check, not an unconditional next send.

Four older test names were replaced to reflect the corrected contract:

- `test_scheduler_clears_stale_protect_phase_without_active_prompt` becomes
  `test_scheduler_retains_unconfirmed_place_without_active_prompt`.
- `test_place_timeout_degrades_to_direct_exchange_without_protection` becomes
  `test_place_timeout_waits_without_unprotected_exchange`.
- `test_protected_trade_confirmation_clears_prompt_anchor_before_recall_retry`
  becomes `test_protected_trade_confirmation_waits_without_recall_retry`.
- `test_manual_enable_clean_nanlong_due_time_keeps_clear_semantics` becomes
  `test_manual_enable_expired_nanlong_keeps_unresolved_work`, with a separate
  empty-prompt clearing regression.

The earlier deferred-recall test remains; its trade branch now carries the
actual exchange command rather than a placement-command reply object. The
older log-recovery fixture's send time now precedes its reply time. These
corrections retain the original cleanup/backoff assertions while exercising
valid native evidence.

## Verification

- Initial reproduction: 22 failing assertions/cases in
  `/tmp/xiuxian-c1-nanlong-initial-20260915.xml`.
- Native control follow-through: four reproduced failures in
  `/tmp/xiuxian-c1-nanlong-control-initial-20260915.xml`.
- Explicit server-time replay: eight reproduced failures in
  `/tmp/xiuxian-c1-nanlong-replay-initial-20260915.xml`.
- Malformed legacy/UI acceptance: seven reproduced failures in
  `/tmp/xiuxian-c1-nanlong-legacy-initial-20260915.xml`.
- Focused regression: 244 tests and 194 subtests passed in 5.79 seconds.
  `/tmp/xiuxian-c1-nanlong-focused-20260915.xml`.
- Full regression: 14455 tests and 1325 subtests passed in 406.29 seconds.
  `/tmp/xiuxian-c1-nanlong-full-20260915.xml`.
- Parsed comparison with R135 verifies 15 added cases and 41 additional
  subtests. After the four explicitly listed renames, no prior case is missing;
  every focused case appears in the full run, with no failures/errors/skips.
- Configured project Ruff, scoped full-F checks, compileall, `pip check` and
  `git diff --check` pass. Tests use temporary state and
  `XIUXIAN_ALLOW_LIVE_TEST_DB=0` inside private network namespaces. The full
  suite has loopback enabled solely for its local HTTP fixtures.

The quiz bank and UI-key inspection tool retain their user-owned hashes.
HEAD remains `cbf152cd`; no commit, push or deployment was performed.

## Still Open

- R07 shared no-message-ID crash reconciliation is separately unapproved.
  Preserving unknown work is not proof that its missing outcome was recovered.
- Missing historical logs, unowned legacy/account-binding evidence and
  overlapping manual outcomes still need their explicit C1/C4 disposition.
  Owned orphan child scheduler cases are now covered by
  `C1-nanlong-orphan-recovery-20260915.md`. Missing original route/clock/account
  evidence remains unresolved; rejection of an ambiguous result is not
  historical reconstruction.
- Nanlong business-result text authority and general post-result save-failure
  reconciliation are not certified by this pause/receipt checkpoint.
- C1-C6 whole-package acceptance and the project-wide Final Review remain
  open. R67's 19 subordinate roles still lack production cultivation evidence.

Do not reopen the accepted rows above without new failing evidence. Continue
the remaining contracts instead of using these tests as a completion metric.
