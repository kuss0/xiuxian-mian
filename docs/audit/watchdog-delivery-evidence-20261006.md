# Watchdog Delivery Evidence

Base: `ead26a9b`. Worktree:
`/root/xiuxian-watchdog-delivery-evidence-20261006`.
Merged and pushed as `a2eec0db` to `xiuxian-mian/main`; post-merge isolated
verification: 319 passed / 16 subtests. The running watchdog has not reloaded.

## Finding

The independent watchdog's `send_log_via_bot()` read only 256 response bytes
and returned `log bot ok` for every normally returned HTTP response. It did
not validate Telegram's success envelope or message receipt. Errors included
raw exception text, which may contain the authenticated Bot API URL. Success
diagnostics echoed the beginning of the raw response, including message text.

These are source-level findings, reproduced with isolated responses. No
production false acknowledgement or credential exposure was established.
A read-only watchdog journal search from October 1 through this checkpoint
found no matching `log bot failed:` or token-shaped Bot API URL lines; this
bounded negative search is not proof that no historical exposure occurred.

## Narrow Repair

- Read at most 64 KiB plus one byte. Oversized, malformed, incomplete or
  duplicate-key JSON is unknown, not success. Response streams close on both
  success and failure, including HTTP errors and interrupted reads.
- A success requires HTTP 200, literal `ok=true`, a positive integer message
  ID in range and an exact numeric destination-chat match. Only the message
  ID is printed, not raw response content.
- Explicit `ok=false` plus an integer 4xx error code matching the HTTP status
  is unconfirmed. Timeouts, 5xx, inconsistent envelopes and absent receipts
  stay unknown. No retry or fallback follows this classification.
- Exceptions print their class only. Telegram descriptions, URL credentials
  and notification text do not enter the new diagnostic strings.

The URL, request body, HTML parse mode, preview setting, 8-second network
timeout and one-call behavior are unchanged. So are warning throttling,
fuse markers, thresholds, pauses, stop actions and all game controls. Return
strings are printed by the two callers; neither caller uses them to decide
whether to retry or alter a fuse. The earlier warning-detail folding remains.

The watchdog remains stdlib-only. No Telethon/main-runtime imports, new
delivery queue, database writes or new transport-metrics schema are added.
This does not complete independent-sender telemetry or change the separate
manual report senders. Numeric `LOG_GROUP_ID` is required to confirm the
destination; a name-only target would be sent as before but reported unknown.

## Verification

Before repair, the new tests produce 32 failures and three passing missing-
configuration cases. Initial repaired watchdog tests: 139 passed. Further
review adds stream-read failures and message-ID bounds. Cross-module tests:
327 passed / 16 subtests, covering watchdog policy/evidence, observer and
runtime notification reporting/acceptance.

Frozen full regression: 16420 passed / 1461 subtests, 462.68 seconds;
JUnit `/tmp/xiuxian-watchdog-delivery-evidence-20261006.xml`. Separate
maintainer review across policy, stream cleanup, notification presentation
and health: 355 passed / 57 subtests. Both real callers only print the
diagnostic; no branch uses it to drive sending or game control.

Ruff, compileall, diff checks and `python -S tools/safety_watchdog.py --help`
pass. All tests use `XIUXIAN_ALLOW_LIVE_TEST_DB=0` and mock Telegram. No test
warning was sent and no service was restarted. The maintainer review is not
an independent external audit. Runtime load and natural delivery remain
unaccepted; disk merge and post-merge verification are complete.

## Live Boundary

The installed watchdog process is unchanged and has not loaded this patch.
At 17:09, watchdog is healthy; health observer reports only the two historical
held summary batches. Main worker 2207227 still loads `f7958deb`. The earlier
harvest repair is merged/pushed as `40f81876`, with documentation `ead26a9b`,
but is also not runtime-loaded. Preserve both distinctions in handoff.

WA's 16:57 voyage return, three heart choices and 16:59:26 settlement have
one-to-one command/edit evidence in chat -1002083016447. It started another
Moon Palace voyage at 16:59:33, expected return 22:59:37. No extra warning
was manufactured for this repair. Foreground observation session 21262
continues through approximately 17:34; renew it after expiry.

At 17:13:47 the normal runtime summary was confirmed: 318 UTF-16 units,
eight lines, no explicit mention links; receipt
`6cc4c82cdb7c43f186a47e517e8e4faf`. Its pending rows are now zero and held
batches remain two. This is a different sender, not acceptance of the
watchdog change. Lpprceqei's 17:11 check again deferred on no_baseline to
17:41:08 without a duel; the calibration repair remains staged, not loaded.
