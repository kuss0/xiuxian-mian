# CommandAttempt Shadow 72h Review

- Review time: 2026-07-15 07:36:29 UTC+8
- Observation: 77.524 hours
- Policy: shadow write/bind only; no send, retry, cooldown, recovery, reducer, scheduler, or business control
- Decision: keep Gate 4 closed

## Evidence

- Attempts: 4130
- Sent attempts: 3683
- Persisted sent-log parity: 3683/3683
- Evidence rows: 4904
- Binding: 4904 `exact_reply_to_root`; 0 non-strong bindings
- Approximate attempt/evidence payload: 965267 bytes
- Database file: 10874880 bytes
- Retention action: none; archive remains separately gated

## Accepted Historical Anomalies

- 17 `send_unknown` rows are from 2026-07-13 channel send-as rejection:
  `You can't send messages as the specified peer`. These commands were not sent.
  Current runtime classifies this error as `send_as_peer_invalid` and definitely
  unsent; historical rows are retained unchanged.
- 1 `send_unknown` row is `.我的灵根` with
  `scope_exit_without_terminal` during shutdown.
- 2 stale `queued` rows are `.观命` and `.深度闭关`. Their timestamps align
  with the 2026-07-14 repeated service restart window, and neither row has a
  sent transition or root message ID.

These rows explain the report `warn` status. They do not indicate weak evidence
binding or sent-log parity loss, and they must not be rewritten merely to make
the checkpoint green.

## Gate Decision

Gate 0-3 shadow observation remains acceptable. Gate 4 is not approved because:

1. report-only recovery still requires a separate review;
2. open-ledger retention/archive policy is not approved;
3. the listener sidecar still has no independent connected account;
4. formal per-module reply/edit latency baselines are not yet a stable report.

No CommandAttempt control flag is enabled by this review.
