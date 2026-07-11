# CommandAttempt Shadow Observation

- Status: active observation; Gate 4 not approved
- Started: 2026-07-12 02:01:32 UTC+8
- Scope: production shadow write and strict shadow bind only
- Runtime commits: `2d66f594`, `2635c57e`, `014aa210`
- Pre-deploy database backup:
  `data/state/chaogu_state.db.pre-command-attempt-shadow-20260712020047`

## Enabled Flags

- `XIUXIAN_ATTEMPT_SHADOW_WRITE=1`
- `XIUXIAN_ATTEMPT_SHADOW_BIND=1`
- `XIUXIAN_ATTEMPT_RECOVER_REPORT_ONLY=0`
- `XIUXIAN_ATTEMPT_CONTROL_MODULES=`
- `XIUXIAN_ATTEMPT_CONTROL_IDENTITIES=`

The shadow ledger has no send, retry, cooldown, reducer, scheduler, recovery, or
module-control authority.

## Validation Before Deploy

- Focused Gate 2 tests: 40 passed.
- Focused Gate 3 and routing tests: 52 passed.
- Final full regression: 2907 passed, 391 subtests passed.
- Production database migrated from schema 8 to schema 9.
- All four Xiuxian services remained active after deployment.

## Initial Production Checkpoint

Checkpoint: 2026-07-12 02:15 UTC+8.

- Attempt rows: 18.
- Transport state: 18 sent; 0 created; 0 queued; 0 send_unknown; 0 blocked.
- Root send parity: 18/18 sent Attempt root IDs exist in `message_index`.
- Evidence rows: 25.
- Exact `reply_to_msg_id == root_msg_id` bindings: 25.
- Guessed identity/family/time bindings: 0.
- Transport rows stale for more than five minutes: 0.
- `database is locked` or CommandAttempt shadow failures: 0 observed.
- Sensitive token/session/startapp scan: no matches.
- Read-only aggregate query latency: about 4 ms at this sample size.
- Offline replay of `data/messages/2026-07-12.log`: 5,187 events;
  33 strong-anchor matches, 5,154 unmatched, 0 ambiguous. Replay performed no
  evidence writes.

Observed Tianxing exploration for `growrdick` and `xuruode6` completed with the
existing protected route and normal result handling. Shadow persistence added no
game command and did not alter those module states.

## Observation Gates

Keep shadow mode active for 24-72 hours. Each checkpoint must verify:

1. every sent Attempt maps to real send evidence;
2. anchored binding precision remains 100 percent;
3. ambiguous candidates produce zero written bindings;
4. no stale created/queued rows or continuous abnormal growth;
5. no SQLite lock errors or material send-path latency regression;
6. no secrets in Attempt intent, metadata, or evidence payloads;
7. no control flag becomes enabled.

Gate 4 requires a separate review after the evidence window. No module takeover
is authorized by this document.

Send parity must use persisted `event_type=sent` message logs as the durable
source. `message_index` is a bounded recent-message index and may legitimately
evict older root IDs; it is only a fast-path check. At the 03:09 checkpoint,
82/82 sent Attempts were present in persisted sent logs even though the bounded
index had already evicted the earliest rows.

## 5.7-Hour Checkpoint

Checkpoint: 2026-07-12 07:47 UTC+8.

- Attempt rows: 289.
- Transport/business distribution: 289 `sent/open`.
- Persisted sent-log parity: 289/289.
- Evidence rows: 387.
- Bind reason: 387 `exact_reply_to_root`.
- Bind anchor: 387 `reply_to_msg_id`.
- Non-strong or guessed written bindings: 0.
- Stale created/queued/sending rows over five minutes: 0.
- Attempt `last_error` rows: 0.
- Production blocked samples: 0.
- Production send-unknown samples: 0.
- Observation rate: about 50.65 attempts/hour.
- 72-hour projection: about 3,647 attempts and 0.84 MB of
  Attempt/Evidence JSON payload.

The production absence of blocked/send-unknown samples is not being forced.
Lab coverage verifies both paths:

- `global_disabled` before send-slot entry records `blocked` with
  `definitely_unsent=true`;
- queued `send_timeout` records `send_unknown` with
  `definitely_unsent=false`;
- both preserve the legacy return value and do not take control.

Validation command:

```bash
.venv/bin/python -m pytest -q tests/test_command_attempt_runtime_shadow.py \
  -k 'pre_send_block or queued_send_timeout'
```

Result: 2 passed.

## Periodic Checkpoint

`tools/attempt_shadow_checkpoint.py` produces the read-only aggregate used for
the observation gate. It reads the SQLite ledger and persisted message logs,
then writes JSON reports under:

```text
data/state/command_attempt_checkpoints/
```

The accompanying systemd timer runs at 00:15 and 12:15 UTC+8 with up to five
minutes randomized delay. It has no send, retry, cooldown, recovery, reducer,
scheduler, or business-control authority.

Capacity and future archive constraints are defined in
`docs/audit/command-attempt-shadow-retention-20260712.md`. No archive or delete
job is approved during the 72-hour evidence window.
