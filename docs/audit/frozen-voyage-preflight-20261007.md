# Frozen Voyage Coverage Preflight

## Scope

The existing read-only `tools/defensive_preflight.py` now reports the gap
between an enabled voyage module and the ordinary concubine scheduler's
disabled-identity exclusion. It only examines identities explicitly present
in the closed channel's `restore_identity_ids`, with global automation on
and a nonempty public-entry configuration. Manually disabled identities,
disabled voyage modules, open channels and missing entry configurations are
excluded. Required-column checks preserve older diagnostic database fixtures.

The output is a local `watch`, not a server failure, a ready-to-return
decision or a Telegram notification. Entry configuration is not proof of
valid authentication or reachable HTTP. All timestamps are explicitly local
snapshots; stale or future values do not authorize a voyage action. Entry
URLs and tokens are not printed. Invalid/nonfinite times are sanitized.

The SQLite connection is read-only, explicitly begins a read transaction
before inspecting metadata/identities, and closes before log analysis.
This keeps concurrent channel-state and identity updates from producing a
mixed snapshot. It does not hold a transaction across network calls.
No runtime module, schema, switch, timer, guard, retry or recovery is changed.

## Validation

- Initial Lab focused regression: 53 passed.
- Secondary review reproduced eight failures: seven blank/wrong-type entry
  cases and a WAL concurrent-writer snapshot race. Thirty-one controls passed.
- After correction: 73 passed across frozen-voyage, ordinary defensive
  preflight and daily-duel-closure diagnostics. Ruff passed.
- Full regression: 16915 passed / 1486 subtests. Cross-module second-review
  regression: 353 passed / 16 subtests, including the health observer,
  public phaseful monitoring, channel send-as health and watchdog.
- Ruff, compileall and diff checks passed. The second pass reviewed explicit
  freeze membership, malformed configuration, same-snapshot reads, connection
  release before log analysis and the absence of control/notification hooks.
  Deployment and natural timer acceptance remain pending at this checkpoint.

All pytest invocations set `XIUXIAN_ALLOW_LIVE_TEST_DB=0`; fixtures use
temporary databases. A byte comparison verifies the diagnostic did not
write to its fixture; the concurrency case only writes to a temporary WAL DB.

At 14:08 CST October 7, an isolated import of the CLI with its paths pointed
at production performed only read-only SQLite/log/service inspection. It
reported exactly one voyage coverage gap: xuruode1 / 3888882303, local
phase idle/status sailing. Local snapshot July 19, return July 25, next
schedule August 14. It did not infer current game state or send a probe.
Existing checks retained the inactive sidecar, future WA rift watch, closed
daily duel batch and empty pending queue.

## Deployment And Remaining Debt

This CLI is invoked by `xiuxian-defensive-preflight.timer` every minute.
After validated merge it requires no main/observer restart, no service-unit
change and no manual timer trigger. Confirm a natural timer execution and
unchanged worker PID before marking diagnostic coverage complete.

Public-entry-only voyage calibration/scheduling is still unresolved. Do not
enable the entire ordinary concubine recovery chain for frozen identities.
The broader Lab's unknown/cancelled-dispatch cases remain blocked pending
durable pre-dispatch ownership and evidence-based reconciliation; this
diagnostic does not resolve or authorize them. See the separate
[voyage admission audit](voyage-owned-work-admission-20261007.md).
