# R122 Trial Receipt And Quota Contract

Status: scoped offline candidate repaired and verified. The final associated
683-case/19-subtest suite and isolated full regression of 13089 cases/1275
subtests pass; production acceptance and Final Review remain open.

## Evidence And Scope

- Before this repair, `trial_miniapp` promoted any HTTP `ok=true` finish to a settlement,
  including missing/empty results and explicitly negative nested outcomes.
  Its fallback exposes the entire envelope to recursive reward reporting.
- The old `_finish_remaining_count` accepted booleans/fractions through `int(float())`,
  takes the first alias without checking conflicts and reads a generic
  `result.remaining` as a daily quota. Embedded duplicate challenges can be
  submitted more than once within a run.
- Existing repository fixtures cover `result.traceGain/expGain`, root
  `dailyProgress(completed, limit, remaining)` and
  `nextTrial(dailyLimit, remainingToday)`. Local read-only reference code in
  `/root/upstream-refs/xiuxian-wxjerry/model/features/tianji_trial.py` also
  recognizes `result.settled_in_app`, `daily_progress`, `daily_limit`,
  `reward_trace`, `traceBalance` and `nextChallenge`. Its truthiness checks are
  reference behavior, not proof that malformed input is valid. No live receipt
  or production state was collected for this review.
- Boundary review also found that arbitrary payload fields could replace
  `token`, `playerId` and `initData` after selection validation. Challenge ID
  resolution stringified booleans/containers and silently chose conflicting
  aliases; invalid identifiers could therefore reach `finish`.

## Required Contract

1. Transport success alone is not settlement. Empty, pending, explicitly
   negative, malformed or conflicting finish bodies cannot add a round/reward
   or open another round. Preserve prior settlements and HTTP evidence.
2. Use only the returned current result, not unrelated envelope/account/history
   data, for reward reporting. An explicit challenge/player mismatch cannot be
   projected to the submitted challenge/selected identity.
3. Read daily quota only from supported scoped fields. Present invalid values,
   conflicting aliases/containers and inconsistent arithmetic are not zero.
   Absent fields remain unknown; never fabricate daily exhaustion.
4. A confirmed settlement survives a later invalid quota, but invalid quota
   cannot authorize another request. Do not submit one challenge ID twice
   within the same run. Keep the existing request budget and no-retry policy.
5. Verify valid current and supported legacy shapes as well as rejection paths;
   do not obtain green tests by disabling the trial flow or deleting coverage.
6. Only explicit request arguments supply selection/authentication metadata.
   Challenge identifiers must be nonempty opaque strings or safe integers;
   all present supported aliases must agree. Rejected identifiers never reach
   a finish request.

## Candidate Repair

- `trial_receipts.py` owns the current-result, gain, item, quota and binding
  contract. HTTP success alone cannot count a round. Only root `result` or a
  single `data.result` envelope is accepted; mixed current envelopes, explicit
  negative/pending/malformed flags and reflected challenge/player mismatches
  are rejected without another mutation.
- Supported legacy scalar rewards, explicit settlement/status flags, score and
  zero rewards remain valid. Only recognized current-result fields are exposed
  to reporting, excluding history, account, challenge and other diagnostic data.
  Matching gain aliases count once; conflicts withhold that gain while other
  independently confirmed gains survive.
- Reward quantities are normalized without truncating fractions or turning zero
  into one. Invalid material entries retain other valid items and explicit
  settlement but stop the run as partial. Trial reporting uses the same item
  parser; the old permissive reward helper was removed.
- Daily quota is read only from `dailyProgress`, `nextTrial` and legacy result
  `daily_progress/daily_limit/daily_remaining`, with alias, integer, bound and
  arithmetic checks. Generic `remaining`, ordinary trial/history metadata and
  absent fields cannot fabricate exhaustion. A bad quota retains this round's
  settlement but cannot authorize another; a requested single round can finish
  with a quota-warning event.
- User and channel selections are normalized before WebView authorization and
  request construction. Request payloads cannot supply reserved metadata, even
  when no explicit player was selected. One typed challenge-ID normalizer is
  shared by proof construction and receipt binding. Completed challenge IDs
  cannot be submitted again in the same run.
- Existing solvers, default round/request limits and no-mutation-retry policy
  remain unchanged. `_trial_result_from_finish` and `_finish_remaining_count`
  were removed; no remaining references were found.

## Offline Evidence

- Initial receipt/quota reproduction: 21 failed, 1 passed;
  `/tmp/xiuxian-r122-repro-20260914.xml`.
- Later request/challenge boundary reproduction: 20 failed, 9 passed;
  `/tmp/xiuxian-r122-boundary-repro-20260914.xml`.
- Final associated regression: 683 passed, 19 subtests, 24.63 seconds;
  `/tmp/xiuxian-r122-related-final-20260914.xml`. This includes 185 new R122
  cases plus R121 lifecycle, trial runtime, WebAppCore, protocol, cave runtime,
  public UI and background lifecycle coverage.
- Two solver-only fixtures now carry explicit `result.settled_in_app=true`;
  their solver assertions remain unchanged. A pre-existing numeric `reward`
  fixture remains unchanged and passes via supported legacy receipt handling.
- Final isolated full regression: 13089 passed, 1275 subtests, 330.24 seconds;
  `/tmp/xiuxian-r122-full-20260914.xml`. Parsed associated/full JUnit artifacts
  have zero failures, errors or skips; all 185 new receipt cases are included.
- Scoped Ruff, compileall and `git diff --check` pass. All test sessions
  completed. HEAD remains `cbf152cd61325ca9218c74ee995d643038b20905`; inherited
  quiz-bank and UI-key-tool changes were not modified by this repair.

## Still Open

No forced-stop durability, persistent unknown-round recovery, historical
receipt identity, cross-day authority, cross-container reward deduplication or
failed-report retention policy is implied here. R67 production subordinate-role
cultivation remains unvalidated. No production edits, requests, restarts,
config/DB mutation, commits/pushes, World Boss/refinement changes or skill edits.
