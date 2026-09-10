import copy
import itertools
import json

import pytest

from model import resource_accounting as accounting


CHAT = -10065001


def point(at, msg_id, chat=CHAT, *, edited=False):
    return {
        "at": float(at),
        "evidence": {"source": "telegram", "chat_id": chat, "msg_id": msg_id, "edited": edited},
    }


def baseline(value=500000, at=100, msg_id=10):
    return accounting.record_snapshot({}, value, point(at, msg_id))


def debit(ledger, number, *, amount=-60000, start=110, end=120, chat=CHAT):
    return accounting.record_delta(
        ledger, f"duel:{chat}:{number}", amount,
        point(start, number, chat), point(end, number + 1, chat),
    )


def test_two_chats_do_not_share_a_message_id_watermark():
    ledger = debit(baseline(), 1000)
    ledger = debit(ledger, 100, chat=CHAT - 1, start=125, end=130)
    assert accounting.evaluate(ledger) == {"status": "ready", "value": 380000}


def test_older_loss_after_newer_absolute_snapshot_is_already_included():
    ledger = accounting.record_snapshot({}, 200000, point(140, 1000))
    ledger = debit(ledger, 100, start=110, end=120)
    assert accounting.evaluate(ledger)["value"] == 200000


def test_delayed_snapshot_retains_later_known_loss():
    ledger = debit(baseline(), 100, start=115, end=120)
    ledger = accounting.record_snapshot(ledger, 500000, point(110, 50))
    assert accounting.evaluate(ledger)["value"] == 440000


@pytest.mark.parametrize("order", list(itertools.permutations([0, 1, 2])))
def test_distinct_out_of_order_changes_are_each_counted_once(order):
    ledger = baseline()
    for index in (*order, *reversed(order)):
        ledger = debit(ledger, 20 + index * 2, amount=-10000, start=110 + index * 2, end=111 + index * 2)
    assert accounting.evaluate(ledger)["value"] == 470000
    assert len(ledger["entries"]) == 3


def test_snapshot_during_execution_does_not_guess_inclusion():
    ledger = debit(baseline(), 100, start=110, end=130)
    ledger = accounting.record_snapshot(ledger, 440000, point(120, 50))
    assert accounting.evaluate(ledger) == {"status": "overlapping_observation", "value": None}
    ledger = accounting.record_snapshot(ledger, 440000, point(140, 200))
    assert accounting.evaluate(ledger)["value"] == 440000


def test_old_snapshot_cannot_regress_baseline_or_projection():
    ledger = debit(baseline(), 100)
    before = copy.deepcopy(ledger)
    ledger = accounting.record_snapshot(ledger, 900000, point(90, 9))
    assert ledger == before
    assert accounting.evaluate(ledger)["value"] == 440000


@pytest.mark.parametrize("other", [point(100, 10), point(100, 11, CHAT - 1)])
def test_conflicting_snapshots_block_spending_until_a_newer_snapshot(other):
    ledger = accounting.record_snapshot(baseline(), 400000, other)
    assert accounting.evaluate(ledger) == {"status": "conflicting_snapshot", "value": None}
    ledger = accounting.record_snapshot(ledger, 500000, point(100, 10, edited=True))
    assert accounting.evaluate(ledger)["status"] == "conflicting_snapshot"
    ledger = accounting.record_snapshot(ledger, 400000, point(110, 20))
    assert accounting.evaluate(ledger)["value"] == 400000


def test_same_second_order_requires_comparable_message_positions():
    ledger = accounting.record_snapshot({}, 1000, point(100, 20))
    ledger = debit(ledger, 30, amount=-50, start=100, end=100)
    assert accounting.evaluate(ledger)["value"] == 950
    ledger = debit(ledger, 30, amount=-50, start=100, end=100, chat=CHAT - 1)
    assert accounting.evaluate(ledger)["status"] == "overlapping_observation"


def test_same_second_completed_result_before_snapshot_is_included():
    ledger = accounting.record_snapshot({}, 1000, point(100, 40))
    ledger = debit(ledger, 30, amount=-50, start=100, end=100)
    assert accounting.evaluate(ledger)["value"] == 1000


def test_changed_final_edit_revises_one_charge_and_rejects_old_delivery():
    ledger = debit(baseline(), 100)
    original = copy.deepcopy(ledger)
    ledger = accounting.record_delta(
        ledger, f"duel:{CHAT}:100", -50000, point(110, 100), point(120, 101),
        revision=point(130, 101, edited=True),
    )
    assert accounting.evaluate(ledger)["value"] == 450000
    entry = original["entries"][0]
    ledger = accounting.record_delta(ledger, entry["key"], entry["amount"], entry["start"], entry["end"])
    assert accounting.evaluate(ledger)["value"] == 450000
    assert len(ledger["entries"]) == 1


def test_zero_revision_retracts_an_earlier_resource_amount():
    ledger = debit(baseline(), 100, amount=60000)
    ledger = accounting.record_delta(
        ledger, f"duel:{CHAT}:100", 0, point(110, 100), point(130, 101, edited=True),
    )
    assert accounting.evaluate(ledger) == {"status": "ready", "value": 500000}


def test_correction_across_snapshot_requires_another_authoritative_observation():
    ledger = debit(baseline(), 100)
    ledger = accounting.record_snapshot(ledger, 440000, point(125, 150))
    ledger = accounting.record_delta(
        ledger, f"duel:{CHAT}:100", -50000, point(110, 100), point(120, 101),
        revision=point(130, 101, edited=True),
    )
    assert accounting.evaluate(ledger)["status"] == "overlapping_observation"
    ledger = accounting.record_snapshot(ledger, 450000, point(140, 200))
    assert accounting.evaluate(ledger)["value"] == 450000


def test_replayed_correction_does_not_shrink_its_execution_upper_bound():
    ledger = debit(baseline(), 100)
    ledger = accounting.record_snapshot(ledger, 440000, point(125, 150))
    for _ in range(2):
        ledger = accounting.record_delta(
            ledger, f"duel:{CHAT}:100", -50000, point(110, 100), point(120, 101),
            revision=point(130, 101, edited=True),
        )
        assert accounting.evaluate(ledger)["status"] == "overlapping_observation"


def test_amount_changed_back_to_old_value_does_not_erase_the_intervening_revision():
    ledger = debit(baseline(), 100, amount=60000)
    for at, amount in ((130, 50000), (140, 60000)):
        ledger = accounting.record_delta(ledger, f"duel:{CHAT}:100", amount, point(110, 100), point(at, 101, edited=True))
    ledger = accounting.record_snapshot(ledger, 550000, point(135, 200))
    ledger = debit(ledger, 100, amount=60000)
    assert accounting.evaluate(ledger)["status"] == "overlapping_observation"


@pytest.mark.parametrize("snapshot_at, snapshot_value, expected", [
    (100, 500000, 560000), (125, 560000, None), (135, 550000, None), (150, 560000, 560000),
])
def test_all_delivery_orders_preserve_amount_revisions_across_snapshots(snapshot_at, snapshot_value, expected):
    facts = [(120, 60000, False), (130, 50000, True), (140, 60000, True)]
    for order in itertools.permutations(facts):
        ledger = accounting.record_snapshot({}, snapshot_value, point(snapshot_at, 200))
        for at, amount, edited in [*order, *reversed(order)]:
            ledger = accounting.record_delta(ledger, "duel:command:100", amount, point(110, 100), point(at, 101, edited=edited))
        assert accounting.read_ledger(ledger) is not None, order
        assert accounting.evaluate(ledger)["value"] == expected, order
        assert len(ledger["entries"]) == 1


def test_same_result_with_a_conflicting_command_cannot_keep_old_credit_spendable():
    ledger = debit(baseline(), 100, amount=60000)
    ledger = accounting.record_delta(ledger, "duel:other", 50000, point(105, 90), point(130, 101, edited=True))
    assert accounting.evaluate(ledger)["status"] == "conflicting_revision"


def test_conflicting_command_on_another_result_keeps_a_valid_recoverable_ledger():
    ledger = debit(baseline(), 100, amount=60000)
    ledger = accounting.record_delta(ledger, f"duel:{CHAT}:100", 50000, point(105, 90), point(130, 102))
    assert accounting.read_ledger(ledger) is not None
    assert accounting.evaluate(ledger)["status"] == "conflicting_revision"
    ledger = accounting.record_delta(ledger, f"duel:{CHAT}:100", 50000, point(110, 100), point(140, 102, edited=True))
    assert accounting.evaluate(ledger)["value"] == 550000


def test_duplicate_original_command_under_two_keys_is_rejected_on_reload():
    ledger = debit(baseline(), 100, amount=60000)
    duplicate = copy.deepcopy(ledger["entries"][0])
    duplicate["key"] = "duel:other"
    duplicate["end"] = duplicate["revision"] = point(130, 102)
    duplicate["sources"] = [102]
    ledger["entries"].append(duplicate)
    assert accounting.read_ledger(ledger) is None


def test_first_seen_edited_result_keeps_the_revision_upper_bound():
    ledger = accounting.record_snapshot({}, 440000, point(125, 150))
    ledger = accounting.record_delta(
        ledger, f"duel:{CHAT}:100", -50000, point(110, 100), point(120, 101),
        revision=point(130, 101, edited=True),
    )
    assert accounting.evaluate(ledger)["status"] == "overlapping_observation"


def test_conflict_cannot_be_covered_by_a_snapshot_before_the_conflicting_revision():
    ledger = debit(baseline(), 100)
    ledger = accounting.record_delta(
        ledger, f"duel:{CHAT}:100", -60000, point(110, 100), point(140, 102),
    )
    ledger = accounting.record_snapshot(ledger, 440000, point(130, 103))
    ledger = accounting.record_delta(
        ledger, f"duel:{CHAT}:100", -50000, point(110, 100), point(140, 102),
    )
    assert accounting.evaluate(ledger)["status"] == "conflicting_revision"
    ledger = accounting.record_snapshot(ledger, 450000, point(150, 200))
    assert accounting.evaluate(ledger)["value"] == 450000


def test_same_second_conflict_is_not_ordered_by_one_selected_message_id():
    ledger = debit(baseline(), 100)
    ledger = accounting.record_delta(
        ledger, f"duel:{CHAT}:100", -50000, point(110, 100), point(120, 102, edited=True),
    )
    ledger = accounting.record_snapshot(ledger, 450000, point(120, 103))
    assert accounting.evaluate(ledger)["status"] == "conflicting_revision"
    ledger = accounting.record_delta(
        ledger, f"duel:{CHAT}:100", -50000, point(110, 100), point(120, 103, edited=True),
    )
    assert accounting.evaluate(ledger)["status"] == "conflicting_revision"


def test_conflicting_same_revision_is_not_resolved_by_arrival_order():
    ledger = debit(baseline(), 100)
    ledger = debit(ledger, 100, amount=-50000)
    assert accounting.evaluate(ledger)["status"] == "conflicting_revision"
    ledger = accounting.record_delta(
        ledger, f"duel:{CHAT}:100", -50000, point(110, 100), point(120, 101),
        revision=point(130, 101, edited=True),
    )
    assert accounting.evaluate(ledger)["value"] == 450000


def test_duplicate_final_message_is_not_another_charge():
    ledger = debit(baseline(), 100)
    ledger = accounting.record_delta(ledger, f"duel:{CHAT}:100", -60000, point(110, 100), point(140, 102))
    assert accounting.evaluate(ledger)["value"] == 440000
    assert ledger["entries"][0]["end"] == point(120, 101)


def test_two_unanchored_results_are_merged_when_their_shared_command_is_proved():
    ledger = accounting.record_delta(baseline(), "duel:result:101", 60000, None, point(120, 101))
    ledger = accounting.record_delta(ledger, "duel:result:102", 60000, None, point(130, 102))
    ledger = accounting.record_delta(ledger, "duel:command:100", 60000, point(110, 100), point(120, 101))
    ledger = accounting.record_delta(ledger, "duel:command:100", 60000, point(110, 100), point(130, 102))
    assert accounting.evaluate(ledger) == {"status": "ready", "value": 560000}
    assert len(ledger["entries"]) == 1
    assert ledger["entries"][0]["sources"] == [101, 102]


def test_edit_of_intermediate_duplicate_source_still_revises_the_same_operation():
    ledger = debit(baseline(), 100, amount=60000)
    for msg_id in (102, 103):
        ledger = accounting.record_delta(ledger, f"duel:{CHAT}:100", 60000, point(110, 100), point(120 + msg_id, msg_id))
    ledger = accounting.record_unresolved_delta(ledger, "duel:result:102", None, point(250, 102, edited=True))
    assert len(ledger["entries"]) == 1
    assert accounting.evaluate(ledger)["status"] == "conflicting_revision"
    ledger = accounting.record_delta(ledger, f"duel:{CHAT}:100", 50000, point(110, 100), point(260, 103, edited=True))
    assert accounting.evaluate(ledger)["value"] == 550000


def test_source_capacity_remains_bounded_without_silently_losing_an_edit(monkeypatch):
    monkeypatch.setattr(accounting, "MAX_SOURCES_PER_ENTRY", 2)
    ledger = debit(baseline(), 100, amount=60000)
    ledger = accounting.record_delta(ledger, f"duel:{CHAT}:100", 60000, point(110, 100), point(130, 102))
    ledger = accounting.record_delta(ledger, f"duel:{CHAT}:100", 50000, point(110, 100), point(140, 103))
    assert ledger["entries"][0]["sources"] == [101, 102]
    assert accounting.evaluate(ledger)["status"] == "capacity"
    ledger = accounting.record_snapshot(ledger, 550000, point(150, 200))
    assert accounting.evaluate(ledger)["value"] == 550000
    ledger = accounting.record_delta(ledger, f"duel:{CHAT}:100", 50000, point(110, 100), point(140, 103))
    assert accounting.evaluate(ledger)["value"] == 550000


def test_corrupt_duplicate_source_cannot_be_loaded_as_two_spendable_credits():
    ledger = debit(baseline(), 100, amount=60000)
    duplicate = copy.deepcopy(ledger["entries"][0])
    duplicate["key"] += ":duplicate"
    ledger["entries"].append(duplicate)
    assert accounting.read_ledger(ledger) is None


def test_earlier_identical_result_can_tighten_an_execution_interval():
    ledger = accounting.record_snapshot({}, 440000, point(130, 102))
    ledger = accounting.record_delta(ledger, f"duel:{CHAT}:100", -60000, point(110, 100), point(140, 103))
    assert accounting.evaluate(ledger)["status"] == "overlapping_observation"
    ledger = accounting.record_delta(ledger, f"duel:{CHAT}:100", -60000, point(110, 100), point(120, 101))
    assert accounting.evaluate(ledger)["value"] == 440000


def test_unknown_command_time_is_not_manufactured_from_receipt_time():
    ledger = accounting.record_delta(baseline(), "soothe:1", -50, None, point(120, 30))
    assert accounting.evaluate(ledger)["status"] == "overlapping_observation"
    ledger = accounting.record_snapshot(ledger, 499950, point(130, 40))
    assert accounting.evaluate(ledger)["value"] == 499950


def test_api_request_time_does_not_certify_a_resource_snapshot():
    original = baseline()
    assert accounting.record_snapshot(original, 1000, {
        "at": 100.0, "evidence": {"source": "api", "requested_at": 100.2},
    }) is None
    assert accounting.evaluate(original)["value"] == 500000
    ledger = accounting.record_snapshot(original, 499950, point(130, 40))
    assert accounting.evaluate(ledger)["value"] == 499950


def test_unverified_api_baseline_cannot_be_loaded_as_a_spendable_ledger():
    ledger = baseline()
    ledger["baseline"]["point"] = {
        "at": 100.0, "evidence": {"source": "api", "requested_at": 100.2},
    }
    assert accounting.read_ledger(ledger) is None
    assert accounting.evaluate(ledger)["status"] == "corrupt"


def test_capacity_does_not_forget_an_uncovered_charge(monkeypatch):
    monkeypatch.setattr(accounting, "MAX_ENTRIES", 2)
    ledger = debit(baseline(), 20)
    ledger = debit(ledger, 30, start=130, end=140)
    before = copy.deepcopy(ledger["entries"])
    ledger = debit(ledger, 40, start=150, end=160)
    assert ledger["entries"] == before
    assert accounting.evaluate(ledger) == {"status": "capacity", "value": None}
    ledger = accounting.record_snapshot(ledger, 380000, point(145, 35))
    assert accounting.evaluate(ledger)["status"] == "capacity"
    ledger = accounting.record_snapshot(ledger, 320000, point(170, 50))
    assert accounting.evaluate(ledger)["value"] == 320000


def test_evicted_covered_charge_cannot_become_a_new_debit(monkeypatch):
    monkeypatch.setattr(accounting, "MAX_ENTRIES", 2)
    ledger = debit(baseline(), 20)
    ledger = debit(ledger, 30, start=130, end=140)
    ledger = accounting.record_snapshot(ledger, 380000, point(150, 40))
    ledger = debit(ledger, 50, start=160, end=170)
    assert len(ledger["entries"]) == 2
    ledger = debit(ledger, 20)
    assert accounting.evaluate(ledger)["value"] == 320000
    assert len(ledger["entries"]) <= 2


def test_late_duplicate_for_evicted_root_must_not_guess_a_new_execution(monkeypatch):
    monkeypatch.setattr(accounting, "MAX_ENTRIES", 1)
    ledger = debit(baseline(), 20)
    ledger = accounting.record_snapshot(ledger, 440000, point(130, 40))
    ledger = debit(ledger, 50, start=140, end=150)
    ledger = accounting.record_delta(ledger, f"duel:{CHAT}:20", -60000, point(110, 20), point(160, 60))
    assert accounting.evaluate(ledger)["value"] is None


def test_real_zero_and_inconsistent_negative_balance_are_distinct():
    ledger = debit(baseline(value=60000), 100)
    assert accounting.evaluate(ledger) == {"status": "ready", "value": 0}
    ledger = debit(ledger, 200, amount=-50, start=125, end=130)
    assert accounting.evaluate(ledger) == {"status": "inconsistent_balance", "value": None}


def test_positive_and_negative_deltas_share_the_same_baseline():
    ledger = debit(baseline(), 100)
    ledger = debit(ledger, 200, amount=2000, start=125, end=130)
    assert accounting.evaluate(ledger)["value"] == 442000


def test_json_reload_retains_deduplication_and_projection():
    ledger = debit(baseline(), 100)
    ledger = accounting.read_ledger(json.loads(json.dumps(ledger)))
    before = copy.deepcopy(ledger)
    ledger = debit(ledger, 100)
    assert ledger == before
    assert accounting.evaluate(ledger)["value"] == 440000


@pytest.mark.parametrize("corrupt", [None, [], {"version": True}, {"entries": []}])
def test_corruption_does_not_become_fresh_empty_history(corrupt):
    assert accounting.read_ledger(corrupt) is None
    assert debit(corrupt, 100) is None
    assert accounting.record_snapshot(corrupt, 1000, point(100, 10)) is None
    assert accounting.evaluate(corrupt)["status"] == "corrupt"


@pytest.mark.parametrize("fault", ["amount", "version", "time", "root", "source", "duplicate"])
def test_malformed_persisted_receipts_are_rejected(fault):
    ledger = debit(baseline(), 100)
    if fault == "amount":
        ledger["entries"][0]["amount"] = True
    elif fault == "version":
        ledger["version"] = 1.0
    elif fault == "time":
        ledger["entries"][0]["start"]["at"] = float("nan")
    elif fault == "root":
        ledger["entries"][0]["start"]["evidence"]["msg_id"] = 1000
    elif fault == "source":
        ledger["entries"][0]["end"]["evidence"]["source"] = "untrusted"
    else:
        ledger["entries"].append(copy.deepcopy(ledger["entries"][0]))
    assert accounting.read_ledger(ledger) is None


def test_transitions_do_not_mutate_caller_owned_values():
    ledger = baseline()
    before = copy.deepcopy(ledger)
    end = point(120, 101)
    updated = accounting.record_delta(ledger, "duel:100", -60000, point(110, 100), end)
    end["at"] = 999
    assert ledger == before
    assert updated["entries"][0]["end"]["at"] == 120
