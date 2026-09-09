import asyncio
import copy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import persistence
from model import state as state_module
from model.features import explore_rift, storage_bag


NOW = 1780000000.0
IDENTITY = 990590001
ACCOUNT = 7551
CHAT = -100590001
OTHER_CHAT = -100590002
ROOT = 5901
RESULT = 5902
ITEM = "\u6cd5\u5219\u788e\u7247"
SUCCESS = "\u3010\u63a2\u5bfb\u6210\u529f\u3011\n\u83b7\u5f97\u4e86\u3010\u6cd5\u5219\u788e\u7247\u3011x2"
FAILURE = "\u3010\u906d\u9047\u98ce\u66b4\u3011\n\u4fee\u4e3a\u5012\u9000\u4e86 300 \u70b9\uff01"
START = explore_rift.EXPLORE_RIFT_PENDING_KEYWORD
FATAL = explore_rift.EXPLORE_RIFT_FATAL_TITLE
ESCAPE = explore_rift.EXPLORE_RIFT_ESCAPE_WEAK_TITLE + "\n6\u5c0f\u65f6"
CD = "\u7a7a\u95f4\u88c2\u7f1d\u5c1a\u672a\u7a33\u5b9a\uff0c\u8bf7\u7b49\u5f85 1\u5c0f\u65f6\u3002"


@pytest.fixture
def env(monkeypatch, tmp_path):
    saved = copy.deepcopy(state_module._meta_state)
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    state_module.set_identity_account(IDENTITY, ACCOUNT)
    state_module.set_game_group_route_config({
        "enabled": True, "primary_group_id": CHAT, "backup_group_ids": [OTHER_CHAT],
    })
    identity = state_module.get_identity_state(IDENTITY)
    identity.update(explore_rift_enabled=True, tianxing_enabled=False)
    audit = AsyncMock()
    for module in (explore_rift, storage_bag):
        monkeypatch.setattr(module, "save_state", Mock(return_value=True))
    monkeypatch.setattr(explore_rift, "send_audit_log", audit)
    monkeypatch.setattr(explore_rift, "send_game_command", AsyncMock())
    monkeypatch.setattr(explore_rift.random, "uniform", lambda *_args: 0)
    monkeypatch.setattr(explore_rift.time, "time", lambda: NOW + 200)
    monkeypatch.setattr(explore_rift, "MESSAGES_DIR", str(tmp_path))
    monkeypatch.setattr(persistence, "DB_FILE", str(tmp_path / "rift-results.db"))
    with state_module.use_identity(IDENTITY):
        yield SimpleNamespace(identity=identity, audit=audit)
    explore_rift.send_game_command.assert_not_awaited()
    state_module._meta_state.clear()
    state_module._meta_state.update(saved)


def delivery(text=SUCCESS, *, root=ROOT, result=RESULT, chat=CHAT, at=NOW, kind="message", context=None):
    return explore_rift.handle_explore_rift_reply(
        text, NOW + 200, matched_family="explore_rift", result_msg_id=result,
        reply_to=SimpleNamespace(id=root, chat_id=chat, raw_text=explore_rift.CMD_EXPLORE_RIFT),
        reply_context={
            "send_as_id": IDENTITY, "account_id": ACCOUNT, "chat_id": chat,
            "root_msg_id": root, "reply_to_msg_id": root, "msg_id": result,
            "server_event_at": at, "processed_at": NOW + 200, "event_type": kind,
            **(context or {}),
        },
    )


def item_count():
    return state_module.get_storage_bag_records().get(str(IDENTITY), {}).get("items", {}).get(ITEM, 0)


@pytest.mark.parametrize("reload", [False, True])
def test_interleaved_result_replay_never_reapplies_items_or_rewinds_cooldown(env, monkeypatch, reload):
    if reload:
        monkeypatch.setattr(explore_rift, "save_state", persistence.save_state)
        monkeypatch.setattr(storage_bag, "save_state", persistence.save_state)
    assert asyncio.run(delivery())
    assert asyncio.run(delivery(root=ROOT + 10, result=RESULT + 10, at=NOW + 100))
    if reload:
        assert persistence.load_state()
    expected = copy.deepcopy(state_module.get_identity_state(IDENTITY))
    assert asyncio.run(delivery())
    assert item_count() == 4
    assert state_module.get_identity_state(IDENTITY) == expected


@pytest.mark.parametrize("edit", ["wording", "second_message"])
def test_same_operation_cannot_award_twice_or_extend_cooldown(env, edit):
    assert asyncio.run(delivery())
    next_time = env.identity["next_explore_rift_time"]
    assert asyncio.run(delivery(
        SUCCESS + "\n\u6218\u62a5\u5b8c\u6210", at=NOW + 30,
        result=RESULT + 1 if edit == "second_message" else RESULT,
        kind="message" if edit == "second_message" else "edit",
    ))
    assert item_count() == 2
    assert env.identity["next_explore_rift_time"] == next_time


def test_newer_reward_edit_applies_only_the_difference(env):
    assert asyncio.run(delivery())
    next_time = env.identity["next_explore_rift_time"]
    assert asyncio.run(delivery(SUCCESS.replace("x2", "x3"), at=NOW + 20, kind="edit"))
    assert item_count() == 3
    assert env.identity["next_explore_rift_time"] == next_time
    assert asyncio.run(delivery())
    assert item_count() == 3


def test_same_ids_in_two_chats_are_distinct_results(env):
    assert asyncio.run(delivery())
    assert asyncio.run(delivery(chat=OTHER_CHAT, at=NOW + 10))
    assert item_count() == 4
    assert env.identity["next_explore_rift_time"] == NOW + 10 + explore_rift._resolve_cd_sec()


def test_first_late_older_result_adds_missing_items_without_replacing_newer_state(env):
    assert asyncio.run(delivery(FAILURE, root=ROOT + 10, result=RESULT + 10, at=NOW + 100))
    expected = {key: env.identity[key] for key in (
        "next_explore_rift_time", "explore_rift_last_result", "explore_rift_last_msg_id",
        "explore_rift_last_result_key",
    )}
    assert asyncio.run(delivery())
    assert item_count() == 2
    for key, value in expected.items():
        assert env.identity[key] == value, key


@pytest.mark.parametrize("text", [START, CD, FAILURE, SUCCESS, ESCAPE, FATAL])
@pytest.mark.parametrize("bad", [
    {"send_as_id": IDENTITY + 1}, {"account_id": ACCOUNT + 1}, {"chat_id": OTHER_CHAT},
    {"msg_id": RESULT + 20}, {"root_msg_id": ROOT + 20}, {"chat_id": 0},
    {"server_event_at": 0}, {"server_event_at": True}, {"server_event_at": float("nan")},
    {"server_event_at": NOW + 36000}, {"event_type": "sent"},
    {"event_type": []},
])
def test_unowned_or_malformed_result_does_not_touch_state(env, text, bad):
    expected = copy.deepcopy(state_module._meta_state)
    assert not asyncio.run(delivery(text, context=bad))
    assert state_module._meta_state == expected
    env.audit.assert_not_awaited()


def test_conflicting_edits_in_same_server_second_are_not_guessed(env):
    assert asyncio.run(delivery(kind="edit"))
    expected = copy.deepcopy(state_module._meta_state)
    assert not asyncio.run(delivery(SUCCESS.replace("x2", "x3"), kind="edit"))
    assert state_module._meta_state == expected


def test_original_then_edit_in_same_second_accepts_the_edit_once(env):
    assert asyncio.run(delivery())
    assert asyncio.run(delivery(SUCCESS.replace("x2", "x3"), kind="edit"))
    assert item_count() == 3
    assert asyncio.run(delivery())
    assert item_count() == 3


def test_every_commit_contains_both_result_evidence_and_inventory_delta(env, monkeypatch):
    commits = []

    def save():
        commits.append((env.identity["explore_rift_last_result_key"], item_count()))
        return True

    monkeypatch.setattr(explore_rift, "save_state", save)
    monkeypatch.setattr(storage_bag, "save_state", save)
    assert asyncio.run(delivery())
    assert commits
    assert all(key and count == 2 for key, count in commits)


def test_newer_inventory_snapshot_already_includes_old_reward(env):
    state_module.set_storage_bag_records({str(IDENTITY): {
        "identity_id": IDENTITY, "items": {ITEM: 10}, "sections": {}, "updated_at": NOW + 100,
    }})
    assert asyncio.run(delivery())
    assert item_count() == 10


def test_fatal_duplicate_does_not_extend_grace_or_restore_fatal_after_escape(env):
    assert asyncio.run(delivery(FATAL))
    deadline = env.identity["explore_rift_fatal_confirm_due_at"]
    assert asyncio.run(delivery(FATAL, kind="edit", at=NOW + 2))
    assert env.identity["explore_rift_fatal_confirm_due_at"] == deadline
    assert asyncio.run(delivery(ESCAPE, kind="edit", at=NOW + 4))
    expected = copy.deepcopy(env.identity)
    assert asyncio.run(delivery(FATAL))
    assert env.identity == expected


@pytest.mark.parametrize("change", ["removed", "replaced", "rebound", "newer_result", "cancelled"])
def test_escape_is_committed_before_notification_and_cannot_write_after_it(env, change):
    expected = {}

    async def notify(*args, **kwargs):
        assert env.identity["explore_rift_last_result_key"]
        if change == "cancelled":
            raise asyncio.CancelledError()
        if change in {"removed", "replaced"}:
            state_module.remove_identity(IDENTITY)
            target = IDENTITY if change == "replaced" else IDENTITY + 1
            state_module.set_identity_account(target, ACCOUNT)
        else:
            target = IDENTITY
            if change == "rebound":
                state_module.set_identity_account(IDENTITY, ACCOUNT + 1)
            else:
                env.identity["explore_rift_last_result_key"] = "newer-result"
        expected.update(target=target, state=copy.deepcopy(state_module.get_identity_state(target)))

    env.audit.side_effect = notify
    if change == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(delivery(ESCAPE))
        assert env.identity["explore_rift_last_result_key"]
    else:
        assert asyncio.run(delivery(ESCAPE))
        assert state_module.get_identity_state(expected["target"]) == expected["state"]


def test_older_pending_or_cooldown_cannot_clear_new_operation(env):
    assert asyncio.run(delivery())
    env.identity.update(explore_rift_reply_to_msg_id=ROOT + 20, explore_rift_reply_due_at=NOW + 300)
    expected = copy.deepcopy(env.identity)
    assert asyncio.run(delivery(START, at=NOW - 10))
    assert asyncio.run(delivery(CD, at=NOW - 5))
    assert env.identity == expected


@pytest.mark.parametrize("boundary", ["inventory", "tianxing"])
def test_reducer_exception_cannot_leave_partial_completion(env, monkeypatch, boundary):
    expected = copy.deepcopy(state_module._meta_state)

    def fail(*args, **kwargs):
        env.identity["explore_rift_last_result"] = "partial"
        raise ValueError("injected reducer failure")

    name = "apply_storage_bag_item_deltas" if boundary == "inventory" else "_apply_tianxing_explore_rift_result"
    monkeypatch.setattr(explore_rift, name, fail)
    with pytest.raises(ValueError, match="injected reducer failure"):
        asyncio.run(delivery())
    assert state_module._meta_state == expected
    env.audit.assert_not_awaited()


def test_database_rollback_cannot_commit_reward_without_completion_or_the_reverse(env, monkeypatch):
    assert persistence.save_state()
    monkeypatch.setattr(explore_rift, "save_state", persistence.save_state)
    with monkeypatch.context() as broken:
        broken.setattr(persistence, "upsert_identity_to_db", Mock(side_effect=OSError("injected write failure")))
        assert not asyncio.run(delivery())
    assert persistence.load_state()
    assert not state_module.state["explore_rift_last_result_key"]
    assert not state_module.state["explore_rift_result_evidence"]
    assert item_count() == 0
    assert asyncio.run(delivery())
    assert persistence.load_state()
    assert item_count() == 2
    assert state_module.state["explore_rift_result_evidence"]["receipts"]


def test_failed_save_can_retry_in_memory_without_awarding_twice(env, monkeypatch):
    save = Mock(side_effect=[False, True])
    monkeypatch.setattr(explore_rift, "save_state", save)
    assert not asyncio.run(delivery())
    assert item_count() == 2
    assert asyncio.run(delivery())
    assert item_count() == 2
    assert save.call_count == 2


@pytest.mark.parametrize("reason", ["capacity", "age"])
def test_evicted_result_cannot_become_a_new_reward_even_with_a_new_edit_timestamp(env, monkeypatch, reason):
    monkeypatch.setattr(explore_rift, "save_state", persistence.save_state)
    monkeypatch.setattr(explore_rift, "EXPLORE_RIFT_RESULT_LIMIT", 3 if reason == "capacity" else 64)
    monkeypatch.setattr(explore_rift, "EXPLORE_RIFT_RESULT_RETENTION_SEC", 2 if reason == "age" else 86400)
    for index in range(6):
        assert asyncio.run(delivery(root=ROOT + index * 10, result=RESULT + index * 10, at=NOW + index))
    assert persistence.load_state()
    ledger = state_module.state["explore_rift_result_evidence"]
    assert len(ledger["receipts"]) <= 3
    assert ledger["retired_roots"][str(CHAT)] >= ROOT
    expected = copy.deepcopy(state_module._meta_state)
    assert asyncio.run(delivery(SUCCESS.replace("x2", "x5"), at=NOW + 100, kind="edit"))
    assert item_count() == 12
    assert state_module._meta_state == expected
    assert asyncio.run(delivery(chat=OTHER_CHAT, at=NOW + 101))
    assert item_count() == 14


@pytest.mark.parametrize("bad", [
    [], {"receipts": []}, {"retired_roots": []}, {"latest": []},
    {"receipts": {"bad": []}}, {"receipts": {"bad": {"event_at": "later"}}},
    {"retired_roots": {str(CHAT): "bad"}}, {"retired_roots": {str(CHAT): True}},
    {"latest": {"key": "missing", "root_msg_id": "bad", "chat_id": CHAT}},
])
def test_corrupt_completion_evidence_is_not_silently_reset_or_applied(env, bad):
    env.identity["explore_rift_result_evidence"] = bad
    expected = copy.deepcopy(state_module._meta_state)
    assert not asyncio.run(delivery())
    assert state_module._meta_state == expected


def test_new_account_cannot_reinterpret_completed_result_without_account_context(env):
    assert asyncio.run(delivery())
    state_module.set_identity_account(IDENTITY, ACCOUNT + 1)
    expected = copy.deepcopy(state_module._meta_state)
    assert not asyncio.run(delivery(context={"account_id": ACCOUNT + 1}, kind="edit", at=NOW + 10))
    assert state_module._meta_state == expected


def test_terminal_state_is_retained_when_tianxing_audit_loses_the_identity(env, monkeypatch):
    async def removed(*args, **kwargs):
        state_module.remove_identity(IDENTITY)
        state_module.set_identity_account(IDENTITY + 1, ACCOUNT)

    monkeypatch.setattr(explore_rift, "_send_tianxing_explore_rift_result_audit", removed)
    assert asyncio.run(delivery())
    env.audit.assert_not_awaited()
    assert not state_module.get_identity_state(IDENTITY + 1)["explore_rift_last_result_key"]


@pytest.mark.parametrize("field,value", [("stage", []), ("event_type", []), ("items", []), ("event_at", True)])
def test_corrupt_existing_receipt_stays_visible_and_does_not_crash(env, field, value):
    assert asyncio.run(delivery())
    env.identity["explore_rift_result_evidence"]["receipts"][f"{CHAT}:{ROOT}"][field] = value
    expected = copy.deepcopy(state_module._meta_state)
    assert not asyncio.run(delivery(kind="edit", at=NOW + 10))
    assert state_module._meta_state == expected


def test_legacy_sqlite_schema_adds_result_evidence_with_atomic_save(env, monkeypatch):
    assert persistence.save_state()
    conn = persistence.get_db_conn()
    conn.execute("ALTER TABLE identity_runtime_state DROP COLUMN explore_rift_result_evidence")
    conn.commit()
    monkeypatch.setattr(explore_rift, "save_state", persistence.save_state)
    assert asyncio.run(delivery())
    assert persistence.load_state()
    assert state_module.state["explore_rift_result_evidence"]["receipts"]
    assert item_count() == 2


def test_native_message_and_edit_replay_preserve_scoped_reward_idempotence(env, monkeypatch):
    from model import app, app_runtime, runtime

    monkeypatch.setattr(runtime, "_notify_game_command_sent_observers", Mock())
    monkeypatch.setattr(runtime, "note_game_command_sent", Mock())
    monkeypatch.setattr(runtime, "_reply_chain_tracker", {})
    monkeypatch.setattr(app, "_remember_early_routed_reply", Mock())
    monkeypatch.setattr(app, "schedule_cleanup", AsyncMock())
    monkeypatch.setattr(app_runtime, "_runtime_event_claims", {})
    monkeypatch.setattr(app_runtime, "_runtime_message_consumed", {})
    for root, result, at, kind in (
        (ROOT, RESULT, NOW, "message"),
        (ROOT + 10, RESULT + 10, NOW + 10, "edit"),
        (ROOT, RESULT, NOW + 20, "edit"),
    ):
        runtime._finalize_game_command_sent(
            explore_rift.CMD_EXPLORE_RIFT, msg_id=root, sent_at=NOW - 10, send_started_at=NOW - 11,
            send_as_id=IDENTITY, game_group_id=CHAT, topic_id=0, max_retry=0, append_sent_log=False,
        )
        event = SimpleNamespace(
            id=result, chat_id=CHAT, sender_id=880590001, raw_text=SUCCESS,
            date=datetime.fromtimestamp(at, timezone.utc),
            edit_date=datetime.fromtimestamp(at, timezone.utc) if kind == "edit" else None,
            reply_to=SimpleNamespace(reply_to_msg_id=root),
        )
        reply = SimpleNamespace(id=root, raw_text=explore_rift.CMD_EXPLORE_RIFT, chat_id=CHAT)
        ctx = {"send_as_id": IDENTITY, "root_msg_id": root, "reply_to_msg_id": root, "family": "explore_rift"}
        assert asyncio.run(app._handle_routed_reply_event(event, SUCCESS, NOW + 200, reply, ctx, event_kind=kind))
    assert item_count() == 4
    assert env.identity["next_explore_rift_time"] == NOW + 10 + explore_rift._resolve_cd_sec()


def test_removed_identity_cannot_enter_result_reducer(env):
    state_module.remove_identity(IDENTITY)
    state_module.set_identity_account(IDENTITY + 1, ACCOUNT)
    expected = copy.deepcopy(state_module._meta_state)
    assert not asyncio.run(delivery())
    assert state_module._meta_state == expected


@pytest.mark.parametrize("chat", [CHAT, OTHER_CHAT])
def test_legacy_last_result_without_chat_cannot_be_assumed_unapplied(env, chat):
    env.identity["explore_rift_last_result_key"] = explore_rift._make_result_key(RESULT, explore_rift.EXPLORE_RIFT_RESULT_TITLE, SUCCESS)
    state_module.set_storage_bag_records({str(IDENTITY): {"items": {ITEM: 2}, "sections": {}}})
    expected = copy.deepcopy(state_module._meta_state)
    assert not asyncio.run(delivery(chat=chat, kind="edit", at=NOW + 10))
    assert item_count() == 2
    assert state_module._meta_state == expected


def test_new_results_and_reload_do_not_erase_legacy_unscoped_hold(env, monkeypatch):
    env.identity["explore_rift_last_result_key"] = explore_rift._make_result_key(RESULT, explore_rift.EXPLORE_RIFT_RESULT_TITLE, SUCCESS)
    state_module.set_storage_bag_records({str(IDENTITY): {"items": {ITEM: 2}, "sections": {}}})
    monkeypatch.setattr(explore_rift, "save_state", persistence.save_state)
    assert asyncio.run(delivery(root=ROOT + 10, result=RESULT + 10, at=NOW + 20))
    assert item_count() == 4
    assert persistence.load_state()
    explore_rift.clear_explore_rift_state(persist=True)
    expected = copy.deepcopy(state_module._meta_state)
    assert not asyncio.run(delivery(SUCCESS.replace("x2", "x3"), kind="edit", at=NOW + 100))
    assert state_module._meta_state == expected
    assert item_count() == 4


def test_legacy_clear_keeps_unscoped_evidence_before_the_first_new_result(env):
    env.identity["explore_rift_last_result_key"] = explore_rift._make_result_key(RESULT, explore_rift.EXPLORE_RIFT_RESULT_TITLE, SUCCESS)
    explore_rift.clear_explore_rift_state()
    expected = copy.deepcopy(state_module._meta_state)
    assert not asyncio.run(delivery())
    assert state_module._meta_state == expected
