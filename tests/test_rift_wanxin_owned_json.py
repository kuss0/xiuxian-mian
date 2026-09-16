import asyncio
import copy
import json
from unittest.mock import AsyncMock, Mock

import pytest

from model import control, persistence, state as state_module, ui
from model.features import explore_rift, wanxin
from tests import test_explore_rift_dispatch_lifecycle as dispatch_tests
from tests import test_explore_rift_rebirth_lifecycle as rebirth_tests
from tests import test_explore_rift_result_lifecycle as result_tests
from tests import test_wanxin_lifecycle as wanxin_tests


rebirth_env = rebirth_tests.env
result_env = result_tests.env
dispatch_env = dispatch_tests.env
wanxin_env = wanxin_tests.env
prepare_rift_route = explore_rift._prepare_explore_rift_tianxing_route
BROKEN = '{"op_id":"retained-operation","token":"fixture-secret"'
FIELDS = (
    "explore_rift_rebirth_operation",
    "explore_rift_result_evidence",
    "wanxin_observation",
)


@pytest.fixture(autouse=True)
def isolated_persistence(monkeypatch, tmp_path):
    for name, value in {
        "DB_FILE": str(tmp_path / "owned-json.db"),
        "_db_conn": None,
        "_db_initialized": False,
        "_schema_columns_ensured_key": None,
        "_schema_columns_ensured_version": None,
        "_persistence_snapshot_db_key": "",
        "_persisted_meta_snapshot": {},
        "_persisted_identity_snapshots": {},
        "_state_dirty": False,
        "_last_flush_time": 0,
        "_last_save_failed_at": 0,
        "_last_save_error": "",
    }.items():
        monkeypatch.setattr(persistence, name, value)
    monkeypatch.setattr(persistence, "_try_write_live_guard_backup", Mock())
    monkeypatch.setattr(persistence, "_maybe_restore_live_guard_backup", Mock(return_value=False))
    monkeypatch.setattr(control, "save_state", persistence.save_state)
    monkeypatch.setattr(control, "console_log", Mock())
    try:
        yield
    finally:
        if persistence._db_conn is not None:
            persistence._db_conn.close()


def inject_sql_value(identity_id, field, raw):
    assert field in FIELDS
    assert persistence.save_state()
    conn = persistence.get_db_conn()
    conn.execute(f"UPDATE identity_runtime_state SET {field} = ? WHERE send_as_id = ?", (raw, identity_id))
    conn.commit()
    assert persistence.load_state()
    return state_module.get_identity_state(identity_id)


@pytest.mark.parametrize("raw", ["[]", BROKEN, b"\xff{"])
def test_rebirth_corruption_cannot_reopen_native_dispatch_after_sqlite_reload(rebirth_env, raw):
    identity = inject_sql_value(rebirth_tests.IDENTITY, FIELDS[0], raw)
    retained = copy.deepcopy(identity[FIELDS[0]])
    rebirth_tests.run()
    rebirth_env.send.assert_not_awaited()
    assert explore_rift._rebirth_operation() is None
    assert identity[FIELDS[0]] == retained
    assert not asyncio.run(rebirth_tests.deliver(rebirth_tests.INTACT))
    assert identity["explore_rift_rebirth_required"]
    assert persistence.save_state()
    assert persistence.load_state()
    rebirth_tests.run(rebirth_tests.NOW + 86400)
    rebirth_env.send.assert_not_awaited()
    assert state_module.state[FIELDS[0]] == retained


@pytest.mark.parametrize("raw", ["[]", BROKEN, b"\xff{", '{"invalid":true}'])
def test_corrupt_rift_ledger_cannot_reaward_an_older_result(result_env, raw):
    assert asyncio.run(result_tests.delivery())
    assert asyncio.run(result_tests.delivery(
        root=result_tests.ROOT + 10, result=result_tests.RESULT + 10, at=result_tests.NOW + 100,
    ))
    assert result_tests.item_count() == 4
    identity = inject_sql_value(result_tests.IDENTITY, FIELDS[1], raw)
    before = copy.deepcopy(identity)
    accepted = asyncio.run(result_tests.delivery())
    assert result_tests.item_count() == 4
    assert not accepted
    assert identity == before


@pytest.mark.parametrize("raw", ["[]", BROKEN, b"\xff{", '{"invalid":true}'])
def test_wanxin_corruption_blocks_native_scheduler_and_reply(wanxin_env, monkeypatch, raw):
    sender = AsyncMock(return_value=wanxin_tests.receipt())
    monkeypatch.setattr(wanxin, "send_game_command", sender)
    owner = inject_sql_value(wanxin_tests.OWNER, FIELDS[2], raw)
    before = copy.deepcopy(owner)
    assert not asyncio.run(wanxin_tests.deliver(wanxin_tests.visit_event()))
    assert owner == before
    asyncio.run(wanxin_tests.schedule())
    sender.assert_not_awaited()
    with state_module.use_identity(wanxin_tests.OWNER):
        assert wanxin.get_wanxin_ui_state()["unresolved_invalid"]
        assert "fixture-secret" not in json.dumps(wanxin.get_wanxin_ui_state())
        assert "fixture-secret" not in json.dumps(ui.get_identity_ui_snapshot(wanxin_tests.OWNER))


@pytest.mark.parametrize("raw", ["[]", BROKEN, b"\xff{", '{"invalid":true}'])
def test_wanxin_corruption_cannot_start_new_work(wanxin_env, monkeypatch, raw):
    sender = AsyncMock(return_value=wanxin_tests.receipt())
    monkeypatch.setattr(wanxin, "send_game_command", sender)
    inject_sql_value(wanxin_tests.OWNER, FIELDS[2], raw)
    asyncio.run(wanxin_tests.schedule())
    sender.assert_not_awaited()


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("raw", [None, "null", "[]", '"unexpected"', "false", "5", BROKEN, b"\xff{"])
def test_owned_codec_preserves_corrupt_source(field, raw):
    loaded = persistence._deserialize_db_value(field, raw)
    assert loaded and loaded.get("invalid") is True
    if isinstance(raw, bytes):
        assert bytes.fromhex(loaded["raw_bytes_hex"]) == raw
    else:
        assert loaded["raw_json"] == raw
    assert persistence._deserialize_db_value(field, persistence._serialize_db_value(field, loaded)) == loaded


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("value", [None, [], False, 0, "unexpected"])
def test_saved_invalid_memory_is_not_an_empty_owned_record(field, value):
    loaded = persistence._deserialize_db_value(field, persistence._serialize_db_value(field, value))
    assert loaded.get("invalid") is True
    assert json.loads(loaded["raw_json"]) == value


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("value", [{}, {"invalid": True}, {"unresolved": {"op_id": "retained"}}])
def test_owned_codec_does_not_reinterpret_objects(field, value):
    assert persistence._deserialize_db_value(field, persistence._serialize_db_value(field, value)) == value


@pytest.mark.parametrize("value", [None, [], False, 0, "unexpected"])
def test_wanxin_explicit_invalid_memory_is_not_default_construction(value):
    observed = wanxin.normalize_wanxin_observation(value)
    assert observed["unresolved_invalid"]
    assert observed["invalid_observation"] == value
    assert wanxin.normalize_wanxin_observation(json.loads(json.dumps(observed))) == observed
    assert not wanxin.normalize_wanxin_observation().get("unresolved_invalid")
    assert not wanxin.normalize_wanxin_observation({}).get("unresolved_invalid")


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("raw", ["[]", BROKEN, b"\xff{"])
def test_corruption_survives_unrelated_sqlite_save(rebirth_env, field, raw):
    identity = inject_sql_value(rebirth_tests.IDENTITY, field, raw)
    retained = copy.deepcopy(identity[field])
    identity["pet_enabled"] = not identity["pet_enabled"]
    assert persistence.save_state()
    assert persistence.load_state()
    assert state_module.state[field] == retained
    encoded = persistence.get_db_conn().execute(
        f"SELECT {field} FROM identity_runtime_state WHERE send_as_id = ?", (rebirth_tests.IDENTITY,),
    ).fetchone()[0]
    assert json.loads(encoded) == retained


@pytest.mark.parametrize("raw", [None, [], False, 0, "unexpected"])
def test_wanxin_invalid_memory_survives_native_initial_check_and_config(wanxin_env, monkeypatch, raw):
    sender = AsyncMock(return_value=wanxin_tests.receipt())
    monkeypatch.setattr(wanxin, "send_game_command", sender)
    monkeypatch.setattr(wanxin, "save_state", persistence.save_state)
    wanxin_env.owner[FIELDS[2]] = copy.deepcopy(raw)
    with state_module.use_identity(wanxin_tests.OWNER):
        wanxin.schedule_wanxin_initial_check(wanxin_tests.NOW - 100)
        assert wanxin.set_wanxin_config({"publish_enabled": True})[0]
    assert persistence.load_state()
    owner = state_module.get_identity_state(wanxin_tests.OWNER)
    assert owner[FIELDS[2]]["invalid_observation"] == raw
    asyncio.run(wanxin_tests.schedule())
    sender.assert_not_awaited()
    assert owner[FIELDS[2]]["invalid_observation"] == raw


@pytest.mark.parametrize("status", ["corrupt", "sent", "complete"])
def test_wanxin_native_control_preserves_owned_history(wanxin_env, monkeypatch, status):
    sender = AsyncMock(return_value=wanxin_tests.receipt())
    monkeypatch.setattr(wanxin, "send_game_command", sender)
    if status == "corrupt":
        owner = inject_sql_value(wanxin_tests.OWNER, FIELDS[2], BROKEN)
    else:
        asyncio.run(wanxin_tests.schedule())
        owner = wanxin_env.owner
        sender.assert_awaited_once()
        pending = copy.deepcopy(owner[FIELDS[2]]["pending"])
        owner["pending_tasks"][(wanxin_tests.CHAT, 100)] = {
            "cmd": wanxin.CMD_WANXIN_VISIT, "family": "wanxin_visit",
            "chat_id": wanxin_tests.CHAT, "account_id": wanxin_tests.OWNER,
            "op_id": pending["op_id"], "sent_at": wanxin_tests.NOW,
        }
        if status == "complete":
            assert asyncio.run(wanxin_tests.deliver(wanxin_tests.visit_event()))
    before = copy.deepcopy(owner[FIELDS[2]])
    shared = copy.deepcopy(owner["pending_tasks"])
    assert asyncio.run(control.set_module_enabled(wanxin.WANXIN_MODULE_NAME, False, wanxin_tests.OWNER))[0]
    assert owner[FIELDS[2]] == before
    assert owner["pending_tasks"] == shared
    assert asyncio.run(control.set_module_enabled(wanxin.WANXIN_MODULE_NAME, True, wanxin_tests.OWNER))[0]
    assert persistence.load_state()
    owner = state_module.get_identity_state(wanxin_tests.OWNER)
    for key, value in before.items():
        assert owner[FIELDS[2]][key] == value
    sender.reset_mock()
    asyncio.run(wanxin_tests.schedule(wanxin_tests.NOW + 100))
    sender.assert_not_awaited()
    if status == "corrupt":
        with state_module.use_identity(wanxin_tests.OWNER):
            assert wanxin.get_wanxin_ui_state()["unresolved_invalid"]
    elif status == "sent":
        assert asyncio.run(wanxin_tests.deliver(wanxin_tests.visit_event()))
        assert not owner["pending_tasks"]
    else:
        due = owner[FIELDS[2]]["next_visit_time"]
        assert asyncio.run(wanxin_tests.deliver(wanxin_tests.visit_event()))
        assert owner[FIELDS[2]]["next_visit_time"] == due


@pytest.mark.parametrize("raw", ["[]", BROKEN, '{"invalid":true}', '{"receipts":[]}'])
def test_corrupt_result_ledger_blocks_native_rift_dispatch_and_preparation(dispatch_env, raw):
    identity = inject_sql_value(dispatch_tests.IDENTITY, FIELDS[1], raw)
    retained = copy.deepcopy(identity)
    asyncio.run(explore_rift.run_explore_rift_scheduler(dispatch_tests.NOW))
    dispatch_env.send.assert_not_awaited()
    dispatch_env.prepare.assert_not_awaited()
    assert identity == retained
    assert explore_rift.has_unresolved_explore_rift()


@pytest.mark.parametrize("value", [None, [], {"invalid": True}, {"receipts": []}])
def test_queued_rift_rechecks_result_ledger_before_dispatch(dispatch_env, value):
    async def queued(command, **kwargs):
        assert kwargs["operation_check"]()
        dispatch_env.identity[FIELDS[1]] = copy.deepcopy(value)
        assert not kwargs["operation_check"]()
        return None

    dispatch_env.send.side_effect = queued
    asyncio.run(explore_rift.run_explore_rift_scheduler(dispatch_tests.NOW))
    dispatch_env.send.assert_awaited_once()
    assert dispatch_env.identity[FIELDS[1]] == value


def test_result_corruption_during_preparation_does_not_dispatch(dispatch_env):
    async def prepare(*args, **kwargs):
        dispatch_env.identity[FIELDS[1]] = {"invalid": True}
        return True

    dispatch_env.prepare.side_effect = prepare
    asyncio.run(explore_rift.run_explore_rift_scheduler(dispatch_tests.NOW))
    dispatch_env.send.assert_not_awaited()


@pytest.mark.parametrize("field", FIELDS[:2])
def test_rift_controls_preserve_corrupt_evidence_and_timers(dispatch_env, field):
    identity = inject_sql_value(dispatch_tests.IDENTITY, field, BROKEN)
    retained = copy.deepcopy(identity)
    for enabled in (False, True):
        assert asyncio.run(control.set_module_enabled(dispatch_tests.SOURCE, enabled, dispatch_tests.IDENTITY))[0]
        retained["explore_rift_enabled"] = enabled
        assert identity == retained
    assert persistence.load_state()
    asyncio.run(explore_rift.run_explore_rift_scheduler(dispatch_tests.NOW + 86400))
    dispatch_env.send.assert_not_awaited()


@pytest.mark.parametrize("field", FIELDS[:2])
def test_corrupt_rift_record_does_not_disable_healthy_wanxin(wanxin_env, monkeypatch, field):
    sender = AsyncMock(return_value=wanxin_tests.receipt())
    monkeypatch.setattr(wanxin, "send_game_command", sender)
    owner = inject_sql_value(wanxin_tests.OWNER, field, BROKEN)
    retained = copy.deepcopy(owner[field])
    asyncio.run(wanxin_tests.schedule())
    sender.assert_awaited_once()
    assert asyncio.run(wanxin_tests.deliver(wanxin_tests.visit_event()))
    assert owner[field] == retained


def test_corrupt_wanxin_does_not_disable_healthy_rebirth(rebirth_env):
    identity = inject_sql_value(rebirth_tests.IDENTITY, FIELDS[2], BROKEN)
    retained = copy.deepcopy(identity[FIELDS[2]])
    rebirth_tests.run()
    rebirth_env.send.assert_awaited_once()
    assert asyncio.run(rebirth_tests.deliver(rebirth_tests.INTACT))
    assert not identity["explore_rift_rebirth_required"]
    assert identity[FIELDS[2]] == retained


@pytest.mark.parametrize("field", FIELDS[:2])
def test_corrupt_rift_status_explains_hold_without_exposing_raw_source(dispatch_env, field):
    inject_sql_value(dispatch_tests.IDENTITY, field, BROKEN)
    status = explore_rift.get_explore_rift_status_text()
    assert "\u8bb0\u5f55\u5f02\u5e38" in status
    assert "fixture-secret" not in status


@pytest.mark.parametrize("marker", [
    {"invalid": False}, {"raw_json": BROKEN}, {"raw_bytes_hex": "ff7b"},
    {"invalid_observation": []},
])
def test_wanxin_retained_corruption_cannot_be_dismissed_by_a_false_flag(marker):
    observed = wanxin.normalize_wanxin_observation({**marker, "unresolved_invalid": False})
    assert observed["unresolved_invalid"]
    for key, value in marker.items():
        assert observed[key] == value
    assert wanxin.normalize_wanxin_observation(observed) == observed


def test_corrupt_result_ledger_does_not_block_an_owned_rebirth_result(rebirth_env):
    rebirth_tests.run()
    expected_operation = copy.deepcopy(rebirth_tests.operation())
    identity = inject_sql_value(rebirth_tests.IDENTITY, FIELDS[1], BROKEN)
    retained = copy.deepcopy(identity[FIELDS[1]])
    assert rebirth_tests.operation() == expected_operation
    assert asyncio.run(rebirth_tests.deliver(rebirth_tests.INTACT))
    assert rebirth_tests.operation()["status"] == "complete"
    assert not identity["explore_rift_rebirth_required"]
    rebirth_env.send.assert_awaited_once()
    assert identity[FIELDS[1]] == retained


def test_native_tianxing_preparation_rejects_corrupt_rift_ledger(dispatch_env, monkeypatch):
    dispatch_env.identity[FIELDS[1]] = {"invalid": True}
    preflight = Mock(side_effect=AssertionError("invalid ledger reached route planning"))
    monkeypatch.setattr(explore_rift, "build_tianxing_route_preflight_plan", preflight)
    assert not asyncio.run(prepare_rift_route(dispatch_tests.NOW))
    preflight.assert_not_called()


def test_native_tianxing_preparation_rechecks_ledger_after_await(dispatch_env, monkeypatch):
    preflight = Mock(return_value={"stage": "prediction_conflict"})
    monkeypatch.setattr(explore_rift, "build_tianxing_route_preflight_plan", preflight)

    async def consume(*args, **kwargs):
        assert kwargs["operation_check"]()
        dispatch_env.identity[FIELDS[1]] = {"invalid": True}
        assert not kwargs["operation_check"]()
        return {"active": False}

    monkeypatch.setattr(explore_rift, "run_tianxing_consume_craft_prediction", consume)
    assert not asyncio.run(prepare_rift_route(dispatch_tests.NOW))
    preflight.assert_called_once()
    dispatch_env.send.assert_not_awaited()
