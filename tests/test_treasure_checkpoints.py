from copy import deepcopy
from unittest.mock import Mock

import pytest

from model.webapp_core import MiniAppRequestAborted
from test_treasure_lifecycle import INIT, TOKEN, adapter, panel, run, scripted_transport


def test_each_mutation_has_an_acknowledged_intent_before_transport():
    calls, checkpoints = [], []
    base = scripted_transport(calls, limit=1)

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        if endpoint != "start":
            frame = checkpoints[-1]
            assert frame["phase"] == "intent"
            assert frame["pending"]["action"] == {
                "hunt": "enter", "hunt_reveal": "search", "hunt_settle": "settle",
            }[endpoint]
        return base(request)

    result = run(transport, checkpoint=lambda frame: checkpoints.append(deepcopy(frame)) or True)
    assert result["status"] == "daily_limit"
    assert result["action_dispatched"] is True
    assert result["checkpoint_sequence"] == len(checkpoints)
    assert checkpoints[-1]["phase"] == "settled"
    assert len(checkpoints[-1]["receipts"]) == 1
    assert TOKEN not in str(checkpoints) and INIT not in str(checkpoints)


@pytest.mark.parametrize("ack", [False, None, "true", 1])
def test_failed_checkpoint_acknowledgement_never_reaches_mutation(ack):
    transport = Mock(return_value=panel())
    result = run(transport, checkpoint=lambda _frame: ack)
    assert [call.args[0]["safe_summary"]["endpoint"] for call in transport.call_args_list] == ["start"]
    assert result["checkpoint_error"] == "treasure_checkpoint_failed"
    assert not result["action_dispatched"]


@pytest.mark.parametrize("ack", [True, False, None, 1])
def test_initial_daily_limit_requires_a_read_checkpoint(ack):
    checkpoints = []
    transport = Mock(return_value=(409, {
        "ok": False, "error": "hunt_daily_limit", "account": panel()["account"],
    }))

    def checkpoint(frame):
        checkpoints.append(deepcopy(frame))
        return ack

    result = run(transport, checkpoint=checkpoint)
    assert result["ok"] is (ack is True)
    assert result["status"] == ("daily_limit" if ack is True else "persistence_pending")
    assert transport.call_count == 1
    assert len(checkpoints) == 1 and checkpoints[0]["phase"] == "response"
    assert not checkpoints[0]["pending"] and not checkpoints[0]["action_dispatched"]
    assert checkpoints[0]["state"]["daily_limit_confirmed"]


def test_abort_thrown_inside_transport_is_not_proof_of_an_unsent_mutation():
    calls = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if endpoint == "hunt":
            raise MiniAppRequestAborted("fixture abort after dispatch boundary")
        return panel()

    result = run(transport)
    assert calls == ["start", "hunt"]
    assert result["outcome_unknown"] is True
    assert result["action_dispatched"] is True


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422])
def test_http_failure_without_a_business_rejection_does_not_clear_dispatch(status):
    calls = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        return (status, "fixture intermediary error") if endpoint == "hunt" else panel()

    result = run(transport)
    assert calls == ["start", "hunt"]
    assert result["outcome_unknown"] is True


def test_prior_settlement_is_checkpointed_before_a_later_round():
    calls, checkpoints = [], []
    base = scripted_transport(calls, limit=2)

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        if endpoint == "hunt" and calls.count("hunt_settle"):
            assert len(checkpoints[-1]["receipts"]) == 1
            assert any(frame["phase"] == "settled" for frame in checkpoints)
            raise OSError("fixture later request lost")
        return base(request)

    result = run(transport, checkpoint=lambda frame: checkpoints.append(deepcopy(frame)) or True)
    assert result["outcome_unknown"]
    assert result["data"]["settled_count"] == 1
    assert len(result["operation_evidence"]["receipts"]) == 1


@pytest.mark.parametrize("endpoint", ["hunt", "hunt_reveal", "hunt_settle"])
def test_explicit_business_refusal_resolves_only_its_request(endpoint):
    calls, frames = [], []
    base = scripted_transport(calls)

    def transport(request):
        if request["safe_summary"]["endpoint"] == endpoint:
            calls.append(endpoint)
            return 409, {"ok": False, "error": "fixture_business_refusal"}
        return base(request)

    result = run(transport, checkpoint=lambda frame: frames.append(deepcopy(frame)) or True)
    assert result["action_dispatched"] and not result["outcome_unknown"]
    assert calls[-1] == endpoint and calls.count(endpoint) == 1
    assert result["operation_evidence"]["resolution"]["kind"] == "rejected"
    assert result["operation_evidence"]["pending"] == {}
    assert not result["checkpoint_error"]


@pytest.mark.parametrize("status", [408, 425, 429, 500, 503])
def test_overloaded_or_timed_out_business_error_retains_unknown(status):
    calls = []
    base = scripted_transport(calls)

    def transport(request):
        if request["safe_summary"]["endpoint"] == "hunt":
            calls.append("hunt")
            return status, {"ok": False, "error": "fixture_business_refusal"}
        return base(request)

    result = run(transport)
    assert result["outcome_unknown"]
    assert calls == ["start", "hunt"]


@pytest.mark.parametrize("container", ["huntRun", "huntResult"])
def test_foreign_session_in_business_refusal_does_not_clear_original_request(container):
    calls = []
    base = scripted_transport(calls)

    def transport(request):
        if request["safe_summary"]["endpoint"] == "hunt_reveal":
            return 409, {"ok": False, "error": "fixture_refusal", container: {"sessionId": "foreign-session"}}
        return base(request)

    result = run(transport)
    assert result["outcome_unknown"]
    assert result["operation_evidence"]["pending"]["action"] == "search"


def test_admission_change_after_intent_is_proven_unsent_without_double_checkpoint():
    calls, frames, allowed = [], [], True

    def checkpoint(frame):
        nonlocal allowed
        frames.append(deepcopy(frame))
        if frame["phase"] == "intent":
            allowed = False
        return True

    result = run(scripted_transport(calls), checkpoint=checkpoint, operation_check=lambda: allowed)
    assert calls == ["start"]
    assert not result["outcome_unknown"] and not result["action_dispatched"]
    assert [frame["phase"] for frame in frames] == ["response", "intent", "response"]
    assert frames[-1]["resolution"]["kind"] == "not_sent"
    assert not result["checkpoint_error"]


def test_request_budget_rejection_has_no_saved_mutation_intent():
    calls, frames = [], []
    result = run(scripted_transport(calls), adapter=adapter(1),
                 checkpoint=lambda frame: frames.append(deepcopy(frame)) or True)
    assert result["error"] == "request_budget_exhausted"
    assert calls == ["start"] and len(frames) == 1
    assert frames[0]["phase"] == "response" and frames[0]["pending"] == {}


def test_async_checkpoint_is_rejected_and_closed_before_mutation():
    async def checkpoint(_frame):
        return True

    calls = []
    result = run(scripted_transport(calls), checkpoint=checkpoint)
    assert calls == ["start"]
    assert result["checkpoint_error"]


def test_checkpoint_consumer_cannot_mutate_worker_fact_buffers():
    def checkpoint(frame):
        frame["receipts"].clear()
        frame["state"].clear()
        return True

    result = run(scripted_transport([], limit=1), checkpoint=checkpoint)
    assert result["status"] == "daily_limit"
    assert len(result["operation_evidence"]["receipts"]) == 1
    assert result["operation_evidence"]["state"]["games_used"] == 1


def test_transport_error_text_and_receipt_session_are_not_persisted_verbatim():
    frames = []

    def transport(request):
        if request["safe_summary"]["endpoint"] == "start":
            return panel()
        raise OSError(f"fixture failure {TOKEN} {INIT}")

    result = run(transport, checkpoint=lambda frame: frames.append(deepcopy(frame)) or True)
    assert frames and result["outcome_unknown"]
    assert TOKEN not in str(frames) and INIT not in str(frames)
    assert TOKEN not in str(result["operation_evidence"])
