"""Xuangu short deadlines require exact send metadata, never a text exemption."""
import copy

import pytest

from tools import safety_watchdog as watchdog


def pair():
    prefix = "xuangu:-1003807888644:walterwa2000:1794533513444"
    previous = {"_epoch": 1000, "sender_id": 8659059191, "text": ".进入玄骨",
                "source_module": "玄骨遗府", "priority": "chain",
                "op_id": prefix + ":8659059191:.进入玄骨", "chain_id": prefix + ":joining"}
    current = {"_epoch": 1005, "sender_id": 301299112, "text": ".玄骨行动 1 护队",
               "source_module": "玄骨遗府", "priority": "urgent_reactive",
               "op_id": prefix + ":301299112:.玄骨行动 1 护队", "chain_id": prefix + ":1"}
    return previous, current


def test_owned_xuangu_deadline_action_uses_the_urgent_global_gap():
    previous, current = pair()
    assert watchdog.is_safe_global_gap_pair(previous, current)


@pytest.mark.parametrize("changes", [
    {"source_module": "其他"}, {"priority": "chain"}, {"op_id": "xuangu:forged"},
    {"sender_id": 2}, {"chain_id": "xuangu:-1003807888644:walterwa2000:1794533513444:2"},
    {"text": ".玄骨行动 2 护队"}, {"text": ".进入玄骨"}, {"_epoch": 1000},
])
def test_unverified_or_too_fast_xuangu_event_retains_the_gap_check(changes):
    previous, current = pair()
    current.update(changes)
    assert not watchdog.is_safe_global_gap_pair(previous, current)


def test_duplicate_xuangu_operation_is_not_exempt():
    previous, current = pair()
    previous = copy.deepcopy(current)
    previous["_epoch"] = 1000
    assert not watchdog.is_safe_global_gap_pair(previous, current)


def test_owned_finish_is_recognized_without_exempting_other_rounds():
    previous, current = pair()
    prefix = current["chain_id"].rsplit(":", 1)[0]
    current.update(text=".玄骨收尾 收拢遗宝", chain_id=prefix + ":5",
                   op_id=prefix + ":301299112:.玄骨收尾 收拢遗宝")
    assert watchdog.is_safe_global_gap_pair(previous, current)
    current["chain_id"] = prefix + ":4"
    assert not watchdog.is_safe_global_gap_pair(previous, current)


@pytest.mark.parametrize("changes", [{}, {"sender_id": 2}, {"priority": "chain"},
                                   {"chain_id": "hunt:1794534886.0619228:2"},
                                   {"text": ".起锚"}, {"source_module": "其他"}])
def test_hunt_urgent_gap_requires_exact_owned_day_and_action(changes):
    previous = {"_epoch": 1000, "op_id": "previous"}
    current = {"_epoch": 1003, "sender_id": 301299112, "text": ".护船",
               "source_module": "出海猎妖", "priority": "urgent_reactive",
               "op_id": "hunt:1794534886.0619228:3:301299112:.护船",
               "chain_id": "hunt:1794534886.0619228:3"}
    current.update(changes)
    assert watchdog.is_safe_global_gap_pair(previous, current) is (not changes)
