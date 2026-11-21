import asyncio
import copy
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from model.features import qingxi_quests as quests


SAMPLE = Path(__file__).parent / "fixtures/qingxi-engage-distance-20261121.json"
IDENTITY = 8659059191


@pytest.mark.parametrize("taint", [
    "", "identity", "version", "transport", "error", "non_json", "status", "body", "operation",
    "enemy", "extra_pending", "journal", "other_unknown", "late_write", "settlement", "read_error",
    "flight_locked", "target_dead", "corpse", "fresh_identity", "missing_roster",
])
def test_real_distance_refusal_requires_full_receipt_and_fresh_server_facts(tmp_path, monkeypatch, taint):
    result = json.loads(SAMPLE.read_text())
    pending = result["pending_operations"][0]
    ack = next(row for row in result["receipts"] if row.get("ok") is False)
    fresh = {"ok": True, "playerId": IDENTITY, "quests": {
        "states": {}, "corpses": [], "field": {"enemies": [{"id": pending["enemy_id"], "hp": 10}]},
    }}
    flight = {"ok": True, "mana": {"flightLocked": False}}
    if taint == "identity":
        result["player"] += 1
    elif taint == "version":
        result["upstream_version"] = "unknown"
    elif taint == "transport":
        result["transport_policy"] = 0
    elif taint == "error":
        result["error"] = "quest_transport_unconfirmed"
    elif taint == "non_json":
        ack["parse_error"] = "AbortError"
    elif taint == "status":
        ack["http_status"] = 503
    elif taint == "body":
        ack["shape"].append("grants")
    elif taint == "operation":
        ack["operation_id"] = "unrelated"
    elif taint == "enemy":
        ack["enemy_id"] = "unrelated"
    elif taint == "extra_pending":
        result["pending"] += 1
    elif taint == "journal":
        result["stored_pending"] = []
    elif taint == "other_unknown":
        result["receipts"].append({"resource": "field-cast", "ok": False})
    elif taint == "late_write":
        result["receipts"].append({"resource": "quests", "action": "field-defeat", "ok": True, "at": ack["at"] + 1})
    elif taint == "settlement":
        result["report"]["fieldSettlements"][-1]["ok"] = False
    elif taint == "read_error":
        result["status_error"] = "offline"
    elif taint == "flight_locked":
        flight["mana"]["flightLocked"] = True
    elif taint == "target_dead":
        fresh["quests"]["field"]["enemies"][0]["hp"] = 0
    elif taint == "corpse":
        fresh["quests"]["corpses"].append({"enemyId": pending["enemy_id"]})
    elif taint == "fresh_identity":
        fresh["playerId"] += 1
    elif taint == "missing_roster":
        fresh["quests"]["field"].pop("enemies")
    (tmp_path / "result.json").write_text(json.dumps(result))
    record = {"phase": "held", "gameplay_uncertain": True, "browser": {"evidence_dir": str(tmp_path)}}
    before = copy.deepcopy(record)
    call = AsyncMock(side_effect=[fresh, flight])
    monkeypatch.setattr(quests, "save", Mock())
    asyncio.run(quests.review_browser_hold(call, record, IDENTITY))
    if not taint:
        assert record["gameplay_uncertain"] is False
        review = record["browser_review"]
        assert review["reason"] == "confirmed_field_engage_distance_rejection"
        assert review["abandoned_preparation_pending"] == [pending]
        assert review["rejected_receipt"] == ack
        assert review["flight"]["flightLocked"] is False
        assert call.await_count == 2
    else:
        assert record["gameplay_uncertain"] == before["gameplay_uncertain"]
        assert record["phase"] == "held"
        assert "browser_review" not in record
