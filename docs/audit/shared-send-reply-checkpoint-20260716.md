# Shared Send/Reply Observation Checkpoint

- Time: 2026-07-16 02:10 UTC+8
- Scope: read-only observation of the frozen shared send/reply path
- Runtime control: unchanged
- Gate 4: not approved

## 1. Shared path freeze

The shared sender, global lock, listener routing, retry ownership, cooldown reducers,
and business schedulers were not changed during this observation closure. Recent
runtime fixes were module-local (Wanxin and small-world configuration), and no
new shared recovery path was added.

Current live state at the checkpoint:

- `xiuxian`, listener, health observer, and watchdog are active.
- `global_enabled=1`.
- runtime pending queue is empty.
- safety watchdog returns `watchdog ok`.

## 2. Module latency baseline

Tool: `tools/module_latency_report.py`

The tool is read-only and correlates a persisted `sent` root only with replies or
edits from an identified game Bot. Player replies to an old command root are
excluded. This matters because an earlier report incorrectly treated a player
reply 822 seconds after a completed duel as the final game event; the real duel
had completed in 12 seconds.

### Current 24-hour window

| Module | Sent / replied / missing | First reply P50/P95/P99/max | Final event P50/P95/P99/max |
|---|---:|---:|---:|
| Wild training | 181 / 180 / 1 | 1s / 2s / 3s / 3s | 7s / 8s / 10s / 11s |
| Duel | 6 / 6 / 0 | 1s / 2s / 2s / 2s | 10s / 11s / 11s / 11s |
| Mulan | 60 / 60 / 0 | 1s / 2s / 12s / 12s | 1s / 7s / 12s / 12s |

The only 24-hour direct-reply miss is wild-training root `146404` for identity
`4319360789` at 04:29:46. No reply or edit with that root exists in the message
log. It remains a real no-direct-reply sample, not a false timeout created by the
report.

### Incident-inclusive 72-hour window

| Module | Sent / replied / missing | First reply P99/max | Final event P99/max |
|---|---:|---:|---:|
| Wild training | 560 / 533 / 27 | 5s / 10s | 11s / 14s |
| Duel | 64 / 61 / 3 | 11s / 11s | 18s / 18s |
| Mulan | 195 / 194 / 1 | 7s / 12s | 8s / 12s |

The 72-hour missing samples include known maintenance/channel-health periods, so
they must not be used as a reason to blindly extend business cooldowns or resend
high-risk commands. Runtime timeout values remain unchanged. A future wait-line
change must be module-specific and must preserve passive recovery before retry.

## 3. Listener sidecar classification

The sidecar is explicitly classified as `degraded_no_connected_accounts`, not as
an independent redundant listener. Its four target sessions are not separately
authorized. When main-runtime Bot replies are fresh, the health observer emits
`listener_sidecar_unbound` as informational evidence with zero score deduction.
It must not be described as working redundancy until separate listener sessions
are authorized.

## 4. CommandAttempt shadow checkpoint

Checkpoint: `data/state/command_attempt_checkpoints/checkpoint-20260716-020659.json`

- Observation: 96.03 hours.
- Attempts: 4698.
- Sent-log parity: 4237 / 4237, zero missing roots.
- Evidence bindings: 5678, all `exact_reply_to_root`.
- Non-strong written bindings: 0.
- Blocked samples: 440.
- `send_unknown`: 19.
- Stale queued rows: 2, unchanged historical shadow rows.
- Approximate Attempt + evidence payload: 1.11 MB.

The checkpoint remains `warn` only because the two stale queued rows and 19 real
error/unknown samples are retained. Shadow write/bind may continue. It still does
not control send, resend, cooldown, reducer, recovery, or scheduler behavior.

## 5. Remaining observation debt

Business-semantic monitoring remains active for daily-once repetition, guarded
command repetition, target duel cooldown evidence, Tianxing prerequisites,
small-world command density, and the process-wide MiniApp 90 requests/minute
limiter. This is ongoing operational monitoring rather than a reason to open
Gate 4. The next concrete debt is to turn unexplained small-world faith deltas
and MiniApp limiter saturation into explicit read-only evidence without adding
new active probes.

## 6. Renamed identity live validation

The identity profile now keeps a bounded username history while the business
relationship is anchored to the Telegram numeric identity ID. The aliases are
persisted in `identities.username_aliases`.

Live validation for WA/Wise completed without a manual game command:

- 02:16:18: identity `8659059191`, current username `WalterWA20000`, sent the
  normal scheduled anchor message `175505`.
- 02:16:32: Wise sent `.双修 温养` as message `175518`, replying to `175505`.
- 02:16:40: the game completed the edit as `温养双修·大成`, still naming the
  contract partner with the old game-side name `@WalterWA2000`.
- Persisted Hehuan state now has `last_partner_identity_id=8659059191`, result
  `success`, empty `auto_last_error`, and the next one-hour cooldown.
- Post-validation health observer returned `ok`; watchdog remained `ok` and the
  restart journal contained no runtime error.

## 7. 2026-07-18 business-semantic closeout

Tool: `tools/business_semantic_report.py`

The tool is read-only. It consumes persisted script send roots, game Bot
replies/edits, and sanitized MiniApp capture records. It does not import the
runtime scheduler, send Telegram commands, or call MiniApp HTTP endpoints.

Three-day MiniApp evidence through 2026-07-18:

- 2,608 actual HTTP attempts.
- Maximum 60-second request window: 48 / 90.
- Saturated windows: 0.
- Capture errors retained for review: 5 `app`, 12 `transient`.
- 202 World Boss `*_business` evidence rows were excluded because they are not
  network requests. Counting them would falsely report 96 requests/minute.

Three-day small-world evidence:

- 31 script-owned small-world command roots and 21 directly bound panel replies.
- 16 non-zero faith changes between adjacent bound panels.
- None can be fully reconstructed to the later panel value from a same-identity
  command reply or a named disaster broadcast in the persisted log.
- The latest WA sample is 98 to 86. A directly bound manifest reply in the
  interval would have raised the modeled value to 100, so the later loss remains
  explicitly unexplained instead of being hidden by the successful manifest.

`unexplained` here means that the persisted evidence is insufficient to attribute
the delta. It is not automatically classified as a scheduler bug, and it does
not authorize a status query or retry. The sample remains available for later
passive correlation.

Focused validation:

```text
176 passed, 10 subtests passed
```

This closes the instrumentation and evidence debt in item 5. Ongoing semantic
monitoring continues, CommandAttempt remains shadow-only, and Gate 4 remains
closed.
