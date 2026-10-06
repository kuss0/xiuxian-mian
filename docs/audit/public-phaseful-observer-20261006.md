# Frozen-Channel Public Phaseful Observation

## Scope

The ordinary observer deliberately excludes disabled identities. Public-entry
HTTP, however, also admits registered identities in the closed channel-health
record's `restore_identity_ids`. Their deep retreat and YuanYing work was absent
from ordinary module summaries even when enabled and executing.

Add a separate `public_phaseful_summary` to the read-only business snapshot.
Do not remove the ordinary enabled filter, count frozen group commands as due,
modify identity switches, dispatch requests, or change recovery ownership.

Only disabled, registered, restorable identities with both the public module
switch and identity module switch enabled enter this supplementary view. An open
or unknown channel-health state does not activate a stale restore list. Normal
enabled identities remain in the existing observer path.

## Evidence Boundaries

- Distinguish missing/blocked entry, shared Retry-After, manual global pause,
  recovery hold, pending operation, unknown outcome, local wait, and due time.
- The existing `tianzun_maintenance` exception still permits public HTTP.
- An identity-bound v2 deep-retreat snapshot can support `server_running` only
  when successful, non-conflicting, active, not completed/settle-ready, observed
  no later than now and no earlier than the last recorded action, with the
  current phase still running and a finite future server deadline.
- Pending/unknown state takes precedence over an older successful snapshot.
- A local future timer is `local_wait`, not proof of game success. YuanYing has
  no equivalent absolute server-end snapshot here; do not invent one.
- More than one hour overdue without a known dependency hold produces a
  warning to verify persisted schedules. This threshold is diagnostic only;
  it does not alter a game cooldown, HTTP retry, or ordinary observer threshold.
- Worker-memory backoff, active slots and account connectivity are unavailable
  to this DB-only path. Every row declares that limitation; warnings explicitly
  do not assert a scheduler stall. Missing/zero timers are evidence gaps.
- Output contains selected diagnostic fields, never raw response/auth payloads.

No rows are added to ordinary pending counts or `stuck_phases`. Existing alert
aggregation receives only a grouped verification warning when necessary.

## Validation

- Initial targeted run: 215 passed / 16 subtests; initial full run:
  16097 passed / 1461 subtests.
- Second review added superseded-snapshot and maintenance-pause regressions.
  Expanded targeted run: 399 passed / 16 subtests.
- Final full regression: 16105 passed / 1461 subtests, 444.06s. Additional
  YuanYing lifecycle, phaseful routing, summary-health and send-observer tests:
  111 passed / two subtests. Ruff, compilation and diff checks pass. Review is
  a second pass by the same maintainer, not an independent external audit.
- SQLite fixture tests use query-only access; production replay is read-only.
  All tests use `XIUXIAN_ALLOW_LIVE_TEST_DB=0`.
- Production replay at about 09:40 covers 19 frozen-channel deep retreats and
  four enabled YuanYing modules. Natural renewals produce `server_running`,
  `local_wait`, short `due_unverified`, and pending transitions with no review
  warnings. Manual disables and inactive modules are excluded.

## Deployment

Merged and pushed to `xiuxian-mian/main` as `fcd00218`. Post-merge isolated
verification: 245 passed / 16 subtests. Observer reloaded at 09:48:53, PID
2322377; supervisor 2207210, worker 2207227 and watchdog 2178662 unchanged.
Staged notification/runtime changes remain disk-only until a separate normal
maintenance window; the main worker still runs the earlier `f7958deb` code.

The daemon's 09:50:54 and 09:51:55 snapshots contain all 19 frozen-channel deep
retreats and four selected YuanYing modules. Xuruode6 moves from a pending
settlement to `server_running` after its natural 09:51:29 launch, with no review
warning. At 09:52, twelve identities have completed natural deep renewals since
09:32 and there are zero unknown deep/YuanYing records. The remaining due
windows continue to be watched, not forced.

The full window finished at 10:16:49: all 23 due identities renewed naturally.
Journal confirmation counts are exactly one settle and one start per identity;
all 23 latest deep snapshots are identity-verified and active. At 10:18, game
pending and deep/YuanYing unknown records are zero, and the daemon again reports
19 `server_running` frozen-channel retreats plus four `local_wait` YuanYing
modules. WA's separate long retreat remains due October 9 at 17:18:35.

At 09:49:50, a new routine digest was confirmed (receipt
`2d70fc7302ce4a128ad872326d820658`, 722ms, 1021 UTF-16 units, 23 lines, no
mentions). This does not resolve the two older held deliveries; both remain
intact. Those are the only current observer warning, not a new phaseful fault.

Natural acceptance closes only supplementary persisted phaseful coverage, not live in-memory
scheduler visibility, all MiniApp module monitoring, notification unknown
delivery, backup drain, or CommandAttempt recovery.
