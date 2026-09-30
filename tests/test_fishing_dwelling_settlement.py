from copy import deepcopy

import pytest

from model.features.fishing_dwelling_protocol import ProtocolError, parse_settlement_resources
from test_fishing_dwelling_journal import response
from test_fishing_dwelling_supply import shop_context


def result():
    data = response(settled=True)
    data["session"]["sessionId"] = 12345
    data["session"]["result"]["bonusLoot"] = [{"name": "gem", "qty": 2}]
    data["context"] = shop_context(plain=18, rice=19)["context"]
    data["context"]["shop"]["activeChum"] = {"name": "chum", "remaining": 3}
    data["context"]["shop"]["chums"][0].update(usedToday=1, remainingToday=1)
    return data


def parse(data):
    return parse_settlement_resources(data, session_id=12345, site_id="west-shore")


def test_separates_rewards_and_fish_from_live_bait_and_chum_stock():
    data = result()
    before = deepcopy(data)
    assert parse(data) == {"rewards": {"gem": 2}, "baits": {"plain": 18, "rice": 19},
                           "active_chum": {"name": "chum", "remaining": 3}, "chum_usage": {"chum": 1}}
    assert data == before and data["session"]["result"]["fish"]["name"] == "fish"


def test_missing_context_does_not_clear_bait_or_chum():
    data = result()
    del data["context"]
    assert parse(data) == {"rewards": {"gem": 2}, "baits": None, "active_chum": None, "chum_usage": None}


def test_empty_bonus_loot_is_an_explicit_zero_not_a_missing_receipt():
    data = result()
    data["session"]["result"].update(caught=False, fish=None, bonusLoot=[])
    assert parse(data)["rewards"] == {}
    del data["session"]["result"]["bonusLoot"]
    with pytest.raises(ProtocolError):
        parse(data)


@pytest.mark.parametrize("loot", [None, {}, [None], [{"name": "gem", "qty": True}],
                                   [{"name": "gem", "qty": -1}], [{"name": "gem", "qty": 1.0}],
                                   [{"name": "", "qty": 1}], [{"name": "gem", "quantity": 2}],
                                   [{"name": "gem", "qty": 1}] * 65])
def test_bad_bonus_loot_cannot_be_silently_dropped(loot):
    data = result()
    data["session"]["result"]["bonusLoot"] = loot
    with pytest.raises(ProtocolError):
        parse(data)


def test_duplicate_named_drops_are_summed_with_a_bound():
    data = result()
    data["session"]["result"]["bonusLoot"] *= 2
    assert parse(data)["rewards"] == {"gem": 4}
    data["session"]["result"]["bonusLoot"] = [{"name": "gem", "qty": 10**9}] * 2
    with pytest.raises(ProtocolError):
        parse(data)


@pytest.mark.parametrize("change", ["foreign", "cross_type", "not_ready", "chum_missing", "bad_chum", "missing_usage", "duplicate_bait"])
def test_unowned_or_incomplete_settlement_cannot_authorize_projection(change):
    data = result()
    if change == "foreign":
        data["session"]["sessionId"] = 12346
    elif change == "cross_type":
        data["session"]["sessionId"] = "12345"
    elif change == "not_ready":
        data["session"]["result"]["ready"] = False
    elif change == "chum_missing":
        del data["context"]["shop"]["activeChum"]
    elif change == "bad_chum":
        data["context"]["shop"]["activeChum"]["remaining"] = True
    elif change == "missing_usage":
        del data["context"]["shop"]["chums"][0]["usedToday"]
    elif change == "duplicate_bait":
        data["context"]["baits"][1]["name"] = "plain"
    with pytest.raises(ProtocolError):
        parse(data)
