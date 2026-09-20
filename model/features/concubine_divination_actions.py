"""Owned divination effects; unknown spending never expires into retry."""

import asyncio
import copy
import re

from . import concubine as c
from . import tianjige_transport


STATE_KEY = "concubine_tianji_action"
SOURCE = "concubine_tianji"
UNRESOLVED = {"sending", "sent", "unknown"}
_INFLIGHT = {}
HTTP_RECONCILE_INTERVAL_SEC = 1800
HEADER = "\u3010\u5929\u673a\u4ee3\u535c\u94fe\u3011"
RE_PARTNER = re.compile(r"^\u4f8d\u59be\u3010(?P<name>[^\r\n\u3011]+)\u3011\u711a\u9999\u63a8\u6f14[\uff0c,]\u4e3a\u4f60\u63a5\u5f15\u4e00\u7f15\u5929\u673a[\u3002.!]?$", re.MULTILINE)
RE_EFFECT = re.compile(r"^\u5f97\u5366\u3010(?P<name>[^\r\n\u3011]+)\u3011[\uff1a:][^\r\n]+$", re.MULTILINE)
RE_COST = re.compile(r"^\u672c\u6b21\u6d88\u8017[\uff1a:]\s*(?P<amount>[0-9]+|[1-9][0-9]{0,2}(?:,[0-9]{3})+)\s*\u4fee\u4e3a[\u3002.!]?$", re.MULTILINE)
RE_CD = re.compile(r"\u5929\u673a\u94fe\u8def\u5c1a\u672a\u91cd\u94f8[\uff0c,]\s*\u8bf7\u5728\s*(?P<wait>[^\r\n]+?)\s*\u540e\u518d\u8bd5[\u3002.!\uff01]?")


def _zero_time(value):
    return type(value) in {int, float} and (value == 0 or c._query_time(value) is not None)


def _name(value, *, empty=False):
    return bool(isinstance(value, str) and value == value.strip() and (empty or value)
                and len(value) <= 120 and all(ord(char) >= 32 for char in value))


def _valid_effect(value):
    return bool(isinstance(value, dict) and value.keys() == {"due_at", "chain", "chain_due_at"}
                and _zero_time(value["due_at"]) and _zero_time(value["chain_due_at"]) and _name(value["chain"], empty=True))


def _effect():
    value = {key: c.state.get("concubine_tianji_" + key, "" if key == "chain" else 0)
             for key in ("due_at", "chain", "chain_due_at")}
    return value if _valid_effect(value) else None


def _parse_result(record, text, at):
    text = re.sub(r"[*`]+", "", str(text or "")).strip()
    if not text or len(text) > 4096 or c._is_phaseful_summary_text(text) or c._is_heavenly_ban_text(text):
        return None
    cooldown = RE_CD.fullmatch(text)
    voyage = c._parse_voyage_rejection(text, at)
    shortage = c._is_tianji_resource_shortage_text(text)
    affinity = any(token in text for token in ("\u60c5\u7f18\u672a\u81f3", "\u60c5\u7f18\u672a\u6df1", "\u65e0\u6cd5\u4e3a\u4f60\u535c\u7b97\u5929\u673a"))
    no_partner = c._is_no_partner_text(text)
    if "\u5929\u673a\u94fe\u8def\u5c1a\u672a\u91cd\u94f8" in text and not cooldown:
        return None
    if sum((text.count(HEADER), bool(cooldown), bool(voyage), shortage, affinity, no_partner)) != 1:
        return None
    if voyage and voyage.get("partner") not in {"", record["partner"]}:
        return None
    result = {"outcome": "", "chain": "", "due_at": 0, "cost": 0, "voyage_wait_until": 0,
              "applied": False, "text": text}
    if HEADER in text:
        partners, effects, costs = list(RE_PARTNER.finditer(text)), list(RE_EFFECT.finditer(text)), list(RE_COST.finditer(text))
        if (len(partners) != 1 or len(effects) != 1 or len(costs) != 1 or text.splitlines()[0] != HEADER
                or text.count("\u672c\u6b21\u6d88\u8017") != 1 or text.count("\u5f97\u5366\u3010") != 1
                or partners[0]["name"] != record["partner"] or not _name(effects[0]["name"])):
            return None
        cost = c._parse_count(costs[0]["amount"])
        due_at = c._query_time(at + c.CONCUBINE_TIANJI_CD_SEC + c.CD_BUFFER_SEC)
        if not 0 < cost < 2 ** 63 or due_at is None:
            return None
        return dict(result, outcome="success", chain=effects[0]["name"], due_at=due_at, cost=cost)
    if cooldown:
        due_at = c._parse_wait_due_at(cooldown["wait"], at)
        return dict(result, outcome="cooldown", due_at=due_at) if due_at and due_at > at else None
    if voyage and voyage.get("status") == "sailing" and _zero_time(voyage.get("return_at")):
        return dict(result, outcome="voyage_lock", voyage_wait_until=voyage["return_at"])
    if shortage:
        return dict(result, outcome="resource_shortage")
    if affinity:
        return dict(result, outcome="affinity_shortage")
    return dict(result, outcome="no_partner") if no_partner else None


def _valid_record(value):
    required = {"op_id", "kind", "identity_id", "account_id", "chat_id", "command", "started_at", "status",
                "msg_id", "plan_key", "partner", "snapshot_at", "affinity", "effect"}
    optional = {"sent_at", "dispatch_at", "reply_at", "reply_msg_id", "replay_after", "retry_at", "result",
                "transport", "http_receipt", "reconciliation"}
    if not isinstance(value, dict) or required - value.keys() or value.keys() - required - optional:
        return False
    http = value.get("transport") == "miniapp"
    if "transport" in value and not http:
        return False
    if http:
        if (value["msg_id"] != 0 or value.get("reply_msg_id", 0) != 0
                or type(value.get("reply_msg_id", 0)) is not int
                or "sent_at" in value or "dispatch_at" in value or value["status"] == "sent"):
            return False
        if value["status"] == "complete":
            if not tianjige_transport.receipt_matches(
                value.get("http_receipt"), identity_id=value["identity_id"], account_id=value["account_id"],
                op_id=value["op_id"], command=value["command"], started_at=value["started_at"],
            ) or value.get("reply_at") != value["http_receipt"]["started_at"]:
                return False
        elif "http_receipt" in value:
            return False
    elif "http_receipt" in value:
        return False
    if (value["kind"] != "tianji" or value["command"] != c.CMD_CONCUBINE_TIANJI
            or not _name(value["partner"]) or not _valid_effect(value["effect"])
            or c._query_int(value["identity_id"]) != c.get_current_identity_id()
            or c._query_int(value["account_id"]) <= 0 or not c._query_int(value["chat_id"])
            or not isinstance(value["op_id"], str) or re.fullmatch(r"[a-f0-9]{32}", value["op_id"]) is None
            or not isinstance(value["plan_key"], str) or re.fullmatch(r"[a-f0-9]{64}", value["plan_key"]) is None
            or type(value["affinity"]) is not int or not 0 <= value["affinity"] < 2 ** 63
            or not _zero_time(value["snapshot_at"]) or c._query_time(value["started_at"]) is None
            or value["snapshot_at"] > value["started_at"]
            or type(value["msg_id"]) is not int or not 0 <= value["msg_id"] < 2 ** 63
            or not isinstance(value["status"], str) or value["status"] not in UNRESOLVED | {"unsent", "complete", "reconciled"}
            or any(c._query_time(value[key]) is None for key in ("sent_at", "dispatch_at", "reply_at", "replay_after", "retry_at") if key in value)
            or (not http and value["status"] in {"sent", "complete"} and not value["msg_id"])
            or (value["status"] in {"sending", "unsent"} and value["msg_id"])
            or (value["status"] == "unsent" and value.get("retry_at", 0) <= value["started_at"])
            or (bool(value["msg_id"]) != ("sent_at" in value and "dispatch_at" in value))
            or (not value["msg_id"] and bool({"sent_at", "dispatch_at"} & value.keys()))
            or (value["msg_id"] and not value["started_at"] - 1 <= value.get("dispatch_at", 0) <= value.get("sent_at", 0))):
        return False
    if "reconciliation" in value or value["status"] == "reconciled":
        probe = value.get("reconciliation")
        if (not http or value["status"] not in UNRESOLVED | {"reconciled"}
                or not isinstance(probe, dict) or not isinstance(probe.get("op_id"), str)
                or re.fullmatch(r"[a-f0-9]{32}", probe["op_id"]) is None
                or probe["op_id"] == value["op_id"] or c._query_time(probe.get("started_at")) is None
                or probe["started_at"] <= value["started_at"]):
            return False
        if value["status"] == "reconciled":
            if (probe.keys() != {"op_id", "started_at", "receipt", "text", "effect"}
                    or not tianjige_transport.receipt_matches(
                        probe["receipt"], identity_id=value["identity_id"], account_id=value["account_id"],
                        op_id=probe["op_id"], command=c.CMD_CONCUBINE_STATUS, started_at=probe["started_at"])
                    or not isinstance(probe["text"], str) or not _valid_effect(probe["effect"])
                    or _reconciled_effect(value, probe["text"], probe["receipt"]["started_at"]) != probe["effect"]):
                return False
        elif probe.keys() != {"op_id", "started_at"}:
            return False
    if value["status"] != "complete":
        return not ({"result", "reply_at", "reply_msg_id"} & value.keys())
    result = value.get("result")
    if ((not http and c._query_int(value.get("reply_msg_id")) <= value["msg_id"])
            or c._query_time(value.get("reply_at")) is None or value["reply_at"] < value.get("dispatch_at", value["started_at"]) - 1
            or not isinstance(result, dict)
            or result.keys() != {"outcome", "chain", "due_at", "cost", "voyage_wait_until", "applied", "text"}
            or type(result["applied"]) is not bool or type(result["cost"]) is not int
            or not _zero_time(result["due_at"]) or not _zero_time(result["voyage_wait_until"])
            or not _name(result["chain"], empty=True) or not isinstance(result["text"], str)):
        return False
    parsed = _parse_result(value, result["text"], value["reply_at"])
    return bool(parsed is not None and dict(parsed, applied=result["applied"]) == result
                and (not result["applied"] or result["outcome"] in {"success", "cooldown"}))


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
    if c._phase() == "tianji_pending" or c.state.get("concubine_tianji_msg_id"):
        return "legacy_pending"
    return ""


def next_at():
    value, effect = record(), _effect()
    if value is None or effect is None or block_reason():
        return float("inf")
    return max(effect["due_at"], (value or {}).get("retry_at", 0),
               (value or {}).get("result", {}).get("due_at", 0),
               (value or {}).get("reconciliation", {}).get("effect", {}).get("due_at", 0))


def _clear_pending(value):
    if value.get("transport") == "miniapp":
        return
    c._clear_status_query_pending(value, source_module=SOURCE, family=SOURCE)


def _controls(owner):
    return bool(c._owns_status_query(owner) and owner[2] > 0 and c.get_global_enabled()
                and c.get_identity_enabled(owner[0]) and c.get_game_group_id()
                and not c.external_events.needs_calibration()
                and owner[1].get("concubine_tianji_enabled"))


def _other_work():
    query = c._status_query_record()
    pending = c.state.get("pending_tasks")
    if (query is None or (query and query["status"] in c.CONCUBINE_QUERY_UNRESOLVED)
            or c.affinity_actions.block_reason() or c.fragment_actions.block_reason() or c.voyage_actions.block_reason()
            or c.heart_actions.block_reason() or c.reacquire_actions.block_reason()
            or not isinstance(pending, dict)):
        return True
    for item in pending.values():
        if not isinstance(item, dict):
            return True
        words = c.get_pending_command(item).split()
        if str(item.get("family") or "").startswith("concubine_") or (words and words[0] in c.CONCUBINE_PENDING_COMMANDS):
            return True
    return False


async def send(now):
    owner = c._status_query_owner()
    if (c._query_time(now) is None or not _controls(owner) or owner[0] in _INFLIGHT
            or block_reason() or _effect() is None or _other_work() or c._status_snapshot_block_reason(now)
            or not _name(owner[1].get("concubine_name"))
            or type(owner[1].get("concubine_affinity")) is not int
            or not 0 <= owner[1]["concubine_affinity"] < 2 ** 63
            or not c._has_tianji_due_action(now) or c._has_voyage_runtime_state(now)
            or c._has_active_nanlong_pending(now)):
        return False
    identity_id, identity, account_id = owner
    before = copy.deepcopy(identity)
    if c._defer_active_for_phaseful_summary(now, "\u5929\u673a\u4ee3\u535c", error_key="concubine_tianji_last_error"):
        c._save_query_projection(owner, before)
        return False
    started_at = max(float(now), c.time.time())
    value = {"op_id": c.uuid4().hex, "kind": "tianji", "identity_id": identity_id, "account_id": account_id,
             "chat_id": c.get_game_group_id(), "command": c.CMD_CONCUBINE_TIANJI, "started_at": started_at,
             "status": "sending", "msg_id": 0, "plan_key": c._status_query_plan(owner),
             "partner": identity.get("concubine_name"), "snapshot_at": identity.get("concubine_last_snapshot_at", 0),
             "affinity": identity.get("concubine_affinity", 0), "effect": _effect()}
    if tianjige_transport.pavilion_available(identity_id):
        value["transport"] = "miniapp"
    if not _valid_record(value):
        return False
    c._set_phase("tianji_pending")
    identity["concubine_tianji_msg_id"] = 0
    identity["next_concubine_time"] = started_at + c.CONCUBINE_PHASE_TIMEOUT_SEC
    value["plan_key"] = c._status_query_plan(owner)
    _store(value)
    _INFLIGHT[identity_id] = value["op_id"]

    def current():
        if not c._owns_status_query(owner):
            return None
        with c.use_identity(identity_id):
            item = record()
        return item if item and all(item[key] == value[key] for key in (
            "op_id", "identity_id", "account_id", "chat_id", "command", "started_at")) else None

    def can_send():
        if not _controls(owner):
            return False
        with c.use_identity(identity_id):
            at = c.time.time()
            return bool(current() == value and c._status_query_plan(owner) == value["plan_key"]
                        and (value.get("transport") != "miniapp" or tianjige_transport.pavilion_available(identity_id))
                        and not _other_work() and not c._has_active_nanlong_pending(at)
                        and not c._has_phaseful_summary_window(at))

    try:
        if not c._save_query_projection(owner, before):
            return False
        if value.get("transport") == "miniapp":
            return await _send_miniapp(value, owner, current, can_send)
        previous_block = dict(c.classify_game_send_block(identity_id, value["command"]))
        try:
            msg = await c._send_concubine_game_command(
                value["command"], track=True, max_retry=0, reply_timeout=c.CONCUBINE_PHASE_TIMEOUT_SEC,
                send_as_id=identity_id, target_chat_id=value["chat_id"], source_module=SOURCE,
                op_id=value["op_id"], operation_check=can_send,
            )
        except (asyncio.CancelledError, Exception):
            item = current()
            if item and item["status"] in {"sending", "complete"}:
                snapshot = copy.deepcopy(identity)
                if item["status"] == "sending":
                    _store(dict(item, status="unknown"))
                else:
                    _clear_pending(item)
                try:
                    c._save_query_projection(owner, snapshot)
                except Exception as exc:
                    c.console_log(f"Divination recovery save failed ({type(exc).__name__})")
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
        block = c.classify_game_send_block(identity_id, value["command"]) if not msg else {}
        unchanged = c._status_query_plan(owner) == value["plan_key"]
        if (not msg and block.get("status") == "unsent" and block != previous_block
                and started_at <= (c._query_time(block.get("at")) or 0) <= at + 1):
            retry_at = at + c.random.uniform(c.CONCUBINE_SEND_FAILURE_RETRY_MIN_SEC, c.CONCUBINE_SEND_FAILURE_RETRY_MAX_SEC)
            _store(dict(value, status="unsent", retry_at=retry_at))
            c._release_owned_concubine_phase(value, "concubine_tianji_msg_id", unchanged=unchanged)
            if unchanged:
                identity["next_concubine_time"] = retry_at
                identity["concubine_tianji_last_error"] = ""
            c._save_query_projection(owner, snapshot)
            return False
        root, chat = c._query_int(getattr(msg, "id", 0)), c._query_int(getattr(msg, "chat_id", 0))
        sent_at = c._query_time(getattr(msg, "sent_at", 0)) or 0
        dispatch_at = c._query_time(getattr(msg, "send_started_at", 0)) or 0
        known = root > 0 and chat == value["chat_id"] and started_at - 1 <= dispatch_at <= sent_at <= at + 1
        item = dict(value, status="sent" if known else "unknown")
        if known:
            item.update(msg_id=root, sent_at=sent_at, dispatch_at=dispatch_at)
        if unchanged:
            if known:
                identity["concubine_tianji_msg_id"] = root
                identity["next_concubine_time"] = sent_at + c.CONCUBINE_PHASE_TIMEOUT_SEC
            identity["concubine_tianji_last_error"] = "" if known else "\u5929\u673a\u4ee3\u535c\u53d1\u9001\u72b6\u6001\u672a\u77e5\uff0c\u4fdd\u7559\u539f\u64cd\u4f5c\u7b49\u5f85\u53cd\u9988"
            item["plan_key"] = c._status_query_plan(owner)
        _store(item)
        return c._save_query_projection(owner, snapshot) and known
    finally:
        if _INFLIGHT.get(identity_id) == value["op_id"]:
            _INFLIGHT.pop(identity_id, None)


def _apply_result(value, now, unchanged):
    result = value["result"]
    snapshot_at = c.state.get("concubine_last_snapshot_at", 0)
    current = bool(c._current_partner_matches(value["partner"]) and _effect() == value["effect"]
                   and _zero_time(snapshot_at) and snapshot_at == value["snapshot_at"])
    if current and result["outcome"] in {"success", "cooldown"}:
        c.state["concubine_tianji_due_at"] = result["due_at"]
        if result["outcome"] == "success":
            c.state["concubine_tianji_chain"] = result["chain"]
            c.state["concubine_tianji_chain_due_at"] = result["due_at"]
        else:
            c._clear_expired_tianji_chain(value["reply_at"])
        result["applied"] = True
    if not unchanged:
        return
    c.state["concubine_tianji_last_error"] = ""
    if result["outcome"] == "resource_shortage":
        backoff = c.record_resource_shortage(c.CONCUBINE_TIANJI_RESOURCE_KEY, now, reason=result["text"])
        value["retry_at"] = backoff["next_at"]
        c.state["next_concubine_time"] = value["retry_at"]
        c.state["concubine_tianji_last_error"] = result["text"]
    elif result["outcome"] in {"affinity_shortage", "no_partner"}:
        c._set_availability("unknown")
        c.state["concubine_last_snapshot_at"] = 0
        c.state["concubine_tianji_last_error"] = result["text"]
        c._schedule_status_recheck(now)
    elif result["outcome"] == "voyage_lock":
        c._apply_voyage_snapshot({"status": "sailing", "return_at": result["voyage_wait_until"]}, value["reply_at"])
        c._schedule_voyage_wait(now)
        c.state["concubine_tianji_last_error"] = "\u5929\u673a\u4ee3\u535c\u88ab\u8fdc\u822a\u9501\u62e6\u622a\uff0c\u7b49\u5f85\u5f52\u822a"
    else:
        c.reset_resource_shortage(c.CONCUBINE_TIANJI_RESOURCE_KEY)
        c._schedule_after_tianji(now)


async def handle_reply(text, now, reply_to, *, current_msg_id=0, current_chat_id=0, observed_at=0, reply_context=None):
    now, at = c._query_time(now), c._query_time(observed_at)
    owner, value = c._status_query_owner(), record()
    context = reply_context if isinstance(reply_context, dict) else {}
    root = c._query_int(getattr(reply_to, "id", 0))
    if (now is None or at is None or at > now or not c._owns_status_query(owner)
            or not value or value.get("transport") == "miniapp"
            or value["status"] not in UNRESOLVED or owner[2] != value["account_id"]
            or c._query_int(context.get("sender_id")) not in c.get_game_bot_ids()
            or c._query_int(current_chat_id) != value["chat_id"]
            or c._query_int(getattr(reply_to, "chat_id", 0)) not in (0, value["chat_id"])
            or str(getattr(reply_to, "raw_text", "") or "").strip() not in ("", value["command"])):
        return False
    expected = {"send_as_id": value["identity_id"], "account_id": value["account_id"], "chat_id": value["chat_id"],
                "root_msg_id": root, "reply_to_msg_id": root}
    if (any(key in context and c._query_int(context[key]) != expected_value for key, expected_value in expected.items())
            or any(key in context and context[key] != expected_value for key, expected_value in {
                "op_id": value["op_id"], "source_module": SOURCE, "family": SOURCE,
            }.items())
            or (getattr(reply_to, "sender_id", 0) and not c.sender_matches_identity(reply_to.sender_id, value["identity_id"]))
            or context.get("reply_to_command_edited")
            or (context.get("reply_to_command") and context["reply_to_command"] != value["command"])):
        return False
    value = c._adopt_status_query_receipt(value, now, source_module=SOURCE, include_logs=False)
    if value["msg_id"] <= 0 or root != value["msg_id"] or c._query_int(current_msg_id) <= root or at < value["dispatch_at"] - 1:
        return False
    result = _parse_result(value, text, at)
    if result is None:
        return False
    completed = dict(value, status="complete", reply_at=at, reply_msg_id=current_msg_id, result=result)
    completed.pop("replay_after", None)
    if not _valid_record(completed):
        return False
    unchanged = c._status_query_plan(owner) == value["plan_key"]
    before = copy.deepcopy(owner[1])
    try:
        c._release_owned_concubine_phase(value, "concubine_tianji_msg_id", unchanged=unchanged)
        _apply_result(completed, now, unchanged)
        _store(completed)
        _clear_pending(value)
        return c._save_query_projection(owner, before)
    except Exception:
        owner[1].clear()
        owner[1].update(before)
        c.mark_dirty()
        raise


async def recover(now):
    owner, value = c._status_query_owner(), record()
    if not owner or value is None or c._query_time(now) is None:
        return True
    if not value:
        return bool(block_reason())
    if value["status"] == "complete" and owner[2] == value["account_id"]:
        before = copy.deepcopy(owner[1])
        _clear_pending(value)
        if owner[1] != before:
            c._save_query_projection(owner, before)
            return True
    if value["status"] not in UNRESOLVED:
        return bool(block_reason())
    if value.get("transport") == "miniapp":
        await _recover_miniapp(value, owner, now)
        return True
    if owner[0] in _INFLIGHT or now < value.get("replay_after", value["started_at"]) or owner[2] != value["account_id"]:
        return True
    before = copy.deepcopy(owner[1])
    value = c._adopt_status_query_receipt(value, now, source_module=SOURCE)
    value["replay_after"] = now + c.CONCUBINE_QUERY_REPLAY_SEC
    _store(value)
    if not c._save_query_projection(owner, before):
        return True
    for entry in c._find_owned_concubine_replies(value, now):
        if _parse_result(value, entry.get("text", ""), entry["server_event_at"]) is None:
            continue
        await handle_reply(
            entry.get("text", ""), now,
            c.SimpleNamespace(id=value["msg_id"], chat_id=value["chat_id"], raw_text=value["command"]),
            current_msg_id=entry["message_id"], current_chat_id=value["chat_id"], observed_at=entry["server_event_at"],
            reply_context={"sender_id": entry["sender_id"]},
        )
        break
    return True


async def _send_miniapp(value, owner, current, can_send):
    try:
        response = await tianjige_transport.execute(
            value["identity_id"], value["command"], op_id=value["op_id"], operation_check=can_send,
        )
    except (asyncio.CancelledError, Exception):
        if current() == value:
            before = copy.deepcopy(owner[1])
            _store(dict(value, status="unknown"))
            c._save_query_projection(owner, before)
        raise
    if current() != value:
        return False
    now = c.time.time()
    unchanged = c._status_query_plan(owner) == value["plan_key"]
    before = copy.deepcopy(owner[1])
    receipt = response.get("receipt")
    result = None
    if response.get("terminal") is True and tianjige_transport.receipt_matches(
        receipt, identity_id=value["identity_id"], account_id=value["account_id"],
        op_id=value["op_id"], command=value["command"], started_at=value["started_at"],
    ):
        result = _parse_result(value, response.get("message", ""), receipt["started_at"])
    try:
        if result is not None and can_send():
            completed = dict(value, status="complete", result=result, http_receipt=receipt,
                             reply_at=receipt["started_at"], reply_msg_id=0)
            if not _valid_record(completed):
                return False
            c._release_owned_concubine_phase(value, "concubine_tianji_msg_id", unchanged=True)
            _apply_result(completed, now, True)
            _store(completed)
            return c._save_query_projection(owner, before)
        if response.get("action_dispatched") is False:
            retry_at = now + c.CONCUBINE_SEND_FAILURE_RETRY_MAX_SEC
            _store(dict(value, status="unsent", retry_at=retry_at))
            c._release_owned_concubine_phase(value, "concubine_tianji_msg_id", unchanged=unchanged)
            if unchanged:
                owner[1]["next_concubine_time"] = retry_at
        else:
            _store(dict(value, status="unknown"))
            if unchanged:
                owner[1]["concubine_tianji_last_error"] = "宝阁代卜结果未确认，仅核验状态，不补发、不转群命令"
        c._save_query_projection(owner, before)
        return False
    except Exception:
        owner[1].clear()
        owner[1].update(before)
        c.mark_dirty()
        raise


def _reconciled_effect(value, text, at):
    if not isinstance(text, str) or not text or len(text) > 4096:
        return None
    parsed = c._parse_status_panel(text, at)
    if (not c._is_complete_status_panel(parsed) or not parsed.get("has_partner")
            or parsed["name"] != value["partner"] or "tianji_chain" not in parsed
            or parsed["tianji_due_at"] <= max(at, value["effect"]["due_at"])):
        return None
    effect = {"due_at": parsed["tianji_due_at"], "chain": parsed["tianji_chain"],
              "chain_due_at": parsed["tianji_chain_due_at"]}
    return effect if _valid_effect(effect) else None


async def _recover_miniapp(value, owner, now):
    identity_id = owner[0]
    if (identity_id in _INFLIGHT or now < max(value["started_at"] + HTTP_RECONCILE_INTERVAL_SEC, value.get("replay_after", 0))
            or owner[2] != value["account_id"] or not _controls(owner)
            or not tianjige_transport.available(identity_id)):
        return

    def current():
        if not c._owns_status_query(owner):
            return False
        with c.use_identity(identity_id):
            return bool(_controls(owner) and tianjige_transport.available(identity_id)
                        and record() == value and c._status_query_plan(owner) == value["plan_key"]
                        and c._current_partner_matches(value["partner"]) and _effect() == value["effect"]
                        and c.state.get("concubine_last_snapshot_at", 0) == value["snapshot_at"]
                        and not _other_work())

    if not current():
        return
    before = copy.deepcopy(owner[1])
    probe = {"op_id": c.uuid4().hex, "started_at": max(now, c.time.time())}
    value = dict(value, reconciliation=probe, replay_after=probe["started_at"] + HTTP_RECONCILE_INTERVAL_SEC)
    _store(value)
    if not c._save_query_projection(owner, before):
        return
    _INFLIGHT[identity_id] = probe["op_id"]
    try:
        response = await tianjige_transport.execute(
            identity_id, c.CMD_CONCUBINE_STATUS, op_id=probe["op_id"], operation_check=current,
        )
        if not current() or response.get("terminal") is not True:
            return
        receipt = response.get("receipt")
        if not tianjige_transport.receipt_matches(
            receipt, identity_id=identity_id, account_id=owner[2], op_id=probe["op_id"],
            command=c.CMD_CONCUBINE_STATUS, started_at=probe["started_at"],
        ):
            return
        text = response.get("message", "")
        effect = _reconciled_effect(value, text, receipt["started_at"])
        if effect is None:
            return
        completed = dict(value, status="reconciled", reconciliation=dict(probe, receipt=receipt, text=text, effect=effect))
        if not _valid_record(completed):
            return
        before = copy.deepcopy(owner[1])
        try:
            c._release_owned_concubine_phase(value, "concubine_tianji_msg_id", unchanged=True)
            for key, item in effect.items():
                c.state["concubine_tianji_" + key] = item
            c.state["concubine_tianji_last_error"] = ""
            c._schedule_after_tianji(c.time.time())
            _store(completed)
            c._save_query_projection(owner, before)
        except Exception:
            owner[1].clear()
            owner[1].update(before)
            c.mark_dirty()
            raise
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        c.console_log(f"Divination status reconciliation deferred ({type(exc).__name__})")
    finally:
        if _INFLIGHT.get(identity_id) == probe["op_id"]:
            _INFLIGHT.pop(identity_id, None)
