"""One owned heart trial: launch, three choices, and bounded read-only recovery."""

import asyncio
import copy
import re

from ..message_log_recovery import iter_message_log_entries_between
from ..state import get_identity_ids
from . import concubine as c


STATE_KEY = "concubine_heart_session"
SOURCE = "concubine_heart"
UNRESOLVED = {"sending", "sent", "unknown"}
PROBE_LIMIT = 3
PROBE_GAP_SEC = 10 * 60
_INFLIGHT = {}
_FOLLOWUPS = {}
PROJECTION_KEYS = (
    "concubine_phase", "concubine_heart_msg_id", "concubine_heart_prompt_msg_id",
    "concubine_heart_round", "concubine_heart_choice_prompt_msg_id", "concubine_heart_choice_round",
    "concubine_heart_choice_sent_at", "concubine_heart_choice_retry_count",
)


def _zero_time(value):
    return type(value) in {int, float} and (value == 0 or c._query_time(value) is not None)


def _token(value, length=32):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{" + str(length) + "}", value) is not None


def _same(left, right):
    if type(left) in {int, float} and type(right) in {int, float}:
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    return left == right


def _probe_facts(text, at):
    if not isinstance(text, str) or len(text) > 4096:
        return None
    parsed = c._parse_status_panel(text, at)
    if not c._is_complete_status_panel(parsed) or not parsed.get("has_partner"):
        return None
    return {"outcome": "panel", "partner": parsed["name"], "due_at": parsed["heart_due_at"], "text": text}


def _valid_step(step, number, *, probe=False):
    required = {"op_id", "round", "command", "reply_to_msg_id", "started_at", "status", "msg_id"}
    optional = {"sent_at", "dispatch_at", "response", "retry_at"}
    if not isinstance(step, dict) or required - step.keys() or step.keys() - required - optional:
        return False
    command = c.CMD_CONCUBINE_STATUS if probe else c.CMD_CONCUBINE_HEART if number == 0 else c.CMD_CONCUBINE_HEART_STEADY
    if (not _token(step["op_id"]) or type(step["round"]) is not int or step["round"] != number
            or step["command"] != command or type(step["reply_to_msg_id"]) is not int
            or not (step["reply_to_msg_id"] == 0 if probe else 0 < step["reply_to_msg_id"] < 2 ** 63)
            or c._query_time(step["started_at"]) is None or type(step["msg_id"]) is not int
            or not 0 <= step["msg_id"] < 2 ** 63 or not isinstance(step["status"], str)
            or step["status"] not in UNRESOLVED | {"unsent", "answered"} | ({"expired"} if probe else set())
            or (step["status"] in {"sent", "answered"} and not step["msg_id"])
            or (step["status"] in {"sending", "unsent"} and step["msg_id"])
            or (step["msg_id"] and step["msg_id"] <= step["reply_to_msg_id"])
            or any(c._query_time(step[key]) is None for key in ("sent_at", "dispatch_at", "retry_at") if key in step)
            or (step["status"] == "unsent" and step.get("retry_at", 0) <= step["started_at"])
            or (bool(step["msg_id"]) != ("sent_at" in step and "dispatch_at" in step))
            or (not step["msg_id"] and bool({"sent_at", "dispatch_at"} & step.keys()))
            or (step["msg_id"] and not step["started_at"] - 1 <= step["dispatch_at"] <= step["sent_at"])):
        return False
    if step["status"] != "answered":
        return "response" not in step
    response = step.get("response")
    if (not isinstance(response, dict) or response.keys() != {"msg_id", "root_msg_id", "sender_id", "at", "event_type", "facts"}
            or c._query_int(response["msg_id"]) <= 0 or type(response["root_msg_id"]) is not int
            or not 0 <= response["root_msg_id"] < 2 ** 63 or c._query_int(response["sender_id"]) <= 0
            or c._query_time(response["at"]) is None or response["at"] < step["dispatch_at"] - 1
            or response["event_type"] not in ("message", "edit") or not isinstance(response["facts"], dict)):
        return False
    facts = response["facts"]
    parsed = _probe_facts(facts.get("text"), response["at"]) if probe else c.heart_contract.parse(facts.get("text"), response["at"])
    if parsed is None or not _same(facts, parsed):
        return False
    if not probe and facts["outcome"] == "round" and (type(facts["round"]) is not int or facts["round"] != number + 1):
        return False
    if not probe and facts["outcome"] == "settlement":
        return bool(number == 3 and facts["choices"] == [c.CMD_CONCUBINE_HEART_STEADY[1:]] * 3
                    and all(type(facts[key]) is int for key in ("cultivation_delta", "affinity_delta", "demon_delta", "demon_current")))
    return True


def _prompt(value):
    for step in reversed(value["steps"]):
        response = step.get("response", {})
        if response.get("facts", {}).get("outcome") == "round":
            return response
    return None


def _bound_response(value, step, response, *, probe=False):
    msg, root, at = response["msg_id"], response["root_msg_id"], response["at"]
    if not step["msg_id"] or at < step["dispatch_at"] - 1:
        return False
    if probe or step["round"] == 0:
        return root == step["msg_id"] and msg > step["msg_id"]
    previous = value["steps"][step["round"] - 1]["response"]
    if at <= previous["at"] or root not in {0, step["msg_id"], value["steps"][0]["msg_id"], previous["msg_id"]}:
        return False
    if response["sender_id"] != previous["sender_id"]:
        return False
    # Telegram edits keep the prompt ID, even after newer choice commands.
    if msg == previous["msg_id"]:
        return response["event_type"] == "edit"
    return root > 0 and msg > step["msg_id"]


def _valid_record(value):
    fields = {"session_id", "identity_id", "account_id", "chat_id", "started_at", "partner", "snapshot_at",
              "affinity", "due_at", "plan_key", "projection", "status", "steps", "probe", "probe_count",
              "probe_after", "replay_after", "next_at", "result"}
    if not isinstance(value, dict) or value.keys() != fields:
        return False
    partner = value["partner"]
    if (not _token(value["session_id"]) or not _token(value["plan_key"], 64)
            or c._query_int(value["identity_id"]) != c.get_current_identity_id()
            or c._query_int(value["account_id"]) <= 0 or not c._query_int(value["chat_id"])
            or not isinstance(partner, str) or not 0 < len(partner) <= 120 or partner != partner.strip()
            or any(ord(char) < 32 for char in partner) or c._query_time(value["started_at"]) is None
            or not _zero_time(value["snapshot_at"]) or value["snapshot_at"] > value["started_at"]
            or type(value["affinity"]) is not int or not 0 <= value["affinity"] < 2 ** 63
            or any(not _zero_time(value[key]) for key in ("due_at", "next_at", "probe_after", "replay_after"))
            or value["status"] not in ("active", "reconcile", "unsent", "complete")
            or not isinstance(value["steps"], list) or not 1 <= len(value["steps"]) <= 4
            or type(value["probe_count"]) is not int or not 0 <= value["probe_count"] <= PROBE_LIMIT
            or not isinstance(value["probe"], dict) or not isinstance(value["result"], dict)):
        return False
    projection = value["projection"]
    if (not isinstance(projection, dict) or projection.keys() != set(PROJECTION_KEYS)
            or not isinstance(projection["concubine_phase"], str)
            or projection["concubine_phase"] not in c.CONCUBINE_HEART_ACTIVE_PHASES
            or any(type(projection[key]) is not int or not 0 <= projection[key] < 2 ** 63
                   for key in PROJECTION_KEYS if key not in {"concubine_phase", "concubine_heart_choice_sent_at"})
            or not _zero_time(projection["concubine_heart_choice_sent_at"])):
        return False
    if (projection["concubine_heart_round"] not in range(4)
            or projection["concubine_heart_choice_round"] not in range(4)
            or projection["concubine_heart_choice_retry_count"] != 0):
        return False
    operations, roots = set(), set()
    for number, step in enumerate(value["steps"]):
        if not _valid_step(step, number) or step["started_at"] < value["started_at"]:
            return False
        if step["op_id"] in operations or (step["msg_id"] and step["msg_id"] in roots):
            return False
        operations.add(step["op_id"])
        roots.add(step["msg_id"])
        if number:
            previous = value["steps"][number - 1].get("response", {})
            if (previous.get("facts", {}).get("outcome") != "round"
                    or step["reply_to_msg_id"] != previous["msg_id"] or step["started_at"] < previous["at"]
                    or (step["msg_id"] and step["msg_id"] <= previous["msg_id"])):
                return False
        if "response" in step and not _bound_response(value, step, step["response"]):
            return False
    probe = value["probe"]
    if probe:
        if (not value["probe_count"] or not _valid_step(probe, 0, probe=True)
                or probe["op_id"] in operations or (probe["msg_id"] and probe["msg_id"] in roots)
                or probe["started_at"] < value["started_at"]
                or (probe["status"] in UNRESOLVED and probe["started_at"] < value["steps"][-1]["started_at"])
                or ("response" in probe and not _bound_response(value, probe, probe["response"], probe=True))):
            return False
    elif value["probe_count"]:
        return False
    last = value["steps"][-1]
    if value["status"] == "unsent" and (len(value["steps"]) != 1 or last["status"] != "unsent" or probe):
        return False
    result = value["result"]
    if value["status"] != "complete":
        if last["status"] == "answered":
            allowed = {"round"} if value["status"] == "active" else {"anchor_lost", "in_progress"}
            if last["response"]["facts"]["outcome"] not in allowed:
                return False
        if value["status"] == "active" and last["status"] == "unsent" and not last["round"]:
            return False
        return not result
    if (result.keys() != {"outcome", "due_at", "affinity_applied", "cooldown_applied", "source"}
            or type(result["affinity_applied"]) is not bool or type(result["cooldown_applied"]) is not bool
            or not _zero_time(result["due_at"])):
        return False
    if result["source"] == last["op_id"] and last["status"] == "answered":
        facts = last["response"]["facts"]
        return bool(result["outcome"] == facts["outcome"]
                    and facts["outcome"] in {"settlement", "cooldown", "resource_shortage", "missing_panel", "voyage_lock"}
                    and result["due_at"] == facts.get("due_at", 0)
                    and (not result["affinity_applied"] or facts["outcome"] == "settlement"))
    if probe and result["source"] == probe["op_id"] and probe["status"] == "answered":
        facts = probe["response"]["facts"]
        return bool(not result["affinity_applied"] and result["due_at"] == facts["due_at"]
                    and ((result["outcome"] == "reconciled_cooldown" and facts["due_at"] > probe["response"]["at"])
                         or (result["outcome"] == "reconciled_ready" and facts["due_at"] == 0
                             and last.get("response", {}).get("facts", {}).get("outcome") == "anchor_lost")))
    return False


def record():
    value = c.state.get(STATE_KEY, {})
    return copy.deepcopy(value) if isinstance(value, dict) and (not value or _valid_record(value)) else None


def _store(value):
    c.state[STATE_KEY] = copy.deepcopy(value)
    c.mark_dirty()


def block_reason():
    value = record()
    if value is None:
        return "invalid"
    if value and value["status"] in {"active", "reconcile"}:
        return "pending"
    if c._phase() in c.CONCUBINE_HEART_ACTIVE_PHASES or any(c.state.get(key) for key in (
            "concubine_heart_msg_id", "concubine_heart_prompt_msg_id", "concubine_heart_choice_prompt_msg_id")):
        return "legacy_pending"
    sessions = c.state.get("action_guard_sessions")
    guard = sessions.get(SOURCE) if isinstance(sessions, dict) else None
    if not value and isinstance(guard, dict) and not guard.get("closed_at"):
        return "legacy_pending"
    return ""


def next_at():
    value, due = record(), c.state.get("concubine_heart_due_at", 0)
    if value is None or not _zero_time(due) or block_reason():
        return float("inf")
    return max(due, (value or {}).get("next_at", 0), (value or {}).get("result", {}).get("due_at", 0))


def panel_anchor(now):
    query = c._status_query_record()
    if (c._query_time(now) is None or not query or query["kind"] not in {"status", "gift_status"}
            or query["status"] != "complete" or query["account_id"] != c.get_identity_account(c.get_current_identity_id())
            or not now - c.CONCUBINE_HEART_PANEL_MAX_AGE_SEC <= query["reply_at"] <= now
            or not _same(c.state.get("concubine_last_snapshot_at"), query["reply_at"])
            or type(c.state.get("concubine_last_panel_msg_id")) is not int
            or c.state["concubine_last_panel_msg_id"] != query["reply_msg_id"]
            or type(c.state.get("concubine_last_panel_chat_id")) is not int
            or c.state["concubine_last_panel_chat_id"] != query["chat_id"]):
        return None
    return {"msg_id": query["reply_msg_id"], "chat_id": query["chat_id"], "event_ts": query["reply_at"],
            "source": "observed_panel" if query.get("origin") == "observed" else "owned_panel"}


def _wire(value, step):
    return dict(step, identity_id=value["identity_id"], account_id=value["account_id"], chat_id=value["chat_id"])


def _clear_pending(value):
    for step in value["steps"] + ([value["probe"]] if value["probe"] else []):
        if step["status"] in {"answered", "expired"} or value["status"] == "complete":
            family = "concubine_status" if step is value["probe"] else SOURCE
            c._clear_status_query_pending(_wire(value, step), source_module=SOURCE, family=family)


def _other_work(value=None):
    query = c._status_query_record()
    pending = c.state.get("pending_tasks")
    if (query is None or (query and query["status"] in c.CONCUBINE_QUERY_UNRESOLVED)
            or c.affinity_actions.block_reason() or c.fragment_actions.block_reason()
            or c.voyage_actions.block_reason() or c.divination_actions.block_reason()
            or c.reacquire_actions.block_reason() or not isinstance(pending, dict)):
        return True
    owned = (value["steps"] + ([value["probe"]] if value["probe"] else [])) if value else []
    for key, item in pending.items():
        if not isinstance(item, dict):
            return True
        if any(c._status_query_pending_matches(_wire(value, step), item, source_module=SOURCE)
               and c._status_query_pending_ref(key, item) == (value["chat_id"], step["msg_id"])
               for step in owned if step["msg_id"]):
            continue
        words = c.get_pending_command(item).split()
        if str(item.get("family") or "").startswith("concubine_") or (words and words[0] in c.CONCUBINE_PENDING_COMMANDS):
            return True
    return False


def _controls(owner):
    return bool(c._owns_status_query(owner) and owner[2] > 0 and c.get_global_enabled()
                and c.get_identity_enabled(owner[0]) and owner[1].get("concubine_heart_enabled") and c.get_game_group_id())


def _can_act(owner, value, now):
    return bool(_controls(owner) and c._status_query_plan(owner) == value["plan_key"]
                and not _other_work(value) and not c._has_active_nanlong_pending(now)
                and not c._has_phaseful_summary_window(now) and not c._has_voyage_runtime_state(now))


def _global_start_at(identity_id):
    latest = 0
    for other_id in get_identity_ids():
        if other_id == identity_id:
            continue
        with c.use_identity(other_id):
            other = record()
        if other and other["steps"][0]["status"] != "unsent":
            step = other["steps"][0]
            latest = max(latest, step.get("sent_at", step["started_at"]))
    return latest + c.CONCUBINE_HEART_GLOBAL_START_GAP_SEC if latest else 0


def _project(value):
    step, prompt = value["steps"][-1], _prompt(value)
    choice_ready = step["status"] == "unsent" or (step.get("response", {}).get("facts", {}).get("outcome") == "round")
    c.state["concubine_phase"] = "heart_choice_pending" if choice_ready and prompt else "heart_choice_reply_pending" if step["round"] else "heart_pending"
    c.state["concubine_heart_msg_id"] = value["steps"][0]["msg_id"]
    c.state["concubine_heart_prompt_msg_id"] = prompt["msg_id"] if prompt else 0
    c.state["concubine_heart_round"] = prompt["facts"]["round"] if prompt else 0
    c.state["concubine_heart_choice_prompt_msg_id"] = step["reply_to_msg_id"] if step["round"] else 0
    c.state["concubine_heart_choice_round"] = step["round"]
    c.state["concubine_heart_choice_sent_at"] = step.get("sent_at", 0) if step["round"] else 0
    c.state["concubine_heart_choice_retry_count"] = 0
    c.state["next_concubine_time"] = value["next_at"]
    value["projection"] = {key: c.state[key] for key in PROJECTION_KEYS}


def _release_phase(value, unchanged):
    expected = value["projection"]
    if not all(_same(c.state.get(key), expected[key]) for key in PROJECTION_KEYS):
        return False
    if not unchanged and not (expected["concubine_heart_msg_id"] or expected["concubine_heart_prompt_msg_id"]):
        return False
    for key in PROJECTION_KEYS:
        c.state[key] = "idle" if key == "concubine_phase" else 0
    return True


def _current(owner, session_id, op_id=None, *, probe=False):
    if not c._owns_status_query(owner):
        return None
    with c.use_identity(owner[0]):
        value = record()
    step = (value["probe"] if probe else value["steps"][-1]) if value else None
    return value if value and value["session_id"] == session_id and (op_id is None or (step and step["op_id"] == op_id)) else None


def owns_launch_dispatch(command, intent, *, account_id, chat_id, now):
    """Distinguish this caller's durable pre-send phase from an older live trial."""
    owner, value = c._status_query_owner(), record()
    if (command != c.CMD_CONCUBINE_HEART or not isinstance(intent, dict)
            or intent.get("source_module") != SOURCE or not value
            or value["status"] != "active" or len(value["steps"]) != 1 or value["probe"]
            or type(account_id) is not int or account_id != owner[2] or account_id != value["account_id"]
            or type(chat_id) is not int or chat_id != value["chat_id"]
            or intent.get("chain_id") != value["session_id"]):
        return False
    step = value["steps"][0]
    return bool(
        step["status"] == "sending" and not step["msg_id"] and step["round"] == 0
        and intent.get("op_id") == step["op_id"] == _INFLIGHT.get(owner[0])
        and all(_same(c.state.get(key), value["projection"][key]) for key in PROJECTION_KEYS)
        and _can_act(owner, value, now) and not c.external_events.needs_calibration()
        and now >= _global_start_at(owner[0])
    )


def _step(number, command, root, at):
    return {"op_id": c.uuid4().hex, "round": number, "command": command, "reply_to_msg_id": root,
            "started_at": at, "status": "sending", "msg_id": 0}


async def _dispatch(owner, value, before, *, probe=False):
    identity_id, identity, _account = owner
    if not _valid_record(value):
        identity.clear()
        identity.update(before)
        c.mark_dirty()
        return False
    step = value["probe"] if probe else value["steps"][-1]
    _INFLIGHT[identity_id] = step["op_id"]
    _store(value)

    def can_send():
        if not c._owns_status_query(owner):
            return False
        with c.use_identity(identity_id):
            at = c.time.time()
            return bool(_current(owner, value["session_id"], step["op_id"], probe=probe) == value
                        and _can_act(owner, value, at)
                        and (probe or step["round"] or not c.external_events.needs_calibration())
                        and (probe or step["round"] or at >= _global_start_at(identity_id)))

    try:
        if not c._save_query_projection(owner, before):
            return False
        previous_block = dict(c.classify_game_send_block(identity_id, step["command"]))
        try:
            msg = await c._send_concubine_game_command(
                step["command"], track=True, max_retry=0, reply_timeout=c.CONCUBINE_PHASE_TIMEOUT_SEC,
                send_as_id=identity_id, target_chat_id=value["chat_id"], source_module=SOURCE,
                op_id=step["op_id"], chain_id=value["session_id"], operation_check=can_send,
                priority="chain", **({"reply_to": step["reply_to_msg_id"]} if step["reply_to_msg_id"] else {}),
            )
        except (asyncio.CancelledError, Exception):
            current = _current(owner, value["session_id"], step["op_id"], probe=probe)
            if current:
                snapshot = copy.deepcopy(identity)
                active = current["probe"] if probe else current["steps"][-1]
                if active["status"] == "sending":
                    active["status"] = "unknown"
                _clear_pending(current)
                _store(current)
                try:
                    c._save_query_projection(owner, snapshot)
                except Exception as exc:
                    c.console_log(f"Heart recovery save failed ({type(exc).__name__})")
            raise
        current = _current(owner, value["session_id"], step["op_id"], probe=probe)
        if current is None:
            return False
        snapshot = copy.deepcopy(identity)
        active = current["probe"] if probe else current["steps"][-1]
        if active["status"] != "sending":
            _clear_pending(current)
            c._save_query_projection(owner, snapshot)
            return active["status"] in {"sent", "answered"}
        at = max(step["started_at"], c.time.time())
        unchanged = c._status_query_plan(owner) == value["plan_key"]
        block = c.classify_game_send_block(identity_id, step["command"]) if not msg else {}
        known = False
        if (not msg and block.get("status") == "unsent" and block != previous_block
                and step["started_at"] <= (c._query_time(block.get("at")) or 0) <= at + 1):
            active.update(status="unsent", retry_at=at + c.random.uniform(
                c.CONCUBINE_SEND_FAILURE_RETRY_MIN_SEC, c.CONCUBINE_SEND_FAILURE_RETRY_MAX_SEC))
            if probe:
                current["probe_after"] = active["retry_at"]
            else:
                current["next_at"] = active["retry_at"]
                if not step["round"]:
                    current["status"] = "unsent"
        else:
            root, chat = c._query_int(getattr(msg, "id", 0)), c._query_int(getattr(msg, "chat_id", 0))
            sent_at, dispatch_at = c._query_time(getattr(msg, "sent_at", 0)) or 0, c._query_time(getattr(msg, "send_started_at", 0)) or 0
            prior = current["steps"] if probe else current["steps"][:-1]
            floor = max([0] + [item["msg_id"] for item in prior]
                        + [item["response"]["msg_id"] for item in prior if "response" in item])
            known = root > max(floor, step["reply_to_msg_id"]) and chat == value["chat_id"] and step["started_at"] - 1 <= dispatch_at <= sent_at <= at + 1
            active["status"] = "sent" if known else "unknown"
            if known:
                active.update(msg_id=root, sent_at=sent_at, dispatch_at=dispatch_at)
            if not probe:
                current["next_at"] = at + c.CONCUBINE_PHASE_TIMEOUT_SEC
        if unchanged:
            if current["status"] == "unsent":
                _release_phase(current, True)
                identity["next_concubine_time"] = current["next_at"]
            else:
                _project(current)
            identity["concubine_heart_last_error"] = "" if known or active["status"] == "unsent" else "\u5fc3\u52ab\u53d1\u9001\u72b6\u6001\u672a\u77e5\uff0c\u4fdd\u7559\u539f\u64cd\u4f5c\u7b49\u5f85\u53cd\u9988"
            current["plan_key"] = c._status_query_plan(owner)
        _store(current)
        return c._save_query_projection(owner, snapshot) and known
    finally:
        if _INFLIGHT.get(identity_id) == step["op_id"]:
            _INFLIGHT.pop(identity_id, None)


async def send(now):
    owner = c._status_query_owner()
    if (c._query_time(now) is None or not _controls(owner) or owner[0] in _INFLIGHT
            or block_reason() or _other_work() or c._status_snapshot_block_reason(now)
            or next_at() > now or not c._has_available_partner() or c._has_voyage_runtime_state(now)
            or c._has_active_nanlong_pending(now) or c._has_phaseful_summary_window(now)):
        return False
    before = copy.deepcopy(owner[1])
    gap_at = _global_start_at(owner[0])
    if now < gap_at:
        owner[1]["next_concubine_time"] = gap_at
        c._save_query_projection(owner, before)
        return False
    panel = panel_anchor(now)
    if not panel:
        await c._send_status_query("status", now)
        return False
    started_at = max(float(now), c.time.time())
    value = {"session_id": c.uuid4().hex, "identity_id": owner[0], "account_id": owner[2],
             "chat_id": panel["chat_id"], "started_at": started_at, "partner": owner[1].get("concubine_name"),
             "snapshot_at": owner[1].get("concubine_last_snapshot_at", 0), "affinity": owner[1].get("concubine_affinity"),
             "due_at": owner[1].get("concubine_heart_due_at", 0), "status": "active",
             "steps": [_step(0, c.CMD_CONCUBINE_HEART, panel["msg_id"], started_at)],
             "probe": {}, "probe_count": 0, "probe_after": started_at + c.CONCUBINE_PHASE_TIMEOUT_SEC,
             "replay_after": 0, "next_at": started_at + c.CONCUBINE_PHASE_TIMEOUT_SEC, "result": {}}
    _project(value)
    value["plan_key"] = c._status_query_plan(owner)
    if not _valid_record(value):
        owner[1].clear()
        owner[1].update(before)
        return False
    return await _dispatch(owner, value, before)


async def send_choice(now):
    owner, value = c._status_query_owner(), record()
    if (c._query_time(now) is None or not value or value["status"] != "active"
            or not _controls(owner) or owner[0] in _INFLIGHT or value["account_id"] != owner[2]
            or (value["probe"] and value["probe"]["status"] in UNRESOLVED)
            or now < value["next_at"] or not _can_act(owner, value, now)):
        return False
    previous = value["steps"][-1]
    retry = previous["status"] == "unsent" and previous["round"] > 0
    facts = previous.get("response", {}).get("facts", {})
    number = previous["round"] if retry else facts.get("round") if facts.get("outcome") == "round" else 0
    if type(number) is not int or number not in {1, 2, 3}:
        return False
    prompt = value["steps"][number - 1]["response"]
    before = copy.deepcopy(owner[1])
    step = _step(number, c.CMD_CONCUBINE_HEART_STEADY, prompt["msg_id"], max(float(now), c.time.time()))
    if retry:
        value["steps"][-1] = step
    else:
        value["steps"].append(step)
    value["next_at"] = value["probe_after"] = step["started_at"] + c.CONCUBINE_PHASE_TIMEOUT_SEC
    _project(value)
    value["plan_key"] = c._status_query_plan(owner)
    return await _dispatch(owner, value, before)


def _schedule_followup(owner, value):
    token = (value["session_id"], value["steps"][-1]["op_id"])
    if _FOLLOWUPS.get(owner[0]) == token:
        return
    _FOLLOWUPS[owner[0]] = token

    async def delayed():
        try:
            await asyncio.sleep(max(0, value["next_at"] - c.time.time()))
            if not c._owns_status_query(owner):
                return
            async with c._CONCUBINE_SCHEDULER_LOCK:
                if not c._owns_status_query(owner):
                    return
                with c.use_identity(owner[0]):
                    current = _current(owner, *token)
                    if current and current["plan_key"] == value["plan_key"]:
                        await send_choice(c.time.time())
        finally:
            if _FOLLOWUPS.get(owner[0]) == token:
                _FOLLOWUPS.pop(owner[0], None)

    c._fire_and_forget(delayed())


def _adopt(value, now, *, logs=False):
    for step in value["steps"] + ([value["probe"]] if value["probe"] else []):
        if step["status"] not in UNRESOLVED or step["msg_id"]:
            continue
        adopted = c._adopt_status_query_receipt(_wire(value, step), now, source_module=SOURCE, include_logs=logs)
        if adopted["msg_id"]:
            before = copy.deepcopy(step)
            step.update({key: adopted[key] for key in ("status", "msg_id", "sent_at", "dispatch_at")})
            if not _valid_record(value):
                step.clear()
                step.update(before)
    return value


def _candidate_roots(value, step, probe=False):
    if probe or step["round"] == 0:
        return {step["msg_id"]} - {0}
    return {step["msg_id"], value["steps"][0]["msg_id"], value["steps"][step["round"] - 1]["response"]["msg_id"]} - {0}


def _apply_terminal(value, step, facts, now, unchanged, *, outcome=None):
    outcome = outcome or facts["outcome"]
    value["status"] = "complete"
    result = {"outcome": outcome, "due_at": facts.get("due_at", 0), "affinity_applied": False,
              "cooldown_applied": False, "source": step["op_id"]}
    current = bool(c._current_partner_matches(value["partner"])
                   and _same(c.state.get("concubine_last_snapshot_at"), value["snapshot_at"]))
    if current and _same(c.state.get("concubine_heart_due_at"), value["due_at"]) and outcome in {
            "settlement", "cooldown", "reconciled_cooldown", "reconciled_ready"}:
        c.state["concubine_heart_due_at"] = result["due_at"]
        result["cooldown_applied"] = True
    if current and outcome == "settlement" and _same(c.state.get("concubine_affinity"), value["affinity"]):
        affinity = value["affinity"] + facts["affinity_delta"]
        if 0 <= affinity < 2 ** 63:
            c.state["concubine_affinity"] = affinity
            result["affinity_applied"] = True
    value["result"] = result
    _release_phase(value, unchanged)
    if not unchanged:
        return
    c.state["concubine_heart_last_error"] = ""
    value["next_at"] = result["due_at"]
    if outcome == "resource_shortage":
        backoff = c.record_resource_shortage(c.CONCUBINE_HEART_RESOURCE_KEY, now, reason=facts["text"])
        value["next_at"] = backoff["next_at"]
        c.state["next_concubine_time"] = value["next_at"]
        c.state["concubine_heart_last_error"] = facts["text"]
    elif outcome == "voyage_lock":
        c._apply_voyage_snapshot(facts["voyage"], step["response"]["at"])
        c._schedule_voyage_wait(now)
    elif outcome in {"missing_panel", "reconciled_ready", "reconciled_cooldown"}:
        c.state["concubine_last_panel_msg_id"] = 0
        c.state["concubine_last_panel_chat_id"] = 0
        c.state["concubine_last_snapshot_at"] = 0
        c._schedule_status_recheck(now)
    else:
        c.reset_resource_shortage(c.CONCUBINE_HEART_RESOURCE_KEY)
        c._schedule_at_due_or_chain(now, result["due_at"])


def _close_completed_guard(owner, value, now):
    first = value["steps"][0]
    if not first["msg_id"] or not c._owns_status_query(owner):
        return
    before = copy.deepcopy(owner[1])
    try:
        if c.close_action_guard(SOURCE, send_as_id=owner[0], reason="owned_heart_complete", now=now,
                                expected_msg_id=first["msg_id"], expected_chat_id=value["chat_id"]):
            c._save_query_projection(owner, before)
    except Exception as exc:
        c.console_log(f"Heart result committed; guard cleanup will retry ({type(exc).__name__})")


async def handle_reply(text, now, reply_to, *, current_msg_id=0, current_chat_id=0, observed_at=0, reply_context=None, probe=False):
    owner, value = c._status_query_owner(), record()
    now, at = c._query_time(now), c._query_time(observed_at)
    context = reply_context if isinstance(reply_context, dict) else {}
    if (now is None or at is None or at > now or not value or value["status"] not in {"active", "reconcile"}
            or not c._owns_status_query(owner) or owner[2] != value["account_id"]
            or c._query_int(current_chat_id) != value["chat_id"]
            or c._query_int(context.get("sender_id")) not in c.get_game_bot_ids()
            or c._query_int(getattr(reply_to, "chat_id", 0)) not in (0, value["chat_id"])):
        return False
    value = _adopt(value, now)
    if not _valid_record(value):
        return False
    step = value["probe"] if probe else value["steps"][-1]
    if not step or step["status"] not in UNRESOLVED:
        return False
    root = c._query_int(getattr(reply_to, "id", 0))
    roots = _candidate_roots(value, step, probe)
    expected = {"send_as_id": value["identity_id"], "account_id": value["account_id"], "chat_id": value["chat_id"], "reply_to_msg_id": root}
    if (any(key in context and c._query_int(context[key]) != item for key, item in expected.items())
            or ("root_msg_id" in context and c._query_int(context["root_msg_id"]) not in roots | ({0} if not root else set()))
            or ("source_module" in context and context["source_module"] != SOURCE)
            or ("family" in context and context["family"] != ("concubine_status" if probe else SOURCE))
            or ("chain_id" in context and context["chain_id"] != value["session_id"])):
        return False
    commands = {item["msg_id"]: item for item in value["steps"] + ([value["probe"]] if value["probe"] else []) if item["msg_id"]}
    if "op_id" in context and context["op_id"] not in {commands[item]["op_id"] for item in roots if item in commands}:
        return False
    if root in commands:
        command = commands[root]["command"]
        sender = getattr(reply_to, "sender_id", 0)
        if (str(getattr(reply_to, "raw_text", "") or "").strip() not in ("", command)
                or (context.get("reply_to_command") and context["reply_to_command"] != command)
                or context.get("reply_to_command_edited")
                or (sender and not c.sender_matches_identity(sender, value["identity_id"]))):
            return False
    facts = _probe_facts(text, at) if probe else c.heart_contract.parse(text, at)
    if facts is None or (probe and facts["partner"] != value["partner"]) or (
            not probe and facts["outcome"] == "voyage_lock" and facts["voyage"].get("partner") not in ("", value["partner"])):
        return False
    response = {"msg_id": current_msg_id, "root_msg_id": root, "sender_id": context["sender_id"],
                "at": at, "event_type": context.get("event_type", "message"), "facts": facts}
    answered = dict(step, status="answered", response=response)
    if not _valid_step(answered, step["round"], probe=probe) or not _bound_response(value, answered, response, probe=probe):
        return False
    before = copy.deepcopy(owner[1])
    unchanged = c._status_query_plan(owner) == value["plan_key"]
    step.update(answered)
    try:
        if probe:
            previous_outcome = value["steps"][-1].get("response", {}).get("facts", {}).get("outcome")
            if facts["due_at"] > at:
                _apply_terminal(value, step, facts, now, unchanged, outcome="reconciled_cooldown")
            elif previous_outcome == "anchor_lost":
                _apply_terminal(value, step, facts, now, unchanged, outcome="reconciled_ready")
            else:
                value["probe_after"] = now + PROBE_GAP_SEC
                if unchanged:
                    c.state["concubine_heart_last_error"] = "\u5fc3\u52ab\u9762\u677f\u672a\u8bc1\u5b9e\u65e7\u94fe\u7ed3\u675f\uff0c\u4fdd\u7559\u672a\u51b3\u64cd\u4f5c"
        elif facts["outcome"] == "round":
            value["status"] = "active"
            value["next_at"] = now + c._heart_next_choice_delay()
            if unchanged:
                _project(value)
                c.state["concubine_heart_last_error"] = ""
        elif facts["outcome"] in {"anchor_lost", "in_progress"}:
            value["status"] = "reconcile"
            value["probe_after"] = max(now, facts.get("due_at", 0))
            if unchanged:
                c.state["concubine_heart_last_error"] = "\u5fc3\u52ab\u7b49\u5f85\u53ea\u8bfb\u72b6\u6001\u6821\u51c6\uff0c\u4e0d\u91cd\u53d1\u6d88\u8d39\u547d\u4ee4"
        else:
            _apply_terminal(value, step, facts, now, unchanged)
        _clear_pending(value)
        if unchanged:
            value["plan_key"] = c._status_query_plan(owner)
        if not _valid_record(value):
            owner[1].clear()
            owner[1].update(before)
            c.mark_dirty()
            return False
        _store(value)
        if not c._save_query_projection(owner, before):
            return False
    except Exception:
        owner[1].clear()
        owner[1].update(before)
        c.mark_dirty()
        raise
    if value["status"] == "complete":
        _close_completed_guard(owner, value, now)
    elif not probe and facts["outcome"] == "round" and unchanged and _controls(owner):
        _schedule_followup(owner, value)
    return True


def owns_probe(root, chat_id, now):
    value = record()
    if not value or not value["probe"] or value["account_id"] != c.get_identity_account(c.get_current_identity_id()):
        return False
    value = _adopt(value, now)
    return bool(c._query_int(root) > 0 and (c._query_int(chat_id), root) == (value["chat_id"], value["probe"]["msg_id"]))


def matches_reply(root, msg_id, chat_id, now):
    value = record()
    if not value or value["account_id"] != c.get_identity_account(c.get_current_identity_id()) or c._query_int(chat_id) != value["chat_id"]:
        return False
    value = _adopt(value, now)
    if not _valid_record(value):
        return False
    step = value["steps"][-1]
    prompt = _prompt(value)
    return bool((c._query_int(root) > 0 and root in _candidate_roots(value, step))
                or (prompt and c._query_int(msg_id) == prompt["msg_id"]))


def _logged_replies(value, now):
    roots = {step["msg_id"] for step in value["steps"]} - {0}
    prompts = {step["response"]["msg_id"] for step in value["steps"] if "response" in step}
    if value["probe"]:
        roots.add(value["probe"]["msg_id"])
    windows = []
    times = [step["started_at"] for step in value["steps"]]
    if value["probe"]:
        times.append(value["probe"]["started_at"])
    for at in sorted(set(times + [max(value["started_at"], now - c.CONCUBINE_LOG_REPLAY_LOOKBACK_SEC)])):
        start, end = max(value["started_at"] - 1, at - 1), min(now, at + c.CONCUBINE_LOG_REPLAY_LOOKBACK_SEC)
        if windows and start <= windows[-1][1]:
            windows[-1] = (windows[-1][0], max(end, windows[-1][1]))
        else:
            windows.append((start, end))
    revisions = {}
    for start, end in windows:
        for entry, _received_at in iter_message_log_entries_between(start, end):
            at = c._query_time(entry.get("server_event_at"))
            root, msg = c._query_int(entry.get("reply_to_msg_id")), c._query_int(entry.get("message_id"))
            if (at is None or not value["started_at"] - 1 <= at <= now or msg <= 0
                    or entry.get("event_type") not in {"message", "edit"} or entry.get("sender_is_bot") is not True
                    or c._query_int(entry.get("sender_id")) not in c.get_game_bot_ids()
                    or c._query_int(entry.get("chat_id")) != value["chat_id"]
                    or (root not in roots | prompts and msg not in prompts)):
                continue
            previous, conflict = revisions.get(msg, ({"server_event_at": 0}, False))
            if at > previous["server_event_at"]:
                revisions[msg] = (entry, False)
            elif at == previous["server_event_at"]:
                revisions[msg] = (previous, conflict or entry.get("text") != previous.get("text"))
    return sorted((entry for entry, conflict in revisions.values() if not conflict),
                  key=lambda item: (item["server_event_at"], item["message_id"]), reverse=True)


async def _send_probe(owner, value, now):
    if (value["probe_count"] >= PROBE_LIMIT or now < value["probe_after"] or not _can_act(owner, value, now)
            or (value["probe"] and value["probe"]["status"] in UNRESOLVED)):
        return False
    before = copy.deepcopy(owner[1])
    value["status"] = "reconcile"
    value["probe"] = _step(0, c.CMD_CONCUBINE_STATUS, 0, max(now, c.time.time()))
    value["probe_count"] += 1
    value["probe_after"] = now + PROBE_GAP_SEC
    return await _dispatch(owner, value, before, probe=True)


async def recover(now, *, allow_send=True):
    owner, value = c._status_query_owner(), record()
    if not owner or value is None or c._query_time(now) is None:
        return True
    if not value:
        return bool(block_reason())
    if value["account_id"] != owner[2]:
        return True
    if value["status"] == "complete":
        before = copy.deepcopy(owner[1])
        _clear_pending(value)
        if owner[1] != before:
            c._save_query_projection(owner, before)
        _close_completed_guard(owner, value, now)
        return bool(block_reason())
    if value["status"] == "unsent":
        return bool(block_reason())
    if owner[0] in _INFLIGHT:
        return True
    if now >= value["replay_after"]:
        before = copy.deepcopy(owner[1])
        value = _adopt(value, now, logs=True)
        if not _valid_record(value):
            return True
        value["replay_after"] = now + c.CONCUBINE_QUERY_REPLAY_SEC
        _store(value)
        if not c._save_query_projection(owner, before):
            return True
        entries = _logged_replies(value, now)
        for probe in (False, True):
            for entry in entries:
                current = _current(owner, value["session_id"])
                if not current or current["status"] == "complete":
                    return True
                if await handle_reply(
                    entry.get("text", ""), now,
                    c.SimpleNamespace(id=c._query_int(entry.get("reply_to_msg_id")), chat_id=value["chat_id"], raw_text=""),
                    current_msg_id=entry["message_id"], current_chat_id=value["chat_id"], observed_at=entry["server_event_at"],
                    reply_context={"sender_id": entry["sender_id"], "event_type": entry["event_type"]}, probe=probe,
                ):
                    return True
    value = _current(owner, value["session_id"])
    if not value:
        return True
    probe = value["probe"]
    if probe and probe["status"] in UNRESOLVED and now >= probe["started_at"] + c.CONCUBINE_PHASE_TIMEOUT_SEC:
        before = copy.deepcopy(owner[1])
        probe["status"] = "expired"
        value["probe_after"] = now + PROBE_GAP_SEC
        _clear_pending(value)
        _store(value)
        c._save_query_projection(owner, before)
        return True
    if not allow_send or not _can_act(owner, value, now):
        return True
    last = value["steps"][-1]
    if value["status"] == "active" and (last["status"] == "unsent" or last.get("response", {}).get("facts", {}).get("outcome") == "round"):
        await send_choice(now)
    elif now >= value["probe_after"]:
        await _send_probe(owner, value, now)
    return True
