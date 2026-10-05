# Fishing Gift Handoff

Base: `05c57417`. Lab: `/root/xiuxian-fishing-gift-handoff-20261005`.
Initial audit and second review are separate maintainer passes, not independent
external reviews. Status: accepted candidate, rollout checkpoint below.

## Reproduction

The timing-canary Lab retained a strict expected-failure test: fishing clears
`fishing_caught_fish_json` after an in-memory storage batch accepts the task;
clearing process memory and reloading SQLite loses the waiting gift. The
production helper has the same acceptance/completion confusion.

Read-only production inspection on October 5 found no configured fishing gift
target and no queued catch. This proves a reachable implementation risk, not an
observed loss of a user's fish. No target was enabled or live gift sent.

## Narrow Repair

- A bounded per-identity `fishing_gift_handoff` replaces the fishing caller's
  handoff to the volatile batch queue. Moving catches into it and clearing the
  old queue share one checked SQLite save. It stores one outstanding task or
  last completed task, at most 64 item types / 16 KiB.
- Fishing waits for the existing storage task/batch to become idle and then
  uses the same gift task, sender, safety gates, receipt handler and interval.
  It does not add a Telegram sender or change the manual batch API.
- Sender intent is saved before any command, including a locator message.
  Only proven-unsent work may use the existing bounded retry path. A receipt
  wait, send timeout, exception, cancellation or unknown outcome cannot cause
  another gift send. Original anchored game replies can still be recovered
  through the existing handler while the task is live.
- Each confirmed item and its local inventory projection commit together.
  Partial completion is retained; new catches are a separate queue. Errors
  roll back only this transaction's in-memory changes and halt the sender.
- Reloaded queued work can resume. Reloaded active/sending/waiting work becomes
  held for evidence review, without sending; held work cannot be overwritten by
  a new catch. This is conservative at-most-once recovery, not an exactly-once
  guarantee or an automatic retry of unknown delivery.
- Unknown/corrupt records stay blocked. The existing fishing status/error
  fields expose queue, confirmed progress and review-required states. No new
  UI controls, account switches or MiniApp limits are introduced.

## Review Checklist

The first focused run passed 160 tests / 21 subtests. Expanded regression passed
593 tests / 75 subtests, including isolated SQLite reloads, send cancellation,
save failures, replacement identities/records, partial progress and inventory
rollback, no duplicate sends, and foreign/unscoped receipt rejection.

Review additionally found two dispatch boundaries and one stale-caller error
write. The final candidate pins the send to a configured anchor group, reuses
the sender's pure `operation_check` at actual dispatch, and uses the shared
definitely-unsent classifier. A stale caller cannot write even its error into
a deleted/recreated/rebound identity. These are tested, including the
source/target becoming invalid while queued and foreign group anchors.

Intermediate full suites passed 15848 and 15858 tests / 1436 subtests; they are
not final-version acceptance. After the final stale-caller change, focused
regression passed 557 tests / 488 subtests. Final frozen full-suite acceptance:
**15861 passed, 1436 subtests**, 445.41 seconds. JUnit:
`/tmp/xiuxian-fishing-gift-handoff-accepted-20261005.xml`.
Second review re-read queued/active ownership, exact message/group binding,
failure rollback, configuration changes during transport, normal manual batch
compatibility and restart behavior. No remaining blocker found for this narrow
path. Second-pass regression: **836 passed, 492 subtests**. Ruff, compilation and
diff checks passed. All tests used `XIUXIAN_ALLOW_LIVE_TEST_DB=0`.
Runtime/test files remained frozen:

```text
9362c21a3c6824eb50b945b95022159c97f2e35baea66b3ce5b4b01269289c4a  model/features/fishing_gift.py
96863cc332321888a621b55f43bbf0babf6dbb900af9bb4507a53dcb32eaf558  model/features/storage_bag.py
3f0980c0de6dca19b8cacbccf3b48d25cd179e2eb5744ea14cd32025a98bdb5b  model/features/fishing_runtime.py
1b2ec83a73ec63cd95af740dbaa48c34469489c3e31899544202017fa0c80dbb  model/persistence.py
a75342d1e7a15552dba6cfc3abccba2d03b33e358df68e1866dba29334171f4d  model/state.py
801a1a5f53f9ee43cdc61e74d8991541de6854cc8eafd76db3ea295c7df5b561  tests/test_fishing_gift_handoff.py
bb718bea51615f0ed5b2da6ffba92d035030c3dfedb67c586f4c1ae40c4b3c54  tests/test_fishing_runtime.py
```

## Boundaries And Remaining Debt

- The old native-fishing Lab is not merged. Native catch-to-queue generation,
  native fish-open migration and public-only gift transport are separate work.
- General manual storage batches remain volatile. Their legacy gift/listing/
  buying retry behavior is not declared repaired by this opt-in fishing path.
- Restarted in-flight work requires manual evidence review using the retained
  source/target/account IDs, operation ID, item index, command, group, locator,
  command message ID and original message logs. Do not clear or replay a held
  task simply because no receipt was found. Inventory quantity alone is not
  proof that a particular gift succeeded.
- A queued task is safe to resume; an active task interrupted even before the
  actual RPC is deliberately held. This sacrifices automatic recovery where
  non-delivery cannot be established, rather than risking duplicate gifts.
- No natural gift validation exists without an authorized target. Do not claim
  live gift success from mock transport and real SQLite tests.

## Deployment And Rollback

After final isolated tests and second review, use one controlled main-service
restart only when pending gameplay and near-term high-risk actions allow it.
Snapshot game and notification databases; preserve all identity switches,
fishing targets and the historical unknown notification batch.

The schema addition is additive. Do not roll back to the old fishing caller
while a nonempty handoff exists: old code cannot consume this ledger. Preserve
the database and resolve/hold that work explicitly first. Never restore an old
database to undo a code deployment.

## Notification Follow-up

The prior quiz/wild notification patch remains unchanged. A natural external
quiz timeout at 19:58:04 appears only in the local journal; the 19:57-19:59
window has no `TG_NOTIFICATION_DELIVERY` receipt. Since 19:30 the runtime
notification metrics show one confirmed 230-unit / 4-line summary with no
mentions or repeated confirmed payload. This does not cover independent
watchdog/report senders or prove a complete game-day result summary.

At 20:36 the main service, observer and watchdog are active; main NRestarts=0.
The observer's only reason remains the October 5 09:11 held notification batch.
It is retained, not replayed or erased. A natural wild-result summary after the
prior patch is still pending observation.
