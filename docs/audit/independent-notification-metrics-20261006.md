# Independent Notification Metrics

Base: `d24ab584`. Lab:
`/root/xiuxian-independent-notification-metrics-20261006`.

## Scope

The previous runtime receipt stream excludes the independent watchdog and
the two explicitly invoked report CLIs. Their success rate cannot be inferred
from runtime receipts or from a quiet watchdog journal.

The existing stdlib-only `tools/bot_delivery.py` now emits one metadata record
after each normal transport outcome for these three callers. It retains their
validated `confirmed`, `unconfirmed` and `unknown` distinctions. There is no
new request, retry, fallback, background task or storage mutation. Missing
configuration and default offline report execution do not count as sends.
Storage report chunks still stop at the first unconfirmed or unknown result.

Schema 2 records contain a random per-attempt ID, clock, source, destination
group/topic, outcome, transport duration, payload digest and raw size. They do
not contain text, URL, token, exception message or Telegram response body.
Records go to stderr so ordinary report stdout is unchanged. The existing
CLI intentionally prints its report body; this patch does not claim to redact
that pre-existing user-facing output.

Metrics failures cannot change the validated transport result. A process
termination, unavailable clock or broken stderr may leave no record. This is
best-effort local telemetry, not a durable outbox, replay log or exactly-once
delivery guarantee. Manual callers must retain stderr to include their records
in a later report. No historical records are invented from old success strings.

## Report Semantics

- Schema 1 remains the runtime's existing visible-text/mention measurement.
  Schema 2 measures raw payload size, including any HTML markup. Separate
  counters and percentiles prevent mixing the two. Missing samples stay null
  in percentile output; runtime-only mention totals are labeled as such.
- Totals count supplied, valid transport records; `by_source` separates the
  runtime, watchdog and two manual report tools. This is not proof that all
  senders or all retained periods are covered.
- Identical record IDs are idempotent. Contradictory records sharing an ID are
  excluded from outcome totals and counted in `excluded_conflicting_receipts`;
  the report does not choose the first success over a later unknown result.
- Payload repetition uses the destination group, known topic scope and digest.
  Legacy runtime records lack topic scope; no topic is inferred. Identical
  payloads are not business event identities or authorization to resend.
- Only known fields and tool sources survive parsing. Malformed or excessively
  nested JSON is ignored; arbitrary input fields cannot inject a source name
  or corrupt grouping keys.

The main runtime delivery module, notification policy, urgency, warning/fuse
thresholds, queue/held state and all game controls are unchanged.

## Verification

The original implementation fails 13 new tests. Initial targeted validation:
308 passed, including all three senders, ambiguous/429 responses, partial
chunks, unavailable clocks/output, source isolation, HTML measurement and
conflicting records. Ruff, compileall and diff checks pass. All four CLIs
support `python -S ... --help`; they do not require runtime imports.

The second maintainer review (not an external independent audit) added strict
JSON duplicate-field rejection and string-only record/digest IDs. It reuses
the bounded Bot API helper's JSON-object validation rather than duplicating
it. Supplementary regressions: 184 passed. Final tool suite: 312 passed;
cross-module notification/store/health validation: 282 passed / 11 subtests.

Initial full regression: 16564 passed / 1461 subtests. It predates that last
reader correction. Final frozen full regression: 16568 passed / 1461 subtests
in 452.54 seconds. JUnit:
`/tmp/xiuxian-independent-notification-metrics-final-20261006.xml`.
All tests use `XIUXIAN_ALLOW_LIVE_TEST_DB=0` and mocked network. Ruff,
compileall and diff checks remain green. No real notification was sent for
verification, and no service was restarted.

Read-only replay of October 6 17:00-19:05 journals yields 5 confirmed runtime
records, 0 conflicting records, 2 explicit mention links, and visible length
P50/P95/P99 318/652/652. There are no schema 2 live samples yet. This does not
claim that independent senders made zero requests or had 100% success.

The correct ordinary daily-report capture directory is
`data/state/miniapp_capture`. Read-only output remains 72 trial settlements /
1032 Tianji remnants and 15 treasure runs. This does not prove durable reward
handoff or deduplication across cumulative recovery records.

## Outstanding Acceptance

The watchdog is still resident on its old code. Natural schema 2 records must
be observed after a normal, separately scheduled service reload. The new
helper must be distributed with the scripts; copying only an entry script is
not sufficient. The report reader also needs this stdlib helper beside it.

Trial child-to-parent reward handoff, urgent/legacy unknown fallback, two
historical held batches, independent senders outside these three callers and
rendered watchdog mention/length measurement remain separate work. They are
not closed by this instrumentation.
