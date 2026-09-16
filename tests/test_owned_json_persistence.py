import asyncio
import copy
import json
from unittest.mock import AsyncMock, Mock

import pytest

from model import control, persistence, state as state_module, ui
from model.features import concubine, second_soul, tianti
from tests import test_concubine_greet_lifecycle as greet_tests


base_env = greet_tests.env
ID, NOW = greet_tests.ID, greet_tests.NOW
READERS = {
    "concubine_status_query": concubine._status_query_record,
    "concubine_gift_actions": concubine.affinity_actions.records,
    "concubine_greet_action": concubine.affinity_actions.records,
    "concubine_fragment_actions": concubine.fragment_actions.records,
    "concubine_voyage_actions": concubine.voyage_actions.records,
    "concubine_tianji_action": concubine.divination_actions.record,
    "concubine_heart_session": concubine.heart_actions.record,
    "concubine_reacquire_action": concubine.reacquire_actions.record,
    "concubine_external_observation": concubine.external_events.record,
    "second_soul_commands": second_soul._command_records,
    "tianti_commands": tianti._tianti_commands,
}
CONCUBINE_FIELDS = tuple(key for key in READERS if key.startswith("concubine_"))
MODULE_KEYS = {
    "concubine_gift_actions": "concubine_tianji_enabled",
    "concubine_greet_action": "concubine_tianji_enabled",
    "concubine_tianji_action": "concubine_tianji_enabled",
    "concubine_heart_session": "concubine_heart_enabled",
    "concubine_voyage_actions": "concubine_voyage_enabled",
}
BROKEN = '{"op_id":"retained-operation","token":"fixture-secret"'


@pytest.fixture
def env(base_env, monkeypatch):
    for name, value in {
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
    monkeypatch.setattr(concubine, "save_state", persistence.save_state)
    monkeypatch.setattr(control, "save_state", persistence.save_state)
    monkeypatch.setattr(control, "console_log", Mock())
    monkeypatch.setattr(second_soul, "send_game_command", AsyncMock())
    monkeypatch.setattr(second_soul, "send_audit_log", AsyncMock())
    monkeypatch.setattr(second_soul, "save_state", persistence.save_state)
    monkeypatch.setattr(tianti, "send_game_command", AsyncMock())
    try:
        yield base_env
    finally:
        if persistence._db_conn is not None:
            persistence._db_conn.close()


def reload_identity(env):
    assert persistence.load_state()
    env.identity = state_module.get_identity_state(ID)


def inject_sql_value(env, field, value):
    assert field in READERS
    assert persistence.save_state()
    conn = persistence.get_db_conn()
    conn.execute(f"UPDATE identity_runtime_state SET {field} = ? WHERE send_as_id = ?", (value, ID))
    conn.commit()
    reload_identity(env)


@pytest.mark.parametrize("field", READERS)
@pytest.mark.parametrize("raw", [None, "null", "[]", '"unexpected"', "false", "5", BROKEN, b"\xff{"])
def test_invalid_owned_json_retains_evidence_instead_of_becoming_idle(field, raw):
    value = persistence._deserialize_db_value(field, raw)
    assert value and value.get("invalid") is True
    if isinstance(raw, bytes):
        assert bytes.fromhex(value["raw_bytes_hex"]) == raw
    else:
        assert value["raw_json"] == raw
    encoded = persistence._serialize_db_value(field, value)
    assert persistence._deserialize_db_value(field, encoded) == value


@pytest.mark.parametrize("field", READERS)
@pytest.mark.parametrize("value", [None, [], False, 0, "unexpected"])
def test_in_memory_invalid_records_cannot_be_saved_as_empty(field, value):
    encoded = persistence._serialize_db_value(field, value)
    loaded = persistence._deserialize_db_value(field, encoded)
    assert loaded and loaded.get("invalid") is True
    assert json.loads(loaded["raw_json"]) == value


@pytest.mark.parametrize("field", READERS)
@pytest.mark.parametrize("value", [{}, {"invalid": True}, {"unresolved": {"op_id": "retained"}}])
def test_object_records_are_not_repaired_or_reinterpreted_by_the_codec(field, value):
    assert persistence._deserialize_db_value(field, persistence._serialize_db_value(field, value)) == value


@pytest.mark.parametrize("field,expected", [("small_world_panel_snapshot", {}), ("quiz_options", {})])
def test_disposable_json_defaults_are_unchanged(field, expected):
    assert persistence._deserialize_db_value(field, "not-json") == expected
    assert persistence._deserialize_db_value(field, persistence._serialize_db_value(field, None)) == expected


@pytest.mark.parametrize("field", READERS)
@pytest.mark.parametrize("raw", ["[]", BROKEN, b"\xff{"])
def test_invalid_sqlite_record_survives_unrelated_save_and_reload(env, field, raw):
    inject_sql_value(env, field, raw)
    expected = copy.deepcopy(env.identity[field])
    with state_module.use_identity(ID):
        assert READERS[field]() is None
    env.identity["pet_enabled"] = not env.identity["pet_enabled"]
    assert persistence.save_state()
    reload_identity(env)
    assert env.identity[field] == expected
    with state_module.use_identity(ID):
        assert READERS[field]() is None
    stored = persistence.get_db_conn().execute(
        f"SELECT {field} FROM identity_runtime_state WHERE send_as_id = ?", (ID,)
    ).fetchone()[0]
    retained = json.loads(stored)
    if isinstance(raw, bytes):
        assert bytes.fromhex(retained["raw_bytes_hex"]) == raw
    else:
        assert retained["raw_json"] == raw


@pytest.mark.parametrize("field", CONCUBINE_FIELDS)
@pytest.mark.parametrize("source", ["memory_null", "sql_truncated"])
def test_concubine_corruption_stays_blocked_through_native_controls_and_scheduler(env, field, source):
    module_key = MODULE_KEYS.get(field, "concubine_enabled")
    env.identity[module_key] = True
    if source == "memory_null":
        env.identity[field] = None
        assert persistence.save_state()
        reload_identity(env)
    else:
        inject_sql_value(env, field, BROKEN)
    expected = copy.deepcopy(env.identity[field])
    with state_module.use_identity(ID):
        assert READERS[field]() is None
        concubine.restore_concubine_runtime(NOW)
        asyncio.run(concubine.run_concubine_scheduler(NOW))
        snapshot = ui.get_identity_ui_snapshot(ID)
        assert "fixture-secret" not in json.dumps(snapshot, ensure_ascii=False)
        assert "\u5f02\u5e38" in concubine.get_concubine_status_text()
    module_name = next(name for name, key in control.MODULE_KEY_MAP.items() if key == module_key)
    assert asyncio.run(control.set_module_enabled(module_name, False, ID))[0]
    assert asyncio.run(control.set_module_enabled(module_name, True, ID))[0]
    reload_identity(env)
    with state_module.use_identity(ID):
        assert READERS[field]() is None
        concubine.restore_concubine_runtime(NOW + 86400)
        asyncio.run(concubine.run_concubine_scheduler(NOW + 86400))
    assert env.identity[field] == expected
    assert not asyncio.run(greet_tests.send_greet(env))
    assert not asyncio.run(greet_tests.reply(env))
    assert env.identity[field] == expected
    env.send.assert_not_awaited()


@pytest.mark.parametrize("field", ["second_soul_commands", "tianti_commands"])
def test_adjacent_invalid_journals_block_native_dispatch_and_scheduler(env, field):
    env.identity["second_soul_enabled" if field == "second_soul_commands" else "tianti_enabled"] = True
    inject_sql_value(env, field, BROKEN)
    expected = copy.deepcopy(env.identity[field])
    with state_module.use_identity(ID):
        if field == "second_soul_commands":
            assert second_soul._owns_identity(second_soul._capture_owner(), enabled=True)
            assert second_soul._command_ownership_error()
            assert not asyncio.run(second_soul._send_owned_command("train", ID, NOW))
            second_soul.restore_second_soul_runtime(NOW + 86400)
            asyncio.run(second_soul.run_second_soul_scheduler(NOW + 86400))
            second_soul.send_game_command.assert_not_awaited()
        else:
            owner = tianti._capture_tianti_owner()
            assert tianti._owns_tianti(owner, sending=True)
            assert tianti._tianti_native_block_reason()
            assert not asyncio.run(tianti._send_tianti_command("climb", owner, NOW))
            asyncio.run(tianti.run_tianti_scheduler(NOW + 86400))
            tianti.send_game_command.assert_not_awaited()
    assert env.identity[field] == expected
    assert persistence.save_state()
    reload_identity(env)
    with state_module.use_identity(ID):
        assert READERS[field]() is None
    assert env.identity[field] == expected


@pytest.mark.parametrize("field", ["second_soul_commands", "tianti_commands"])
def test_other_invalid_module_does_not_block_healthy_greeting(env, field):
    inject_sql_value(env, field, BROKEN)
    expected = copy.deepcopy(env.identity[field])
    with state_module.use_identity(ID):
        assert READERS[field]() is None
    assert asyncio.run(greet_tests.send_greet(env))
    assert asyncio.run(greet_tests.reply(env))
    env.send.assert_awaited_once()
    assert persistence.save_state()
    reload_identity(env)
    assert env.identity[field] == expected
    assert env.identity[greet_tests.FIELD]["status"] == "complete"


def test_genuine_empty_records_still_allow_greeting_after_sqlite_reload(env):
    assert persistence.save_state()
    reload_identity(env)
    assert all(env.identity[field] == {} for field in READERS)
    assert asyncio.run(greet_tests.send_greet(env))
    env.send.assert_awaited_once()
