# Persistence Shadow Observation 2026-07-18

## Boundary

- Production checkout: `/opt/xiuxian-main`, branch `main`.
- Candidate reference: `lab/persistence-delta-20260716` (`fbecbe91`).
- The candidate snapshot-diff and guard-generation code is not merged.
- Production keeps the existing full SQL save and existing last-good backup
  behavior. The new code only compares in-memory snapshots and writes count-only
  shadow metrics after a successful save.
- Shadow is opt-in through the systemd drop-in
  `/etc/systemd/system/xiuxian.service.d/persistence-shadow.conf`; offline
  tools and tests remain disabled unless they explicitly set the environment
  variable.

## What Is Measured

`model/persistence_shadow.py` records no state values, message text, tokens,
usernames, or credentials. It records only:

- save count and no-change save count;
- would-write metadata key count and identity count;
- deleted identity count;
- whether a fresh DB/snapshot would force a full scope;
- candidate P2 backup reasons: roster, account structure, or periodic;
- telemetry errors.

The actual production write path is unchanged. A shadow failure is swallowed and
cannot turn a committed state save into a failed save.

## Read-Only Report

```text
.venv/bin/python tools/persistence_shadow_report.py --since-hours 24 --json
```

Data is written under `data/state/persistence_shadow/` by the service only.
The report marks P1 ready only after at least 12 hours and two process sessions
(one restart), with zero telemetry errors. P2 additionally requires 24 hours.
These flags are evidence gates, not deployment approval.

## Deployment Gate

1. Run focused and full tests before restart.
2. Restart once with the drop-in enabled and verify `baseline` plus interval
   records, service health, pending queue, and watchdog.
3. Observe at least 12 hours including that restart before reviewing P1.
4. Observe at least 24 hours before reviewing P2's ordinary-change backup
   interval and last-good generation rotation.
5. Do not merge candidate persistence behavior, enable module-level scopes, or
   change backup cadence before the corresponding gate review.

Initial production deployment evidence is appended below after the restart.
