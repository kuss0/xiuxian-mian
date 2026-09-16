import asyncio
from copy import deepcopy
from unittest.mock import Mock

import pytest

from model import state as state_module
from model.features import cave_treasure_miniapp as worker
from model.features import cave_treasure_runtime as runtime
from model.features import treasure_operations as operations
from model.features import treasure_results as results
from model.features.miniapp_common import MiniAppIdentityOwner
from test_treasure_lifecycle import IDENTITY, call, panel, receipt, round_state, run
from test_treasure_lifecycle import h as h
from test_treasure_operations import native as native
from test_treasure_operations import frames_for_run
from test_treasure_operations import treasure_db as treasure_db


def active_run(**changes):
    return {"sessionId": "fixture129", "status": "active", "size": 3,
            "ap": 2, "maxAp": 8, "foundMain": False,
            "cells": [{"index": index, "revealed": False} for index in range(9)], **changes}


def parsed(value):
    return worker.parse_cave_treasure_state({**panel(1, 1), "huntRun": value})


BAD_CELLS = [
    None, [], {}, [1], [{"revealed": False}], [{"index": True, "revealed": False}],
    [{"index": -1, "revealed": False}], [{"index": 0.5, "revealed": False}],
    [{"index": 9, "revealed": False}], [{"index": 0, "revealed": "false"}],
    [{"index": 0}], [{"index": 0, "revealed": False}, {"index": 0, "revealed": True}],
]


@pytest.mark.parametrize("cells", BAD_CELLS)
def test_bad_native_cells_never_authorize_a_guessed_target(cells):
    value = active_run(cells=deepcopy(cells))
    transport = Mock(return_value={**panel(1, 1), "huntRun": value})
    result = run(transport)
    assert transport.call_count == 1
    assert not result["ok"] and not result["outcome_unknown"]
    assert worker.choose_cave_treasure_action(parsed(value))["action"] == "blocked"


@pytest.mark.parametrize("change", [
    {"size": True}, {"size": -1}, {"size": 1.5}, {"size": 0},
    {"maxAp": True}, {"maxAp": "bad"}, {"maxAp": 1}, {"maxAp": -1},
    {"status": "unknown"}, {"status": None},
])
def test_bad_native_run_metadata_cannot_authorize_search(change):
    decision = worker.choose_cave_treasure_action(parsed(active_run(**change)))
    assert decision["action"] == "blocked"


def test_sparse_known_indices_are_not_clamped_to_the_cell_count():
    value = active_run(cells=[{"index": 5, "revealed": False}])
    value.pop("size")
    decision = worker.choose_cave_treasure_action(parsed(value))
    assert decision["action"] == "search"
    assert decision["targetIndex"] == 6


@pytest.mark.parametrize("alias", ["targetCount", "npcCount", "gridCount"])
def test_native_board_bounds_cannot_be_replaced_by_a_legacy_count(alias):
    value = active_run(cells=[{"index": 5, "revealed": False}], **{alias: 1})
    decision = worker.choose_cave_treasure_action(parsed(value))
    assert decision["targetIndex"] == 6


@pytest.mark.parametrize("complete", [False, True])
def test_all_revealed_cells_do_not_trigger_a_random_reveal(complete):
    cells = [{"index": index, "revealed": True} for index in range(9 if complete else 2)]
    decision = worker.choose_cave_treasure_action(parsed(active_run(cells=cells)))
    assert decision["action"] == ("settle" if complete else "blocked")


def test_native_narrative_without_a_main_flag_does_not_authorize_settlement():
    value = active_run(text="\u4f60\u53d1\u73b0\u4e3b\u5b9d\uff0c\u53ef\u89c1\u597d\u5c31\u6536")
    value.pop("foundMain")
    decision = worker.choose_cave_treasure_action(parsed(value))
    assert decision["action"] == "search"


@pytest.mark.parametrize("change", ["unchanged", "ap_only", "missing_cells", "target_missing", "regressed", "geometry", "lost_geometry_bounds"])
def test_reveal_response_requires_owned_progress_before_another_mutation(change):
    before = active_run(hintTarget=2)
    before["cells"][0]["revealed"] = True
    calls = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if endpoint == "start":
            return {**panel(1, 1), "huntRun": deepcopy(before)}
        assert endpoint == "hunt_reveal"
        after = deepcopy(before)
        index = request["payload"]["index"]
        if change == "ap_only":
            after["ap"] = 1
        elif change == "missing_cells":
            after.pop("cells")
        elif change == "target_missing":
            after["ap"] = 1
            after["cells"] = [cell for cell in after["cells"] if cell["index"] != index]
        elif change in {"regressed", "geometry"}:
            after["cells"][index]["revealed"] = True
            if change == "regressed":
                after["cells"][0]["revealed"] = False
            else:
                after["size"] = 4
        elif change == "lost_geometry_bounds":
            after["cells"][index]["revealed"] = True
            after.pop("size")
            after["cells"].append({"index": 9, "revealed": False})
        return {"ok": True, "huntRun": after}

    result = run(transport, max_steps=3)
    assert calls == ["start", "hunt_reveal"]
    assert result["outcome_unknown"]
    assert result["operation_evidence"]["pending"]["action"] == "search"


@pytest.mark.parametrize("terminal", ["found", "failed", "no_ap", "full_board"])
def test_explicit_terminal_run_can_settle_without_inventing_a_target(terminal):
    value = active_run()
    if terminal == "found":
        value["foundMain"] = True
        value.pop("cells")
    elif terminal == "failed":
        value = {"sessionId": "fixture129", "status": "failed"}
    elif terminal == "no_ap":
        value["ap"] = 0
        value.pop("cells")
    else:
        for cell in value["cells"]:
            cell["revealed"] = True
    calls = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if endpoint == "start":
            return {**panel(1, 1), "huntRun": value}
        assert endpoint == "hunt_settle"
        return {**panel(1, 1), "huntResult": {**receipt(), "sessionId": "fixture129"}}

    result = run(transport)
    assert result["ok"] and result["data"]["settled_count"] == 1
    assert calls == ["start", "hunt_settle"]


@pytest.mark.parametrize("caller", ["public", "command"])
def test_native_no_progress_reply_keeps_the_exact_request_unresolved(native, caller):
    base = native.transport

    def transport(request):
        data = base(request)
        if request["safe_summary"]["endpoint"] == "hunt_reveal":
            return {"ok": True, "huntRun": round_state()}
        return data

    native.transport = transport
    asyncio.run(call(native, caller))
    assert native.calls == ["start", "hunt", "hunt_reveal"]
    record = native.owner[operations.STATE_KEY]
    assert record["checkpoint"]["pending"]["action"] == "search"
    assert operations.hold_reason(IDENTITY) == "outcome_unknown_hold"
    assert not state_module.get_inventory_delta_records()
    before = list(native.calls)
    prior_record = deepcopy(record)
    prior_result = deepcopy(native.owner[results.STATE_KEY])
    asyncio.run(call(native, "public"))
    assert native.calls == before + ["start"]
    assert native.owner[operations.STATE_KEY] == prior_record
    assert native.owner[results.STATE_KEY] == prior_result


def test_checkpoint_cannot_authorize_search_without_a_selectable_cell(native):
    async def scenario():
        projection = results.ResultProjection.capture(MiniAppIdentityOwner.capture(IDENTITY))
        writer = operations.CheckpointWriter(projection, player_id=IDENTITY)
        value = {"version": 1, "phase": "response", "sequence": 1,
                 "action_dispatched": False, "pending": {}, "receipts": [], "resolution": {},
                 "retry_after_sec": 0, "ok": False, "status": "running", "error": "",
                 "state": {"in_round": True, "session_key": operations.session_key("fixture129"),
                           "available_targets": [], "action_remaining": 2, "target_count": 9}}
        assert writer(value)
        intent = deepcopy(value)
        intent.update(phase="intent", sequence=2,
                      pending={"action": "search", "session_key": value["state"]["session_key"], "target_index": 1})
        assert not writer(intent)

    asyncio.run(scenario())


@pytest.mark.parametrize("container", ["root", "hint", "latestHint", "cell"])
@pytest.mark.parametrize("value", [1, "bad", {}, None])
def test_malformed_optional_markers_do_not_disable_a_valid_board(container, value):
    current = active_run(cells=[{"index": 5, "revealed": False}])
    if container == "root":
        current["markers"] = value
    elif container == "cell":
        current["cells"][0]["hint"] = {"markers": value}
    else:
        current[container] = {"markers": value}
    decision = worker.choose_cave_treasure_action(parsed(current))
    assert decision["action"] == "search" and decision["targetIndex"] == 6


@pytest.mark.parametrize("index", [True, -1, 4.5, "bad"])
def test_invalid_optional_marker_index_does_not_create_a_hint(index):
    current = active_run(markers=[{"index": index, "kind": "treasure"}])
    assert not parsed(current)["hint_target"]


def test_normal_multiple_reveals_use_distinct_confirmed_indices_and_settle():
    current = active_run(size=2, ap=3, maxAp=3,
                         cells=[{"index": index, "revealed": False} for index in range(4)])
    calls, indices = [], []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if endpoint == "start":
            return {**panel(1, 1), "huntRun": deepcopy(current)}
        if endpoint == "hunt_reveal":
            index = request["payload"]["index"]
            assert index not in indices
            indices.append(index)
            current["cells"][index]["revealed"] = True
            current["ap"] -= 1
            return {"ok": True, "huntRun": deepcopy(current)}
        assert endpoint == "hunt_settle"
        return {**panel(1, 1), "huntResult": {**receipt(), "sessionId": "fixture129", "foundMain": False}}

    result = run(transport)
    assert result["ok"] and result["data"]["settled_count"] == 1
    assert calls == ["start"] + ["hunt_reveal"] * 3 + ["hunt_settle"]
    assert len(set(indices)) == 3


@pytest.mark.parametrize("remaining", [1, 2, 3])
def test_revealed_target_proves_progress_even_when_ap_does_not_decrease(remaining):
    current = active_run()

    def transport(request):
        if request["safe_summary"]["endpoint"] == "start":
            return {**panel(1, 1), "huntRun": deepcopy(current)}
        assert request["safe_summary"]["endpoint"] == "hunt_reveal"
        current["cells"][request["payload"]["index"]]["revealed"] = True
        current["ap"] = remaining
        return {"ok": True, "huntRun": deepcopy(current)}

    result = run(transport, max_steps=1)
    assert result["status"] == "step_limit" and not result["outcome_unknown"]
    assert result["data"]["state"]["action_remaining"] == remaining
    assert len(result["data"]["state"]["revealed_targets"]) == 1


@pytest.mark.parametrize("change", ["no_progress", "missing_proof", "overlap", "outside_board", "false_complete", "lost_geometry"])
def test_checkpoint_rejects_unproven_or_inconsistent_reveal_state(native, change):
    frames = frames_for_run()
    response = deepcopy(frames[4])
    assert frames[3]["pending"]["action"] == "search"
    if change == "no_progress":
        response["state"] = deepcopy(frames[3]["state"])
    elif change == "missing_proof":
        response["state"]["run_verified"] = False
    elif change == "overlap":
        response["state"]["available_targets"] = [1]
    elif change == "outside_board":
        response["state"]["revealed_targets"] = [2]
    elif change == "lost_geometry":
        response["state"].update(board_size=0, board_complete=False)
    else:
        response["state"].update(board_size=2, target_count=4, board_complete=True)

    async def scenario():
        projection = results.ResultProjection.capture(MiniAppIdentityOwner.capture(IDENTITY))
        writer = operations.CheckpointWriter(projection, player_id=IDENTITY)
        for frame in frames[:4]:
            assert writer(frame)
        assert not writer(response)
        assert native.owner[operations.STATE_KEY]["checkpoint"]["pending"]["action"] == "search"

    asyncio.run(scenario())


def test_no_progress_request_and_board_survive_sqlite_reload(treasure_db):
    from model import persistence

    h = treasure_db
    base = h.transport

    def transport(request):
        data = base(request)
        if request["safe_summary"]["endpoint"] == "hunt_reveal":
            data["huntRun"] = round_state()
        return data

    h.transport = transport
    response = asyncio.run(call(h, "public"))
    assert response["extra"]["outcome_unknown"]
    before = deepcopy(h.owner[operations.STATE_KEY])
    game_calls = list(h.calls)
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    restored = state_module.get_identity_state(IDENTITY)[operations.STATE_KEY]
    assert restored == before and operations.valid_record(restored)
    assert restored["checkpoint"]["state"]["board_verified"]
    assert restored["checkpoint"]["state"]["available_targets"] == [1]
    assert restored["checkpoint"]["state"]["revealed_targets"] == []
    assert operations.hold_reason(IDENTITY) == "outcome_unknown_hold"
    runtime.recover_cave_treasure_result(IDENTITY)
    assert h.calls == game_calls
    assert not state_module.get_inventory_delta_records()


def test_partial_replies_cannot_forget_previously_revealed_cells():
    current = active_run(hintTarget=2)
    current["cells"][0]["revealed"] = True
    calls = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if endpoint == "start":
            return {**panel(1, 1), "huntRun": deepcopy(current)}
        assert endpoint == "hunt_reveal"
        for cell in current["cells"]:
            if cell["index"] == request["payload"]["index"]:
                cell["revealed"] = True
        if calls.count("hunt_reveal") == 1:
            current["cells"] = current["cells"][1:]
        else:
            current["cells"].append({"index": 0, "revealed": False})
        return {"ok": True, "huntRun": deepcopy(current)}

    result = run(transport, max_steps=3)
    assert calls == ["start", "hunt_reveal", "hunt_reveal"]
    assert result["outcome_unknown"]
    assert result["operation_evidence"]["state"]["revealed_targets"] == [1, 2]


def test_checkpoint_cannot_forget_an_earlier_revealed_cell(native):
    current = active_run(hintTarget=2)
    current["cells"][0]["revealed"] = True
    frames = []

    def transport(request):
        if request["safe_summary"]["endpoint"] == "start":
            return {**panel(1, 1), "huntRun": deepcopy(current)}
        current["cells"][request["payload"]["index"]]["revealed"] = True
        return {"ok": True, "huntRun": deepcopy(current)}

    run(transport, max_steps=1, checkpoint=lambda frame: frames.append(deepcopy(frame)) or True)
    assert len(frames) == 3
    response = deepcopy(frames[2])
    response["state"]["revealed_targets"].remove(1)
    response["state"]["board_complete"] = False

    async def scenario():
        writer = operations.CheckpointWriter(results.ResultProjection.capture(MiniAppIdentityOwner.capture(IDENTITY)),
                                             player_id=IDENTITY)
        for frame in frames[:2]:
            assert writer(frame)
        assert not writer(response)

    asyncio.run(scenario())


@pytest.mark.parametrize("remaining", ["2", " 2 "])
def test_numeric_action_count_uses_the_same_conversion_for_limit_comparison(remaining):
    current = parsed(active_run())
    current["action_remaining"] = remaining
    assert worker.choose_cave_treasure_action(current)["action"] == "search"
    current["action_limit"] = 1
    assert worker.choose_cave_treasure_action(current)["action"] == "blocked"


@pytest.mark.parametrize("ap", [None, "invalid"])
def test_complete_exhausted_board_does_not_need_search_action_points(ap):
    current = active_run(ap=ap, cells=[{"index": index, "revealed": True} for index in range(9)])
    if ap is None:
        current.pop("ap")
    assert worker.choose_cave_treasure_action(parsed(current))["action"] == "settle"
    current["cells"].pop()
    assert worker.choose_cave_treasure_action(parsed(current))["action"] == "blocked"


@pytest.mark.parametrize("omit_size", [False, True])
def test_partial_last_cell_response_completes_the_retained_board(native, omit_size):
    current = active_run(size=2, cells=[{"index": index, "revealed": index < 3} for index in range(4)])
    calls, frames = [], []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if endpoint == "start":
            return {**panel(1, 1), "huntRun": deepcopy(current)}
        if endpoint == "hunt_reveal":
            assert request["payload"]["index"] == 3
            current["cells"] = [{"index": 3, "revealed": True}]
            if omit_size:
                current.pop("size")
            return {"ok": True, "huntRun": deepcopy(current)}
        assert endpoint == "hunt_settle"
        return {**panel(1, 1), "huntResult": {**receipt(), "sessionId": "fixture129", "foundMain": False}}

    result = run(transport, checkpoint=lambda frame: frames.append(deepcopy(frame)) or True)
    assert result["ok"] and not result["outcome_unknown"]
    assert calls == ["start", "hunt_reveal", "hunt_settle"]
    assert frames[2]["state"]["board_complete"]
    assert frames[2]["state"]["revealed_targets"] == [1, 2, 3, 4]

    async def scenario():
        writer = operations.CheckpointWriter(results.ResultProjection.capture(MiniAppIdentityOwner.capture(IDENTITY)),
                                             player_id=IDENTITY)
        for frame in frames:
            assert writer(frame)

    asyncio.run(scenario())


def test_partial_reveal_with_no_next_target_is_known_progress_not_an_unknown_request():
    current = active_run()
    calls = []

    def transport(request):
        endpoint = request["safe_summary"]["endpoint"]
        calls.append(endpoint)
        if endpoint == "start":
            return {**panel(1, 1), "huntRun": deepcopy(current)}
        assert endpoint == "hunt_reveal"
        current["cells"] = [{"index": request["payload"]["index"], "revealed": True}]
        return {"ok": True, "huntRun": deepcopy(current)}

    result = run(transport, max_steps=2)
    assert calls == ["start", "hunt_reveal"]
    assert result["status"] == "blocked" and not result["outcome_unknown"]
    assert result["operation_evidence"]["state"]["in_round"]
    assert len(result["operation_evidence"]["state"]["revealed_targets"]) == 1
    assert not result["operation_evidence"]["pending"]
