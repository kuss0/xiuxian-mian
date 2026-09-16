from dataclasses import replace
from unittest.mock import Mock

import pytest

from model.features import trial_miniapp as trial
from model.webapp_core import MiniAppRequestPolicy


TOKEN = "trial_FIXTURE124_SECRET"
INIT = "query_id=fixture&hash=FIXTURE124_HASH"
PLAYER = 991240001


def challenge(index=1):
    return {"challengeId": f"fixture124-{index}", "mode": "tianjiMeridianV1",
            "sequence": ["p"], "points": [{"id": "p", "x": 10, "y": 20}],
            "minDurationMs": 20, "maxDurationMs": 1000}


def run(*, transport=None, budget=32, checkpoint=None, rounds=1, operation_check=None):
    def default_transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        if endpoint == "finish":
            return {"ok": True, "result": {"traceGain": 3}, "nextChallenge": challenge(2)}
        return {"ok": True, "challenge": challenge()}

    kwargs = {} if checkpoint is None else {"checkpoint": checkpoint}
    return trial.run_trial_miniapp_loop_lab_flow(
        token=TOKEN, init_data=INIT, player_id=PLAYER, transport=transport or default_transport,
        max_rounds=rounds, sleeper=lambda _delay: None,
        operation_check=operation_check,
        adapter=replace(trial.build_trial_miniapp_adapter(), request_policy=MiniAppRequestPolicy(
            min_interval_sec=0, max_requests_per_run=budget,
        )), **kwargs,
    )


def test_unknown_finish_retains_explicit_dispatch_and_round_evidence():
    calls = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if endpoint == "finish":
            raise OSError("fixture connection reset")
        return {"ok": True, "challenge": challenge()}

    result = run(transport=transport)
    assert result.get("action_dispatched") is True
    assert result.get("outcome_unknown") is True
    assert result.get("pending", {}).get("action") == "finish"
    assert calls == ["start", "finish"]


def test_budget_rejection_does_not_fabricate_a_dispatched_finish():
    transport = Mock(return_value={"ok": True, "challenge": challenge()})
    result = run(transport=transport, budget=1)
    assert result.get("outcome_unknown") is False
    assert result.get("pending", {}).get("action") == "challenge"
    assert result["events"][-2].get("dispatched") is False
    transport.assert_called_once()


def test_returned_settlement_has_a_stable_sanitized_round_fact():
    result = run()
    receipts = result.get("round_receipts") or []
    assert len(receipts) == 1
    assert receipts[0]["data"]["traceGain"] == 3
    assert len(receipts[0]["round_key"]) == 64
    assert result.get("pending") == {}
    assert result.get("outcome_unknown") is False


@pytest.mark.parametrize("ack", [False, None, "true"])
def test_missing_checkpoint_acknowledgement_prevents_transport(ack):
    transport = Mock()
    result = run(transport=transport, checkpoint=lambda _record: ack)
    transport.assert_not_called()
    assert result["checkpoint_error"] == "trial_checkpoint_failed"


@pytest.mark.parametrize("metadata", [
    {"token": False}, {"token": 1}, {"token": ""}, {"token": "   "},
    {"token": ["fixture"]}, {"token": TOKEN, "nextToken": TOKEN + "_different"},
    {"nextToken": TOKEN, "trialToken": None},
    {"data": {"token": False}},
    {"result": {"trial": {"nextToken": False}}},
    {"token": TOKEN, "data": {"trialToken": TOKEN + "_different"}},
])
def test_invalid_next_token_cannot_fall_back_to_old_entry(metadata):
    calls = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if endpoint == "finish":
            return {"ok": True, "result": {"traceGain": 3}}
        if endpoint == "next":
            return {"ok": True, "challenge": challenge(2), **metadata}
        return {"ok": True, "challenge": challenge()}

    result = run(transport=transport, rounds=2)
    assert calls == ["start", "finish", "next"]
    assert result["data"]["settled_count"] == 1
    assert result["pending"]["action"] == "next" and result["outcome_unknown"]
    assert result["error"] == "trial_next_token_invalid"


def test_async_checkpoint_is_not_an_acknowledgement():
    async def checkpoint(_frame):
        return True

    transport = Mock()
    result = run(transport=transport, checkpoint=checkpoint)
    transport.assert_not_called()
    assert result["checkpoint_error"]


def test_checkpoint_consumer_cannot_mutate_worker_evidence():
    def checkpoint(frame):
        frame["pending"] = {"untrusted": True}
        frame["round_receipts"].clear()
        return True

    result = run(checkpoint=checkpoint)
    assert result["data"]["settled_count"] == len(result["round_receipts"]) == 1
    assert result["pending"] == {} and result["status"] == "settled"


def test_transport_error_secrets_do_not_enter_checkpoints():
    frames = []

    def transport(_request):
        raise OSError(f"fixture opaque failure {TOKEN} {INIT}")

    run(transport=transport, checkpoint=lambda frame: frames.append(frame) or True)
    assert frames
    assert all(TOKEN not in str(frame) and INIT not in str(frame) for frame in frames)
