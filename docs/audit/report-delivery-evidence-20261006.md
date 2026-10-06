# Independent Report Delivery Evidence

Base: `205b6ad2`. Worktree:
`/root/xiuxian-report-delivery-evidence-20261006`.
Merged and pushed as `b16f4114` to `xiuxian-mian/main`; production-checkout
isolated verification: 277 passed. No service was restarted.

## Finding

The manual MiniApp daily report and storage-bag report accepted ordinary
HTTP responses without checking the Telegram success envelope, destination
or message ID. Storage chunks could continue after an unproven response.
HTTP/network failures could include authenticated URLs in exception output.
These are offline reproductions, not a claim of observed production duplicate
sends, false confirmation or credential exposure.

## Repair

The watchdog's recently tested bounded receipt logic now lives in the
stdlib-only `tools/bot_delivery.py`, shared by all three tools. The helper
calls its opener exactly once, closes the response, reads at most 64 KiB plus
one byte, rejects malformed or duplicate-key JSON, and distinguishes a
matching message receipt, an explicit 4xx rejection and an unknown outcome.
It neither constructs requests nor retries them. Raw responses and exception
text are not returned to callers.

Reports require confirmation before returning success. Storage chunks also
verify an explicitly requested forum topic, print confirmed chunk/message
IDs, and stop with a confirmed-prefix count on an unconfirmed/unknown chunk.
This is diagnostic evidence, not a durable resume cursor; rerunning a manual
report can still duplicate earlier chunks. No automatic rerun or fallback
was introduced. Name-only target groups cannot be verified by numeric chat
ID and remain unknown, as in the watchdog validator.

Requests, plain-text/HTML mode, environment precedence, 20-second report
timeouts and 8-second watchdog timeout remain unchanged. Reports still read
offline by default and send only with `--send-log-group`. Watchdog warning,
pause, fuse, throttling and action policies are unchanged. Its diagnostic
strings remain compatible. All tools now need their sibling helper file;
the installed service already executes from the repository checkout.

## Verification

- Original implementation: 50 new report regressions fail.
- Repaired report/watchdog/formatting tests: 109 passed.
- Second maintainer cross-module review: 424 passed / 30 subtests, including
  watchdog policy, evidence guards, health, notification acceptance and
  existing daily reports. This is not an independent external audit.
- Both direct scripts and watchdog module execution work with `python -S`.
  Ruff, compileall and diff checks pass. The new watchdog dry-run against
  production evidence is healthy and does not send anything.
- Frozen full regression: 16485 passed / 1461 subtests in 470.22 seconds;
  JUnit `/tmp/xiuxian-report-delivery-evidence-20261006.xml`. Tests used
  `XIUXIAN_ALLOW_LIVE_TEST_DB=0`. The same production capture files produce
  identical offline daily-report output before and after this patch.

The initial repaired run exposed a test-only false positive: traceback
formatting includes source lines, and a literal test message appeared in a
fixture lambda. The fixture now passes a named constant so the assertion
actually tests exception/body leakage. No production sanitization was relaxed.

## Remaining Boundaries

This repair does not implement independent sender metrics, durable notification
delivery, capture/round reward deduplication or trial child-to-parent reward
handoff. It does not touch the main runtime's notification transport, change
gameplay, emit a real test notification or restart any service. The two held
summary batches remain untouched. Natural sends from these independent tools
have not been used for acceptance.

At 18:05:19 the existing runtime summary confirmed delivery of 652 UTF-16
units / 19 lines / zero mention links, receipt
`75b60bf464d24303bd6f424e4ad2f2e6`. The subsequently queued retreat records
start at 18:05:25, not before that flush; held batches remain two. This
validates the existing runtime sender, not the changed independent tools.
Main worker 2207227 still loads `f7958deb`; observer and watchdog processes
remain 2322377 and 2178662. No restart is part of this delivery.
