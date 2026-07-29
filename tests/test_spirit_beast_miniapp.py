import json
from types import SimpleNamespace

from model.features import spirit_beast_miniapp


def _event(url, text="进入万兽谷"):
    button = SimpleNamespace(
        button=SimpleNamespace(text=text, web_app=SimpleNamespace(url=url)),
    )
    row = SimpleNamespace(buttons=[button])
    return SimpleNamespace(
        message=SimpleNamespace(reply_markup=SimpleNamespace(rows=[row]), text=text),
    )


def test_spirit_beast_adapter_is_manual_only_and_scoped():
    adapter = spirit_beast_miniapp.build_spirit_beast_miniapp_adapter()
    assert adapter.manual_only is True
    assert adapter.default_enabled is False
    assert set(adapter.safe_summary()["endpoint_keys"]) == {
        "start", "expedition_start", "expedition_choose",
    }
    assert adapter.allowed_api_paths == ("/api/miniapp/xianxia-spirit-beast/",)


def test_spirit_beast_entry_extracts_token_but_safe_summary_redacts_it():
    launch = spirit_beast_miniapp.extract_spirit_beast_miniapp_launch(
        _event("https://t.me/fanrenxiuxian_bot/app?startapp=spiritbeast_SECRET9999"),
    )
    assert launch["token"] == "spiritbeast_SECRET9999"
    serialized = json.dumps(launch["safe_summary"], ensure_ascii=False)
    assert "spiritbeast_SECRET9999" not in serialized
    assert launch["safe_summary"]["adapter_key"] == "spirit_beast"


def test_spirit_beast_request_has_expected_shape_without_transport_retry():
    request = spirit_beast_miniapp.build_spirit_beast_miniapp_request(
        "expedition_choose",
        token="spiritbeast_SECRET9999",
        init_data="query_id=secret&hash=hidden",
        payload={"runToken": "run_secret", "seq": 1, "approach": "trace"},
    )
    assert request["url"].endswith("/api/miniapp/xianxia-spirit-beast/expedition/choose")
    assert request["payload"]["approach"] == "trace"
    assert request["safe_summary"]["secret_keys"] == ["initData", "token"]
    assert request["safe_summary"]["has_init_data"] is True


def test_spirit_beast_state_and_outcome_parsers_keep_only_safe_fields():
    state = spirit_beast_miniapp.parse_spirit_beast_state({
        "attempts": {"used": 2, "limit": 8, "remaining": 6},
        "beasts": [
            {"id": 1, "canExpedition": True},
            {"id": 2, "status": "休息中", "stamina": 24},
            {"id": 3, "status": "休息中", "stamina": 3},
        ],
        "expedition": {"runToken": "secret", "seq": 1},
    })
    assert state == {
        "daily_used": 2,
        "daily_limit": 8,
        "daily_remaining": 6,
        "beast_count": 3,
        "eligible_beast_count": 2,
        "expedition_active": True,
        "expedition_keys": ["seq"],
    }
    outcome = spirit_beast_miniapp.parse_spirit_beast_outcome({
        "outcome": {"score": 18},
        "history": [{"score": 18, "reward": {"name": "灵兽精魄", "count": 2, "grade": "稀有"}}],
    })
    assert outcome == {
        "score": 18,
        "grade": "稀有",
        "reward_name": "灵兽精魄",
        "reward_count": 2,
    }


def test_spirit_beast_flow_plan_has_no_production_scheduler():
    plan = spirit_beast_miniapp.build_spirit_beast_miniapp_flow_plan()
    assert plan.manual_only is True
    assert plan.default_enabled is False
    assert [step.key for step in plan.steps] == [
        "launch", "start", "expedition_start", "expedition_choose",
    ]
