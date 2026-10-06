# Overnight Checkpoint

Observations through October 7, 2026, 04:17 CST. This is a checkpoint, not a
claim that staged runtime fixes have been loaded or naturally accepted.

## Code And Services

`536bbdc9` was fast-forwarded to production `main` and pushed to
`xiuxian-mian/main`. It fixes independent small-world prayer deadlines; final
full regression passed 16603 tests / 1478 subtests, broadened review 557 / 24,
and production-directory isolated tests 356 / 19. See its dedicated audit.

Supervisor 2207210, worker 2207227, observer 2322377 and watchdog 2178662 remain
unchanged and active, service NRestarts=0. Worker generation remains `f7958deb`.
Only production dirt is runtime `data/quiz/quiz_bank.json`. No restart, live
state correction, manual game request or test notification was performed.

Foreground follower **86357** remains active, bounded until about 02:49.
The agent kept consuming its output across midnight. No independent listener
was enabled; main listeners continue to cover the two groups.

## Harvests

- Baji checked at 00:22:22 and performed one collect at 00:22:25. The three
  captures (initial identity, details, collect) all succeeded. Official action
  text confirms **6000 incense collected, balance 242283**. Runtime advanced
  last harvest to 00:22:25 and next harvest to 08:22:25.
- WA performed one collect at 00:39:47, after a respected entry-rate backoff.
  Text confirms **8592 incense collected, balance 197541**. Last/next local
  harvest clocks are 00:39:46 / 08:39:46.
- Both remain idle with refining **off**, harvest **on**. Prayer checks remain
  Baji 03:26:49 and WA 02:27:38. No manual calibration or extra collect occurred.

The old worker's stored balances remain 236283 / 180908. Do not call this full
state acceptance: `40f81876` already fixes partial harvest balance application
and summary routing on disk, but is not loaded. Do not derive the actual harvest
amount by subtracting those stale balances, or compensate their residuals.

Old-worker harvest notifications were individually delivered, not new summary
acceptance: Baji `8a7ee6e9df5147a6adb788d04527ea43` (89 UTF-16 / 3 lines),
WA `8461ef7925c84b698d2f19e4ca46b251` (92 / 3), neither with a mention.

## Rate Limit And Recovery

At 00:34:40 xuruode1 / 3888882303 loaded identity and details successfully,
then `external:fate_cards` returned HTTP 429 / `external_action_rate_limited`.
The public-entry background scheduler retained a five-minute shared hold until
00:39:40. This was not proof of an expired dwelling URL. No hold was cleared.

WA harvested after that deadline, and xuruode1 resumed fate cards successfully
at 00:40:39. The latter has accepted its fate and awaits retreat completion;
it has not yet claimed its reward. At 00:43 the captured MiniApp peak is 29/90
over 476 requests, with one transient error. A per-endpoint 429 can still occur
below that observed aggregate count; Retry-After/backoff remains authoritative.

## Notifications And Routine Work

- The 00:30:51 ordinary summary confirmed as 996 UTF-16 units / 23 lines /
  no mention, receipt `abfea319fc42456591ba20fb15f7de7a`.
- Midnight missing-rod/companion cases are per-identity daily skips, not a
  circuit break. Lpprceqei and xueuode5 subsequently produced actual fish.
  WA/Baji are waiting for their companions' return, not marked complete.
- Baji fate-card completion at 00:13:33 reports cultivation +62 and remnants +3.
  The legacy daily-report tool's empty result is not evidence of no rewards
  across all native/outer-court games.
- The external `@socom0244` timeout at 00:35:03 stayed in local logs; a later
  ordinary-queue check found zero matching rows. No new independent delivery
  receipt for it was observed.
- Ordinary wild results are queued rather than individually sent. At 00:45
  there are still 17 per-run rows. User-requested result aggregation is not
  complete merely because priority was lowered. Keep this as a separate debt,
  with authoritative per-run ownership before summing gains.

Fishing skip display grouping is being validated in a separate Lab. Historical
untyped rows are not rewritten. Two historical held batches remain untouched.

## Attempt And Next Windows

The scheduled shadow checkpoint ran at 00:16:14-26. It reports 28193 attempts,
27525 anchored evidence labels, zero non-strong written labels, and 4045/4045
legacy message-ID presence matches. Lack of stored chat scope means parity is
still partial; it is not a precision audit or Gate 4 approval.

The one 24-hour error is the existing October 6 04:27:13 WA `.天机代卜`
`send_unknown` / caller cancelled after RPC started, not a new night error.
Open ledger and historical unknowns are not deleted, replayed or controlled.

Next high-risk watch: WA wild training at 01:13:49, preparation around 01:03.
Its rift is 07:06:07; deep retreat does not block Tianxing. Backup at 04:29:57
may load staged code and requires explicit post-load validation. Baji voyage
check is 03:57:45, WA voyage check 05:13:18. Renew follower before 02:49.

## Follow-Up Through 01:58

`72d76453` fishing-skip grouping and `8ba02c80` wild-outcome grouping are now
merged and pushed to `xiuxian-mian/main`. Production-directory isolated checks
passed 245 and 331 respectively. Wild final full regression: 16663 passed /
1478 subtests; see its separate audit for independent review and limitations.
Supervisor/worker/observer/watchdog PIDs remain unchanged, NRestarts=0; there
was no restart or state correction. Worker `f7958deb` still has the old renderer.
Follower 86357 is active. No test or push session remains running.

WA naturally finished all eight deep wild actions between 01:11:21 and
01:51:05. Server state explicitly shows 8/8, zero remaining, unavailable, and
the next local run is October 8 00:39:55. Eight distinct prediction sends each
have exact reply anchors in group -1002083016447:
`1288684,1288836,1288847,1288911,1288929,1288946,1288954,1288997`.
Change-fate sends were `1288700,1288839,1288948`, not once per successful run;
unconsumed protection was reused and both escape outcomes were followed by
new preparation. After the last action prediction is empty, remaining change
is Exploration, and timeline `blocked_replan` awaits the next consumption window.
No rift is due until 07:06:07. Deep retreat did not block Tianxing.

Per-run result logs confirm cultivation **+270000**, no cultivation losses,
fourth-tier demon cores x2, third-tier cores x2, Yin Ning crystals x2,
Tian Feng feathers x3, and soul-nurturing wood x1. Two runs were change-fate
escapes with no cultivation loss. These are result deltas, not an account
balance or a claim about net Tianji after preparation costs.

Old-format routine summaries confirmed:
- 01:01:53 `b49d09b220fa44c38c9efb733a56fa82`: 1747 UTF-16 / 23 lines / no mention.
- 01:32:02 `e3a66a46a08d437cbeecd9c9b810635f`: 1829 / 22 / no mention.

At 01:58 there are 68 queued records, next summary about 02:02:22, and the same
two historical held batches. Ordinary stargazer successes still use individual
notices; legacy routing is not fully closed. No notifications were replayed.
The 01:00 trial batch initially ended 11/12 successful because dingfengbosushi's
public entry was busy with wild training. Retrospective verification at 02:03
found the natural retry: one step restarted at 01:42:39, settled three games
at 01:43:34 and marked wave1 completed. The original eleven successful steps
did not repeat. No manual trial was started.

Health checks at 01:33 and 01:54 report only those held-notification warnings;
watchdog is healthy. Both small-world identities remain idle, refining off and
harvest on, with checks still WA 02:27:38 / Baji 03:26:49. Their harvest balances
remain subject to the unloaded partial-receipt fix described above. Natural
deep-retreat settlements/restarts and several fate-card rewards continue.
Observe the next summary, trial retry, and prayer window before extending scope.

## Follow-Up Through 02:22

The 02:02:22 ordinary summary confirmed as 1273 UTF-16 units / 23 lines /
no explicit mention, receipt `87ee46fd0a964f18975536e538b8c72f`.
External `@ilinuxio` timeout at 02:05:29 stayed local; subsequent queue query
found zero matching rows. Ordinary stargazer notices still deliver individually.
Through about 02:14, current-day captures contain 1782 HTTP records, rolling
60-second peak 44, one error (the earlier recovered 429). This is observed
capture coverage, not a claim to see every external request.

At 02:01:08 mudamuda0's fate-card read reported progress 4 -> 0, target 30.
Identity load, selected-role load, details, external entry and fate start all
returned HTTP 200; no draw/interpret/choose/settle followed. Previous progress
4 remains stored. Same-day/record/choice/quest checks passed before rejection;
the native contract requires an explicit nonnegative integer progress, so this
is not a missing-field default. Wild training finished at 00:39:32, before the
00:42 task, and no later matching game message establishes another loss.
Neither net cultivation nor upstream staleness is proven as the cause.

By 02:22 there are 22/24 settled fate-card records. Boxboxji settled at 02:21:57.
The two remaining records are mudamuda0 and WA. WA's stored 00:50 progress is
24/30; its subsequent eight wild runs earned cultivation, but the fate waiting
helper uses deep-retreat time (capped at 12 hours), and the background retry map
does not distinguish a business wait from server Retry-After. No gain event
invalidates that business wait. This confirms the existing waiting-strategy
debt; do not clear the shared map, invent progress, or prematurely claim the
task. A later fix needs separate authority for business and transport waiting.

Services and pending queues remain stable. Next direct observation is WA's
02:27:38 small-world check. Staged notification/prayer fixes are still unloaded.

## WA Prayer Check At 02:27

The normal scheduler sent `.小世界` once at 02:27:42, message 1289167 in
-1002083016447. Official hantianzun33_bot replied as 1289168 at 02:27:44 with
population 420000, faith 97/100, stability 84/100, incense stock 197541, and
pending incense 1912.93. It explicitly says no prayer and another 5h59m59s wait.
The reducer returned to idle with query/manifest anchors zero and no error;
next check is 08:37:12. No manifest/god/refine action followed. Latest god
timestamp remains the earlier 23:25 sermon. Refining is off, harvesting on.

This full panel also naturally corrected the old worker's stale incense balance
to the authoritative 197541. It does not certify the unloaded partial-harvest
patch or prayer-deadline fix, and no local balance/timer correction was made.
Pending game tasks are zero. Baji's next prayer check remains 03:26:49, and its
voyage check 03:57:45. Renew follower before 02:49; backup at 04:29:57 still
requires loaded-generation and post-maintenance verification.

## Follow-Up Through 03:28

Two additional scoped changes are merged and pushed to `xiuxian-mian/main`:

- `449f3efc`: bound normal fate quest rechecks to the existing 30-minute wait
  or earlier retreat completion, retaining actual server Retry-After on failed
  `/start` reads. Full regression 16681 / 1478 subtests; second review 975 / 31;
  production-directory isolated recheck 309 / 5. No wait-map clearing or live
  progress correction. See its dedicated audit for the remaining durable-wait
  and upstream-progress issues.
- `8810b336`: send ordinary stargazer successes through the existing summary;
  changed abnormal/backoff/unknown outcomes retain their prior route. No game
  behavior or storage format change. Full regression 16684 / 1484 subtests;
  second review 392 / 17; production-directory isolated recheck 182 / 17.

All test/push sessions have completed. No service was restarted. Supervisor
2207210, worker 2207227, observer 2322377 and watchdog 2178662 remain unchanged,
active, NRestarts=0. Worker generation is still `f7958deb`; production dirt is
only runtime quiz learning. Follower 86357 expired normally; replacement
**52586** is active, bounded until approximately **04:47**. Observe the scheduled
04:29:57 backup and renewal before that follower expires.

At 03:01:35 mudamuda0 again reported progress 4 -> 0. Original 4 is preserved,
with no draw/choose/settle. At 03:11 fate remains 22/24 settled; WA remains at
the old 24/30 snapshot. No assumption was made that local cultivation gains
authorize claiming without a new server read. The new waiting patch is not
yet loaded.

Official Mulan evidence, all in -1002083016447:
- Yinluo command 1289253 -> ack/final edit 1289254, 02:59:21 -> 02:59:27,
  hantianzun31_bot: cultivation +217, stones +158, hundred-year ironwood x2.
- Baji command 1289265 -> ack/final edit 1289266, 03:00:59 -> 03:01:07,
  hantianzun23_bot: cultivation +409, stones +195, gold concentrate x2.
Both support steps occurred once and reached their final business state.

WA's 03:20:20 command 1289429 was artifact petting, not equipment replacement
or a duel. Official 1289431 replied at 03:20:22 with rapport +4, experience +16;
next pet time is 05:20:27 and pending queue is empty.

The next ordinary summary was correctly scheduled from a new idle batch at
02:51:16, not assumed to run at a fixed half-hour boundary. At 03:21:17 receipt
`4c20f0106ea741c0b7634795470fd7a7` confirmed, 361 UTF-16 units / eight lines /
no explicit mention. Pending summary rows are zero, the same two historical
held batches remain untouched. This is old-renderer evidence, not acceptance
of newly staged wild/fishing/stargazer formatting.

Across October 7 files in `data/state/miniapp_capture`, at 03:22 there were
1855 captured HTTP records, 1854 HTTP 200 and the one earlier recovered 429.
Observed rolling-60-second peak is 44. This is capture coverage, not proof
that every possible external request is instrumented.

## Baji Prayer Check At 03:27

The scheduler sent `.小世界` once at 03:26:59, message 1289448 in
-1002083016447. Official hantianzun35_bot replied as 1289450 at 03:27:00:
population/capacity 320000, faith 98/100, stability 100/100, incense stock
242283, pending incense 2319.50. No prayer; another 5h59m59s wait.

Reducer returned to idle with query/manifest anchors zero, no pending god
action or error, next check 09:35:50. No manifest, miracle or refinement
followed. Both identities still have refining off and harvesting on. Baji's
next harvest remains 08:22:25; WA's remains 08:39:46. This full panel corrected
Baji's previously stale balance naturally; it does not validate the unloaded
partial-harvest fix. No balance difference was treated as an extra harvest.

Watch next: Baji voyage at 03:57:45, planned maintenance at 04:29:57, WA voyage
05:13:18, and Tianxing preparation before rift 07:06:07. Current preflight is
watch (outside preparation window), pending queue empty, watchdog healthy.
Deep retreat does not block Tianxing. These are checkpoints, not completion of
the monitor-repair loop or a claim that all debt is closed.

## Follow-Up Through 04:17

`c3bd8e64` corrects the staged stargazer classifier: only `wait` with a valid
farm snapshot and no error/unknown/backoff is routine. The planner can return
`inspect` for `reason=unknown`, so that status must not be newly downgraded.
The real planner regression failed before the correction and passed after it.
Final full regression: 16685 passed / 1484 subtests; cross-review 268 / 17;
production-directory isolated recheck 123 / 17. Push to `xiuxian-mian/main`
confirmed. These changes remain unloaded, not naturally accepted.

Baji's voyage returned through MiniApp at 03:57:47 (stored settlement
03:57:46): cultivation +364, stones +80, soul-nurturing wood x1, affection +4.
The expected fishing reservation was not a voyage failure. Five catches then
completed at 03:59:46, 04:01:48, 04:04:12, 04:06:17 and 04:09:41: silver fish
x3 and green-scale fish x2. Supply confirmations did not count as casts.
The read-only fishing report verifies 5/5, accounted cast/supply operations,
no error, and next fishing October 8 00:00:04. Voyage restarted at 04:09:44,
with return time 10:09:48. No manual action or sixth cast was observed.

At 04:16 fishing selection covers 23 identities: three with recorded catches,
13 skipped for no companion, six for no rod, and WA still awaiting voyage
return at 05:05:01. No identity-level report warning. Old saved slots and
future timers alone are not execution evidence.

The 04:10:59 summary receipt `369666e87ed74253bd9404009d1b117a` is confirmed,
477 UTF-16 units / 11 lines / no explicit mention. Queue is empty; the same
two historical held batches remain untouched. The external @axial5511 timeout
at 03:35:48 stayed local. Old-worker receipts do not certify new formatting.

The read-only trial inventory validates 24 current operations and 72 receipts,
no pending request/save, no cross-operation round-key conflict. Current slots
are 28505 encoded bytes; one old unknown archive remains. This mixes current
slots from different batches, not a claim that all 24 ran today. Wave1 is
completed for October 7; wave2 still records October 6. Parent ownership and
durable reward handoff remain unverified. Source review still finds ordinary
terminal/pause checkpoints ignoring save acknowledgement; no live persistence
failure was established and that implementation debt is not closed.

At 04:11 health/watchdog checks show no new anomaly, only the two held batches.
Supervisor 2207210, worker 2207227, observer 2322377 and watchdog 2178662 remain
unchanged; worker generation is still `f7958deb`. Pre-maintenance read-only
switch snapshot preserves WA/Baji refining off, harvest on, both voyages on,
global enabled, the existing 19 frozen channel identities and public MiniApp
selections. Do not infer public MiniApp inactivity from channel enabled=0.

Follower 52586 remains active until approximately 04:47. Scheduled backup at
04:29:57 is the next loading checkpoint; observe its actual stop/start and
post-load behavior, without an extra manual restart or game probe. WA fate
24/30 and mudamuda0 4->0 remain unresolved; the latter repeated at 03:32:07
and 04:02:16 without draw/choose/settle. Do not overwrite either snapshot or
infer completion from cultivation gains. WA voyage check 05:13:18 and Tianxing
preparation before rift 07:06:07 remain the next business watches.
