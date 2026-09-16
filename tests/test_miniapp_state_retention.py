import copy

import pytest

from model import miniapp_state, persistence
from model import state as state_module
from model.features import cave_treasure_runtime as cave


GAME_KEYS = (
    "cave_entry_directory", "cave_deep_retreat", "cave_small_world", "cave_yuanying",
    "cave_treasure", "fate_cards", "wild_training", "tower", "tree",
)
NOW = 1_700_000_000.0


@pytest.fixture
def registry(monkeypatch):
    previous = copy.deepcopy(state_module._meta_state)
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    monkeypatch.setattr(miniapp_state, "save_state", lambda: True)
    try:
        yield
    finally:
        state_module._meta_state.clear()
        state_module._meta_state.update(previous)


def round_trip():
    _get, encode, restore = persistence._META_STATE_CODEC["miniapp_state_records"]
    encoded = encode(state_module.get_miniapp_state_records())
    state_module.set_miniapp_state_records({})
    restore(encoded)
    return state_module.get_miniapp_state_records()


def write(identity_id, game_key, value, *, now=NOW):
    return miniapp_state.record_miniapp_state(identity_id, game_key, value, now=now, persist=False)


def test_registered_game_slots_survive_more_than_the_summary_limit(registry):
    expected = {}
    for identity_id in range(1001, 1041):
        state_module.ensure_identity_registered(identity_id)
        for index, game_key in enumerate(GAME_KEYS):
            result = write(identity_id, game_key, {"marker": identity_id}, now=NOW + identity_id + index)
            expected[result["record_key"]] = copy.deepcopy(result["record"])

    assert len(expected) > miniapp_state.MINIAPP_STATE_RECORD_LIMIT
    records = round_trip()
    assert {key: row for key, row in records.items() if key != "_meta"} == expected


def test_registered_records_do_not_compete_with_disposable_summaries(registry):
    state_module.ensure_identity_registered(1001)
    for game_key in GAME_KEYS:
        write(1001, game_key, {"outcome_unknown": True})
    for number in range(miniapp_state.MINIAPP_STATE_RECORD_LIMIT + 20):
        write(1001, f"diagnostic_{number}", {"marker": number}, now=NOW + number + 1)

    records = round_trip()
    for game_key in GAME_KEYS:
        assert records[f"1001:{game_key}"]["state"]["outcome_unknown"] is True
    summaries = {key: row for key, row in records.items() if ":diagnostic_" in key}
    assert len(summaries) == miniapp_state.MINIAPP_STATE_RECORD_LIMIT
    assert "1001:diagnostic_0" not in summaries
    assert "1001:diagnostic_319" in summaries
    assert len(records) == len(GAME_KEYS) + miniapp_state.MINIAPP_STATE_RECORD_LIMIT + 1


def test_unknown_flags_cannot_reserve_arbitrary_game_slots(registry):
    state_module.ensure_identity_registered(1001)
    for number in range(miniapp_state.MINIAPP_STATE_RECORD_LIMIT + 20):
        write(1001, f"unsupported_{number}", {"outcome_unknown": True}, now=NOW + number)

    records = round_trip()
    assert len(records) == miniapp_state.MINIAPP_STATE_RECORD_LIMIT + 1
    assert "1001:unsupported_0" not in records


def test_repeated_updates_replace_a_slot_without_history_growth(registry):
    state_module.ensure_identity_registered(1001)
    for number in range(miniapp_state.MINIAPP_STATE_RECORD_LIMIT + 20):
        write(1001, "fate_cards", {"challenge_date": str(number), "status": "settled"}, now=NOW + number)

    records = round_trip()
    assert set(records) == {"_meta", "1001:fate_cards"}
    assert records["1001:fate_cards"]["state"]["challenge_date"] == "319"


def test_disabled_identity_keeps_unknown_operation_but_removal_reclaims_slots(registry):
    for identity_id in (1001, 10010):
        state_module.ensure_identity_registered(identity_id)
        write(identity_id, "cave_deep_retreat", {"outcome_unknown": True})
    state_module.set_identity_enabled(1001, False)
    for number in range(miniapp_state.MINIAPP_STATE_RECORD_LIMIT + 1):
        write(10010, f"diagnostic_{number}", {"marker": number}, now=NOW + number + 1)

    assert round_trip()["1001:cave_deep_retreat"]["state"]["outcome_unknown"]
    assert state_module.remove_identity(1001)
    records = round_trip()
    assert "1001:cave_deep_retreat" not in records
    assert records["10010:cave_deep_retreat"]["state"]["outcome_unknown"]
    state_module.ensure_identity_registered(1001)
    assert "1001:cave_deep_retreat" not in state_module.get_miniapp_state_records()


def test_late_write_cannot_recreate_removed_identity_evidence(registry):
    state_module.ensure_identity_registered(1001)
    write(1001, "fate_cards", {"status": "settled"})
    state_module.remove_identity(1001)
    before = copy.deepcopy(state_module.get_miniapp_state_records())
    result = write(1001, "fate_cards", {"status": "choose_unknown"})

    assert not result["changed"]
    assert state_module.get_miniapp_state_records() == before


def test_pruning_does_not_release_treasure_unknown_hold_after_reload(registry):
    state_module.ensure_identity_registered(1001)
    write(1001, "cave_treasure", {
        "outcome_unknown": True, "outcome_unknown_day": cave.get_day_key(NOW),
    })
    for number in range(miniapp_state.MINIAPP_STATE_RECORD_LIMIT + 1):
        write(1001, f"diagnostic_{number}", {"marker": number}, now=NOW + number + 1)

    round_trip()
    assert cave._cave_treasure_unknown_hold(1001, NOW + 1)


def test_operational_record_capacity_tracks_registered_slots(registry):
    state_module.ensure_identity_registered(1001)
    state_module.ensure_identity_registered(1002)
    write(1001, "fate_cards", {"status": "settled"})
    snapshot = miniapp_state.get_miniapp_state_snapshot(now=NOW)

    assert snapshot["meta"]["runtime_record_limit"] == 2 * len(GAME_KEYS)
    assert snapshot["meta"]["summary_record_limit"] == miniapp_state.MINIAPP_STATE_RECORD_LIMIT
    assert snapshot["meta"]["record_limit"] == 2 * len(GAME_KEYS) + miniapp_state.MINIAPP_STATE_RECORD_LIMIT


def test_pruning_cannot_reschedule_a_completed_daily_quest(registry, monkeypatch):
    from model import ui

    state_module.ensure_identity_registered(1001)
    monkeypatch.setattr(ui, "_cave_public_background_daily_done", set())
    write(1001, "fate_cards", {"challenge_date": cave.get_day_key(NOW), "status": "settled"})
    for number in range(miniapp_state.MINIAPP_STATE_RECORD_LIMIT + 1):
        write(1001, f"diagnostic_{number}", {"marker": number}, now=NOW + number + 1)
    round_trip()

    assert not ui._cave_public_background_action_due("fate_cards", 1001, NOW + 1)
    assert ui._cave_public_background_action_due("fate_cards", 1001, NOW + 86400)
