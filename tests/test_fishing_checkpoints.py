import asyncio
import copy
import hashlib
import json

import pytest

from model.features import fishing_miniapp as fishing
from test_fishing_worker_lifecycle import INIT, TOKEN, URL, adapter_with_limit, response


def run(checkpoint, *, transport=None, rounds=2, **kwargs):
    next_index = 0

    def default_transport(request):
        nonlocal next_index
        endpoint = request["safe_summary"]["endpoint"]
        if endpoint == "next":
            next_index += 1
            return {"ok": True, "token": f"fish_CHECKPOINT112_{next_index}"}
        return response(endpoint)

    return fishing.run_fishing_miniapp_loop_lab_flow(
        token=TOKEN, init_data=INIT, checkpoint=checkpoint, transport=transport or default_transport,
        adapter=adapter_with_limit(), max_rounds=rounds, sleeper=lambda _delay: None, **kwargs,
    )


def test_intent_and_confirmed_prefix_are_checkpointed_before_next_mutation():
    records, calls = [], []

    def checkpoint(record):
        records.append(copy.deepcopy(record))
        return True

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if endpoint in {"start", "finish", "next"}:
            latest = records[-1]
            assert latest["phase"] == "intent"
            assert latest["unresolved_action"] == endpoint
            assert latest["unresolved_round_key"] == hashlib.sha256(request["payload"]["token"].encode()).hexdigest()
            if endpoint == "next":
                assert len(latest["round_receipts"]) == 1
                assert latest["unresolved_round_known"] is False
                return {"ok": True, "token": "fish_CHECKPOINT112"}
        return response(endpoint)

    result = run(checkpoint, transport=transport)
    assert result["data"]["settled_count"] == 2
    assert [record["sequence"] for record in records] == list(range(1, len(records) + 1))
    assert [record["phase"] for record in records] == [
        "intent", "response", "intent", "response", "settled", "intent", "response",
        "intent", "response", "intent", "response", "settled",
    ]
    assert records[-1]["outcome_unknown"] is False
    assert len(records[-1]["round_receipts"]) == 2
    assert all(receipt["data"]["settled_count"] == 1 for receipt in records[-1]["round_receipts"])
    serialized = json.dumps(records)
    assert TOKEN not in serialized
    assert "fish_CHECKPOINT112" not in serialized
    assert "FIXTURE105_SECRET" not in serialized
    assert "fishingProof" not in serialized
    assert len(calls) == 7


@pytest.mark.parametrize("ack", [False, None, 1, {}, "true"])
def test_non_true_intent_ack_never_authorizes_http(ack):
    calls = []
    result = run(lambda _record: ack, transport=lambda request: calls.append(request))
    assert calls == []
    assert result["action_dispatched"] is False
    assert result["outcome_unknown"] is False
    assert result["checkpoint_error"]
    assert result["data"]["settled_count"] == 0


def test_async_checkpoint_is_not_treated_as_a_successful_save():
    calls = []

    async def checkpoint(_record):
        return True

    result = run(checkpoint, transport=lambda request: calls.append(request))
    assert calls == []
    assert result["checkpoint_error"]


@pytest.mark.parametrize("phase,action,confirmed", [
    ("response", "start", 0), ("intent", "finish", 0), ("response", "finish", 0),
    ("settled", "", 1), ("intent", "next", 1), ("response", "next", 1),
])
@pytest.mark.parametrize("raises", [False, True])
def test_failed_checkpoint_stops_future_http_without_erasing_facts(phase, action, confirmed, raises):
    calls, stopped = [], []

    def checkpoint(record):
        if record["phase"] == phase and record["unresolved_action"] == action:
            stopped.append(list(calls))
            if raises:
                raise OSError("fixture save failed")
            return False
        return True

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        return response(endpoint)

    result = run(checkpoint, transport=transport)
    assert len(stopped) == 1
    assert calls == stopped[0]
    assert result["data"]["settled_count"] == confirmed
    assert len(result["round_receipts"]) == confirmed
    assert result["checkpoint_error"]
    assert result["outcome_unknown"] is (phase != "settled" and not (phase == "intent" and action == "next"))


def test_checkpoint_consumer_cannot_mutate_worker_evidence():
    def checkpoint(record):
        if record["round_receipts"]:
            record["round_receipts"][0]["data"]["catches"] = []
        record["unresolved_round_key"] = "corrupt"
        return True

    result = run(checkpoint)
    assert len(result["data"]["catches"]) == 2
    assert all(receipt["data"]["catches"] for receipt in result["round_receipts"])
    assert result["unresolved_round_key"] == ""


def test_operation_is_rechecked_after_intent_ack():
    allowed = True
    calls = []

    def checkpoint(_record):
        nonlocal allowed
        allowed = False
        return True

    result = run(checkpoint, transport=lambda request: calls.append(request), operation_check=lambda: allowed)
    assert calls == []
    assert result["action_dispatched"] is False
    assert result["outcome_unknown"] is False


def test_decoder_error_retains_a_settlement_marker_without_claiming_full_projection(monkeypatch):
    records, calls = [], []

    def checkpoint(record):
        records.append(record)
        return True

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        return response(endpoint)

    def broken_decoder(_data):
        raise ValueError("fixture parser failed")

    monkeypatch.setattr(fishing, "extract_fishing_miniapp_catches", broken_decoder)
    result = run(checkpoint, transport=transport)
    assert calls == ["start", "finish", "result"]
    assert result["data"]["settled_count"] == 1
    assert result["data"]["expGain"] == 4
    assert records[-1]["phase"] == "settled"
    receipt = records[-1]["round_receipts"][0]
    assert receipt["projection_error"] is True
    assert receipt["data"] == {"settled_count": 1}
    assert result["checkpoint_error"] == "fishing_receipt_projection_failed"


def test_ambiguous_catches_are_not_checkpointed_as_a_complete_empty_rod():
    records, calls = [], []

    def checkpoint(record):
        records.append(record)
        return True

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        result = response(endpoint)
        if endpoint == "result":
            result["result"]["catches"] = [{"fish": "first"}, {"fish": "second"}]
        return result

    result = run(checkpoint, transport=transport)
    assert calls == ["start", "finish", "result"]
    assert result["data"]["settled_count"] == 1
    assert records[-1]["round_receipts"][0].get("projection_error") is True


def test_production_worker_passes_the_checkpoint_callback():
    records = []

    def checkpoint(record):
        records.append(record)
        return True

    result = asyncio.run(fishing.run_fishing_miniapp_production_flow(
        991120002, token=TOKEN, webview_url=URL, init_data=INIT, checkpoint=checkpoint,
        transport=lambda request: response(request["safe_summary"]["endpoint"]),
        adapter=adapter_with_limit(), sleeper=lambda _delay: None,
    ))
    assert result["ok"]
    assert records[-1]["phase"] == "settled"
    assert len(records[-1]["round_receipts"]) == 1


def test_recovery_worker_checkpoints_the_bound_settlement_without_mutation_intent():
    records = []

    def checkpoint(record):
        records.append(record)
        return True

    result = fishing.run_fishing_miniapp_recovery_lab_flow(
        token=TOKEN, init_data=INIT, expected_round_key=hashlib.sha256(TOKEN.encode()).hexdigest(),
        checkpoint=checkpoint, transport=lambda _request: response("result"),
        adapter=adapter_with_limit(), sleeper=lambda _delay: None,
    )
    assert result["ok"]
    assert [record["phase"] for record in records] == ["settled"]
    assert records[0]["action_dispatched"] is False
    assert records[0]["round_receipts"][0]["round_key"] == hashlib.sha256(TOKEN.encode()).hexdigest()
