# R07: Durable Transport Ownership Review

Status: Proposed for isolated candidate implementation; approval requested.
Candidate baseline: `a5db2b32` on `rebuild/stability-20260907`.
Production deployment, live requests and CommandAttempt control remain excluded.

## Confirmed Failure

The existing in-memory `_GAME_SEND_TASKS` receipt protects a running process,
not a killed process. Message-keyed pending work cannot describe a dispatch
whose Telegram message ID is still unknown. Saving the existing state after
the fake server observes the command does not close that gap.

The isolated probe now distinguishes two crash points:

1. `before_id`: the fake transport observes its effect, state saves, and the
   process is killed before Telegram returns a message ID.
2. `after_id`: runtime finalizes the receipt and state saves, but the process
   is killed before the calling feature receives that receipt.

Verified against the candidate on 2026-09-10:

| Command | Crash point | Tracking | Reload clock advance | Result |
| --- | --- | --- | --- | --- |
| checkin | before_id | enabled | 0 | Second transport invocation; unsafe |
| rift | before_id | enabled | 0 | Second transport invocation; unsafe; saved guard attempt was zero |
| checkin | after_id | disabled | 0 | ID was known, no pending row survived; second invocation |
| rift | after_id | disabled | 24 hours | Saved guard attempt was one, but expiry allowed a second invocation |

Both post-ID results disprove a design that simply deletes a pre-send marker
as soon as Telegram returns an ID. A temporary cooldown/guard can mask the
gap for one immediate restart, but cannot establish durable ownership.

The probe starts only fake clients in spawned processes and temporary SQLite
directories. SIGKILL targets only that probe's child process. No Telegram
session, production database or inventory API is used.

```bash
/root/xiuxian-main-rebuild-venv-20260907/bin/python \
  tools/lab_send_crash_probe.py --command checkin --assert-safe
/root/xiuxian-main-rebuild-venv-20260907/bin/python \
  tools/lab_send_crash_probe.py --command rift --assert-safe
/root/xiuxian-main-rebuild-venv-20260907/bin/python \
  tools/lab_send_crash_probe.py --command checkin --crash-point after_id \
  --untracked --assert-safe
/root/xiuxian-main-rebuild-venv-20260907/bin/python \
  tools/lab_send_crash_probe.py --command rift --crash-point after_id \
  --untracked --advance-seconds 86400 --assert-safe
```

Exit 1 with `safe=false` is the reproduced defect, not a passing safety test.
`--advance-seconds` changes only the isolated reload process's clock; it does
not sleep, touch host time or change the first process's dispatch clock.

## Proposed Boundary

Extend runtime's existing transport/pending ownership, not the shadow Attempt
ledger. This is a behavior-changing architecture decision, not an observability
patch. Its implementation must receive a separate offline review before it is
used in the candidate's send path. It grants no production rollout permission.

- Persist a stable transport-operation ID before invoking the Telegram send
  coroutine. Capture identity generation, account, original chat/topic,
  original reply target, exact command identity and caller operation metadata.
- Preserve a no-ID `dispatch_unknown` record across timeout, cancellation,
  process loss, reload, pause, route switches and expired business guards.
  Elapsed time alone is not evidence that a command was never sent.
- An acknowledged message ID advances the same operation to a handoff state.
  It must not erase the only durable evidence before pending ownership and the
  feature's state transition can survive a crash. `track=False` must receive
  an explicit handoff contract, not a silent tracking-policy rewrite.
- Release a definitely-unsent operation only from a proven pre-dispatch stop
  or an explicit Telegram rejection that guarantees no send. Unknown errors
  cannot take that transition. Persistence failure before dispatch means no
  send; persistence failure after dispatch means unresolved ownership.
- Resolve an acknowledged operation through the existing exact-root reply
  route and owning reducer. Domain cooldowns, resource effects and one-shot
  decisions stay with that reducer; a transport fact alone is not completion.
- A completed business result and release of its transport ownership need a
  defined atomic/durable ordering. A replay after either crash boundary must
  clean residual bookkeeping without applying the result or notification twice.
- Do not add automatic resends, query loops, guessed message-ID adoption or
  an alternative shared business scheduler. Do not read Attempt state to allow
  or deny a send. Its flags and shadow-only behavior remain unchanged.

## Binding And Admission

Exact binding requires the recorded operation/receipt and original owner.
Account, chat, identity and command text with a time window can generate a
candidate, but cannot prove which no-ID operation owns a message. Ambiguity
must remain explicit and visible. Telegram's native request identifier may be
recorded as a correlation fact; this proposal does not authorize retransmitting
that request as a recovery mechanism.

Binding is chat-specific. Duplicate-send admission must nevertheless prevent
the same unresolved logical action from being reissued through the backup
group. Identity deletion, recreation or account rebinding must not transfer
old operations to the new owner or silently discard still-unknown sends.

UI pause, module reset and ordinary cleanup cannot erase unresolved transport
facts. Any manual resolution requires a narrowly scoped operation ID, recorded
evidence/reason and explicit operator action; it must not resemble a general
"unlock all" toggle. Diagnostics must distinguish an unknown old operation
from a newly requested command that was definitely not sent.

## Persistence And Capacity

Choose the storage shape with the existing persistence owner. Do not introduce
a second independent business ledger or rely on an in-memory marker followed
by a periodic flush. Test the actual SQLite commit boundary, failed commits,
schema migration and rollback compatibility before runtime integration.

Active unknown operations must not expire through retention. Completed detail
must have bounded retention and replay tombstones. Reaching a capacity limit
must report an explicit affected-owner hold, not evict unknown work or pause
unrelated identities. Raw credentials, WebApp URLs and session tokens must not
appear in diagnostic summaries. Benchmark write latency and lock contention;
do not perform broad history scans while owning the send queue/RPC locks.

## Acceptance Before Closing R07

- Both crash points, tracked and untracked callers, with real process kills
  and fresh-process reloads; repeat after the business guard/retention horizon.
- Crash before the durable commit, after it but before transport, after
  transport, after receipt persistence and between business commit/cleanup.
- No duplicate transport call on unresolved work, including route changes,
  identity disable/re-enable, account rebind and repeated scheduler ticks.
- Exact positive evidence resumes/finishes the existing operation without
  manual fabrication; conflicting, missing and stale evidence never binds.
- Known-unsent and ordinary successful operations retain legitimate future
  execution. A permanent blanket freeze is not an acceptable repair.
- Early results, delayed RPC receipts, cancellation and notification errors
  cannot regress the operation or duplicate business effects.
- Module/transport handoff tests cover actual callers, including untracked
  callers; the isolated fake-RPC probe alone cannot establish this contract.
- Legacy migration, corrupt records, failed writes, bounded retention,
  operator diagnostics and targeted manual resolution are verified.
- Full regressions, contract review and deployment comparison remain required.
  A green narrow probe is not the full-project final acceptance.

## Current Disposition

R07 is open. Only the offline probe and this design review were changed in
this checkpoint. No durable transport controller or fence was inserted into
runtime, and no production behavior was changed. Continue independent R11,
MiniApp and UI review while the shared architecture decision is outstanding.
