# R123 Trial Cross-Day Unknown Archive

Status: deployed as `b111a274`; production migration and the first natural
next-day trial were verified on 2026-09-30. This closes the scoped cross-day
archive acceptance, not the broader CommandAttempt or MiniApp recovery work.

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

## Natural Production Acceptance: 2026-09-30

Read-only verification at 03:12-03:19 Asia/Shanghai:

- Service has remained active since 2026-09-29 12:36:44, with `NRestarts=0`.
  The archive column exists and the existing unknown operation survived until
  the next daily wave.
- Identity `3504367852` (`xuruode8`) has exactly one archive entry for operation
  `67d11634d2f04f15a97747b7704cf971`, source day `2026-09-18`, archived for
  `2026-09-30`. Its original `finish` checkpoint still says dispatched,
  `outcome_unknown=true`, and has no round receipts.
- New operation `265dadc09ec645f19f8606f46e7b39dd` is settled, not pending or
  unknown. It has three distinct round receipts with daily progress 1, 2 and 3,
  each under a daily limit of 3. Reward traces are 14, 10 and 19.
- Journal at 01:02:14 reports three current-day settlements and trace +43,
  matching only those new receipts. Batch `cave_public_1790701202_12` finished
  at 01:12:54 with 12/12 identities successful, 36 rounds and trace +516.
- Repeated read-only checks did not create another archive entry or change the
  active operation. No manual sends, retries, configuration changes or database
  writes were used for acceptance.
- Follow-up regression selection: 115 passed, 115 deselected
  (`test_trial_operations.py` and `test_cave_treasure_runtime.py`,
  `-k 'archive or cross_day or trial'`). Tests used the isolated pytest DB.

The later daily wave is still a separate monitoring checkpoint. The archived
September 18 outcome remains unknown; archival does not settle that debt or
authorize replay of its old request.
