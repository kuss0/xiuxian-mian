import asyncio
import copy

import pytest

from model import persistence, state as state_module
from model.features import concubine, concubine_fragment_actions as actions
from tests import test_concubine_fragment_actions as base


env = base.env


@pytest.mark.parametrize("kind", base.KINDS)
@pytest.mark.parametrize("early_reply", [False, True])
def test_affinity_update_during_transport_does_not_strand_owned_phase(env, kind, early_reply):
    base.prepare(env, kind)

    async def sent(*_args, **_kwargs):
        env.identity["concubine_affinity"] += 6
        base.receipt(env, kind)
        if early_reply:
            assert await base.reply(env, kind)
        return env.send.return_value

    env.send.side_effect = sent
    assert asyncio.run(base.start(env, kind))
    if not early_reply:
        assert env.identity[f"concubine_{kind}_msg_id"] == base.ROOT
        assert asyncio.run(base.reply(env, kind))
    assert env.identity[base.FIELD][kind]["status"] == "complete"
    assert env.identity["concubine_phase"] == "idle"
    assert env.identity["concubine_affinity"] == 1006
    with state_module.use_identity(base.ID):
        assert not actions.block_reason()
        assert not asyncio.run(actions.recover(base.NOW + 1000))
    env.send.assert_awaited_once()


@pytest.mark.parametrize("kind", base.KINDS)
@pytest.mark.parametrize("change", ["timer", "phase", "anchor", "bool_anchor"])
@pytest.mark.parametrize("early_reply", [False, True])
def test_transport_completion_preserves_replacement_phase(env, kind, change, early_reply):
    base.prepare(env, kind)
    replacement = {}

    async def sent(*_args, **_kwargs):
        env.identity["concubine_affinity"] += 6
        key = f"concubine_{kind}_msg_id"
        field, value = {
            "timer": ("next_concubine_time", base.NOW + 9999),
            "phase": ("concubine_phase", "status_pending"),
            "anchor": (key, base.ROOT + 99),
            "bool_anchor": (key, False),
        }[change]
        env.identity[field] = value
        replacement.update({name: env.identity[name] for name in ("concubine_phase", key, "next_concubine_time")})
        base.receipt(env, kind)
        if early_reply:
            assert await base.reply(env, kind)
        return env.send.return_value

    env.send.side_effect = sent
    assert asyncio.run(base.start(env, kind))
    if not early_reply:
        assert asyncio.run(base.reply(env, kind))
    for key, value in replacement.items():
        assert type(env.identity[key]) is type(value) and env.identity[key] == value
    assert env.identity[base.FIELD][kind]["status"] == "complete"
    env.send.assert_awaited_once()


@pytest.mark.parametrize("kind", base.KINDS)
@pytest.mark.parametrize("invalid", ["missing", "extra", "phase", "anchor", "boolean", "time", "deadline", "null"])
def test_invalid_projection_is_not_used_to_release_or_resend(env, kind, invalid):
    base.prepare(env, kind)
    assert asyncio.run(base.start(env, kind))
    record = env.identity[base.FIELD][kind]
    projection = record["projection"]
    if invalid == "missing":
        projection.pop("next_concubine_time")
    elif invalid == "extra":
        projection["unexpected"] = 0
    elif invalid == "phase":
        projection["concubine_phase"] = "idle"
    elif invalid == "anchor":
        projection[f"concubine_{kind}_msg_id"] = base.ROOT + 1
    elif invalid == "boolean":
        projection[f"concubine_{kind}_msg_id"] = False
    elif invalid == "time":
        projection["next_concubine_time"] = "invalid"
    elif invalid == "deadline":
        projection["next_concubine_time"] += 1
    else:
        record["projection"] = None
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(base.ID):
        assert actions.records() is None
        assert actions.block_reason() == "invalid"
        assert asyncio.run(actions.recover(base.NOW + 86400))
    assert not asyncio.run(base.reply(env, kind))
    assert not asyncio.run(base.start(env, kind))
    assert env.identity == before
    env.send.assert_awaited_once()


@pytest.mark.parametrize("kind", base.KINDS)
@pytest.mark.parametrize("known", [False, True])
def test_affinity_race_survives_reload_and_only_owned_reply_releases(env, kind, known):
    base.prepare(env, kind)
    result = env.send.return_value if known else None

    async def sent(*_args, **_kwargs):
        env.identity["concubine_affinity"] += 6
        base.receipt(env, kind)
        return result

    env.send.side_effect = sent
    assert asyncio.run(base.start(env, kind)) is known
    assert persistence.save_state()
    assert persistence.load_state()
    env.identity = state_module.get_identity_state(base.ID)
    with state_module.use_identity(base.ID):
        concubine.restore_concubine_runtime(base.NOW + 60)
        assert actions.records() is not None
        assert actions.block_reason() == "pending"
    assert not asyncio.run(base.start(env, kind))
    assert asyncio.run(base.reply(env, kind))
    assert env.identity["concubine_phase"] == "idle"
    assert persistence.save_state()
    assert persistence.load_state()
    env.identity = state_module.get_identity_state(base.ID)
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(base.ID):
        assert not actions.block_reason()
        assert not asyncio.run(actions.recover(base.NOW + 1000))
    assert not asyncio.run(base.reply(env, kind))
    assert env.identity == before
    env.send.assert_awaited_once()


@pytest.mark.parametrize("kind", base.KINDS)
@pytest.mark.parametrize("anchor", [0, base.ROOT])
def test_old_record_without_projection_keeps_conservative_release(env, kind, anchor):
    base.prepare(env, kind)
    assert asyncio.run(base.start(env, kind))
    env.identity[base.FIELD][kind].pop("projection")
    env.identity["concubine_affinity"] += 6
    env.identity[f"concubine_{kind}_msg_id"] = anchor
    assert asyncio.run(base.reply(env, kind))
    assert env.identity["concubine_phase"] == ("idle" if anchor else kind + "_pending")
    assert env.identity[base.FIELD][kind]["status"] == "complete"
    env.send.assert_awaited_once()


@pytest.mark.parametrize("kind", base.KINDS)
def test_affinity_change_cancels_before_dispatch_and_releases_only_own_phase(env, kind, monkeypatch):
    base.prepare(env, kind)
    block = {"status": "none"}
    monkeypatch.setattr(concubine, "classify_game_send_block", lambda *_args: dict(block))

    async def unsent(_command, **kwargs):
        env.identity["concubine_affinity"] += 6
        assert not kwargs["operation_check"]()
        block.update(status="unsent", code="pre_send_guard", at=base.NOW)
        return None

    env.send.side_effect = unsent
    assert not asyncio.run(base.start(env, kind))
    assert env.identity[base.FIELD][kind]["status"] == "unsent"
    assert env.identity["concubine_phase"] == "idle"
    with state_module.use_identity(base.ID):
        assert actions.records()[kind]["retry_at"] >= base.NOW + 120


def test_real_duplicate_fragment_reply_closes_owned_wa_dream(env):
    text = (
        "\u3010\u5165\u68a6\u5bfb\u56fe\u3011\n"
        "\u672c\u6b21\u68a6\u5146\u9501\u5b9a\uff1a\u3010\u865a\u5929\u6b8b\u56fe\u3011 \u7ebf\u8def\u3002\n"
        f"\u4f60\u4e0e\u4f8d\u59be\u3010{base.NAME}\u3011\u5171\u68a6\u4e71\u661f\u6d77\uff0c\u83b7\u5f97 "
        "\u3010\u865a\u5929\u6b8b\u56fe\u3011 \u6b8b\u7eb9 \u5317\u9619\u6b8b\u7eb9\uff08\u91cd\u590d\u85cf\u672c\uff09\u3002\n"
        "\u5f53\u524d\u8fdb\u5ea6\uff1a3/4\u3002\n"
        "\u53ef\u7528 .\u6b8b\u56fe \u67e5\u770b\u8be6\u60c5\uff0c\u96c6\u9f50\u540e\u4f7f\u7528 .\u62fc\u56fe \u89e6\u53d1\u5bf9\u5e94\u526f\u672c\u673a\u7f18\u3002"
    )

    async def sent(*_args, **_kwargs):
        env.identity["concubine_affinity"] += 6
        base.receipt(env, "dream")
        assert await base.reply(env, "dream", text=text, route="native")
        return env.send.return_value

    env.send.side_effect = sent
    assert asyncio.run(base.start(env, "dream"))
    assert env.identity["concubine_phase"] == "idle"
    assert env.identity["concubine_fragment_xutian_count"] == 3
    assert env.identity[base.FIELD]["dream"]["result"]["applied"] is True
    assert env.identity["concubine_dream_due_at"] == base.NOW + 1 + concubine.CONCUBINE_DREAM_CD_SEC + concubine.CD_BUFFER_SEC
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(base.reply(env, "dream", text=text))
    assert env.identity == before
    env.send.assert_awaited_once()
