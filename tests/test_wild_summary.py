from copy import deepcopy

import pytest

from model.audit_wild import confirmed_wild_outcome, validate_wild_outcome
from model.audit_summary import format_grouped_summary


def response(run=1, cultivation=12000):
    return {"ok": True, "extra": {
        "acted": True, "completed": True, "transport_ok": True,
        "outcome_unknown": False, "phase": "completed",
        "before_wild": {"daily_count": run - 1, "reset_at": 1791388800000},
        "wild": {"daily_count": run, "daily_limit": 8, "reset_at": 1791388800000,
                 "last_at": 1791306000000 + run},
        "action_result": {"ok": True, "completed": True, "type": "wild_experience",
                          "dailyCount": run, "lastAt": 1791306000000 + run,
                          "cultivationDelta": cultivation, "loot": [{"name": "养魂木", "quantity": 1}]},
    }}


def row(identity=1, run=1, cultivation=12000, count=1):
    return {"identity_id": identity, "count": count, "html": "original detail",
            "wild_actor": "[wa]", "wild_outcome": confirmed_wild_outcome(identity, response(run, cultivation))}


@pytest.mark.parametrize("field,value", [
    ("acted", False), ("completed", 1), ("transport_ok", False), ("outcome_unknown", True),
    ("phase", "action_unknown"), ("before_wild", {}), ("wild", {}), ("action_result", {}),
])
def test_unknown_or_unanchored_results_keep_ordinary_copy(field, value):
    result = response()
    result["extra"][field] = value
    assert confirmed_wild_outcome(1, result) is None


@pytest.mark.parametrize("field,value", [
    ("ok", False), ("completed", False), ("type", "status"), ("dailyCount", 2),
    ("lastAt", 0), ("cultivationDelta", "12000"), ("cultivationDelta", True),
    ("loot", [{"name": "item"}]), ("loot", [{"name": "item", "quantity": "2"}]),
    ("tianjiGain", "1"), ("loot", None),
])
def test_contradictory_receipt_or_incomplete_gains_are_not_summed(field, value):
    result = response()
    result["extra"]["action_result"][field] = value
    assert confirmed_wild_outcome(1, result) is None


def test_projection_is_detached_signed_and_excludes_snapshots():
    result = response(cultivation=-1260)
    result["extra"]["account"] = {"cultivation": 999999}
    before = deepcopy(result)
    outcome = confirmed_wild_outcome(1, result)
    assert outcome["gains"] == {"修为": -1260, "养魂木": 1}
    assert validate_wild_outcome(outcome, 1) == outcome
    with pytest.raises(ValueError):
        validate_wild_outcome(outcome, 2)
    outcome["gains"]["修为"] = 0
    assert result == before


def test_repeated_receipt_counts_once_and_preserves_loss_separately():
    rows = [row(count=9), row(run=2, cultivation=-1260), row(count=3)]
    before = deepcopy(rows)
    text = format_grouped_summary(rows, now_text="01:00")
    assert "野外：1 个身份，2 次结算" in text
    assert "修为+12000/-1260" in text
    assert "养魂木+2" in text and "original detail" not in text
    assert rows == before


def test_conflicting_receipts_do_not_silently_sum_or_overwrite():
    text = format_grouped_summary([row(), row(cultivation=99), row(run=2)], now_text="01:00")
    assert "1 次结算" in text and "1 份冲突结算未计收益" in text
    assert "修为+12000" in text and "12099" not in text


def test_other_failures_and_legacy_rows_remain_visible():
    rows = [row(), {"count": 1, "html": "野外结果未知，请核查"},
            {"count": 1, "html": "legacy 修为+777", "identity_id": 1}]
    text = format_grouped_summary(rows, now_text="01:00")
    assert "野外结果未知" in text and "legacy 修为+777" in text
    assert "修为+12000" in text and "12777" not in text


def test_stable_identity_separates_accounts_and_reset_separates_days():
    first = row()
    tomorrow = row()
    tomorrow["wild_outcome"]["reset_at"] += 86400000
    tomorrow["wild_outcome"]["last_at"] += 86400000
    text = format_grouped_summary([first, tomorrow, row(identity=2)], now_text="01:00")
    assert "野外：2 个身份，3 次结算" in text


def test_actor_and_resource_names_are_escaped():
    item = row()
    item["wild_actor"] = "[<b>name</b>]"
    item["wild_outcome"]["gains"] = {"<item>": 2}
    text = format_grouped_summary([item], now_text="01:00")
    assert "&lt;b&gt;name&lt;/b&gt;" in text and "&lt;item&gt;" in text


def test_late_repeat_preserves_latest_actor_name_across_earlier_runs():
    earlier = row()
    earlier.update(wild_actor="[new]", last_at=30)
    later = row(run=2)
    later.update(wild_actor="[old]", last_at=20)
    text = format_grouped_summary([earlier, later], now_text="01:00")
    assert "[new]" in text and "[old]" not in text


def test_current_tianxing_receipt_reuses_parser_without_reading_stale_state():
    from model.features.wild_training import _wild_summary_outcome

    result = response(cultivation=0)
    result["extra"]["action_result"]["rawMessage"] = (
        "【野外历练 · 改命脱险】\n【推命命中】司命演算吻合，天机值 +1，宗门贡献 +30\n"
        "【改命回天】你强行拨正命轨，本次未损修为。"
    )
    outcome = _wild_summary_outcome(1, result)
    assert outcome["gains"] == {"修为": 0, "养魂木": 1, "天机": 1, "贡献": 30}
    result["extra"]["action_result"]["tianjiGain"] = 2
    assert _wild_summary_outcome(1, result) is None


@pytest.mark.parametrize("field,value", [
    ("identity_id", True), ("run", True), ("reset_at", float("nan")), ("last_at", 0),
    ("gains", {"修为": "10"}), ("gains", {"item": 10**19}), ("gains", {"": 1}),
])
def test_persisted_metadata_rejects_corruption(field, value):
    from model.audit_summary_store import _validate_row

    item = row()
    item.update(bucket_key="wild:1", summary_kind="", seq=1, first_at=1, last_at=1,
                first_ts="01:00", last_ts="01:00", plain="original detail")
    item["wild_outcome"][field] = value
    with pytest.raises(ValueError):
        _validate_row(item)
