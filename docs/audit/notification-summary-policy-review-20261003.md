# Routine Summary Delivery: Audit And Second Review

Base: `13d68525`.
Lab: `/root/xiuxian-summary-delivery-policy-20261003`.
Implementation, initial audit and second review are separate passes by the
same maintainer, not an independent external review.

## Scope

The structured routine-summary caller opts out of account fallback after an
unknown Bot result. A timeout, exception, malformed response, mismatched status
or 5xx cannot prove non-delivery. Its durable store retains the batch as held;
restart and later flushes never automatically replay it.

Explicit local rejection or matching Bot JSON 4xx retains the existing fallback
and backoff policy. Account mode and pre-existing Bot backoff still permit one
account attempt, since that call made no Bot attempt. Cancellation propagates.
Classifier failure fails closed. Receipt metrics do not control this policy.

The default urgent/legacy path and secondary notification channel are unchanged.
This does not close TG-06 for all notification transports. No game command,
cooldown, identity setting, unknown game recovery or resource policy changes.

## Initial Audit And Acceptance

Reviewed actual thread timeout behavior, explicit rejection classification,
metrics-off behavior, cancellation, store checkpoint/restart interaction and
the sole opt-in call site in `flush_low_priority_audit_summary()`.

- Focused: **148 passed, 17 subtests**.
- Full isolated suite: **15787 passed, 1430 subtests**, 453.52 seconds.
  JUnit: `/tmp/xiuxian-summary-delivery-full-20261003.xml`.
- Ruff, compilation and `git diff --check` passed.
- All tests used `XIUXIAN_ALLOW_LIVE_TEST_DB=0`. Unknown sends were injected
  into mocks, not production. No game request or test TG message was sent.
- A test-only invalid nested `with` statement was corrected before the frozen
  full run. No corresponding production defect or code change was involved.

## Second Review

Re-read delivery evidence classification, runtime call sites, exception paths,
thread completion after timeout, durable held lifecycle and urgent defaults.
No additional blocker found for this limited routine-summary policy.

Regression repeated after full acceptance: **210 passed, 263 subtests**,
including notification acceptance, summary store/health, send timeout,
shutdown, red-packet and heavenly-ban tests.

Frozen source hashes:

```text
b48b81ddebbfd2b6e50d5e3ca4ac15b8131a1b4d932af2277385a5a06768d5f9  model/runtime.py
2dac16b81049c85a7ff73fe22ae9033ade598fdccffcd21ae54f8c2916981d1a  model/audit_delivery.py
26f814ba804ac8bfb4308790fe4d734ede935e649ba145fe302af1e126ffcb94  tests/test_summary_delivery_policy.py
```

## Rollout Plan And Acceptance Limits

Limited rollout may enable `LOG_GROUP_STRUCTURED_SUMMARY=1` alongside existing
delivery metrics and observer `--notification-summary-required`. The only newly
regrouped normal-priority sources are explicitly confirmed deep-retreat and
YuanYing results. Existing low-priority records also use the durable bucket.
Urgent/human-action and interactive notifications retain their existing route.
The structured summary interval is at least 1800 seconds.

Check dotenv precedence, game pending state and upcoming Tianxing actions before
one controlled main restart. Do not enable the inactive listener. Preserve
unrelated quiz/backup changes and all account settings. Record actual rollout
evidence separately; this plan alone is not proof of enabling or delivery.

The old low-priority bucket is memory-only. A database backup cannot preserve or
prove that bucket was drained. Retain local journal evidence; do not re-send old
notifications speculatively or claim a lossless handoff without direct evidence.

Rollback: disable structured summaries and the observer's required flag together,
then restart the affected services. Retain `audit_summary.db` and held evidence;
never clear the checkpoint or replay held batches. Game state is not rolled back.

Production acceptance still needs real summary receipts, at least 24 hours of
observation and a complete natural daily result. Unknown delivery has no natural
sample yet. Further module grouping, default urgent fallback, operator resolution
of held batches, gift handoff, backup delivery and MiniApp debts remain open.
