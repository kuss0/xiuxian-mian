# R135 Nanlong Closeout And Protocol Scope

Offline candidate only in `/root/xiuxian-main-rebuild-20260907`. This addresses
the recorded C1 Nanlong follow-through and corrects a stale C3 inventory item.
It is not whole-module, production or whole-project acceptance.

## Nanlong Closeout

Replayed failures showed that confirmed placement was followed by state
deletion when the prompt had expired, and that choosing rejection could leave
the partner placed without recall. Replayed historical placement could also
attempt an exchange using its old event time after the prompt actually expired.

- Expired confirmed placement proceeds to recall, not exchange or silent
  state deletion. A known-sent rejection retains the existing protection phase
  until recall; normal, detached-receipt and restored-rejection paths share
  finalization. Plain rejection without placement keeps its existing behavior.
- Log evidence retains source time for admission. Follow-up admission uses the
  current recovery clock; recovering an old trade broadcast cannot make it
  current or bypass its original send-time bound.
- A send with unknown outcome is not erased merely because its prompt expired.
  Unknown recall does not resend. This is retention, not invented completion
  or a new server-reconciliation mechanism.
- Proven-unsent recall and pre-send save rejection/exception preserve the
  cleanup phase and retry deadline. The next scheduler tick does not drop the
  obligation, skip its backoff or charge an unsent call to the sent retry limit.
- Recall completion/failure logs no longer claim that a trade succeeded when
  the branch merely expired or rejected the offer. Only confirmed trade text
  produces the existing trade-result/reward reporting.
- Existing native send guards, exact chat cleanup and the permanent Nangong
  partner protection exemption remain in force. No global control was changed.

No schema was added. Future rollback must still preserve the meaning of a
protected rejection awaiting recall; an older reader must not mistake that
state for completed cleanup.

## Nangongque Scope

Source/caller review found only the default-disabled, manual protocol adapter
and its registry/flow-plan entries, not a production combat loop. The earlier
claim that an active custom loop needed lifecycle rebuilding was inaccurate.

The single-request executor now accepts `request_budget` and `operation_check`,
so an authorized caller can share one run budget and stop before transport.
Existing one-attempt/no-retry behavior is preserved. Tests verify the shared
budget, control rejection and existing secret-safe single-attempt contract.

No combat automation was added or enabled. Real response validation and any
new Nangongque runtime remain separate prerequisites, not production capability
established by these protocol tests. World Boss remains disabled.

## Verification

- Initial replay: seven failures, retained in
  `/tmp/xiuxian-r135-initial-20260915.xml`.
- Follow-up review reproduced two rejection-recovery failures and nine
  deferred-recall subtest failures, retained in
  `/tmp/xiuxian-r135-rejection-recovery-initial-20260915.xml` and
  `/tmp/xiuxian-r135-deferred-recall-initial-20260915.xml`.
- Final focused regression: 242 passed and 329 subtests passed in 8.38 seconds.
  `/tmp/xiuxian-r135-focused-20260915.xml`.
- Coverage includes the native Nanlong chain, exact delayed runtime receipts,
  existing real-message fixtures, actual temporary SQLite reloads and the
  guarded runtime send queue. Tests do not send to Telegram or the game.
- Final full regression: 14440 passed and 1284 subtests passed in 404.46 seconds.
  `/tmp/xiuxian-r135-full-20260915.xml`.
- Parsed comparison with R134: 12 new cases and nine additional subtests,
  zero removed cases, zero failures/errors/skips, and all focused cases present.
  The earlier green run before the final failure-path review is retained in
  `/tmp/xiuxian-r135-before-final-review-full-20260915.xml`.
- Configured Ruff, scoped full-F, compileall, dependency and whitespace checks
  pass. Tests used temporary state, `XIUXIAN_ALLOW_LIVE_TEST_DB=0` and private
  network namespaces; only loopback was enabled for full-suite HTTP fixtures.

## Still Open

R07 shared no-ID crash reconciliation, R11/general legacy reply ownership,
post-dispatch disable behavior, broader Nanlong missing-result policy and
whole-project Final Review are not closed by this change. C1-C6 remain open.
R67 still lacks production cultivation verification for the 19 subordinate
roles. Production/config/DB, services/listeners, switches, CommandAttempt
control ownership and skills were not changed. No commit or push occurred;
HEAD and the inherited quiz-bank/UI-key-tool hashes remain unchanged.
