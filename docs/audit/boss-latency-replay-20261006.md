# Boss Timing Replay

Observed event: October 6 13:40, existing capture
`data/messages/miniapp-captures/world_boss-2026-10-06.jsonl`.
Lab base `6ef283ab`; no production runtime or configuration changes.

## Measured Evidence

The server accepted 15 hits, 12 perfect, out of 16 planned windows. One
window was skipped, no hit was rejected, and the player survived. This is
not a full-window or all-perfect run. The earlier Turnstile rejection and
subsequent successful begin are a separate, already recorded entry issue.

| Window | Planned / Local Hold ms | Server Hold ms | Absolute Center Error ms | Charge / Hit RTT ms |
| --- | --- | --- | --- | --- |
| 6 | 1142 / 1142 | 1569.3 | 283.5 | 85 / 586.3 |
| 8 | 1139 / 1139 | 1501.1 | 494.2 | 395 / 804.0 |
| 15 | 1130 / 1255 | 978.8 | 354.6 | 937 / 541.5 |

The current perfect limits are 520-1250ms hold and a center tolerance of
210ms in this event. All three non-perfect hits exceeded the center tolerance;
only windows 6/8 also exceeded the server hold limit. Window 15's server
hold was valid. Its local hold exceeding 1250ms is not itself evidence of
server overcharging. The capture's center error is absolute: it is not a
signed network-delay measurement or proof of which network direction stalled.

Window 11 was received at elapsed 56292ms for center 56572ms. With the
190ms release lead, only 90ms remained, less than the 520ms minimum. The
preceding reveal responses took 82-129ms and reported a null window. The
response disclosing window 11 took 619ms. Their stored shapes contain no
explicit next-window timing hint. The capture cannot establish the exact
server publication time between requests.

Main HTTP transport already retains a `requests.Session` for each identity.
The optional realtime feed uses a separate ticket request/session path and
currently publishes Boss state, not authoritative per-window timing. It is
incorrect to prescribe adding basic keep-alive as if none existed, or to
assume a successful WebSocket ticket proves timing-window pushes are consumed.
The event had 71 reveal requests. The day's combined capture peak remained
73/90 requests per 60 seconds; the cap was not changed.

## Counterfactual Checks

The existing virtual server-clock harness now covers plausible asymmetric
one-way splits that sum to the measured RTTs. These are synthetic scenarios
consistent with the evidence, not measured one-way latency, a deterministic
replay of server processing, or a promise about a future event.

For windows 6/8/15, reducing nominal hold to 800ms alone does not eliminate
center error. In the window-8 scenario, the slow charge reply also invokes
the existing minimum-hold extension: the actual local interval exceeds the
nominal 800ms, and server hold remains above 1250ms. The initial test wrongly
expected every shortened hold to become valid; the observed harness result
corrected that assumption. No runtime change was made to force a passing
perfect score. The window-11 replay still skips safely without charge/hit.

These checks deliberately assert non-perfect contributions remain reported
as non-perfect, with no retry or fabricated hit. Narrowing a constant cannot
by itself solve variable request arrival, late disclosure and charge-reply
extension simultaneously. Keep the timing optimization debt open.

## Test Isolation Defect

The first broader Boss test run was 173 passed / 14 subtests and one failure
in the existing micro-stagger ordering test. It mocked `asyncio.to_thread`
but the current blocking-flow executor used another path, leaving OS thread
order nondeterministic. The optional feed's ticket transport also bypassed
the event's injected fake main transport. Existing fixtures used fake game
tokens/identities, but earlier suite runs cannot be described as globally
network-isolated.

The runtime test class now disables the optional default connector; dedicated
feed tests still inject their fake connectors. A requests-session guard
rejects unmocked HTTP and asserts no attempt occurred even if a background
task swallowed the exception. The one ordering test also injects a synchronous
blocking-flow double. It tests assigned launch order/delays, not a guarantee
that production threads or remote arrivals are globally serialized.

No production transport was changed. Dedicated realtime tests still test
ping/state/reconnect behavior with their own fakes. The existing real local
thread-drain/cancellation tests elsewhere are not replaced by the ordering
test's inline double.

## Acceptance

Corrected Boss suite: 174 passed / 14 subtests. Final full regression:
16572 passed / 1461 subtests in 453.47 seconds. JUnit:
`/tmp/xiuxian-boss-latency-replay-20261006.xml` (18033 cases, zero failures
or errors). Second cross-module regression: 169 passed, covering timing,
runtime, MiniApp admission, Turnstile lifecycle, retry and protocol contracts.
Ruff, compileall and diff checks pass. Every run uses
`XIUXIAN_ALLOW_LIVE_TEST_DB=0`; the new replay itself uses only virtual time
and injected transports. No account setting, Boss timing, rate limit, retry,
production DB, service process or game participation was changed.

Next runtime design must evaluate hold and release jointly under explicit
latency uncertainty, and preserve the request budget and unknown-send rules.
Do not delete the minimum-hold guard, force high scores, or increase polling
merely to turn this finite sample into a claimed all-perfect run.
