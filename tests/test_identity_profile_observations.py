import asyncio
import copy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import app, control, persistence, profile_observation, ui
from model import state as state_module
from model.storage_bag_api_client import StorageBagApiError, StorageBagApiResult
from test_passive_identity_profile import COMBINED_CARD


NOW = 1780000000.0
IDENTITY = 990630001
ACCOUNT = 7631
CHAT = -100630001
BOT = 880630001
USERNAME = "jfdffdddd"
EARLY_REALM = "\u7ed3\u4e39\u540e\u671f"
MIDDLE_REALM = "\u5143\u5a74\u4e2d\u671f"
LATE_REALM = "\u5143\u5a74\u540e\u671f"


@pytest.fixture
def env(monkeypatch):
    saved = copy.deepcopy(state_module._meta_state)
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    state_module.set_identity_account(IDENTITY, ACCOUNT)
    state_module.set_game_group_route_config({"primary_group_id": CHAT})
    state_module.set_game_bot_ids([BOT])
    state_module.update_send_as_profile(IDENTITY, username=USERNAME, realm=EARLY_REALM)
    state_module.set_storage_bag_api_config({
        "base_url": "https://example.invalid", "cookie": "session=old", "api_token": "old-token",
    })
    state_module.set_storage_bag_records({str(IDENTITY): {"items": {"untouched": 7}}})
    state_module.set_tianjige_dao_path_records({})
    clock = SimpleNamespace(now=NOW)
    monkeypatch.setattr(control.time, "time", lambda: clock.now)
    monkeypatch.setattr(control, "save_state", Mock(return_value=True))
    monkeypatch.setattr(ui, "save_state", Mock(return_value=True))
    monkeypatch.setattr(control, "send_audit_log", AsyncMock())
    monkeypatch.setattr(control, "enforce_identity_module_availability", Mock())
    monkeypatch.setattr(ui, "send_game_command", AsyncMock(side_effect=AssertionError("no game sends")))
    monkeypatch.setattr(app, "_claim_runtime_event", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(ui, "_storage_bag_api_state", {"running": False, "keepalive_running": False})
    monkeypatch.setattr(ui, "_storage_bag_api_config_generation", 0)
    try:
        yield clock
    finally:
        state_module._meta_state.clear()
        state_module._meta_state.update(saved)


def event(at, *, edited_at=None, sender=BOT, chat=CHAT):
    return SimpleNamespace(
        id=6301, sender_id=sender, chat_id=chat,
        date=datetime.fromtimestamp(at, timezone.utc),
        edit_date=datetime.fromtimestamp(edited_at, timezone.utc) if edited_at else None,
        reply_context={},
    )


def breakthrough(realm=LATE_REALM, username=USERNAME):
    return f"@{username} \u7075\u5149\u4e00\u95ea\uff0c\u6210\u529f\u7a81\u7834\u81f3\u3010{realm}\u3011\uff01"


async def dispatch_breakthrough(clock, *, at=NOW + 20, realm=LATE_REALM, edited=False, username=USERNAME):
    message = event(NOW + 1 if edited else at, edited_at=at if edited else None)
    text = breakthrough(realm, username)
    if edited:
        await app._dispatch_message_edited_realm_breakthrough(message, text, clock.now)
    else:
        handlers = tuple(item for item in app._NEW_MESSAGE_BROADCAST_HANDLERS if item[0] == "realm_breakthrough")
        await app._dispatch_broadcast_handlers(message, text, clock.now, handlers)


@pytest.mark.parametrize("edited", [False, True])
def test_native_breakthrough_prevents_delayed_profile_rollback(env, edited):
    env.now = NOW + 100

    async def scenario():
        await dispatch_breakthrough(env, edited=edited)
        assert await control.handle_passive_identity_profile_card(COMBINED_CARD, env.now, event=event(NOW + 10))

    asyncio.run(scenario())
    assert state_module.get_send_as_profile(IDENTITY)["realm"] == LATE_REALM
    assert state_module.get_identity_state(IDENTITY)["identity_profile_observed_at"]["realm"] == NOW + 20


def test_old_forward_breakthrough_cannot_override_newer_lower_profile(env):
    env.now = NOW + 100

    async def scenario():
        assert await control.handle_passive_identity_profile_card(COMBINED_CARD, env.now, event=event(NOW + 30))
        await dispatch_breakthrough(env, at=NOW + 20)

    asyncio.run(scenario())
    assert state_module.get_send_as_profile(IDENTITY)["realm"] == MIDDLE_REALM
    control.send_audit_log.assert_not_awaited()


def test_same_realm_breakthrough_still_advances_evidence_clock(env):
    state_module.update_send_as_profile(IDENTITY, realm=LATE_REALM)
    env.now = NOW + 100

    async def scenario():
        await dispatch_breakthrough(env)
        await control.handle_passive_identity_profile_card(COMBINED_CARD, env.now, event=event(NOW + 10))

    asyncio.run(scenario())
    assert state_module.get_send_as_profile(IDENTITY)["realm"] == LATE_REALM
    control.send_audit_log.assert_not_awaited()


@pytest.mark.parametrize("username", [USERNAME + "1", USERNAME + "_suffix", "other_" + USERNAME])
def test_breakthrough_does_not_match_username_prefix_or_substring(env, username):
    env.now = NOW + 100
    asyncio.run(dispatch_breakthrough(env, username=username))
    assert state_module.get_send_as_profile(IDENTITY)["realm"] == EARLY_REALM


def test_breakthrough_exact_username_wins_over_another_identity_prefix(env):
    state_module.set_identity_account(IDENTITY + 1, ACCOUNT)
    state_module.update_send_as_profile(IDENTITY + 1, username=USERNAME + "1", realm=EARLY_REALM)
    env.now = NOW + 100
    asyncio.run(dispatch_breakthrough(env, username=USERNAME + "1"))
    assert state_module.get_send_as_profile(IDENTITY)["realm"] == EARLY_REALM
    assert state_module.get_send_as_profile(IDENTITY + 1)["realm"] == LATE_REALM


def test_breakthrough_accepts_exact_casefolded_previous_username(env):
    state_module.update_send_as_profile(IDENTITY, username=USERNAME + "1")
    env.now = NOW + 100
    asyncio.run(dispatch_breakthrough(env, username=USERNAME.upper()))
    assert state_module.get_send_as_profile(IDENTITY)["realm"] == LATE_REALM


@pytest.mark.parametrize("fault", ["sender", "chat", "missing_time", "future_time", "missing_edit_time"])
def test_breakthrough_requires_trusted_source_and_server_time(env, fault):
    env.now = NOW + 100
    message = event(NOW + 20)
    if fault == "sender":
        message.sender_id += 1
    elif fault == "chat":
        message.chat_id -= 1
    elif fault == "missing_time":
        message.date = None
    elif fault == "future_time":
        message.date = datetime.fromtimestamp(NOW + 1000, timezone.utc)
    if fault == "missing_edit_time":
        asyncio.run(app._dispatch_message_edited_realm_breakthrough(message, breakthrough(), env.now))
    else:
        handlers = tuple(item for item in app._NEW_MESSAGE_BROADCAST_HANDLERS if item[0] == "realm_breakthrough")
        asyncio.run(app._dispatch_broadcast_handlers(message, breakthrough(), env.now, handlers))
    assert state_module.get_send_as_profile(IDENTITY)["realm"] == EARLY_REALM
    control.send_audit_log.assert_not_awaited()


def api_result(payload=None, *, path="/api/cultivator/" + USERNAME):
    if payload is None:
        payload = {"telegram_id": IDENTITY, "username": USERNAME, "cultivation_level": EARLY_REALM}
    return StorageBagApiResult(payload, 200, "session=rotated", "rotated-token", path)


@pytest.mark.parametrize("drift", ["account", "replace", "delete_recreate", "delete"])
def test_api_read_does_not_write_into_changed_identity_or_continue_requests(env, monkeypatch, drift):
    calls = []
    after = {}

    async def fetch(_config, path):
        calls.append(path)
        if drift == "account":
            state_module.set_identity_account(IDENTITY, ACCOUNT + 1)
        elif drift == "replace":
            state_module._meta_state["identity_states"][IDENTITY] = state_module.new_identity_state()
        else:
            state_module._meta_state["identity_ids"].remove(IDENTITY)
            state_module._meta_state["identity_states"].pop(IDENTITY, None)
            state_module._meta_state["send_as_profiles"].pop(IDENTITY, None)
            if drift == "delete_recreate":
                state_module.set_identity_account(IDENTITY, ACCOUNT)
                state_module.update_send_as_profile(IDENTITY, username=USERNAME, realm=LATE_REALM)
        after["profile"] = copy.deepcopy(state_module.get_send_as_profile(IDENTITY))
        return api_result({"telegram_id": IDENTITY, "username": USERNAME, "cultivation_level": MIDDLE_REALM})

    monkeypatch.setattr(ui, "fetch_storage_bag_result", fetch)
    ok, _message, _snapshot = asyncio.run(ui.ui_refresh_identity_from_api(IDENTITY))
    assert not ok
    assert len(calls) == 1
    assert state_module.get_send_as_profile(IDENTITY) == after["profile"]
    assert str(IDENTITY) not in state_module.get_tianjige_dao_path_records()
    if drift == "delete":
        assert not state_module.has_identity(IDENTITY)


@pytest.mark.parametrize("failure", [False, True])
def test_old_api_response_cannot_replace_new_ui_credentials(env, monkeypatch, failure):
    replacement = {"base_url": "https://changed.invalid", "cookie": "session=new", "api_token": "new-token"}

    async def fetch(_config, _path):
        ui.ui_set_storage_bag_api_config(replacement)
        if failure:
            raise StorageBagApiError("old login expired", status_code=401, auth_failed=True, cookie="session=old-response")
        return api_result()

    monkeypatch.setattr(ui, "fetch_storage_bag_result", fetch)
    before = copy.deepcopy(state_module.get_send_as_profile(IDENTITY))
    ok, _message, _snapshot = asyncio.run(ui.ui_refresh_identity_from_api(IDENTITY))
    assert not ok
    assert state_module.get_send_as_profile(IDENTITY) == before
    config = state_module.get_storage_bag_api_config()
    assert all(config[key] == value for key, value in replacement.items())
    assert not config.get("last_keepalive_error")


def test_api_read_started_before_new_card_does_not_roll_back_shared_fields(env, monkeypatch):
    async def fetch(_config, _path):
        env.now = NOW + 100
        await control.handle_passive_identity_profile_card(COMBINED_CARD, env.now, event=event(NOW + 20))
        return api_result({
            "telegram_id": IDENTITY, "username": USERNAME,
            "cultivation_level": EARLY_REALM, "cultivation_points": 17,
            "sect_name": "\u6563\u4fee",
        })

    monkeypatch.setattr(ui, "fetch_storage_bag_result", fetch)
    ok, _message, _snapshot = asyncio.run(ui.ui_refresh_identity_from_api(IDENTITY))
    assert ok
    profile = state_module.get_send_as_profile(IDENTITY)
    assert profile["realm"] == MIDDLE_REALM
    assert profile["xiuwei_current"] == 445955
    assert profile["sect_name"] == "\u51cc\u9704\u5bab"
    assert state_module.get_storage_bag_records()[str(IDENTITY)]["items"] == {"untouched": 7}


@pytest.mark.parametrize("payload", [
    {"username": "not_a_local_role", "cultivation_level": LATE_REALM},
    {"telegram_id": IDENTITY + 99, "username": USERNAME, "cultivation_level": LATE_REALM},
    {"characters": [{"username": "not_a_local_role", "cultivation_level": LATE_REALM}]},
])
def test_api_conflicting_owner_never_falls_back_to_selected_identity(env, monkeypatch, payload):
    monkeypatch.setattr(ui, "fetch_storage_bag_result", AsyncMock(return_value=api_result(payload)))
    before = copy.deepcopy(state_module.get_send_as_profile(IDENTITY))
    ok, _message, _snapshot = asyncio.run(ui.ui_refresh_identity_from_api(IDENTITY))
    assert not ok
    assert state_module.get_send_as_profile(IDENTITY) == before
    assert str(IDENTITY) not in state_module.get_tianjige_dao_path_records()


def test_api_me_missing_owner_does_not_inherit_selected_identity(env, monkeypatch):
    async def fetch(_config, path):
        if path != "/api/me":
            raise StorageBagApiError("missing", status_code=404)
        return api_result({"cultivation_level": LATE_REALM, "status": "normal"}, path=path)

    monkeypatch.setattr(ui, "fetch_storage_bag_result", fetch)
    before = copy.deepcopy(state_module.get_send_as_profile(IDENTITY))
    ok, _message, _snapshot = asyncio.run(ui.ui_refresh_identity_from_api(IDENTITY))
    assert not ok
    assert state_module.get_send_as_profile(IDENTITY) == before


@pytest.mark.parametrize("card_first", [False, True])
def test_same_second_native_messages_use_chat_scoped_message_order(env, card_first):
    env.now = NOW + 100
    card_event = event(NOW + 20)
    card_event.id = 6300

    async def card():
        await control.handle_passive_identity_profile_card(COMBINED_CARD, env.now, event=card_event)

    async def scenario():
        if card_first:
            await card()
        await dispatch_breakthrough(env)
        if not card_first:
            await card()

    asyncio.run(scenario())
    assert state_module.get_send_as_profile(IDENTITY)["realm"] == LATE_REALM


@pytest.mark.parametrize("card_first", [False, True])
def test_same_second_telegram_card_outranks_api_request_bound(env, monkeypatch, card_first):
    env.now = NOW + 0.75

    async def fetch(_config, path):
        if card_first:
            await control.handle_passive_identity_profile_card(COMBINED_CARD, env.now, event=event(NOW))
        return api_result(path=path)

    async def scenario():
        await ui.ui_refresh_identity_from_api(IDENTITY)
        if not card_first:
            await control.handle_passive_identity_profile_card(COMBINED_CARD, env.now, event=event(NOW))

    monkeypatch.setattr(ui, "fetch_storage_bag_result", fetch)
    asyncio.run(scenario())
    assert state_module.get_send_as_profile(IDENTITY)["realm"] == MIDDLE_REALM


@pytest.mark.parametrize("malformed_id", ["not-an-id", True, [], {}])
def test_malformed_explicit_api_identity_cannot_authorize_fallback(env, monkeypatch, malformed_id):
    async def fetch(_config, path):
        return api_result({"telegram_id": malformed_id, "cultivation_level": LATE_REALM}, path=path)

    monkeypatch.setattr(ui, "fetch_storage_bag_result", fetch)
    ok, _message, _snapshot = asyncio.run(ui.ui_refresh_identity_from_api(IDENTITY))
    assert not ok
    assert state_module.get_send_as_profile(IDENTITY)["realm"] == EARLY_REALM


def test_multiple_explicit_api_ids_cannot_disagree(env, monkeypatch):
    async def fetch(_config, path):
        return api_result({"telegram_id": IDENTITY, "identity_id": IDENTITY + 1, "cultivation_level": LATE_REALM}, path=path)

    monkeypatch.setattr(ui, "fetch_storage_bag_result", fetch)
    ok, _message, _snapshot = asyncio.run(ui.ui_refresh_identity_from_api(IDENTITY))
    assert not ok
    assert state_module.get_send_as_profile(IDENTITY)["realm"] == EARLY_REALM


def test_numeric_api_identity_disambiguates_shared_display_name(env, monkeypatch):
    state_module.set_identity_account(IDENTITY + 1, ACCOUNT)
    for identity_id in (IDENTITY, IDENTITY + 1):
        state_module.update_send_as_profile(identity_id, label="shared label")

    async def fetch(_config, path):
        return api_result({"telegram_id": IDENTITY, "label": "shared label", "cultivation_level": LATE_REALM}, path=path)

    monkeypatch.setattr(ui, "fetch_storage_bag_result", fetch)
    ok, _message, _snapshot = asyncio.run(ui.ui_refresh_identity_from_api(IDENTITY))
    assert ok
    assert state_module.get_send_as_profile(IDENTITY)["realm"] == LATE_REALM
    assert not state_module.get_send_as_profile(IDENTITY + 1)["realm"]


def test_api_owner_conflict_is_not_hidden_by_nested_owner_object(env, monkeypatch):
    state_module.set_identity_account(IDENTITY + 1, ACCOUNT)
    state_module.update_send_as_profile(IDENTITY + 1, username="another_local")

    async def fetch(_config, path):
        return api_result({
            "owner": {"telegram_id": IDENTITY}, "username": "another_local", "cultivation_level": LATE_REALM,
        }, path=path)

    monkeypatch.setattr(ui, "fetch_storage_bag_result", fetch)
    ok, _message, _snapshot = asyncio.run(ui.ui_refresh_identity_from_api(IDENTITY))
    assert not ok
    assert state_module.get_send_as_profile(IDENTITY)["realm"] == EARLY_REALM


def test_config_changed_away_and_back_still_invalidates_old_api_read(env, monkeypatch):
    original = state_module.get_storage_bag_api_config()

    async def fetch(_config, path):
        ui.ui_set_storage_bag_api_config({"cookie": "session=another"})
        ui.ui_set_storage_bag_api_config(original)
        return api_result(path=path)

    monkeypatch.setattr(ui, "fetch_storage_bag_result", fetch)
    ok, _message, _snapshot = asyncio.run(ui.ui_refresh_identity_from_api(IDENTITY))
    assert not ok
    assert state_module.get_storage_bag_api_config()["cookie"] == original["cookie"]


def test_replaced_api_slot_keeps_newer_status_and_ownership(env, monkeypatch):
    newer_token = object()

    async def fetch(_config, path):
        ui._storage_bag_api_state.update({
            "dao_path_request": newer_token, "running": True,
            "dao_path_last_message": "newer result", "dao_path_last_ok": True,
        })
        return api_result(path=path)

    monkeypatch.setattr(ui, "fetch_storage_bag_result", fetch)
    ok, _message, _snapshot = asyncio.run(ui.ui_refresh_identity_from_api(IDENTITY))
    assert not ok
    assert ui._storage_bag_api_state["dao_path_request"] is newer_token
    assert ui._storage_bag_api_state["running"]
    assert ui._storage_bag_api_state["dao_path_last_message"] == "newer result"
    assert ui._storage_bag_api_state["dao_path_last_ok"]


def test_all_profile_refresh_keeps_other_owners_but_skips_changed_one(env, monkeypatch):
    other = IDENTITY + 1
    state_module.set_identity_account(other, ACCOUNT)
    state_module.update_send_as_profile(other, username="other_local", realm=EARLY_REALM)
    calls = []

    async def fetch(_config, path):
        calls.append(path)
        state_module.set_identity_account(IDENTITY, ACCOUNT + 1)
        return api_result({"characters": [
            {"telegram_id": IDENTITY, "cultivation_level": LATE_REALM},
            {"telegram_id": other, "cultivation_level": MIDDLE_REALM},
        ]}, path=path)

    monkeypatch.setattr(ui, "fetch_storage_bag_result", fetch)
    ok, _message, _snapshot = asyncio.run(ui.ui_refresh_tianjige_dao_path_from_api())
    assert ok
    assert calls == ["/api/me"]
    assert state_module.get_send_as_profile(IDENTITY)["realm"] == EARLY_REALM
    assert state_module.get_send_as_profile(other)["realm"] == MIDDLE_REALM


def test_duplicate_api_click_and_cancel_leave_no_profile_write(env, monkeypatch):
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def fetch(_config, path):
            calls.append(path)
            started.set()
            await release.wait()
            return api_result(path=path)

        monkeypatch.setattr(ui, "fetch_storage_bag_result", fetch)
        task = asyncio.create_task(ui.ui_refresh_identity_from_api(IDENTITY))
        await started.wait()
        try:
            assert not (await ui.ui_refresh_identity_from_api(IDENTITY))[0]
            assert len(calls) == 1
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            release.set()
        assert not ui._is_storage_bag_api_busy()
        assert str(IDENTITY) not in state_module.get_tianjige_dao_path_records()
        assert state_module.get_storage_bag_api_config()["cookie"] == "session=old"

    asyncio.run(scenario())


def test_profile_source_evidence_survives_sqlite_reload(env, monkeypatch, tmp_path):
    env.now = NOW + 100
    asyncio.run(dispatch_breakthrough(env))
    before = copy.deepcopy(state_module.get_identity_state(IDENTITY)["identity_profile_observed_at"])
    monkeypatch.setattr(persistence, "DB_FILE", str(tmp_path / "profile_sources.db"))
    assert persistence.save_state()
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    assert state_module.get_identity_state(IDENTITY)["identity_profile_observed_at"] == before
    older_card = event(NOW + 20)
    older_card.id = 6300
    assert asyncio.run(control.handle_passive_identity_profile_card(COMBINED_CARD, env.now, event=older_card))
    assert state_module.get_send_as_profile(IDENTITY)["realm"] == LATE_REALM


@pytest.mark.parametrize("kind", ["same_message_edit", "other_chat", "different_message_edit"])
def test_equal_clock_orders_only_comparable_evidence(env, kind):
    old_evidence = {"source": "telegram", "chat_id": CHAT, "msg_id": 6300, "edited": False}
    assert profile_observation.apply_profile_observation(IDENTITY, {"realm": MIDDLE_REALM}, NOW, evidence=old_evidence)
    evidence = dict(old_evidence)
    if kind == "other_chat":
        evidence.update(chat_id=CHAT - 1, msg_id=6309)
    elif kind == "different_message_edit":
        evidence.update(edited=True, msg_id=6309)
    else:
        evidence["edited"] = True
    changes = profile_observation.apply_profile_observation(IDENTITY, {"realm": LATE_REALM}, NOW, evidence=evidence)
    assert bool(changes) is (kind == "same_message_edit")
    assert state_module.get_send_as_profile(IDENTITY)["realm"] == (LATE_REALM if changes else MIDDLE_REALM)


@pytest.mark.parametrize("evidence", [None, [], {"not_a_group": {}}, {"realm": {"source": "api", "requested_at": "bad"}}])
def test_corrupt_source_metadata_cannot_authorize_profile_writes(env, evidence):
    state_module.get_identity_state(IDENTITY)["identity_profile_observed_at"] = {"realm": NOW, "_evidence": evidence}
    assert profile_observation.apply_profile_observation(IDENTITY, {"realm": LATE_REALM}, NOW + 100) is None
    assert state_module.get_send_as_profile(IDENTITY)["realm"] == EARLY_REALM


def test_profile_observation_never_creates_unknown_identity(env):
    assert not state_module.has_identity(IDENTITY + 7)
    assert profile_observation.apply_profile_observation(IDENTITY + 7, {"realm": LATE_REALM}, NOW) is None
    assert not state_module.has_identity(IDENTITY + 7)


def test_breakthrough_result_is_not_reopened_by_failed_notification(env):
    env.now = NOW + 100
    control.send_audit_log.side_effect = RuntimeError("notification unavailable")
    with pytest.raises(RuntimeError, match="notification unavailable"):
        asyncio.run(dispatch_breakthrough(env))
    asyncio.run(dispatch_breakthrough(env))
    assert state_module.get_send_as_profile(IDENTITY)["realm"] == LATE_REALM
    control.send_audit_log.assert_awaited_once()
    control.save_state.assert_called_once()


def test_api_duplicate_rows_count_one_identity_and_keep_first_snapshot(env, monkeypatch):
    async def fetch(_config, path):
        return api_result({"characters": [
            {"telegram_id": IDENTITY, "cultivation_level": LATE_REALM, "status": "normal"},
            {"telegram_id": IDENTITY, "cultivation_level": EARLY_REALM, "status": "retreat"},
        ]}, path=path)

    monkeypatch.setattr(ui, "fetch_storage_bag_result", fetch)
    ok, _message, snapshot = asyncio.run(ui.ui_refresh_tianjige_dao_path_from_api())
    assert ok
    assert snapshot["dao_path_updated_count"] == 1
    assert state_module.get_tianjige_dao_path_records()[str(IDENTITY)]["status"] == "normal"
    assert state_module.get_send_as_profile(IDENTITY)["realm"] == LATE_REALM


def test_api_parse_exception_does_not_mark_session_unhealthy(env, monkeypatch):
    monkeypatch.setattr(ui, "fetch_storage_bag_result", AsyncMock(return_value=api_result()))
    monkeypatch.setattr(ui, "_tianjige_apply_dao_path_payload", Mock(side_effect=ValueError("invalid local snapshot")))
    ok, _message, _snapshot = asyncio.run(ui.ui_refresh_identity_from_api(IDENTITY))
    assert not ok
    assert not state_module.get_storage_bag_api_config().get("last_keepalive_error")
    assert not ui._is_storage_bag_api_busy()


def test_nested_role_object_is_not_saved_as_identity_label(env, monkeypatch):
    state_module.update_send_as_profile(IDENTITY, label="local-label")
    async def fetch(_config, path):
        return api_result({"role": {"telegram_id": IDENTITY, "cultivation_level": LATE_REALM}}, path=path)

    monkeypatch.setattr(ui, "fetch_storage_bag_result", fetch)
    assert asyncio.run(ui.ui_refresh_identity_from_api(IDENTITY))[0]
    assert state_module.get_send_as_profile(IDENTITY)["label"] == "local-label"


def test_ambiguous_query_name_does_not_bind_ownerless_api_response(env, monkeypatch):
    state_module.set_identity_account(IDENTITY + 1, ACCOUNT)
    for identity_id in (IDENTITY, IDENTITY + 1):
        state_module.update_send_as_profile(identity_id, label="shared")

    async def fetch(_config, path):
        if path != "/api/cultivator/shared":
            raise StorageBagApiError("not found", status_code=404)
        return api_result({"cultivation_level": LATE_REALM}, path=path)

    monkeypatch.setattr(ui, "fetch_storage_bag_result", fetch)
    ok, _message, _snapshot = asyncio.run(ui.ui_refresh_identity_from_api(IDENTITY))
    assert not ok
    assert state_module.get_send_as_profile(IDENTITY)["realm"] == EARLY_REALM


def test_partial_api_refresh_keeps_committed_progress_on_later_error(env, monkeypatch):
    state_module.set_identity_account(IDENTITY + 1, ACCOUNT)
    state_module.update_send_as_profile(IDENTITY + 1, username="other_local")

    async def fetch(_config, path):
        if path == "/api/me":
            return api_result({"characters": [{"telegram_id": IDENTITY, "cultivation_level": LATE_REALM}]}, path=path)
        raise StorageBagApiError("HTTP 401", status_code=401, auth_failed=True)

    monkeypatch.setattr(ui, "fetch_storage_bag_result", fetch)
    ok, _message, snapshot = asyncio.run(ui.ui_refresh_tianjige_dao_path_from_api())
    assert not ok
    assert state_module.get_send_as_profile(IDENTITY)["realm"] == LATE_REALM
    assert snapshot["dao_path_updated_count"] == 1
