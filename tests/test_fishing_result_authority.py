import asyncio
import copy
from unittest.mock import Mock

import pytest

from model import state as state_module
from model.features import fishing_miniapp as adapter
from model.features import fishing_runtime as fishing
import test_fishing_caller_lifecycle as lifecycle
from test_fishing_worker_lifecycle import INIT, TOKEN, adapter_with_limit, response


fishing_env = lifecycle.fishing_env
REAL_HARVEST = fishing._send_fishing_miniapp_harvest_summary
MISSING = object()


@pytest.mark.parametrize("catches", [[], [{}], ["unrecognized"]])
def test_explicit_empty_current_catches_do_not_fall_back_to_history(catches):
    data = {"catches": catches, "last_result": {"details": {"fish": {"name": "old-fish"}}}}
    assert adapter.extract_fishing_miniapp_catches(data) == []


@pytest.mark.parametrize("status", ["failed", "settled", "finish_submitted", "not_ready", "daily_limit", "no_rod"])
def test_zero_confirmed_rounds_cannot_add_fish_or_rewards(fishing_env, status):
    h = fishing_env
    h.result.update(status=status)
    h.result["data"].update(settled_count=0)
    h.result["data"]["catches"][0]["rewards"] = [{"name": "\u6cd5\u5219\u788e\u7247", "qty": 1}]
    with state_module.use_identity(h.identity_id):
        fishing._apply_fishing_miniapp_result(h.result, h.now)
    h.items.assert_not_called()
    summary = fishing._normalize_fishing_daily_catch_summary(h.identity["fishing_daily_catch_summary_json"])
    assert not summary["fish"] and not summary["rewards"] and not summary["rods"]
    assert h.identity["fishing_valuable_drop_reminders"] == []


@pytest.mark.parametrize("count", [True, False, "1", 1.5, -1, None])
def test_invalid_confirmed_count_cannot_authorize_a_result(fishing_env, count):
    h = fishing_env
    h.result["data"]["settled_count"] = count
    assert not fishing.fishing_miniapp_has_confirmed_outcome(h.result)
    with state_module.use_identity(h.identity_id), pytest.raises(fishing.FishingMiniAppCommitError):
        fishing._apply_fishing_miniapp_result(h.result, h.now)
    h.items.assert_not_called()
    assert h.identity["fishing_daily_count"] == 0


@pytest.mark.parametrize("outer,inner", [(0, 1), (2, 1), (1, 2)])
def test_disagreeing_confirmed_counts_are_not_implicitly_preferred(fishing_env, outer, inner):
    h = fishing_env
    h.result["settled_count"] = outer
    h.result["data"]["settled_count"] = inner
    with state_module.use_identity(h.identity_id), pytest.raises(fishing.FishingMiniAppCommitError):
        fishing._apply_fishing_miniapp_result(h.result, h.now)
    h.items.assert_not_called()


@pytest.mark.parametrize("status", ["failed", "cancelled", "next_failed"])
def test_positive_confirmed_partial_result_keeps_its_fish(fishing_env, status):
    h = fishing_env
    h.result.update(ok=False, status=status)
    with state_module.use_identity(h.identity_id):
        fishing._apply_fishing_miniapp_result(h.result, h.now)
    assert h.identity["fishing_daily_count"] == 1
    h.items.assert_called_once_with(h.identity_id, {"fixture-fish": 1}, persist=False)


def test_two_identical_confirmed_catches_are_not_content_deduplicated(fishing_env):
    h = fishing_env
    h.result["data"].update(settled_count=2, catches=[h.result["data"]["catches"][0]] * 2)
    with state_module.use_identity(h.identity_id):
        fishing._apply_fishing_miniapp_result(h.result, h.now)
    assert h.identity["fishing_daily_count"] == 2
    h.items.assert_called_once_with(h.identity_id, {"fixture-fish": 2}, persist=False)


@pytest.mark.parametrize("count", [0, "1", True, -1])
def test_unconfirmed_harvest_is_not_announced_or_captured(fishing_env, monkeypatch, count):
    h = fishing_env
    h.result["data"]["settled_count"] = count
    capture = Mock(return_value={})
    monkeypatch.setattr(fishing, "append_business_capture", capture)
    with state_module.use_identity(h.identity_id):
        assert not asyncio.run(REAL_HARVEST(h.result))
        fishing._record_fishing_business_capture(Mock(), h.result, source="fixture", now=h.now)
    h.audit.assert_not_awaited()
    capture.assert_not_called()


def test_partial_confirmed_failure_is_retained_in_business_capture(fishing_env, monkeypatch):
    h = fishing_env
    h.result.update(ok=False, status="failed")
    capture = Mock(return_value={"saved": True})
    monkeypatch.setattr(fishing, "append_business_capture", capture)
    fishing._record_fishing_business_capture(Mock(), h.result, source="fixture", now=h.now)
    capture.assert_called_once()
    assert capture.call_args.kwargs["detail"]["settled_count"] == 1


def test_excess_catches_are_not_a_confirmed_outcome(fishing_env):
    h = fishing_env
    h.result["data"]["catches"] *= 2
    assert not fishing.fishing_miniapp_has_confirmed_outcome(h.result)
    with state_module.use_identity(h.identity_id), pytest.raises(fishing.FishingMiniAppCommitError):
        fishing._apply_fishing_miniapp_result(h.result, h.now)
    h.items.assert_not_called()
    assert h.identity["fishing_daily_count"] == 0


@pytest.mark.parametrize("consumer", ["summary", "notice", "capture"])
def test_excess_catches_cannot_be_announced_or_captured(fishing_env, monkeypatch, consumer):
    h = fishing_env
    h.result["data"]["catches"] *= 2
    capture = Mock(return_value={})
    monkeypatch.setattr(fishing, "append_business_capture", capture)
    with state_module.use_identity(h.identity_id):
        if consumer == "summary":
            summary = fishing._format_miniapp_result_summary(h.result)
            assert "fixture-fish" not in summary
            assert "\u672a\u786e\u8ba4\u7ed3\u7b97" in summary
        elif consumer == "notice":
            assert not asyncio.run(REAL_HARVEST(h.result))
        else:
            fishing._record_fishing_business_capture(Mock(), h.result, source="fixture", now=h.now)
    h.audit.assert_not_awaited()
    capture.assert_not_called()


@pytest.mark.parametrize("caller", ["public", "message"])
def test_callers_do_not_capture_rejected_catch_projection(fishing_env, monkeypatch, caller):
    h = fishing_env
    h.result["data"]["catches"] *= 2
    capture = Mock(return_value={})
    monkeypatch.setattr(fishing, "append_business_capture", capture)
    asyncio.run(lifecycle.run_caller(h, caller))
    capture.assert_not_called()
    h.items.assert_not_called()
    h.harvest.assert_not_awaited()
    h.daily.assert_not_awaited()
    assert h.identity["fishing_daily_count"] == 0
    assert h.identity["fishing_result_pending"] == {"invalid": True}


@pytest.mark.parametrize("caller", ["public", "message"])
def test_business_capture_sink_failure_preserves_confirmed_result(fishing_env, monkeypatch, caller):
    h = fishing_env
    capture = Mock()
    capture.append.side_effect = OSError("fixture capture failure")
    monkeypatch.setattr(fishing, "_fishing_miniapp_capture_store", Mock(return_value=capture))
    monkeypatch.setattr(lifecycle.cave, "_fishing_miniapp_capture_store", Mock(return_value=capture))
    asyncio.run(lifecycle.run_caller(h, caller))
    capture.append.assert_called_once()
    assert h.identity["fishing_daily_count"] == 1
    h.items.assert_called_once_with(h.identity_id, {"fixture-fish": 1}, persist=False)
    assert h.identity["fishing_result_pending"] == {}


@pytest.mark.parametrize("outer,inner", [
    (True, False), (False, True), (True, 0), (True, "false"), (1, True), (None, True), (True, None),
])
def test_conflicting_readiness_cannot_settle_or_start_next_round(outer, inner):
    calls = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        payload = response(endpoint)
        if endpoint == "result":
            payload["ready"] = outer
            payload["result"]["ready"] = inner
        return payload

    result = adapter.run_fishing_miniapp_loop_lab_flow(
        token=TOKEN, init_data=INIT, transport=transport, adapter=adapter_with_limit(),
        max_rounds=2, result_poll_limit=1, sleeper=lambda _delay: None,
    )
    assert result["data"]["settled_count"] == 0
    assert result["data"]["catches"] == []
    assert "next" not in calls


@pytest.mark.parametrize("outer,inner", [(True, True), (True, MISSING), (MISSING, True)])
def test_valid_single_readiness_source_still_settles(outer, inner):
    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        payload = response(endpoint)
        if endpoint == "result":
            payload.pop("ready", None)
            if outer is not MISSING:
                payload["ready"] = outer
            if inner is not MISSING:
                payload["result"]["ready"] = inner
        return payload

    result = adapter.run_fishing_miniapp_loop_lab_flow(
        token=TOKEN, init_data=INIT, transport=transport, adapter=adapter_with_limit(),
        max_rounds=1, result_poll_limit=1, sleeper=lambda _delay: None,
    )
    assert result["data"]["settled_count"] == 1
    assert result["data"]["catches"][0]["fish"] == "fixture-fish"


@pytest.mark.parametrize("nested", [[], "invalid", None, 1])
def test_invalid_ready_result_wrapper_is_not_a_settlement(nested):
    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        payload = response(endpoint)
        if endpoint == "result":
            payload["result"] = nested
        return payload

    result = adapter.run_fishing_miniapp_loop_lab_flow(
        token=TOKEN, init_data=INIT, transport=transport, adapter=adapter_with_limit(),
        max_rounds=1, result_poll_limit=1, sleeper=lambda _delay: None,
    )
    assert result["data"]["settled_count"] == 0


def test_later_ambiguous_result_keeps_only_earlier_confirmed_round():
    calls = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        payload = response(endpoint)
        if endpoint == "result" and calls.count("result") == 2:
            payload["result"]["ready"] = False
            payload["result"]["details"]["fish"]["name"] = "unconfirmed-fish"
        return copy.deepcopy(payload)

    result = adapter.run_fishing_miniapp_loop_lab_flow(
        token=TOKEN, init_data=INIT, transport=transport, adapter=adapter_with_limit(),
        max_rounds=3, result_poll_limit=1, sleeper=lambda _delay: None,
    )
    assert result["data"]["settled_count"] == 1
    assert [catch["fish"] for catch in result["data"]["catches"]] == ["fixture-fish"]
    assert result["data"]["expGain"] == 4
    assert calls.count("next") == 1
