# Lab Rust/Py Absorb 2026-06-19

## Scope

Lab worktree: `/root/xiuxian-main-absorb-lab`

Branch: `lab/rust-py-absorb-20260619`, based on `xiuxian-mian/main` commit `5dc3883`.

This lab does not restart services, touch production data, or push to remotes.

## Absorbed

### Rust inbox discipline

Added a lab-only `model/message_box.py` fact layer:

- `MessageFact` normalizes Telegram events into immutable facts.
- `MessageBox.upsert()` is the single write path.
- Duplicate events are ignored by event type, chat id, message id, and text hash.
- Edited text for the same message id replaces the latest fact.
- A stale non-edit event cannot roll back a newer edit.
- `MessageBoxSnapshot` is stable and read-only.
- `scan_after_seq()` is the Rust-aligned cursor path.
- `scan_after_seq()` defaults to new messages only; edits require `include_edits=True`.

This absorbs the Rust-side lesson that msg-id cursors miss same-message edits. The old `scan_after(msg_id)` remains available only as a compatibility/read helper; future routing should use seq cursors.

Added lab-only shadow wiring in `model/app.py`:

- Telegram game-group `NewMessage` bot replies are recorded into the in-memory `MessageBox`.
- Telegram game-group `MessageEdited` bot replies are recorded with `event_type="edit"`.
- Suspected unregistered game-bot replies are recorded after local text/reply-context matching.
- Shadow recording failures are swallowed and do not block the existing routed-reply path.
- The shadow box is observable through a read-only snapshot helper for tests only.

This is still not a primary routing path. Existing module handlers continue to receive the same `event`, `text`, `reply_to`, and `reply_context` arguments as before.

Added lab-only shadow alignment helpers in `model/message_contract.py`:

- Compare MessageBox facts with passive-event-ledger records by `(identity_id, family, msg_id)`.
- Classify routeable shadow facts as `changed`, `unhandled`, `no_change`, `gap`, `observed`, or `missing`.
- Ignore unrouteable broadcasts for missing-count purposes.
- Keep edits included by default, while same-message edits collapse to one routeable key for alignment.
- Return read-only summaries only; no state writes and no sends.

Added lab-only report support in `tools/message_contract_report.py`:

- `--shadow-path` reads a MessageBox shadow JSON snapshot.
- The report compares that snapshot with the selected passive-event-ledger file.
- JSON output exposes `shadow_alignment`.
- Text output prints a read-only MessageBox shadow alignment section.

Added lab-only shadow snapshot export helpers:

- `model.message_box.build_message_box_snapshot_payload()` converts a MessageBox snapshot to JSON-safe facts.
- `model.message_box.write_message_box_snapshot_payload()` writes the payload atomically.
- `model.app.get_message_box_shadow_payload()` exports the running in-memory shadow box.
- `model.app.write_message_box_shadow_snapshot()` writes the running shadow box to a JSON file.

Added a lab-only admin log-group command:

- `.消息盒子shadow [limit]` exports the in-memory shadow snapshot to `STATE_DIR/message_box_shadow/latest.json`.
- The command replies with a read-only alignment summary against passive-event-ledger.
- It is admin-only, does not send game commands, does not query APIs, and does not mutate module state.
- `model.control` uses a registered provider so it does not import `model.app` directly.
- Provider/write/ledger failures are reported as read-only export errors instead of crashing log-group handling.

### wxjerry storage-bag timestamp fix

Changed storage-bag local item deltas so inferred local quantity changes no longer refresh `updated_at` / `updated_at_text`.

Reason: `updated_at` should mean a full storage-bag snapshot/API read time. Local transfer/reward deltas update item counts but should not make stale inventory look freshly read.

## Not Absorbed

- No production shadow-mode wiring.
- No module state machine migration to message-box primary routing.
- No persistent JSONL/SQLite message-box ledger.
- No UI button for shadow dump yet.
- No API-read policy changes.
- No wxjerry direct merge or cherry-pick.
- No wxjerry 灵树定脉 copy; current line already has its own tree implementation.
- No world Boss logic change; current line already implements the 90s/1s/70s/send-retry strategy.

## Verification

Focused verification:

```text
83 passed, 140 subtests passed in 0.77s
```

Command:

```text
env XIUXIAN_DATA_DIR=/root/xiuxian-main-absorb-lab-data/data \
  XIUXIAN_SESSION_DIR=/root/xiuxian-main-absorb-lab-data/session \
  XIUXIAN_STATE_DIR=/root/xiuxian-main-absorb-lab-data/state \
  XIUXIAN_MESSAGES_DIR=/root/xiuxian-main-absorb-lab-data/messages \
  XIUXIAN_DB_FILE=/root/xiuxian-main-absorb-lab-data/state/chaogu_state.db \
  XIUXIAN_LIVE_GUARD_DIR=/root/xiuxian-main-absorb-lab-data/live-guard \
  /opt/xiuxian-main/.venv/bin/python -m pytest -q \
  tests/test_message_box_shadow.py \
  tests/test_message_contract.py \
  tests/test_log_group_display.py
```

Focused app/module verification:

```text
183 passed, 140 subtests passed in 1.45s
```

Command:

```text
env XIUXIAN_DATA_DIR=/root/xiuxian-main-absorb-lab-data/data \
  XIUXIAN_SESSION_DIR=/root/xiuxian-main-absorb-lab-data/session \
  XIUXIAN_STATE_DIR=/root/xiuxian-main-absorb-lab-data/state \
  XIUXIAN_MESSAGES_DIR=/root/xiuxian-main-absorb-lab-data/messages \
  XIUXIAN_DB_FILE=/root/xiuxian-main-absorb-lab-data/state/chaogu_state.db \
  XIUXIAN_LIVE_GUARD_DIR=/root/xiuxian-main-absorb-lab-data/live-guard \
  /opt/xiuxian-main/.venv/bin/python -m pytest -q \
  tests/test_message_box_shadow.py \
  tests/test_message_contract.py \
  tests/test_storage_bag_transfer.py \
  tests/test_divination.py
```

Expanded verification:

```text
405 passed, 151 subtests passed in 3.38s
```

Full verification:

```text
1416 passed, 329 subtests passed in 6.41s
```

Command:

```text
env XIUXIAN_DATA_DIR=/root/xiuxian-main-absorb-lab-data/data \
  XIUXIAN_SESSION_DIR=/root/xiuxian-main-absorb-lab-data/session \
  XIUXIAN_STATE_DIR=/root/xiuxian-main-absorb-lab-data/state \
  XIUXIAN_MESSAGES_DIR=/root/xiuxian-main-absorb-lab-data/messages \
  XIUXIAN_DB_FILE=/root/xiuxian-main-absorb-lab-data/state/chaogu_state.db \
  XIUXIAN_LIVE_GUARD_DIR=/root/xiuxian-main-absorb-lab-data/live-guard \
  /opt/xiuxian-main/.venv/bin/python -m pytest -q
```

## Evaluation

Message-box absorption is safe as a lab layer because it is not promoted into production routing. It is useful: it captures the Rust seq-cursor lesson and gives a migration target that can handle edited bot replies without letting every module accidentally consume edits.

The app-level shadow wiring is also safe in lab because it is observe-only: it does not call send paths, does not clear pending tasks, and does not decide whether a module handled a reply. Its value is that it can be compared against current handler behavior before any module is migrated.

The alignment helper makes that comparison explicit without turning MessageBox into an input source. A missing item means "this routeable fact did not appear in passive ledger changed/skipped evidence", not "send a retry".

The report entry is intentionally file-based because a standalone CLI process cannot read the running bot process's in-memory MessageBox. The app-level export helper is the bridge for a future in-process log-group command or UI button.

Storage-bag timestamp absorption is small and likely production-worthy after one live-log review. It preserves local count updates while keeping snapshot freshness honest. Main risk is UI wording or any hidden code that treated `updated_at` as "last local quantity mutation"; tests found no such dependency.

## Promotion Plan

Recommended first promotion unit:

- `model/message_box.py`
- app shadow recording/export helpers in `model/app.py`
- shadow alignment helpers in `model/message_contract.py`
- `.消息盒子shadow [limit]` admin command in `model/control.py`
- `tools/message_contract_report.py --shadow-path`
- tests and this lab note

Why first:

- It is observe-only and supports the user's local-text-first principle.
- It does not send game commands, query APIs, clear pending tasks, or mutate module state.
- It gives immediate diagnostic value for "Tianzun replied but module did not move" incidents.

Risk:

- Memory overhead from the in-process ring buffer. Current cap is 10000 deliveries.
- A manual admin `.消息盒子shadow` command writes one JSON snapshot to `STATE_DIR/message_box_shadow/latest.json`.
- The command may show `missing` for a valid reply that is handled outside passive ledger; this is an audit signal, not an automatic retry instruction.

Rollback:

- Remove the `.消息盒子shadow` command branch and provider registration first if the command is noisy.
- Remove `_record_message_box_shadow()` calls from `on_message`, `on_message_edited`, and `_handle_suspected_game_bot_reply` if shadow ingestion itself is suspected.
- Existing module handlers remain unchanged, so rollback does not require state migration.

Suggested live verification after promotion:

1. Restart once and confirm normal startup.
2. Send `.消息盒子shadow 200` in the log group.
3. Confirm the reply says read-only and shows a snapshot path.
4. Watch for 30-60 minutes: no watchdog fuse, no increased send volume, no API calls caused by the command.
5. When a known module reply arrives, run `.消息盒子shadow 500` and inspect whether it is `changed`, `unhandled`, or `missing`.

Deferred:

- Making MessageBox the primary input for modules.
- Persistent JSONL/SQLite message-box ledger.
- UI button for shadow dump.
- Any automatic retry based on `missing`.
