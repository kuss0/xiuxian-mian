# World Boss WebSocket Lab Review (2026-07-29)

## Scope

This Lab compares wxjerry `origin/main` commits `0e2459d3` and `91f3626c`
against the local World Boss MiniApp runtime. It does not change production,
Boss action counts, account selection, finish reserve windows, hit timing, or
the HTTP settlement path.

## Finding

The local implementation already has the stronger combat path: parallel join,
HTTP `/begin` clock calibration, RTT-aware release lead, server-side hit
accounting, shared event closure, and per-identity action reduction. The useful
upstream delta is the optional WebSocket state feed and reconnect fallback.

The WebSocket must remain advisory. HTTP `/start`, `/hit`, and `/finish` stay
authoritative because the pushed state does not provide the battle challenge or
settlement proof.

## Lab Candidate

- add the official `/ws-ticket` endpoint to the protocol adapter;
- validate returned WebSocket URLs against the exact API origin and the World
  Boss WebSocket path prefix;
- decode ping, nested state, snapshot, and compact boss payloads;
- extend the shared MiniApp sanitizer for session and WebSocket ticket keys;
- run one optional feed per joined identity;
- wake the existing HTTP start-refresh loop only on meaningful state changes;
- reconnect after stale or failed sockets while preserving HTTP polling;
- leave the path dormant when `websockets` is unavailable;
- pin `websockets>=15,<16`, whose `connect()` API supports the required
  `proxy` argument.

## Safety Boundaries

- no live probe when no Boss event exists;
- no WebSocket message may directly submit a hit or finish;
- duplicate push snapshots do not trigger additional HTTP requests;
- all ws-ticket calls continue through the shared MiniApp 90 requests/minute
  limiter with World Boss priority;
- WA and 吧唧 keep their existing extra two-window reduction;
- the global two-window finish reserve remains unchanged;
- dependency absence or feed failure falls back to the current HTTP behavior.

## Validation

Executed with the production Python environment and also with an isolated
`websockets 15.0.1` target:

```text
tests/test_world_boss_miniapp.py
tests/test_world_boss_miniapp_runtime.py
tests/test_miniapp_protocol_flows.py
```

Final Lab result: full suite `3411 passed, 535 subtests passed`; focused
cross-module suite `204 passed, 10 subtests passed`; and `147 passed,
10 subtests passed` with `websockets 15.0.1` actually loaded. `py_compile`,
Ruff, and `git diff --check` pass.

## Gate

Lab candidate only. Production merge requires a final focused/full regression,
dependency installation before restart, and post-deploy observation. Do not
exercise the socket or send Boss requests outside a real event.
