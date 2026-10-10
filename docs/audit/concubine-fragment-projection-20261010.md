# Fragment Phase Ownership, 2026-10-10

## Incident

Production base: `b01c9a9a`. WA identity `8659059191` remained in
`dream_pending` on October 10, with compatibility message ID zero. Its durable
dream operation `91ab4ec373b84767bf0c72c2180d3e2b` was already complete.
The scheduler kept visiting the identity without progressing to voyage.

Message log `data/messages/2026-10-09.log`, chat `-1002083016447`:

- 18:45:26: moon greeting `1312539` sent.
- 18:45:41: official greeting response `1312541`, affinity +6.
- Dream operation start: `1791542740.2057195` (18:45:40).
- 18:45:44: dream command `1312542`, matching operation/source metadata.
- 18:45:46: official dream response `1312543`, bot `8788895566`, server
  timestamp `1791542745`. Duplicate North fragment, Xutian progress 3/4.
- Durable result: complete/applied, cooldown `1791571550` (October 10 02:45:50).

The timestamp order is consistent with a peer affinity update during transport;
it does not prove exact callback execution order. Offline injection of that
update reproduces all four original failures (dream/puzzle, early/normal reply).

## Narrow Fix

The whole-business plan includes affinity. An unrelated affinity change can
prevent the transport receipt from writing its compatibility anchor; the shared
zero-anchor release guard then correctly refuses to guess ownership.

Only `concubine_fragment_actions.py` changes runtime behavior. The durable action
gets an optional three-field projection: phase, command anchor, deadline. A
matching operation may bind its own receipt and release its phase after a peer
business update. Projection validation checks exact fields, integer anchors and
the operation-derived deadline. A replacement phase, anchor or deadline wins.

Admission/pre-send guards, whole-business plan checks, partner/snapshot/resource
guards, sender/chat/op binding and unknown-send recovery remain unchanged.
There is no shared sender, safety lock, Tianxing, UI or cooldown-policy change.
Older records remain readable and conservative; no automatic inference for a
legacy zero-anchor record is added.

## Verification

All pytest runs use `XIUXIAN_ALLOW_LIVE_TEST_DB=0`.

- Initial focused regression: 347 passed.
- Expanded focused regression: 390 passed. Covers real duplicate-fragment text,
  early/native replies, normal receipt, replacement timers/phases/anchors,
  invalid projection, pre-send cancellation, SQLite reload, unknown send,
  duplicate reply and legacy records. Existing shared ownership tests stay green.
- Full regression: 17,142 passed, 1,517 subtests passed in 458.45 seconds;
  JUnit `/tmp/xiuxian-fragment-projection-full-20261010.xml`.
- Second cross-module review: all concubine and Wanxin suites, 3,428 passed,
  13 subtests passed. Source review confirmed no changes to common phase release
  or resource projection, and no automatic legacy-record inference. Ruff passed.
- Production deployment and natural continuation: pending.

## Existing WA State

The old completed operation has no projection. The runtime fix alone deliberately
does not erase that legacy wait. Plan: with the worker stopped after drain,
validate the exact persisted operation against the official command/reply logs,
back up SQLite, and conditionally change only `concubine_phase` from
`dream_pending` to `idle`. No change to rewards, progress, affinity, cooldowns,
timers, module switches or durable action records. No replay of game commands.

The phase repair must be recorded separately from patch verification. Normal
scheduler continuation is the live acceptance criterion, not just a green test.
The one-off helper `/tmp/xiuxian-wa-dream-reconcile-20261010.py` passed read-only
validation of the exact production row and anchored official logs. Apply mode
requires the service and worker to be stopped, an unused backup path, no pending
commands, matching operation/resources and a conditional single-row update.

## Other Boundaries

- The 04:25 production restart was the scheduled R2 backup, not a crash or this
  repair. Worker at investigation: `318523`.
- Five historical notification batches are held for delivery review, without
  automatic replay. Ordinary 09:50:33 summary was confirmed (602 UTF-16 units,
  10 lines, no mentions).
- Nineteen channel identities remain group-send frozen; native MiniApp actions
  continue. No channel-send bypass is introduced.
- WA/Baji duel, incense refinement and Boss switches are not changed.
- Existing trial/voyage Labs remain separate. CommandAttempt stays shadow-only;
  Gate 4 and open-business retention remain unapproved.
