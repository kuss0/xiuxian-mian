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
