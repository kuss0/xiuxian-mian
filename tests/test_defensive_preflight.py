import json

from tools import defensive_preflight as preflight
from unittest.mock import patch


def test_tianxing_outside_prepare_window_is_watch():
    now = 1_700_000_000.0
    item = preflight._tianxing_action_status(
        label="wa2000",
        username="WalterWA2000",
        action="野外历练",
        due_at=now + 1800,
        retry_at=0,
        obs={"tianji_value": 43},
        timeline={"phase": "blocked_replan"},
        config={"route_prepare_lead_sec": 300},
        now=now,
    )

    assert item["level"] == "watch"
    assert item["prepare_lead_sec"] == 600
    assert "尚未进入" in item["reason"]


def test_tianxing_inside_prepare_window_without_protection_is_at_risk():
    now = 1_700_000_000.0
    item = preflight._tianxing_action_status(
        label="wa2000",
        username="WalterWA2000",
        action="野外历练",
        due_at=now + 120,
        retry_at=0,
        obs={"tianji_value": 43},
        timeline={"phase": "blocked_replan"},
        config={"route_prepare_lead_sec": 300},
        now=now,
    )

    assert item["level"] == "at_risk"
    assert "未见有效推命/改命" in item["reason"]


def test_tianxing_valid_prediction_and_change_is_healthy():
    now = 1_700_000_000.0
    item = preflight._tianxing_action_status(
        label="wa2000",
        username="WalterWA2000",
        action="野外历练",
        due_at=now + 120,
        retry_at=0,
        obs={
            "current_prediction": "探索",
            "current_prediction_until": now + 3600,
            "current_prediction_set_at": now - 120,
            "current_change": "探索",
            "current_change_until": now + 3600,
            "current_change_set_at": now - 120,
            "tianji_value": 40,
        },
        timeline={"phase": "blocked_replan"},
        config={"route_prepare_lead_sec": 300},
        now=now,
    )

    assert item["level"] == "healthy"
    assert "均有效" in item["reason"]


def test_tianxing_wild_training_far_running_deep_retreat_does_not_block():
    now = 1_700_000_000.0
    item = preflight._tianxing_action_status(
        label="tutu",
        username="tutuerduoxiao",
        action="野外历练",
        due_at=now + 120,
        retry_at=0,
        obs={
            "current_prediction": "探索",
            "current_prediction_until": now + 3600,
            "current_prediction_set_at": now - 120,
            "current_change": "探索",
            "current_change_until": now + 3600,
            "current_change_set_at": now - 120,
            "tianji_value": 40,
        },
        timeline={"phase": "blocked_replan"},
        config={"route_prepare_lead_sec": 300},
        now=now,
        deep_retreat_phase="running",
        next_deep_retreat_time=now + 3600,
    )

    assert item["level"] == "healthy"
    assert "均有效" in item["reason"]


def test_tianxing_wild_training_near_running_deep_retreat_is_watch():
    now = 1_700_000_000.0
    item = preflight._tianxing_action_status(
        label="tutu",
        username="tutuerduoxiao",
        action="野外历练",
        due_at=now + 120,
        retry_at=0,
        obs={
            "current_prediction": "探索",
            "current_prediction_until": now + 3600,
            "current_prediction_set_at": now - 120,
            "current_change": "探索",
            "current_change_until": now + 3600,
            "current_change_set_at": now - 120,
            "tianji_value": 40,
        },
        timeline={"phase": "blocked_replan"},
        config={"route_prepare_lead_sec": 300},
        now=now,
        deep_retreat_phase="running",
        next_deep_retreat_time=now + 300,
    )

    assert item["level"] == "watch"
    assert "深度闭关 running" in item["reason"]
    assert "顺延" in item["reason"]


def test_tianxing_stale_change_counter_is_not_healthy_inside_prepare_window():
    now = 1_700_000_000.0
    item = preflight._tianxing_action_status(
        label="tutu",
        username="tutuerduoxiao",
        action="野外历练",
        due_at=now + 120,
        retry_at=0,
        obs={
            "current_prediction": "探索",
            "current_prediction_until": now + 3600,
            "current_prediction_set_at": now - 120,
            "current_change": "探索",
            "current_change_until": now + 3600,
            "tianji_value": 40,
        },
        timeline={"phase": "idle"},
        config={"route_prepare_lead_sec": 300},
        now=now,
    )

    assert item["level"] == "at_risk"
    assert "改命证据不新鲜" in item["reason"]


def test_tianxing_wild_training_tianji_short_with_prediction_is_at_risk():
    now = 1_700_000_000.0
    item = preflight._tianxing_action_status(
        label="万灵 1",
        username="xueuode5",
        action="野外历练",
        due_at=now + 120,
        retry_at=0,
        obs={
            "current_prediction": "探索",
            "current_prediction_until": now + 3600,
            "current_prediction_set_at": now - 120,
            "current_change": "",
            "current_change_until": 0,
            "tianji_value": 0,
        },
        timeline={"phase": "need_tianji_for_change"},
        config={"route_prepare_lead_sec": 300},
        now=now,
    )

    assert item["level"] == "at_risk"
    assert "转炼制攒点" in item["reason"]


def test_tianxing_rift_tianji_short_with_prediction_stays_at_risk():
    now = 1_700_000_000.0
    item = preflight._tianxing_action_status(
        label="万灵 1",
        username="xueuode5",
        action="探寻裂缝",
        due_at=now + 120,
        retry_at=0,
        obs={
            "current_prediction": "探索",
            "current_prediction_until": now + 3600,
            "current_prediction_set_at": now - 120,
            "current_change": "",
            "current_change_until": 0,
            "tianji_value": 0,
        },
        timeline={"phase": "need_tianji_for_change"},
        config={"route_prepare_lead_sec": 300},
        now=now,
    )

    assert item["level"] == "at_risk"
    assert "未见有效推命/改命" in item["reason"]


def test_tianxing_later_action_with_prior_consume_is_watch():
    now = 1_700_000_000.0
    item = preflight._tianxing_action_status(
        label="wa2000",
        username="WalterWA2000",
        action="探寻裂缝",
        due_at=now + 4 * 3600,
        retry_at=0,
        obs={
            "current_prediction": "探索",
            "current_prediction_until": now + 20 * 3600,
            "current_prediction_set_at": now - 120,
            "current_change": "探索",
            "current_change_until": now + 20 * 3600,
            "current_change_set_at": now - 120,
            "tianji_value": 40,
        },
        timeline={"phase": "downstream_released"},
        config={"route_prepare_lead_sec": 300},
        now=now,
        prior_consume_at=now + 300,
    )

    assert item["level"] == "watch"
    assert "先被" in item["reason"]
    assert "消费" in item["reason"]


def test_listener_inactive_without_heartbeat_is_watch(tmp_path):
    missing = tmp_path / "listener_heartbeat.json"

    with patch.object(preflight, "HEARTBEAT_PATH", missing), patch.object(preflight, "_listener_service_state", return_value="inactive"):
        item = preflight._listener_status(1_700_000_000.0)

    assert item["level"] == "watch"
    assert item["service_state"] == "inactive"
    assert "inactive" in item["reason"]


def test_listener_sidecar_without_independent_accounts_is_watch(tmp_path):
    heartbeat = tmp_path / "listener_heartbeat.json"
    heartbeat.write_text(
        json.dumps(
            {
                "status": "degraded_no_connected_accounts",
                "updated_at": 1_700_000_000.0,
                "registered_accounts": [],
                "failed_accounts": [{"account_id": 301299112, "error": "listener session 未独立授权"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with patch.object(preflight, "HEARTBEAT_PATH", heartbeat), patch.object(preflight, "_listener_service_state", return_value="active"):
        item = preflight._listener_status(1_700_000_010.0)

    assert item["level"] == "watch"
    assert item["service_state"] == "active"
    assert "independent accounts" in item["reason"]


def test_hehuan_cooldown_with_early_send_is_at_risk():
    now = 1_700_000_000.0
    with patch.object(
        preflight,
        "_recent_script_sends",
        return_value=[{"message_id": 9911, "ts": now - 30}],
    ):
        item = preflight._hehuan_status(
            label="Wise Mole",
            username="wisemole",
            send_as_id=8574677796,
            obs={
                "last_observed_at": now - 300,
                "last_warm_success_at": now - 300,
                "next_hehuan_time": now + 3300,
                "auto_next_time": now + 3300,
                "last_result": "success",
            },
            now=now,
        )

    assert item["level"] == "at_risk"
    assert item["message_id"] == 9911
    assert "提前放行" in item["reason"]


def test_hehuan_auto_backoff_does_not_reflag_triggering_send():
    now = 1_700_000_000.0
    calls = []

    def fake_recent_script_sends(**kwargs):
        calls.append(kwargs)
        return []

    with patch.object(preflight, "_recent_script_sends", side_effect=fake_recent_script_sends):
        item = preflight._hehuan_status(
            label="ice",
            username="iceeet1",
            send_as_id=3943539390,
            obs={
                "last_observed_at": now - 7200,
                "last_warm_success_at": now - 7200,
                "next_hehuan_time": now - 3600,
                "auto_next_time": now + 1800,
                "auto_last_error_at": now - 20,
                "last_result": "success",
            },
            now=now,
        )

    assert item is None
    assert calls
    assert calls[0]["after_ts"] == now - 20 + preflight.HEHUAN_EARLY_SEND_GRACE_SEC


def test_hehuan_pending_past_deadline_is_at_risk():
    now = 1_700_000_000.0
    item = preflight._hehuan_status(
        label="ice",
        username="iceeet1",
        send_as_id=3943539390,
        obs={
            "last_observed_at": now - 100,
            "last_result": "pending",
            "auto_pending_msg_id": 9922,
            "auto_pending_sent_at": now - 240,
            "auto_pending_deadline_at": now - 30,
        },
        now=now,
    )

    assert item["level"] == "at_risk"
    assert item["pending_msg_id"] == 9922
    assert "不能重发" in item["reason"]
