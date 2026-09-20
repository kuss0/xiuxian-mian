"""Owned voyage launch/settlement mutations with evidence-only recovery."""

import asyncio
import copy
import re

from . import concubine as c
from . import tianjige_transport


STATE_KEY = "concubine_voyage_actions"
SOURCE = "concubine_voyage"
KINDS = {"voyage", "voyage_return"}
UNRESOLVED = {"sending", "sent", "unknown"}
_INFLIGHT = {}
HTTP_RECONCILE_INTERVAL_SEC = 1800
START_HEAD = "\u3010\u4e71\u661f\u6d77\u8fdc\u822a\u00b7\u542f\u3011"
RETURN_HEAD = "\u3010\u4e71\u661f\u6d77\u8fdc\u822a\u00b7\u5f52\u3011"
RE_AMOUNT = re.compile(r"(?:[0-9]+|[1-9][0-9]{0,2}(?:,[0-9]{3})+)")
RE_REWARD = re.compile(r"^[ \t]*(?:-[ \t]*)?[^\r\n]+?\s*[x\u00d7+\uff0b]\s*[0-9]+(?:,[0-9]{3})*\s*$", re.MULTILINE)
RE_AFFINITY_GAIN = re.compile(r"\u60c5\u7f18\u589e\u52a0(?:\u4e86)?\s*(?P<amount>[0-9,]+)\s*\u70b9")


def _zero_time(value):
    return type(value) in {int, float} and (value == 0 or c._query_time(value) is not None)


def _bounded_name(value, *, empty=False, limit=120):
    return isinstance(value, str) and value == value.strip() and (empty or bool(value)) and len(value) <= limit


def _valid_voyage(value):
    return bool(isinstance(value, dict) and value.keys() == {"status", "route", "return_at"}
                and isinstance(value["status"], str) and value["status"] in {"", "idle", "sailing", "returned", "needs_status"}
                and _bounded_name(value["route"], empty=True, limit=40) and _zero_time(value["return_at"]))


def _voyage():
    value = {key: c.state.get("concubine_voyage_" + key, 0 if key == "return_at" else "")
             for key in ("status", "route", "return_at")}
    return value if _valid_voyage(value) else None


def _command(kind, route):
    return f"{c.CMD_CONCUBINE_VOYAGE} {route}" if kind == "voyage" else c.CMD_CONCUBINE_VOYAGE_RETURN


def _valid_record(kind, record):
    required = {"op_id", "kind", "identity_id", "account_id", "chat_id", "command", "started_at", "status", "msg_id",
                "plan_key", "partner", "route", "snapshot_at", "affinity", "voyage"}
    optional = {"sent_at", "dispatch_at", "reply_at", "reply_msg_id", "replay_after", "retry_at", "result", "transport", "http_receipt", "reconciliation"}
    if not isinstance(record, dict) or required - record.keys() or record.keys() - required - optional:
        return False
    http = record.get("transport") == "miniapp"
    if "transport" in record and not http:
        return False
    if http:
        if (record["msg_id"] != 0 or record.get("reply_msg_id", 0) != 0
                or type(record.get("reply_msg_id", 0)) is not int
                or "sent_at" in record or "dispatch_at" in record or record["status"] == "sent"):
            return False
        if record["status"] == "complete":
            if not tianjige_transport.receipt_matches(
                record.get("http_receipt"), identity_id=record["identity_id"], account_id=record["account_id"],
                op_id=record["op_id"], command=record["command"], started_at=record["started_at"],
            ) or record.get("reply_at") != record["http_receipt"]["started_at"]:
                return False
        elif "http_receipt" in record:
            return False
    elif "http_receipt" in record:
        return False
    if (
        kind not in KINDS or record["kind"] != kind or not _bounded_name(record["route"], limit=40)
        or record["command"] != _command(kind, record["route"])
        or not _bounded_name(record["partner"]) or not _valid_voyage(record["voyage"])
        or c._query_int(record["identity_id"]) != c.get_current_identity_id()
        or c._query_int(record["account_id"]) <= 0 or not c._query_int(record["chat_id"])
        or not isinstance(record["op_id"], str) or re.fullmatch(r"[a-f0-9]{32}", record["op_id"]) is None
        or not isinstance(record["plan_key"], str) or re.fullmatch(r"[a-f0-9]{64}", record["plan_key"]) is None
        or type(record["affinity"]) is not int or not 0 <= record["affinity"] < 2 ** 63
        or not _zero_time(record["snapshot_at"]) or c._query_time(record["started_at"]) is None
        or record["snapshot_at"] > record["started_at"]
        or type(record["msg_id"]) is not int or not 0 <= record["msg_id"] < 2 ** 63
        or not isinstance(record["status"], str) or record["status"] not in UNRESOLVED | {"unsent", "complete", "reconciled"}
        or any(c._query_time(record[key]) is None for key in ("sent_at", "dispatch_at", "reply_at", "replay_after", "retry_at") if key in record)
        or (not http and record["status"] in {"sent", "complete"} and not record["msg_id"])
        or (record["status"] in {"sending", "unsent"} and record["msg_id"])
        or (bool(record["msg_id"]) != ("sent_at" in record and "dispatch_at" in record))
        or (not record["msg_id"] and bool({"sent_at", "dispatch_at"} & record.keys()))
        or (record["msg_id"] and not record["started_at"] - 1 <= record.get("dispatch_at", 0) <= record.get("sent_at", 0))
        or (kind == "voyage" and record["voyage"]["status"] not in {"", "idle"})
        or (kind == "voyage_return" and (
            record["voyage"]["status"] not in {"returned", "sailing"}
            or not 0 < record["voyage"]["return_at"] <= record["started_at"]
            or record["voyage"]["route"] != record["route"]))
    ):
        return False
    if "reconciliation" in record or record["status"] == "reconciled":
        probe = record.get("reconciliation")
        if (not http or record["status"] not in UNRESOLVED | {"reconciled"}
                or not isinstance(probe, dict)
                or not isinstance(probe.get("op_id"), str)
                or re.fullmatch(r"[a-f0-9]{32}", probe["op_id"]) is None
                or probe["op_id"] == record["op_id"]
                or c._query_time(probe.get("started_at")) is None
                or probe["started_at"] <= record["started_at"]):
            return False
        if record["status"] == "reconciled":
            if (probe.keys() != {"op_id", "started_at", "receipt", "text", "voyage"}
                    or not tianjige_transport.receipt_matches(
                        probe["receipt"], identity_id=record["identity_id"], account_id=record["account_id"],
                        op_id=probe["op_id"], command=c.CMD_CONCUBINE_VOYAGE_STATUS, started_at=probe["started_at"])
                    or not isinstance(probe["text"], str)
                    or not _valid_voyage(probe["voyage"])
                    or _reconciled_voyage(record, probe["text"], probe["receipt"]["started_at"]) != probe["voyage"]):
                return False
        elif probe.keys() != {"op_id", "started_at"}:
            return False
    if record["status"] != "complete":
        return not ({"result", "reply_at", "reply_msg_id"} & record.keys())
    result = record.get("result")
    if ((not http and c._query_int(record.get("reply_msg_id")) <= record["msg_id"])
            or c._query_time(record.get("reply_at")) is None or record["reply_at"] < record.get("dispatch_at", record["started_at"]) - 1
            or not isinstance(result, dict)
            or result.keys() != {"outcome", "voyage", "affinity_loss", "affinity_gain", "minimum_affinity", "applied", "affinity_applied", "text"}
            or type(result["applied"]) is not bool or type(result["affinity_applied"]) is not bool
            or not isinstance(result["voyage"], dict)
            or (result["voyage"] and not _valid_voyage(result["voyage"]))
            or any(type(result[key]) is not int for key in ("affinity_loss", "affinity_gain", "minimum_affinity"))
            or (result["applied"] and not result["voyage"])
            or (result["affinity_applied"] and not result["applied"])
            or not isinstance(result["text"], str) or len(result["text"]) > 4096):
        return False
    parsed = _parse_result(record, result["text"], record["reply_at"])
    return bool(parsed is not None and dict(parsed, applied=result["applied"], affinity_applied=result["affinity_applied"]) == result
                and (not result["affinity_applied"] or (
                    result["outcome"] == "returned" and result["affinity_loss"] + result["affinity_gain"] > 0)))


def records():
    value = c.state.get(STATE_KEY, {})
    if not isinstance(value, dict) or value.keys() - KINDS or any(not _valid_record(kind, item) for kind, item in value.items()):
        return None
    return copy.deepcopy(value)


def _store(record):
    values = dict(c.state.get(STATE_KEY, {}))
    values[record["kind"]] = copy.deepcopy(record)
    c.state[STATE_KEY] = values
    c.mark_dirty()


def block_reason():
    values = records()
    if values is None:
        return "invalid"
    if any(item["status"] in UNRESOLVED for item in values.values()):
        return "pending"
    if c._phase() in c.CONCUBINE_VOYAGE_PENDING_PHASES:
        return "legacy_pending"
    query = c._status_query_record()
    owned_read = bool(query and query["kind"] == "voyage_status" and query["status"] in c.CONCUBINE_QUERY_UNRESOLVED)
    if c.state.get("concubine_voyage_msg_id") and not owned_read:
        return "legacy_pending"
    return ""


def blocks_affinity_snapshot(parsed, at):
    value = (records() or {}).get("voyage_return", {})
    return bool(value.get("status") == "reconciled"
                and parsed.get("has_partner") and parsed.get("name") == value["partner"]
                and c._current_partner_matches(value["partner"])
                and c.state.get("concubine_last_snapshot_at", 0)
                <= value["reconciliation"]["receipt"]["started_at"]
                and ("affinity" not in parsed or at <= value["reconciliation"]["receipt"]["started_at"]))


def _clear_pending(record):
    if record.get("transport") == "miniapp":
        return
    c._clear_status_query_pending(record, source_module=SOURCE, family="concubine_voyage")


def _release_phase(record, *, unchanged):
    return c._release_owned_concubine_phase(record, "concubine_voyage_msg_id", unchanged=unchanged)


def _controls(owner, kind):
    return bool(c._owns_status_query(owner) and owner[2] > 0 and c.get_global_enabled()
                and c.get_identity_enabled(owner[0]) and c.get_game_group_id()
                and not c.external_events.needs_calibration()
                and (kind == "voyage_return" or owner[1].get("concubine_voyage_enabled")))


def _other_work():
    query = c._status_query_record()
    pending = c.state.get("pending_tasks")
    if (query is None or (query and query["status"] in c.CONCUBINE_QUERY_UNRESOLVED)
            or c.affinity_actions.block_reason() or c.fragment_actions.block_reason() or c.divination_actions.block_reason()
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


def _admitted(kind, now):
    owner = c._status_query_owner()
    if (kind not in KINDS or c._query_time(now) is None or not _controls(owner, kind)
            or owner[0] in _INFLIGHT or block_reason() or _voyage() is None or _other_work()
            or c._status_snapshot_block_reason(now) or not c._has_available_partner()
            or c._has_active_nanlong_pending(now)):
        return False
    if now < records().get(kind, {}).get("retry_at", 0):
        return False
    if kind == "voyage_return":
        return c._is_voyage_return_due(now) and not c._is_voyage_return_retry_exhausted(now)
    return c._is_voyage_eligible(now)


async def send(kind, now):
    if not _admitted(kind, now):
        return False
    owner = c._status_query_owner()
    identity_id, identity, account_id = owner
    if c._has_phaseful_summary_window(now, allow_replayable_trigger=True):
        before = copy.deepcopy(identity)
        c._schedule_after(now, c.CONCUBINE_ACTIVE_DEFER_MIN_SEC, c.CONCUBINE_ACTIVE_DEFER_MAX_SEC)
        identity["concubine_voyage_last_error"] = "\u8fdc\u822a\u7b49\u5f85\u95ed\u5173/\u5143\u5a74\u7ed3\u7b97\uff0c\u7a0d\u540e\u5904\u7406"
        c._save_query_projection(owner, before)
        return False
    started_at = max(float(now), c.time.time())
    route = c._preferred_voyage_route() if kind == "voyage" else identity.get("concubine_voyage_route", "")
    record = {
        "op_id": c.uuid4().hex, "kind": kind, "identity_id": identity_id, "account_id": account_id,
        "chat_id": c.get_game_group_id(), "command": _command(kind, route), "started_at": started_at,
        "status": "sending", "msg_id": 0, "plan_key": c._status_query_plan(owner),
        "partner": identity.get("concubine_name"), "route": route,
        "snapshot_at": identity.get("concubine_last_snapshot_at", 0),
        "affinity": identity.get("concubine_affinity", 0), "voyage": _voyage(),
    }
    if tianjige_transport.pavilion_available(identity_id):
        record["transport"] = "miniapp"
    if not _valid_record(kind, record):
        return False
    before = copy.deepcopy(identity)
    c._set_phase(kind + "_pending")
    identity["concubine_voyage_msg_id"] = 0
    identity["next_concubine_time"] = started_at + c.CONCUBINE_VOYAGE_REPLY_TIMEOUT_SEC
    record["plan_key"] = c._status_query_plan(owner)
    _store(record)
    _INFLIGHT[identity_id] = record["op_id"]

    def current():
        if not c._owns_status_query(owner):
            return None
        with c.use_identity(identity_id):
            item = (records() or {}).get(kind)
        return item if item and all(item[key] == record[key] for key in (
            "op_id", "kind", "identity_id", "account_id", "chat_id", "command", "started_at")) else None

    def can_send():
        if not _controls(owner, kind):
            return False
        with c.use_identity(identity_id):
            at = c.time.time()
            return bool(current() == record and c._status_query_plan(owner) == record["plan_key"]
                        and (record.get("transport") != "miniapp" or tianjige_transport.pavilion_available(identity_id))
                        and not _other_work() and not c._has_active_nanlong_pending(at)
                        and not c._has_phaseful_summary_window(at, allow_replayable_trigger=True))

    try:
        if not c._save_query_projection(owner, before):
            return False
        if record.get("transport") == "miniapp":
            return await _send_miniapp(record, owner, current, can_send)
        previous_block = dict(c.classify_game_send_block(identity_id, record["command"]))
        try:
            msg = await c._send_concubine_game_command(
                record["command"], track=True, max_retry=0, reply_timeout=c.CONCUBINE_VOYAGE_REPLY_TIMEOUT_SEC,
                send_as_id=identity_id, target_chat_id=record["chat_id"], source_module=SOURCE,
                op_id=record["op_id"], operation_check=can_send, priority="chain",
            )
        except (asyncio.CancelledError, Exception):
            item = current()
            if item and item["status"] == "sending":
                snapshot = copy.deepcopy(identity)
                _store(dict(item, status="unknown"))
                try:
                    c._save_query_projection(owner, snapshot)
                except Exception as exc:
                    c.console_log(f"Voyage remains unresolved ({type(exc).__name__})")
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
        block = c.classify_game_send_block(identity_id, record["command"]) if not msg else {}
        unchanged = c._status_query_plan(owner) == record["plan_key"]
        if (not msg and block.get("status") == "unsent" and block != previous_block
                and started_at <= (c._query_time(block.get("at")) or 0) <= at + 1):
            retry_at = at + c.random.uniform(c.CONCUBINE_SEND_FAILURE_RETRY_MIN_SEC, c.CONCUBINE_SEND_FAILURE_RETRY_MAX_SEC)
            _store(dict(record, status="unsent", retry_at=retry_at))
            _release_phase(record, unchanged=unchanged)
            if unchanged:
                identity["next_concubine_time"] = retry_at
                identity["concubine_voyage_last_error"] = ""
            c._save_query_projection(owner, snapshot)
            return False
        root, chat = c._query_int(getattr(msg, "id", 0)), c._query_int(getattr(msg, "chat_id", 0))
        sent_at = c._query_time(getattr(msg, "sent_at", 0)) or 0
        dispatch_at = c._query_time(getattr(msg, "send_started_at", 0)) or 0
        known = root > 0 and chat == record["chat_id"] and started_at - 1 <= dispatch_at <= sent_at <= at + 1
        item = dict(record, status="sent" if known else "unknown")
        if known:
            item.update(msg_id=root, sent_at=sent_at, dispatch_at=dispatch_at)
        if unchanged:
            if known:
                identity["concubine_voyage_msg_id"] = root
                identity["next_concubine_time"] = sent_at + c.CONCUBINE_VOYAGE_REPLY_TIMEOUT_SEC
            identity["concubine_voyage_last_error"] = "" if known else "\u8fdc\u822a\u53d1\u9001\u72b6\u6001\u672a\u77e5\uff0c\u4fdd\u7559\u539f\u64cd\u4f5c\u7b49\u5f85\u53cd\u9988"
            item["plan_key"] = c._status_query_plan(owner)
        _store(item)
        return c._save_query_projection(owner, snapshot) and known
    finally:
        if _INFLIGHT.get(identity_id) == record["op_id"]:
            _INFLIGHT.pop(identity_id, None)


async def _send_miniapp(record, owner, current, can_send):
    try:
        response = await tianjige_transport.execute(
            record["identity_id"], record["command"], op_id=record["op_id"], operation_check=can_send,
        )
    except (asyncio.CancelledError, Exception):
        if current() == record:
            before = copy.deepcopy(owner[1])
            _store(dict(record, status="unknown"))
            owner[1]["concubine_voyage_last_error"] = "宝阁远航请求中断，保留原操作，不补发"
            c._save_query_projection(owner, before)
        raise
    if current() != record:
        return False
    now = c.time.time()
    unchanged = c._status_query_plan(owner) == record["plan_key"]
    before = copy.deepcopy(owner[1])
    receipt = response.get("receipt")
    result = None
    if response.get("terminal") and tianjige_transport.receipt_matches(
        receipt, identity_id=record["identity_id"], account_id=record["account_id"],
        op_id=record["op_id"], command=record["command"], started_at=record["started_at"],
    ):
        result = _parse_result(record, response.get("message", ""), receipt["started_at"])
    if result is not None and can_send():
        completed = dict(record, status="complete", result=result, http_receipt=receipt,
                         reply_at=receipt["started_at"], reply_msg_id=0)
        if not _valid_record(record["kind"], completed):
            return False
        _release_phase(record, unchanged=unchanged)
        _apply_result(completed, now, unchanged)
        _store(completed)
        if not c._save_query_projection(owner, before):
            return False
        if result["outcome"] == "returned":
            try:
                await c._send_voyage_result_audit({"status": "idle", "partner": record["partner"],
                                                "route": record["route"], "result": result["text"]})
            except Exception as exc:
                c.console_log(f"Voyage completion saved; audit failed ({type(exc).__name__})")
        return True
    if response.get("action_dispatched") is False:
        retry_at = now + c.CONCUBINE_SEND_FAILURE_RETRY_MAX_SEC
        _store(dict(record, status="unsent", retry_at=retry_at))
        _release_phase(record, unchanged=unchanged)
        if unchanged:
            owner[1]["next_concubine_time"] = retry_at
    else:
        _store(dict(record, status="unknown"))
        if unchanged:
            owner[1]["concubine_voyage_last_error"] = "宝阁远航结果未确认，保留原操作，不补发、不转群命令"
    c._save_query_projection(owner, before)
    return False


def _parse_result(record, text, observed_at):
    text = re.sub(r"[*`]+", "", str(text or "")).strip()
    if not text or len(text) > 4096 or c._is_phaseful_summary_text(text) or c._is_heavenly_ban_text(text):
        return None
    starts, returns = list(c.RE_VOYAGE_START.finditer(text)), list(c.RE_VOYAGE_RETURN.finditer(text))
    status = c._parse_voyage_status_text(text, observed_at)
    requirements = list(c.RE_VOYAGE_AFFINITY_REQUIREMENT.finditer(text))
    shortage = "\u5f00\u542f\u8fdc\u822a\u9700\u8981" in text or ("\u8fdc\u822a" in text and any(
        token in text for token in ("\u7075\u77f3\u4e0d\u8db3", "\u4fee\u4e3a\u4e0d\u8db3", "\u8d44\u6e90\u4e0d\u8db3")))
    no_partner = c._is_no_partner_text(text)
    if sum((len(starts), len(returns), bool(status), len(requirements), shortage, no_partner)) != 1:
        return None
    result = {"outcome": "", "voyage": {}, "affinity_loss": 0, "affinity_gain": 0, "minimum_affinity": 0,
              "applied": False, "affinity_applied": False, "text": text}
    if starts or returns:
        match = (starts or returns)[0]
        if (match["name"].strip() != record["partner"] or match["route"].strip() != record["route"]
                or bool(starts) != (record["kind"] == "voyage")
                or text.count(START_HEAD) + text.count(RETURN_HEAD) != 1
                or text.splitlines()[0] != (START_HEAD if starts else RETURN_HEAD)):
            return None
        if starts:
            due_at = c._parse_wait_due_at(match["wait"].strip().removesuffix("\u540e").strip(), observed_at)
            if due_at is None or due_at <= observed_at or text.count("\u9884\u8ba1\u5f52\u822a\u65f6\u95f4") != 1:
                return None
            return dict(result, outcome="started", voyage={"status": "sailing", "route": record["route"], "return_at": due_at})
        losses = list(c.RE_VOYAGE_AFFINITY_LOSS.finditer(text))
        gains = list(RE_AFFINITY_GAIN.finditer(text))
        if (len(losses) + len(gains) > 1 or ("\u60c5\u7f18\u51cf\u5c11" in text and not losses)
                or ("\u60c5\u7f18\u589e\u52a0" in text and not gains)
                or any(RE_AMOUNT.fullmatch(item["amount"]) is None for item in losses + gains)
                or not re.search(r"\u5411\u4f60\u5448\u4e0a\u6536\u83b7[\uff1a:]", text)
                or not (RE_REWARD.search(text) or losses or gains)):
            return None
        loss = c._parse_count(losses[0]["amount"]) if losses else 0
        gain = c._parse_count(gains[0]["amount"]) if gains else 0
        if not 0 <= loss < 2 ** 63 or not 0 <= gain < 2 ** 63:
            return None
        return dict(result, outcome="returned", voyage={"status": "idle", "route": record["route"], "return_at": 0},
                    affinity_loss=loss, affinity_gain=gain)
    if status:
        if status.get("partner") not in (None, "", record["partner"]) or START_HEAD in text or RETURN_HEAD in text:
            return None
        # A status-like refusal is not proof that settlement produced rewards.
        value = status["status"]
        value = "idle" if value == "no_task" and status.get("clear_idle") is True else value
        voyage = {"status": value, "route": status.get("route") or record["route"], "return_at": status.get("return_at", 0)}
        return dict(result, outcome="status", voyage=voyage) if _valid_voyage(voyage) else None
    if requirements and record["kind"] == "voyage":
        minimum = c._parse_count(requirements[0]["amount"])
        return dict(result, outcome="affinity_shortage", minimum_affinity=minimum) if (
            0 < minimum < 2 ** 63 and RE_AMOUNT.fullmatch(requirements[0]["amount"])) else None
    if shortage and record["kind"] == "voyage":
        return dict(result, outcome="shortage")
    return dict(result, outcome="no_partner") if no_partner else None


def _apply_result(record, now, unchanged):
    result = record["result"]
    same_partner = c._current_partner_matches(record["partner"])
    projection_current = bool(same_partner and _voyage() == record["voyage"]
                              and c.state.get("concubine_last_snapshot_at", 0) == record["snapshot_at"])
    if projection_current and result["voyage"]:
        for key, value in result["voyage"].items():
            c.state["concubine_voyage_" + key] = value
        c.state["concubine_voyage_retry_count"] = 0
        if result["outcome"] == "returned":
            c.state["concubine_voyage_last_result"] = result["text"]
        elif result["outcome"] == "started":
            c.state["concubine_voyage_last_result"] = ""
        result["applied"] = True
    if (projection_current and (result["affinity_loss"] or result["affinity_gain"])
            and c.state.get("concubine_affinity") == record["affinity"]):
        c.state["concubine_affinity"] = max(0, record["affinity"] - result["affinity_loss"]) + result["affinity_gain"]
        result["affinity_applied"] = True
        if (unchanged and result["affinity_loss"] and c.state.get("concubine_kind") == "\u9053\u5fc3\u4f8d\u59be"
                and c.state["concubine_affinity"] < c.CONCUBINE_TIANJI_MIN_AFFINITY):
            c.state["concubine_tianji_last_error"] = "\u8fdc\u822a\u635f\u8017\u60c5\u7f18\uff0c\u7b49\u5f85\u95ee\u5b89/\u8d60\u4e88\u6062\u590d"
    if not unchanged:
        return
    c.state["concubine_voyage_last_error"] = ""
    if result["outcome"] in {"shortage", "affinity_shortage"}:
        record["retry_at"] = now + c.random.uniform(c.CONCUBINE_SEND_FAILURE_RETRY_MIN_SEC, c.CONCUBINE_SEND_FAILURE_RETRY_MAX_SEC)
        c.state["concubine_voyage_last_error"] = result["text"]
    if result["outcome"] in {"affinity_shortage", "no_partner"}:
        c._set_availability("unknown")
        c.state["concubine_last_snapshot_at"] = 0
    if c._is_voyage_sailing(now) or c.state.get("concubine_voyage_status") == "needs_status":
        c._schedule_voyage_wait(now)
    elif c.state.get("concubine_availability") != "available":
        c._schedule_status_recheck(now)
    elif result["outcome"] == "shortage":
        c.state["next_concubine_time"] = record["retry_at"]
    elif result["affinity_applied"] and c._has_affinity_recovery_due(now):
        c._schedule_affinity_recovery(now)
    else:
        c._schedule_chain_action(now)


async def handle_reply(text, now, reply_to, *, current_msg_id=0, current_chat_id=0, observed_at=0, reply_context=None):
    now, at = c._query_time(now), c._query_time(observed_at)
    owner, values = c._status_query_owner(), records()
    context = reply_context if isinstance(reply_context, dict) else {}
    root = c._query_int(getattr(reply_to, "id", 0))
    if (now is None or at is None or at > now or not c._owns_status_query(owner) or values is None
            or c._query_int(context.get("sender_id")) not in c.get_game_bot_ids()):
        return False
    candidates = []
    for record in values.values():
        if (record.get("transport") != "miniapp" and record["status"] in UNRESOLVED and record["account_id"] == owner[2]
                and c._query_int(current_chat_id) == record["chat_id"]):
            candidate = c._adopt_status_query_receipt(record, now, source_module=SOURCE, include_logs=False)
            if root > 0 and root == candidate["msg_id"] and c._query_int(current_chat_id) == candidate["chat_id"]:
                candidates.append(candidate)
    if len(candidates) != 1:
        return False
    record = candidates[0]
    if (c._query_int(context.get("sender_id")) not in c.get_game_bot_ids()
            or c._query_int(getattr(reply_to, "chat_id", 0)) not in (0, record["chat_id"])
            or str(getattr(reply_to, "raw_text", "") or "").strip() not in ("", record["command"])
            or c._query_int(current_msg_id) <= root or at < record["dispatch_at"] - 1):
        return False
    expected = {"send_as_id": record["identity_id"], "account_id": record["account_id"], "chat_id": record["chat_id"],
                "root_msg_id": root, "reply_to_msg_id": root}
    if (any(key in context and c._query_int(context[key]) != value for key, value in expected.items())
            or any(key in context and context[key] != value for key, value in {
                "op_id": record["op_id"], "source_module": SOURCE, "family": "concubine_voyage",
            }.items())
            or (getattr(reply_to, "sender_id", 0) and not c.sender_matches_identity(reply_to.sender_id, record["identity_id"]))
            or context.get("reply_to_command_edited")
            or (context.get("reply_to_command") and context["reply_to_command"] != record["command"])):
        return False
    result = _parse_result(record, text, at)
    if result is None:
        return False
    completed = dict(record, status="complete", reply_at=at, reply_msg_id=current_msg_id, result=result)
    completed.pop("replay_after", None)
    if not _valid_record(record["kind"], completed):
        return False
    unchanged = c._status_query_plan(owner) == record["plan_key"]
    before = copy.deepcopy(owner[1])
    try:
        _release_phase(record, unchanged=unchanged)
        _apply_result(completed, now, unchanged)
        _store(completed)
        _clear_pending(record)
        if not c._save_query_projection(owner, before):
            return False
    except Exception:
        owner[1].clear()
        owner[1].update(before)
        c.mark_dirty()
        raise
    if result["outcome"] == "returned":
        try:
            await c._send_voyage_result_audit({"status": "idle", "partner": record["partner"],
                                               "route": record["route"], "result": result["text"]})
        except Exception as exc:
            c.console_log(f"Voyage completion saved; audit failed ({type(exc).__name__})")
    return True


async def recover(now):
    owner, values = c._status_query_owner(), records()
    if not owner or values is None or c._query_time(now) is None:
        return True
    for record in values.values():
        if record["status"] == "complete" and owner[2] == record["account_id"]:
            before = copy.deepcopy(owner[1])
            _clear_pending(record)
            if owner[1] != before:
                c._save_query_projection(owner, before)
                return True
        if record["status"] not in UNRESOLVED:
            continue
        if record.get("transport") == "miniapp":
            await _recover_miniapp(record, owner, now)
            return True
        if owner[0] in _INFLIGHT or now < record.get("replay_after", record["started_at"]) or owner[2] != record["account_id"]:
            return True
        before = copy.deepcopy(owner[1])
        record = c._adopt_status_query_receipt(record, now, source_module=SOURCE)
        record["replay_after"] = now + c.CONCUBINE_QUERY_REPLAY_SEC
        _store(record)
        if not c._save_query_projection(owner, before):
            return True
        for entry in c._find_owned_concubine_replies(record, now):
            if _parse_result(record, entry.get("text", ""), entry["server_event_at"]) is None:
                continue
            await handle_reply(
                entry.get("text", ""), now,
                c.SimpleNamespace(id=record["msg_id"], chat_id=record["chat_id"], raw_text=record["command"]),
                current_msg_id=entry["message_id"], current_chat_id=record["chat_id"], observed_at=entry["server_event_at"],
                reply_context={"sender_id": entry["sender_id"]},
            )
            break
        return True
    return bool(block_reason())


def _reconciled_voyage(record, text, observed_at):
    parsed = c._parse_voyage_status_text(text, observed_at)
    if not parsed or parsed.get("partner") not in (None, "", record["partner"]):
        return None
    if record["kind"] == "voyage":
        if parsed.get("status") not in {"sailing", "returned"} or parsed.get("route") != record["route"]:
            return None
        voyage = {"status": parsed["status"], "route": record["route"], "return_at": parsed.get("return_at", 0)}
        return voyage if _valid_voyage(voyage) and voyage["return_at"] > 0 else None
    if parsed.get("status") == "no_task" and parsed.get("clear_idle") is True:
        return {"status": "idle", "route": record["route"], "return_at": 0}
    return None


async def _recover_miniapp(record, owner, now):
    # A fresh status can reconcile business state, not reconstruct lost rewards.
    identity_id = owner[0]
    earliest = record["started_at"] + HTTP_RECONCILE_INTERVAL_SEC
    if (identity_id in _INFLIGHT or now < max(earliest, record.get("replay_after", 0))
            or owner[2] != record["account_id"] or not _controls(owner, record["kind"])
            or not tianjige_transport.available(identity_id)):
        return

    def current():
        if not c._owns_status_query(owner):
            return False
        with c.use_identity(identity_id):
            return bool(_controls(owner, record["kind"])
                        and tianjige_transport.available(identity_id)
                        and (records() or {}).get(record["kind"]) == record
                        and c._status_query_plan(owner) == record["plan_key"]
                        and c._current_partner_matches(record["partner"])
                        and _voyage() == record["voyage"]
                        and c.state.get("concubine_last_snapshot_at", 0) == record["snapshot_at"]
                        and not _other_work())

    if not current():
        return
    before = copy.deepcopy(owner[1])
    probe = {"op_id": c.uuid4().hex, "started_at": max(now, c.time.time())}
    record = dict(record, reconciliation=probe, replay_after=probe["started_at"] + HTTP_RECONCILE_INTERVAL_SEC)
    _store(record)
    if not c._save_query_projection(owner, before):
        return
    _INFLIGHT[identity_id] = probe["op_id"]
    try:
        response = await tianjige_transport.execute(
            identity_id, c.CMD_CONCUBINE_VOYAGE_STATUS, op_id=probe["op_id"], operation_check=current,
        )
        if not current() or response.get("terminal") is not True:
            return
        receipt = response.get("receipt")
        if not tianjige_transport.receipt_matches(
            receipt, identity_id=identity_id, account_id=owner[2], op_id=probe["op_id"],
            command=c.CMD_CONCUBINE_VOYAGE_STATUS, started_at=probe["started_at"],
        ):
            return
        text = response.get("message", "")
        voyage = _reconciled_voyage(record, text, receipt["started_at"])
        if voyage is None:
            return
        completed = dict(record, status="reconciled", reconciliation=dict(probe, receipt=receipt, text=text, voyage=voyage))
        if not _valid_record(record["kind"], completed):
            return
        before = copy.deepcopy(owner[1])
        try:
            _release_phase(record, unchanged=True)
            c._apply_voyage_snapshot(voyage, c.time.time())
            if record["kind"] == "voyage_return":
                # The missing settlement might have changed affinity; require a fresh panel.
                c._set_availability("unknown")
                c.state["concubine_last_snapshot_at"] = 0
                c.state["concubine_voyage_last_result"] = ""
                c._schedule_status_recheck(c.time.time())
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
        c.console_log(f"Voyage status reconciliation deferred ({type(exc).__name__})")
    finally:
        if _INFLIGHT.get(identity_id) == probe["op_id"]:
            _INFLIGHT.pop(identity_id, None)
