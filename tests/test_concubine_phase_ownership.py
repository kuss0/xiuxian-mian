import asyncio
import copy
from types import SimpleNamespace

import pytest

from model import app, runtime, state as state_module
from model.features import concubine
from tests import test_concubine_divination_lifecycle as divination_tests
from tests import test_concubine_external_events as external_tests
from tests import test_concubine_fragment_actions as fragment_tests
from tests import test_concubine_fragment_lifecycle as fragment_query_tests
from tests import test_concubine_gift_lifecycle as gift_tests
from tests import test_concubine_greet_lifecycle as greet_tests
from tests import test_concubine_query_lifecycle as query_tests
from tests import test_concubine_reacquire_lifecycle as reacquire_tests
from tests import test_concubine_voyage_actions as voyage_tests
from tests import test_concubine_voyage_query as voyage_query_tests


base_env = fragment_tests.env
divination_env = divination_tests.env
fragment_env = fragment_tests.env
fragment_query_env = fragment_query_tests.env
gift_env = gift_tests.env
greet_env = greet_tests.env
query_env = query_tests.env
reacquire_env = reacquire_tests.env
voyage_env = voyage_tests.env
voyage_query_env = voyage_query_tests.env
KINDS = ("status", "gift_status", "fragment", "voyage_status", "gift_bag", "gift", "greet",
         "dream", "puzzle", "voyage", "voyage_return", "tianji", "reacquire")
READS = ("status", "gift_status", "fragment", "voyage_status", "gift_bag")


@pytest.fixture
def flow(request, monkeypatch):
    kind = request.param
    field, nested = "concubine_status_query", False
    source = concubine.CONCUBINE_QUERY_SOURCE
    if kind in {"status", "gift_status"}:
        module, fixture, text = query_tests, "query_env", query_tests.PANEL
    elif kind == "fragment":
        module, fixture, text = fragment_query_tests, "fragment_query_env", fragment_query_tests.panel()
    elif kind == "voyage_status":
        module, fixture, text = voyage_query_tests, "voyage_query_env", voyage_query_tests.IDLE
    elif kind in {"gift_bag", "gift"}:
        module, fixture = gift_tests, "gift_env"
        field, nested, source = gift_tests.FIELD, True, "concubine_gift"
        text = gift_tests.BAG if kind == "gift_bag" else gift_tests.SUCCESS
    elif kind == "greet":
        module, fixture, text = greet_tests, "greet_env", greet_tests.SUCCESS
        field, source = greet_tests.FIELD, "concubine_greet"
    elif kind in {"dream", "puzzle"}:
        module, fixture = fragment_tests, "fragment_env"
        field, nested, source = fragment_tests.FIELD, True, "concubine_fragments"
        text = fragment_tests.DREAM if kind == "dream" else fragment_tests.PUZZLE
    elif kind == "tianji":
        module, fixture, text = divination_tests, "divination_env", divination_tests.SUCCESS
        field, source = divination_tests.FIELD, "concubine_tianji"
    elif kind == "reacquire":
        module, fixture, text = reacquire_tests, "reacquire_env", reacquire_tests.SECT_SUCCESS
        field, source = reacquire_tests.FIELD, reacquire_tests.SOURCE
    else:
        module, fixture = voyage_tests, "voyage_env"
        field, nested, source = voyage_tests.FIELD, True, voyage_tests.SOURCE
        text = voyage_tests.STARTED if kind == "voyage" else voyage_tests.RETURNED
    env = request.getfixturevalue(fixture)
    if kind == "gift":
        gift_tests.prepare_gift(env, monkeypatch)
    if kind in {"dream", "puzzle", "voyage", "voyage_return"}:
        module.prepare(env, kind)
    key = "concubine_voyage_msg_id" if kind in {"voyage_status", "voyage", "voyage_return"} else f"concubine_{kind}_msg_id"

    def record():
        value = env.identity[field]
        return value[kind] if nested else value

    async def launch():
        if kind == "gift":
            return await gift_tests.send_gift(env)
        with state_module.use_identity(module.ID):
            return await getattr(concubine, f"_send_{kind}_command")(env.clock[0])

    def receipt():
        item = record()
        with state_module.use_identity(module.ID):
            runtime._finalize_game_command_sent(
                item["command"], msg_id=item["msg_id"], sent_at=item["sent_at"],
                send_started_at=item["dispatch_at"], send_as_id=module.ID,
                game_group_id=module.CHAT, topic_id=0, track=True, max_retry=0,
                append_sent_log=False, send_intent={"op_id": item["op_id"], "source_module": source},
            )

    async def reply():
        item = record()
        return await app._handle_routed_reply_event(
            SimpleNamespace(id=item["msg_id"] + 1, chat_id=module.CHAT, sender_id=module.BOT,
                            server_event_at=env.clock[0] + 1),
            text, env.clock[0] + 2,
            SimpleNamespace(id=item["msg_id"], chat_id=module.CHAT, sender_id=module.ID, raw_text=item["command"]),
            {"send_as_id": module.ID, "account_id": module.ACCOUNT, "chat_id": module.CHAT,
             "family": runtime.resolve_reply_family(item["command"]), "root_msg_id": item["msg_id"],
             "reply_to_msg_id": item["msg_id"], "sender_id": module.BOT},
        )

    return SimpleNamespace(env=env, kind=kind, key=key, module=module, record=record,
                           launch=launch, receipt=receipt, reply=reply)


@pytest.mark.parametrize("flow", KINDS, indirect=True)
@pytest.mark.parametrize("anchor", [0, False, 999999])
def test_old_native_completion_preserves_replacement_phase(flow, anchor):
    assert asyncio.run(flow.launch())
    flow.receipt()
    root = flow.record()["msg_id"]
    flow.env.identity.update({flow.key: anchor, "next_concubine_time": flow.module.NOW + 9999,
                              "concubine_last_snapshot_at": flow.module.NOW + 1})
    before_sends = flow.env.send.await_count
    assert asyncio.run(flow.reply())
    assert flow.record()["status"] == "complete"
    assert flow.env.identity["concubine_phase"] == flow.kind + "_pending"
    assert type(flow.env.identity[flow.key]) is type(anchor) and flow.env.identity[flow.key] == anchor
    assert flow.env.identity["next_concubine_time"] == flow.module.NOW + 9999
    assert (flow.module.CHAT, root) not in flow.env.identity["pending_tasks"]
    assert flow.env.send.await_count == before_sends
    before = copy.deepcopy(flow.env.identity)
    assert not asyncio.run(flow.reply())
    assert flow.env.identity == before


@pytest.mark.parametrize("flow", KINDS, indirect=True)
def test_exact_anchor_can_close_without_replacing_new_snapshot_or_timer(flow):
    assert asyncio.run(flow.launch())
    flow.receipt()
    flow.env.identity.update(next_concubine_time=flow.module.NOW + 9999,
                             concubine_last_snapshot_at=flow.module.NOW + 1)
    before_sends = flow.env.send.await_count
    assert asyncio.run(flow.reply())
    assert flow.record()["status"] == "complete"
    assert flow.env.identity["concubine_phase"] == "idle"
    assert flow.env.identity[flow.key] == 0
    assert flow.env.identity["next_concubine_time"] == flow.module.NOW + 9999
    assert flow.env.identity["concubine_last_snapshot_at"] == flow.module.NOW + 1
    assert flow.env.send.await_count == before_sends


@pytest.mark.parametrize("flow", ["status", "greet", "dream", "voyage", "tianji", "reacquire"], indirect=True)
def test_unsent_dispatch_cannot_clear_a_replacement_phase(flow, monkeypatch):
    block = {"status": "none"}
    monkeypatch.setattr(concubine, "classify_game_send_block", lambda *_args: dict(block))

    async def sent(_command, **kwargs):
        flow.env.identity["next_concubine_time"] = flow.module.NOW + 9999
        assert not kwargs["operation_check"]()
        block.update(status="unsent", code="pre_send_guard", at=flow.module.NOW)
        return None

    flow.env.send.side_effect = sent
    assert not asyncio.run(flow.launch())
    assert flow.record()["status"] == "unsent"
    assert flow.env.identity["concubine_phase"] == flow.kind + "_pending"
    assert flow.env.identity["next_concubine_time"] == flow.module.NOW + 9999


@pytest.mark.parametrize("flow", [kind for kind in KINDS if kind != "reacquire"], indirect=True)
@pytest.mark.parametrize("invalid", [False, True])
def test_queued_peer_action_rechecks_reacquisition_owner(flow, invalid, monkeypatch):
    block = {"status": "none"}
    monkeypatch.setattr(concubine, "classify_game_send_block", lambda *_args: dict(block))

    async def sent(_command, **kwargs):
        assert kwargs["operation_check"]()
        value = {"invalid": True} if invalid else {
            "op_id": "a" * 32, "kind": "reacquire", "identity_id": flow.module.ID,
            "account_id": flow.module.ACCOUNT, "chat_id": flow.module.CHAT,
            "command": concubine.CMD_CONCUBINE_SECT_MARRY, "started_at": flow.env.clock[0],
            "status": "unknown", "msg_id": 0, "plan_key": "b" * 64,
            "partner_key": concubine.reacquire_actions._partner_key(),
            "snapshot_at": flow.env.identity["concubine_last_snapshot_at"],
            "attempts": 0, "redirects": 0, "ack": {},
        }
        flow.env.identity[reacquire_tests.FIELD] = value
        if not invalid:
            assert concubine.reacquire_actions.record() == value
        assert not kwargs["operation_check"]()
        block.update(status="unsent", code="pre_send_guard", at=flow.env.clock[0])
        return None

    flow.env.send.side_effect = sent
    assert not asyncio.run(flow.launch())
    assert flow.record()["status"] == "unsent"
    assert flow.env.identity["concubine_phase"] == "idle"


@pytest.mark.parametrize("flow", READS, indirect=True)
def test_read_expiry_cannot_clear_unanchored_replacement_phase(flow):
    assert asyncio.run(flow.launch())
    flow.receipt()
    flow.env.identity.update({flow.key: 0, "next_concubine_time": flow.module.NOW + 9999})
    with state_module.use_identity(flow.module.ID):
        if flow.kind == "gift_bag":
            asyncio.run(concubine.affinity_actions.recover(flow.module.NOW + 1000))
        else:
            asyncio.run(concubine._recover_status_query(flow.module.NOW + 1000))
    assert flow.record()["status"] == "expired"
    assert flow.env.identity["concubine_phase"] == flow.kind + "_pending"
    assert flow.env.identity["next_concubine_time"] == flow.module.NOW + 9999


@pytest.mark.parametrize("flow", ["gift", "greet", "dream", "puzzle", "voyage", "voyage_return"], indirect=True)
@pytest.mark.parametrize("invalid", [False, True])
def test_queued_peer_mutation_rechecks_divination_owner(flow, invalid, monkeypatch):
    block = {"status": "none"}
    monkeypatch.setattr(concubine, "classify_game_send_block", lambda *_args: dict(block))

    async def sent(_command, **kwargs):
        if invalid:
            value = {"invalid": True}
        else:
            value = {
                "op_id": "d" * 32, "kind": "tianji", "identity_id": flow.module.ID,
                "account_id": flow.module.ACCOUNT, "chat_id": flow.module.CHAT,
                "command": concubine.CMD_CONCUBINE_TIANJI, "started_at": flow.env.clock[0],
                "status": "unknown", "msg_id": 0, "plan_key": "e" * 64,
                "partner": flow.env.identity["concubine_name"],
                "snapshot_at": flow.env.identity["concubine_last_snapshot_at"],
                "affinity": flow.env.identity["concubine_affinity"], "effect": concubine.divination_actions._effect(),
            }
        flow.env.identity["concubine_tianji_action"] = value
        if not invalid:
            assert concubine.divination_actions.record() == value
        assert not kwargs["operation_check"]()
        block.update(status="unsent", code="pre_send_guard", at=flow.env.clock[0])
        return None

    flow.env.send.side_effect = sent
    assert not asyncio.run(flow.launch())
    assert flow.record()["status"] == "unsent"


@pytest.mark.parametrize("flow", ["gift_bag", "gift", "greet", "dream", "puzzle", "voyage", "voyage_return", "tianji", "reacquire"], indirect=True)
@pytest.mark.parametrize("invalid", [False, True])
def test_external_observation_cancels_queued_mutation_without_stranding_unsent_phase(flow, invalid, monkeypatch):
    block = {"status": "none"}
    monkeypatch.setattr(concubine, "classify_game_send_block", lambda *_args: dict(block))
    state_module.update_send_as_profile(flow.module.ID, username="external_fixture")

    async def sent(_command, **kwargs):
        assert kwargs["operation_check"]()
        if invalid:
            flow.env.identity[external_tests.FIELD] = {"invalid": True}
        else:
            assert await concubine.handle_concubine_affinity_event(
                external_tests.MOON.replace("query_owner", "external_fixture"), flow.env.clock[0],
                SimpleNamespace(id=flow.module.ROOT - 1, chat_id=flow.module.CHAT, sender_id=flow.module.BOT,
                                server_event_at=flow.env.clock[0]))
        assert not kwargs["operation_check"]()
        block.update(status="unsent", code="pre_send_guard", at=flow.env.clock[0])
        return None

    flow.env.send.side_effect = sent
    assert not asyncio.run(flow.launch())
    assert flow.record()["status"] == "unsent"
    assert flow.env.identity["concubine_phase"] == "idle"
    assert flow.env.identity[flow.key] == 0
    assert flow.env.identity[external_tests.FIELD]


@pytest.mark.parametrize("flow", ["gift", "greet", "dream", "puzzle", "voyage", "voyage_return", "tianji", "reacquire"], indirect=True)
def test_owned_reply_finishes_but_cannot_clear_new_external_observation(flow):
    state_module.update_send_as_profile(flow.module.ID, username="external_fixture")
    assert asyncio.run(flow.launch())
    flow.receipt()
    with state_module.use_identity(flow.module.ID):
        assert asyncio.run(concubine.handle_concubine_affinity_event(
            external_tests.MOON.replace("query_owner", "external_fixture"), flow.env.clock[0] + 1,
            SimpleNamespace(id=flow.module.ROOT + 5, chat_id=flow.module.CHAT, sender_id=flow.module.BOT,
                            server_event_at=flow.env.clock[0] + 1)))
    before_sends = flow.env.send.await_count
    assert asyncio.run(flow.reply())
    assert flow.record()["status"] == "complete"
    assert flow.env.identity[external_tests.FIELD]["status"] == "pending"
    assert flow.env.send.await_count == before_sends
    with state_module.use_identity(flow.module.ID):
        assert not concubine._has_available_partner()
