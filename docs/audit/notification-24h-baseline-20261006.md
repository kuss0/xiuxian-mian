# Runtime Notification Baseline

Window: October 5 07:00 through October 6 07:00, 2026, UTC+8.
The final capture was repeated after the end of that window. Inventory uses
production disk revision `9488f9e8`; different worker revisions ran during
the window. This is not a controlled before/after performance comparison.

Reproduction:

```bash
journalctl -u xiuxian.service --since '2026-10-05 07:00:00' --until '2026-10-06 07:00:00' --no-pager -o json | /opt/xiuxian-main/.venv/bin/python tools/notification_report.py --journal - --output /tmp/xiuxian-notification-24h-20261006.json
```

## Measured Results

- 77 measured transport attempts: 76 confirmed, one unknown.
- Nine explicit mention links in confirmed messages, not necessarily nine
  distinct incidents.
- Visible UTF-16 units: P50 101, P95 1220, P99 1786.
- Confirmed transport latency: P50 658ms, P95 1746ms, P99 5869ms.
- Peak confirmed volume: 13 messages in the October 5 13:00 hour; the
  October 6 00:00, 01:00 and 02:00 hours each had six.
- Zero repeated identical confirmed payloads. This does not mean zero
  duplicate business events: the October 6 rift generated an urgent result
  and a differently titled digest entry for the same materials.
- Static inventory: 495 audit call sites, zero AST parse errors. Largest
  files: UI 38, second soul 34, quiz 30, Taiyi 28, runtime 23, duel 22,
  Tiandao judgement 22 and storage bag 21. Static counts are not send volume.

## Limits And Remaining Work

Coverage is the runtime log group and secondary-channel transport receipts.
Independent watchdog/report senders are excluded. Unknown may have reached
Telegram; 77 is not a count of distinct business events or all visible group
messages. The retained unknown batch is not cleared or replayed.

This supplies a complete bounded runtime receipt window for TG-01, but does
not close its independent-sender coverage or full event taxonomy. Neither
TG-02 through TG-06 nor all notification debt is closed by these metrics.

Priority follow-ups remain trial child/batch reward duplication, daily-report
durable delivery outcomes, and urgent/legacy unknown fallback. Trial progress
and startup copy were handled separately. Before suppressing trial children,
address `_persist_trial_daily_batch_state(completed=True)` clearing outcomes
before final notification and failed-step retry replacing the old aggregate;
otherwise a restart or failure can lose the only remaining reward report.
The current child messages are intentionally retained until that handoff is
durable and tested.

## Independent Sender Review

Read-only source and installed-schedule review at approximately 07:37 CST:

| Entry | Trigger and scope | Receipt coverage and next boundary |
| --- | --- | --- |
| `model/runtime.py` log-group transport | Automatic audit, summaries and secondary channel | Covered by the measured receipt format; ordinary summary unknown handling differs from urgent/legacy defaults. |
| `model/app_message_log.py::_send_replica_group_message` | Replica-group notifications, including interactive buttons/replies | Own bot/account path and sent-message log, not the runtime receipt stream. Bot timeout can fall through to account; assess separately under TG-06, not as evidence of a live duplicate here. |
| `tools/safety_watchdog.py::send_log_via_bot` | Safety fuse/warning, independent of main runtime | Preserve immediate warnings. Same warning category is limited to once per 300 seconds in process memory; fuse marker protects repeated fuse actions. No measured-format receipts. The reviewed 24h service journal has no matching warning/fuse/send-result entries; this is not transport confirmation. |
| `tools/miniapp_daily_report.py::send_log_group` | Explicit CLI `--send-log-group`; default prints only | Own Bot API request, not measured-format receipts. This session used read-only output only. |
| `tools/storage_bag_report.py::send_log_group_chunks` | Explicit CLI `--send-log-group`; default prints only | Own sequential chunk sends, not measured-format receipts; manual reports remain outside automatic summary grouping. |

The AST inventory's `tools/notification_report.py` candidate is its own
`sendMessage` search string, not an additional sender. Literal candidates
are not a complete inventory of Telegram SDK calls or external programs.
The installed systemd timer list and references under `/etc/systemd/system`,
`/etc/cron.d`, `/etc/crontab` and root's cron spool showed no scheduled use
of the two manual report CLIs. This does not exclude ad hoc invocation or
an external scheduler. No service, warning threshold, sender or fallback
was changed by this review.
