# R88 Owned Dream And Puzzle Mutations

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

- Dream and puzzle used untracked sends, claimed their phases after await,
  rewound early completion, retried unknown effects and reset on restart.
  Resource-consuming dream was also used to discover an unknown partner.
- Weak scalar/log ownership and a separate passive projection accepted
  unowned fragments/CDs. Unknown replies cleared pending work; puzzle success
  invented a fresh dream cooldown. Old puzzle inference borrowed cached
  fragment kinds and guessed counts from missing-piece descriptions.
- The initial 27 lifecycle regressions all failed against the old code:
  /tmp/xiuxian-r88-fragment-actions-reproduced-20260911.xml.
- Final review reproduced two further defects in the first implementation:
  voyage rejection borrowed a cached return time, and a late native send
  registration left completed work pending. Eight failures/six passes:
  /tmp/xiuxian-r88-fragment-review-before-20260911.xml.
- The first full suite had 9707 passes and two safety-routing compatibility
  failures. Universal ban/pardon routing is now preserved separately from
  mutation completion; the old unowned direct-handler test no longer grants
  authority to erase a pending operation or its cooldown.

## Implementation

The bounded concubine_fragment_actions JSON column keeps at most one dream
and one puzzle record per identity. Defaults, runtime/JSON column registration
and both schema declarations are updated. Records bind identity, account,
chat, command, unique operation, dispatch times, partner, fragment baseline,
snapshot time and business plan. Puzzle also captures a completed owned
fragment query, its confirmation key and its original server timestamp.

Save intent before the tracked zero-retry send. Validate ownership, controls,
other work and puzzle confirmation again at queued dispatch. A send return
cannot rewind early completion or write through a deleted/replaced/rebound
owner. Only fresh explicit unsent evidence permits local retry. Missing
receipts, exceptions and cancellation remain unresolved; neither timeout nor
restart creates retry permission. An unknown partner requires a status read.

Native and passive result routes run before generic dedupe/cleanup and share
the same handler. Missing identity hints need a unique account-bound
chat/root receipt, not scalar IDs or partner-name matching. Recovery reuses
exact native receipt adoption and official bot/server-clock log revisions.
There is no log scan in passive owner selection and no live query in recovery.

Strict result classification rejects incomplete/conflicting text. Dream
success/no-drop needs a typed fragment progress; cooldown needs an explicit
duration and uses server time. Puzzle clears only a confirmed kind and never
changes dream CD. Incomplete puzzles revoke confirmation without inventing
piece counts. Voyage waits must come from the reply, not cached return state.
Resource shortage uses separate local retry state, not a fabricated game CD.

Apply results only to the unchanged partner/fragment/snapshot projection;
late facts cannot reschedule a replacement plan. Completion and recovery
checkpoint saves roll back on false returns/exceptions. Cleanup is exact
operation/chat/root only, including native registration arriving after saved
completion. Completed result validation is stable across later voyage changes.

Removed the superseded send/reply bodies, scalar log replay specs, passive
fragment/CD writers and unused inference helpers. Legacy scalar-only dream
or puzzle pending remains visibly held. Status text distinguishes pending
ownership and local retry from game cooldown; no legacy ownership is invented.
Universal ban/pardon handling still runs, but is not a mutation result: it
does not fabricate fragment completion or clear the operation's game cooldown.

## Verification

- Added 216 cases in tests/test_concubine_fragment_actions.py; migrated old
  affinity/send-recovery fixtures to owned transport and server-clock evidence.
- Covers all three routes, early/late/duplicate receipts, unknowns,
  cancellation, replaced owners, invalid records, foreign routes, cross-chat
  ambiguity, failed saves, native log replay, SQLite reload, explicit waits,
  local retry and a complete dream -> fragment query -> puzzle scheduler flow.
- Final focused: 1375 passed, 49 subtests, 9.37s.
  /tmp/xiuxian-r88-fragment-focused-final-20260911.xml.
- Final isolated full: 9725 passed, 1275 subtests, 150.93s, exit code 0.
  /tmp/xiuxian-r88-fragment-full-final-20260911.xml.
- Configured Ruff, E9/F63/F7/F82, compileall, pip check and diff checks pass.
  Tests use temporary SQLite and an isolated network namespace; only loopback
  is enabled for full-suite fake servers. No production request is permitted.
- All test sessions completed. Only audit documentation changed after the
  final full-suite run. HEAD remains unchanged; inherited dirty work is kept.

## Remaining Gates

- Legacy scalar-only work needs explicit migration/reconciliation before
  rollout. Ignoring the new column with old timeout/reset code is unsafe.
  Snapshot and compatible rollback procedures are mandatory for deployment.
- Summary-only, untimed voyage, ban-only and otherwise incomplete mutation
  outcomes retain unresolved evidence. Missing evidence beyond log retention
  needs reconciliation, not an automatic replay or invented completion.
- Sibling greeting/query voyage parsing still uses the broader legacy parser;
  review cached return-time inference separately. Divination, heart, voyage,
  reacquisition and the remaining module matrix still require lifecycle review.
- Full resource accounting, post-completion corrections, shared R07 forced-stop
  durability, R65/R74 migration/capacity and whole-project Final Review remain
  open. The new bounded slots do not solve those wider contracts.
- Production jfdffdddd subordinate-role MiniApp cultivation remains unverified.
  No claim is made that those live roles have resumed or completed cultivation.
