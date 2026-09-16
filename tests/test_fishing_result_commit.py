import asyncio
import copy
import json
from unittest.mock import AsyncMock, Mock

import pytest

from model import persistence, state as state_module, ui
from model.features import cave_treasure_runtime as cave
from model.features import fishing_runtime as fishing
from model.features import storage_bag
from model.features.miniapp_common import MiniAppFlowCancelled
import test_fishing_caller_lifecycle as lifecycle
from test_fishing_caller_lifecycle import REAL_DAILY_REPORT, invalidate, run_caller


fishing_env = lifecycle.fishing_env
fishing_db = lifecycle.fishing_db


def apply_result(h, result=None, **kwargs):
    with state_module.use_identity(h.identity_id):
        return fishing._apply_fishing_miniapp_result(result or h.result, h.now, **kwargs)


def identity_without_pending(identity):
    return {key: value for key, value in identity.items() if key != "fishing_result_pending"}


@pytest.mark.parametrize("failure", [False, OSError("disk full")])
def test_failed_result_save_restores_facts_and_retains_local_projection(fishing_env, monkeypatch, failure):
    h = fishing_env
    before = copy.deepcopy(h.identity)
    inventory = copy.deepcopy(state_module.get_storage_bag_records())
    monkeypatch.setattr(fishing, "apply_storage_bag_item_deltas", storage_bag.apply_storage_bag_item_deltas)
    monkeypatch.setattr(storage_bag, "save_state", Mock(return_value=True))
    save = Mock(return_value=failure) if failure is False else Mock(side_effect=failure)
    monkeypatch.setattr(fishing, "save_state", save)
    with pytest.raises(RuntimeError, match="persist|commit|save"):
        apply_result(h)
    assert identity_without_pending(h.identity) == identity_without_pending(before)
    assert state_module.get_storage_bag_records() == inventory
    assert h.identity.get("fishing_result_pending")
    storage_bag.save_state.assert_not_called()


def test_result_has_one_commit_including_inventory_and_reminders(fishing_env, monkeypatch):
    h = fishing_env
    h.result["data"]["catches"][0]["rewards"] = [{"name": "\u6cd5\u5219\u788e\u7247", "qty": 1}]
    writes = []

    def save():
        writes.append((copy.deepcopy(h.identity), copy.deepcopy(state_module.get_storage_bag_records())))
        return True

    monkeypatch.setattr(fishing, "save_state", save)
    monkeypatch.setattr(storage_bag, "save_state", save)
    monkeypatch.setattr(fishing, "apply_storage_bag_item_deltas", storage_bag.apply_storage_bag_item_deltas)
    apply_result(h)
    assert len(writes) == 1
    identity, inventory = writes[0]
    assert identity["fishing_daily_count"] == 1
    assert identity["fishing_valuable_drop_reminders"]
    assert not identity.get("fishing_result_pending")
    assert inventory[str(h.identity_id)]["items"]["fixture-fish"] == 1


@pytest.mark.parametrize("caller", ["public", "message"])
def test_failed_commit_is_not_reported_as_completed(fishing_env, monkeypatch, caller):
    h = fishing_env
    save = Mock(side_effect=[True, False]) if caller == "message" else Mock(return_value=False)
    monkeypatch.setattr(fishing, "save_state", save)
    response = asyncio.run(run_caller(h, caller))
    assert h.identity["fishing_daily_count"] == 0
    assert h.identity.get("fishing_result_pending")
    h.daily.assert_not_awaited()
    h.harvest.assert_not_awaited()
    if caller == "public":
        assert response["ok"] is False
        assert response["extra"]["status"] == "persistence_pending"


@pytest.mark.parametrize("caller", ["public", "message", "scheduler"])
def test_local_commit_recovery_runs_without_another_game_call(fishing_env, monkeypatch, caller):
    h = fishing_env
    monkeypatch.setattr(fishing, "apply_storage_bag_item_deltas", storage_bag.apply_storage_bag_item_deltas)
    monkeypatch.setattr(storage_bag, "save_state", Mock(return_value=True))
    monkeypatch.setattr(fishing, "save_state", Mock(return_value=False))
    try:
        apply_result(h)
    except RuntimeError:
        pass
    assert h.identity.get("fishing_result_pending")
    monkeypatch.setattr(fishing, "save_state", Mock(return_value=True))
    if caller == "scheduler":
        with state_module.use_identity(h.identity_id):
            asyncio.run(fishing.run_fishing_scheduler(h.now))
    else:
        asyncio.run(run_caller(h, caller))
    assert h.identity["fishing_daily_count"] == 1
    assert not h.identity.get("fishing_result_pending")
    assert state_module.get_storage_bag_records()[str(h.identity_id)]["items"]["fixture-fish"] == 1
    h.flow.assert_not_awaited()
    h.loader.assert_not_awaited()
    h.external.assert_not_awaited()


def test_pre_worker_save_failure_never_starts_http(fishing_env, monkeypatch):
    h = fishing_env
    monkeypatch.setattr(fishing, "save_state", Mock(return_value=False))
    asyncio.run(run_caller(h, "message"))
    h.flow.assert_not_awaited()
    h.harvest.assert_not_awaited()


@pytest.mark.parametrize("caller", ["public", "message"])
def test_cancelled_confirmed_result_stays_pending_when_commit_fails(fishing_env, monkeypatch, caller):
    h = fishing_env
    h.flow.side_effect = MiniAppFlowCancelled(h.result)
    save = Mock(side_effect=[True, False]) if caller == "message" else Mock(return_value=False)
    monkeypatch.setattr(fishing, "save_state", save)
    with pytest.raises(MiniAppFlowCancelled):
        asyncio.run(run_caller(h, caller))
    assert h.identity["fishing_daily_count"] == 0
    assert h.identity.get("fishing_result_pending")
    assert not fishing._fishing_send_lock(h.identity_id).locked()


def test_no_rod_save_failure_is_not_silent_success(fishing_env, monkeypatch):
    h = fishing_env
    before = copy.deepcopy(h.identity)
    monkeypatch.setattr(fishing, "save_state", Mock(return_value=False))
    with pytest.raises(RuntimeError, match="persist|commit|save"):
        apply_result(h, {"ok": False, "status": "no_rod", "data": {}})
    assert identity_without_pending(h.identity) == identity_without_pending(before)


def test_failed_result_then_sqlite_reload_recovers_once(fishing_db, monkeypatch):
    h = fishing_db
    assert persistence.save_state()
    monkeypatch.setattr(fishing, "save_state", Mock(return_value=False))
    try:
        apply_result(h)
    except RuntimeError:
        pass
    assert h.identity.get("fishing_result_pending")
    assert persistence.save_state()
    state_module._meta_state["identity_states"] = {}
    state_module.set_storage_bag_records({})
    assert persistence.load_state()
    h.identity = state_module.get_identity_state(h.identity_id)
    assert h.identity["fishing_daily_count"] == 0
    assert h.identity.get("fishing_result_pending")
    monkeypatch.setattr(fishing, "save_state", persistence.save_state)
    with state_module.use_identity(h.identity_id):
        asyncio.run(fishing.run_fishing_scheduler(h.now))
    assert h.identity["fishing_daily_count"] == 1
    assert not h.identity.get("fishing_result_pending")
    state_module._meta_state["identity_states"] = {}
    state_module.set_storage_bag_records({})
    assert persistence.load_state()
    h.identity = state_module.get_identity_state(h.identity_id)
    assert h.identity["fishing_daily_count"] == 1
    assert state_module.get_storage_bag_records()[str(h.identity_id)]["items"]["fixture-fish"] == 1
    h.flow.assert_not_awaited()


@pytest.mark.parametrize("value", ["{broken", "[]", "null", "false", "1"])
def test_corrupt_pending_json_does_not_decode_as_no_pending(fishing_env, value):
    decoded = persistence._deserialize_db_value("fishing_result_pending", value)
    assert isinstance(decoded, dict) and decoded and not fishing._valid_fishing_result_pending(decoded)


def stage_pending(h, monkeypatch):
    monkeypatch.setattr(fishing, "save_state", Mock(return_value=False))
    monkeypatch.setattr(storage_bag, "save_state", Mock(return_value=True))
    monkeypatch.setattr(fishing, "apply_storage_bag_item_deltas", storage_bag.apply_storage_bag_item_deltas)
    with pytest.raises(fishing.FishingMiniAppCommitError):
        apply_result(h)
    monkeypatch.setattr(fishing, "save_state", Mock(return_value=True))
    assert h.identity["fishing_result_pending"]


@pytest.mark.parametrize("change", ["paused", "module_disabled", "identity_disabled", "schedule", "pond", "bait"])
def test_recovery_after_controls_change_commits_only_facts(fishing_env, monkeypatch, change):
    h = fishing_env
    h.identity["fishing_transfer_target_id"] = h.other_id
    h.result["data"]["catches"][0]["rewards"] = [{"name": "\u6cd5\u5219\u788e\u7247", "qty": 1}]
    stage_pending(h, monkeypatch)
    invalidate(h, change)
    before = copy.deepcopy(h.identity)
    response = fishing.recover_fishing_result_pending(h.identity_id)
    assert response["ok"]
    assert h.identity["fishing_daily_count"] == 1
    for key in fishing._FISHING_RESULT_PLAN_KEYS:
        assert h.identity.get(key) == before.get(key), key
    assert h.identity["fishing_valuable_drop_reminders"] == []
    assert not h.identity["fishing_caught_fish_json"]
    assert state_module.get_storage_bag_records()[str(h.identity_id)]["items"]["fixture-fish"] == 1


@pytest.mark.parametrize("change", ["account", "facts", "inventory", "inventory_snapshot"])
@pytest.mark.parametrize("caller", ["public", "message", "scheduler"])
def test_changed_recovery_basis_does_not_send_or_overwrite(fishing_env, monkeypatch, change, caller):
    h = fishing_env
    stage_pending(h, monkeypatch)
    if change == "account":
        state_module.set_identity_account(h.identity_id, 7199)
    elif change == "facts":
        h.identity["fishing_daily_count"] = 2
    else:
        state_module.set_storage_bag_records({str(h.identity_id): {
            "items": {"fixture-fish": 1} if change == "inventory" else {},
            "updated_at": h.now if change == "inventory_snapshot" else 0,
        }})
    before = copy.deepcopy(h.identity)
    inventory = copy.deepcopy(state_module.get_storage_bag_records())
    if caller == "scheduler":
        with state_module.use_identity(h.identity_id):
            asyncio.run(fishing.run_fishing_scheduler(h.now))
    else:
        asyncio.run(run_caller(h, caller))
    assert h.identity == before
    assert state_module.get_storage_bag_records() == inventory
    h.flow.assert_not_awaited()
    h.loader.assert_not_awaited()


@pytest.mark.parametrize("read", ["status", "daily"])
def test_reads_cannot_change_pending_day_basis(fishing_env, monkeypatch, read):
    h = fishing_env
    h.identity.update(fishing_pond=state_module.IDENTITY_STATE_TEMPLATE["fishing_pond"],
                      fishing_bait=state_module.IDENTITY_STATE_TEMPLATE["fishing_bait"])
    stage_pending(h, monkeypatch)
    before = copy.deepcopy(h.identity)
    monkeypatch.setattr(fishing.time, "time", lambda: h.now + 86400)
    with state_module.use_identity(h.identity_id):
        if read == "status":
            fishing.get_fishing_status_text()
        else:
            asyncio.run(REAL_DAILY_REPORT(h.now + 86400))
    assert h.identity == before
    assert fishing.recover_fishing_result_pending(h.identity_id)["ok"]


@pytest.mark.parametrize("broken", ["huge_time", "nan", "wrong_keys", "wrong_owner", "bad_counts", "empty_summary", "bad_json"])
def test_corrupt_pending_projection_holds_without_gameplay(fishing_env, monkeypatch, broken):
    h = fishing_env
    stage_pending(h, monkeypatch)
    pending = h.identity["fishing_result_pending"]
    if broken == "huge_time":
        pending["created_at"] = 10 ** 500
    elif broken == "nan":
        pending["created_at"] = float("nan")
    elif broken == "wrong_keys":
        pending["updates"]["tianxing_enabled"] = True
    elif broken == "wrong_owner":
        pending["identity_id"] = h.other_id
    elif broken == "bad_counts":
        pending["updates"]["fishing_daily_count"] = True
    elif broken == "empty_summary":
        pending["summary"] = ""
    else:
        pending["updates"]["fishing_daily_catch_summary_json"] = "{broken"
    asyncio.run(run_caller(h, "public"))
    assert h.identity["fishing_daily_count"] == 0
    assert h.identity["fishing_result_pending"]
    h.flow.assert_not_awaited()
    h.loader.assert_not_awaited()


def test_public_missing_bait_schedule_is_part_of_result_commit(fishing_env, monkeypatch):
    h = fishing_env
    h.result.update(status="bait_missing")
    writes = []

    def save():
        writes.append(copy.deepcopy(h.identity))
        return True

    monkeypatch.setattr(fishing, "save_state", save)
    monkeypatch.setattr(cave, "save_state", save)
    response = asyncio.run(run_caller(h, "public"))
    assert response["ok"]
    assert len(writes) == 1
    assert writes[0]["next_fishing_time"] == h.identity["next_fishing_time"]
    assert writes[0]["fishing_last_result"] == h.identity["fishing_last_result"]


def test_inventory_noop_cannot_commit_only_the_daily_count(fishing_env, monkeypatch):
    h = fishing_env
    monkeypatch.setattr(fishing, "apply_storage_bag_item_deltas", Mock(return_value=False))
    with pytest.raises(fishing.FishingMiniAppCommitError):
        apply_result(h)
    assert h.identity["fishing_daily_count"] == 0
    assert h.identity["fishing_result_pending"]


def test_pending_blocks_followups_and_is_visible_in_ui(fishing_env, monkeypatch):
    h = fishing_env
    h.identity.update(fishing_pond=state_module.IDENTITY_STATE_TEMPLATE["fishing_pond"],
                      fishing_bait=state_module.IDENTITY_STATE_TEMPLATE["fishing_bait"])
    stage_pending(h, monkeypatch)
    h.identity["fishing_daily_count"] = 7
    reminders, transfers = AsyncMock(), AsyncMock()
    monkeypatch.setattr(fishing, "_run_fishing_valuable_drop_reminders", reminders)
    monkeypatch.setattr(fishing, "_run_pending_fishing_transfer", transfers)
    with state_module.use_identity(h.identity_id):
        asyncio.run(fishing.run_fishing_scheduler(h.now))
        status = fishing.get_fishing_status_text()
    assert "\u5165\u8d26" in status
    reminders.assert_not_awaited()
    transfers.assert_not_awaited()


def test_pending_background_work_is_due_even_after_daily_limit(fishing_env, monkeypatch):
    h = fishing_env
    stage_pending(h, monkeypatch)
    h.identity.update(fishing_daily_count=10, fishing_last_result="daily_limit")
    monkeypatch.setattr(ui, "normalize_miniapp_auto_config", lambda: {"cave_public_fishing_identity_ids": [h.identity_id]})
    assert ui._cave_public_background_action_due("fishing", h.identity_id, h.now)


def test_real_sqlite_failure_keeps_old_counts_inventory_and_recoverable_projection(fishing_db):
    h = fishing_db
    h.result["data"]["catches"][0]["rewards"] = [{"name": "\u6cd5\u5219\u788e\u7247", "qty": 1}]
    assert persistence.save_state()
    conn = persistence.get_db_conn()
    conn.execute("""CREATE TEMP TRIGGER fail_fishing_projection BEFORE UPDATE ON identity_runtime_state
                    WHEN NEW.fishing_daily_count > OLD.fishing_daily_count
                    BEGIN SELECT RAISE(ABORT, 'fixture write failure'); END""")
    before_inventory = copy.deepcopy(state_module.get_storage_bag_records())
    with pytest.raises(fishing.FishingMiniAppCommitError):
        apply_result(h)
    assert h.identity["fishing_daily_count"] == 0
    assert h.identity["fishing_valuable_drop_reminders"] == []
    assert state_module.get_storage_bag_records() == before_inventory
    row = conn.execute("SELECT fishing_daily_count, fishing_result_pending FROM identity_runtime_state WHERE send_as_id = ?",
                       (h.identity_id,)).fetchone()
    assert row["fishing_daily_count"] == 0
    assert json.loads(row["fishing_result_pending"]) == {}
    saved_items = json.loads(conn.execute("SELECT value FROM meta WHERE key = 'storage_bag_records'").fetchone()[0])
    assert saved_items == before_inventory
    conn.execute("DROP TRIGGER fail_fishing_projection")
    conn.commit()
    assert persistence.save_state()
    state_module._meta_state["identity_states"] = {}
    state_module.set_storage_bag_records({})
    assert persistence.load_state()
    assert fishing.recover_fishing_result_pending(h.identity_id)["ok"]
    identity = state_module.get_identity_state(h.identity_id)
    assert identity["fishing_daily_count"] == 1
    assert len(identity["fishing_valuable_drop_reminders"]) == 1
    assert not identity["fishing_result_pending"]
    assert fishing.recover_fishing_result_pending(h.identity_id) is None
    assert state_module.get_storage_bag_records()[str(h.identity_id)]["items"]["fixture-fish"] == 1


@pytest.mark.parametrize("boundary", ["inventory", "reminder"])
def test_projection_exception_restores_all_components(fishing_env, monkeypatch, boundary):
    h = fishing_env
    h.result["data"]["catches"][0]["rewards"] = [{"name": "\u6cd5\u5219\u788e\u7247", "qty": 1}]
    before = copy.deepcopy(h.identity)
    inventory = copy.deepcopy(state_module.get_storage_bag_records())
    actual = storage_bag.apply_storage_bag_item_deltas if boundary == "inventory" else fishing._queue_fishing_valuable_drop_reminders

    def fail_after_partial_write(*args, **kwargs):
        actual(*args, **kwargs)
        raise ValueError("fixture projection error")

    monkeypatch.setattr(fishing, "apply_storage_bag_item_deltas", storage_bag.apply_storage_bag_item_deltas)
    name = "apply_storage_bag_item_deltas" if boundary == "inventory" else "_queue_fishing_valuable_drop_reminders"
    monkeypatch.setattr(fishing, name, fail_after_partial_write)
    with pytest.raises(fishing.FishingMiniAppCommitError):
        apply_result(h)
    assert identity_without_pending(h.identity) == identity_without_pending(before)
    assert state_module.get_storage_bag_records() == inventory
    assert h.identity["fishing_result_pending"]
    fishing.save_state.assert_not_called()
    monkeypatch.setattr(fishing, name, actual)
    assert fishing.recover_fishing_result_pending(h.identity_id)["ok"]
    assert h.identity["fishing_daily_count"] == 1
    assert len(h.identity["fishing_valuable_drop_reminders"]) == 1


def test_new_result_cannot_replace_an_unresolved_local_projection(fishing_env, monkeypatch):
    h = fishing_env
    stage_pending(h, monkeypatch)
    before = copy.deepcopy(h.identity)
    h.result["data"]["catches"][0]["fish"] = "another-fish"
    with pytest.raises(fishing.FishingMiniAppCommitError):
        apply_result(h)
    assert h.identity == before


def test_local_recovery_cannot_write_through_other_current_identity(fishing_env, monkeypatch):
    h = fishing_env
    stage_pending(h, monkeypatch)
    other = state_module.get_identity_state(h.other_id)
    before = copy.deepcopy(other)
    with state_module.use_identity(h.other_id):
        assert fishing.recover_fishing_result_pending(h.identity_id)["ok"]
        assert state_module.get_current_identity_id() == h.other_id
    assert other == before
    assert h.identity["fishing_daily_count"] == 1


def test_pending_projection_does_not_persist_raw_miniapp_credentials(fishing_env, monkeypatch):
    h = fishing_env
    h.result.update(ok=False, status="failed", error="token=R107SECRET&initData=R107AUTH")
    h.result["data"].update(token="R107SECRET", initData="R107AUTH", url="https://fixture.invalid/?token=R107SECRET")
    stage_pending(h, monkeypatch)
    encoded = json.dumps(h.identity["fishing_result_pending"])
    assert "R107SECRET" not in encoded and "R107AUTH" not in encoded
    assert "fixture.invalid" not in encoded
    assert fishing.recover_fishing_result_pending(h.identity_id)["ok"]


def test_daily_ack_does_not_mark_facts_with_a_new_uncommitted_result(fishing_env, monkeypatch):
    h = fishing_env
    h.identity.update(fishing_daily_count=10, fishing_daily_catch_summary_json=json.dumps({
        "day": h.identity["fishing_daily_day"], "rods": 10, "fish": {"fixture-fish": 10}, "rewards": {},
    }))

    async def report(*_args, **_kwargs):
        h.identity["fishing_result_pending"] = {"invalid": True}
        return True

    monkeypatch.setattr(fishing, "send_audit_log", report)
    with state_module.use_identity(h.identity_id):
        asyncio.run(REAL_DAILY_REPORT(h.now))
    assert h.identity["fishing_daily_summary_day"] == ""
