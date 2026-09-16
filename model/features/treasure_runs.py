"""Current native treasure boards and evidence of one reveal's progress."""

from .treasure_receipts import treasure_integer, treasure_session_id


TREASURE_MAX_CELLS = 4096
TREASURE_MAX_BOARD_SIZE = 64


def parse_treasure_run(run):
    state = {"state_error": "", "run_verified": False, "in_round": False,
             "session_id": "", "status": "", "action_remaining": 0, "action_limit": 0,
             "treasure_found": False, "settled": False, "board_verified": False,
             "board_complete": False, "board_size": 0, "target_count": 0,
             "available_targets": [], "revealed_targets": []}

    def fail(reason):
        state["state_error"] = reason
        return state

    if not isinstance(run, dict) or not run:
        return fail("hunt_run_invalid")
    session_id = treasure_session_id(run.get("sessionId"))
    status = run.get("status")
    if not session_id:
        return fail("hunt_run_session_missing")
    state.update(session_id=session_id, in_round=status != "settled")
    if not isinstance(status, str) or status not in {"active", "failed", "settled"}:
        return fail("hunt_run_status_invalid")
    state["status"] = status
    if "foundMain" in run and type(run["foundMain"]) is not bool:
        return fail("hunt_run_main_flag_invalid")
    state.update(run_verified=True, treasure_found=run.get("foundMain") is True,
                 settled=status == "settled")
    if status == "settled":
        return state
    ap, limit = treasure_integer(run.get("ap")), treasure_integer(run.get("maxAp"))
    state.update(action_remaining=ap or 0, action_limit=limit or 0)
    terminal = state["treasure_found"] or status == "failed" or ap == 0
    action_error = ""
    if not terminal:
        if ap is None:
            action_error = "hunt_run_actions_missing_or_invalid"
        elif "maxAp" in run and (limit is None or limit <= 0 or ap > limit):
            action_error = "hunt_run_action_limit_invalid"

    def board_error(reason):
        # Settlement does not need a selectable square or unused AP.
        return state if terminal else fail(action_error or reason)

    size = treasure_integer(run.get("size")) if "size" in run else 0
    if size is None or "size" in run and not 0 < size <= TREASURE_MAX_BOARD_SIZE:
        return board_error("hunt_run_size_invalid")
    state["board_size"] = size
    cells = run.get("cells")
    if not isinstance(cells, list) or not 0 < len(cells) <= TREASURE_MAX_CELLS:
        return board_error("hunt_run_cells_missing_or_invalid")
    available, revealed, seen = [], [], set()
    for cell in cells:
        if not isinstance(cell, dict):
            return board_error("hunt_run_cell_invalid")
        index = treasure_integer(cell.get("index"))
        if (index is None or index >= (size * size if size else TREASURE_MAX_CELLS)
                or index in seen or type(cell.get("revealed")) is not bool):
            return board_error("hunt_run_cell_invalid")
        seen.add(index)
        (revealed if cell["revealed"] else available).append(index + 1)
    count = size * size if size else max(seen) + 1
    complete = bool(size and len(seen) == count)
    state.update(board_verified=True, board_complete=complete, target_count=count,
                 available_targets=available, revealed_targets=revealed)
    if not terminal and not (complete and not available) and action_error:
        return fail(action_error)
    return state


def treasure_settlement_allowed(state):
    return (state.get("run_verified") is True and state.get("in_round") is True
            and state.get("status") in {"active", "failed"}
            and not state.get("state_error") and (
                state.get("treasure_found") is True or state.get("status") == "failed"
                or treasure_integer(state.get("action_remaining")) == 0
                or state.get("board_verified") is True and state.get("board_complete") is True
                and not state.get("available_targets")))


def treasure_search_error(state, *, require_target=True):
    if state.get("state_error"):
        return state["state_error"]
    remaining = treasure_integer(state.get("action_remaining"))
    if (state.get("run_verified") is not True or state.get("in_round") is not True
            or state.get("status") != "active" or state.get("treasure_found") is True
            or remaining in (None, 0)):
        return "hunt_run_search_unverified"
    limit = treasure_integer(state.get("action_limit", 0))
    if limit is None or limit and remaining > limit:
        return "hunt_run_action_limit_invalid"
    targets, revealed = state.get("available_targets"), state.get("revealed_targets", [])
    count = treasure_integer(state.get("target_count"))
    if (state.get("board_verified") is not True or count in (None, 0) or count > TREASURE_MAX_CELLS
            or not isinstance(targets, list) or len(targets) > TREASURE_MAX_CELLS
            or require_target and not targets
            or not isinstance(revealed, list) or len(revealed) > TREASURE_MAX_CELLS
            or any(type(value) is not int or not 0 < value <= count for value in targets + revealed)
            or len(set(targets + revealed)) != len(targets) + len(revealed)):
        return "hunt_run_search_targets_unverified"
    return ""


def treasure_reveal_progress_error(before, after, target, *, session_field="session_id"):
    if treasure_search_error(before) or target not in before.get("available_targets", []):
        return "hunt_reveal_request_unverified"
    continuity_error = treasure_run_continuity_error(before, after, session_field=session_field)
    if continuity_error:
        return continuity_error
    if after.get("treasure_found") is True or after.get("status") == "failed":
        return ""
    # Confirming this reveal does not require a target for the next request.
    if not treasure_settlement_allowed(after) and treasure_search_error(after, require_target=False):
        return "hunt_reveal_run_unverified"
    if target in after.get("revealed_targets", []):
        return ""
    if (treasure_integer(after.get("action_remaining")) == 0
            and target not in after.get("available_targets", [])):
        return ""
    return "hunt_reveal_progress_unverified"


def treasure_run_continuity_error(before, after, *, session_field="session_id"):
    if not before.get(session_field) or before.get(session_field) != after.get(session_field):
        return "hunt_response_session_mismatch"
    if after.get("state_error"):
        return after["state_error"]
    if after.get("run_verified") is not True or after.get("in_round") is not True:
        return "hunt_reveal_run_unverified"
    if before.get("board_size") and after.get("board_size") and before["board_size"] != after["board_size"]:
        return "hunt_run_geometry_changed"
    size = before.get("board_size") or after.get("board_size")
    if size and any(target > size * size for state in (before, after)
                    for target in state.get("available_targets", []) + state.get("revealed_targets", [])):
        return "hunt_run_geometry_changed"
    if set(before.get("revealed_targets", [])) & set(after.get("available_targets", [])):
        return "hunt_run_cells_regressed"
    return ""


def retain_treasure_run_evidence(before, observed):
    current = dict(observed)
    if not before.get("session_id") or before["session_id"] != current.get("session_id"):
        return current
    revealed = sorted(set(before.get("revealed_targets", [])) | set(current.get("revealed_targets", [])))
    size = before.get("board_size") or current.get("board_size", 0)
    count = size * size if size else max(before.get("target_count", 0), current.get("target_count", 0))
    current.update(revealed_targets=revealed, board_size=size, target_count=count,
                   board_complete=bool(current.get("board_verified") and size
                                       and len(revealed) + len(current.get("available_targets", [])) == count))
    return current
