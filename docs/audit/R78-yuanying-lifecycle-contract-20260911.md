# R78 YuanYing Public-Entry Lifecycle

## Scope

Offline candidate only, following R77. The two-step public-entry operation is
status read followed by at most one launch. This work does not enable modules,
deploy R67, rebaseline production channel identities, or change CommandAttempt
ownership. World Boss and incense refinement remain outside this change.

## Reproduced Failures

- **41 failures** exposed entry/status awaits continuing after deletion,
  replacement, rebinding, pause, switch-off or a newer schedule/record; launch
  results overwrote replacement work and cancellation discarded real results.
- The MiniApp bridge invoked asynchronous Telegram success/status handlers.
  A false outer response could still calibrate state, and notification errors
  turned completed work into caller-visible failure.
- No unresolved public launch record existed before dispatch. An unknown
  request could be reissued, including through the legacy group scheduler.
- Further review reproduced **three bypasses** in warm-status continuation and
  the shared send admission path, plus **two contradictory ready-panel cases**.
- Final review reproduced **seven more cases** involving corrupt terminal
  markers and loss of the native game rejection reason.
- One additional postcondition reproducer rejected zero countdown as evidence
  of an active cloud-travel operation.

## Candidate Contract

- Capture the identity object, login account, YuanYing schedule, original
  enabled state and current MiniApp record. Revalidate entry/status awaits and
  pass the same check into authorization/HTTP work and the parent entry lease.
- Keep admission separate from result ownership. A confirmed dispatched result
  may be saved after pause/switch-off, but never through a changed owner or
  newer business schedule. It cannot start a subsequent action.
- Apply only identity-bound, typed, noncontradictory evidence without entering
  legacy async Telegram handlers. Failures cannot calibrate a successful-looking
  body. Logging runs after business state is saved; notification failure or
  cancellation cannot erase that fact.
- Store a per-identity, account-bound launch intent before awaiting dispatch.
  Drained worker cancellation carries the result back to runtime under the
  entry lock. Explicit rejection remains distinct from unknown dispatch, and
  Retry-After survives both status and launch failures.
- An unknown launch permits status reads, not another launch. An unchanged
  warm status, a generic recovery cooldown, or a separate YuanYing retreat is
  not proof of cloud travel. Same-owner cloud-travel status with a strictly
  positive return countdown can reconcile the running condition without
  claiming rewards or a new launch. A zero countdown retains uncertainty.
- Unknown records use a reserved `cave_yuanying` slot per registered identity;
  disposable summaries cannot evict them. Normal codec reload retains the hold.
  The UI scheduler admits the due status check while the legacy scheduler,
  warm-status continuation and existing pre-send guard reject another launch.
- Corrupt markers and mismatched/missing pending owners fail closed. The guard
  is limited to YuanYing launches, including sect retreat, and leaves status,
  deep retreat and Tianxing commands unchanged.

## Verification

- Initial: **41 failed, 2 passed**;
  `/tmp/xiuxian-r78-yuanying-before-20260911.xml`.
- Initial scoped repair plus R77: **105 passed**;
  `/tmp/xiuxian-r78-yuanying-after-20260911.xml`.
- Expanded related tests: **329 passed, 5 subtests**;
  `/tmp/xiuxian-r78-yuanying-related-20260911.xml`.
- Bypass and contradictory-panel reproducers:
  `/tmp/xiuxian-r78-yuanying-bypass-before-20260911.xml` and
  `/tmp/xiuxian-r78-yuanying-panel-before-20260911.xml`.
- Intermediate full suite: **8121 passed, 1275 subtests**, 140.44s;
  `/tmp/xiuxian-r78-yuanying-full-20260911.xml`.
- Final-review reproducer: **7 failed**;
  `/tmp/xiuxian-r78-yuanying-review-before-20260911.xml`.
- Review fixes full suite: **8128 passed, 1275 subtests**, 141.46s;
  `/tmp/xiuxian-r78-yuanying-final-full-20260911.xml`.
- Positive-postcondition reproducer: **1 failed, 4 passed**;
  `/tmp/xiuxian-r78-yuanying-postcondition-before-20260911.xml`.
- Final full suite: **8129 passed, 1275 subtests**, 139.97s;
  `/tmp/xiuxian-r78-yuanying-completion-full-20260911.xml`. This includes all
  84 new YuanYing lifecycle cases. All pytest runs use a network namespace
  without external connectivity, loopback enabled, and
  `XIUXIAN_ALLOW_LIVE_TEST_DB=0`. Broad Ruff, compileall, pip check and diff
  checks pass. This is scoped candidate acceptance, not production acceptance.

## Remaining Gates

- Ordinary save/codec tests are not proof of synchronous durable dispatch
  logging across forced process termination; shared R07 remains separately gated.
- An unknown launch whose observable running period was entirely missed stays
  unresolved. Do not infer non-execution from a later warm panel or invent a
  successful launch/reward. Recovery of such legacy evidence requires its own
  review; no timed blind retry was added.
- General legacy YuanYing reply chronology, Tianti/read-only await ownership,
  other remaining MiniApps, R65/R74 migration/capacity and whole-project Final
  Review remain open.
- Production was not touched. R67 rollout and authoritative per-channel retreat
  baselines remain unapproved/unverified. No restart, live game request,
  listener activation, skill edit, commit or push occurred.
