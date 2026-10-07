# Callback Poll Observation

Base: `dd4005e3`. Lab: `/root/xiuxian-callback-poll-observation-20261007`.
Merged and pushed as `8bcc47bb`. Production-directory isolated checks pass
207 tests / 16 subtests. The new 09:35 read-only `--once` check correctly
reports no unresolved callback warning after recovery. No service restart or
runtime policy change; the resident observer remains on its prior generation.

## Incident

October 7, 09:11:32: log Bot `getUpdates` returned HTTP 429 with Retry-After 5.
Two HTTP 502 responses and three read timeouts followed, reaching six
consecutive failures at 09:14:53. Existing backoff grew from 6 seconds to
60 seconds, and polling recovered naturally at 09:16:08.

During the incident, the 09:15:05 routine summary still confirmed delivery:
receipt `260d18842cf64e168afe507733a920a9`, 208 UTF-16 units, six lines,
no mention. This is not evidence of a MiniApp rate-limit failure or a failed
send. No retry, alternate-account send, Bot API probe or restart was added.

The observer intentionally filters individual transient callback errors, but
also missed the sustained streak. Main monitoring caught it directly in the
journal; daemon health still only mentioned the two historical held summaries.

## Narrow Change

Read the existing bounded journal output for exact poller-generated failure
and recovery lines. Use the explicit failure counter, not the number of log
lines or observation passes. At least five consecutive failures without a
later recovery in the scanned window produces one warning per host/process.
Same-process recovery clears it; another process's recovery cannot do so.
Repeated rows, quoted player messages, missing counters and malformed zero
counters do not inflate or clear the evidence.

The warning is synthesized from counters and PID only. It does not copy raw
HTTP bodies, exception strings or credentials. Individual transient errors
remain non-hard. Other journal errors remain visible. Existing scan limits,
service-start boundary, severity scoring and report format are unchanged.
Consumers in `model/control.py` and `model/ui.py` display the health snapshot;
this change does not grant the observer any game or notification control.

Scope is recent observed streaks, not a poller heartbeat or proof of current
connectivity. A truncated window without qualifying events proves neither
health nor failure. The parser covers the current default journal format and
plain captured messages; changing journal output format requires revalidation.

## Verification

- Before repair: 10 new checks fail, 15 controls pass.
- Final focused selection: 207 passed / 16 subtests.
- Second maintainer review across notification persistence, receipt reporting,
  startup guards and acceptance: 330 passed / 16 subtests.
- Actual journal replay through 09:15:59 returns one six-failure warning;
  extending the same replay through 09:16:30 returns none.
- Full frozen suite: **16810 passed / 1486 subtests**, 455.56 seconds.
  XML: `/tmp/xiuxian-callback-poll-observation-full-20261007.xml`.
- Ruff, compilation and diff checks pass. Tests use
  `XIUXIAN_ALLOW_LIVE_TEST_DB=0`. Second review is a separate pass by the same
  maintainer, not an independent external audit.

After merge, normal `--once` monitoring can use the new code immediately.
The already-running observer stays on its old generation until planned
maintenance; do not claim it was hot-reloaded. No main/observer/watchdog
restart, production state mutation, notification or new daemon is required.

## Concurrent Checks

xuruode6 completed YuanYing at 08:56:09; next 16:56:11. Tower results:
boxboxji 09:01:10 (+6986 cultivation, +54 seals), imcanonical_ai 09:20:57
(-12358 cultivation, +57 seals), Yinluo 09:25:28 (+2821, +44) and iceeet1
09:26:31 (+3915, +44). Negative cultivation is a real game outcome, not an
execution failure. WA's 09:20 command 1291039 was artifact petting, not
equipment removal; reply 1291040 confirms rapport +4 and experience +18.

mudamuda0's progress still regressed 4 -> 0 at 09:26:58. Preserve that open
issue and watch the independent deep-retreat settlement around 09:54. Main
worker 2901475 and monitor PIDs remain unchanged. Foreground 56988 is active
until about 10:43. Captured HTTP count at 09:25 was 2186, peak 44/90, with only
the previously recovered transient MiniApp error.
