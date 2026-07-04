from tools import defensive_preflight as preflight


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
            "current_change": "探索",
            "current_change_until": now + 3600,
            "tianji_value": 40,
        },
        timeline={"phase": "blocked_replan"},
        config={"route_prepare_lead_sec": 300},
        now=now,
    )

    assert item["level"] == "healthy"
    assert "均有效" in item["reason"]
