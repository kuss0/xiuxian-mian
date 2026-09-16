"""Owned greetings, gift inventory reads and spending without blind retries."""

import asyncio
import copy
import re

from ..state import get_storage_bag_records, set_storage_bag_records
from . import concubine as c


STATE_KEY = "concubine_gift_actions"
GREET_STATE_KEY = "concubine_greet_action"
KEYS = {"gift_bag": "concubine_gift_bag_msg_id", "gift": "concubine_gift_msg_id", "greet": "concubine_greet_msg_id"}
FAMILIES = {"gift_bag": "storage_bag", "gift": "concubine_gift", "greet": "concubine_greet"}
UNRESOLVED = {"sending", "sent", "unknown"}
_INFLIGHT = {}


def _source(kind):
    return "concubine_greet" if kind == "greet" else "concubine_gift"


def _error_key(kind):
    return "concubine_greet_last_error" if kind == "greet" else "concubine_gift_last_error"


def _label(kind):
    return "\u95ee\u5b89" if kind == "greet" else "\u8d60\u793c"


def _valid_record(kind, record):
    required = {
        "op_id", "kind", "identity_id", "account_id", "chat_id", "command", "started_at",
        "status", "msg_id", "plan_key", "partner", "affinity", "affinity_seen_at", "day",
    }
    if kind != "greet":
        required.update({"amount", "inventory_key", "parent_op_id"})
    optional = {"sent_at", "dispatch_at", "reply_at", "reply_msg_id", "replay_after", "retry_at", "result"}
    if not isinstance(record, dict) or required - record.keys() or record.keys() - required - optional:
        return False
    if (
        record["kind"] != kind or c._query_int(record["identity_id"]) != c.get_current_identity_id()
        or c._query_int(record["account_id"]) <= 0 or not c._query_int(record["chat_id"])
        or not isinstance(record["op_id"], str) or re.fullmatch(r"[a-f0-9]{32}", record["op_id"]) is None
        or not isinstance(record["plan_key"], str) or re.fullmatch(r"[a-f0-9]{64}", record["plan_key"]) is None
        or not isinstance(record["partner"], str) or not 0 < len(record["partner"]) <= 120
        or type(record["affinity"]) is not int or not 0 <= record["affinity"] < c.CONCUBINE_TIANJI_MIN_AFFINITY
        or (c._query_time(record["affinity_seen_at"]) is None and not (
            kind == "greet" and type(record["affinity_seen_at"]) in {int, float} and record["affinity_seen_at"] == 0))
        or c._query_time(record["started_at"]) is None
        or record["affinity_seen_at"] > record["started_at"]
        or record["day"] != c._local_day_key(record["started_at"])
        or type(record["msg_id"]) is not int or not 0 <= record["msg_id"] < 2 ** 63
        or not isinstance(record["status"], str)
        or record["status"] not in UNRESOLVED | {"complete", "unsent", "expired"}
        or (record["status"] == "expired" and kind != "gift_bag")
        or (record["status"] in {"sent", "complete"} and not record["msg_id"])
        or (record["status"] == "unsent" and record["msg_id"])
        or any(c._query_time(record[key]) is None for key in (
            "sent_at", "dispatch_at", "reply_at", "replay_after", "retry_at") if key in record)
        or (record["msg_id"] and not (
            record["started_at"] - 1 <= record.get("dispatch_at", 0) <= record.get("sent_at", 0)))
    ):
        return False
    if kind == "greet":
        if record["command"] != c.CMD_CONCUBINE_DAILY_GREET:
            return False
    elif (
        type(record["amount"]) is not int or record["amount"] != c.CONCUBINE_TIANJI_MIN_AFFINITY - record["affinity"]
        or not isinstance(record["inventory_key"], str) or re.fullmatch(r"[a-f0-9]{64}", record["inventory_key"]) is None
        or (kind == "gift_bag" and record["parent_op_id"] != "")
        or (kind == "gift" and (not isinstance(record["parent_op_id"], str)
                               or re.fullmatch(r"[a-f0-9]{32}", record["parent_op_id"]) is None))
        or record["command"] != (c.CMD_STORAGE_BAG if kind == "gift_bag" else
                                  f"{c.CMD_CONCUBINE_GIFT_STONE} \u7075\u77f3*{record['amount']}")
    ):
        return False
    if record["status"] == "complete":
        result = record.get("result")
        if (
            c._query_int(record.get("reply_msg_id")) <= record["msg_id"]
            or c._query_time(record.get("reply_at")) is None
            or record["reply_at"] < record["dispatch_at"] - 1
            or not isinstance(result, dict)
            or result.keys() != ({"outcome", "gain", "applied", "wait_until"} if kind == "greet" else
                                 {"outcome", "stones", "gain", "applied"})
            or not isinstance(result["outcome"], str)
            or result["outcome"] not in {"ready", "success", "shortage", "stale", "no_partner", "daily_limit", "summary", "voyage_lock"}
            or any(type(result[key]) is not int or not 0 <= result[key] < 2 ** 63
                   for key in (("gain",) if kind == "greet" else ("stones", "gain")))
            or type(result["applied"]) is not bool
        ):
            return False
        if kind == "greet":
            return bool(
                result["outcome"] in {"success", "daily_limit", "no_partner", "voyage_lock"}
                and (c._query_time(result["wait_until"]) is not None or (
                    type(result["wait_until"]) in {int, float} and result["wait_until"] == 0))
                and (result["outcome"] != "voyage_lock" or result["wait_until"] == 0 or result["wait_until"] > record["reply_at"])
                and (result["outcome"] == "voyage_lock" or result["wait_until"] == 0)
                and (0 < result["gain"] < 2 ** 63 - record["affinity"] if result["outcome"] == "success" else
                     result["gain"] == 0 and result["applied"] is False)
            )
        if (
            (kind == "gift_bag" and (result["outcome"] not in {"ready", "shortage", "stale", "summary"} or result["gain"] or result["applied"]))
            or (kind == "gift" and result["outcome"] not in {"success", "shortage", "no_partner", "daily_limit"})
            or (result["outcome"] == "ready" and result["stones"] < record["amount"])
            or (result["outcome"] == "success" and (
                result["stones"] != record["amount"] or not 0 < result["gain"] < 2 ** 63 - record["affinity"]))
        ):
            return False
    elif "result" in record or "reply_at" in record or "reply_msg_id" in record:
        return False
    return True


def records():
    value = c.state.get(STATE_KEY, {})
    greet = c.state.get(GREET_STATE_KEY, {})
    if not isinstance(value, dict) or value.keys() - {"gift_bag", "gift"} or not isinstance(greet, dict):
        return None
    value = dict(value)
    if greet:
        value["greet"] = greet
    if any(not _valid_record(kind, record) for kind, record in value.items()):
        return None
    return copy.deepcopy(value)


def _affinity_completions():
    value = records()
    if value is None:
        return None
    return [record for kind, record in value.items()
            if kind in {"greet", "gift"} and record["status"] == "complete"
            and record["account_id"] == c.get_identity_account(record["identity_id"])
            and record["result"]["outcome"] == "success"]


def needs_calibration():
    """An unprojected confirmed gain is not a usable cached balance."""
    completed = _affinity_completions()
    if completed is None:
        return True
    snapshot_at = c._query_time(c.state.get("concubine_last_snapshot_at"))
    pending = [record for record in completed
               if not record["result"]["applied"] and record["partner"] == c.state.get("concubine_name")
               and (snapshot_at is None or snapshot_at <= record["reply_at"])]
    if not pending:
        return False
    from .wanxin import wanxin_affinity_read_covers

    return any(not wanxin_affinity_read_covers(c.state.get("wanxin_observation"), record["reply_at"], now=c.time.time())
               for record in pending)


def snapshot_is_stale(observed_at, *, now):
    at, processed_at = c._query_time(observed_at), c._query_time(now)
    if at is None or processed_at is None or at > processed_at:
        return False
    # Completion records lack reply-edit provenance. Equal seconds cannot
    # establish inclusion, so do not synthesize native order from local sends.
    return any(at <= record["reply_at"] <= processed_at for record in _affinity_completions() or ())


def _store(record):
    if record["kind"] == "greet":
        c.state[GREET_STATE_KEY] = copy.deepcopy(record)
    else:
        value = dict(c.state.get(STATE_KEY, {}))
        value[record["kind"]] = copy.deepcopy(record)
        c.state[STATE_KEY] = value
    c.mark_dirty()


def block_reason():
    value = records()
    if value is None:
        return "invalid"
    if any(record["status"] in UNRESOLVED for record in value.values()):
        return "pending"
    for kind, key in KEYS.items():
        if c._phase() == kind + "_pending" or c.state.get(key):
            return "legacy_pending"
    return ""


def finished_today(now, kind="gift"):
    value = records()
    if value is None:
        return True
    day = c._local_day_key(now)
    return any(
        record["status"] == "complete" and record["day"] == day
        and ((kind == "greet" and record["kind"] == "greet" and record["result"]["outcome"] in {"success", "daily_limit"})
             or (kind == "gift" and (record["kind"] == "gift"
                 or (record["kind"] == "gift_bag" and record["result"]["outcome"] == "shortage"))))
        for record in value.values()
    )


def _inventory_key(identity_id):
    try:
        value = get_storage_bag_records().get(str(identity_id), {})
        return c.hashlib.sha256(c.json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()
    except (TypeError, ValueError, OverflowError):
        return ""


def _clear_pending(record):
    c._clear_status_query_pending(record, source_module=_source(record["kind"]), family=FAMILIES[record["kind"]])


def _release_phase(record, *, unchanged):
    released = c._release_owned_concubine_phase(record, KEYS[record["kind"]], unchanged=unchanged)
    if released and record["kind"] == "gift" and c.state.get("concubine_gift_amount") == record["amount"]:
        c.state["concubine_gift_amount"] = 0


def _ready_bag(now, amount):
    value = records()
    bag = (value or {}).get("gift_bag", {})
    if (
        not bag or bag["status"] != "complete" or bag["result"]["outcome"] != "ready"
        or bag["account_id"] != c.get_identity_account(bag["identity_id"])
        or bag["chat_id"] != c.get_game_group_id() or bag["day"] != c._local_day_key(now)
        or bag["partner"] != c.state.get("concubine_name") or bag["affinity"] != c.state.get("concubine_affinity")
        or bag["plan_key"] != c._status_query_plan(c._status_query_owner())
        or not now - c.CONCUBINE_PANEL_REUSE_MAX_AGE_SEC <= bag["reply_at"] <= now
        or type(amount) is not int or amount != bag["amount"] or bag["result"]["stones"] < amount
    ):
        return None
    gift = value.get("gift", {})
    if gift and gift["parent_op_id"] == bag["op_id"] and gift["status"] != "unsent":
        return None
    if gift and now < gift.get("retry_at", 0):
        return None
    return bag


def _admitted(kind, now, amount):
    owner = c._status_query_owner()
    if (
        kind not in KEYS or c._query_time(now) is None
        or not c._owns_status_query(owner, sending=True, kind="gift_status")
        or (owner[0], kind) in _INFLIGHT or block_reason()
        or c._status_snapshot_block_reason(now) or c._has_phaseful_summary_window(now)
        or c._has_voyage_runtime_state(now)
    ):
        return False
    value = records()
    if any(now < item.get("retry_at", 0) for item in value.values()):
        return False
    if kind == "greet":
        return c._is_daily_greet_due(now)
    if not c._is_gift_recovery_eligible(now) or not c._has_recent_concubine_status_panel(now):
        return False
    return kind == "gift_bag" or _ready_bag(now, amount) is not None


async def send(kind, now, amount=0):
    if not _admitted(kind, now, amount):
        return False
    owner = c._status_query_owner()
    identity_id, identity, account_id = owner
    bag = _ready_bag(now, amount) if kind == "gift" else None
    started_at = max(float(now), c.time.time())
    command = c.CMD_CONCUBINE_DAILY_GREET
    if kind != "greet":
        amount = c.CONCUBINE_TIANJI_MIN_AFFINITY - identity["concubine_affinity"]
        command = c.CMD_STORAGE_BAG if kind == "gift_bag" else f"{c.CMD_CONCUBINE_GIFT_STONE} \u7075\u77f3*{amount}"
    record = {
        "op_id": c.uuid4().hex, "kind": kind, "identity_id": identity_id, "account_id": account_id,
        "chat_id": c.get_game_group_id(), "command": command, "started_at": started_at,
        "status": "sending", "msg_id": 0, "plan_key": c._status_query_plan(owner),
        "partner": identity["concubine_name"], "affinity": identity["concubine_affinity"],
        "affinity_seen_at": identity["concubine_last_snapshot_at"], "day": c._local_day_key(started_at),
    }
    if kind != "greet":
        record.update(amount=amount, inventory_key=_inventory_key(identity_id), parent_op_id=bag["op_id"] if bag else "")
    if not _valid_record(kind, record):
        return False
    c._set_phase(kind + "_pending")
    identity[KEYS[kind]] = 0
    identity["next_concubine_time"] = started_at + c.CONCUBINE_PHASE_TIMEOUT_SEC
    if kind == "gift":
        identity["concubine_gift_amount"] = amount
    record["plan_key"] = c._status_query_plan(owner)
    _store(record)
    _INFLIGHT[identity_id, kind] = record["op_id"]
    error_key, label = _error_key(kind), _label(kind)
    unknown_text = f"{label}\u53d1\u9001\u72b6\u6001\u672a\u77e5\uff0c\u4fdd\u7559\u539f\u64cd\u4f5c\u7b49\u5f85\u53cd\u9988"

    def current():
        if not c._owns_status_query(owner):
            return None
        with c.use_identity(identity_id):
            value = records()
        item = value.get(kind) if value is not None else None
        return item if item and all(item[key] == record[key] for key in (
            "op_id", "identity_id", "account_id", "chat_id", "kind", "command", "started_at")) else None

    def can_send():
        if not c._owns_status_query(owner, sending=True, kind="gift_status"):
            return False
        with c.use_identity(identity_id):
            return bool(
                current() == record and c._status_query_plan(owner) == record["plan_key"]
                and (kind == "greet" or _inventory_key(identity_id) == record["inventory_key"])
                and c._local_day_key(c.time.time()) == record["day"]
                and not c._has_phaseful_summary_window(c.time.time())
                and not c.voyage_actions.block_reason()
                and not c.divination_actions.block_reason() and not c.heart_actions.block_reason()
                and not c.reacquire_actions.block_reason()
                and not c.external_events.needs_calibration()
            )

    def unsent(reason, *, persist=True):
        unchanged = c._status_query_plan(owner) == record["plan_key"]
        retry_at = max(started_at, c.time.time()) + c.random.uniform(
            c.CONCUBINE_SEND_FAILURE_RETRY_MIN_SEC, c.CONCUBINE_SEND_FAILURE_RETRY_MAX_SEC)
        _store(dict(record, status="unsent", retry_at=retry_at))
        _release_phase(record, unchanged=unchanged)
        if unchanged:
            identity["next_concubine_time"] = retry_at
            if kind != "greet":
                identity["concubine_gift_attempt_day"] = ""
            identity[error_key] = reason
        return c.save_state() if persist else False

    try:
        try:
            saved = bool(record["plan_key"]) and c.save_state() is not False
        except Exception:
            unsent(f"{label}\u5728\u9014\u72b6\u6001\u4fdd\u5b58\u5f02\u5e38\uff0c\u672c\u6b21\u672a\u53d1\u9001", persist=False)
            raise
        if not saved:
            unsent(f"{label}\u5728\u9014\u72b6\u6001\u672a\u4fdd\u5b58\uff0c\u672c\u6b21\u672a\u53d1\u9001")
            return False
        previous_block = dict(c.classify_game_send_block(identity_id, command))
        try:
            msg = await c._send_concubine_game_command(
                command, track=True, max_retry=0, reply_timeout=c.CONCUBINE_PHASE_TIMEOUT_SEC,
                send_as_id=identity_id, target_chat_id=record["chat_id"], source_module=_source(kind),
                op_id=record["op_id"], operation_check=can_send,
            )
        except (asyncio.CancelledError, Exception):
            item = current()
            if item and item["status"] == "sending":
                _store(dict(item, status="unknown"))
                if c._status_query_plan(owner) == record["plan_key"]:
                    identity[error_key] = unknown_text
                c.save_state()
            elif item and item["status"] == "complete":
                _clear_pending(item)
                c.save_state()
            raise
        item = current()
        if item is None:
            return False
        if item["status"] != "sending":
            if item["status"] == "complete":
                _clear_pending(item)
                c.save_state()
            return item["status"] in {"sent", "complete"}
        at = max(started_at, c.time.time())
        if not msg:
            block = c.classify_game_send_block(identity_id, command)
            if (block.get("status") == "unsent" and block != previous_block
                    and started_at <= (c._query_time(block.get("at")) or 0) <= at + 1):
                unsent(f"{label}\u672a\u53d1\u9001\uff1a{block.get('code') or 'blocked'}")
                return False
        root, chat = c._query_int(getattr(msg, "id", 0)), c._query_int(getattr(msg, "chat_id", 0))
        sent_at = c._query_time(getattr(msg, "sent_at", 0)) or 0
        dispatch_at = c._query_time(getattr(msg, "send_started_at", 0)) or 0
        known = root > 0 and chat == record["chat_id"] and started_at - 1 <= dispatch_at <= sent_at <= at + 1
        item = dict(record, status="sent" if known else "unknown")
        if known:
            item.update(msg_id=root, sent_at=sent_at, dispatch_at=dispatch_at)
        if c._status_query_plan(owner) == record["plan_key"]:
            if known:
                identity[KEYS[kind]] = root
                identity["next_concubine_time"] = sent_at + c.CONCUBINE_PHASE_TIMEOUT_SEC
            identity[error_key] = "" if known else unknown_text
            item["plan_key"] = c._status_query_plan(owner)
        _store(item)
        c.save_state()
        return known
    finally:
        if _INFLIGHT.get((identity_id, kind)) == record["op_id"]:
            _INFLIGHT.pop((identity_id, kind), None)


def owns_reply(family, root, chat_id):
    kind = next((key for key, value in FAMILIES.items() if value == family), None)
    if kind is None:
        return False
    if kind == "greet":
        return True
    value = records()
    if value is None:
        return True
    record = value.get(kind)
    if not record:
        return False
    if kind == "gift":
        return True
    record = c._adopt_status_query_receipt(record, c.time.time() + 1, source_module=_source(kind), include_logs=False)
    return bool(c._query_int(chat_id) == record["chat_id"] and c._query_int(root) > 0 and (
        c._query_int(root) <= record["msg_id"] or (record["status"] in UNRESOLVED and not record["msg_id"])))


def _parse_result(kind, text, record, observed_at):
    if kind == "greet":
        result = {"outcome": "", "gain": 0, "applied": False, "wait_until": 0}
        gains = list(c.RE_AFFINITY_GAIN.finditer(text))
        daily_limit = "\u4eca\u65e5\u5df2\u7ecf\u95ee\u5b89\u8fc7\u4e86" in text
        voyage_lock = c._is_voyage_lock_text(text)
        no_partner = c._is_no_partner_text(text)
        if (c._is_phaseful_summary_text(text) or len(gains) > 1
                or sum((bool(gains), daily_limit, voyage_lock, no_partner)) != 1):
            return None
        if gains:
            gain = gains[0]
            if gain["name"].strip() != record["partner"] or not 0 < int(gain["amount"]) < 2 ** 63 - record["affinity"]:
                return None
            return dict(result, outcome="success", gain=int(gain["amount"]))
        if daily_limit:
            return dict(result, outcome="daily_limit")
        if voyage_lock:
            voyage = c._parse_voyage_rejection(text, observed_at)
            if voyage and voyage["partner"] in ("", record["partner"]):
                return dict(result, outcome="voyage_lock", wait_until=voyage["return_at"])
        if no_partner:
            return dict(result, outcome="no_partner")
        return None
    if kind == "gift_bag":
        if c._is_phaseful_summary_text(text):
            return {"outcome": "summary", "stones": 0, "gain": 0, "applied": False}
        parsed = c.parse_storage_bag_reply(text)
        if (not parsed or c.resolve_storage_bag_identity_id(parsed.get("owner")) != record["identity_id"]
                or ("\u7075\u77f3" not in parsed["items"] and not parsed["empty"])):
            return None
        stones = parsed["items"].get("\u7075\u77f3", 0)
        return {"outcome": "ready" if stones >= record["amount"] else "shortage",
                "stones": stones, "gain": 0, "applied": False}
    parsed = c._parse_gift_success(text)
    if parsed:
        if parsed["name"] != record["partner"] or parsed["stone"] != record["amount"] or parsed["amount"] <= 0:
            return None
        return {"outcome": "success", "stones": parsed["stone"], "gain": parsed["amount"], "applied": False}
    if any(token in text for token in ("\u7075\u77f3\u4e0d\u8db3", "\u7075\u77f3\u4e0d\u591f", "\u6570\u91cf\u4e0d\u8db3")):
        return {"outcome": "shortage", "stones": 0, "gain": 0, "applied": False}
    if c._is_no_partner_text(text):
        return {"outcome": "no_partner", "stones": 0, "gain": 0, "applied": False}
    if any(token in text for token in ("\u4eca\u65e5\u5df2\u8d60\u4e88", "\u4eca\u65e5\u5df2\u7ecf\u8d60\u4e88", "\u4eca\u65e5\u8d60\u4e88\u6b21\u6570\u5df2\u5c3d")):
        return {"outcome": "daily_limit", "stones": 0, "gain": 0, "applied": False}
    return None


def _apply_result(record, result, now, unchanged):
    if record["kind"] == "gift_bag":
        if not unchanged or not c._is_gift_recovery_eligible(now) or now - record["reply_at"] > c.CONCUBINE_PANEL_REUSE_MAX_AGE_SEC:
            result["outcome"] = "stale"
        if unchanged:
            c.state["concubine_gift_attempt_day"] = record["day"] if result["outcome"] in {"ready", "shortage"} else ""
            c.state["concubine_gift_last_error"] = (
                "\u7075\u77f3\u4e0d\u8db3\uff0c\u672c\u65e5\u6682\u4e0d\u8d60\u793c" if result["outcome"] == "shortage" else "")
            c._schedule_chain_action(now) if result["outcome"] == "ready" else c._schedule_status_recheck(now)
        return
    if result["outcome"] == "success":
        if (c.state.get("concubine_name") == record["partner"]
                and c.state.get("concubine_affinity") == record["affinity"]
                and record["affinity_seen_at"] > 0
                and c.state.get("concubine_last_snapshot_at") == record["affinity_seen_at"]):
            c.state["concubine_affinity"] = record["affinity"] + result["gain"]
            result["applied"] = True
        if record["kind"] == "gift":
            inventory = get_storage_bag_records().get(str(record["identity_id"]), {})
            seen_at = c._query_time(inventory.get("updated_at")) if isinstance(inventory, dict) else None
            if (_inventory_key(record["identity_id"]) == record["inventory_key"]
                    and seen_at is not None and seen_at <= record["dispatch_at"]):
                c.apply_storage_bag_item_deltas(record["identity_id"], {"\u7075\u77f3": -result["stones"]}, persist=False)
    if record["kind"] == "greet":
        _apply_greet_result(record, result, now, unchanged)
        return
    if result["outcome"] in {"success", "daily_limit"}:
        c.state["concubine_last_gift_day"] = max(str(c.state.get("concubine_last_gift_day") or ""), record["day"])
    c.state["concubine_gift_attempt_day"] = max(str(c.state.get("concubine_gift_attempt_day") or ""), record["day"])
    if unchanged:
        c.state["concubine_gift_last_error"] = "" if result["outcome"] == "success" and result["applied"] else (
            "\u8d60\u793c\u5df2\u6536\u5230\u786e\u8ba4\uff0c\u7b49\u5f85\u4e0b\u6b21\u72b6\u6001\u6821\u51c6")
        if result["applied"]:
            c._normalize_tianji_affinity_error(now)
        else:
            c._schedule_status_recheck(now)


def _apply_greet_result(record, result, now, unchanged):
    if result["outcome"] in {"success", "daily_limit"}:
        c.state["concubine_last_greet_day"] = max(str(c.state.get("concubine_last_greet_day") or ""), record["day"])
    if not unchanged:
        return
    c.state["concubine_greet_retry_count"] = 0
    c.state["concubine_greet_last_error"] = ""
    if result["outcome"] == "voyage_lock":
        c._apply_voyage_snapshot({"status": "sailing", "return_at": result["wait_until"]}, now)
        c.state["concubine_greet_last_error"] = "\u6bcf\u65e5\u95ee\u5b89\u88ab\u8fdc\u822a\u9501\u62e6\u622a\uff0c\u7b49\u5f85\u5f52\u822a"
    elif result["outcome"] == "no_partner" or (result["outcome"] == "success" and not result["applied"]):
        c.state["concubine_greet_last_error"] = "\u95ee\u5b89\u5df2\u6536\u5230\u786e\u8ba4\uff0c\u7b49\u5f85\u4e0b\u6b21\u72b6\u6001\u6821\u51c6"
        c._set_availability("unknown")
        c.state["concubine_last_snapshot_at"] = 0
        c._schedule_status_recheck(now)
    elif result["outcome"] == "success":
        c._normalize_tianji_affinity_error(now)
        if c.state["concubine_affinity"] < c.CONCUBINE_TIANJI_MIN_AFFINITY:
            c._schedule_affinity_recovery(now)
        else:
            c._schedule_after_tianji(now)
    else:
        c.state["concubine_greet_last_error"] = "\u4eca\u65e5\u5df2\u7ecf\u95ee\u5b89\u8fc7"
        if c._is_gift_recovery_due(now):
            c._schedule_chain_action(now)
        else:
            c._schedule_next_daily_greet_check(now)


async def handle_reply(kind, text, now, reply_to, *, current_msg_id=0, current_chat_id=0, observed_at=0, reply_context=None):
    now = c._query_time(now)
    if now is None:
        return False
    owner = c._status_query_owner()
    value = records()
    record = (value or {}).get(kind)
    at = c._query_time(observed_at)
    context = reply_context if isinstance(reply_context, dict) else {}
    if (
        not record or not c._owns_status_query(owner) or owner[2] != record["account_id"]
        or record["status"] not in UNRESOLVED or at is None or not at <= now
        or c._query_int(context.get("sender_id")) not in c.get_game_bot_ids()
        or c._query_int(current_chat_id) != record["chat_id"]
        or c._query_int(getattr(reply_to, "chat_id", 0)) not in (0, record["chat_id"])
        or str(getattr(reply_to, "raw_text", "") or "").strip() not in ("", record["command"])
    ):
        return False
    expected = {
        "send_as_id": record["identity_id"], "account_id": record["account_id"],
        "chat_id": record["chat_id"], "root_msg_id": c._query_int(getattr(reply_to, "id", 0)),
        "reply_to_msg_id": c._query_int(getattr(reply_to, "id", 0)),
    }
    if (any(key in context and c._query_int(context[key]) != value for key, value in expected.items())
            or context.get("reply_to_command_edited")
            or (context.get("reply_to_command") and context["reply_to_command"] != record["command"])):
        return False
    record = c._adopt_status_query_receipt(record, now, source_module=_source(kind))
    if (record["msg_id"] <= 0 or c._query_int(getattr(reply_to, "id", 0)) != record["msg_id"]
            or c._query_int(current_msg_id) <= record["msg_id"] or at < record["dispatch_at"] - 1):
        return False
    result = _parse_result(kind, str(text or ""), record, at)
    if result is None:
        return False
    unchanged = c._status_query_plan(owner) == record["plan_key"]
    identity = owner[1]
    before, inventory = copy.deepcopy(identity), get_storage_bag_records()
    completed = dict(record, status="complete", reply_at=at, reply_msg_id=current_msg_id, result=result)
    completed.pop("replay_after", None)
    if not _valid_record(kind, completed):
        return False
    try:
        _release_phase(record, unchanged=unchanged)
        _apply_result(completed, result, now, unchanged)
        completed["plan_key"] = c._status_query_plan(owner)
        _store(completed)
        _clear_pending(record)
        saved = c.save_state() is not False
    except Exception:
        identity.clear()
        identity.update(before)
        set_storage_bag_records(inventory)
        c.mark_dirty()
        raise
    if not saved:
        identity.clear()
        identity.update(before)
        set_storage_bag_records(inventory)
        c.mark_dirty()
        return False
    if kind == "gift_bag" and result["outcome"] == "ready":
        await c._send_gift_command(now, record["amount"])
    return True


async def recover(now):
    owner = c._status_query_owner()
    value = records()
    if not owner or value is None or c._query_time(now) is None:
        return True
    if not value:
        return bool(block_reason())
    for kind, record in value.items():
        if record["status"] == "complete" and owner[2] == record["account_id"]:
            before = copy.deepcopy(owner[1])
            _clear_pending(record)
            if owner[1] != before:
                c._save_query_projection(owner, before)
                return True
        if record["status"] not in UNRESOLVED:
            continue
        if (record["identity_id"], kind) in _INFLIGHT or now < record.get("replay_after", record["started_at"]):
            return True
        if kind != "gift_bag" and owner[2] != record["account_id"]:
            return True
        before = copy.deepcopy(owner[1])
        record = c._adopt_status_query_receipt(record, now, source_module=_source(kind))
        record["replay_after"] = now + c.CONCUBINE_QUERY_REPLAY_SEC
        _store(record)
        if not c._save_query_projection(owner, before):
            return True
        if record["account_id"] == c.get_identity_account(record["identity_id"]):
            for entry in c._find_owned_concubine_replies(record, now):
                if _parse_result(kind, entry.get("text", ""), record, entry["server_event_at"]) is None:
                    continue
                await handle_reply(
                    kind, entry.get("text", ""), now,
                    c.SimpleNamespace(id=record["msg_id"], chat_id=record["chat_id"], raw_text=record["command"]),
                    current_msg_id=entry["message_id"], current_chat_id=record["chat_id"],
                    observed_at=entry["server_event_at"], reply_context={"sender_id": entry["sender_id"]},
                )
                # A valid result awaiting local commit is not a read timeout.
                return True
        if kind == "gift_bag" and now >= record.get("sent_at", record["started_at"]) + c.CONCUBINE_PHASE_TIMEOUT_SEC:
            before = copy.deepcopy(owner[1])
            unchanged = (record["account_id"] == c.get_identity_account(record["identity_id"])
                         and c._status_query_plan(c._status_query_owner()) == record["plan_key"])
            retry_at = now + c.CONCUBINE_STATUS_RECHECK_MIN_SEC
            _store(dict(record, status="expired", retry_at=retry_at))
            _clear_pending(record)
            _release_phase(record, unchanged=unchanged)
            if unchanged:
                c.state["concubine_gift_attempt_day"] = ""
                c.state["next_concubine_time"] = retry_at
                c.state["concubine_gift_last_error"] = "\u8d60\u793c\u50a8\u7269\u888b\u672a\u6536\u5230\u5b8c\u6574\u9762\u677f\uff0c\u7a0d\u540e\u6821\u51c6"
            c._save_query_projection(owner, before)
        return True
    if block_reason() or any(now < item.get("retry_at", 0) for item in value.values()):
        return True
    bag = value.get("gift_bag", {})
    if bag and _ready_bag(now, bag["amount"]):
        await c._send_gift_command(now, bag["amount"])
        return True
    return False
