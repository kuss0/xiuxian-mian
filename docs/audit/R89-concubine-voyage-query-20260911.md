# R89 Owned Voyage Status And Untimed Refusals

Date: 2026-09-11.
Candidate: /root/xiuxian-main-rebuild-20260907.
Branch: rebuild/stability-20260907.
Base HEAD: cbf152cd61325ca9218c74ee995d643038b20905.

Offline candidate work only. No production files, services, configuration,
database, live gameplay requests, listeners, skills or remotes were changed.
No commit, push or deployment. World Boss/refinement disablement, explicit
UI-only inventory API, CommandAttempt shadow-only and Tianxing/retreat policy
remain unchanged. This is not whole-project Final Review or live acceptance.

## Reproductions

- Unknown-time voyages used settlement as a probe, including after retry
  exhaustion. Status/rejection parsing borrowed cached return times and
  unrelated durations; passive voyage text had authority without an owned read.
- Explicit untimed refusals could strand greeting, fragment, dream or puzzle
  work. A no-settleable-task result was treated as proof that no voyage existed.
- Recovery checkpoint/expiry saves could leave dirty state after failure;
  valid affinity evidence awaiting a successful save could expire into a new
  spending chain. Late receipt registration left completed reads pending.
- Initial voyage reproduction: 26 failed, 1 passed.
  /tmp/xiuxian-r89-voyage-reproduced-20260911.xml.
- Affinity recovery reproduction: 9 failed, 1 passed.
  /tmp/xiuxian-r89-affinity-recovery-reproduced-20260911.xml.
- Final review reproduced 10 failures: queued query admission missed newly
  pending business work, and restart slid the saved unknown-time probe deadline.
  /tmp/xiuxian-r89-final-review-reproduced-20260911.xml.

## Implementation

Reuse the existing concubine_status_query record for voyage_status. The command
is the existing voyage-status read; its actual runtime family is
concubine_voyage, source is concubine_status, phase is voyage_status_pending,
and its scalar compatibility anchor is concubine_voyage_msg_id. No controller
or database column is added.

Persist identity/account/chat/operation/partner/plan intent before tracked,
zero-retry dispatch. Recheck controls and business pending state at queued
dispatch. Native and passive owned reads route before generic dedupe; remove
the unowned passive voyage projection. Receipt and completion cleanup is exact,
including late native registration. Recovery/checkpoint/completion/expiry saves
roll back on failure, and valid results cannot expire after a failed save.
The analogous affinity-read recovery save defects are fixed in the same scope.

Voyage status/rejection parsers are independent of cached route/return state
and unrelated durations. Only explicit no-active-task evidence clears the
voyage hold. A no-settleable-task response becomes needs_status instead of
idle. Returned panels accept the actual pending-settlement command suffix.
Evidence older than the existing ten-minute read window cannot unlock actions.

Explicit untimed voyage refusals complete the refused owned greeting,
dream/puzzle or fragment operation without consuming daily/CD state. They retain
sailing with an unknown return time and schedule owned read-only calibration.
Unknown-time probes use status reads, never settlement; checks remain hourly,
including after exhausted legacy retries, and restart preserves saved deadlines.
Voyage mutation helpers cannot dispatch through an unresolved owned read.

This supersedes R88's untimed-refusal hold limitation only for explicit owned
refusals with this safe read-only continuation. Incomplete, summary-only or
unowned evidence still does not authorize completion or mutation retry.

## Verification

- Added 132 cases in tests/test_concubine_voyage_query.py and 19 affinity
  recovery cases across gift/greeting lifecycle tests. Updated affected older
  affinity and fragment-action expectations.
- Covers native/passive/log routes, early/late receipts, identity/chat/plan
  races, restart, stale status, explicit waits, ambiguous no-task replies,
  failed checkpoints/completion/expiry and full refusal -> read continuation.
- Final focused: 1713 passed, 49 subtests, 11.12s.
  /tmp/xiuxian-r89-focused-final-20260911.xml.
- Final isolated full: 9876 passed, 1275 subtests, 152.45s, exit code 0.
  /tmp/xiuxian-r89-full-final-20260911.xml.
- Configured Ruff, E9/F63/F7/F82, compileall, pip check and diff checks pass.
  Tests use temporary SQLite, XIUXIAN_ALLOW_LIVE_TEST_DB=0 and an isolated
  network namespace. Only loopback is enabled for full-suite fake servers.
- All test sessions completed. Only audit documentation changed after this
  final full-suite checkpoint. HEAD is unchanged and inherited dirty work kept.

## Remaining Gates

- Voyage start/return remain legacy untracked mutations with scalar reply
  handling and timeout retry. Review their intent, ownership, rewards and
  recovery separately; this status-read repair does not certify those paths.
- Legacy scalar/query reconciliation, divination, heart, reacquisition and
  the remaining per-module lifecycle matrix remain open. No read-expiration
  policy may be applied to uncertain resource-consuming operations.
- R65/R74 accounting, migration/capacity, shared R07 forced-stop durability,
  rollout/rollback and whole-project Final Review remain open.
- Production jfdffdddd subordinate-role MiniApp cultivation remains unverified.
  No claim is made that those live roles have resumed or completed cultivation.
