# R110 Fishing HTTP Outcomes

Offline candidate only. No production, live request, services, configuration,
listener, skill, commit or push changes. R67 production cultivation is not
validated; the whole-project acceptance matrix remains open.

## Review And Contract Before Implementation

Fishing abandons a failed finish response without checking the existing
result endpoint. A transient result-read failure also ends the read sequence.
Callers receive status strings without explicit action/outcome evidence, while
startup can clear the MiniApp phase. A completion count alone cannot describe
an uncertain next round after already confirmed catches.

- Track actual transport entry for each fishing HTTP call separately from
  request preparation, operation checks and budget denial. Do not infer a
  definitely unsent action from a cancellation exception raised inside an
  already-entered transport. No shared transport/controller changes.
- An uncertain dispatched finish may use bounded result reads with the same
  token. Never repeat finish, next, start or proof to recover it. Result reads
  already have a polling contract; transient read failures may consume later
  poll slots with pacing, the same request budget and operation guard.
- Do not immediately probe authentication, rate-limit or access-challenge
  failures. Respect Retry-After and stop rather than waiting indefinitely.
  Explicit application rejection is not a transient transport retry.
- Expose action_dispatched, outcome_unknown and an unresolved action/key in
  every worker return, including authentication failures, cancellation,
  partial success, no-rod and budget exhaustion. Hash token identity; never
  add raw credentials to public summaries. Confirmed results resolve only
  the current round; later next/start/finish uncertainty remains visible.
- Preserve already confirmed catches/counts/rewards and all HTTP evidence.
  A terminal response arriving during cancellation still survives the drain;
  no later request is allowed after operation invalidation.

## Acceptance And Remaining Boundary

Use actual fake transports (not fabricated final flow results) for lost finish
responses, read errors, rejections, HTTP limits, budget admission, cancellation
and later-round faults. Run focused and isolated full regressions.

This is the worker's bounded recovery and evidence contract, not durable
historical accounting or restart admission. The caller/startup ledger must
consume this evidence in a separate reviewed step before the module can be
accepted end to end. Do not mark unknown operations complete or certify R07,
historical receipt identity, source-day/quota authority or production here.

## Verification

- Initial reproducer: 28 failed;
  `/tmp/xiuxian-r110-reproducer-20260914.xml`. A later cancellation test
  exposed the old-token fingerprint after a successful next acknowledgment;
  the pending record now uses the returned round fingerprint. A lost next
  response retains the request fingerprint instead of guessing a new round.
- All 46 new cases pass. Actual transports cover failed finish/result reads,
  application rejection, shared budget exhaustion, Retry-After, cancellation
  and later-round faults. Both actual caller paths account a recovered finish
  once, without resubmitting it. No shared failure budget was relaxed.
- Associated suite: 753 passed, 36 subtests, 26.47s;
  `/tmp/xiuxian-r110-focused-final-20260914.xml`. Isolated full suite:
  11921 passed, 1275 subtests, 172.41s;
  `/tmp/xiuxian-r110-full-final-20260914.xml`. Configured Ruff, scoped F841,
  compilation, pip check and whitespace checks pass. All test sessions ended.
- Final caller review confirms startup still clears a message MiniApp phase,
  and public fishing has no durable unresolved-operation ownership. R110
  provides evidence for that next contract; it does not repair those callers
  or authorize a blind replay, a timed release of uncertainty, or deployment.
