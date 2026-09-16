# R103 Native Affinity Source Ordering

Candidate: /root/xiuxian-main-rebuild-20260907.
Branch: rebuild/stability-20260907.
HEAD: cbf152cd61325ca9218c74ee995d643038b20905, unchanged.

Offline candidate work only. No production, live requests, service changes,
data/config writes, listeners, skills, commits, pushes or deployment.
World Boss/refinement remain disabled; inventory API remains UI-only;
CommandAttempt remains shadow-only; deep retreat never affects Tianxing.

## Review

R100/R101 compare scalar timestamps, although Telegram creation timestamps have
one-second resolution. A qualified older greeting result delivered after a
native panel changes 209 to 218; an older seal result changes 176 to 152.
The reverse delivery order also lets an earlier native manual panel overwrite
the later Wanxin value. These failures use real parsers and current native/
passive handlers, not fabricated state updates in a stand-in reducer.

The first 30 cases produced 13 failures and 17 passes. The older moon-panel
control initially used the same balance on both sides; change that control to
distinct values so it tests replacement, not an accidental equal-value result.
The corrected reproducer produced 15 failures and 15 passes.

## Contract Before Implementation

- Preserve separate mutation completion, cooldowns, exact pending cleanup and
  affinity projection. A covered gain/cost still completes, but cannot count
  twice merely because its server timestamp equals the current snapshot.
- For a current native snapshot, only a validated completed status-query record
  tied to the current identity/account, snapshot clock and panel chat/message
  may establish its original command boundary. Scalar panel IDs alone cannot
  manufacture that source; a replacement query is not the old panel's source.
- A non-edited same-chat result created before that original query command is
  already included. For a moon status read, use its original command point.
  Never compare message IDs across chats, or use edited-message IDs to invent
  creation order. Preserve the existing strictly-older timestamp guard.
- For incoming native status panels, pass the verified original command point
  through the shared snapshot reducer. Use the existing resource point-order
  helper to reject a provably older same-second read. A later reply/edit does
  not turn that old query into a newer read. Owned query completion remains
  independent of whether its panel projects.
- MiniApp and legacy observations without native source evidence retain their
  current timestamp checks; do not fabricate Telegram points for them. This
  contract does not certify uncertain/overlapping read intervals or missing
  legacy receipts. Those require explicit reconciliation, not a guessed order.
- Keep source helpers read-only, record formats unchanged, and every projection
  inside the existing checked save/rollback boundary. No new ledger, automatic
  retry, control switch or sending authority is introduced.

## Acceptance

Exercise both delivery orders, owned/observed status reads, actual native and
passive routes, same-chat order, genuine later reads, foreign/rebound/malformed
snapshot anchors, edited/cross-chat ambiguity, save failure, SQLite reload and
duplicate replay. Preserve current command/cooldown assertions and real wording.

## Implementation And Final Review

- Native owned/observed status callers pass the independently verified original
  query point into the existing snapshot transaction. The bounded Wanxin
  receipts reject a provably older same-second read with compare_points;
  MiniApp/legacy paths do not fabricate native message evidence.
- A covered Wanxin result still records completion, cooldown and exact pending
  cleanup. Only its obsolete affinity projection is suppressed. Coverage needs
  a completed validated query for the current owner/account, matching snapshot
  clock and exact panel chat/message. A scalar panel ID or replacement query
  cannot supply that proof.
- The lifecycle review reproduced a second boundary: a later greeting and its
  same-second correction return affinity to the original value, while the
  edited reply becomes incomparable to another message. The retained original
  command is still a validated lower bound. An earlier read cannot contain that
  operation, even though its old plan fingerprint now matches again.
- The negative check uses that lower bound only with verified native points.
  Missing/malformed receipts, edited original commands, cross-chat equality,
  missing observation points and genuinely later reads do not invent order.
- Existing transactions retain their rollback and exact cleanup boundaries.
  Temporary SQLite reload preserves both coverage and the round-trip barrier;
  failed saves leave the original receipt replayable. No schema, record format,
  ledger, control flag, sending authority or automatic retry is added.

## Verification

- Corrected initial reproducer: 15 failed, 15 passed;
  /tmp/xiuxian-r103-affinity-order-reproducer-v2-20260913.xml.
  First implementation associated suite: 258 passed;
  /tmp/xiuxian-r103-affinity-first-fixed-20260913.xml.
- Lifecycle boundary reproducer: 6 failed, 50 passed;
  /tmp/xiuxian-r103-lifecycle-boundary-first-20260913.xml. The six failures
  were the owned/observed round trip through direct/native/passive routes.
  The command-bound fix passed all 56 cases;
  /tmp/xiuxian-r103-lifecycle-boundary-fixed-20260913.xml.
- Final source/lifecycle suite: 81 passed in 1.26 seconds;
  /tmp/xiuxian-r103-source-boundary-final-20260913.xml. Includes invalid
  command sources, read-only negative checks, reload, failed saves, duplicate
  replay, exact pending cleanup and preserved disabled-send behavior.
- Associated regression: 3740 passed, 197 subtests, 29.21 seconds;
  /tmp/xiuxian-r103-focused-final-20260913.xml.
- Full network-isolated regression: 11485 passed, 1275 subtests, 170.69 seconds;
  /tmp/xiuxian-r103-full-final-20260913.xml. JUnit records 12760 cases with
  zero failures/errors/skips. Configured Ruff, scoped F841, compileall, pip
  check and git diff --check pass. All test sessions finished.

This scoped candidate contract is implemented and verified offline. The full
rebuild objective remains active: overlapping/legacy/post-consumption affinity
accounting, R65 capacity/migration, shared R07 durability, remaining games,
production acceptance and whole-project Final Review are not complete.
Production subordinate-role cultivation is still unvalidated. No production,
live data/configuration, services, listeners, skills, commits or pushes changed.
