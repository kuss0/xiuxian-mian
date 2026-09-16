import asyncio
import copy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import app, persistence, state as state_module
from model.features import concubine
from model.features import concubine_external_events as external
from tests import test_concubine_query_lifecycle as query_tests
from tests.test_concubine_query_lifecycle import ACCOUNT, BOT, CHAT, ID, NOW, PANEL, ROOT


base_env = query_tests.env
FIELD = "concubine_external_observation"
NAME = "\u51cc\u7389\u7075"
GAIN = f"@query_owner \u4f8d\u59be\u3010{NAME}\u3011\u5411\u4f60\u5fae\u5fae\u988c\u9996\uff0c\u4f60\u4eec\u7684\u60c5\u7f18\u589e\u52a0\u4e86 30 \u70b9\u3002"
LOSS = f"@query_owner \u7684\u4f8d\u59be\u3010{NAME}\u3011\u88ab\u5357\u9647\u4faf\u63b3\u8d70\u3002"
MOON = (
    "\u3010\u6708\u6bbf\u56e0\u679c \u00b7 \u5357\u5bab\u5a49\u5165\u4e16\u3011\n"
    "\u9053\u53cb @query_owner \u5df2\u4ee5 LDC \u5951\u7ea6\u8bf7\u5f97 \u3010\u5357\u5bab\u5a49\u3011 \u76f8\u968f\u3002\n"
    "\u539f\u4f8d\u59be\uff1a\u3010\u65e0\u3011 \u5df2\u88ab\u66ff\u6362\u3002\n"
    "\u5357\u5bab\u5a49\u4e0d\u4f1a\u88ab\u5357\u9647\u4faf\u593a\u8d70\uff0c\u4e5f\u4e0d\u4f1a\u88ab\u6d1e\u5e9c\u8bbf\u5ba2\u62d0\u8d70\u3002\n"
    "\u521d\u59cb\u60c5\u7f18\uff1a120\uff0c\u53ef\u7528 .\u6211\u7684\u4f8d\u59be \u67e5\u770b\u3002"
)
SELFLESS = (
    f"@query_owner \u3010\u65e0\u6211\u4e4b\u5883\u3011\n\u4f8d\u59be {NAME} \u633a\u8eab\u800c\u51fa\uff0c"
    "\u8017\u5c3d\u4e0e\u4f60\u7684\u6240\u6709\u60c5\u7f18\u4e3a\u4f60\u6321\u4e0b\u6b64\u52ab..."
)


@pytest.fixture
def env(base_env):
    base_env.identity.update(concubine_auto_reacquire=True, concubine_last_snapshot_at=NOW - 2,
                             next_concubine_time=NOW + 86400)
    return base_env


def event(**changes):
    return SimpleNamespace(**dict(dict(id=ROOT - 1, chat_id=CHAT, sender_id=BOT,
                                       server_event_at=NOW - 1), **changes))


async def observe(env, text=GAIN, *, loss=False, source=None, **kwargs):
    source = source if source is not None else event()
    with state_module.use_identity(ID):
        if loss:
            return await concubine.handle_concubine_loss_broadcast(text, env.clock[0], source, **kwargs)
        return await concubine.handle_concubine_affinity_event(
            text, env.clock[0], source, require_identity_hint=True, **kwargs)


async def tick(env):
    with state_module.use_identity(ID):
        return await concubine.run_concubine_scheduler(env.clock[0])


@pytest.mark.parametrize("text,loss", [(GAIN, False), (MOON, False), (SELFLESS, False), (LOSS, True)])
def test_external_facts_require_calibration_not_direct_projection_or_cleanup(env, text, loss):
    env.identity.update(concubine_phase="heart_pending", concubine_heart_msg_id=ROOT + 50,
                         concubine_heart_due_at=NOW + 10000, concubine_tianji_due_at=NOW + 20000)
    before = copy.deepcopy(env.identity)
    assert asyncio.run(observe(env, text, loss=loss))
    assert env.identity[FIELD]["status"] == "pending"
    assert env.identity["concubine_availability"] == before["concubine_availability"]
    with state_module.use_identity(ID):
        assert not concubine._has_available_partner()
    for key in ("concubine_phase", "concubine_heart_msg_id", "concubine_affinity", "concubine_name",
                "concubine_heart_due_at", "concubine_tianji_due_at", "pending_tasks", "next_concubine_time"):
        assert env.identity[key] == before[key]
    env.send.assert_not_awaited()


@pytest.mark.parametrize("field,bad", [("id", 0), ("id", True), ("id", str(ROOT)), ("chat_id", CHAT - 1),
                                     ("sender_id", BOT + 1), ("sender_id", str(BOT)),
                                     ("server_event_at", 0), ("server_event_at", True),
                                     ("server_event_at", NOW + 1), ("server_event_at", NOW - 3)])
def test_external_observation_rejects_untrusted_or_stale_source(env, field, bad):
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(observe(env, source=event(**{field: bad})))
    assert env.identity == before


def test_contract_owner_cannot_be_replaced_by_an_unrelated_mention(env):
    before = copy.deepcopy(env.identity)
    text = MOON.replace("@query_owner", "@somebody_else") + "\n@query_owner"
    assert not asyncio.run(observe(env, text))
    assert env.identity == before


def test_loss_of_a_previous_partner_cannot_erase_replacement(env):
    env.identity["concubine_name"] = "replacement"
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(observe(env, LOSS, loss=True))
    assert env.identity == before


def test_duplicate_observations_coalesce_across_sqlite_reload(env):
    assert asyncio.run(observe(env))
    assert persistence.save_state() and persistence.load_state()
    env.identity = state_module.get_identity_state(ID)
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(observe(env))
    assert env.identity == before


def test_scheduler_calibrates_before_future_business_timer_then_resumes_from_panel(env):
    assert asyncio.run(observe(env))
    asyncio.run(tick(env))
    assert env.send.await_args.args[0] == concubine.CMD_CONCUBINE_STATUS
    assert env.send.await_count == 1
    assert asyncio.run(query_tests.reply(env))
    assert env.identity[FIELD]["status"] == "complete"
    assert env.identity["concubine_affinity"] == 184
    assert env.identity["concubine_availability"] == "available"


@pytest.mark.parametrize("mode", ["false", "exception"])
def test_external_observation_save_failure_rolls_back_all_state(env, mode):
    before = copy.deepcopy(env.identity)
    if mode == "false":
        env.save.return_value = False
        assert not asyncio.run(observe(env))
    else:
        env.save.side_effect = OSError("observation save")
        with pytest.raises(OSError):
            asyncio.run(observe(env))
    assert env.identity == before
    env.audit.assert_not_awaited()


def test_disabled_role_keeps_observations_without_starting_calibration(env):
    state_module.set_identity_enabled(ID, False)
    env.identity.update(concubine_enabled=False, concubine_tianji_enabled=False)
    assert asyncio.run(observe(env))
    asyncio.run(tick(env))
    assert env.identity[FIELD]["status"] == "pending"
    env.send.assert_not_awaited()


def test_external_reply_is_not_terminal_evidence_for_its_parent_command(env):
    parent = SimpleNamespace(id=ROOT - 10, chat_id=CHAT, sender_id=ID, raw_text=".fixture")
    context = {"send_as_id": ID, "account_id": ACCOUNT, "chat_id": CHAT,
               "root_msg_id": ROOT - 10, "reply_to_msg_id": ROOT - 10,
               "reply_to_sender_id": ID, "sender_id": BOT, "family": "fixture"}
    assert not asyncio.run(app._handle_routed_reply_event(event(), GAIN, NOW, parent, context))
    assert env.identity[FIELD]["status"] == "pending"


@pytest.mark.parametrize("text,panel,name,affinity,available", [
    (GAIN, PANEL.replace("184", "300"), NAME, 300, "available"),
    (SELFLESS, PANEL.replace("184", "0"), NAME, 0, "available"),
    (MOON, PANEL.replace(NAME, "\u5357\u5bab\u5a49").replace("184", "120"), "\u5357\u5bab\u5a49", 120, "available"),
    (LOSS, "\u4f60\u5c1a\u65e0\u7ea2\u989c\u77e5\u5df1\u3002", "", 0, "no_partner"),
])
def test_owned_absolute_panel_reconciles_each_external_kind(env, text, panel, name, affinity, available):
    env.identity["concubine_tianji_last_error"] = "\u60c5\u7f18\u6062\u590d\u4e2d\uff08270/300\uff09"
    assert asyncio.run(observe(env, text, loss=text == LOSS))
    asyncio.run(tick(env))
    query_tests.receipt(env)
    assert asyncio.run(query_tests.reply(env, text=panel))
    assert env.identity[FIELD]["status"] == "complete"
    assert env.identity["concubine_name"] == name
    assert env.identity["concubine_affinity"] == affinity
    assert env.identity["concubine_availability"] == available
    assert (CHAT, ROOT) not in env.identity["pending_tasks"]
    if text == MOON:
        assert env.identity["concubine_auto_reacquire"] is False
    if text == GAIN:
        assert not env.identity["concubine_tianji_last_error"]
    assert persistence.save_state() and persistence.load_state()
    assert state_module.get_identity_state(ID)[FIELD] == env.identity[FIELD]


@pytest.mark.parametrize("mode", ["ok", "false", "exception"])
def test_miniapp_absolute_panel_reconciliation_is_atomic(env, mode):
    assert asyncio.run(observe(env))
    before = copy.deepcopy(env.identity)
    env.save.return_value = mode != "false"
    env.save.side_effect = OSError("panel save") if mode == "exception" else None
    with state_module.use_identity(ID):
        if mode == "exception":
            with pytest.raises(OSError):
                concubine.sync_concubine_miniapp_status(PANEL, NOW)
        else:
            result = concubine.sync_concubine_miniapp_status(PANEL, NOW)
            assert result["handled"] == (mode == "ok")
    if mode == "ok":
        assert env.identity[FIELD]["status"] == "complete"
        assert env.identity["concubine_affinity"] == 184
        assert env.identity["concubine_last_snapshot_at"] == NOW
    else:
        assert env.identity == before
    env.send.assert_not_awaited()


@pytest.mark.parametrize("mode", ["false", "exception"])
def test_native_panel_and_observation_completion_rollback_together(env, mode):
    assert asyncio.run(observe(env))
    asyncio.run(tick(env))
    query_tests.receipt(env)
    before = copy.deepcopy(env.identity)
    if mode == "false":
        env.save.return_value = False
        assert not asyncio.run(query_tests.reply(env))
    else:
        env.save.side_effect = OSError("panel save")
        with pytest.raises(OSError):
            asyncio.run(query_tests.reply(env))
    assert env.identity == before
    env.save.return_value, env.save.side_effect = True, None
    assert asyncio.run(query_tests.reply(env))
    assert env.identity[FIELD]["status"] == "complete"
    assert env.identity["concubine_status_query"]["status"] == "complete"


def test_query_dispatched_before_newer_observation_cannot_reconcile_it(env):
    assert asyncio.run(query_tests.send_query("status"))
    env.clock[0] = NOW + 1
    assert asyncio.run(observe(env, source=event(server_event_at=NOW + 1)))
    assert asyncio.run(query_tests.reply(env, observed_at=NOW + 2))
    assert env.identity["concubine_status_query"]["status"] == "complete"
    assert env.identity[FIELD]["status"] == "pending"
    assert env.identity["concubine_affinity"] == 100
    env.clock[0] = NOW + 3
    env.send.return_value = SimpleNamespace(id=ROOT, chat_id=CHAT, sent_at=NOW + 3.5, send_started_at=NOW + 3)
    asyncio.run(tick(env))
    assert env.send.await_count == 2
    assert asyncio.run(query_tests.reply(env, observed_at=NOW + 4))
    assert env.identity[FIELD]["status"] == "complete"


def test_retry_throttle_is_preserved_when_new_observations_arrive(env, monkeypatch):
    send = AsyncMock(return_value=False)
    monkeypatch.setattr(concubine, "_send_status_command", send)
    assert asyncio.run(observe(env))
    asyncio.run(tick(env))
    assert env.identity[FIELD]["retry_at"] == NOW + 60
    for offset in (1, 30, 59):
        env.clock[0] = NOW + offset
        assert asyncio.run(observe(env, source=event(id=ROOT + offset, server_event_at=NOW + offset)))
        asyncio.run(tick(env))
    assert send.await_count == 1
    env.clock[0] = NOW + 60
    asyncio.run(tick(env))
    assert send.await_count == 2
    env.audit.assert_awaited_once()


def test_due_scan_reaches_external_calibration_before_old_business_deadline(env, monkeypatch):
    assert asyncio.run(observe(env))
    monkeypatch.setattr(app, "_is_identity_account_offline", lambda _identity: False)
    monkeypatch.setattr(app, "is_identity_weak", lambda *_args: False)
    monkeypatch.setattr(app, "has_phaseful_summary_block", lambda _now: False)
    monkeypatch.setattr(app, "console_log", Mock())
    asyncio.run(app._run_due_concubine_schedulers(NOW))
    assert env.send.await_count == 1
    assert env.send.await_args.args[0] == concubine.CMD_CONCUBINE_STATUS


@pytest.mark.parametrize("transient", [False, True])
def test_outer_scheduler_failure_does_not_rewrite_external_retry_or_business_plan(env, transient):
    assert asyncio.run(observe(env))
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        app._record_due_concubine_candidate_failure(now=NOW, reason="fixture", transient=transient)
    assert env.identity[FIELD] == before[FIELD]
    assert env.identity["next_concubine_time"] == before["next_concubine_time"]
    assert env.identity["concubine_phase"] == before["concubine_phase"]


@pytest.mark.parametrize("mode", ["false", "exception", "cancel"])
def test_observation_broadcast_replays_after_failed_save_or_cancelled_notification(env, mode):
    state_module.set_identity_enabled(ID, False)
    state_module.set_global_enabled(False)
    before = copy.deepcopy(env.identity)
    if mode == "false":
        env.save.return_value = False
        asyncio.run(app._dispatch_concubine_affinity_fallbacks(event(), GAIN, NOW))
        assert env.identity == before
    elif mode == "exception":
        env.save.side_effect = OSError("broadcast save")
        with pytest.raises(OSError):
            asyncio.run(app._dispatch_concubine_affinity_fallbacks(event(), GAIN, NOW))
        assert env.identity == before
    else:
        env.audit.side_effect = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(app._dispatch_concubine_affinity_fallbacks(event(), GAIN, NOW))
        assert env.identity[FIELD]["status"] == "pending"
    env.save.return_value, env.save.side_effect, env.audit.side_effect = True, None, None
    asyncio.run(app._dispatch_concubine_affinity_fallbacks(event(), GAIN, NOW))
    assert env.identity[FIELD]["status"] == "pending"
    assert env.identity["concubine_affinity"] == before["concubine_affinity"]
    env.send.assert_not_awaited()


@pytest.mark.parametrize("context", [False, "", [], 0])
def test_explicit_malformed_context_is_not_an_empty_trusted_context(env, context):
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(observe(env, reply_context=context))
    assert env.identity == before


def test_pending_new_partner_observations_use_the_contract_owner_not_old_snapshot(env):
    assert asyncio.run(observe(env, MOON))
    env.clock[0] = NOW + 1
    later = SELFLESS.replace(NAME, "\u5357\u5bab\u5a49")
    assert asyncio.run(observe(env, later, source=event(id=ROOT, server_event_at=NOW)))
    assert env.identity[FIELD]["event"]["facts"]["kind"] == "selfless"
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(observe(env, LOSS, loss=True, source=event(id=ROOT + 1, server_event_at=NOW + 1)))
    assert env.identity == before


def test_external_observation_can_calibrate_a_ready_but_unsent_puzzle(env):
    env.identity.update(concubine_phase="puzzle_ready", concubine_fragment_xutian_count=4,
                        concubine_fragment_xutian_total=4)
    assert asyncio.run(observe(env))
    asyncio.run(tick(env))
    env.send.assert_awaited_once()
    assert env.send.await_args.args[0] == concubine.CMD_CONCUBINE_STATUS
    assert asyncio.run(query_tests.reply(env))
    assert env.identity[FIELD]["status"] == "complete"


def test_authoritative_edit_clock_not_original_message_time_controls_barrier(env):
    source = SimpleNamespace(id=ROOT, chat_id=CHAT, sender_id=BOT,
                             date=datetime.fromtimestamp(NOW - 10, timezone.utc),
                             edit_date=datetime.fromtimestamp(NOW - 1, timezone.utc))
    assert not asyncio.run(observe(env, source=source))
    assert asyncio.run(observe(env, source=source, event_type="edit"))
    assert env.identity[FIELD]["event"]["at"] == NOW - 1
    assert env.identity[FIELD]["event"]["event_type"] == "edit"


@pytest.mark.parametrize("amount", ["0", "1.5", "-1", "1,00", "9223372036854775808"])
def test_external_gain_parser_rejects_invalid_bounded_numbers(env, amount):
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(observe(env, GAIN.replace("30", amount)))
    assert env.identity == before


@pytest.mark.parametrize("bad", [None, False, [], "pending", {"status": "pending"}])
def test_malformed_external_record_cannot_resume_or_silently_overwrite_itself(env, bad):
    env.identity[FIELD] = bad
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        assert external.invalid()
        assert not concubine._has_available_partner()
        assert not concubine.sync_concubine_miniapp_status(PANEL, NOW)["handled"]
        concubine.restore_concubine_runtime(NOW)
    assert not asyncio.run(observe(env))
    asyncio.run(tick(env))
    assert env.identity == before
    env.send.assert_not_awaited()


def routed_context(**changes):
    return dict({"send_as_id": ID, "account_id": ACCOUNT, "chat_id": CHAT,
                 "root_msg_id": ROOT - 10, "reply_to_msg_id": ROOT - 10,
                 "reply_to_sender_id": ID, "sender_id": BOT,
                 "event_type": "message", "server_event_at": NOW - 1}, **changes)


def parent(**changes):
    return SimpleNamespace(**dict({"id": ROOT - 10, "chat_id": CHAT, "sender_id": ID,
                                   "raw_text": ".fixture"}, **changes))


def test_unmentioned_gain_requires_explicit_owned_parent(env):
    assert asyncio.run(observe(env, GAIN.replace("@query_owner ", ""),
                               reply_to=parent(), reply_context=routed_context()))
    assert env.identity[FIELD]["event"]["binding"] == "reply"
    assert env.identity[FIELD]["event"]["root_msg_id"] == ROOT - 10


@pytest.mark.parametrize("key,bad", [
    ("send_as_id", ID + 1), ("send_as_id", str(ID)), ("account_id", ACCOUNT + 1),
    ("chat_id", CHAT - 1), ("sender_id", BOT + 1), ("reply_to_sender_id", ID + 1),
    ("root_msg_id", ROOT - 9), ("reply_to_msg_id", ROOT - 9),
    ("event_type", "edit"), ("server_event_at", NOW - 2), ("server_event_at", True),
])
def test_conflicting_routed_metadata_is_not_observation_evidence(env, key, bad):
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(observe(env, reply_to=parent(), reply_context=routed_context(**{key: bad})))
    assert env.identity == before


@pytest.mark.parametrize("key,bad", [
    ("id", 0), ("id", -1), ("id", ROOT), ("id", True), ("id", str(ROOT - 10)),
    ("chat_id", CHAT - 1), ("sender_id", ID + 1), ("sender_id", True),
])
def test_unmentioned_gain_rejects_invalid_or_unowned_parent(env, key, bad):
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(observe(env, GAIN.replace("@query_owner ", ""),
                                   reply_to=parent(**{key: bad}), reply_context=routed_context()))
    assert env.identity == before


@pytest.mark.parametrize("root", [-1, True, str(ROOT - 10), ROOT + 100])
def test_mentioned_observation_cannot_persist_a_malformed_parent(env, root):
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(observe(env, reply_to=parent(id=root)))
    assert env.identity == before


def test_reply_context_cannot_override_the_event_original_parent(env):
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(observe(env, GAIN.replace("@query_owner ", ""),
                                   source=event(reply_to_msg_id=ROOT - 11),
                                   reply_to=parent(), reply_context=routed_context()))
    assert env.identity == before


@pytest.mark.parametrize("placement", ["event", "message"])
def test_forwarded_game_text_does_not_become_a_new_observation(env, placement):
    changes = {"fwd_from": SimpleNamespace()} if placement == "event" else {
        "message": SimpleNamespace(fwd_from=SimpleNamespace()),
    }
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(observe(env, source=event(**changes)))
    assert env.identity == before


@pytest.mark.parametrize("duplicate", [False, True])
def test_username_aliases_are_accepted_only_when_unique(env, duplicate):
    state_module.update_send_as_profile(ID, username="renamed_owner")
    if duplicate:
        state_module.set_identity_account(ID + 1, ACCOUNT)
        state_module.update_send_as_profile(ID + 1, username="second_owner", username_aliases=["QUERY_OWNER"])
    before = copy.deepcopy(env.identity)
    assert asyncio.run(observe(env)) == (not duplicate)
    if duplicate:
        assert env.identity == before
    else:
        assert env.identity[FIELD]["event"]["facts"]["owner"] == "query_owner"


@pytest.mark.parametrize("field,bad", [
    ("identity_id", ID + 1), ("identity_id", str(ID)), ("account_id", True), ("status", []),
    ("retry_at", True), ("retry_at", -1), ("retry_at", float("inf")), ("resolved_at", NOW),
    ("event.msg_id", 0), ("event.msg_id", True), ("event.chat_id", "1"),
    ("event.sender_id", True), ("event.at", False), ("event.at", float("inf")),
    ("event.root_msg_id", -1), ("event.root_msg_id", True), ("event.root_msg_id", ROOT + 100),
    ("event.event_type", "edited"), ("event.binding", "reply"), ("event.facts.amount", True),
])
def test_corrupt_observation_fields_remain_quarantined(env, field, bad):
    assert asyncio.run(observe(env, GAIN.replace("30", "1")))
    target = env.identity[FIELD]
    parts = field.split(".")
    for part in parts[:-1]:
        target = target[part]
    target[parts[-1]] = bad
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        assert external.record() is None
        assert external.next_at() is None
        assert not concubine._has_available_partner()
    assert not asyncio.run(observe(env, source=event(id=ROOT, server_event_at=NOW)))
    asyncio.run(tick(env))
    assert env.identity == before
    env.send.assert_not_awaited()


def test_rebound_account_does_not_take_over_prior_observation(env):
    assert asyncio.run(observe(env))
    state_module.set_identity_account(ID, ACCOUNT + 1)
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(observe(env, source=event(id=ROOT, server_event_at=NOW)))
    asyncio.run(tick(env))
    assert env.identity == before
    env.send.assert_not_awaited()


@pytest.mark.parametrize("error", [RuntimeError, asyncio.CancelledError])
def test_calibration_cancel_or_send_exception_retains_unknown_request(env, error):
    assert asyncio.run(observe(env))
    env.send.side_effect = error()
    with pytest.raises(error):
        asyncio.run(tick(env))
    assert env.identity[FIELD]["status"] == "pending"
    assert env.identity[FIELD]["retry_at"] == NOW + 60
    assert env.identity["concubine_status_query"]["status"] == "unknown"
    env.send.side_effect = None
    env.clock[0] += 59
    asyncio.run(tick(env))
    env.send.assert_awaited_once()


@pytest.mark.parametrize("phase", ["idle", "puzzle_ready"])
def test_miniapp_snapshot_resolves_pending_observation_without_mutation(env, phase):
    env.identity["concubine_phase"] = phase
    assert asyncio.run(observe(env))
    with state_module.use_identity(ID):
        assert concubine.sync_concubine_miniapp_status(PANEL, NOW)["handled"]
    assert env.identity[FIELD]["status"] == "complete"
    env.send.assert_not_awaited()


@pytest.mark.parametrize("suffix", ["\n" + GAIN, "\n" + LOSS, "\n" + MOON, "\n@other_owner"])
def test_conflicting_or_duplicate_external_text_is_not_admitted(env, suffix):
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(observe(env, GAIN + suffix))
    assert env.identity == before


@pytest.mark.parametrize("via", ["native", "miniapp"])
def test_pending_affinity_observation_requires_an_absolute_value_not_cached_red_dust_data(env, via):
    env.identity["concubine_kind"] = "\u7ea2\u5c18\u9053\u4fa3"
    panel = PANEL.replace("\u9053\u5fc3\u4f8d\u59be", "\u7ea2\u5c18\u9053\u4fa3").replace("\n\u60c5\u7f18\u503c: 184", "")
    assert asyncio.run(observe(env))
    if via == "native":
        asyncio.run(tick(env))
        assert asyncio.run(query_tests.reply(env, text=panel))
        assert env.identity["concubine_status_query"]["status"] == "complete"
    else:
        with state_module.use_identity(ID):
            assert not concubine.sync_concubine_miniapp_status(panel, NOW)["handled"]
    assert env.identity[FIELD]["status"] == "pending"
    assert env.identity["concubine_affinity"] == 100
    assert env.identity["concubine_last_snapshot_at"] == NOW - 2


def test_absence_contradicting_permanent_partner_does_not_clear_observation(env):
    env.identity["concubine_name"] = "\u5357\u5bab\u5a49"
    assert asyncio.run(observe(env, SELFLESS.replace(NAME, "\u5357\u5bab\u5a49")))
    asyncio.run(tick(env))
    assert asyncio.run(query_tests.reply(env, text="\u4f60\u5c1a\u65e0\u7ea2\u989c\u77e5\u5df1\u3002"))
    assert env.identity[FIELD]["status"] == "pending"
    assert env.identity["concubine_last_snapshot_at"] == NOW - 2
    with state_module.use_identity(ID):
        assert not concubine._has_available_partner()


@pytest.mark.parametrize("route", ["gain", "new_loss", "edit_loss"])
def test_broadcast_stops_at_unique_owner_even_if_notification_deletes_another_role(env, route, monkeypatch):
    state_module.set_identity_account(ID + 1, ACCOUNT)
    state_module.update_send_as_profile(ID + 1, username="other_owner")
    monkeypatch.setattr(app, "_dispatch_broadcast_handlers", AsyncMock(return_value=False))

    async def notify(*_args, **_kwargs):
        state_module.remove_identity(ID + 1)

    env.audit.side_effect = notify
    if route == "gain":
        asyncio.run(app._dispatch_concubine_affinity_fallbacks(event(), GAIN, NOW))
    elif route == "new_loss":
        asyncio.run(app._dispatch_new_message_broadcasts(event(), LOSS, NOW))
    else:
        asyncio.run(app._dispatch_concubine_loss_broadcast_fallbacks(event(), LOSS, NOW, event_type="edit"))
    assert not state_module.has_identity(ID + 1)
    assert env.identity[FIELD]["status"] == "pending"
    env.audit.assert_awaited_once()


def test_calibration_dispatch_waits_until_it_is_newer_than_the_external_clock(env):
    assert asyncio.run(observe(env, source=event(server_event_at=NOW)))
    asyncio.run(tick(env))
    env.send.assert_not_awaited()
    env.clock[0] = NOW + 1
    env.send.return_value = SimpleNamespace(id=ROOT, chat_id=CHAT, sent_at=NOW + 1.5, send_started_at=NOW + 1)
    asyncio.run(tick(env))
    env.send.assert_awaited_once()
    assert asyncio.run(query_tests.reply(env, observed_at=NOW + 2))
    assert env.identity[FIELD]["status"] == "complete"


def test_new_partner_panel_does_not_inherit_old_partner_future_cooldowns(env):
    keys = ("dream_due_at", "tianji_due_at", "heart_due_at")
    env.identity.update({"concubine_" + key: NOW + 86400 for key in keys})
    assert asyncio.run(observe(env, MOON))
    asyncio.run(tick(env))
    panel = PANEL.replace(NAME, "\u5357\u5bab\u5a49")
    assert asyncio.run(query_tests.reply(env, text=panel))
    parsed = concubine._parse_status_panel(panel, NOW + 1)
    assert env.identity[FIELD]["status"] == "complete"
    for key in keys:
        assert env.identity["concubine_" + key] == parsed[key]
