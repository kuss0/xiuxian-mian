"""Owned spouse acquisition; an uncertain request cannot become another purchase."""

import asyncio
import copy
import re

from . import concubine as c


STATE_KEY = "concubine_reacquire_action"
SOURCE = "concubine_reacquire"
UNRESOLVED = {"sending", "sent", "unknown"}
_INFLIGHT = {}
RE_SECT = re.compile(r"\u65b0\u7684\u9053\u5fc3\u4f8d\u59be\s*\u3010(?P<name>[^\r\n\u3011]{1,120})\u3011\s*\u5df2\u88ab\u6307\u6d3e")
RE_ROMANCE = re.compile(r"\u540d\u4e3a\s*\u3010(?P<name>[^\r\n\u3011]{1,120})\u3011\s*\u7684\u5973\u5b50[\s\S]{0,2048}?\u6210\u4e3a\u4f60\u7684\u4f8d\u59be")
RE_WAIT = re.compile(r"\u8bf7\u5728\s*(?P<wait>[^\r\n]+?)\s*\u540e\u518d\u8bd5")
PROGRESS = "\u5f00\u542f\u4e86\u4e00\u6bb5\u5bfb\u7f18\u4e4b\u65c5"


def _zero_time(value):
    return type(value) in {int, float} and (value == 0 or c._query_time(value) is not None)


def _token(value, length):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{" + str(length) + "}", value) is not None


def _name(value, *, empty=False):
    return bool(isinstance(value, str) and (empty or value) and len(value) <= 120
                and value == value.strip() and all(ord(char) >= 32 for char in value))


def _partner_key():
    values = {key: c.state.get(key) for key in c.CONCUBINE_PARTNER_SNAPSHOT_KEYS}
    values = {key: float(value) if type(value) in {int, float} else value for key, value in values.items()}
    try:
        raw = c.json.dumps(values, sort_keys=True, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError, OverflowError):
        return ""
    return c.hashlib.sha256(raw.encode()).hexdigest()


def _parse(value, text, at):
    if not isinstance(text, str) or len(text) > 4096 or c._query_time(at) is None:
        return None
    text = re.sub(r"[*`]+", "", text).strip().replace("\r\n", "\n")
    if not text or c._is_phaseful_summary_text(text) or c._is_heavenly_ban_text(text):
        return None
    sect, romance = list(RE_SECT.finditer(text)), list(RE_ROMANCE.finditer(text))
    voyage = c._parse_voyage_rejection(text, at)
    flags = {
        "sect": "\u65b0\u7684\u9053\u5fc3\u4f8d\u59be" in text,
        "romance": "\u6210\u4e3a\u4f60\u7684\u4f8d\u59be" in text,
        "progress": PROGRESS in text,
        "exists": any(word in text for word in ("\u5df2\u6709\u9053\u4fa3", "\u5df2\u89c5\u5f97\u7ea2\u989c\u77e5\u5df1")),
        "resource_shortage": any(word in text for word in ("\u8d21\u732e\u4e0d\u8db3", "\u7075\u77f3\u4e0d\u8db3", "\u4fee\u4e3a\u4e0d\u8db3", "\u8d44\u6e90\u4e0d\u8db3")),
        "not_eligible": c._is_partner_not_eligible_text(text),
        "not_found": any(word in text for word in ("\u8e0f\u904d\u4e07\u5343\u7ea2\u5c18", "\u672a\u80fd\u5bfb\u5f97\u6709\u7f18\u4e4b\u4eba")),
        "cooldown": "\u795e\u5ff5\u6d88\u8017\u8fc7\u5267" in text,
        "use_sect": any(word in text for word in ("\u4f60\u4e43\u661f\u5bab\u5f1f\u5b50", "\u5b97\u95e8\u81ea\u6709\u9053\u5fc3\u4f8d\u59be")),
        "use_romance": any(word in text for word in ("\u5e76\u975e\u661f\u5bab\u5f1f\u5b50", "\u82e5\u4e3a\u6563\u4fee\u6216\u5176\u4ed6\u5b97\u95e8")),
        "voyage_lock": bool(voyage),
    }
    if sum(flags.values()) != 1:
        return None
    outcome = next(key for key, present in flags.items() if present)
    result = {"outcome": outcome, "partner": "", "kind": "", "next_command": "",
              "due_at": 0, "retry_at": 0, "applied": False, "text": text}
    if outcome in {"sect", "romance"}:
        matches = sect if outcome == "sect" else romance
        marker = "\u65b0\u7684\u9053\u5fc3\u4f8d\u59be" if outcome == "sect" else "\u6210\u4e3a\u4f60\u7684\u4f8d\u59be"
        command = c.CMD_CONCUBINE_SECT_MARRY if outcome == "sect" else c.CMD_CONCUBINE_ROMANCE
        if len(matches) != 1 or text.count(marker) != 1 or value["command"] != command or not _name(matches[0]["name"]):
            return None
        return dict(result, outcome="acquired", partner=matches[0]["name"],
                    kind="\u9053\u5fc3\u4f8d\u59be" if outcome == "sect" else "\u7ea2\u5c18\u9053\u4fa3")
    if outcome == "progress":
        return result if value["command"] == c.CMD_CONCUBINE_ROMANCE and text.count(PROGRESS) == 1 else None
    if outcome == "cooldown":
        waits = list(RE_WAIT.finditer(text))
        due = c._parse_wait_due_at(waits[0]["wait"], at) if len(waits) == 1 else None
        return dict(result, due_at=due, retry_at=due) if due and due > at else None
    if outcome in {"use_sect", "use_romance"}:
        command = c.CMD_CONCUBINE_SECT_MARRY if outcome == "use_sect" else c.CMD_CONCUBINE_ROMANCE
        allowed = command != value["command"] and value["redirects"] == 0
        return dict(result, outcome="redirect", next_command=command if allowed else "",
                    retry_at=at + (c.CONCUBINE_ACTIVE_DEFER_MIN_SEC if allowed else c.CONCUBINE_REACQUIRE_RETRY_SEC))
    if outcome in {"resource_shortage", "not_eligible", "not_found"}:
        if outcome == "not_found" and value["command"] != c.CMD_CONCUBINE_ROMANCE:
            return None
        return dict(result, retry_at=at + c.CONCUBINE_REACQUIRE_RETRY_SEC)
    if outcome == "voyage_lock":
        if voyage.get("status") != "sailing" or not _zero_time(voyage.get("return_at")) or not _name(voyage.get("partner", ""), empty=True):
            return None
    return result


def _valid_reply(value, reply, *, intermediate=False):
    if (not isinstance(reply, dict) or reply.keys() != {"msg_id", "sender_id", "at", "event_type", "text"}
            or not value["msg_id"] or c._query_int(reply["msg_id"]) <= value["msg_id"]
            or c._query_int(reply["sender_id"]) <= 0 or c._query_time(reply["at"]) is None
            or reply["at"] < value["dispatch_at"] - 1 or reply["event_type"] not in ("message", "edit")):
        return False
    facts = _parse(value, reply["text"], reply["at"])
    if facts is None or (facts["outcome"] == "progress") is not intermediate:
        return False
    ack = value["ack"]
    if not intermediate and ack:
        if reply["sender_id"] != ack["sender_id"]:
            return False
        if reply["msg_id"] == ack["msg_id"]:
            return reply["event_type"] == "edit" and reply["at"] > ack["at"]
        return reply["msg_id"] > ack["msg_id"] and reply["at"] >= ack["at"]
    return True


def _valid_record(value):
    required = {"op_id", "kind", "identity_id", "account_id", "chat_id", "command", "started_at", "status",
                "msg_id", "plan_key", "partner_key", "snapshot_at", "attempts", "redirects", "ack"}
    optional = {"sent_at", "dispatch_at", "replay_after", "retry_at", "reply", "result"}
    if not isinstance(value, dict) or required - value.keys() or value.keys() - required - optional:
        return False
    if (value["kind"] != "reacquire" or not isinstance(value["command"], str)
            or value["command"] not in c.CONCUBINE_REACQUIRE_COMMANDS
            or not _token(value["op_id"], 32) or not _token(value["plan_key"], 64) or not _token(value["partner_key"], 64)
            or c._query_int(value["identity_id"]) != c.get_current_identity_id()
            or c._query_int(value["account_id"]) <= 0 or not c._query_int(value["chat_id"])
            or c._query_time(value["started_at"]) is None or c._query_time(value["snapshot_at"]) is None
            or value["snapshot_at"] > value["started_at"]
            or type(value["attempts"]) is not int or not 0 <= value["attempts"] < 2 ** 63 - 1
            or type(value["redirects"]) is not int or value["redirects"] not in (0, 1)
            or type(value["msg_id"]) is not int or not 0 <= value["msg_id"] < 2 ** 63
            or not isinstance(value["status"], str) or value["status"] not in UNRESOLVED | {"unsent", "complete"}
            or (value["status"] in {"sent", "complete"} and not value["msg_id"])
            or (value["status"] in {"sending", "unsent"} and value["msg_id"])
            or any(c._query_time(value[key]) is None for key in ("sent_at", "dispatch_at", "replay_after", "retry_at") if key in value)
            or (value["status"] == "unsent" and value.get("retry_at", 0) <= value["started_at"])
            or ("retry_at" in value and value["status"] != "unsent")
            or (bool(value["msg_id"]) != ("sent_at" in value and "dispatch_at" in value))
            or (not value["msg_id"] and bool({"sent_at", "dispatch_at"} & value.keys()))
            or (value["msg_id"] and not value["started_at"] - 1 <= value["dispatch_at"] <= value["sent_at"])
            or not isinstance(value["ack"], dict)):
        return False
    if value["ack"] and not _valid_reply(value, value["ack"], intermediate=True):
        return False
    if value["status"] != "complete":
        return not ({"result", "reply"} & value.keys())
    result = value.get("result")
    if (not _valid_reply(value, value.get("reply")) or not isinstance(result, dict)
            or result.keys() != {"outcome", "partner", "kind", "next_command", "due_at", "retry_at", "applied", "text"}
            or type(result["applied"]) is not bool or not _zero_time(result["due_at"]) or not _zero_time(result["retry_at"])
            or not _name(result["partner"], empty=True) or not _name(result["kind"], empty=True)):
        return False
    parsed = _parse(value, value["reply"]["text"], value["reply"]["at"])
    return bool(dict(parsed, applied=result["applied"]) == result
                and (not result["applied"] or result["outcome"] in {"acquired", "exists", "voyage_lock"}))


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
    if value and value["status"] in UNRESOLVED:
        return "pending"
    if c._phase() == "reacquire_pending" or c.state.get("concubine_reacquire_msg_id"):
        return "legacy_pending"
    guards = c.state.get("action_guard_sessions")
    guard = guards.get(SOURCE) if isinstance(guards, dict) else None
    return "legacy_pending" if not value and isinstance(guard, dict) and not guard.get("closed_at") else ""


def next_at():
    value, blocked = record(), c.state.get("concubine_reacquire_blocked_until", 0)
    if value is None or block_reason() or not _zero_time(blocked):
        return float("inf")
    return max(blocked, (value or {}).get("retry_at", 0), (value or {}).get("result", {}).get("retry_at", 0))


def _clear_pending(value):
    c._clear_status_query_pending(value, source_module=SOURCE, family=SOURCE)


def _controls(owner):
    return bool(c._owns_status_query(owner) and owner[2] > 0 and c.get_global_enabled()
                and c.get_identity_enabled(owner[0]) and c.get_game_group_id()
                and not c.external_events.needs_calibration()
                and owner[1].get("concubine_enabled") and owner[1].get("concubine_auto_reacquire"))


def _other_work(owned=None):
    query = c._status_query_record()
    pending = c.state.get("pending_tasks")
    if (query is None or (query and query["status"] in c.CONCUBINE_QUERY_UNRESOLVED)
            or c.affinity_actions.block_reason() or c.fragment_actions.block_reason()
            or c.voyage_actions.block_reason() or c.divination_actions.block_reason() or c.heart_actions.block_reason()
            or not isinstance(pending, dict)):
        return True
    for key, item in pending.items():
        if not isinstance(item, dict):
            return True
        if (owned and owned["msg_id"] and c._status_query_pending_matches(owned, item, source_module=SOURCE)
                and c._status_query_pending_ref(key, item) == (owned["chat_id"], owned["msg_id"])):
            continue
        words = c.get_pending_command(item).split()
        if str(item.get("family") or "").startswith("concubine_") or (words and words[0] in c.CONCUBINE_PENDING_COMMANDS):
            return True
    return False


async def send(now):
    owner, previous = c._status_query_owner(), record()
    scheduled = c.state.get("next_concubine_time", 0)
    if (c._query_time(now) is None or not _controls(owner) or owner[0] in _INFLIGHT
            or not _zero_time(scheduled) or now < scheduled
            or block_reason() or _other_work() or next_at() > now or c._status_snapshot_block_reason(now)
            or c._is_permanent_moon_partner() or c._has_available_partner()
            or c._has_active_nanlong_pending(now) or c._has_voyage_runtime_state(now)):
        return False
    identity_id, identity, account_id = owner
    before = copy.deepcopy(identity)
    if c._defer_active_for_phaseful_summary(now, "\u4f8d\u59be\u8865\u9886"):
        c._save_query_projection(owner, before)
        return False
    snapshot_at = c._query_time(identity.get("concubine_last_snapshot_at")) or 0
    needs_new_snapshot = bool(previous and previous.get("result", {}).get("outcome") in {"acquired", "exists", "voyage_lock"}
                              and snapshot_at <= previous["reply"]["at"])
    if (identity.get("concubine_availability") != "no_partner" or identity.get("concubine_name") != ""
            or not now - c.CONCUBINE_PANEL_REUSE_MAX_AGE_SEC <= snapshot_at <= now or needs_new_snapshot):
        await c._send_status_query("status", now)
        return False
    command, partner_key = c._get_reacquire_command(), _partner_key()
    # A read refresh or definitely-unsent retry is not a new acquisition cycle.
    redirects = 0
    if previous:
        if previous["status"] == "unsent" and previous["command"] == command:
            redirects = previous["redirects"]
        elif previous.get("result", {}).get("next_command") == command:
            redirects = 1
    started_at = max(float(now), c.time.time())
    value = {"op_id": c.uuid4().hex, "kind": "reacquire", "identity_id": identity_id, "account_id": account_id,
             "chat_id": c.get_game_group_id(), "command": command, "started_at": started_at,
             "status": "sending", "msg_id": 0, "plan_key": c._status_query_plan(owner), "partner_key": partner_key,
             "snapshot_at": snapshot_at, "attempts": identity.get("concubine_reacquire_attempts", 0),
             "redirects": redirects, "ack": {}}
    if not _valid_record(value):
        return False
    c._set_phase("reacquire_pending")
    identity["concubine_reacquire_msg_id"] = 0
    identity["next_concubine_time"] = started_at + c.CONCUBINE_PHASE_TIMEOUT_SEC
    value["plan_key"] = c._status_query_plan(owner)
    _store(value)
    _INFLIGHT[identity_id] = value["op_id"]

    def current():
        if not c._owns_status_query(owner):
            return None
        with c.use_identity(identity_id):
            item = record()
        return item if item and item["op_id"] == value["op_id"] else None

    def can_send():
        if not _controls(owner):
            return False
        with c.use_identity(identity_id):
            at = c.time.time()
            return bool(current() == value and c._status_query_plan(owner) == value["plan_key"]
                        and not _other_work() and not c._is_permanent_moon_partner()
                        and not c._has_active_nanlong_pending(at) and not c._has_phaseful_summary_window(at))

    try:
        if not c._save_query_projection(owner, before):
            return False
        old_block = dict(c.classify_game_send_block(identity_id, command))
        try:
            msg = await c._send_concubine_game_command(
                command, track=True, max_retry=0, reply_timeout=c.CONCUBINE_PHASE_TIMEOUT_SEC,
                send_as_id=identity_id, target_chat_id=value["chat_id"], source_module=SOURCE,
                op_id=value["op_id"], operation_check=can_send,
            )
        except (asyncio.CancelledError, Exception):
            item = current()
            if item:
                snapshot = copy.deepcopy(identity)
                if item["status"] == "sending":
                    _store(dict(item, status="unknown"))
                elif item["status"] == "complete":
                    _clear_pending(item)
                try:
                    c._save_query_projection(owner, snapshot)
                except Exception as exc:
                    c.console_log(f"Reacquire recovery save failed ({type(exc).__name__})")
            raise
        item = current()
        if item is None:
            return False
        snapshot = copy.deepcopy(identity)
        if item["status"] != "sending":
            if item["status"] == "complete":
                _clear_pending(item)
                c._save_query_projection(owner, snapshot)
            return item["status"] in {"sent", "complete"}
        at = max(started_at, c.time.time())
        block = c.classify_game_send_block(identity_id, command) if not msg else {}
        unchanged = c._status_query_plan(owner) == value["plan_key"]
        if (not msg and block.get("status") == "unsent" and block != old_block
                and started_at <= (c._query_time(block.get("at")) or 0) <= at + 1):
            retry = at + c.random.uniform(c.CONCUBINE_SEND_FAILURE_RETRY_MIN_SEC, c.CONCUBINE_SEND_FAILURE_RETRY_MAX_SEC)
            _store(dict(value, status="unsent", retry_at=retry))
            c._release_owned_concubine_phase(value, "concubine_reacquire_msg_id", unchanged=unchanged)
            if unchanged:
                identity["next_concubine_time"] = retry
                identity["concubine_last_error"] = ""
            c._save_query_projection(owner, snapshot)
            return False
        root, chat = c._query_int(getattr(msg, "id", 0)), c._query_int(getattr(msg, "chat_id", 0))
        sent = c._query_time(getattr(msg, "sent_at", 0)) or 0
        dispatch = c._query_time(getattr(msg, "send_started_at", 0)) or 0
        known = root > 0 and chat == value["chat_id"] and started_at - 1 <= dispatch <= sent <= at + 1
        item = dict(value, status="sent" if known else "unknown")
        if known:
            item.update(msg_id=root, sent_at=sent, dispatch_at=dispatch)
        if unchanged:
            if known:
                identity["concubine_reacquire_msg_id"] = root
                identity["concubine_reacquire_attempts"] = value["attempts"] + 1
            identity["concubine_last_error"] = "" if known else "\u8865\u9886\u53d1\u9001\u72b6\u6001\u672a\u77e5\uff0c\u4fdd\u7559\u539f\u64cd\u4f5c\u7b49\u5f85\u53cd\u9988"
            item["plan_key"] = c._status_query_plan(owner)
        _store(item)
        return c._save_query_projection(owner, snapshot) and known
    finally:
        if _INFLIGHT.get(identity_id) == value["op_id"]:
            _INFLIGHT.pop(identity_id, None)


def _apply(value, now, unchanged):
    result = value["result"]
    current = _partner_key() == value["partner_key"] and not c._is_permanent_moon_partner()
    if current and result["outcome"] in {"acquired", "exists", "voyage_lock"}:
        if result["outcome"] == "acquired":
            c.state["concubine_name"] = result["partner"]
            c.state["concubine_kind"] = result["kind"]
            c.state["concubine_location"] = "\u5f85\u786e\u8ba4"
        c.state["concubine_availability"] = "unknown"
        c.state["concubine_last_snapshot_at"] = 0
        c.state["concubine_last_panel_msg_id"] = 0
        c.state["concubine_last_panel_chat_id"] = 0
        result["applied"] = True
    if not unchanged:
        return
    c.state["concubine_last_error"] = ""
    if result["outcome"] in {"acquired", "exists", "voyage_lock"}:
        c.state["concubine_reacquire_blocked_until"] = 0
        c.state["concubine_reacquire_command_override"] = ""
        c._schedule_chain_action(now)
    else:
        c.state["concubine_reacquire_blocked_until"] = result["retry_at"]
        c.state["next_concubine_time"] = max(now, result["retry_at"])
        c.state["concubine_last_error"] = result["text"]
        if result["outcome"] == "redirect" and result["next_command"]:
            c.state["concubine_reacquire_command_override"] = result["next_command"]


def _release_phase(value, unchanged):
    # A reply can beat its send return while controls are paused. Match the
    # entire initial wait before treating its still-zero scalar as owned.
    initial_wait = bool(
        type(c.state.get("concubine_reacquire_msg_id")) is int and c.state["concubine_reacquire_msg_id"] == 0
        and c.state.get("next_concubine_time") == value["started_at"] + c.CONCUBINE_PHASE_TIMEOUT_SEC
        and type(c.state.get("concubine_reacquire_attempts")) is int
        and c.state["concubine_reacquire_attempts"] == value["attempts"]
        and _partner_key() == value["partner_key"] and not _other_work(value)
    )
    return c._release_owned_concubine_phase(value, "concubine_reacquire_msg_id", unchanged=unchanged or initial_wait)


def _close_guard(owner, value, now):
    before = copy.deepcopy(owner[1])
    try:
        if c.close_action_guard(SOURCE, send_as_id=owner[0], reason="owned_reacquire_complete", now=now,
                                expected_msg_id=value["msg_id"], expected_chat_id=value["chat_id"]):
            c._save_query_projection(owner, before)
    except Exception as exc:
        c.console_log(f"Reacquire result committed; guard cleanup will retry ({type(exc).__name__})")


async def handle_reply(text, now, reply_to, *, current_msg_id=0, current_chat_id=0, observed_at=0, reply_context=None):
    owner, value = c._status_query_owner(), record()
    now, at = c._query_time(now), c._query_time(observed_at)
    context = reply_context if isinstance(reply_context, dict) else {}
    root = c._query_int(getattr(reply_to, "id", 0))
    if (now is None or at is None or at > now or not c._owns_status_query(owner)
            or not value or value["status"] not in UNRESOLVED or owner[2] != value["account_id"]
            or c._query_int(context.get("sender_id")) not in c.get_game_bot_ids()
            or c._query_int(current_chat_id) != value["chat_id"]
            or c._query_int(getattr(reply_to, "chat_id", 0)) not in (0, value["chat_id"])
            or str(getattr(reply_to, "raw_text", "") or "").strip() not in ("", value["command"])):
        return False
    expected = {"send_as_id": value["identity_id"], "account_id": value["account_id"], "chat_id": value["chat_id"],
                "root_msg_id": root, "reply_to_msg_id": root}
    if (any(key in context and c._query_int(context[key]) != item for key, item in expected.items())
            or any(key in context and context[key] != item for key, item in {
                "op_id": value["op_id"], "source_module": SOURCE, "family": SOURCE,
            }.items())
            or (getattr(reply_to, "sender_id", 0) and not c.sender_matches_identity(reply_to.sender_id, value["identity_id"]))
            or context.get("reply_to_command_edited")
            or (context.get("reply_to_command") and context["reply_to_command"] != value["command"])):
        return False
    value = c._adopt_status_query_receipt(value, now, source_module=SOURCE, include_logs=False)
    if not _valid_record(value) or root <= 0 or root != value["msg_id"]:
        return False
    facts = _parse(value, text, at)
    if facts is None:
        return False
    progress = facts["outcome"] == "progress"
    receipt = {"msg_id": current_msg_id, "sender_id": context["sender_id"], "at": at,
               "event_type": context.get("event_type", "message"), "text": facts["text"]}
    if not _valid_reply(value, receipt, intermediate=progress) or (progress and value["ack"]):
        return False
    before = copy.deepcopy(owner[1])
    unchanged = c._status_query_plan(owner) == value["plan_key"]
    try:
        if progress:
            value["ack"] = receipt
            if unchanged:
                c.state["concubine_reacquire_msg_id"] = value["msg_id"]
                c.state["concubine_reacquire_attempts"] = value["attempts"] + 1
        else:
            value.update(status="complete", reply=receipt, result=facts)
            _release_phase(value, unchanged)
            _apply(value, now, unchanged)
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
    if not progress:
        _close_guard(owner, value, now)
        if c._owns_status_query(owner):
            summary = (f"\u5df2\u8865\u9886\u4f8d\u59be\u3010{facts['partner']}\u3011\uff0c\u7b49\u5f85\u72b6\u6001\u6821\u51c6"
                       if facts["outcome"] == "acquired" else f"\u4f8d\u59be\u8865\u9886\u53cd\u9988\uff1a{facts['text'][:140]}")
            try:
                await c.send_audit_log(summary, scope="identity", limit=220)
            except Exception as exc:
                c.console_log(f"Reacquire result committed; notification failed ({type(exc).__name__})")
    return True


async def recover(now):
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
        _close_guard(owner, value, now)
        return bool(block_reason())
    if value["status"] not in UNRESOLVED:
        return bool(block_reason())
    if owner[0] in _INFLIGHT or now < value.get("replay_after", value["started_at"]):
        return True
    before = copy.deepcopy(owner[1])
    value = c._adopt_status_query_receipt(value, now, source_module=SOURCE)
    if not _valid_record(value):
        return True
    value["replay_after"] = now + c.CONCUBINE_QUERY_REPLAY_SEC
    _store(value)
    if not c._save_query_projection(owner, before):
        return True
    for entry in c._find_owned_concubine_replies(value, now):
        if await handle_reply(
            entry.get("text", ""), now,
            c.SimpleNamespace(id=value["msg_id"], chat_id=value["chat_id"], raw_text=value["command"]),
            current_msg_id=entry["message_id"], current_chat_id=value["chat_id"], observed_at=entry["server_event_at"],
            reply_context={"sender_id": entry["sender_id"], "event_type": entry["event_type"]},
        ):
            break
    return True
