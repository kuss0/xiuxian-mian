"""Owned dream/puzzle mutations; unknown sends are never read-timeout retries."""

import asyncio
import copy
import re

from . import concubine as c
from . import tianjige_transport


STATE_KEY = "concubine_fragment_actions"
SOURCE = "concubine_fragments"
KINDS = {"dream", "puzzle"}
UNRESOLVED = {"sending", "sent", "unknown"}
_INFLIGHT = {}
HTTP_RECONCILE_INTERVAL_SEC = 1800
HTTP_EVENT_CLOCK_SKEW_SEC = 5
RE_DREAM_HEAD = re.compile(r"^\u3010\u5165\u68a6\u5bfb\u56fe\u3011$", re.MULTILINE)
RE_DREAM_BROADCAST = re.compile(r"^\u3010\u5168\u7fa4\u5f02\u95fb\u00b7(?P<kind>\u865a\u5929|\u82cd\u5764)\u6b8b\u56fe\u3011$", re.MULTILINE)
RE_PUZZLE_HEAD = re.compile(r"^\u3010(?P<kind>\u865a\u5929|\u82cd\u5764)\u6b8b\u56fe\u00b7\u62fc\u5408\u6210\u529f\u3011$", re.MULTILINE)
RE_PUZZLE_BROADCAST = re.compile(r"^\u3010\u5168\u7fa4\u5e7f\u64ad\u00b7(?P<kind>\u865a\u5929|\u82cd\u5764)\u6b8b\u56fe\u62fc\u5408\u3011$", re.MULTILINE)
RE_TYPED_PROGRESS = re.compile(
    r"(?P<kind>\u865a\u5929|\u82cd\u5764)\s*(?:\u6b8b\u56fe)?(?:\u62fc\u7247|\u8fdb\u5ea6)?\s*(?:\u5df2\u81f3)?\s*[\uff1a:]?\s*"
    r"(?P<count>[-+0-9.]+)\s*/\s*(?P<total>[-+0-9.]+)"
)
RE_PROGRESS = re.compile(
    r"(?:\u62fc\u7247\u8fdb\u5ea6|\u5f53\u524d\u8fdb\u5ea6|\u6b8b\u56fe\u8fdb\u5ea6\u5df2\u81f3)\s*[\uff1a:]?\s*"
    r"(?P<count>[-+0-9.]+)\s*/\s*(?P<total>[-+0-9.]+)"
)
RE_KIND = re.compile(r"\u3010(?P<kind>\u865a\u5929|\u82cd\u5764)\u6b8b\u56fe\u3011")
RE_WAIT = re.compile(r"\u8bf7\u5728\s*(?P<wait>[^\n\u3002]+?)\s*\u540e\u518d\u8bd5")


def _command(kind):
    return c.CMD_CONCUBINE_DREAM if kind == "dream" else c.CMD_CONCUBINE_PUZZLE


def _zero_time(value):
    return type(value) in {int, float} and (value == 0 or c._query_time(value) is not None)


def _valid_fragments(value, *, full=False):
    return bool(isinstance(value, dict) and not (value.keys() - set(c.FRAGMENT_KIND_ORDER))
                and (not full or value.keys() == set(c.FRAGMENT_KIND_ORDER))
                and all(isinstance(parts, list) and len(parts) == 2
                        and type(parts[0]) is int and 0 <= parts[0] <= 4
                        and type(parts[1]) is int and parts[1] == 4 for parts in value.values()))


def _fragments():
    values = {kind: [c.state.get(keys[0], 0), c.state.get(keys[1], 4)] for kind, keys in c.FRAGMENT_FIELDS.items()}
    return values if _valid_fragments(values, full=True) else None


def _snapshot_consumed(record, snapshot, observed_at=None):
    if not tianjige_transport.fragment_snapshot_valid(snapshot, partner=record.get("partner")):
        return []
    threshold = record["started_at"] - HTTP_EVENT_CLOCK_SKEW_SEC
    ceiling = ((c._query_time(observed_at) or float("inf")) + HTTP_EVENT_CLOCK_SKEW_SEC)
    if record["kind"] == "dream":
        return ["dream"] if threshold <= snapshot["last_dream_at"] <= ceiling else []
    completed = set(record["confirmation_key"].split("|"))
    return [kind for kind in c.FRAGMENT_KIND_ORDER
            if f"{kind}:4/4" in completed and threshold <= snapshot["last_puzzle_at"][kind] <= ceiling]


def _valid_record(kind, record):
    fields = {"op_id", "kind", "identity_id", "account_id", "chat_id", "command", "started_at", "status", "msg_id",
              "plan_key", "partner", "snapshot_at", "fragments", "dream_due_at", "parent_op_id", "confirmation_key", "confirmed_at"}
    optional = {"sent_at", "dispatch_at", "reply_at", "reply_msg_id", "replay_after", "retry_at", "result",
                "transport", "http_receipt", "reconciliation"}
    if not isinstance(record, dict) or fields - record.keys() or record.keys() - fields - optional:
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
                    op_id=record["op_id"], command=record["command"], started_at=record["started_at"]
            ) or record.get("reply_at") != record["http_receipt"]["started_at"]:
                return False
        elif "http_receipt" in record:
            return False
    elif "http_receipt" in record or "reconciliation" in record:
        return False
    if (
        record["kind"] != kind or record["command"] != _command(kind)
        or c._query_int(record["identity_id"]) != c.get_current_identity_id()
        or c._query_int(record["account_id"]) <= 0 or not c._query_int(record["chat_id"])
        or not isinstance(record["op_id"], str) or re.fullmatch(r"[a-f0-9]{32}", record["op_id"]) is None
        or not isinstance(record["plan_key"], str) or re.fullmatch(r"[a-f0-9]{64}", record["plan_key"]) is None
        or not isinstance(record["partner"], str) or not 0 < len(record["partner"].strip()) <= 120
        or not _valid_fragments(record["fragments"], full=True)
        or not _zero_time(record["snapshot_at"]) or not _zero_time(record["dream_due_at"])
        or c._query_time(record["started_at"]) is None or record["snapshot_at"] > record["started_at"]
        or type(record["msg_id"]) is not int or not 0 <= record["msg_id"] < 2 ** 63
        or not isinstance(record["status"], str) or record["status"] not in UNRESOLVED | {"unsent", "complete", "reconciled"}
        or any(c._query_time(record[key]) is None for key in ("sent_at", "dispatch_at", "reply_at", "replay_after", "retry_at") if key in record)
        or (not http and record["status"] in {"sent", "complete"} and not record["msg_id"])
        or (record["status"] in {"unsent", "sending"} and record["msg_id"])
        or (bool(record["msg_id"]) != ("sent_at" in record and "dispatch_at" in record))
        or (not record["msg_id"] and bool({"sent_at", "dispatch_at"} & record.keys()))
        or (record["msg_id"] and not record["started_at"] - 1 <= record.get("dispatch_at", 0) <= record.get("sent_at", 0))
    ):
        return False
    if kind == "dream":
        if (record["parent_op_id"] != "" or record["confirmation_key"] != ""
                or not _zero_time(record["confirmed_at"]) or record["confirmed_at"] != 0):
            return False
    else:
        key = "|".join(f"{name}:4/4" for name in c.FRAGMENT_KIND_ORDER if record["fragments"][name] == [4, 4])
        if (not isinstance(record["parent_op_id"], str) or re.fullmatch(r"[a-f0-9]{32}", record["parent_op_id"]) is None
                or not key or record["confirmation_key"] != key or c._query_time(record["confirmed_at"]) is None
                or not 0 <= record["started_at"] - record["confirmed_at"] <= c.CONCUBINE_PANEL_REUSE_MAX_AGE_SEC):
            return False
    if "reconciliation" in record or record["status"] == "reconciled":
        probe = record.get("reconciliation")
        if (not http or record["status"] not in UNRESOLVED | {"reconciled"}
                or not isinstance(probe, dict) or not isinstance(probe.get("op_id"), str)
                or re.fullmatch(r"[a-f0-9]{32}", probe["op_id"]) is None
                or probe["op_id"] == record["op_id"] or c._query_time(probe.get("started_at")) is None
                or probe["started_at"] <= record["started_at"]):
            return False
        if record["status"] == "reconciled":
            if (probe.keys() != {"op_id", "started_at", "receipt", "snapshot", "consumed"}
                    or not tianjige_transport.pavilion_receipt_matches(
                        probe["receipt"], identity_id=record["identity_id"], account_id=record["account_id"],
                        op_id=probe["op_id"], started_at=probe["started_at"])
                    or not tianjige_transport.fragment_snapshot_valid(probe["snapshot"], partner=record["partner"])
                    or not isinstance(probe["consumed"], list) or not probe["consumed"]
                    or probe["consumed"] != _snapshot_consumed(
                        record, probe["snapshot"], probe["receipt"]["started_at"])):
                return False
        elif probe.keys() != {"op_id", "started_at"}:
            return False
    if record["status"] != "complete":
        return not ({"result", "reply_at", "reply_msg_id"} & record.keys())
    result = record.get("result")
    valid = bool(
        (http or c._query_int(record.get("reply_msg_id")) > record["msg_id"])
        and c._query_time(record.get("reply_at")) is not None
        and record["reply_at"] >= record.get("dispatch_at", record["started_at"]) - 1
        and isinstance(result, dict) and result.keys() == {"outcome", "partner", "progresses", "wait_until", "applied", "text"}
        and isinstance(result["outcome"], str)
        and result["outcome"] in ({"dream", "cooldown", "shortage", "no_partner", "voyage_lock"} if kind == "dream" else
                                  {"puzzle", "incomplete", "no_partner", "voyage_lock"})
        and isinstance(result["partner"], str) and len(result["partner"]) <= 120
        and _valid_fragments(result["progresses"]) and _zero_time(result["wait_until"])
        and type(result["applied"]) is bool and isinstance(result["text"], str) and len(result["text"]) <= 4096
    )
    if not valid or (result["applied"] and result["outcome"] not in {"dream", "puzzle"}):
        return False
    parsed = _parse_result(record, result["text"], record["reply_at"])
    return parsed is not None and dict(parsed, applied=result["applied"]) == result


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
    if any(c._phase() == kind + "_pending" or c.state.get(f"concubine_{kind}_msg_id") for kind in KINDS):
        return "legacy_pending"
    return ""


def next_dream_at():
    values = records()
    if values is None:
        return float("inf")
    due_at = c.state.get("concubine_dream_due_at", 0)
    if not _zero_time(due_at):
        return float("inf")
    return max(float(due_at), float(values.get("dream", {}).get("retry_at", 0)))


def _clear_pending(record):
    if record.get("transport") == "miniapp":
        return
    c._clear_status_query_pending(record, source_module=SOURCE, family="concubine_" + record["kind"])


def _release_phase(record, *, unchanged):
    return c._release_owned_concubine_phase(record, f"concubine_{record['kind']}_msg_id", unchanged=unchanged)


def _ready_puzzle(now):
    if not c._is_current_fragment_confirmed(now):
        return None
    parent = c._status_query_record()
    previous = (records() or {}).get("puzzle", {})
    if previous.get("parent_op_id") == parent["op_id"] and previous.get("status") != "unsent":
        return None
    return parent


def _admitted(kind, now):
    owner = c._status_query_owner()
    if (kind not in KINDS or c._query_time(now) is None
            or not c._owns_status_query(owner, sending=True, kind="fragment") or not c.get_game_group_id()
            or owner[0] in _INFLIGHT or block_reason() or not c._has_available_partner()
            or c._status_snapshot_block_reason(now, allow_fragment_ready=kind == "puzzle")
            or c._has_voyage_runtime_state(now) or c._has_active_nanlong_pending(now)):
        return False
    previous = records().get(kind, {})
    if now < previous.get("retry_at", 0):
        return False
    return (next_dream_at() <= now and not c._is_puzzle_ready()) if kind == "dream" else _ready_puzzle(now) is not None


async def send(kind, now):
    if not _admitted(kind, now):
        return False
    owner = c._status_query_owner()
    identity_id, identity, account_id = owner
    if c._has_phaseful_summary_window(now, allow_replayable_trigger=kind == "dream"):
        before = copy.deepcopy(identity)
        c._schedule_after(now, c.CONCUBINE_ACTIVE_DEFER_MIN_SEC, c.CONCUBINE_ACTIVE_DEFER_MAX_SEC)
        label = "\u5165\u68a6\u5bfb\u56fe" if kind == "dream" else "\u6b8b\u56fe\u62fc\u5408"
        identity["concubine_last_error"] = f"{label}\u7b49\u5f85\u95ed\u5173/\u5143\u5a74\u7ed3\u7b97\uff0c\u7a0d\u540e\u5904\u7406"
        c._save_query_projection(owner, before)
        return False
    parent = _ready_puzzle(now) if kind == "puzzle" else None
    started_at = max(float(now), c.time.time())
    record = {
        "op_id": c.uuid4().hex, "kind": kind, "identity_id": identity_id, "account_id": account_id,
        "chat_id": c.get_game_group_id(), "command": _command(kind), "started_at": started_at,
        "status": "sending", "msg_id": 0, "plan_key": c._status_query_plan(owner),
        "partner": identity["concubine_name"], "snapshot_at": identity.get("concubine_last_snapshot_at", 0),
        "fragments": _fragments(), "dream_due_at": identity.get("concubine_dream_due_at", 0),
        "parent_op_id": parent["op_id"] if parent else "", "confirmation_key": parent["confirmation_key"] if parent else "",
        "confirmed_at": parent["reply_at"] if parent else 0,
    }
    if tianjige_transport.pavilion_available(identity_id):
        record["transport"] = "miniapp"
    if not _valid_record(kind, record):
        return False
    before = copy.deepcopy(identity)
    c._set_phase(kind + "_pending")
    identity[f"concubine_{kind}_msg_id"] = 0
    identity["next_concubine_time"] = started_at + c.CONCUBINE_PHASE_TIMEOUT_SEC
    record["plan_key"] = c._status_query_plan(owner)
    _store(record)
    _INFLIGHT[identity_id] = record["op_id"]

    def current():
        if not c._owns_status_query(owner):
            return None
        with c.use_identity(identity_id):
            values = records()
        item = (values or {}).get(kind)
        return item if item and all(item[key] == record[key] for key in (
            "op_id", "kind", "identity_id", "account_id", "chat_id", "command", "started_at")) else None

    def can_send():
        if not c._owns_status_query(owner, sending=True, kind="fragment"):
            return False
        with c.use_identity(identity_id):
            at = c.time.time()
            query = c._status_query_record()
            return bool(current() == record and c._status_query_plan(owner) == record["plan_key"]
                        and (record.get("transport") != "miniapp" or tianjige_transport.pavilion_available(identity_id))
                        and query is not None and (not query or query["status"] not in c.CONCUBINE_QUERY_UNRESOLVED)
                        and not c.affinity_actions.block_reason() and not c.voyage_actions.block_reason()
                        and not c.divination_actions.block_reason() and not c.heart_actions.block_reason()
                        and not c.reacquire_actions.block_reason()
                        and not c.external_events.needs_calibration()
                        and not c._has_active_nanlong_pending(at)
                        and not c._has_phaseful_summary_window(at, allow_replayable_trigger=kind == "dream")
                        and (kind == "dream" or (
                            c._is_current_fragment_confirmed(at)
                            and (c._status_query_record() or {}).get("op_id") == record["parent_op_id"]
                            and c.state.get("concubine_fragment_confirm_key") == record["confirmation_key"]
                            and c.state.get("concubine_fragment_confirmed_at") == record["confirmed_at"])))

    try:
        if not c._save_query_projection(owner, before):
            return False
        if record.get("transport") == "miniapp":
            return await _send_miniapp(record, owner, current, can_send)
        previous_block = dict(c.classify_game_send_block(identity_id, record["command"]))
        try:
            msg = await c._send_concubine_game_command(
                record["command"], track=True, max_retry=0, reply_timeout=c.CONCUBINE_PHASE_TIMEOUT_SEC,
                send_as_id=identity_id, target_chat_id=record["chat_id"], source_module=SOURCE,
                op_id=record["op_id"], operation_check=can_send, **({"priority": "chain"} if kind == "puzzle" else {}),
            )
        except (asyncio.CancelledError, Exception):
            item = current()
            if item and item["status"] == "sending":
                snapshot = copy.deepcopy(identity)
                _store(dict(item, status="unknown"))
                try:
                    c._save_query_projection(owner, snapshot)
                except Exception as exc:
                    c.console_log(f"Fragment mutation remains unresolved ({type(exc).__name__})")
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
                identity["concubine_last_error"] = ""
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
                identity[f"concubine_{kind}_msg_id"] = root
                identity["next_concubine_time"] = sent_at + c.CONCUBINE_PHASE_TIMEOUT_SEC
            identity["concubine_last_error"] = "" if known else "\u5165\u68a6/\u62fc\u56fe\u53d1\u9001\u72b6\u6001\u672a\u77e5\uff0c\u4fdd\u7559\u539f\u64cd\u4f5c\u7b49\u5f85\u53cd\u9988"
            item["plan_key"] = c._status_query_plan(owner)
        _store(item)
        saved = c._save_query_projection(owner, snapshot)
        return saved and known
    finally:
        if _INFLIGHT.get(identity_id) == record["op_id"]:
            _INFLIGHT.pop(identity_id, None)


def _parse_dream_progress(text, broadcast):
    kinds = {match["kind"] for match in RE_KIND.finditer(text)}
    if broadcast:
        kinds.add(broadcast["kind"])
    if len(kinds) != 1:
        return None
    label = next(iter(kinds))
    matches = list(RE_TYPED_PROGRESS.finditer(text)) + list(RE_PROGRESS.finditer(text))
    counts = set()
    for match in matches:
        if match.groupdict().get("kind", label) != label or not re.fullmatch(r"[0-4]", match["count"]) or match["total"] != "4":
            return None
        counts.add(int(match["count"]))
    if len(counts) != 1:
        return None
    return {c._normalize_fragment_kind(label): [next(iter(counts)), 4]}


def _parse_result(record, text, observed_at):
    text = re.sub(r"[*`]+", "", str(text or "")).strip()
    if not text or len(text) > 4096 or c._is_phaseful_summary_text(text) or c._is_heavenly_ban_text(text):
        return None
    dream_heads, broadcasts = list(RE_DREAM_HEAD.finditer(text)), list(RE_DREAM_BROADCAST.finditer(text))
    puzzle_heads = list(RE_PUZZLE_HEAD.finditer(text)) + list(RE_PUZZLE_BROADCAST.finditer(text))
    cooldown = c._is_dream_cooldown_text(text)
    shortage = any(token in text for token in ("\u4fee\u4e3a\u4e0d\u8db3", "\u7075\u77f3\u4e0d\u8db3", "\u8d44\u6e90\u4e0d\u8db3"))
    no_partner, voyage_lock = c._is_no_partner_text(text), c._is_voyage_lock_text(text)
    incomplete = "\u6b8b\u56fe\u5c1a\u672a\u9f50\u5168" in text
    if sum((len(dream_heads), len(broadcasts), len(puzzle_heads), cooldown, shortage, no_partner, voyage_lock, incomplete)) != 1:
        return None
    result = {"outcome": "", "partner": "", "progresses": {}, "wait_until": 0, "applied": False, "text": text}
    if dream_heads or broadcasts:
        if record["kind"] != "dream":
            return None
        partners = list(c.RE_DREAM_PARTNER.finditer(text))
        if dream_heads and len(partners) != 1:
            return None
        progress = _parse_dream_progress(text, broadcasts[0] if broadcasts else None)
        if progress is None:
            return None
        return dict(result, outcome="dream", partner=partners[0]["name"].strip() if partners else record["partner"],
                    progresses=progress, wait_until=observed_at + c.CONCUBINE_DREAM_CD_SEC + c.CD_BUFFER_SEC)
    if puzzle_heads:
        kind = c._normalize_fragment_kind(puzzle_heads[0]["kind"])
        if record["kind"] != "puzzle" or f"{kind}:4/4" not in record["confirmation_key"].split("|"):
            return None
        return dict(result, outcome="puzzle", partner=record["partner"], progresses={kind: [0, 4]})
    if cooldown:
        waits = list(RE_WAIT.finditer(text))
        due_at = c._parse_wait_due_at(waits[0]["wait"], observed_at) if len(waits) == 1 else None
        if record["kind"] != "dream" or due_at is None or due_at <= observed_at:
            return None
        return dict(result, outcome="cooldown", wait_until=due_at)
    if voyage_lock:
        voyage = c._parse_voyage_rejection(text, observed_at)
        if not voyage or voyage["partner"] not in ("", record["partner"]):
            return None
        return dict(result, outcome="voyage_lock", partner=voyage["partner"], wait_until=voyage["return_at"])
    if no_partner:
        return dict(result, outcome="no_partner")
    if shortage and record["kind"] == "dream":
        return dict(result, outcome="shortage")
    if incomplete and record["kind"] == "puzzle":
        return dict(result, outcome="incomplete")
    return None


def _apply_result(record, now, unchanged):
    result = record["result"]
    projection_current = bool(
        c._current_partner_matches(record["partner"]) and result["partner"] in ("", record["partner"])
        and _fragments() == record["fragments"] and c.state.get("concubine_last_snapshot_at", 0) == record["snapshot_at"]
    )
    if projection_current:
        if result["outcome"] in {"dream", "puzzle"}:
            c._apply_fragment_progresses(result["progresses"])
            c._clear_fragment_confirmation()
            result["applied"] = True
        if result["outcome"] in {"dream", "cooldown"}:
            if c.state.get("concubine_dream_due_at", 0) == record["dream_due_at"]:
                c.state["concubine_dream_due_at"] = result["wait_until"]
            c.reset_resource_shortage(c.CONCUBINE_DREAM_RESOURCE_KEY)
    if not unchanged:
        return
    c.state["concubine_last_error"] = ""
    if result["outcome"] == "shortage":
        backoff = c.record_resource_shortage(c.CONCUBINE_DREAM_RESOURCE_KEY, now, reason=result["text"])
        record["retry_at"] = backoff["next_at"]
        c.state["concubine_last_error"] = "\u5165\u68a6\u8d44\u6e90\u4e0d\u8db3\uff0c\u7b49\u5f85\u8d44\u6e90\u6062\u590d"
    elif result["outcome"] == "voyage_lock" and projection_current:
        c._apply_voyage_snapshot({"status": "sailing", "return_at": result["wait_until"]}, now)
    elif result["outcome"] == "incomplete":
        c._clear_fragment_confirmation()
    elif result["outcome"] == "no_partner" or not projection_current:
        c._set_availability("unknown")
        c.state["concubine_last_snapshot_at"] = 0


async def handle_reply(kind, text, now, reply_to, *, current_msg_id=0, current_chat_id=0, observed_at=0, reply_context=None):
    now, at = c._query_time(now), c._query_time(observed_at)
    owner, values = c._status_query_owner(), records()
    record = (values or {}).get(kind)
    context = reply_context if isinstance(reply_context, dict) else {}
    root = c._query_int(getattr(reply_to, "id", 0))
    if (now is None or at is None or at > now or not c._owns_status_query(owner)
            or not record or record.get("transport") == "miniapp"
            or record["status"] not in UNRESOLVED or owner[2] != record["account_id"]
            or c._query_int(context.get("sender_id")) not in c.get_game_bot_ids()
            or c._query_int(current_chat_id) != record["chat_id"]
            or c._query_int(getattr(reply_to, "chat_id", 0)) not in (0, record["chat_id"])
            or str(getattr(reply_to, "raw_text", "") or "").strip() not in ("", record["command"])):
        return False
    expected = {"send_as_id": record["identity_id"], "account_id": record["account_id"], "chat_id": record["chat_id"],
                "root_msg_id": root, "reply_to_msg_id": root}
    if (any(key in context and c._query_int(context[key]) != value for key, value in expected.items())
            or any(key in context and context[key] != value for key, value in {
                "op_id": record["op_id"], "source_module": SOURCE, "family": "concubine_" + kind,
            }.items())
            or (getattr(reply_to, "sender_id", 0) and not c.sender_matches_identity(reply_to.sender_id, record["identity_id"]))
            or context.get("reply_to_command_edited")
            or (context.get("reply_to_command") and context["reply_to_command"] != record["command"])):
        return False
    record = c._adopt_status_query_receipt(record, now, source_module=SOURCE)
    if record["msg_id"] <= 0 or root != record["msg_id"] or c._query_int(current_msg_id) <= root or at < record["dispatch_at"] - 1:
        return False
    result = _parse_result(record, text, at)
    if result is None:
        return False
    completed = dict(record, status="complete", reply_at=at, reply_msg_id=current_msg_id, result=result)
    completed.pop("replay_after", None)
    if not _valid_record(kind, completed):
        return False
    unchanged = c._status_query_plan(owner) == record["plan_key"]
    before = copy.deepcopy(owner[1])
    try:
        _release_phase(record, unchanged=unchanged)
        _apply_result(completed, now, unchanged)
        _store(completed)
        _clear_pending(record)
        if unchanged:
            if c.state.get("concubine_availability") != "available":
                c._schedule_status_recheck(now)
            elif c._is_voyage_sailing(now):
                c._schedule_voyage_wait(now)
            else:
                c._schedule_after_tianji(now)
        if not c._save_query_projection(owner, before):
            return False
    except Exception:
        owner[1].clear()
        owner[1].update(before)
        c.mark_dirty()
        raise
    return True


async def recover(now):
    owner, values = c._status_query_owner(), records()
    if not owner or values is None or c._query_time(now) is None:
        return True
    for kind, record in values.items():
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
                kind, entry.get("text", ""), now,
                c.SimpleNamespace(id=record["msg_id"], chat_id=record["chat_id"], raw_text=record["command"]),
                current_msg_id=entry["message_id"], current_chat_id=record["chat_id"], observed_at=entry["server_event_at"],
                reply_context={"sender_id": entry["sender_id"]},
            )
            break
        return True
    return bool(block_reason())


async def _send_miniapp(record, owner, current, can_send):
    try:
        response = await tianjige_transport.execute(
            record["identity_id"], record["command"], op_id=record["op_id"], operation_check=can_send,
        )
    except (asyncio.CancelledError, Exception):
        if current() == record:
            before = copy.deepcopy(owner[1])
            _store(dict(record, status="unknown"))
            c._save_query_projection(owner, before)
        raise
    if current() != record:
        return False
    now = c.time.time()
    unchanged = c._status_query_plan(owner) == record["plan_key"]
    before = copy.deepcopy(owner[1])
    receipt = response.get("receipt")
    result = None
    if response.get("terminal") is True and tianjige_transport.receipt_matches(
        receipt, identity_id=record["identity_id"], account_id=record["account_id"],
        op_id=record["op_id"], command=record["command"], started_at=record["started_at"],
    ):
        result = _parse_result(record, response.get("message", ""), receipt["started_at"])
    try:
        if result is not None and can_send():
            completed = dict(record, status="complete", result=result, http_receipt=receipt,
                             reply_at=receipt["started_at"], reply_msg_id=0)
            if not _valid_record(record["kind"], completed):
                return False
            _release_phase(record, unchanged=True)
            _apply_result(completed, now, True)
            _store(completed)
            return c._save_query_projection(owner, before)
        if response.get("action_dispatched") is False:
            retry_at = now + c.CONCUBINE_SEND_FAILURE_RETRY_MAX_SEC
            _store(dict(record, status="unsent", retry_at=retry_at))
            _release_phase(record, unchanged=unchanged)
            if unchanged:
                owner[1]["next_concubine_time"] = retry_at
        else:
            _store(dict(record, status="unknown"))
            if unchanged:
                owner[1]["concubine_last_error"] = "宝阁入梦/拼图结果未确认，仅核验状态，不补发、不转群命令"
        c._save_query_projection(owner, before)
        return False
    except Exception:
        owner[1].clear()
        owner[1].update(before)
        c.mark_dirty()
        raise


def _apply_reconciled_snapshot(record, snapshot, now, observed_at):
    consumed = _snapshot_consumed(record, snapshot, observed_at)
    if not consumed:
        return False
    c._apply_fragment_progresses(snapshot["fragments"])
    c._clear_fragment_confirmation()
    if record["kind"] == "dream":
        c.state["concubine_dream_due_at"] = max(
            now, snapshot["last_dream_at"] + c.CONCUBINE_DREAM_CD_SEC + c.CD_BUFFER_SEC,
        )
        c.reset_resource_shortage(c.CONCUBINE_DREAM_RESOURCE_KEY)
    c.state["concubine_last_error"] = ""
    if c._is_puzzle_ready():
        c._schedule_chain_action(now)
    else:
        c._schedule_after_tianji(now)
    return True


async def _recover_miniapp(record, owner, now):
    identity_id = owner[0]
    if (identity_id in _INFLIGHT
            or now < max(record["started_at"] + HTTP_RECONCILE_INTERVAL_SEC, record.get("replay_after", 0))
            or owner[2] != record["account_id"] or not tianjige_transport.available(identity_id)):
        return

    def current():
        if not c._owns_status_query(owner, sending=True, kind="fragment"):
            return False
        with c.use_identity(identity_id):
            values = records()
            item = (values or {}).get(record["kind"])
            return bool(item == record and tianjige_transport.available(identity_id)
                        and c._status_query_plan(owner) == record["plan_key"]
                        and c._current_partner_matches(record["partner"])
                        and _fragments() == record["fragments"])

    if not current():
        return
    before = copy.deepcopy(owner[1])
    probe = {"op_id": c.uuid4().hex, "started_at": max(now, c.time.time())}
    record = dict(record, reconciliation=probe,
                  replay_after=probe["started_at"] + HTTP_RECONCILE_INTERVAL_SEC)
    _store(record)
    if not c._save_query_projection(owner, before):
        return
    _INFLIGHT[identity_id] = probe["op_id"]
    try:
        response = await tianjige_transport.read_pavilion(
            identity_id, partner=record["partner"], op_id=probe["op_id"],
            operation_check=current, projection="fragments",
        )
        if not current() or response.get("ok") is not True:
            return
        receipt, snapshot = response.get("receipt"), response.get("fragments")
        if (not tianjige_transport.pavilion_receipt_matches(
                receipt, identity_id=identity_id, account_id=owner[2],
                op_id=probe["op_id"], started_at=probe["started_at"])
                or not tianjige_transport.fragment_snapshot_valid(snapshot, partner=record["partner"])):
            return
        consumed = _snapshot_consumed(record, snapshot, receipt["started_at"])
        if not consumed:
            return
        completed = dict(record, status="reconciled", reconciliation=dict(
            probe, receipt=receipt, snapshot=snapshot, consumed=consumed,
        ))
        if not _valid_record(record["kind"], completed):
            return
        before = copy.deepcopy(owner[1])
        try:
            _release_phase(record, unchanged=True)
            if not _apply_reconciled_snapshot(record, snapshot, c.time.time(), receipt["started_at"]):
                return
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
        c.console_log(f"Fragment Pavilion reconciliation deferred ({type(exc).__name__})")
    finally:
        if _INFLIGHT.get(identity_id) == probe["op_id"]:
            _INFLIGHT.pop(identity_id, None)
