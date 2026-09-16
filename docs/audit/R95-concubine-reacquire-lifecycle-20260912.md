# R95 Concubine Reacquisition Lifecycle

Date: 2026-09-12.
Candidate: /root/xiuxian-main-rebuild-20260907.
Branch: rebuild/stability-20260907.
HEAD: cbf152cd61325ca9218c74ee995d643038b20905, unchanged.

Offline candidate only. No production files, services, settings, database,
game requests, listeners, skills, commits, pushes or deployments are changed.
World Boss/refinement stay disabled, inventory API stays explicit UI-only and
CommandAttempt stays shadow-only. Deep retreat never consumes or blocks
Tianxing effects. Whole-project Final Review is still open.

## Finding And Evidence

The former spouse reacquisition path dispatched untracked commands before
owning them, treated unknown delivery as retryable, and accepted scalar or
unowned log evidence as authority to change partner state and cooldowns.
Intermediate romance replies and late send returns could also close or
overwrite the wrong lifecycle stage.

- Initial reproducer: 15 failed, 1 passed.
  /tmp/xiuxian-r95-reacquire-reproduced-20260912.xml.
- Expanded review: 13 failed, 103 passed. Fixed due-time admission, early
  receipt projection/phase cleanup and post-commit notification handling.
  /tmp/xiuxian-r95-reacquire-review-20260912.xml.
  The corrected checkpoint passed 116 cases.
  /tmp/xiuxian-r95-reacquire-review-fixed-20260912.xml.
- Final clock review: three failures with 259 passing controls. Same-message
  terminal edits at the acknowledgement's clock were accepted in native,
  passive and direct routes. Require a strictly later authoritative edit time;
  separate later message IDs may still establish ordering within one second.
  /tmp/xiuxian-r95-final-review-confirmed-20260912.xml.
- Redirection review: two failures with 171 passing controls. A status refresh
  or definitely-unsent retry reset the alternate-command budget. Retain that
  budget until a terminal outcome/backoff ends the cycle.
  /tmp/xiuxian-r95-redirection-review-20260912.xml.

These counts describe offline regressions, not observed production incidents.
The first final-review test draft also had misplaced assertions and omitted
the existing five-second CD buffer; those test-authoring errors were corrected
before recording the three confirmed runtime failures above.

## Owned Lifecycle

- One persisted concubine_reacquire_action per identity, registered in runtime
  and JSON fields and both SQLite declarations. Its bounded record holds the
  current operation, one optional acknowledgement and one terminal result.
- Save intent before tracked zero-retry sect-marriage or romance dispatch.
  Bind identity object, account, original chat, operation, partner fingerprint
  and current plan. Queue admission rechecks controls, owner and sibling work.
- Require a fresh no-partner status snapshot. Stale/unknown absence triggers
  an owned read, not a purchase. An available or permanent moon partner cannot
  be acquired again. Existing-partner/voyage refusal also requires calibration.
- Distinguish fresh definitely-unsent evidence from unknown delivery. Only the
  former rearms after bounded transport backoff. Unknown sends, exceptions and
  cancellation retain ownership without fabricated business CD or blind retry.
- An owned romance acknowledgement keeps the command pending. A terminal
  result must match original command/chat/operation and an official sender.
  Original-message edits also match the acknowledgement sender and later clock.
  Ambiguous receipts, conflicting metadata and coerced IDs cannot settle it.
- Typed terminal outcomes cover acquired/existing partner, voyage lock,
  resource/eligibility/search refusal, explicit cooldown and command redirection.
  Explicit CD uses the server event clock plus the existing CD buffer; refusal
  policy retains the existing 12-hour backoff. Permit one alternate command,
  including across status refresh and definitely-unsent retries, then back off.
- Commit result, partner projection and owned cleanup together. Acquired
  partners remain availability=unknown until a fresh panel. Newer partner state
  and permanent moon contracts survive late replies; duplicate results cannot
  apply again or trigger another notification.

## Recovery And Cleanup

- Native and passive routes reach the same reducer before generic deduplication.
  Replay uses only owned pending/sent receipts and trusted timestamped replies;
  it does not select an arbitrary owner or infer completion from old text.
- Cleanup binds exact original chat/message/operation. Zero compatibility
  anchors require an unchanged plan, or the complete unchanged initial wait,
  attempt count, partner fingerprint and absence of sibling work. Boolean or
  replaced anchors/timers/snapshots are not cleared.
- A committed result survives notification failure or cancellation. Guard-save
  failure retains completion and retries only exact guard cleanup, not gameplay.
- Startup retains unresolved or malformed records. The ordinary scheduler and
  phaseful cleanup reach owned recovery; outer scan errors annotate diagnostics
  without rewriting the operation's plan. Queued sibling reads and mutations
  recheck acquisition ownership before sending.
- Remove obsolete reacquisition sender/handler, scalar log replay, command
  switch helper and timeout handling. The previous unowned-log integration test
  now asserts no state rewrite/no notification; authoritative CD is covered
  through native receipt and replay-clock lifecycle cases instead.

## Verification

- 173 acquisition lifecycle cases and 29 additional shared phase/queued-peer
  cases, including actual runtime receipts, native/passive routes, temporary
  SQLite reload, log replay, switch/owner changes, malformed state, early/late
  replies, precise cleanup, command redirection and persistence failures.
- Focused final: 3516 passed, 347 subtests, 26.55s.
  /tmp/xiuxian-r95-focused-final-20260912.xml.
- Configured/fatal Ruff across model/tests/tools, compileall, pip check and
  git diff --check pass.
- Final isolated full suite: 10793 passed, 1275 subtests, 162.08s, exit code 0.
  /tmp/xiuxian-r95-full-first-20260912.xml.
  Tests use temporary SQLite with XIUXIAN_ALLOW_LIVE_TEST_DB=0; the network
  namespace has only loopback enabled. All test sessions completed. Only
  documentation changed after this full-suite run; whole-project acceptance
  and production reconciliation remain unproven.

## Remaining Gates

Legacy/manual reconciliation, R65/R74 accounting/capacity, shared R07 forced-stop
durability, rollout/rollback and whole-project Final Review remain open.
An old reader must not ignore concubine_reacquire_action and resume its former
timeout/retry path. Preserve unresolved ownership through migration/rollback;
deleting a hold or inventing receipt metadata is not reconciliation.

R67's production jfdffdddd subordinate-role MiniApp cultivation has not been
revalidated or resumed by this work. Its candidate selected-player fixes still
need a separately authorized rollout and identity-bound production baselines.
