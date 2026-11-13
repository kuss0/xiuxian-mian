"""Offline acceptance of new controllers; no live database or game requests."""
import asyncio
import copy
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from contextlib import asynccontextmanager

import pytest

from model import app_expeditions, app_spirit_beast, avatar_soul, runtime, state
from model.features import cave_treasure_miniapp, cave_treasure_runtime, paired_activities as pairs, soul_possession, spirit_beast as beast


@pytest.fixture
def env(monkeypatch):
    before = copy.deepcopy(state._meta_state)
    for name in ("_bot_health_state", "_bot_health_reason", "_bot_health_changed_at", "_bot_waiting_since",
                 "_bot_last_seen_at", "_bot_probe_sent_at", "_bot_probe_msg_id", "_bot_last_block_log_at"):
        monkeypatch.setattr(runtime, name, getattr(runtime, name))
    monkeypatch.setattr(runtime, "_reply_chain_tracker", {})
    monkeypatch.setattr(runtime, "_GAME_SEND_TASKS", {})
    monkeypatch.setattr(runtime, "action_guard_note_sent", lambda *a, **k: None)
    monkeypatch.setattr(runtime, "_notify_game_command_sent_observers", lambda *a, **k: None)
    state.set_expedition_run_state({})
    state.set_expedition_config({})
    state.set_global_enabled(True)
    state.set_game_bot_ids([99])
    for i in range(1, 7):
        state.ensure_identity_registered(i)
        state.set_identity_account(i, i)
        state.update_send_as_profile(i, username=f"p{i}", enabled=True, realm="元婴初期")
    now = [1794567600.0]
    monkeypatch.setattr(pairs.time, "time", lambda: now[0])
    monkeypatch.setattr(pairs, "_LOCK", asyncio.Lock())
    monkeypatch.setattr(pairs, "get_registered_client", lambda _: object())
    monkeypatch.setattr(pairs, "hunt_target", lambda role: (-100 if role == "primary" else -200, 42))
    monkeypatch.setattr(app_expeditions, "hunt_target", pairs.hunt_target)
    for module, name in ((pairs, "save"), (app_expeditions, "save_state"), (app_spirit_beast, "save"), (avatar_soul, "save"), (beast, "_save_spirit_beast_state")):
        monkeypatch.setattr(module, name, lambda *a, **k: True)
    monkeypatch.setattr(beast, "send_audit_log", AsyncMock())
    monkeypatch.setattr(soul_possession, "send_audit_log", AsyncMock())
    for mapping in (avatar_soul._LOCKS, avatar_soul._WAITERS, avatar_soul._EARLY, avatar_soul._ECHOES, avatar_soul._SESSIONS):
        mapping.clear()
    sends = []

    async def send(command, **kwargs):
        assert kwargs["operation_check"]()
        sends.append((command, kwargs))
        return SimpleNamespace(id=100 + len(sends), chat_id=-100)

    monkeypatch.setattr(pairs, "send_game_command", send)
    magic = AsyncMock(return_value=SimpleNamespace(id=500))
    monkeypatch.setattr(pairs, "send_duel_private_magic", magic)
    yield SimpleNamespace(now=now, sends=sends, magic=magic)
    state._meta_state.clear()
    state._meta_state.update(before)


def event(env, text, *, reply=101, sender=99, chat=-100, topic=42, mid=1000, forwarded=False):
    return SimpleNamespace(raw_text=text, reply_to_msg_id=reply, sender_id=sender, chat_id=chat,
        id=mid, topic_id=topic, forwarded=forwarded, date=datetime.fromtimestamp(env.now[0], timezone.utc))


def configure(kind, **extra):
    entry = {"name": "本机", "source_identity_id": 1, "target_identity_id": 2, "group_role": "primary"}
    config = {"enabled": True, "pairs": [entry], "lead_text": "修修"} if kind == "warm_pairs" else {"enabled": True, **entry, "count": 2}
    config.update(extra)
    state.set_expedition_config({kind: config})
    return config


def test_warm_pair_anchor_progress_terminal_and_no_duplicate(env):
    async def run():
        configure("warm_pairs")
        await pairs.scheduler(env.now[0])
        assert env.sends[0][0] == "修修"
        env.now[0] += 5
        await pairs.scheduler(env.now[0])
        assert env.sends[1][0] == ".双修 温养"
        assert env.sends[1][1]["reply_to"] == 101
        assert env.sends[1][1]["target_chat_id"] == -100
        await pairs.game_event(event(env, "契印感应，准备进行温养双修", reply=102))
        run = pairs.records("warm_pairs")["本机"]
        assert run["phase"] == "waiting_reply"
        env.now[0] += 20
        await pairs.game_event(event(env, "【温养双修·同心】修为增加了 20 点", reply=102))
        assert run["phase"] == "idle" and env.now[0] + 3660 <= run["next_at"] <= env.now[0] + 3720
        await pairs.game_event(event(env, "【温养双修·同心】修为增加了 20 点", reply=102))
        await pairs.scheduler(env.now[0] + 1000)
        assert len(env.sends) == 2
    asyncio.run(run())


def test_unknown_warm_send_is_held_without_retry(env, monkeypatch):
    async def run():
        configure("warm_pairs")
        send = AsyncMock(side_effect=TimeoutError())
        monkeypatch.setattr(pairs, "send_game_command", send)
        await pairs.scheduler(env.now[0])
        env.now[0] += 1000
        await pairs.scheduler(env.now[0])
        assert send.await_count == 1
        assert pairs.records("warm_pairs")["本机"]["phase"] == "held"
    asyncio.run(run())


@pytest.mark.parametrize("change", [{"chat": -200}, {"topic": 7}, {"sender": 98}, {"reply": 999}, {"forwarded": True}])
def test_pair_reply_requires_exact_ownership(env, change):
    async def run():
        configure("global_duel")
        await pairs.scheduler(env.now[0])
        assert not await pairs.game_event(event(env, "【决斗开启·第一回合】", **change))
        assert pairs.records("global_duel")["本机"]["phase"] == "waiting_start"
    asyncio.run(run())


def test_global_duel_rounds_count_magic_receipts_and_spacing(env):
    async def run():
        configure("global_duel")
        await pairs.scheduler(env.now[0])
        assert env.sends[0][0] == ".对决 @p2"
        await pairs.game_event(event(env, "正在锁定对手天机"))
        await pairs.game_event(event(env, "【决斗开启·第一回合】"))
        env.now[0] += 11
        await pairs.scheduler(env.now[0])
        run = pairs.records("global_duel")["本机"]
        assert run["completed"] == 1 and run["phase"] == "idle"
        assert [call.args[0] for call in env.magic.await_args_list] == [1, 2]
        await pairs.scheduler(env.now[0] + 100)
        assert len(env.sends) == 1
        env.now[0] = run["next_at"] + 1
        await pairs.scheduler(env.now[0])
        await pairs.game_event(event(env, "【决斗开启·第一回合】", reply=102, mid=1001))
        env.now[0] += 11
        await pairs.scheduler(env.now[0])
        assert run["phase"] == "done" and run["completed"] == 2 and env.magic.await_count == 4
        await pairs.scheduler(env.now[0] + 1000)
        assert len(env.sends) == 2
    asyncio.run(run())


def test_global_magic_unknown_never_sends_other_side_or_replays(env):
    async def run():
        configure("global_duel")
        env.magic.side_effect = TimeoutError()
        await pairs.scheduler(env.now[0])
        await pairs.game_event(event(env, "【决斗开启·第一回合】"))
        env.now[0] += 11
        await pairs.scheduler(env.now[0])
        assert pairs.records("global_duel")["本机"]["phase"] == "held"
        pairs.reset_on_startup()
        env.now[0] += 1000
        await pairs.scheduler(env.now[0])
        assert env.magic.await_count == 1 and len(env.sends) == 1
    asyncio.run(run())


def test_cooldown_reply_sets_real_wait(env):
    async def run():
        run = {"phase": "waiting_reply", "command_id": 102, "started_at": env.now[0], "chat_id": -100, "topic_id": 42}
        assert pairs.reduce_reply("warm_pairs", run, event(env, "心神尚未恢复，还需30分钟后再试", reply=102), env.now[0])
        assert run["next_at"] >= env.now[0] + 1860
    asyncio.run(run())


def test_no_configuration_sends_nothing(env):
    async def run():
        await pairs.scheduler(env.now[0])
        await app_spirit_beast.scheduler(env.now[0])
        assert not env.sends and env.magic.await_count == 0
    asyncio.run(run())


def test_new_config_rejects_overlap_and_duplicate_game_names_atomically(env):
    before = state.get_expedition_config()
    assert not app_expeditions.ui_set_expedition_config({"weekly": {"participant_identity_ids": [1, 2], "member_names": {"1": "玄平翁", "2": "玄平翁"}}})[0]
    assert state.get_expedition_config() == before
    assert not app_expeditions.ui_set_expedition_config({"warm_pairs": {"enabled": True, "pairs": [
        {"source_identity_id": 1, "target_identity_id": 2}, {"source_identity_id": 2, "target_identity_id": 3}]}})[0]
    assert state.get_expedition_config() == before
    state.set_identity_account(3, 1)
    assert not app_expeditions.ui_set_expedition_config({"global_duel": {"enabled": True, "source_identity_id": 1, "target_identity_id": 3}})[0]


def beast_response(**changes):
    return {"ok": True, "player": {"sect": "万灵宗"}, "attempts": {"used": 0, "limit": 1, "remaining": 1},
        "beasts": [{"id": 10, "name": "小白", "status": "休息中", "stamina": 100}],
        "zones": [{"key": "spirit_eye", "name": "灵眼泉脉"}], **changes}


def test_spirit_beast_completes_three_response_driven_stages(env):
    async def run():
        record, calls = {}, []
    
        async def call(path, _token, _init, **payload):
            calls.append((path, payload))
            if path == beast.SPIRIT_BEAST_API_START:
                return beast_response()
            assert record["pending"]["path"] == path
            if path == beast.SPIRIT_BEAST_API_EXPEDITION_START:
                seq = 0
            else:
                assert payload["runToken"] == "ephemeral-run-token"
                seq = payload["seq"] + 1
            if seq == 3:
                return beast_response(attempts={"used": 1, "limit": 1, "remaining": 0}, outcome={"score": 10},
                    history=[{"reward": {"itemName": "灵石", "quantity": 20}}])
            return beast_response(expedition={"runToken": "ephemeral-run-token", "seq": seq, "beast": {"id": 10},
                "encounter": {"title": "灵眼", "approaches": [{"key": "trace"}, {"key": "gift"}]}})
    
        assert await app_spirit_beast.run_protocol(call, record, env.now[0])
        assert sum(path == beast.SPIRIT_BEAST_API_EXPEDITION_CHOOSE for path, _ in calls) == 3
        assert record["phase"] == "idle" and not record.get("pending")
        assert "灵石x20" in record["spirit_beast_last_result"]
        assert "ephemeral-run-token" not in json.dumps(record)
    asyncio.run(run())


@pytest.mark.parametrize("response", [None, {"ok": True}])
def test_spirit_unknown_or_incomplete_mutation_is_preserved(env, response):
    async def run():
        record, paths = {}, []
    
        async def call(path, *_args, **_payload):
            paths.append(path)
            return beast_response() if path == beast.SPIRIT_BEAST_API_START else response
    
        with pytest.raises(beast.SpiritBeastMiniappError):
            await app_spirit_beast.run_protocol(call, record, env.now[0])
        assert record["pending"]["path"] == beast.SPIRIT_BEAST_API_EXPEDITION_START
        assert len(paths) == 2
        app_spirit_beast.records()["1"] = record
        app_spirit_beast.reset_on_startup()
        assert record["phase"] == "held"
    asyncio.run(run())


def test_non_wanling_identity_never_mutates(env):
    async def run():
        call = AsyncMock(return_value=beast_response(player={"sect": "合欢宗"}))
        record = {}
        assert not await app_spirit_beast.run_protocol(call, record, env.now[0])
        assert call.await_count == 1 and record["phase"] == "sect_blocked"
    asyncio.run(run())


def register_avatar(env):
    avatar_soul.records()["scans"] = {"1": {"at": env.now[0], "entries": [{"player_id": 123, "dao_name": "玄平翁"}]}}
    assert avatar_soul.register_avatar_identity(1, 123)[0]
    return int(next(iter(avatar_soul.records()["entries"])))


def test_avatar_registration_is_separate_from_channels_and_disabled(env):
    avatar = register_avatar(env)
    assert state.is_avatar_identity(avatar) and not state.is_avatar_identity(2)
    assert state.get_identity_account(avatar) == 1 and state.resolve_send_as_peer_id(avatar) == 1
    assert not state.get_identity_enabled(avatar)
    assert not any(v for k, v in state.get_identity_state(avatar).items() if k.endswith("_enabled"))
    assert not avatar_soul.register_avatar_identity(1, 999)[0]
    assert cave_treasure_runtime._normalize_dwelling_identity_id(avatar) == 123
    assert not cave_treasure_miniapp.cave_action_player_error({"account": {"playerId": 123}}, avatar)
    assert cave_treasure_miniapp.cave_action_player_error({"account": {"playerId": 1}}, avatar)


def test_avatar_reply_routes_to_virtual_identity_using_physical_sender(env):
    avatar = register_avatar(env)
    runtime._finalize_game_command_sent(".点卯", msg_id=777, sent_at=env.now[0], send_as_id=avatar,
                                       game_group_id=-100, topic_id=42, track=False, append_sent_log=False)
    reply = SimpleNamespace(id=777, chat_id=-100, sender_id=1, raw_text=".点卯")
    context = runtime.get_reply_context(reply, chat_id=-100)
    assert context["send_as_id"] == avatar
    assert runtime.get_reply_context(reply, chat_id=-200)["send_as_id"] is None


def test_avatar_choices_never_treat_channels_or_unknown_sources_as_avatars(env):
    choices = [{"playerId": i, "name": f"道号{i}", "source": source} for i, source in ((1, "personal"), (2, "channel"), (3, "avatar"), (4, "unknown"))]
    choices.append({"playerId": 5, "name": "别人的分身", "source": "avatar", "ownerPlayerId": 99})
    assert [entry["player_id"] for entry in avatar_soul.classify_choices({"identity": {"choices": choices}}, 1)] == [3]


def test_avatar_confirmed_early_reply_and_quiet_window(env, monkeypatch):
    async def run():
        avatar = register_avatar(env)
        state.set_identity_enabled(avatar, True)
    
        async def sleeper(delay):
            env.now[0] += delay
    
        async def send(command, **kwargs):
            assert kwargs["send_as_id"] == 1 and kwargs["operation_check"]()
            await avatar_soul.game_event(event(env, "切换成功！你的神念已附着在 【玄平翁】 之上。", reply=600))
            return SimpleNamespace(id=600, chat_id=-100)
    
        monkeypatch.setattr(runtime, "send_game_command", send)
        monkeypatch.setattr(avatar_soul.asyncio, "sleep", sleeper)
        assert await avatar_soul.ensure_avatar_soul(avatar)
        assert avatar_soul.ready(avatar)
        assert avatar_soul.send_block_reason(1, ".点卯")
        assert not avatar_soul.send_block_reason(avatar, ".点卯")
    asyncio.run(run())


def test_unknown_switch_never_runs_avatar_or_repeats_switch(env, monkeypatch):
    async def run():
        avatar = register_avatar(env)
        state.set_identity_enabled(avatar, True)
        send = AsyncMock(return_value=None)
        monkeypatch.setattr(runtime, "send_game_command", send)
        assert not await avatar_soul.ensure_avatar_soul(avatar)
        assert not await avatar_soul.ensure_avatar_soul(avatar)
        assert send.await_count == 1 and avatar_soul.soul(1)["mode"] == "unknown"
        avatar_soul.reset_on_startup()
        assert avatar_soul.send_block_reason(1, ".点卯")
    asyncio.run(run())


def test_soul_pause_requires_owned_trusted_semantic_evidence_and_recovers(env):
    async def run():
        text = "*灵气如刀割我婴身……把肉身给我！*"
        assert not await soul_possession.handle_soul_weak_reply_event(event(env, text, sender=98), text, env.now[0], identity_id=1, root_msg_id=101)
        assert not await soul_possession.handle_soul_weak_reply_event(event(env, text), text, env.now[0], identity_id=1, root_msg_id=0)
        assert not soul_possession.is_soul_weak_reply_text("*你在月下喝了一杯酒。*")
        assert await soul_possession.handle_soul_weak_reply_event(event(env, text), text, env.now[0], identity_id=1, root_msg_id=101)
        assert runtime.is_identity_weak(1) and not runtime.is_identity_weak(2)
        assert soul_possession.clear_soul_possession_pause(1)
        assert not runtime.is_identity_weak(1)
    asyncio.run(run())


def test_avatar_early_business_reply_survives_transport_receipt(env):
    avatar = register_avatar(env)
    avatar_soul.note_send(avatar)
    avatar_soul.note_reply(avatar, env.now[0])
    avatar_soul.note_send(avatar, SimpleNamespace(id=600))
    assert avatar_soul._SESSIONS[avatar]["reply_seen"]
    assert avatar_soul._SESSIONS[avatar]["last_reply"] == env.now[0]


def test_return_main_rejects_channel_identity(env, monkeypatch):
    state.set_identity_account(3, 1)
    switch = AsyncMock()
    monkeypatch.setattr(avatar_soul, "_switch", switch)
    assert not asyncio.run(avatar_soul.return_to_main(3))
    switch.assert_not_awaited()


@pytest.mark.parametrize("selected", [1, 123])
def test_avatar_profile_reads_selected_avatar_without_copying_owner(env, monkeypatch, selected):
    avatar = register_avatar(env)
    owner_before = state.get_send_as_profile(1)
    monkeypatch.setattr(cave_treasure_runtime, "_parse_public_cave_entry_url", lambda _: ("token", "https://entry", ""))
    monkeypatch.setattr(cave_treasure_runtime, "_load_cave_public_identity_session", AsyncMock(return_value={"ok": True, "init_data": "ephemeral"}))
    selected_start = AsyncMock(return_value={"ok": True, "data": {"overview": {
        "player_id": selected, "cultivation_level": "结丹后期", "sect_name": "万灵宗", "dao_name": "玄平翁"}}})
    monkeypatch.setattr(cave_treasure_runtime, "run_cave_dwelling_start_production_flow", selected_start)
    ok, _message = asyncio.run(avatar_soul.refresh_avatar_profile(avatar))
    assert ok == (selected == 123)
    assert selected_start.await_args.args == (1,)
    assert selected_start.await_args.kwargs["player_id"] == 123
    assert state.get_send_as_profile(1) == owner_before
    assert state.get_send_as_profile(avatar)["realm"] == ("结丹后期" if ok else "")
    assert not state.get_identity_enabled(avatar)


@pytest.mark.parametrize("kind", ["hunt_teams", "xuangu", "weekly"])
def test_team_config_rejects_owner_and_avatar_together(env, kind):
    avatar = register_avatar(env)
    payload = [{"name": "本机", "member_identity_ids": [1, avatar]}] if kind == "hunt_teams" else {"participant_identity_ids": [1, avatar]}
    assert not app_expeditions.ui_set_expedition_config({kind: payload})[0]


def test_named_teams_cannot_map_same_group_alias_to_different_identities(env):
    teams = [{"name": f"队{i}", "participant_identity_ids": [i], "member_names": {str(i): "道友"}, "group_role": "primary"} for i in (1, 2)]
    assert not app_expeditions.ui_set_expedition_config({"weekly": {"teams": teams}})[0]
    teams[1]["group_role"] = "secondary"
    assert app_expeditions.ui_set_expedition_config({"weekly": {"teams": teams}})[0]


@pytest.mark.parametrize("raises", [False, True])
def test_manual_finish_save_failure_preserves_pending(env, monkeypatch, raises):
    run = {"phase": "held", "id": "known", "pending": {"path": "mutation"}}
    app_spirit_beast.records()["1"] = copy.deepcopy(run)
    def fail():
        if raises:
            raise OSError("disk full")
        return False
    monkeypatch.setattr(app_expeditions, "save_state", fail)
    ok, _ = asyncio.run(app_expeditions.ui_additional_activity_action({"action": "confirm_finished", "kind": "spirit_beast", "key": "1", "run_id": "known"}))
    assert not ok and app_spirit_beast.records()["1"] == run


def test_duel_restart_save_failure_preserves_finished_rounds(env, monkeypatch):
    configure("global_duel")
    before = {"phase": "done", "completed": 2}
    pairs.records("global_duel")["本机"] = copy.deepcopy(before)
    monkeypatch.setattr(pairs, "save", lambda: (_ for _ in ()).throw(OSError("disk full")))
    ok, _ = asyncio.run(app_expeditions.ui_additional_activity_action({"action": "restart_global_duel"}))
    assert not ok and pairs.records("global_duel")["本机"] == before


def test_mutation_receipt_save_failure_stays_pending_and_cannot_replay(env, monkeypatch):
    async def run():
        record = {}
        calls = []
        def save():
            if calls and calls[-1] != beast.SPIRIT_BEAST_API_START and not record.get("pending"):
                raise OSError("disk full")
        monkeypatch.setattr(app_spirit_beast, "save", save)
        async def call(path, *_a, **_kw):
            calls.append(path)
            return beast_response() if path == beast.SPIRIT_BEAST_API_START else beast_response(expedition={"runToken": "secret", "seq": 0})
        with pytest.raises(OSError):
            await app_spirit_beast.run_protocol(call, record, env.now[0])
        assert record["pending"] and record["phase"] == "held"
        with pytest.raises(beast.SpiritBeastMiniappError):
            await app_spirit_beast.run_protocol(call, record, env.now[0])
        assert len(calls) == 2
    asyncio.run(run())


def test_spirit_nested_launch_is_validated(env):
    valid = "https://t.me/fanrenxiuxian_bot/spiritbeast?startapp=spiritbeast_abcdefgh1234"
    assert app_spirit_beast.find_launch_url({"raw": {"result": {"launch": {"webviewUrl": valid}}}}) == valid
    assert not app_spirit_beast.find_launch_url({"url": "https://evil.invalid/?startapp=spiritbeast_abcdefgh1234"})


@pytest.mark.parametrize("kind", ["warm_pairs", "global_duel"])
def test_active_pair_config_cannot_replace_participants(env, kind):
    async def run():
        config = configure(kind)
        await pairs.scheduler(env.now[0])
        changed = copy.deepcopy(config)
        (changed["pairs"][0] if kind == "warm_pairs" else changed)["target_identity_id"] = 3
        assert not app_expeditions.ui_set_expedition_config({kind: changed})[0]
        assert state.get_expedition_config()[kind] == config
        disabled = {**config, "enabled": False}
        assert app_expeditions.ui_set_expedition_config({kind: disabled})[0]
    asyncio.run(run())


def test_cancellation_after_first_private_magic_holds_without_replay(env):
    async def run():
        configure("global_duel")
        env.magic.side_effect = [SimpleNamespace(id=500), asyncio.CancelledError()]
        await pairs.scheduler(env.now[0])
        await pairs.game_event(event(env, "【决斗开启·第一回合】"))
        env.now[0] += 11
        with pytest.raises(asyncio.CancelledError):
            await pairs.scheduler(env.now[0])
        run = pairs.records("global_duel")["本机"]
        assert run["phase"] == "held" and run["magic"]["1"]["status"] == "sent"
        assert run["magic"]["2"]["status"] == "unknown"
        await pairs.scheduler(env.now[0] + 1000)
        assert env.magic.await_count == 2
    asyncio.run(run())


def test_abyss_injury_waits_then_restores_original_active_beast(env):
    async def run():
        record, actions = {}, []
        initial = beast_response(beasts=[{"id": 10, "name": "小白", "status": "出战中", "stamina": 100}],
                                 abyss={"ready": True, "remainingSeconds": 0})
        injured = beast_response(beasts=[{"id": 10, "name": "小白", "status": "受伤", "stamina": 60}],
            abyss={"ready": False, "remainingSeconds": 3600}, result={"won": False, "beast": {"id": 10, "name": "小白"},
            "statusAfter": "受伤", "recoverySeconds": 600})
        async def call(path, *_args, **payload):
            actions.append((path, payload))
            if path == beast.SPIRIT_BEAST_API_ABYSS_ENTER:
                return injured
            return beast_response(beasts=[{"id": 10, "name": "小白", "stamina": 60,
                "status": "出战中" if payload["action"] == "active" else "休息中"}])
        token = beast._RECORD.set(record)
        call_token = beast._CALL.set(call)
        try:
            await beast._run_spirit_beast_abyss("", "", initial, env.now[0])
            assert [p.get("action") for _, p in actions] == ["rest", None]
            assert record["spirit_beast_restore_active_after"] == env.now[0] + 602
            await beast._restore_active_beast_if_due("", "", injured, env.now[0] + 601)
            assert len(actions) == 2
            healed = beast_response(beasts=[{"id": 10, "name": "小白", "stamina": 60, "status": "休息中"}])
            _data, summary = await beast._restore_active_beast_if_due("", "", healed, env.now[0] + 603)
            assert actions[-1][1] == {"action": "active", "beastId": 10}
            assert not record["spirit_beast_restore_active_beast_id"] and "已恢复出战" in summary
        finally:
            beast._CALL.reset(call_token)
            beast._RECORD.reset(token)
    asyncio.run(run())


@pytest.mark.parametrize("pause_in_resolution", [False, True])
def test_private_magic_uses_fixed_bot_and_rechecks_pause(env, monkeypatch, pause_in_resolution):
    async def run():
        client = SimpleNamespace()
        async def resolve(username):
            assert username == "fanrenxiuxian_bot"
            if pause_in_resolution:
                state.set_global_enabled(False)
            return SimpleNamespace(id=99, username=username, bot=True)
        client.get_entity = resolve
        client.send_message = AsyncMock(return_value=SimpleNamespace(id=900))
        @asynccontextmanager
        async def slot(*_a, **_kw):
            yield
        monkeypatch.setattr(runtime, "get_registered_client", lambda _: client)
        monkeypatch.setattr(runtime, "_send_slot", slot)
        monkeypatch.setattr(runtime, "account_rpc_slot", slot)
        monkeypatch.setattr(runtime, "_game_send_allowed", AsyncMock(return_value=True))
        monkeypatch.setattr(runtime, "_append_sent_message_log", lambda *a, **k: None)
        result = await runtime.send_duel_private_magic(1, operation_check=lambda: True)
        assert (result is None) == pause_in_resolution
        assert client.send_message.await_count == int(not pause_in_resolution)
        if not pause_in_resolution:
            assert client.send_message.await_args.args[1] == "神通"
            assert result.chat_id == 99
    asyncio.run(run())


def test_pet_route_ui_saves_command_and_rolls_back_failed_save(env, monkeypatch):
    from model import ui
    monkeypatch.setattr(ui, "send_audit_log", AsyncMock())
    monkeypatch.setattr(ui, "save_state", lambda: True)
    ok, _ = asyncio.run(ui.ui_set_pet_name(1, "无极剑", pet_trial_name="灵剑", pet_trial_route="寻宝"))
    assert ok and state.get_pet_trial_command(1).endswith("灵剑 寻宝")
    before = state.get_send_as_profile(1)
    monkeypatch.setattr(ui, "save_state", lambda: False)
    ok, _ = asyncio.run(ui.ui_set_pet_name(1, "另一把剑", pet_trial_route="斗战"))
    assert not ok and state.get_send_as_profile(1) == before
    assert state.get_pet_trial_route(1) == "寻宝"


def test_avatar_does_not_return_mid_rebirth_selection(env, monkeypatch):
    async def run():
        avatar = register_avatar(env)
        avatar_soul.soul(1).update(mode="avatar", source="program", avatar_id=avatar, confirmed_at=env.now[0] - 200)
        record = state.get_identity_state(avatar)
        record.update(explore_rift_rebirth_required=True, explore_rift_rebirth_phase="selecting")
        avatar_soul.note_reply(avatar, env.now[0] - 20)
        returning = AsyncMock(return_value=True)
        monkeypatch.setattr(avatar_soul, "return_to_main", returning)
        assert avatar_soul.ready(avatar)
        await avatar_soul.supervisor(env.now[0])
        returning.assert_not_awaited()
        assert not avatar_soul._TASKS
        record.update(explore_rift_rebirth_required=False, explore_rift_rebirth_phase="restored")
        await avatar_soul.supervisor(env.now[0])
        await asyncio.gather(*avatar_soul._TASKS.values())
        returning.assert_awaited_once_with(1)
    asyncio.run(run())
