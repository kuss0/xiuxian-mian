# C1 Nanlong Orphan Receipt Recovery

Offline candidate only in `/root/xiuxian-main-rebuild-20260907`. This addresses
the orphan-child scheduler residual left by `C1-nanlong-pause-recovery-20260915.md`.
The scoped contracts below pass focused and full regression. This does not
close C1-C6 or establish production recovery.

## Reproduced Failures

- A retained placement or recall message ID without the current command and
  receipt could fall through to a new exchange, or disappear at prompt expiry.
- A partial recall phase could send recall again; a contradictory exchange
  phase could start another trade. A stale previous command did not prevent
  this when the current receipt was missing.
- Known, owned native send evidence was not reattached to these orphan steps.
- Invalid legacy prompt IDs or timer fields could raise during the real UI
  identity snapshot. Non-finite timer strings also reached date formatting.

## Candidate Contracts

| Contract | Behavior and verification |
| --- | --- |
| Retain unresolved children | Expiry and scheduler ticks cannot erase or resend a known child. Missing, contradictory or malformed evidence stays visible for reconciliation. |
| Recover a known step | Native pending records and local sent logs may restore missing current-step fields. The account, identity, original chat, prompt key, chain, operation, source module, command and available dispatch metadata must agree. |
| Do not guess a later step | Multiple child anchors require matching evidence for each. The latest known child must also be the latest observed send in that chain. A later unrepresented send or contradictory phase remains unresolved. |
| Preserve the original clock | The retained dispatch bound must fit the original operation and receipt. Recovery time does not become the send time. Explicit invalid or contradictory dispatch metadata is rejected. |
| Rejoin native result handling | Recovered placement results continue through the original protection/expiry checks; recovered recall closes through the existing reply handler. This is not mutation replay or a new shared recovery controller. |
| Survive controls and reload | Real module toggles and temporary SQLite reload retain the orphan, restore the owned receipt, and complete cleanup. Exact original-chat cleanup leaves another group's same-ID pending record intact. |
| Bound local reads | Read only the prompt, last-send and recent windows, merging overlaps. A day-old chain with a later recall does not scan every intervening day. |
| Reject failed persistence | Rejected or failed recovery saves restore the missing-step fields to their previous values and cannot authorize sends. A persistent disk exception is surfaced, not treated as success. |
| Keep UI usable | UI and scheduler share the module's numeric-state parser. Malformed numeric strings, booleans and non-finite timer values remain unchanged in state and appear as unresolved/error status instead of breaking the snapshot. |

The native send-finalization path creates the positive test evidence. Negative
cases include wrong account or identity, wrong chat, mismatched prompt/chain,
command/source/family conflicts, invalid dispatch times, duplicate records and
partial/stale phases. Pending-only recovery and pending-plus-log duplicates are
both tested. Result replay still uses the existing official-bot and reply-route
checks; the tests do not grant authority to arbitrary message text.

## Verification

- Initial orphan/UI reproducer: 18 failed assertions/cases in
  `/tmp/xiuxian-c1-orphan-initial-20260915.xml`.
- Follow-through reproduced contradictory dispatch metadata, partial-phase
  resending and stale-previous-command resending. The evidence test also verifies
  that a persistent disk exception can propagate while original anchors remain.
- Focused native caller/control/reload/UI, message-evidence and complete runtime
  send-timeout regression: 340 passed and 494 subtests passed in 11.44 seconds.
  `/tmp/xiuxian-c1-orphan-focused-20260915.xml`.
- Initial full regression exposed test-order pollution: the new native receipt
  tests left `_bot_waiting_since` at a historical timestamp, causing later
  runtime send tests to enter the real bot-health hold. The route fixture now
  scopes and restores this clock. No runtime health guard was changed.
  The two-test ordered reproducer fails before the isolation fix and passes
  afterward. Reports: `/tmp/xiuxian-c1-orphan-test-isolation-initial-20260915.xml`
  and `/tmp/xiuxian-c1-orphan-test-isolation-verified-20260915.xml`.
- The initial full report is retained at
  `/tmp/xiuxian-c1-orphan-full-20260915.xml`. Final full regression passes
  14467 tests and 1376 subtests in 406.98 seconds. Final report:
  `/tmp/xiuxian-c1-orphan-full-final-20260915.xml`.
- Parsed JUnit comparison with the previous C1 full run confirms 12 added
  cases, no removed or renamed cases, every focused case present in the full
  run, and zero failures, errors or skips. Configured project Ruff, scoped
  full-F checks, compileall, dependency and whitespace checks pass.
- HEAD remains `cbf152cd`. The user-owned quiz bank and UI-key inspection tool
  retain their prior hashes. No commit or deployment occurred.

Tests use temporary state, `XIUXIAN_ALLOW_LIVE_TEST_DB=0` and a private network
namespace. Only the full suite's local HTTP fixtures have namespace loopback.

## Still Open

- This recovery requires an existing child message ID, original chat and
  dispatch bound, prompt identity and explicit native owner/operation evidence.
  Historical rows lacking those facts are not automatically attributed to the
  current account. Missing evidence is not proof of business failure.
- A later unrepresented send, overlapping manual work and contradictory
  ownership still need explicit C1/C4 reconciliation. Retaining such evidence
  is not a claim that the operation has been recovered.
- Nanlong business-text authority and general post-result save-failure handling
  remain outside this scoped checkpoint.
- R07 no-message-ID crash reconciliation remains separately unapproved;
  CommandAttempt stays shadow-only.
- C1-C6 and the whole-project Final Review remain open. R67's 19 subordinate
  roles still lack production cultivation validation. No production, switches,
  DB, listener, skill, commit, push or deployment was changed.
