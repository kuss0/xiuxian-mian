import asyncio
import json
import random
from types import SimpleNamespace

import pytest

from model.features import miniapp_common, world_boss_miniapp


@pytest.mark.parametrize("failure", ["detail", "timestamp", "redaction", "sink"])
def test_common_capture_failure_preserves_business_outcome(monkeypatch, caplog, failure):
    records = []
    kwargs = {"adapter_key": "trial", "detail": {"settled_count": 1}, "created_at": 123.0}

    def broken(_value):
        raise ValueError("token=trial_PRIVATE hash=PRIVATE_HASH")

    sink = records
    if failure == "detail":
        kwargs["detail"] = object()
    elif failure == "timestamp":
        kwargs["created_at"] = "trial_PRIVATE"
    elif failure == "redaction":
        monkeypatch.setattr(miniapp_common, "safe_miniapp_event_detail", broken)
    else:
        sink = broken

    assert miniapp_common.append_business_capture(sink, **kwargs) == {}
    assert records == []
    assert "business capture failed" in caplog.text
    assert "trial_PRIVATE" not in caplog.text
    assert "PRIVATE_HASH" not in caplog.text


@pytest.mark.parametrize("adapter", sorted(miniapp_common.MINIAPP_BUSINESS_CAPTURE_ADAPTERS))
def test_common_capture_preserves_whitelist_and_redaction(adapter):
    records = []
    record = miniapp_common.append_business_capture(
        records, adapter_key=adapter, created_at=123.0,
        source="test token=trial_PRIVATE",
        detail={
            "settled_count": 1, "items": {"gem": 2},
            "token": "trial_PRIVATE", "response": {"raw": "private"},
        },
    )

    assert records == [record]
    assert record["business"] == {"settled_count": 1, "items": {"gem": 2}}
    assert record["created_at"] == 123.0
    assert "trial_PRIVATE" not in json.dumps(record)


@pytest.mark.parametrize("helper", ["common", "boss"])
def test_absent_capture_sink_does_not_construct_diagnostics(monkeypatch, helper):
    def broken(*_args, **_kwargs):
        raise AssertionError("capture construction should not run")

    if helper == "common":
        monkeypatch.setattr(miniapp_common, "safe_miniapp_event_detail", broken)
        assert miniapp_common.append_business_capture(
            None, adapter_key="trial", detail=object(), created_at="invalid",
        ) == {}
    else:
        monkeypatch.setattr(world_boss_miniapp, "sanitize_webapp_secret_text", broken)
        world_boss_miniapp._append_business_capture(
            None, source="test", step="finish", summary=object(),
        )


@pytest.mark.parametrize("sink_kind", ["append", "callable"])
def test_boss_capture_sink_error_is_diagnostic_only(caplog, sink_kind):
    def broken(_record):
        raise OSError("token=qyz_PRIVATE hash=PRIVATE_HASH")

    sink = SimpleNamespace(append=broken) if sink_kind == "append" else broken
    world_boss_miniapp._append_business_capture(
        sink, source="test", step="finish", summary={"score": 900},
    )
    assert "business capture failed" in caplog.text
    assert "OSError" in caplog.text
    assert "qyz_PRIVATE" not in caplog.text
    assert "PRIVATE_HASH" not in caplog.text


def test_boss_capture_construction_error_is_diagnostic_only(caplog):
    records = []
    world_boss_miniapp._append_business_capture(
        records, source="test", step="finish", summary=object(),
    )
    assert records == []
    assert "TypeError" in caplog.text


@pytest.mark.parametrize("helper", ["common", "boss"])
def test_capture_does_not_swallow_cancellation(helper):
    def cancelled(_record):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        if helper == "common":
            miniapp_common.append_business_capture(cancelled, adapter_key="trial", detail={})
        else:
            world_boss_miniapp._append_business_capture(
                cancelled, source="test", step="finish", summary={},
            )


@pytest.mark.parametrize("failed_step", ["hit_business", "finish_business"])
@pytest.mark.parametrize("finish_ok", [True, False])
def test_boss_flow_preserves_accepted_hit_and_settlement_on_capture_failure(failed_step, finish_ok):
    def run(broken_capture):
        now = [100.0]
        calls = []

        def sleep(delay):
            now[0] += delay

        def transport(request):
            endpoint = request["safe_summary"]["endpoint"]
            calls.append(endpoint)
            if endpoint == "start":
                return 200, {
                    "ok": True,
                    "boss": {"roomStatus": "battle", "actionLimit": 1, "actionsRemaining": 1},
                    "challenge": {
                        "challengeId": "capture-test", "playerHp": 1000,
                        "windows": [{"id": "w1", "centerMs": 1500}],
                    },
                }
            if endpoint == "hit":
                return 200, {
                    "ok": True,
                    "hit": {"attemptConsumed": True, "perfect": True, "damageYi": 100},
                    "boss": {"actionLimit": 1, "actionsUsed": 1, "actionsRemaining": 0},
                }
            if endpoint == "finish":
                if not finish_ok:
                    return 503, {"ok": False, "error": "temporary failure"}
                return 200, {"ok": True, "result": {"score": 900, "hits": 1, "perfects": 1}}
            pytest.fail(f"unexpected endpoint: {endpoint}")

        def capture(record):
            if broken_capture and record["step_key"] == failed_step:
                raise OSError("capture disk full")

        receipt = world_boss_miniapp.WorldBossJoinReceipt(True, "joined", player_id="77")
        result = world_boss_miniapp.run_world_boss_joined_battle_lab_flow(
            receipt, token="qyz_TEST", init_data="query_id=x&hash=y", transport=transport,
            rng=random.Random(9), sleeper=sleep, clock=lambda: now[0], capture_sink=capture,
        )
        return result, calls

    expected, expected_calls = run(False)
    actual, actual_calls = run(True)
    assert actual == expected
    assert actual_calls == expected_calls == ["start", "hit", "finish"]
    assert actual["ok"] is finish_ok
    if finish_ok:
        assert actual["status"] == "settled"
        assert actual["data"]["result"]["accepted_hit_count"] == 1
        assert actual["data"]["result"]["accepted_damage_yi"] == 100
