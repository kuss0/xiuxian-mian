# R123 Trial Cross-Day Unknown Archive

Status: implementation candidate verified offline; production migration and the
first natural next-day trial remain acceptance gates.

## Problem

A dispatched Tianji Trial mutation can lose its HTTP response. The durable
`trial_operation` record correctly blocks same-day re-entry, but that single
active slot also blocks every later game day. Re-running the old mutation would
risk duplicate settlement; deleting it would discard the only evidence.

## Contract

1. Same-day unknown operations remain frozen.
2. Only a valid, owned, dispatched request with `outcome_unknown=true` may be
   archived. Invalid records, response-only checkpoints and unsent requests stay
   blocked.
3. The original operation is copied byte-for-byte into a bounded durable archive
   before the active slot is cleared.
4. Archive persistence and active-slot clearing share one `save_state`
   transaction. A failed save restores both in-memory values and authorizes no
   network request.
5. Archiving records no success, failure, reward or quota conclusion for the old
   request. It only permits a fresh operation for a later game day.
6. The archive is append-only per operation ID and capped at 64 records / 256
   KiB. Corruption, conflicts or capacity exhaustion fail closed.
7. Automatic archival is available only to a dated daily trial batch and runs
   before MiniApp network access. Manual runs do not clear old evidence.

## Implementation

- `identity_runtime_state.trial_operation_archive` stores the bounded archive.
- `trial_operations.archive_cross_day_unknown()` validates ownership, evidence,
  day boundary, deduplication and persistence.
- The daily cave batch invokes archival immediately before a trial step. The
  existing same-day recovery hold remains unchanged.

## Verification

- Focused trial and MiniApp entry suite: 210 passed.
- Trial, persistence, schema, scheduler and startup recovery suite:
  1017 passed, 94 subtests passed.
- Compileall and `git diff --check` passed before deployment.

## Production Gates

1. Restart must add the archive column without service restart loops.
2. Existing `xuruode8` unknown evidence must remain untouched on 2026-09-29.
3. On the next daily wave, the old operation must appear once in the archive,
   the fresh operation must use a new operation ID, and no old reward may be
   reported as confirmed.
