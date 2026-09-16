# R105 Fishing Worker Lifecycle

Candidate: /root/xiuxian-main-rebuild-20260907.
Branch: rebuild/stability-20260907.
HEAD: cbf152cd61325ca9218c74ee995d643038b20905, unchanged.

Offline candidate only. No production edits, game requests, configuration or
database writes, deployment, services, listeners, skill edits, commits or pushes.
World Boss/refinement remain disabled; inventory API remains UI-only;
CommandAttempt remains shadow-only; retreat never affects Tianxing.
R67 subordinate-role cultivation has not been validated in production.

## Review

The fishing adapter awaits an unshielded to_thread operation. Cancellation may
release caller exclusion while HTTP continues. Per-round/whole-chain requests
ignore the adapter request budget, and neither authentication nor requests and
waits accept an operation guard. An exception after a confirmed round discards
its result. The chain also discards per-round HTTP events, hiding transport
classification and Retry-After from its callers.

The public-entry and direct-message callers need a separate lifecycle review:
they currently use different locks and lack retained-owner checks across awaits.
Worker tests alone do not establish result persistence or caller correctness.

## Contract Before Implementation

- Reuse the shared draining worker and operation guard. Cancellation waits for
  the in-flight request to return and forbids later requests. Return confirmed
  partial results in MiniAppFlowCancelled without swallowing cancellation.
- Recheck admission around WebView authorization awaits and at every HTTP or
  gameplay wait boundary, including lobby/shop, next-round and result polling.
  A received authoritative result is processed before checking future admission.
- One adapter request budget covers the entire invocation, including retries,
  lobby, shop, waits and all rounds. Keep the shared global 90/min limiter and
  default no-mutation-retry contract unchanged.
- Preserve confirmed catches, gains and round counts if later work is cancelled,
  fails or exhausts the budget. Never count finish submission, an unready result
  or failed round as a confirmed settlement. Keep HTTP classification and delay
  evidence in the returned event stream, without retaining tokens/initData.
- Remove only the unused unselected-cave shortcut after checking references;
  the selected public-entry route remains the supported caller.
- No new retry controller, persistent flag, schema, gameplay policy or live
  switch change. Unknown-effect/restart durability and caller persistence are
  separate acceptance gates, not proved by this worker repair.

## Acceptance

Reproduce thread abandonment, ignored request bounds, lost partial results and
missing HTTP evidence. Test guard invalidation at authorization, lobby, bite,
proof wait, result polling and next-round boundaries; repeated cancellation,
in-flight settlement, no-rod/daily-limit stops, pooled transport admission and
unchanged no-retry behavior. Run focused and network-isolated full regressions.

## Verification

- Initial reproducer: 22 failures; /tmp/xiuxian-r105-reproducer-20260913.xml.
  Concrete failures include 13 calls under a two-call declared budget,
  abandoned cancellation and erased confirmed gains after a wait exception.
- Shared draining, guarded authorization/HTTP/gameplay waits and one budget
  now cover the chain. Per-round HTTP events reach the caller. The unused
  unselected-cave shortcut and its export were removed after reference checks.
- Expanded tests found one additional count-loss boundary after a successful
  result but failed catch decoding: one failed, 50 passed;
  /tmp/xiuxian-r105-lifecycle-expanded-20260913.xml. Completion and HTTP evidence
  now precede optional decoding. No unready/failed result grants a settlement.
- Final associated suite: 413 passed, 36 subtests, 24.36 seconds;
  /tmp/xiuxian-r105-focused-final-20260913.xml, including all 51 new cases.
  The two existing gameplay-wait tests explicitly disable the separate pacing
  delay; new fake-clock cases verify real per-request spacing and the 32-call
  default bound across 99 requested rounds.
- Full network-isolated regression: 11601 passed, 1275 subtests, 175.20 seconds;
  /tmp/xiuxian-r105-full-final-20260913.xml. Configured Ruff and scoped F841 pass.

This scoped worker contract is verified offline. The outer callers, daily
summary writes, result persistence/idempotence, unknown-effect/restart recovery
and production remain separate gates. R106 continues caller review; no live
role recovery, especially R67 subordinate cultivation, is claimed.
