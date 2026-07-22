# Persistence P1 Gate Review 2026-07-22

## Boundary

- Lab branch: `lab/persistence-gate-20260722`.
- Base: production main `502f8333`.
- Production service, state DB, backup policy, and runtime control were not changed by this Lab.
- This review ports P1 delta persistence only. P2 backup throttling and backup generations remain out of scope.

## Shadow Evidence

The production shadow report covered 97.632 hours and 65 process sessions:

- 255322 `save_state()` calls;
- 108266 no-change saves;
- 147056 changed saves;
- zero telemetry errors;
- P1 and P2 review gates both reported ready.

Readiness is evidence for review, not automatic deployment approval.

## Refreshed P1 Design

The 2026-07-16 Lab copied an explicit snapshot field list that had drifted behind main. The refreshed candidate instead reuses `model.persistence_shadow` as the single snapshot encoder:

- metadata keys come from the current `_META_STATE_CODEC`;
- module, timer, and runtime values use the current dynamic identity column lists;
- profile, pending-task, and message-index serialization remains identical to the existing DB write contract;
- the same snapshot instance feeds delta selection and shadow telemetry for each save.

After a successful DB load, P1 records a canonical persisted snapshot. Each later save writes only changed metadata keys and changed identities. A new DB path, reopened connection, missing snapshot, identity deletion, or failed snapshot refresh conservatively falls back to a full or repeated write.

Schema caching also tracks SQLite `schema_version`. An external schema change rechecks required columns and reruns migration when a required column is missing.

## Consistency Invariants

- Metadata and identity changes remain in one SQLite transaction.
- Pending tasks and message-index rows retain delete-and-reinsert semantics for a changed identity.
- Failed writes do not advance the persisted snapshot.
- A failure after DB commit but before snapshot refresh causes a conservative rewrite on the next save.
- Connection reopen clears the snapshot and forces a full identity save.
- Identity-collapse protection, external watchdog pause synchronization, live-guard backup, restore, and shadow telemetry remain enabled and unchanged.
- P2 is intentionally excluded because its default 30-minute backup cadence differs from the current six-hour live-guard cadence and needs a separate review.

## Verification

- Persistence/recovery focused suite: `105 passed`.
- Full suite: `3250 passed, 405 subtests passed`.
- `py_compile` and `git diff --check`: passed.
- Live-shaped SQLite copies were used; production DB was read only through SQLite backup.

No-change save benchmark, backups disabled for both sides:

| Build | Median | Range | Mutating SQL per save |
| --- | ---: | ---: | ---: |
| Current main | 25.654 ms | 25.293-26.740 ms | 275 |
| Refreshed P1 | 10.459 ms | 10.373-10.852 ms | 0 |

## Decision

P1 is Lab-green and materially reduces unnecessary SQLite writes without changing backup or restore policy. It may enter a controlled production deployment after the final staged-diff audit and an explicit pre-restart DB backup. P2 remains blocked from production.
