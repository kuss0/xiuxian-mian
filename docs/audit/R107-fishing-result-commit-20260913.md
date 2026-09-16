# R107 Fishing Result Commit

Offline candidate only: /root/xiuxian-main-rebuild-20260907, branch
rebuild/stability-20260907, HEAD cbf152cd. No production/live requests,
deployment, service, configuration, skill, commit or push changes.

## Review And Contract Before Implementation

The R106 reducer ignores save_state failures. Its inventory helper saves
before valuable-drop reminders are queued, so one outcome has two commits.
A failure can leave dirty completion in memory, report success, and lose or
partially repeat the result after restart. Message entry also starts HTTP
without checking its pre-worker state save.

- Separate a fishing outcome's projection from its commit. Counts, catch
  summary, inventory, permitted scheduling and reminders commit together.
  The existing inventory helper's persist=False is the only path used inside
  this transaction. False returns and exceptions restore the old projection.
- Keep one bounded, account-owned fishing_result_pending record per identity
  for a failed local commit. Store only the prepared business projection and
  its basis, never tokens, initData, URLs, proofs or raw HTTP payloads.
  Do not overwrite unresolved work with a later game result.
- Recover this local projection before another fishing WebView/game call,
  transfer or report. Recovery never sends a game request, reruns proof or
  interprets a local save as a new catch. Identity/account, fact and inventory
  basis changes hold the record for reconciliation. Changed controls/plans
  allow retained facts only, with no replacement scheduling or new reminders.
- Clearing the pending record is part of the same successful commit, so a
  retry/reload cannot apply that pending projection twice. Invalid persisted
  records remain blocked, not silently normalized to an empty record.
- A failed pre-worker marker save must not start gameplay. Callers expose
  pending persistence instead of announcing successful completion; confirmed
  cancellation still propagates after retaining its local result.

## Limits

This is a local business-result transaction, not the unapproved R07 shared
transport architecture or a CommandAttempt recovery controller. A forced
stop before any pending/result commit still has no durable outcome. Historical
HTTP/result replay, source-clock authority, unknown mutations, legacy/startup
reconciliation and full inventory snapshot chronology remain separate gates.
Do not deduplicate unrelated catches just because their contents are equal.
R67 production subordinate-role cultivation is still unvalidated.

## Verification

- Initial reproducer: 13 failed, 2 passed;
  /tmp/xiuxian-r107-reproducer-20260913.xml. Recovery follow-up exposed daily
  read mutation, malformed record admission, the remaining public bait-skip
  save and an unchecked inventory no-op. Its 9 failures included two invalid
  pond fixtures, corrected before acceptance. Daily-ack follow-up: 1 failed;
  /tmp/xiuxian-r107-ack-reproducer-20260913.xml.
- Implemented one prepared result projection and one checked commit using
  persist=False inventory writes. Exceptions and false saves restore counts,
  inventory, scheduling and reminders; the bounded pending projection remains.
  Both callers and the fishing scheduler recover it before gameplay/followups.
  Unknown owners/bases and corrupt records hold rather than clear the receipt.
- Public missing-bait scheduling now commits with the result. Daily/status
  reads cannot normalize away a pending day basis; daily acknowledgements
  include the pending record in their retained snapshot. Background admission
  recognizes pending local work even at the old daily limit, still under its
  existing controls and backoff. UI status exposes pending local accounting.
- All 56 new cases pass. Associated final suite: 547 passed, 22 subtests,
  6.03 seconds; /tmp/xiuxian-r107-focused-final-20260913.xml. Real temporary
  SQLite tests abort a transaction after inventory staging, preserve a pending
  projection through a later save/reload, and commit its counts/items/reminders
  only once. Corrupt JSON, account/control/fact/inventory changes, component
  exceptions, secret exclusion and cancelled-caller retention are covered.
- Final network-isolated full regression: 11752 passed, 1275 subtests,
  173.40 seconds; /tmp/xiuxian-r107-full-final-20260913.xml. Configured Ruff,
  scoped F841/compilation, pip check and whitespace checks pass. All sessions
  finished. No production changes or shared transport controller introduced.

## Deployment And Remaining Work

The new JSON column is additive, but rollback must preserve pending results.
An old reader that ignores fishing_result_pending must not resume those roles.
Tests used only temporary schema/data. No production schema was migrated.

Atomic local accounting does not certify the input facts. R108 begins the
explicit-empty catches, unconfirmed rewards and conflicting readiness review.
Historical replay identifiers, forced-stop/unknown-effect recovery, source
clocks and broad inventory/manual reconciliation still require acceptance.
