# Journey Daily Quota Compatibility

## Evidence

The public MiniApp on 2026-09-17 returns `wildExperience.remainingAttempts`,
`dailyCount`, `dailyLimit=8`, and `resetAt`. It no longer returns
`dailyRemaining` in the observed response. Existing parsing mapped the missing
legacy field to -1, causing `state_missing` and up to four-hour retry backoff.

A single authorized non-Tianxing account probe completed a balanced action:
HTTP 200, matching player ID, `completed=true`, `outcome=defeat`, cultivation
-385, count 0 -> 1 and remaining attempts 8 -> 7. Raw, credential-free evidence
is `/root/xiuxian-release-20260917/wild-wisemole-probe.json` on the host.

## Change

Prefer `remainingAttempts`; use `dailyRemaining` only when the new key is
absent. Do not treat zero as a missing value or fall back from an explicitly
invalid new value. Keep existing validation and server-authoritative limits.
No fixed eight-action loop and no fixed 12-hour cooldown are introduced.

Existing scheduling remains one action per worker, a 60-second follow-up when
the server reports remaining attempts, global serialization, and a stable
30-90 minute spread after the server's midnight reset when quota is exhausted.
User strategy, identity switches, and Tianxing configuration are unchanged.

## Tianxing Verification

Regression tests execute eight rounds through the actual journey/runtime
parsers and Tianxing reducer with mocked transport. Each round consumes a
separate confirmed prediction. A retained change-fate effect is kept only on
explicit `改命待发` evidence; triggered or unknown results cannot release the
next action using the old prediction/change. Exhausted quota sends no ninth
action. The existing late-response, route-conflict, expiry, ownership and
deep-retreat checks remain covered.

These are offline regressions, not a claim that WA has completed eight live
rounds. Deployment must verify natural scheduler receipts separately.

## Validation

- New contract regression before fix: 8 failed, 5 passed.
- Focused journey, wild training, Tianxing and HTTP suite after fix:
  1191 passed, 28 subtests passed.
- Replay of the actual small-account response: remaining 8 -> 7, defeat
  summary `修为-385`, next local follow-up 60 seconds.

## Rollout

Only the parser line changes runtime behavior. Preserve all module switches.
If rescheduling recovery, touch only enabled online identities whose stored
error is exactly `洞府游历页未返回野外历练状态`; stagger them and retain every
Tianxing observation, timeline, and command guard. Do not reset server quota or
clear a pending operation. Back up the quiesced database before changes.

## Production Checkpoint

Deployed `ddee0659` on 2026-09-17 09:58 UTC+8 and pushed to the production
tracking branch. Only the three enabled online identities with the exact
schema error were rescheduled; module flags and all runtime-state rows were
verified unchanged during the maintenance transaction. The main service is
active, PID 2227795, NRestarts 0. Observer/watchdog were not restarted.

- Yinluo: first action completed at 10:01:53, cultivation +4485, spirit stones
  +192, remaining 7/8.
- Lpprceqei: first action completed at 10:02:31, defeat, cultivation -12601,
  remaining 7/8. Defeat is correctly recorded as a completed action.
- WA: `.推命 探索` sent 09:59:05, message 1131741, reply 1131743 confirmed
  before the action. Deep strategy completed at 10:06:26 with change-fate
  rescue, no cultivation loss, fourth-level demon pill x1, remaining 7/8.
- WA's actual action result contains both prediction-hit and change-trigger
  text. It was consumed by the existing Tianxing reducer. The following round
  obtained a new prediction (1131874) and a new change-fate command (1131876,
  confirmed by reply 1131878), rather than reusing consumed effects.
- Two non-Tianxing follow-ups encountered connection resets at `dwelling_start`
  before any journey request. They retained normal backoff; no mutation was
  replayed. This network issue is not resolved by the quota parser change.

The canonical strategies are in `identity_runtime_state`: WA deep, the other
two online identities cautious. The legacy `identities` strategy column still
contains balanced and is not the runtime source of truth. Production strategy
values were preserved. The separate first Wisemole probe used balanced based
on that legacy column; its local report has been corrected.

This checkpoint confirms one live WA cycle, not all eight live daily rounds.
Remaining rounds are handled by normal scheduling and fresh Tianxing evidence.

Separate observation for follow-up: after the new 3-point change-fate receipt,
the local Tianji estimate still read 49 (previous estimate 48 plus the hit).
Do not present it as an authoritative server balance; investigate cost
accounting/idempotence separately rather than editing balances during this
quota fix. The existing insufficient-Tianji server-reply guard stays intact.
