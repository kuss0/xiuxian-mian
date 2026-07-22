# Full Audit Tail Closeout 2026-07-22

## Scope

This closes the remaining review tail recorded for the 2026-07-03 full audit:

- listener sidecar account refresh and session ownership;
- listener heartbeat temporary files and per-event write amplification;
- the global send lock findings;
- delayed-action terminal-row retention.

The source audit remains evidence, not an instruction to replace production
semantics wholesale. Each finding was rechecked against the current checkout.

## Listener Sidecar

The sidecar no longer copies a main account session. It requires a separately
authorized `listener_account_<id>.session` and rejects an auth key shared with
the main account. Production has no separately authorized listener account, so
the sidecar is intentionally inactive and the main runtime listener is the
only active listener. This is an explicit degraded mode, not claimed
redundancy.

The retry loop reloads persisted state and recalculates target accounts on each
retry interval. Runtime account additions and credential changes therefore do
not depend on the startup snapshot.

Crash-left heartbeat temporary files are removed at startup. Normal message and
edit events now update in-memory counters only; the existing 15-second loop
writes the heartbeat. Handler exceptions still write immediately so failure
evidence is not delayed. The write-amplification change is commit `7cc6cd60`.

## Global Send Lock

The global send lock is retained. It is the production serialization boundary
for game-group commands and enforces ordering, identity gaps, module gaps, and
recovery FIFO behavior. Splitting the lock without replacing those invariants
would increase burst and reordering risk across modules.

The current queue path has a bounded wait, releases the lock in the send-slot
`finally` block, and classifies queue timeout as definitely unsent. RPC timeout
tests verify the lock is released and a following send can proceed. On the
current Python runtime, cancellation of a waiting `asyncio.Lock.acquire()`
removes the waiter; there is no live deadlock evidence. The original proposal
to shorten the lock around only part of the RPC is therefore rejected unless a
new design preserves the same global serialization contract.

## Delayed Actions

Permanent failures previously remained in the in-memory and persisted delayed
action collection even after their result had been returned to the owning
module. That could accumulate terminal rows across restarts.

The queue now returns the same failure result to the business handler and then
removes the terminal action. Restore accepts only pending actions and drops
legacy terminal or identity-less rows. A deduplicated pending retry retains its
attempt count deliberately: resetting it on every repeated schedule would let
a caller bypass `max_send_attempts` indefinitely.

The production delayed-action snapshot contained zero rows before deployment,
so this change needs no data migration and cannot discard an in-flight action.

## Gates

- CommandAttempt remains shadow-only; Gate 4 is not approved.
- Listener redundancy remains unavailable until a separate listener account is
  explicitly authorized.
- The shared send lock remains frozen absent concrete deadlock evidence or a
  separately reviewed replacement serialization design.
