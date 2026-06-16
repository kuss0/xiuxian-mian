from model.features import auto_repair


def _item(item_id, name, durability, max_durability):
    return {
        "item_id": item_id,
        "name": name,
        "durability": durability,
        "max_durability": max_durability,
    }


def _snapshot(items, equipped, lingshi, xiuwei):
    return {
        "equipped_treasure_id": equipped,
        "cultivation_points": xiuwei,
        "inventory": {
            "items": items,
            "materials": {"灵石": lingshi},
        },
    }


def test_no_item_below_threshold_emits_nothing():
    decision = auto_repair.decide_auto_repair(
        _snapshot([_item("t1", "剑", 80, 100)], ["t1"], 1_000_000, 1_000_000)
    )

    assert not decision.should_send
    assert decision.reason == "no_item_below_threshold"


def test_equipped_below_threshold_and_affordable_emits():
    decision = auto_repair.decide_auto_repair(
        _snapshot([_item("t1", "剑", 30, 100)], ["t1"], 3_500, 14_000)
    )

    assert decision.should_send
    assert decision.command == ".一键修理"
    assert decision.total_missing == 70
    assert decision.cost_lingshi == 3_500
    assert decision.cost_xiuwei == 14_000


def test_cost_sums_all_damaged_items_in_scope():
    items = [_item("t1", "甲", 20, 100), _item("t2", "乙", 95, 100)]
    decision = auto_repair.decide_auto_repair(_snapshot(items, ["t1", "t2"], 4_250, 17_000))

    assert decision.should_send
    assert decision.total_missing == 85
    assert decision.cost_lingshi == 4_250
    assert decision.cost_xiuwei == 17_000
    assert [candidate.item_id for candidate in decision.trigger_candidates] == ["t1"]
    assert [candidate.item_id for candidate in decision.repair_candidates] == ["t1", "t2"]


def test_resource_shortage_blocks_send():
    items = [_item("t1", "甲", 20, 100), _item("t2", "乙", 95, 100)]

    lingshi_short = auto_repair.decide_auto_repair(_snapshot(items, ["t1", "t2"], 4_249, 17_000))
    assert not lingshi_short.should_send
    assert lingshi_short.reason == "lingshi_shortage"

    xiuwei_short = auto_repair.decide_auto_repair(_snapshot(items, ["t1", "t2"], 4_250, 16_999))
    assert not xiuwei_short.should_send
    assert xiuwei_short.reason == "xiuwei_shortage"


def test_scope_all_sees_backpack_but_equipped_does_not():
    snapshot = _snapshot([_item("t2", "盾", 10, 100)], [], 100_000, 100_000)

    equipped_decision = auto_repair.decide_auto_repair(snapshot)
    assert not equipped_decision.should_send
    assert equipped_decision.reason == "no_item_below_threshold"

    all_decision = auto_repair.decide_auto_repair(snapshot, scope=auto_repair.DURABILITY_SCOPE_ALL)
    assert all_decision.should_send
    assert all_decision.total_missing == 90


def test_threshold_is_strictly_below():
    decision = auto_repair.decide_auto_repair(
        _snapshot([_item("t1", "剑", 50, 100)], ["t1"], 1_000_000, 1_000_000)
    )

    assert not decision.should_send
    assert decision.reason == "no_item_below_threshold"


def test_invalid_shapes_are_conservative():
    decision = auto_repair.decide_auto_repair(
        {
            "equipped_treasure_id": "[\"t1\"]",
            "cultivation_points": 1_000_000,
            "inventory": {"items": [{"item_id": "t1", "durability": "bad", "max_durability": 100}]},
        }
    )

    assert not decision.should_send
    assert decision.reason == "no_item_below_threshold"
