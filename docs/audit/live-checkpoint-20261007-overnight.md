# Overnight Checkpoint

Observations through October 7, 2026, 00:45 CST. This is a checkpoint, not a
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
