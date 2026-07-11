# CommandAttempt Gate 0 Specification

Status: approved for Gate 1 implementation under
`ADR-20260712-command-attempt-conditional-approval.md`.

This document defines the invariants that must exist before any runtime shadow
write. CommandAttempt is a durable saga/event timeline. It is not an ACID
transaction spanning Telegram, the game bot, and local SQLite.

## Scope

Gate 0-3 may record facts and proposed evidence bindings. They must not alter:

- command send/resend decisions;
- cooldowns or module timers;
- module business state;
- reducers, scheduler locks, guards, or recovery behavior.

All feature flags default to off.

## Data Ownership

`command_attempts` is the current projection for one command attempt.

`command_attempt_transitions` is the append-only history of transport and
business projection changes.

`command_attempt_evidence` is the append-only evidence ledger. Full message
text remains in the message log; Attempt stores a digest and a redacted payload.

Module state remains authoritative for gameplay until a later gate explicitly
approves a module migration. During shadow gates there is no dual authority:
Attempt is observational only.

## Identity

Attempt creation requires either:

1. an explicit, registered `send_as_id`; or
2. an active `use_identity()` context containing a registered identity.

The legacy first-identity fallback is forbidden inside Attempt APIs. Gate 0-3
do not change global identity behavior elsewhere.

## Transport States

- `created`: durable intent exists; no queue claim yet.
- `queued`: the runtime has entered the send queue.
- `blocked`: the attempt is definitely unsent or conservatively blocked.
- `sent_no_id`: send may have succeeded but no root message ID is available.
- `sent`: a root message ID or equivalent strong send evidence exists.
- `send_unknown`: Telegram send outcome is unresolved; default is no resend.
- `timed_out`: a transport/reconciliation deadline elapsed without closure.
- `abandoned`: transport work is intentionally closed without a send.

Allowed transitions are explicit in `model.command_attempt.types`. `blocked`,
`sent`, and `abandoned` are terminal for normal automatic transport changes.
Late strong evidence may correct `sent_no_id`, `send_unknown`, or `timed_out`
to `sent`.

## Business States

- `open`: no business progress is confirmed.
- `progressed`: intermediate business evidence was reduced.
- `manual_required`: automation is held for human review.
- `terminal_ok`: business outcome completed successfully.
- `terminal_fail`: business outcome completed with a real game failure.
- `abandoned`: business handling was intentionally closed.

`terminal_ok`, `terminal_fail`, and `abandoned` are immutable. A real negative
game result is `terminal_fail`; an unsent command is not.

## Idempotency And Ordering

- Every projection transition requires a stable `transition_key`.
- Reusing a transition key returns the existing projection without applying a
  second mutation.
- Projection rows contain an integer `version`; updates use compare-and-swap.
- Evidence requires an idempotency key. Duplicate live/log observations do not
  create a second evidence row.
- Sequence numbers are allocated inside `BEGIN IMMEDIATE` transactions.
- Terminal states do not move backward.

## Evidence Binding Contract

Gate 1 does not bind inbound evidence. Gate 3 binding must return one of:

- `matched`: exactly one strong candidate;
- `ambiguous`: multiple candidates; no binding is written;
- `unmatched`: no acceptable candidate.

Binding priority:

1. exact `reply_to_msg_id == root_msg_id`;
2. exact edit of a tracked `result_msg_id`;
3. explicit `op_id` or `chain_id` metadata;
4. identity/family/time only for candidate generation.

The most recent candidate must never be selected as a fallback. Unanchored
broadcasts remain PassiveObservation.

## Consistency With Module State

Gate 0-3 are observational and do not update module state.

Before Gate 4, each migrated reducer must define one of:

- a single SQLite transaction updating Attempt projection and module state; or
- an append-only transition/outbox with an idempotent projector.

Every business transition requires a stable transition key so replay cannot
apply rewards, cooldowns, or state changes twice.

## Feature Flags

- `XIUXIAN_ATTEMPT_SHADOW_WRITE=0`
- `XIUXIAN_ATTEMPT_SHADOW_BIND=0`
- `XIUXIAN_ATTEMPT_RECOVER_REPORT_ONLY=0`
- `XIUXIAN_ATTEMPT_CONTROL_MODULES=`
- `XIUXIAN_ATTEMPT_CONTROL_IDENTITIES=`

Control lists are inert until a later ADR approves Gate 4. Enabling shadow bind
does not imply shadow write; callers must validate prerequisites explicitly.

## Retention And Privacy

- Open, ambiguous, manual-required, and abnormal attempts are retained.
- Normal terminal attempts default to 60 days and may be pruned in bounded
  batches.
- Full game message text remains in message logs; Attempt stores a digest.
- Payload keys containing token, cookie, session, authorization, initData, or
  password are replaced with `[REDACTED]` recursively.
- MiniApp URL tokens and Telegram session material must never enter Attempt
  payloads.

## Gate 1 Acceptance

- Schema creation is restart-safe.
- Identity fail-closed tests pass.
- Transport and business transitions enforce the state graph.
- Duplicate transitions and evidence are idempotent.
- Compare-and-swap conflicts are observable.
- Restart reads preserve open attempts and evidence.
- Bounded retention deletes only eligible terminal attempts.
- No runtime/app import or behavior change is introduced.
