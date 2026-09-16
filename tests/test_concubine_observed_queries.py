import asyncio
import copy
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import app, app_message_log, persistence, state as state_module
from model.features import concubine, passive_inbox
from model.verified_event import from_telegram_event
from tests import test_concubine_query_lifecycle as queries
from tests.test_concubine_query_lifecycle import env as env


ID, ACCOUNT, CHAT, BOT, ROOT, NOW = queries.ID, queries.ACCOUNT, queries.CHAT, queries.BOT, queries.ROOT, queries.NOW
ROUTES = ("direct", "native", "passive")


async def deliver(env, route="direct", *, text=queries.PANEL, root=ROOT, command_at=NOW,
                  at=NOW + 1, now=NOW + 2, event_type="message", corrupt=None):
    event = SimpleNamespace(id=root + 1, chat_id=CHAT, sender_id=BOT, server_event_at=at)
    parent = SimpleNamespace(id=root, chat_id=CHAT, sender_id=ID, raw_text=concubine.CMD_CONCUBINE_STATUS,
                             server_event_at=command_at, edit_date=None)
    context = {
        "send_as_id": ID, "account_id": ACCOUNT, "chat_id": CHAT, "family": "concubine_status",
        "root_msg_id": root, "reply_to_msg_id": root, "reply_to_sender_id": ID,
        "reply_to_command": concubine.CMD_CONCUBINE_STATUS,
        "reply_to_server_at": command_at, "reply_to_command_edited": False,
    }
    if corrupt:
        corrupt(event, parent, context)
    if route == "native":
        return await app._handle_routed_reply_event(event, text, now, parent, context, event_kind=event_type)
    if route == "passive":
        return await passive_inbox.handle_passive_module_card(
            text, now=now, event=event, reply_context=context, event_type=event_type,
        )
    with state_module.use_identity(ID):
        return await concubine.handle_concubine_status_reply(
            text, now, parent, matched_family=context.get("family"), current_msg_id=event.id,
            current_chat_id=event.chat_id, observed_at=event.server_event_at,
            reply_context=dict(context, sender_id=event.sender_id, event_type=event_type),
        )


@pytest.mark.parametrize("route", ROUTES)
def test_observed_query_records_original_source_without_sending(env, route):
    assert asyncio.run(deliver(env, route))
    record = env.identity["concubine_status_query"]
    assert record["origin"] == "observed"
    assert record["status"] == "complete"
    assert (record["identity_id"], record["account_id"], record["chat_id"], record["msg_id"]) == (ID, ACCOUNT, CHAT, ROOT)
    assert record["sender_id"] == BOT
    assert record["actor_id"] == ID
    assert record["started_at"] == NOW
    assert record["reply_at"] == NOW + 1
    assert env.identity["concubine_affinity"] == 184
    env.send.assert_not_awaited()
    env.gift.assert_not_awaited()


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("failure", ["false", "exception"])
def test_failed_observation_save_rolls_back_and_same_reply_can_replay(env, route, failure):
    before = copy.deepcopy(env.identity)
    if failure == "exception":
        env.save.side_effect = RuntimeError("observation save failed")
        with pytest.raises(RuntimeError, match="observation save failed"):
            asyncio.run(deliver(env, route))
    else:
        env.save.return_value = False
        assert not asyncio.run(deliver(env, route))
    assert env.identity == before
    env.save.side_effect = None
    env.save.return_value = True
    assert asyncio.run(deliver(env, route))
    assert env.identity["concubine_affinity"] == 184
    assert env.identity["concubine_status_query"]["status"] == "complete"


def corrupt_source(kind):
    def corrupt(event, parent, context):
        if kind == "bot":
            event.sender_id = BOT + 1
        elif kind == "chat":
            event.chat_id = parent.chat_id = context["chat_id"] = CHAT - 1
        elif kind == "actor":
            parent.sender_id = context["reply_to_sender_id"] = ID + 1
        elif kind == "command":
            parent.raw_text = context["reply_to_command"] = ".fixture"
        elif kind == "edited_command":
            context["reply_to_command_edited"] = True
        elif kind == "missing_clock":
            context.pop("reply_to_server_at")
            del parent.server_event_at
        elif kind == "future_command":
            context["reply_to_server_at"] = parent.server_event_at = NOW + 2
        elif kind == "root":
            context["root_msg_id"] = ROOT + 100
        elif kind == "account":
            context["account_id"] = ACCOUNT + 1
        elif kind == "owner":
            context["send_as_id"] = ID + 1
        elif kind == "bool_sender":
            event.sender_id = True
        else:
            event.id = ROOT
    return corrupt


@pytest.mark.parametrize("route", ["direct", "passive"])
@pytest.mark.parametrize("kind", ["bot", "chat", "actor", "command", "edited_command", "missing_clock",
                                  "future_command", "root", "account", "owner", "bool_sender", "reply_order"])
def test_unowned_status_requires_original_official_command_evidence(env, route, kind):
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(deliver(env, route, corrupt=corrupt_source(kind)))
    assert env.identity == before


@pytest.mark.parametrize("route", ROUTES)
def test_duplicate_and_old_command_edits_cannot_rewrite_new_observation(env, route):
    assert asyncio.run(deliver(env, route))
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(deliver(env, route))
    assert env.identity == before
    assert asyncio.run(deliver(
        env, route, text=queries.PANEL.replace("184", "240"), root=ROOT + 10,
        command_at=NOW + 10, at=NOW + 11, now=NOW + 12,
    ))
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(deliver(
        env, route, text=queries.PANEL.replace("184", "999"), at=NOW + 20, now=NOW + 21, event_type="edit",
    ))
    assert env.identity == before


@pytest.mark.parametrize("route", ROUTES)
def test_old_read_edit_cannot_borrow_its_edit_time_after_a_miniapp_snapshot(env, route):
    env.identity.update(concubine_affinity=240, concubine_last_snapshot_at=NOW + 10,
                        concubine_last_panel_msg_id=0, concubine_last_panel_chat_id=0)
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(deliver(env, route, at=NOW + 20, now=NOW + 21, event_type="edit"))
    assert env.identity == before


@pytest.mark.parametrize("route", ROUTES)
def test_partial_observation_stays_open_until_a_complete_edit(env, route):
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(deliver(env, route, text=queries.PANEL.splitlines()[0]))
    assert env.identity == before
    assert asyncio.run(deliver(env, route, at=NOW + 2, now=NOW + 3, event_type="edit"))
    assert env.identity["concubine_status_query"]["status"] == "complete"


@pytest.mark.parametrize("route", ROUTES)
def test_disabled_modules_still_record_manual_observation_without_starting_work(env, route):
    env.identity.update(concubine_enabled=False, concubine_tianji_enabled=False,
                        concubine_heart_enabled=False, concubine_voyage_enabled=False)
    assert asyncio.run(deliver(env, route))
    assert env.identity["concubine_affinity"] == 184
    assert not any(env.identity[key] for key in (
        "concubine_enabled", "concubine_tianji_enabled", "concubine_heart_enabled", "concubine_voyage_enabled"))
    env.send.assert_not_awaited()
    env.gift.assert_not_awaited()


@pytest.mark.parametrize("route", ROUTES)
def test_legacy_read_closes_only_its_root_and_never_continues_a_gift(env, route):
    env.identity.update(concubine_phase="gift_status_pending", concubine_gift_status_msg_id=ROOT)
    root = {"cmd": concubine.CMD_CONCUBINE_STATUS, "family": "concubine_status", "chat_id": CHAT,
            "message_id": ROOT, "account_id": ACCOUNT, "sent_at": NOW}
    sibling = dict(root, message_id=ROOT + 100)
    env.identity["pending_tasks"] = {(CHAT, ROOT): root, (CHAT, ROOT + 100): sibling}
    assert asyncio.run(deliver(env, route))
    assert env.identity["concubine_phase"] == "idle"
    assert env.identity["pending_tasks"] == {(CHAT, ROOT + 100): sibling}
    env.gift.assert_not_awaited()


@pytest.mark.parametrize("route", ROUTES)
def test_observed_query_sql_failure_and_reload_replay_are_atomic(env, monkeypatch, route):
    monkeypatch.setattr(concubine, "save_state", persistence.save_state)
    monkeypatch.setattr(passive_inbox, "save_state", persistence.save_state)
    assert persistence.save_state()
    before = copy.deepcopy(env.identity)
    conn = persistence.get_db_conn()
    conn.execute(f"CREATE TEMP TRIGGER fail_observed_query BEFORE INSERT ON identity_runtime_state "
                 f"WHEN NEW.send_as_id = {ID} BEGIN SELECT RAISE(ABORT, 'observed query failure'); END")
    try:
        assert not asyncio.run(deliver(env, route))
        assert env.identity == before
    finally:
        conn.execute("DROP TRIGGER fail_observed_query")
    assert asyncio.run(deliver(env, route))
    record = copy.deepcopy(env.identity["concubine_status_query"])
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    env.identity = state_module.get_identity_state(ID)
    assert env.identity["concubine_status_query"] == record
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(deliver(env, route))
    assert env.identity == before


@pytest.mark.parametrize("route", ROUTES)
def test_exact_original_command_does_not_require_a_family_hint(env, route):
    assert asyncio.run(deliver(env, route, corrupt=lambda _e, _p, c: c.pop("family")))
    assert env.identity["concubine_status_query"]["origin"] == "observed"


@pytest.mark.parametrize("channel", [False, True])
@pytest.mark.parametrize("route", ["native", "passive"])
def test_actual_reply_resolution_supplies_observed_query_provenance(env, route, channel):
    actor = -int(f"100{ID}") if channel else ID
    parent = SimpleNamespace(id=ROOT, chat_id=CHAT, sender_id=actor,
                             raw_text=concubine.CMD_CONCUBINE_STATUS,
                             date=datetime.fromtimestamp(NOW, timezone.utc), edit_date=None)
    event = SimpleNamespace(id=ROOT + 1, chat_id=CHAT, sender_id=BOT,
                            date=datetime.fromtimestamp(NOW + 1, timezone.utc),
                            reply_to=SimpleNamespace(reply_to_msg_id=ROOT),
                            get_reply_message=AsyncMock(return_value=parent))

    async def process():
        resolved, context = await app._resolve_event_reply(event)
        assert context["send_as_id"] == ID
        assert context["reply_to_sender_id"] == actor
        assert context["reply_to_server_at"] == NOW
        assert context["reply_to_command_edited"] is False
        if route == "native":
            return await app._handle_routed_reply_event(event, queries.PANEL, NOW + 2, resolved, context)
        return await passive_inbox.handle_passive_module_card(
            queries.PANEL, now=NOW + 2, event=event, reply_context=context, event_type="message",
        )

    assert asyncio.run(process())
    assert env.identity["concubine_status_query"]["actor_id"] == actor
    assert env.identity["concubine_status_query"]["started_at"] == NOW


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("flag", ["global", "identity"])
def test_paused_sending_does_not_discard_a_proven_manual_read(env, route, flag):
    if flag == "global":
        state_module.set_global_enabled(False)
    else:
        state_module.set_identity_enabled(ID, False)
    assert asyncio.run(deliver(env, route))
    assert not (state_module.get_global_enabled() if flag == "global" else state_module.get_identity_enabled(ID))
    env.send.assert_not_awaited()
    env.gift.assert_not_awaited()


@pytest.mark.parametrize("kind", ["status", "gift_status"])
def test_manual_read_cannot_replace_an_unresolved_owned_query(env, kind):
    assert asyncio.run(queries.send_query(kind))
    queries.receipt(env)
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(deliver(env, root=ROOT + 10, command_at=NOW + 10, at=NOW + 11, now=NOW + 12))
    assert env.identity == before


@pytest.mark.parametrize("field,value", [
    ("account_id", ACCOUNT + 1), ("account_id", True), ("send_as_id", ID + 1),
    ("cmd", ".fixture"), ("command", ".fixture"), ("family", "concubine_gift"),
    ("source_module", "concubine_heart"), ("op_id", "replacement"),
    ("chat_id", CHAT - 1), ("message_id", ROOT + 1),
])
def test_conflicting_legacy_pending_cannot_be_claimed_or_cleared(env, field, value):
    env.identity.update(concubine_phase="status_pending", concubine_status_msg_id=ROOT)
    item = {"cmd": concubine.CMD_CONCUBINE_STATUS, "family": "concubine_status", "chat_id": CHAT,
            "message_id": ROOT, "account_id": ACCOUNT, "sent_at": NOW}
    item[field] = value
    env.identity["pending_tasks"] = {(CHAT, ROOT): item}
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(deliver(env))
    assert env.identity == before


@pytest.mark.parametrize("kind", ["status", "gift_status"])
@pytest.mark.parametrize("anchor", [0, True, ROOT + 100])
def test_observation_preserves_a_replacement_read_phase(env, kind, anchor):
    env.identity.update(concubine_phase=kind + "_pending")
    env.identity[queries.KEYS[kind]] = anchor
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(deliver(env))
    assert env.identity == before


@pytest.mark.parametrize("field,value", [
    ("origin", "sent"), ("status", "sent"), ("status", "unknown"), ("kind", "gift_status"),
    ("op_id", "other"), ("command", ".fixture"), ("outcome", "success"),
    ("identity_id", ID + 1), ("account_id", True), ("chat_id", 0), ("msg_id", True),
    ("reply_msg_id", ROOT), ("actor_id", ID + 1), ("sender_id", True),
    ("started_at", NOW + 10), ("reply_at", float("nan")), ("reply_at", str(NOW)),
    ("dispatch_at", NOW), ("plan_key", "f" * 64),
])
def test_observed_record_cannot_become_a_pending_send_or_a_corrupt_proof(env, field, value):
    assert asyncio.run(deliver(env))
    env.identity["concubine_status_query"][field] = value
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        assert concubine._status_query_record() is None
        assert not concubine._status_query_admitted("status", NOW + 10)
    assert not asyncio.run(deliver(env, root=ROOT + 10, command_at=NOW + 10, at=NOW + 11, now=NOW + 12))
    assert env.identity == before
    env.send.assert_not_awaited()


def test_observed_completion_does_not_block_the_next_owned_read(env):
    assert asyncio.run(deliver(env))
    env.clock[0] = NOW + 86400
    env.send.return_value = SimpleNamespace(id=ROOT + 100, chat_id=CHAT, sent_at=env.clock[0], send_started_at=env.clock[0])
    assert asyncio.run(queries.send_query("status"))
    record = env.identity["concubine_status_query"]
    assert record["status"] == "sent" and record["msg_id"] == ROOT + 100
    assert "origin" not in record and "actor_id" not in record


@pytest.mark.parametrize("mode", ["exception", "cancel", "diagnostic"])
def test_notification_failure_keeps_observed_completion_and_blocks_replay(env, monkeypatch, mode):
    text = queries.PANEL + "\n\u68a6\u56fe\u62fc\u7247: \u865a\u5929 4/4 | \u82cd\u5764 2/4"
    if mode == "diagnostic":
        monkeypatch.setattr(passive_inbox, "_record_passive_event", Mock(side_effect=OSError("diagnostic")))
    else:
        env.audit.side_effect = asyncio.CancelledError() if mode == "cancel" else RuntimeError("notification")
    if mode == "cancel":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(deliver(env, "passive", text=text))
    else:
        assert asyncio.run(deliver(env, "passive", text=text))
    assert env.identity["concubine_status_query"]["status"] == "complete"
    assert not asyncio.run(deliver(env, "passive", text=text))
    env.audit.assert_awaited_once()


@pytest.mark.parametrize("route", ROUTES)
def test_summary_completes_only_the_read_and_preserves_financial_markers(env, route):
    env.identity.update(concubine_phase="gift_status_pending", concubine_gift_status_msg_id=ROOT,
                        concubine_gift_attempt_day=concubine._local_day_key(NOW))
    item = {"cmd": concubine.CMD_CONCUBINE_STATUS, "family": "concubine_status", "chat_id": CHAT,
            "message_id": ROOT, "account_id": ACCOUNT, "sent_at": NOW}
    env.identity["pending_tasks"] = {(CHAT, ROOT): item}
    before = copy.deepcopy(env.identity)
    text = "\u3010\u5143\u5a74\u95ed\u5173\u7ed3\u7b97\u3011\n\u4f60\u7684\u5143\u5a74\u5728\u8fc7\u53bb 12 \u5c0f\u65f6\u5185\u4e3a\u4f60\u589e\u52a0\u4e86 15600 \u70b9\u4fee\u4e3a\uff01"
    env.save.return_value = False
    assert not asyncio.run(deliver(env, route, text=text))
    assert env.identity == before
    env.save.return_value = True
    assert asyncio.run(deliver(env, route, text=text))
    assert env.identity["concubine_status_query"]["outcome"] == "summary"
    assert env.identity["concubine_phase"] == "idle" and not env.identity["pending_tasks"]
    for key in ("concubine_gift_attempt_day", "concubine_affinity", "concubine_last_snapshot_at"):
        assert env.identity[key] == before[key]
    env.gift.assert_not_awaited()


@pytest.mark.parametrize("kind", ["clock_conflict", "clock_type", "edit_proof_missing", "parent_edited", "hint_clock",
                                  "hint_family", "hint_operation", "forwarded", "ambiguous_actor"])
def test_contradictory_native_source_cannot_establish_an_observation(env, kind):
    def corrupt(event, parent, context):
        if kind == "clock_conflict":
            parent.server_event_at = NOW - 100
        elif kind == "clock_type":
            parent.server_event_at = str(NOW)
        elif kind == "edit_proof_missing":
            context.pop("reply_to_command_edited")
            del parent.edit_date
        elif kind == "parent_edited":
            parent.edit_date = datetime.fromtimestamp(NOW + 1, timezone.utc)
        elif kind == "hint_clock":
            context["server_event_at"] = NOW + 5
        elif kind == "hint_family":
            context["family"] = "concubine_gift"
        elif kind == "hint_operation":
            context["op_id"] = "missing-owned-query"
        elif kind == "forwarded":
            context["forwarded"] = True
        else:
            parent.sender_id = context["reply_to_sender_id"] = -int(f"100{ID}")

    if kind == "ambiguous_actor":
        state_module.set_identity_account(int(f"100{ID}"), ACCOUNT)
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(deliver(env, corrupt=corrupt))
    assert env.identity == before


def test_newer_manual_read_in_a_second_group_uses_its_own_clock(env):
    state_module.set_game_group_route_config({"primary_group_id": CHAT, "backup_group_ids": [CHAT - 1], "enabled": True})
    assert asyncio.run(deliver(env))
    def second_group(event, parent, context):
        event.chat_id = parent.chat_id = context["chat_id"] = CHAT - 1
    assert asyncio.run(deliver(env, root=10, command_at=NOW + 10, at=NOW + 11, now=NOW + 12, corrupt=second_group))
    assert env.identity["concubine_status_query"]["chat_id"] == CHAT - 1


@pytest.mark.parametrize("kind", ["status", "gift_status"])
@pytest.mark.parametrize("replacement", [False, True])
def test_reloaded_observation_cleans_only_an_exact_late_legacy_read(env, kind, replacement):
    assert asyncio.run(deliver(env))
    env.identity["concubine_phase"] = kind + "_pending"
    env.identity[queries.KEYS[kind]] = ROOT
    pending = {"cmd": concubine.CMD_CONCUBINE_STATUS, "family": "concubine_status", "chat_id": CHAT,
               "message_id": ROOT, "account_id": ACCOUNT, "sent_at": NOW}
    if replacement:
        pending["op_id"] = "replacement"
    env.identity["pending_tasks"] = {(CHAT, ROOT): pending}
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        asyncio.run(concubine._recover_status_query(NOW + 10))
    if replacement:
        assert env.identity == before
    else:
        assert not env.identity["pending_tasks"] and env.identity["concubine_phase"] == "idle"
        assert env.identity[queries.KEYS[kind]] == 0
    env.send.assert_not_awaited()


def legacy_log_read(env, kind="status"):
    env.identity.update(concubine_phase=kind + "_pending", next_concubine_time=NOW + 10)
    env.identity[queries.KEYS[kind]] = ROOT
    env.identity["pending_tasks"] = {(CHAT, ROOT): {
        "cmd": concubine.CMD_CONCUBINE_STATUS, "family": "concubine_status", "chat_id": CHAT,
        "message_id": ROOT, "account_id": ACCOUNT, "sent_at": NOW,
    }}
    return [
        {"event_type": "message", "message_id": ROOT, "chat_id": CHAT, "sender_id": ID,
         "sender_is_bot": False, "account_id": ACCOUNT, "text": concubine.CMD_CONCUBINE_STATUS,
         "server_event_at": NOW, "message_edited": False, "forwarded": False},
        {"event_type": "message", "message_id": ROOT + 1, "chat_id": CHAT, "sender_id": BOT,
         "sender_is_bot": True, "account_id": ACCOUNT, "text": queries.PANEL,
         "reply_to_msg_id": ROOT, "server_event_at": NOW + 1, "message_edited": False, "forwarded": False},
    ]


def write_query_log(monkeypatch, tmp_path, rows):
    monkeypatch.setattr(concubine, "MESSAGES_DIR", str(tmp_path))
    observed = datetime.fromtimestamp(NOW + 2, concubine.TZ_LOCAL)
    log = tmp_path / f"{observed.date().isoformat()}.log"
    log.write_text("\n".join(json.dumps(dict(row, ts=observed.strftime("%Y-%m-%d %H:%M:%S UTC+8"))) for row in rows) + "\n",
                   encoding="utf-8")


@pytest.mark.parametrize("kind", ["status", "gift_status"])
def test_scheduler_recovers_observed_read_from_original_native_log_pair(env, monkeypatch, tmp_path, kind):
    write_query_log(monkeypatch, tmp_path, legacy_log_read(env, kind))
    with state_module.use_identity(ID):
        asyncio.run(concubine.run_concubine_scheduler(NOW + 20))
    assert env.identity["concubine_status_query"]["origin"] == "observed"
    assert env.identity["concubine_status_query"]["started_at"] == NOW
    assert env.identity["concubine_status_query"]["reply_at"] == NOW + 1
    assert env.identity["concubine_affinity"] == 184
    assert env.identity["concubine_phase"] == "idle" and not env.identity["pending_tasks"]
    env.send.assert_not_awaited()
    env.gift.assert_not_awaited()


@pytest.mark.parametrize("kind", ["missing_parent", "sent_only_parent", "edited_parent", "missing_clock", "wrong_account", "wrong_actor",
                                  "later_partial", "later_bad_sender", "later_bad_root", "later_missing_clock", "equal_clock_conflict",
                                  "pending_owner", "pending_chat"])
def test_log_observation_requires_a_complete_latest_source_pair(env, monkeypatch, tmp_path, kind):
    rows = legacy_log_read(env)
    if kind == "missing_parent":
        rows.pop(0)
    elif kind == "sent_only_parent":
        rows[0]["event_type"] = "sent"
    elif kind == "edited_parent":
        rows.append(dict(rows[0], event_type="edit", server_event_at=NOW + 1))
    elif kind == "missing_clock":
        rows[0].pop("server_event_at")
    elif kind == "wrong_account":
        rows[0]["account_id"] = ACCOUNT + 1
    elif kind == "wrong_actor":
        rows[0]["sender_id"] = ID + 1
    elif kind == "pending_owner":
        env.identity["pending_tasks"][(CHAT, ROOT)]["op_id"] = "replacement"
    elif kind == "pending_chat":
        env.identity["pending_tasks"][(CHAT, ROOT)]["chat_id"] = CHAT - 1
    else:
        edit = dict(rows[1], event_type="edit", server_event_at=NOW + 2)
        if kind == "later_partial":
            edit["text"] = queries.PANEL.splitlines()[0]
        elif kind == "later_bad_sender":
            edit["sender_id"] = BOT + 1
        elif kind == "later_bad_root":
            edit["reply_to_msg_id"] = ROOT + 100
        elif kind == "later_missing_clock":
            edit.pop("server_event_at")
        else:
            edit.update(text=queries.PANEL.replace("184", "999"), server_event_at=NOW + 1)
        rows.append(edit)
    write_query_log(monkeypatch, tmp_path, rows)
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        assert not asyncio.run(concubine._recover_concubine_pending_from_message_log(NOW + 20, "status_pending"))
    assert env.identity == before
    env.save.assert_not_called()


@pytest.mark.parametrize("mode", ["false", "exception"])
def test_log_observation_save_failure_holds_the_read_for_same_evidence_replay(env, monkeypatch, tmp_path, mode):
    write_query_log(monkeypatch, tmp_path, legacy_log_read(env))
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        if mode == "exception":
            env.save.side_effect = OSError("save failed")
            with pytest.raises(OSError, match="save failed"):
                asyncio.run(concubine._recover_concubine_pending_from_message_log(NOW + 20, "status_pending"))
        else:
            env.save.return_value = False
            assert asyncio.run(concubine._recover_concubine_pending_from_message_log(NOW + 20, "status_pending"))
        assert env.identity == before
        env.audit.assert_not_awaited()
        env.save.side_effect = None
        env.save.return_value = True
        assert asyncio.run(concubine._recover_concubine_pending_from_message_log(NOW + 20, "status_pending"))
    assert not env.identity["pending_tasks"]
    assert env.identity["concubine_status_query"]["status"] == "complete"


def test_log_observation_does_not_compete_with_the_owned_query_recovery(env, monkeypatch, tmp_path):
    assert asyncio.run(queries.send_query("status"))
    write_query_log(monkeypatch, tmp_path, legacy_log_read(env))
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        assert not asyncio.run(concubine._recover_concubine_pending_from_message_log(NOW + 20, "status_pending"))
    assert env.identity == before


@pytest.mark.parametrize("reason", ["newer_panel", "newer_query", "pending_mutation"])
def test_rejected_log_observation_is_not_mistaken_for_a_failed_save(env, monkeypatch, tmp_path, reason):
    if reason == "newer_query":
        assert asyncio.run(deliver(env, root=ROOT + 10, command_at=NOW + 10, at=NOW + 11, now=NOW + 12))
    rows = legacy_log_read(env)
    if reason == "newer_panel":
        env.identity.update(concubine_affinity=240, concubine_last_snapshot_at=NOW + 10)
    elif reason == "pending_mutation":
        env.identity["pending_tasks"][(CHAT, ROOT + 10)] = {
            "cmd": concubine.CMD_CONCUBINE_GIFT_STONE, "family": "concubine_gift",
            "chat_id": CHAT, "message_id": ROOT + 10, "account_id": ACCOUNT, "sent_at": NOW + 10,
        }
    write_query_log(monkeypatch, tmp_path, rows)
    before = copy.deepcopy(env.identity)
    env.save.reset_mock()
    with state_module.use_identity(ID):
        assert not asyncio.run(concubine._recover_concubine_pending_from_message_log(NOW + 20, "status_pending"))
        assert not asyncio.run(concubine._recover_concubine_pending_from_message_log(NOW + 30, "status_pending"))
    assert env.identity == before
    env.save.assert_not_called()
    env.send.assert_not_awaited()


@pytest.mark.parametrize("row_index", [0, 1])
@pytest.mark.parametrize("field,value", [("message_edited", None), ("message_edited", 0),
                                        ("forwarded", None), ("forwarded", 0), ("forwarded", True)])
def test_log_observation_does_not_invent_absent_or_invalid_provenance(env, monkeypatch, tmp_path, row_index, field, value):
    rows = legacy_log_read(env)
    if value is None:
        rows[row_index].pop(field)
    else:
        rows[row_index][field] = value
    write_query_log(monkeypatch, tmp_path, rows)
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        assert not asyncio.run(concubine._recover_concubine_pending_from_message_log(NOW + 20, "status_pending"))
    assert env.identity == before
    env.save.assert_not_called()


@pytest.mark.parametrize("kind", ["normal", "channel", "edited_command", "forwarded_command", "forwarded_reply",
                                  "missing_command_edit", "missing_reply_edit", "missing_command_forward", "missing_reply_forward"])
def test_actual_message_log_producer_preserves_observed_read_source(env, monkeypatch, tmp_path, kind):
    legacy_log_read(env)
    parent = SimpleNamespace(id=ROOT, chat_id=CHAT, sender_id=ID, raw_text=concubine.CMD_CONCUBINE_STATUS,
                             sender=SimpleNamespace(bot=False), date=datetime.fromtimestamp(NOW, timezone.utc), edit_date=None, fwd_from=None)
    reply = SimpleNamespace(id=ROOT + 1, chat_id=CHAT, sender_id=BOT, raw_text=queries.PANEL,
                            sender=SimpleNamespace(bot=True), date=datetime.fromtimestamp(NOW + 1, timezone.utc),
                            reply_to=SimpleNamespace(reply_to_msg_id=ROOT), edit_date=None, fwd_from=None)
    if kind == "channel":
        parent.sender_id = -int(f"100{ID}")
        parent.sender = SimpleNamespace(title="fixture channel")
    elif kind == "edited_command":
        parent.edit_date = datetime.fromtimestamp(NOW + .5, timezone.utc)
    elif kind == "forwarded_command":
        parent.fwd_from = SimpleNamespace(from_id=ID + 1)
    elif kind == "forwarded_reply":
        reply.fwd_from = SimpleNamespace(from_id=BOT + 1)
    elif kind.startswith("missing_"):
        target = parent if "command" in kind else reply
        delattr(target, "edit_date" if kind.endswith("edit") else "fwd_from")
    _, command_row = app_message_log._build_message_log_payload(parent)
    _, reply_row = app_message_log._build_message_log_payload(reply)
    write_query_log(monkeypatch, tmp_path, [command_row, reply_row])
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        assert asyncio.run(concubine._recover_concubine_pending_from_message_log(NOW + 20, "status_pending")) is (kind in {"normal", "channel"})
    if kind in {"normal", "channel"}:
        assert env.identity["concubine_status_query"]["actor_id"] == parent.sender_id
    else:
        assert env.identity == before


@pytest.mark.parametrize("kind", ["edit", "forward"])
def test_actual_message_log_producer_keeps_nested_source_flags(kind):
    message = SimpleNamespace(edit_date=None, fwd_from=None)
    setattr(message, "edit_date" if kind == "edit" else "fwd_from", datetime.fromtimestamp(NOW, timezone.utc))
    event = SimpleNamespace(id=ROOT, chat_id=CHAT, sender_id=ID, raw_text=concubine.CMD_CONCUBINE_STATUS, message=message)
    _, row = app_message_log._build_message_log_payload(event)
    assert row["message_edited"] is (kind == "edit")
    assert row["forwarded"] is (kind == "forward")


@pytest.mark.parametrize("route", ["native", "passive"])
def test_absent_original_edit_evidence_is_not_invented_by_reply_resolver(env, route):
    parent = SimpleNamespace(id=ROOT, chat_id=CHAT, sender_id=ID, raw_text=concubine.CMD_CONCUBINE_STATUS,
                             date=datetime.fromtimestamp(NOW, timezone.utc))
    event = SimpleNamespace(id=ROOT + 1, chat_id=CHAT, sender_id=BOT, date=datetime.fromtimestamp(NOW + 1, timezone.utc),
                            reply_to=SimpleNamespace(reply_to_msg_id=ROOT), get_reply_message=AsyncMock(return_value=parent))
    before = copy.deepcopy(env.identity)
    async def process():
        resolved, context = await app._resolve_event_reply(event)
        assert "reply_to_command_edited" not in context
        if route == "native":
            return await app._handle_routed_reply_event(event, queries.PANEL, NOW + 2, resolved, context)
        return await passive_inbox.handle_passive_module_card(
            from_telegram_event(event, queries.PANEL, context), now=NOW + 2)
    assert not asyncio.run(process())
    assert env.identity == before


@pytest.mark.parametrize("diagnostic_fails", [False, True])
def test_missing_identity_diagnostic_does_not_consume_observation_replay(env, monkeypatch, diagnostic_fails):
    state_module._meta_state["identity_ids"] = []
    state_module._meta_state["identity_states"] = {}
    if diagnostic_fails:
        monkeypatch.setattr(passive_inbox, "_record_passive_event", Mock(side_effect=OSError("diagnostic")))
    no_hint = lambda _e, _p, context: context.pop("send_as_id")
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(deliver(env, "passive", corrupt=no_hint))
    assert env.identity == before
    assert not passive_inbox._observed_passive_events
    assert not passive_inbox._passive_stats["changed"]
    state_module._meta_state["identity_ids"] = [ID]
    state_module._meta_state["identity_states"] = {ID: env.identity}
    assert asyncio.run(deliver(env, "passive", corrupt=no_hint))
    assert env.identity["concubine_status_query"]["origin"] == "observed"


def test_stale_legacy_read_exits_recovery_hold_without_overwriting_the_new_panel(env, monkeypatch, tmp_path):
    write_query_log(monkeypatch, tmp_path, legacy_log_read(env))
    env.identity.update(concubine_affinity=240, concubine_last_snapshot_at=NOW + 10)
    with state_module.use_identity(ID):
        asyncio.run(concubine.run_concubine_scheduler(NOW + 20))
    assert env.identity["concubine_phase"] == "idle"
    assert env.identity["concubine_status_msg_id"] == 0
    assert env.identity["next_concubine_time"] > NOW + 20
    assert env.identity["concubine_affinity"] == 240
    assert env.identity["concubine_last_snapshot_at"] == NOW + 10
    env.send.assert_not_awaited()


@pytest.mark.parametrize("source", ["command", "reply"])
def test_forwarding_evidence_survives_actual_reply_resolution_and_passive_wrapper(env, source):
    parent = SimpleNamespace(id=ROOT, chat_id=CHAT, sender_id=ID, raw_text=concubine.CMD_CONCUBINE_STATUS,
                             date=datetime.fromtimestamp(NOW, timezone.utc), edit_date=None)
    event = SimpleNamespace(id=ROOT + 1, chat_id=CHAT, sender_id=BOT, date=datetime.fromtimestamp(NOW + 1, timezone.utc),
                            reply_to=SimpleNamespace(reply_to_msg_id=ROOT), get_reply_message=AsyncMock(return_value=parent))
    setattr(parent if source == "command" else event, "fwd_from", SimpleNamespace(from_id=ID + 1))
    before = copy.deepcopy(env.identity)
    async def process():
        resolved, context = await app._resolve_event_reply(event)
        assert not await app._handle_routed_reply_event(event, queries.PANEL, NOW + 2, resolved, context)
        assert not await passive_inbox.handle_passive_module_card(
            from_telegram_event(event, queries.PANEL, context), now=NOW + 2,
        )
    asyncio.run(process())
    assert env.identity == before
