# External Quiz And Wild-Training Notifications

Base: `9ced80bb`. Lab: `/root/xiuxian-quiz-wild-notifications-20261005`.
User request: do not notify external quiz timeouts; summarize wild training.
Initial audit and second review are separate passes by the same maintainer,
not an independent external review.

## Scope And Initial Audit

- After normal timeout cleanup and watcher removal, an external quiz timeout
  (`identity_id is None`) is written only through `console_log`. This handles
  both known/unknown questions and expired question wording. No TG queue entry
  is produced. Managed-identity timeouts still notify; answer confirmation,
  learning new answers and bank conflicts retain their existing paths.
- Both confirmed wild-result notification call sites use explicit low priority,
  including the direct Tianxing result path. They enter the existing durable
  summary window (currently 1800 seconds) and folded detail budget. No new
  schema, aggregation queue, timer, resource total or transport was introduced.
- MiniApp execution failures remain individual notifications. Unconfirmed
  Tianxing deep-route prerequisites still block the action and send high
  priority. Fate consumption, cooldowns, quota, identity ownership and result
  persistence are unchanged. A negative game outcome with confirmed settlement
  is a result, not a transport failure.
- Summary windows do not wait for every identity to finish its game day.
  Existing bounded detail/omission behavior remains; this is not a new complete
  daily reward-total report. Rift notifications are outside this change.

Focused regression: **272 passed, 19 subtests**. Includes external known/unknown
timeouts, owned timeouts, expired wording, worker success/failure, protected
Tianxing release, notification exceptions and real summary-store reload/flush.
The multi-result integration test stubs the production 30-second worker gap
only in the test; production pacing is unchanged.

## Existing Unknown Delivery

Live inspection found one held batch at October 5 09:11:34 CST, containing five
records. Bot receipt `34b495485eaa452e8d96aa55dd055302` was unknown at 09:11:42;
its payload hash matches the held record:

`48a85e924731a4ace52f540483a112d68fe7a50db53c060a52ca65635227392f`

The journal records `routine summary Bot delivery unknown; account fallback
skipped`; nearby Bot polling also reports 502/timeouts. This is real evidence of
the conservative unknown path, not proof the message did not reach Telegram.
Subsequent notifications have confirmed receipts. No automatic replay or
global freeze is observed. Keep the held batch and its observer warning until
delivery can be reconciled; this notification-only patch must not erase it.

The read-only report `data/analysis/notification-followup-20261005.json`, sampled
from October 4 through October 5 19:09, contains 446 transport attempts:
445 confirmed, 1 unknown, no identical confirmed payload repeats. Its coverage
excludes independent watchdog/reports. These counts do not measure the new
patch's reduction, which was not deployed when sampled.

## Rollout Boundary

Full isolated acceptance: **15812 passed, 1436 subtests**, 449.71 seconds.
JUnit: `/tmp/xiuxian-quiz-wild-notifications-full-20261005.xml`.
Ruff, compilation and diff checks passed. Tests used
`XIUXIAN_ALLOW_LIVE_TEST_DB=0`; no production fault injection or game request.

Second review re-read watcher ownership/cleanup, both result call sites,
worker cancellation and notification exception handling, and protected-route
behavior. No blocker found for this notification-only rollout. Repeated
quiz/wild/Tianxing/summary/health/shutdown/red-packet regression:
**563 passed, 31 subtests**. Source hashes remained frozen:

```text
05b063f9abfe8b696d41c6008fb5c95cf4710f761a35fcd64477cb60a4bcc125  model/features/quiz.py
aefa61c0b0c6376b497354dc96f34c78a1e8d84c2c8b1c26aa64655aace74a8e  model/features/wild_training.py
ba755a2f30be215864acd37d16ecaca443fe70d0710b4fcaa2861bc2570c5af7  tests/test_quiz_absorb.py
1c4beb6aa40bbb24844f2a05c85057fef8091d2dc0ceddbc48a9aab701abb392  tests/test_wild_training.py
4f44dbee9c953e20a52c1bca5db4a4afc24ef830d4e10edcc3f08b512bbb797b  tests/test_wild_training_lifecycle.py
```

After full acceptance and a second review, merge only the reviewed code, tests
and evidence. Preserve the auto-updated quiz bank. Check game pending, active
Boss and near-future Tianxing prerequisites before one controlled main restart.
Keep summary flags and the observer required-store contract enabled. Snapshot
game and summary databases, and verify the original held batch survives.

Rollback is reverting these notification call-site changes and restarting the
main service. The unchanged summary schema can still load all pending/held
records. Do not delete the summary database, replay old notifications or roll
back gameplay state. Natural external-timeout and wild-result delivery samples
after rollout remain separate from offline test acceptance.

## Production Checkpoint

- Code `8bf61ef6` merged fast-forward and pushed to `xiuxian-mian/main`.
  Controlled stop began at 19:27:26 CST; old worker exited normally at
  19:27:33. Started at 19:29:42, PID `1940703`, NRestarts 0. Downtime observer
  alerts are explained by this explicit deployment, not an automatic crash.
- Game snapshot `/root/xiuxian-before-quiz-wild-20261005-1927.db` and summary
  snapshot `/root/xiuxian-summary-before-quiz-wild-20261005-1927.db` passed
  quick_check. No database was rolled back.
- 24 identities restored. All 52 module-enable columns and every identity's
  wild strategy match the stopped-state snapshot. Startup normally staggered
  one already-due timer; no claim of byte-identical runtime timestamps.
- The entire historical held list matches the pre-deployment snapshot, still
  one batch. Summary/metrics flags remain enabled; observer/watchdog were not
  restarted. The warning for unknown delivery remains deliberately visible.
- Post-merge isolated regression: **208 passed, 8 subtests**. At 19:31:53,
  watchdog ok; observer's only reason is the existing held summary. The
  earlier preflight showed no game pending and no active Boss. WA's next
  wild action is October 6 01:10:58, outside preparation time.
- No synthetic live quiz, notification or wild action was sent. Natural
  post-release quiz timeout and wild summary samples remain to be observed.
  The auto-updated quiz bank is the only uncommitted production file.

## Natural External Timeout Acceptance

On October 6 at 05:00:13, external player @TachibanaKaoru timed out on the
same Qianlan Ice Flame question family. The local journal contains the
console-only timeout; the watcher remained unowned. There was no corresponding
TG delivery attempt or summary checkpoint row. The surrounding trial-start
receipt is timestamped 05:00:04, a different event. This validates the external
timeout suppression naturally, without sending a test question or changing
question-bank learning. Managed-identity and conflict notifications remain
separate paths; this sample does not certify all of them.
