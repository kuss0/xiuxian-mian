# Native Fishing Gift Queue Candidate

## Scope

Lab-only follow-up to deployed `492d54aa` (documentation head `e876af2c`).
Port only the queue idea from older Lab commit `39553988`; do not merge the old
worktree. No production restart, gift, fish-open request, target change or
fishing switch change accompanies this candidate.

## Behavior

- An ordinary native settlement can append confirmed fish to the existing
  gift queue when the standalone fishing module is enabled, the configured
  target is nonzero and the captured transfer plan is unchanged.
- Inventory, queue and the accounted marker persist atomically. Repeated
  projection does not enqueue again; save failure restores the prior inventory
  and queue while retaining the settlement receipt.
- Preserve old queued fish and an existing transfer deadline. Do not queue
  bonus materials or bait. Reject malformed, negative, Boolean or overflowing
  prior counts instead of silently discarding them.
- Public-only fishing authorizes the native game, not implicit Telegram gifts.
  With standalone fishing disabled, retain the fish in the bag without adding
  a command-transfer job. Canary/recovery-only and cancelled workers cannot
  initiate a gift queue either.
- The established transfer worker holds when any legacy/native cast, result
  or supply receipt is unresolved or invalid. No transport retry/freeze changes.

## Evidence

At 09:37 UTC+8 production had no nonzero fishing transfer targets and no pending
fish gift queues. Native catch-to-gift therefore has no currently configured
live acceptance candidate. Do not enable a target to manufacture one.

Related isolated regression: 1291 passed, 17 subtests passed, 28.16 seconds.
Ruff and whitespace checks passed. The queue-to-existing-gift test uses a mock
batch transport and asserts exactly one handoff; it is not a real gift receipt.

## Remaining

Natural native scheduling and return handoff on the deployed base remain under
observation. Only promote this candidate after that stabilization checkpoint.
Actual gifts still require the existing command permission and configured target.

The current official fishing controller exposes no confirmed native fish-open
endpoint; its `open()` opens the fishing panel. Do not reinterpret it as `.开鱼`.
Native fish-open migration and public-only gift transport remain separate debt,
not implemented or silently enabled here.
