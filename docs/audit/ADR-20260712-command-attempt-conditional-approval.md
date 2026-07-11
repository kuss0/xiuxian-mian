# ADR-20260712: CommandAttempt Conditional Approval

- Status: Accepted with conditions
- Date: 2026-07-12
- Scope: `/opt/xiuxian-main`
- Related audit: `/root/xiuxian-mian-审计报告与整改建议-20260711.md`
- Related implementation plan: `/root/xiuxian-mian-CommandAttempt整改实施方案-20260712.md`
- Risk assessment: `/root/xiuxian-mian-CommandAttempt整改价值与风险评估-20260712.md`

## Decision

The project will pursue CommandAttempt as the long-term mechanism for
consolidating command transport facts, reply evidence, and recovery decisions.

Approval is conditional. The original implementation schedule is not approved
to take production control directly. Gates 0-3 are approved only for design,
Lab validation, shadow persistence, and shadow evidence binding.

Until Gate 4 receives a separate review and approval, CommandAttempt must not:

- alter whether a command is sent or resent;
- advance or delay cooldowns;
- mutate module business state;
- replace module reducers or identity-level scheduler locks;
- drive recovery, replay, or fallback behavior;
- remove existing module recovery paths.

All CommandAttempt flags must default to off. Shadow operation must produce no
additional game commands and no production behavior changes.

## Rationale

The audit's main architectural finding is accepted: command transport facts,
reply/edit evidence, and business state transitions are currently distributed
across runtime, application routing, and feature modules. This leads high-risk
modules to implement overlapping timeout, recovery, and calibration logic.

CommandAttempt has high strategic value because it can provide a durable,
queryable timeline for:

- definitely unsent commands;
- sends with uncertain Telegram results;
- intermediate replies and terminal edits;
- late or recovered message-log evidence;
- business transitions and manual-review decisions.

The attribution is deliberately limited. CommandAttempt does not replace
business reducers, phase machines, resource rules, or scheduler mutual
exclusion. Semantic parsing bugs and concurrent scheduler re-entry remain
independent correctness concerns.

CommandAttempt is therefore treated as a durable saga/event timeline, not a
distributed ACID transaction spanning Telegram, the game bot, and SQLite.

## Mandatory Design Prerequisites

The following five items must be specified in the ADR/design set before runtime
shadow writing begins:

1. Consistency between Attempt projections and module business state, including
   crash recovery and idempotent transition application.
2. Strict evidence binding with explicit `matched`, `ambiguous`, and
   `unmatched` outcomes. Ambiguous evidence must fail closed.
3. Out-of-order and duplicate handling, including legal state transitions,
   terminal-state monotonicity, evidence idempotency, and concurrency control.
4. Layered feature flags separating shadow write, shadow bind, report-only
   recovery, module control, and identity-scoped rollout.
5. Retention, archival, capacity, and secret-redaction policy for Attempt and
   evidence data.

If any prerequisite is unresolved, runtime dual writing is No-Go.

## Binding Rules

Evidence binding must use the following priority:

1. exact `reply_to_msg_id == root_msg_id`;
2. exact edit of a tracked `result_msg_id`;
3. explicit `op_id` or `chain_id` metadata;
4. identity, family, and time window only as candidate generation.

Multiple candidates must remain ambiguous. The system must not select the most
recent Attempt as a fallback. Unanchored broadcasts remain PassiveObservation
and must not be forced into an Attempt.

## Approved Gates

### Gate 0: Specification

- Define transport and business state graphs.
- Define legal transitions and terminal behavior.
- Define cross-state consistency, binding, retention, and privacy rules.

### Gate 1: Store and Shadow Ledger

- Add persistence, idempotent evidence, restart loading, and reports.
- Do not change runtime send or module behavior.

### Gate 2: Shadow Send Lifecycle

- Reuse centralized runtime hooks for blocked, sent, and unknown outcomes.
- Preserve existing return values, guards, pending tasks, and module fields.
- Observe for 24-72 hours with no Attempt reads controlling production.

### Gate 3: Shadow Evidence Binding

- Record proposed bindings without injecting them into reducers.
- Replay real message logs offline.
- Require 100% precision for anchored bindings; ambiguous evidence remains
  unbound.

## Deferred Gates

Gate 4 and later require a separate review. This includes report-only recovery,
module reducer integration, recovery control, resend decisions, and removal of
legacy recovery code.

A low-risk technical pilot should precede the first high-risk business pilot.
`explore_rift` may become the first high-risk migration only after the shadow
ledger and binding gates have passed. Its identity scheduler lock and domain
state machine remain mandatory.

## Go Criteria

Before any module is controlled by CommandAttempt:

- full regression and new concurrency/restart tests pass;
- shadow operation adds zero game sends;
- anchored binding precision is 100%;
- ambiguous binding produces zero guessed assignments;
- open Attempt counts do not leak continuously;
- SQLite write latency and lock errors remain within agreed thresholds;
- crashes at queued, sent-no-id, and sent stages recover conservatively;
- module state and Attempt terminal transitions are idempotently consistent.

## No-Go Conditions

- Flag-off behavior depends on Attempt reads.
- `send_unknown` can automatically resend a high-risk command.
- Binding selects a candidate when evidence is ambiguous.
- Attempt terminal state and module state have no consistency mechanism.
- Existing recovery is removed before shadow comparison and observation.
- Identity fallback or pre-send registration is globally rewritten as part of
  the same rollout.

## Consequences

This decision accepts a slower rollout in exchange for preserving the current
production safety envelope. Early work is judged by factual coverage,
precision, and zero behavior drift, not by how much existing recovery code is
deleted.

The original CommandAttempt implementation plan is subordinate to this ADR. If
the documents conflict, this ADR takes precedence.
