# R87 Query Completion And Replay Saves

Date: 2026-09-11.
Candidate: /root/xiuxian-main-rebuild-20260907.
Branch: rebuild/stability-20260907.
Base HEAD: cbf152cd61325ca9218c74ee995d643038b20905.

This closes the scoped R82/R86 owned-query completion follow-up in the
offline candidate. It is not whole-project Final Review or live acceptance.
No production files, services, configuration, database, gameplay requests,
listeners, skills or remotes were changed. No commit, push or deployment.
World Boss and refinement stay disabled; CommandAttempt stays shadow-only.
Explicit UI inventory API policy and Tianxing/retreat policy are unchanged.

## Reproductions

- A status/gift-status completion changed the partner projection, phase,
  timers, completion record and exact pending task before save_state().
  False saves returned True and restored only the gift-attempt marker;
  exceptions restored none of that state. An unrelated later save could
  persist a completion that had never committed successfully.
- Passive status queries ran after text dedupe. A failed save or a reply
  arriving before its receipt could make the same real reply unreplayable.
  Scalar-only owner selection also missed identical root IDs in two chats.
- Query recovery could accept a complete reply, fail its local save, then
  successfully expire that same read. Its earlier recovery checkpoint also
  wrote through false/throwing saves. Fragment reads shared these defects.

The first reproduction run had 57 failures and four passes. Four additional
early-receipt cases all failed. The recovery-save reproductions had 15
failures and 12 passes. These are separate evidence sets, not a count of
distinct production incidents.

## Implementation

Status/gift-status completion now uses R86's checked projection save. Capture
the owner's state before applying the result; on false/throwing save restore
the whole projection, including the query, pending tasks, phase and timers.
False saves report unhandled; exceptions propagate after rollback. No gift
inventory continuation or notification runs before a successful save.
Successful completion removes only the matching operation/chat/root. New
partner, schedule and phase changes survive both failed and successful late
completion without acquiring follow-up authority.

Owned status queries join the existing fragment/gift/greet passive entry
before generic text dedupe. Missing identity hints can be resolved from an
exact account-bound query/receipt and chat/root match, not a scalar root
alone. Ambiguous owners are rejected. An unbound query reply remains
replayable when its receipt arrives. There is no log-history scan in owner
selection. Explicit manual reads and existing unowned legacy routes remain
separate; no legacy operation identity is fabricated.

Query recovery now checks its checkpoint save and rolls it back on failure.
The result handler and log recovery share one pure response classifier.
When complete, trusted, owned terminal evidence exists, a failed local
completion is retried from that evidence on the next bounded recovery pass;
it does not fall through to read-timeout expiry. Incomplete or invalid
panels still permit the existing bounded read expiry. The classifier retains
the existing status/fragment parsing and conflict rules. No mutation gains
read-timeout retry authority.

The change reuses the bounded concubine_status_query slot and adds no SQL
column or new persistence controller. Passive diagnostics run after saved
completion and cannot invalidate it if logging fails.

## Verification

- Added 106 cases to tests/test_concubine_query_lifecycle.py and strengthened
  its existing failed-gift-completion assertion. The file has 290 cases.
- Coverage includes false/throwing saves; panel/no-partner/summary outcomes;
  direct, native and passive replay; receipt-before/after-reply ordering;
  same-root cross-chat selection and ambiguous owners; changed business
  plans; post-commit diagnostic faults; and sent/unknown SQLite reloads.
- SQLite fixtures load the persisted receipt before comparison, so the
  baseline includes the real loader's normalization and optional columns.
  A later periodic save is checked not to publish a failed completion.
- Compatibility fixtures distinguish explicit manual idle reads from
  scalar-only legacy pending reads. Existing legacy routing is not claimed
  to be fully repaired; see the remaining gates below.
- Final focused: 1254 passed, 75 subtests, 10.18s.
  /tmp/xiuxian-r87-query-completion-focused-final-20260911.xml.
- Final isolated full: 9509 passed, 1275 subtests, 150.06s.
  /tmp/xiuxian-r87-query-completion-full-20260911.xml.
- Configured Ruff, E9/F63/F7/F82, compileall, pip check and diff checks pass.
- All test sessions completed. Only audit documentation changed after the
  final full run. Tests use temporary SQLite and a network namespace with
  only loopback enabled for local fake servers.
- Failure artifacts:
  /tmp/xiuxian-r87-query-completion-before-20260911.xml;
  /tmp/xiuxian-r87-query-unbound-before-20260911.xml;
  /tmp/xiuxian-r87-query-recovery-save-before-20260911.xml.

## Remaining Gates

- Unowned scalar status pending still has legacy routing and save behavior.
  In particular, explicit identity hints during a legacy status_pending
  phase do not take the same route as a manual idle panel or the old scalar
  resolver. Its absent chat/account ownership needs explicit reconciliation,
  not a guessed query record or an automatic downstream release.
- Dream and puzzle still use untracked sends, claim phases after awaits,
  reset pending on restart and rely on weak scalar/log recovery. Their
  handlers clear unrecognized replies and fabricate some dream cooldowns.
  Repair their lifecycle/result contracts next, then divination, heart,
  voyage and the remaining module matrix.
- Manual-read chronology, post-completion corrections and legacy resource
  migration are not proven by one retained terminal query. Missing evidence
  beyond log retention still needs explicit reconciliation.
- Shared R07 forced-stop/save durability, R65/R74 migration and capacity,
  rollout/rollback review and whole-project Final Review remain open.
- Production jfdffdddd subordinate-role MiniApp cultivation is unverified.
  Offline green tests do not establish that the live roles have resumed.
