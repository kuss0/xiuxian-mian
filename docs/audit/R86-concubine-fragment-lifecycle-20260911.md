# R86 Fragment Query Lifecycle

Date: 2026-09-11.
Candidate: /root/xiuxian-main-rebuild-20260907.
Branch: rebuild/stability-20260907.
Base HEAD: cbf152cd61325ca9218c74ee995d643038b20905.

This is an offline candidate repair of fragment reads and the confirmation
they supply to puzzle admission. It does not rebuild dream/puzzle mutations
or close whole-project acceptance. Production services, configuration,
databases, game traffic, listeners, skills, remotes and user-owned quiz/tool
files are unchanged. No commit, push or deployment was performed. World Boss
and refinement remain disabled; CommandAttempt remains shadow-only.

## Reproductions

All initial 20 cases failed: the sender used track=False, claimed its phase
after transport, lacked queue/owner checks, lost early replies and reset
pending reads on startup. Late receipts wrote into replacement/rebound
identities, and an intent save failure did not prevent sending.

Extended review reproduced four freshness failures. A two-hour-old panel
could authorize a puzzle, and confirmation flags had no bounded server clock.
Another eight cases exposed confirmation provenance: changing the account,
partner, query, quantities or confirmation clock left spending authorized.

The first full run found five dispatcher regressions in existing second-soul
and inventory-transfer tests. The newly forwarded sender_id was assumed to
exist even for unrelated minimal events. The caller now supplies an absent
sender as untrusted evidence, and a new case verifies fragment rejection
without raising. Those existing tests were not weakened.

## Implementation

Fragment reads join the existing status/gift-status query engine. One bounded
concubine_status_query slot now accepts kind=fragment, its exact command and
captured partner. No SQL column is added. Completed fragment reads additionally
carry outcome and, when applicable, the exact confirmation_key.

Save intent before transport. Capture identity/account/chat and a unique
operation ID, recheck controls and the business plan at queue dispatch, track
the receipt with zero transport retries and preserve the existing 900-second
wait. Concurrent queries cannot enter the same operation. Deletion, replacement
or rebinding cannot receive an old callback's writes. Only fresh known-unsent
evidence releases a failed dispatch for later retry.

Unknown reads survive cancellation and restart. Recovery adopts only the
matching operation/source/command receipt, then replays official, exact-chat
and exact-root evidence with server clocks. Real runtime receipts and sent
logs without account_id remain supported through the unique operation binding.
No log-history scan is added to passive identity selection. A read may expire
after its bounded wait; that policy never authorizes a mutation retry.

Native and passive fragment replies enter the same owned reducer before
generic text dedupe or family cleanup. Passive selection distinguishes the
same root number in different chats. Invalid/partial/conflicting panels retain
pending state. Terminal completion removes only its own pending row and saves
the projection together with its completion record. Failed/throwing saves
restore state, so replay can finish without another send. Read expiry also
uses this checked save/rollback path.

Completed old reads may close themselves after a control or business change,
but cannot overwrite new quantities, settings or schedules. A delayed panel
outside the existing 600-second reuse bound cannot authorize spending. An
applicable confirmation is bound to the completed fragment query, account,
partner, explicit confirmed kinds and exact server reply clock. Scalar flags
alone, a replaced query or a changed account require another read.

Explicit no-partner results invalidate availability and request status
calibration; they do not erase quantities or revoke a permanent Moon partner.
Summary replies never confirm fragments. Voyage waits use the server event
clock. None of these read outcomes fabricate a dream cooldown. Notifications
run only after the completion save and cannot undo it.

The old fragment sender, weak log-recovery descriptor, passive scalar routing
and startup/timeout reset branches are removed. Legacy unowned fragment
pending remains held for explicit reconciliation and is displayed as such;
it is not assigned a guessed operation ID.

## Verification

- New lifecycle cases: 137 in tests/test_concubine_fragment_lifecycle.py.
- Existing R85 parser fixtures now obtain an owned read before supplying its
  official response. The puzzle queue-timeout fixture obtains real confirmed
  fragment evidence rather than setting only scalar flags.
- Final focused: 1389 passed, 24 subtests, 10.37s.
  /tmp/xiuxian-r86-fragment-confirmation-focused-20260911.xml.
- Final isolated full: 9403 passed, 1275 subtests, 148.75s.
  /tmp/xiuxian-r86-fragment-confirmation-full-20260911.xml.
- Configured Ruff, E9/F63/F7/F82, compileall, pip check and diff checks pass.
- All sessions completed. Only documentation changed after this checkpoint.
- Initial failure artifacts:
  /tmp/xiuxian-r86-fragment-lifecycle-before-20260911.xml;
  /tmp/xiuxian-r86-fragment-freshness-before-20260911.xml;
  /tmp/xiuxian-r86-fragment-confirmation-before-20260911.xml.
- Tests use temporary SQLite and isolated network namespaces. Only loopback
  is enabled for full-suite local fake servers; no live game requests occur.

## Remaining Gates

- Follow-up R87-concubine-query-completion-20260911.md repairs owned
  status/gift-status completion rollback and passive pre-dedupe replay. It
  also repairs shared query recovery checkpoint saves and prevents valid
  terminal evidence from expiring after a failed local completion save.
  Legacy unowned queries and mutation lifecycles remain outside that repair.
- Dream/puzzle sends, result classification, manual chronology and resource
  accounting remain legacy. Divination, heart and voyage need their own
  lifecycle review. Query expiration must not be copied to their mutations.
- The bounded query slot is not a complete fragment/history ledger. Unowned
  legacy pending, post-completion corrections and missing evidence beyond log
  retention need explicit reconciliation, not fabricated ownership.
- R65/R74 migration/capacity, R07 forced-stop durability and remaining
  whole-project gates are open. An eventual rollout/rollback must account
  for the extended query schema and confirmation requirements; old scalar
  puzzle code cannot safely ignore them.
- Production jfdffdddd subordinate-role MiniApp cultivation is unverified.
  Passing offline tests does not establish live recovery or authorize rollout.
