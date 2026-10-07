# Second Soul Return Rewards

Base: `b648f257`. Lab: `/root/xiuxian-second-soul-return-notice-20261007`.
Code and offline acceptance complete; not loaded by the production worker.

## Evidence And Scope

WA's October 7 return broadcast at 08:23:53, message 12642073 in
-1001680975844, is from official hantianzun16_bot. It reports cultivation
+77845 and second-soul experience +3544. The existing notification only says
that training is queued, dropping both gains. This is a presentation defect,
not a failed training or resource-accounting repair.

The matched return handler now appends the exact reward line's two bounded
integer values to its existing notice. Both ordinary return and high-corruption
return use the same presentation helper in `model/audit_messages.py`.
Missing, malformed, duplicate, conflicting and unsupported reward lines keep
the previous notice without inferred gains. The helper does not add current
balances, corruption values or experience into cultivation.

No changes to actor matching, trusted bot routing, deduplication, resource
state, persistence schema, cooldowns, training, purge thresholds, notification
priority, queue metadata, transport, held batches or recovery authority. The
helper runs only after the existing handler accepts and saves a return event.
Existing human-action warnings and purge context remain visible. This is not
complete notification regrouping or a durable daily reward ledger.

## Validation

- Before repair, both new ordinary/high-corruption reward assertions fail;
  existing behavior still recognizes the broadcast and deduplicates it.
- Initial focused checks: 288 tests / 7 subtests. Expanded notification store,
  health and lifecycle checks: 335 tests / 7 subtests.
- Real handler -> real runtime notification -> temporary SQLite -> fake
  transport verifies one durable record and one folded summary, with both
  resource amounts and no admin mention. No game command or immediate notice.
- A second maintainer review covers lifecycle, startup guards, small-world
  integration and log-group display: 597 tests / 67 subtests. Its first command
  named nonexistent `tests/test_runtime_guards.py` and collected no tests; the
  corrected command uses `tests/test_startup_recovery_guards.py`.
- Frozen full suite: **16784 passed / 1486 subtests**, 452.24 seconds.
  XML: `/tmp/xiuxian-second-soul-return-notice-full-20261007.xml`.
- Ruff, compilation and `git diff --check` pass. Tests run with
  `XIUXIAN_ALLOW_LIVE_TEST_DB=0`. Review is another pass by the same maintainer,
  not an independent external audit.

No production restart for a notification-only change. Do not replay today's
return to manufacture acceptance. Natural reward delivery remains pending
after a future controlled runtime load; today's return used the old text.

## Concurrent Live Acceptance

The new-group status request 1290627 got official response 1290628. The return
arrived in the other monitored group, and the runtime consumed it at 08:23:54.
It sent one training command 1290629 in the new group; official reply 1290630
confirmed the new 24-hour run. The scheduled check at 09:22 was superseded by
the real return, not an unexplained early re-send. This is evidence that the
existing dual-group passive path works, not a new control change.
