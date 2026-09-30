from copy import deepcopy

import pytest

from model.features import fishing_dwelling_supply as supply
from model.features.fishing_dwelling_protocol import ProtocolError
from test_fishing_dwelling_journal import context


def shop_context(*, plain=0, rice=0, stone=5000):
    root = context()
    baits = [{"itemId": "plain", "name": "plain", "count": plain, "unlocked": True},
             {"itemId": "rice", "name": "rice", "count": rice, "unlocked": True}]
    root["context"]["baits"] = baits
    root["context"]["shop"] = {
        "castActive": False, "activeChum": None,
        "baits": [{**b, "cost": [{"itemId": "stone", "name": "stone", "qty": cost, "owned": stone}]}
                  for b, cost in zip(baits, (12, 35))],
        "chums": [{"key": "rice-chum", "name": "chum", "usedToday": 0, "remainingToday": 2,
                   "dailyLimit": 2, "affordable": plain >= 2 and stone >= 30,
                   "cost": [{"itemId": "plain", "name": "plain", "qty": 2, "owned": plain},
                            {"itemId": "stone", "name": "stone", "qty": 30, "owned": stone}]}],
    }
    return root


SETTINGS = {"bait_choice": "rice", "auto_buy": True, "buy_count": 20, "chum_names": ("chum",)}


def test_plan_preserves_bait_batch_and_chum_settings():
    assert supply.plan_supply(shop_context(), **SETTINGS) == ("buy-bait", {"baitItemId": "rice", "quantity": 20})
    assert supply.plan_supply(shop_context(rice=20), **SETTINGS) == ("buy-bait", {"baitItemId": "plain", "quantity": 20})
    assert supply.plan_supply(shop_context(rice=20, plain=20), **SETTINGS) == ("chum", {"chumKey": "rice-chum"})
    root = shop_context(rice=20, plain=20)
    root["context"]["shop"]["activeChum"] = {"name": "chum", "remaining": 3}
    assert supply.plan_supply(root, **SETTINGS) == ("", {})
    assert supply.plan_supply(shop_context(rice=1), **{**SETTINGS, "auto_buy": False, "chum_names": ()}) == ("", {})


@pytest.mark.parametrize("change", ["voyage", "rod", "quota", "conflict", "session", "shop_active", "locked",
                                    "disabled", "cost", "currency", "count_disagreement", "duplicate", "unknown_chum"])
def test_bad_or_ineligible_context_cannot_authorize_purchase(change):
    root = shop_context()
    ctx, settings = root["context"], dict(SETTINGS)
    if change == "voyage":
        ctx.update(enabled=False, unavailable="fishing_companion_sailing")
    elif change == "rod":
        ctx["rod"] = None
    elif change == "quota":
        ctx["quota"].update(remaining=0, used=ctx["quota"]["limit"])
    elif change == "conflict":
        ctx["conflict"] = {}
    elif change == "session":
        root["session"] = {"status": "active"}
    elif change == "shop_active":
        ctx["shop"]["castActive"] = True
    elif change == "locked":
        ctx["baits"][1]["unlocked"] = False
    elif change == "disabled":
        ctx["enabled"] = False
    elif change == "cost":
        del ctx["shop"]["baits"][1]["cost"]
    elif change == "currency":
        root = shop_context(stone=500)
    elif change == "count_disagreement":
        ctx["shop"]["baits"][1]["count"] = 20
    elif change == "duplicate":
        ctx["shop"]["baits"].append(deepcopy(ctx["shop"]["baits"][0]))
    elif change == "unknown_chum":
        settings["chum_names"] = ("unknown",)
    with pytest.raises(ProtocolError):
        supply.plan_supply(root, **settings)


def test_missing_bait_can_replenish_but_no_quota_cannot():
    root = shop_context()
    root["context"].update(enabled=False, unavailable="fishing_bait_missing")
    assert supply.plan_supply(root, **SETTINGS)[0] == "buy-bait"
    root["context"]["quota"].update(used=5, remaining=0, limit=5)
    with pytest.raises(ProtocolError):
        supply.plan_supply(root, **SETTINGS)


def test_no_purchase_when_disabled_and_live_daily_limits_take_precedence():
    with pytest.raises(ProtocolError, match="fishing_bait_missing"):
        supply.plan_supply(shop_context(), **{**SETTINGS, "auto_buy": False})
    root = shop_context(rice=1)
    root["context"]["shop"]["chums"][0].update(usedToday=2, remainingToday=0)
    assert supply.plan_supply(root, **SETTINGS) == ("", {})


def test_all_planned_bait_and_chum_costs_are_reserved_before_first_purchase():
    with pytest.raises(ProtocolError, match="fishing_shop_cost_missing"):
        supply.plan_supply(shop_context(stone=969), **SETTINGS)
    assert supply.plan_supply(shop_context(stone=970), **SETTINGS)[0] == "buy-bait"


class Store:
    def __init__(self):
        self.record, self.writes = {}, []
        self.current, self.save_ok = True, True

    def save(self, expected, updated):
        assert self.record == expected
        if not self.save_ok:
            return False
        self.record = deepcopy(updated)
        self.writes.append(deepcopy(updated))
        return True

    def open(self, owner=(1, 2, 3)):
        return supply.SupplyJournal(owner=owner, read_current=lambda: self.record,
                                    compare_and_save=self.save, is_owner_current=lambda: self.current)


def prepare(journal, root=None):
    return journal.prepare(context=root or shop_context(), site_id="west-shore", model_id="ngw", **SETTINGS)


def test_supply_receipt_before_dispatch_and_exact_confirmation():
    store = Store()
    journal = store.open()
    action, expected, payload = prepare(journal)
    assert action == "buy-bait" and store.record["phase"] == "pending"
    assert payload["operationId"] == store.record["operation_id"] and payload["quantity"] == 20
    journal.accept(expected, shop_context(rice=20, stone=4300))
    assert store.record["phase"] == "confirmed" and len(store.writes) == 2
    assert supply.supply_deltas(supply.parse_shop(store.record["before"]), action, store.record["payload"]) == {"rice": 20, "stone": -700}
    with pytest.raises(ProtocolError, match="stale_supply_response"):
        journal.accept(expected, shop_context(rice=20, stone=4300))


@pytest.mark.parametrize("bad", ["false", "no_delta", "partial", "foreign_player", "foreign_operation", "changed"])
def test_unconfirmed_response_retains_original_receipt_and_never_reissues(bad):
    store = Store()
    journal = store.open()
    _, expected, _ = prepare(journal)
    result = shop_context(rice=20, stone=4300)
    if bad == "false":
        result["ok"] = False
    elif bad == "no_delta":
        result = shop_context()
    elif bad == "partial":
        result = {"ok": True}
    elif bad == "foreign_player":
        result["playerId"] = 4
    elif bad == "foreign_operation":
        result["operationId"] = "other"
    elif bad == "changed":
        store.record["revision"] += 1
    original = deepcopy(store.record)
    with pytest.raises(ProtocolError):
        journal.accept(expected, result)
    assert store.record == original
    with pytest.raises(ProtocolError):
        prepare(store.open())
    with pytest.raises(ProtocolError):
        store.open().cancel_undispatched(expected)
    assert store.record == original


def test_confirmed_chum_requires_cost_counter_and_active_effect():
    store = Store()
    journal = store.open()
    _, expected, _ = prepare(journal, shop_context(rice=20, plain=20))
    after = shop_context(rice=20, plain=18, stone=4970)
    with pytest.raises(ProtocolError, match="supply_chum_unconfirmed"):
        journal.accept(expected, after)
    after["context"]["shop"]["chums"][0].update(usedToday=1, remainingToday=1)
    after["context"]["shop"]["activeChum"] = {"name": "chum", "remaining": 3}
    journal.accept(expected, after)
    assert store.record["phase"] == "confirmed"


def test_unsent_compensation_is_only_for_current_process_intent():
    store = Store()
    journal = store.open()
    _, expected, _ = prepare(journal)
    with pytest.raises(ProtocolError):
        store.open().cancel_undispatched(expected)
    journal.cancel_undispatched(expected)
    assert store.record == {}


@pytest.mark.parametrize("change", ["save", "owner", "rebind"])
def test_save_and_owner_failures_do_not_authorize_action(change):
    store = Store()
    journal = store.open()
    if change == "save":
        store.save_ok = False
    elif change == "owner":
        store.current = False
    else:
        prepare(journal)
        with pytest.raises(ProtocolError):
            store.open(owner=(1, 9, 3))
        return
    with pytest.raises(ProtocolError):
        prepare(journal)
    assert store.record == {}
