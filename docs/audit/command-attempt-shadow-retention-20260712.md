# CommandAttempt Shadow Retention Plan

Status: planning only. No archive or delete job is approved or enabled.

## Current Projection

At the 2026-07-12 07:47 UTC+8 checkpoint:

- observation age: about 5.7 hours;
- attempts: 289, all `transport=sent`, `business=open`;
- evidence: 387;
- transitions: 1,156;
- persisted sent-log parity: 289/289;
- non-strong written bindings: 0;
- observed rate: about 50.65 attempts/hour;
- 72-hour projection: about 3,647 attempts;
- projected Attempt/Evidence JSON payload: about 0.84 MB.

This volume does not justify runtime cleanup during the 72-hour evidence gate.
SQLite file size is reported for trend visibility but is not attributed wholly
to CommandAttempt because the database contains the full application state.

## Retention Rules

1. During the first 72 hours, retain every Attempt, transition, and evidence row.
2. Do not delete rows in the send path, listener path, scheduler, startup path,
   health observer, or checkpoint timer.
3. `business=open` is expected in shadow mode because reducers do not consume
   the ledger. It is not independently eligible for deletion.
4. The 12-hour checkpoint must report row counts, oldest age, growth rate,
   projected 72-hour rows/payload, DB file size, and transport distribution.
5. Raise a capacity review if any checkpoint observes one of:
   - more than 100,000 Attempt rows;
   - more than 250 MB of estimated Attempt/Evidence payload;
   - sustained growth above 10 times the first-day baseline;
   - checkpoint query runtime above 5 seconds;
   - SQLite lock errors or measurable send-path latency regression.

## Future Shadow Archive Gate

Any archive implementation is a separate batch after the 72-hour review. It
must remain outside runtime control and satisfy all of the following:

- export complete Attempt, transition, and evidence timelines to dated JSONL;
- write a manifest containing row counts, min/max timestamps, and SHA-256;
- verify the exported counts and digest before any database deletion;
- archive only transport-terminal rows older than an approved retention age;
- never infer business completion merely from `business=open` shadow rows;
- run under a dedicated maintenance command, not an automatic hot-path task;
- default to report-only and require an explicit archive approval flag;
- preserve a database backup and support a restore rehearsal in Lab.

No archive code, delete SQL, or retention timer is introduced by this plan.
