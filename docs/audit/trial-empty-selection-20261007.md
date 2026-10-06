# Trial Empty Selection

Base: `51d116a4`. Lab: `/root/xiuxian-trial-empty-selection-20261007`.
Scope: the existing daily trial scheduler's no-eligible-identity branch only.
No production state, switches, game requests or services changed.

## Finding

When a wave had no currently eligible identity, `run_miniapp_daily_scheduler`
persisted `status=completed, completed=True`. This set the current day's
completion marker without running a game, erased any retained retry prefix
and outcomes, and prevented restored eligibility from starting the same wave.
It also returned before the existing public-entry background scheduler.

Seven new regressions fail on the original implementation; three completed-day
and unknown-hold controls pass. Today's actual production wave1 and wave2
are both genuinely completed, not examples of this bug. Neither is cleared.

## Change

An empty selection now returns the existing `no_enabled_identity` diagnostic
without writing completion, progress, results or a new retry timer. The usual
background scheduler gets its existing turn first, as it already does when a
trial is on a retry hold. Restoration inside the valid window uses the normal
batch admission, including its acknowledged start checkpoint.

This removes a false outcome; it does not add a background job, extra HTTP
request, notification, storage schema, queue or automatic state repair. Wave
partitioning and eligibility rules are unchanged. Completed days, shared entry
holds, unknown outcomes, Retry-After, owner checks and normal wave windows are
not loosened. An empty new wave outside its window is not made catch-up work.

Existing falsely completed historical records are not reset from prose. Such
a migration could replay real work and would need separate evidence. The
durable child-to-parent reward handoff remains a different debt; preserving
a prior aggregate here does not make it an owned receipt ledger.

## Verification

- Regression before fix: **7 failed / 3 passed**, including both waves,
  preserved retry prefix/gains, restored eligibility and background continuation.
- Focused scheduler/admission/terminal-checkpoint suite: **158 passed /
  2 subtests**. All tests use isolated state and fake game calls.
- Second maintainer pass checked the empty branch against real wave selection,
  old completed markers, unknown holds, ordinary background ordering and the
  existing batch-admission owner. Cross-module suite: **299 passed**.
  This is a separate review pass, not an external independent audit.
- Full isolated regression: **16741 passed / 1484 subtests**, 456.49 seconds;
  JUnit `/tmp/xiuxian-trial-empty-selection-20261007.xml`.
- Ruff, compileall and diff checks pass. Merged and pushed as `7f418377`;
  production-directory isolated recheck passed 54 tests. Runtime loading and
  a natural eligible-empty-restored sample remain separate.
  Do not toggle production identities or clear completed markers for testing.

## Production Boundary

At 05:47 CST worker 2835306 remains on code through `c3bd8e64`; all services
are active, NRestarts=0. `514c3a97` terminal-save handling is on disk but not
loaded. Read-only inventory tool `51d116a4` is merged/pushed and needs no reload;
its production-directory isolated recheck passed 44 tests. Only quiz learning
is unrelated production dirt. No manual restart or game action was performed.

The 05:43:25 natural duel 1290085 -> official final 1290087 at 05:43:42
completed normally; target CD was updated to 05:53:42. At the 05:45 scheduler
check there was no new send: the target hold moved next check to 05:58:00.
This verifies the current target guard, not a new duel-code change. The next
Tianxing preparation watch remains around 06:56 for WA's 07:06:07 rift.
