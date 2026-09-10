import copy
import sqlite3

import pytest

from model import cultivation_accounting as cultivation
from model import persistence, profile_observation
from model import state as state_module
from test_resource_accounting import CHAT, point


IDENTITY = 990650001
ACCOUNT = 6501


@pytest.fixture
def env(monkeypatch, tmp_path):
    saved = copy.deepcopy(state_module._meta_state)
    monkeypatch.setattr(persistence, "DB_FILE", str(tmp_path / "resources.db"))
    for name, initial in {
        "_db_conn": None, "_db_initialized": False,
        "_schema_columns_ensured_key": None, "_schema_columns_ensured_version": None,
        "_persistence_snapshot_db_key": "", "_persisted_meta_snapshot": {}, "_persisted_identity_snapshots": {},
        "_state_dirty": False, "_last_flush_time": 0, "_last_save_failed_at": 0.0, "_last_save_error": "",
    }.items():
        monkeypatch.setattr(persistence, name, initial)
    monkeypatch.setattr(persistence, "_write_live_guard_backup", lambda *_args, **_kwargs: None)
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    state_module.set_identity_account(IDENTITY, ACCOUNT)
    try:
        yield state_module.get_identity_state(IDENTITY)
    finally:
        if persistence._db_conn is not None:
            persistence._db_conn.close()
        state_module._meta_state.clear()
        state_module._meta_state.update(saved)


def snapshot(value=500000, at=100, msg_id=10, *, identity=IDENTITY, evidence=None):
    return profile_observation.apply_profile_observation(
        identity, {"xiuwei_current": value}, at, evidence=evidence or point(at, msg_id)["evidence"],
    )


def delta(key="duel:100", amount=-60000, start=110, end=120, *, chat=CHAT, identity=IDENTITY):
    return cultivation.apply_cultivation_delta(identity, key, amount, point(start, 100, chat), point(end, 101, chat))


def value():
    return state_module.get_send_as_profile(IDENTITY)["xiuwei_current"]


def test_known_deltas_and_delayed_absolute_profiles_share_one_projection(env):
    assert snapshot()
    assert delta()
    assert value() == 440000
    assert snapshot(at=105, msg_id=20) == {"xiuwei_current": 440000}
    assert value() == 440000
    assert delta("duel:200", start=125, end=130, chat=CHAT - 1)
    assert value() == 380000
    assert cultivation.cultivation_balance(IDENTITY) == {"status": "ready", "value": 380000}


def test_newer_profile_includes_late_losses_without_changing_its_clock(env):
    assert snapshot(200000, 130, 200)
    before = copy.deepcopy(env["identity_profile_observed_at"])
    assert delta()
    assert value() == 200000
    assert env["identity_profile_observed_at"] == before
    assert not delta()


def test_overlap_cannot_authorize_spending_and_a_later_profile_recovers(env):
    assert snapshot()
    assert delta(end=130)
    assert snapshot(440000, 120, 200)
    assert cultivation.cultivation_balance(IDENTITY)["status"] == "overlapping_observation"
    assert snapshot(440000, 140, 300)
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 440000


def test_conflicting_rejected_profile_is_still_recorded_as_unsafe(env):
    assert snapshot()
    assert snapshot(400000) == {}
    assert value() == 500000
    assert cultivation.cultivation_balance(IDENTITY)["status"] == "conflicting_snapshot"
    assert snapshot(400000, 130, 200)
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 400000


def test_api_display_is_not_a_spending_baseline_or_overwritten_by_late_delta(env):
    assert snapshot()
    assert snapshot(200000, 130, evidence={"source": "api", "requested_at": 130.5})
    assert value() == 200000
    assert cultivation.cultivation_balance(IDENTITY)["status"] == "unverified_profile"
    assert delta()
    assert value() == 200000
    assert snapshot(200000, 140, 200)
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 200000


def test_legacy_profile_and_unknown_start_never_create_spending_authority(env):
    state_module.update_send_as_profile(IDENTITY, xiuwei_current=500000)
    assert cultivation.cultivation_balance(IDENTITY)["status"] == "no_baseline"
    assert cultivation.apply_cultivation_delta(IDENTITY, "duel:100", -60000, None, point(120, 101))
    assert value() == 500000
    assert cultivation.cultivation_balance(IDENTITY)["value"] is None
    assert snapshot(440000, 130, 200)
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 440000


def test_account_rebinding_does_not_relabel_old_facts(env):
    assert snapshot()
    before = copy.deepcopy(env[cultivation.STATE_KEY])
    state_module.set_identity_account(IDENTITY, ACCOUNT + 1)
    assert cultivation.cultivation_balance(IDENTITY)["status"] == "account_changed"
    assert not delta()
    assert snapshot(400000, 140, 200)
    assert env[cultivation.STATE_KEY] == before
    assert cultivation.cultivation_balance(IDENTITY)["value"] is None


def test_external_profile_writes_cannot_be_reclassified_as_verified_balance(env):
    assert snapshot()
    state_module.update_send_as_profile(IDENTITY, xiuwei_current=900000)
    assert cultivation.cultivation_balance(IDENTITY)["status"] == "unverified_profile"
    assert delta()
    assert value() == 900000
    assert cultivation.cultivation_balance(IDENTITY)["value"] is None
    assert snapshot(440000, 130, 200)
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 440000


def test_projected_overflow_does_not_break_profile_persistence(env):
    assert snapshot()
    assert delta(amount=2 ** 63 - 1)
    assert value() == 500000
    assert cultivation.cultivation_balance(IDENTITY)["value"] is None
    assert persistence.save_state()
    assert snapshot(at=105, msg_id=20)
    assert value() == 500000
    assert cultivation.cultivation_balance(IDENTITY)["value"] is None
    assert persistence.save_state()


def test_unpersistable_cultivation_snapshot_is_not_written_to_the_profile(env):
    assert snapshot()
    assert snapshot(2 ** 63, 130, 200) is None
    assert value() == 500000
    assert persistence.save_state()


def test_stale_card_cannot_adopt_an_unverified_profile_write(env):
    assert snapshot()
    state_module.update_send_as_profile(IDENTITY, xiuwei_current=200000)
    assert snapshot(900000, 90, 9) == {}
    assert delta()
    assert value() == 200000
    assert cultivation.cultivation_balance(IDENTITY)["status"] == "unverified_profile"


def test_pause_and_module_toggles_do_not_discard_completed_resource_facts(env):
    env["duel_enabled"] = False
    env["yinluo_enabled"] = False
    state_module._meta_state["global_enabled"] = False
    assert snapshot()
    assert delta()
    assert value() == 440000


def test_actual_sqlite_reload_retains_ledger_profile_and_order(env, monkeypatch, tmp_path):
    monkeypatch.setattr(persistence, "DB_FILE", str(tmp_path / "resources.db"))
    assert snapshot()
    assert persistence.save_state()
    assert delta()
    assert persistence.save_state()
    before = copy.deepcopy(env[cultivation.STATE_KEY])
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    assert state_module.get_identity_state(IDENTITY)[cultivation.STATE_KEY] == before
    assert value() == 440000
    assert not delta()
    assert snapshot(at=105, msg_id=20)
    assert value() == 440000


@pytest.mark.parametrize("stored", [None, [], {"account_id": True}, {"ledger": {}}])
def test_corrupt_resource_state_never_becomes_an_empty_legacy_ledger(env, stored, monkeypatch, tmp_path):
    monkeypatch.setattr(persistence, "DB_FILE", str(tmp_path / "corrupt.db"))
    env[cultivation.STATE_KEY] = stored
    assert cultivation.cultivation_balance(IDENTITY)["status"] == "corrupt"
    assert snapshot()
    assert not delta()
    assert cultivation.cultivation_balance(IDENTITY)["status"] == "corrupt"
    assert persistence.save_state()
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    assert cultivation.cultivation_balance(IDENTITY)["status"] == "corrupt"


def test_invalid_database_json_does_not_reset_resource_history(env, monkeypatch, tmp_path):
    path = str(tmp_path / "invalid.db")
    monkeypatch.setattr(persistence, "DB_FILE", path)
    assert snapshot()
    assert persistence.save_state()
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE identity_runtime_state SET xiuwei_accounting = ? WHERE send_as_id = ?", ("{bad", IDENTITY))
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    assert cultivation.cultivation_balance(IDENTITY)["status"] == "corrupt"


def test_profile_and_ledger_roll_back_together_after_database_write_failure(env):
    assert snapshot()
    assert persistence.save_state()
    original = copy.deepcopy(env[cultivation.STATE_KEY])
    assert delta()
    connection = persistence.get_db_conn()

    def reject_ledger_write(operation, table, _column, *_args):
        if operation == sqlite3.SQLITE_INSERT and table == "identity_runtime_state":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    connection.set_authorizer(reject_ledger_write)
    try:
        assert not persistence.save_state()
    finally:
        connection.set_authorizer(None)
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    assert state_module.get_identity_state(IDENTITY)[cultivation.STATE_KEY] == original
    assert value() == 500000
    assert delta()
    assert persistence.save_state()
    assert value() == 440000
