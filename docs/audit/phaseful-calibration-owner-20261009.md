# Calibration Notice Ownership

October 9, 2026, CST. Base `ae2084da`. Candidate:
`/root/xiuxian-phaseful-calibration-owner-20261009`.
No live command, configuration change, state correction or restart.

## Reproduction

Both `_calibrate_probe_timeout_once` and
`_calibrate_launching_timeout_once` await a notification before calling the
active status query. A reply, disable, reschedule or identity replacement
during that await makes the query obsolete. The original continuation still
queries and can replace confirmed state with another pending window.

The isolated executable baseline is **32 failed / 4 passed**. These are
offline interleavings, not evidence of 32 production incidents. Two additional
tests use the real deep-retreat success reducer during notification delivery
and require no subsequent game send. One attempted broader test command named
a nonexistent test file and ran no tests; the corrected command passed.

## Change

Extract the existing seven-field, identity-object ownership check from
`_send_summary_launch`, with the same cleanup/send boundaries. Reuse it after
the two calibration-notice awaits. Capture the probe reservation after its
timestamp update, so the normal single-flight path remains valid. Unrelated
profile changes do not cancel calibration.

No sender, listener, database schema, receipt replay, retry, cooldown,
notification routing, native MiniApp operation or Tianxing gate changes.
The caller returns after either calibration result, so a superseded query
does not immediately start another operation in the same scheduler tick.

## Validation

- Focused regression: **251 passed**, including launch ownership, calibration
  races, ordinary calibration, single-flight, early-reply routing and startup.
- Ruff, compileall and `git diff --check` pass.
- Frozen full regression: **17095 passed / 1517 subtests**, 468.79 seconds.
  JUnit: `/tmp/xiuxian-phaseful-calibration-owner-full-20261009.xml`.
  No runtime or test edits were made during the run.
- Second maintainer review checked both scheduler return paths, reservation
  order, stable scalar keys and identity removal/replacement. Cross-module
  regression: **660 passed / 319 subtests**, including Tianxing retreat,
  rift, native retreat/soul lifecycles and send observers/timeouts.
  This is not an independent external audit.
- Production-directory isolated regression: **204 passed**, with
  `XIUXIAN_ALLOW_LIVE_TEST_DB=0`.

## Remaining Boundaries

This closes only obsolete calibration following the two notice waits.
`_send_active_summary_query` itself and active/passive trigger transports can
still receive state changes during cleanup/send; they need separate ownership
work that preserves legitimate `summary_due -> observing_summary` transitions.
The queued-launch and negative-message-id passive branches also have separate
notice awaits. They are not claimed fixed here. Do not blindly apply snapshot
guards to all paths or overwrite a genuine observer transition.

The original launch fix `e326afba` and notification changes remain unloaded.
Worker `3981692` still loads `b686012b`. No current production incident has
been attributed to this race. Foreground observation `68402` remains active;
WA's next natural voyage return is 12:34:18, maintenance 12:36:13.

## Delivery

`acb47f74` fast-forwarded and pushed to `xiuxian-mian/main`. Supervisor,
worker, watchdog and observer retain their original PIDs and start times.
The candidate is not loaded and has no natural acceptance yet. The only
remaining production worktree modification is runtime quiz learning.
