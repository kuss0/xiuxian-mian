# wxjerry / Rust Upstream Absorb Review 2026-07-16

## Scope

- Production baseline: `49bf8760 Preserve renamed identity relationships`
- wxjerry: `794bbbf`
- Rust: `c4bbc85`
- Both reference repositories were fetched and are aligned with their current `origin/main`.
- This review does not cherry-pick upstream code, change runtime control, restart services, or send game commands.

The local checkout, current state schema, tests, and real message logs remain the source of truth. Upstream code is used as a design and protocol reference only.

## Decision Summary

### 1. Incremental persistence: absorb the idea, not the patch

wxjerry `fc78e0c` is the highest-value upstream change. It tracks dirty metadata, identities, and modules, then writes only the selected scope. The local line has real write amplification:

- 24 persisted identities.
- 67 metadata codec keys written by every current `save_state()`.
- At least 225 mutating SQL statements per full save at the review checkpoint, before schema checks, selects, WAL traffic, and backup internals.
- 961 static `save_state()` call sites and 175 `mark_dirty()` call sites under `model/`.
- The live DB and last-good guard DB are each about 12 MB.
- Every successful local `save_state()` currently calls SQLite `backup()` for the complete last-good DB.

Reproducible read-only baseline:

```bash
.venv/bin/python tools/persistence_write_profile.py
```

An isolated copy of the live DB was then loaded three times through the current
runtime persistence code. No production file was opened for writing.

| Mode | Iteration times | Traced mutating SQL |
|---|---:|---:|
| Current full save plus last-good backup | 47.64ms / 48.84ms / 48.96ms | 225 each |
| Current full save with backup disabled | 22.49ms / 21.45ms / 21.72ms | 225 each |

This is a no-logical-change save. The SQL half and the full-backup half are both
material; optimizing only one leaves roughly half of the current cost. P1 must
make a repeated no-change save issue zero business-table mutations, while P2
must preserve immediate structural protection without copying the full DB after
every ordinary state update.

Directly applying `fc78e0c` is unsafe for this line:

- Its module scope is inferred from field-name prefixes. Local modules own shared fields, generic pending/message state, and cross-module metadata that are not safely described by one prefix.
- Local persistence has identity-collapse guards, external watchdog pause reconciliation, CommandAttempt tables, richer pending-task columns, and a last-good backup path absent from the upstream design.
- A commit can succeed while the in-memory persisted snapshot update fails; the next flush must remain correct after restart and must not lose dirty scope.
- Identity deletion, pending-task removal, message-index removal, username aliases, and account mappings must remain atomic.
- Reducing SQL writes alone does not remove the full-database backup cost.

Recommended gates:

1. **P0 Lab baseline**: completed with the read-only profile plus SQLite trace/timing against isolated DB copies. Keep this command output as the comparison baseline for later gates.
2. **P1 Snapshot-diff full API**: preserve the existing `save_state()` contract and call sites, but compare canonical post-load snapshots and write only changed metadata/identities. Keep full-save fallback on unknown scope or snapshot loss.
3. **P2 Guard backup generations**: decouple last-good backup cadence from every ordinary state commit. Back up immediately on roster/account structural changes, and periodically after ordinary commits. A backup failure must not roll back an already committed state transaction.
4. **P3 Explicit identity saves**: migrate high-frequency module-local paths to `save_identity_state(identity_id)` only after failure-injection coverage.
5. **P4 Explicit module scopes**: introduce declared field ownership, not prefix inference. Unknown/shared fields fall back to the identity or full-state path.

Required fault injection:

- Failure before transaction commit.
- Failure after commit but before persisted-snapshot refresh.
- Failure during guard backup.
- Restart after each failure point.
- Identity deletion and suspicious identity-collapse rejection.
- Pending/message child-row deletion with an empty replacement set.
- Concurrent metadata plus identity mutation in one flush.

No production persistence change is approved by this review.

### 2. World Boss: local implementation is already the stronger line

wxjerry `e7fad60` and `794bbbf` add the current `/start`, `/state`, `/begin`, `/hit`, and `/finish` protocol, 429 handling, battle-clock calibration, real-time hit reporting, and settlement scoring.

The local line already has:

- `/begin` RTT calibration and server timeline handling.
- Real-time `/hit` requests and accepted damage/perfect aggregation.
- Player HP/death handling.
- The official 0.5-1.25 second perfect-charge constraint with upper-band targeting.
- Consecutive 429 protection and process-wide MiniApp limiting.
- `/finish` recovery and authoritative result preference.
- Per-login-account identity selection and event closeout reporting.

Decision: do not transplant the upstream world-boss state machine. Continue protocol-shape comparison only when a real local capture shows an unparsed field or changed endpoint behavior.

### 3. Public dwelling hunt: compare fixtures only

wxjerry `794bbbf` adds a separate, persistent dwelling-hunt phase machine with uncertain-action reconciliation. The local line already parses the full public dwelling overview, dynamically reads game/action limits, follows hints, carries active-session context, avoids automatic retry of non-idempotent POSTs, and re-enters through `/start` for authoritative state on a later scheduler pass.

Decision: do not copy the separate phase machine. Extract only new response fixtures or hint variants that are absent from local tests. Any future uncertain-write recovery must reconcile through a read-only `/start` before another `hunt`, `reveal`, or `settle` action.

### 4. Fishing chum quota: local command path already covers the business rule

wxjerry `b26455d` learns `usedToday` / `remainingToday` from the MiniApp shop and treats `fishing_chum_daily_limit` as a consumed quota instead of an error. The local command-side state machine already parses the real daily-limit wording and advances to the next configured chum without repeating the exhausted one.

Decision: keep as a conditional MiniApp parser candidate. Add it only after a local MiniApp capture contains `shop.chums` or a real `fishing_chum_daily_limit` response; do not invent fields from upstream alone.

### 5. Account invalidation and membership gates: medium-value follow-up

wxjerry `fdbf13c` preserves account configuration after session invalidation and supports re-login. Rust `25e7dbb` / `140a749` fail closed when an account is not a member of the target group.

The local line already preserves identity configuration when an account goes offline, blocks sends with `account_offline`, exposes the reason in UI, and retains identities through explicit logout while disabling/unbinding them. It does not yet have a single pre-enable target-group membership gate equivalent to Rust.

Decision: add a separate follow-up audit. A membership gate should disable scheduling, not delete identity/module configuration, and must account for the current personal-account plus channel-identity mapping.

### 6. Channel send-as: no direct absorb

Rust `1aadfc7`, `7dcd9da`, and `9622585` add channel-identity fail-closed behavior, a global channel-identity switch, and self-identity short circuits.

The local line already:

- Marks `send_as_peer_invalid` as definitely unsent.
- Closes the action guard for the unsent command.
- Freezes all affected channel identities for the account/group cohort.
- Polls for recovery and restores only identities that were enabled before the freeze.
- Uses direct `get_input_entity(send_as_id)` rather than Rust's per-tick `GetSendAs` cache path.

Decision: no code transplant. Add tests only if a new real failure bypasses the current cohort freeze or restore ownership.

### 7. Trusted Bot learning: retain local anchored policy

Rust `acae7eb` learns Bot IDs from exact display name `韩天尊` in the target group. The local line is more conservative for routing: a strict `hantianzun<digits>_bot` that exactly replies to a registered command root can be learned immediately; nonstandard official shards such as `snpao_bot` and `xlqlcy_bot` require multiple anchored replies across players/commands before persistence.

Current real logs confirm the official display name is exactly `韩天尊`, including nonstandard shard usernames. Tightening substring display-name matching to exact matching is a possible hardening change, but it is not required to absorb Rust's behavior and should be handled separately with replay coverage.

## Absorb Queue

1. P0/P1 incremental persistence Lab.
2. Last-good backup generation/cadence design and restore tests.
3. Account target-group membership audit.
4. Conditional fishing `shop.chums` parser when local capture evidence exists.
5. Exact display-name hardening replay, without weakening strict reply-root binding.

World Boss, public dwelling hunt, and channel send-as are comparison-only unless new local evidence identifies a concrete gap.
