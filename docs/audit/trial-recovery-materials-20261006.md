# Trial Resave Material Projection

Base: `5cc63561`. Lab: `/root/xiuxian-trial-recovery-materials-20261006`.
Merged to disk and pushed as `c33170ac`; post-merge regression: 177 passed.
Main worker remains on `f7958deb`. No main restart or live fault injection.

## Defect And Scope

`trial_operations.recover_local()` retains validated round receipts after a
failed final save. Once the local resave succeeds, its `recovered` response
previously inherited empty `data` from `held_result()`. The public-entry caller
then returned zero settlements and no material gains despite the stored facts.

Project `results` and `settled_count` from the already validated, owner-bound
checkpoint, using independent copies, for the existing resaved-complete branch.
Share that same projection with the existing interrupted-local-recovery branch.
No new storage, replay, notifier, authorization, retry or state transition.

Preserve `ok=False`, `status=recovered`, `persistence_only=True`, and
`action_dispatched=False` for this branch. The wrapper must not turn the resave
into batch completion or successful new gameplay. A subsequent local recovery
still returns `None`, as before. An unresolved request continues returning its
existing held response, even when an earlier round has a valid receipt.

## Verification

- Two added assertions fail on the original code: public recovery reports zero
  settlements, and SQLite receipt-abort recovery has no result data.
- Targeted regression: 177 passed, covering real temporary SQLite abort/resave,
  public-entry lifecycle, batch outcome retention and process restart.
- One/two settled rounds retain their gains; no receipts fabricate no rewards;
  a later unknown finish stays pending; returned mutations do not change the
  durable checkpoint. Existing owner-mismatch and stale-save tests remain.
- Final full regression: 16109 passed / 1461 subtests, 458.96s. Second maintainer
  pass: 455 operation/checkpoint/receipt/lifecycle/process tests plus 34 UI,
  outcome-retention and runtime tests. This was not an independent external
  audit. Ruff, compilation and diff checks pass.
- All tests use `XIUXIAN_ALLOW_LIVE_TEST_DB=0`. No production fault injection.

## Review Boundaries

All three callers were checked: the public entry uses `_trial_recovery_response`
and receives the extra materials; the old event path still has `ok=False` and
does not newly apply its success-only material capture; the manual-entry UI
keeps returning its existing local-recovery status without starting an entry.
No additional notification is sent by this change.

This is not durable reward handoff or cross-retry deduplication. A crash after
local resave and before the caller receives its result can still lose the
handoff. The existing failed-step batch path can still reset its aggregate.
Do not suppress child notifications or carry old aggregate totals into retries
on the strength of this patch. Those debts remain open.

Do not interrupt the main worker solely for this projection change. Natural runtime acceptance remains pending
until a normal maintenance reload and a relevant natural recovery sample.
