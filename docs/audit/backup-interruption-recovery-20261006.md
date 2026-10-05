# Scheduled Backup Interruption

## Evidence

At 04:27:09 the systemd R2 backup began. Its recovery journal stopped observer,
watchdog and main service at 04:27:11. Main exited successfully at 04:27:21;
this was not a crash or an agent-requested restart. Main restarted 04:28:32,
bootstrap completed 04:28:51, and backup completed successfully 04:29:36.
No agent changed the backup schedule, service flags or backup files.

The stop interrupted WA's fourth fishing fight before submission. A single
post-start native state read recovered the original expired rod and accounted
its empty result plus waterweed. Five casts total, not six; fifth rod and
combined daily report subsequently completed. Detailed timing and rewards are
in `fishing-bite-window-20261006.md`.

The companion chain also sent `.天机代卜` root 1283332 in -1002083016447 at
04:27:14. Official bot 8816935632 / hantianzun21_bot replied 1283333 at
04:27:16, but no local reply event survived the stop. The durable divination
operation retained status sent and blocked another send. Its replay loop only
searched local logs, so more local replay alone could not recover this reply.

## Bounded Recovery

A one-shot memory-only, receive_updates=False Telegram connection used the
existing read-only probe convention. It verified account/command ownership
and scanned 61 exact message IDs around the known root. The response was a
strict direct official reply: divination succeeded, cost 180 cultivation,
effect 残图引路. No game command or MiniApp action was sent by the probe.

The exact root/reply pair was fetched again and validated against the current
unchanged durable operation and configured official-bot allowlist. Only this
real event was appended to the message evidence log with original server time,
current collection time and `recovery_source=bounded_telegram_history_probe`.
The standard game message-ledger key prevented a duplicate append. The probe
did not write game state, timers, balances, switches or a fabricated reply.

At 04:41:43 the running application's original log-replay path accepted the
reply and advanced business state. Divination is complete, the effect is
recorded and its due time is 16:27:51. Companion processing continued and Moon
Palace voyage began at 04:43:00. Pending queue was empty at 04:44; no second
divination was sent. This is verified one-shot recovery, not an implemented
automatic remote-history recovery mechanism.

## Remaining Work

- Backup calls systemctl stop. The supervisor already sends SIGUSR1 and drains
  persisted Telegram/legacy pending windows; it is not a wholly absent drain.
  `_wait_for_pending_drain()` accepts the first empty DB sample after a fixed
  flush grace, without a worker acknowledgement of in-flight dispatch state.
  `_active_pending_windows()` does not include native fishing sessions. This
  leaves the stop/late-persist race and timed-game continuation uncoordinated.
  Define bounded admission-stop acknowledgement and in-flight ownership before
  changing it. Merely extending a fixed sleep cannot prove the queue is drained.
  Do not abandon consistent snapshots, disable backups or wait indefinitely.
- Define bounded missing-reply history collection using existing live clients,
  strict root/account/chat/bot binding and original timestamps. Keep it outside
  the send queue. Never turn a missing result into permission to resend.
- The observer still displays the historical cancelled fight for its capture
  window. Do not suppress generic cancellation merely because a later action
  succeeded; any resolved classification needs same-operation terminal evidence.
- The new normal worker start also loads capture-summary fix `a6eb55a2`.
  It was not restarted solely for reporting. No browser/API UI check was made.

Current main supervisor/worker: 2178651/2178653; observer 2178717; watchdog
2178662. All active with NRestarts=0 after scheduled startup. Production dirty
quiz learning data is preserved. Historical held notification remains untouched.
