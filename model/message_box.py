import copy
import hashlib
import json
import os
import tempfile
import time
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from types import SimpleNamespace

from .verified_event import VerifiedGameEvent, clean_event_type, delivery_kind_for_event_type


def _safe_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _clean_event_type(value):
    return clean_event_type(value)


def _event_revision_rank(event_type):
    return 2 if _clean_event_type(event_type) == "edit" else 1


def _text_hash(text):
    raw = str(text or "")
    if not raw:
        return ""
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _event_reply_header(event):
    return getattr(event, "reply_to", None)


def _event_reply_to_msg_id(event):
    reply_header = _event_reply_header(event)
    return (
        _safe_int(getattr(reply_header, "reply_to_msg_id", 0))
        or _safe_int(getattr(event, "reply_to_msg_id", 0))
    )


def _event_topic_id(event):
    reply_header = _event_reply_header(event)
    return (
        _safe_int(getattr(reply_header, "reply_to_top_id", 0))
        or _safe_int(getattr(reply_header, "forum_topic_id", 0))
        or 0
    )


@dataclass(frozen=True)
class MessageFact:
    event_type: str
    chat_id: int
    msg_id: int
    sender_id: int
    raw_text: str
    text_hash: str = ""
    reply_to_msg_id: int = 0
    reply_to_sender_id: int = 0
    root_msg_id: int = 0
    topic_id: int = 0
    is_game_group: bool = False
    is_game_bot: bool = False
    reply_context: dict = field(default_factory=dict)
    identity_id: int = 0
    family: str = ""
    route_source: str = ""
    source: str = "telegram_event"
    ingest_seq: int = 0

    @property
    def is_edit(self):
        return _clean_event_type(self.event_type) == "edit"

    @property
    def delivery_kind(self):
        return delivery_kind_for_event_type(self.event_type)

    @property
    def is_new_delivery(self):
        return self.delivery_kind == "New"

    @property
    def is_edited_delivery(self):
        return self.delivery_kind == "Edited"

    def dedupe_key(self):
        return (
            _clean_event_type(self.event_type),
            self.chat_id,
            self.msg_id,
            self.text_hash,
        )

    def to_verified_game_event(self):
        return VerifiedGameEvent(
            event_type=_clean_event_type(self.event_type),
            chat_id=self.chat_id,
            msg_id=self.msg_id,
            sender_id=self.sender_id,
            text=self.raw_text,
            reply_context=dict(self.reply_context),
            identity_id=self.identity_id,
            family=self.family,
            root_msg_id=self.root_msg_id,
            route_source=self.route_source,
            reply_to_sender_id=self.reply_to_sender_id,
        )


@dataclass(frozen=True)
class LegacyReplyAdapter:
    text: str
    reply_to: object
    reply_context: dict
    matched_family: str


class MessageBoxSnapshot:
    def __init__(self, chat_id, facts_by_msg_id, deliveries_by_seq):
        self.chat_id = int(chat_id or 0)
        self._facts_by_msg_id = OrderedDict(
            (msg_id, copy.deepcopy(fact))
            for msg_id, fact in sorted(facts_by_msg_id.items())
        )
        self._deliveries_by_seq = OrderedDict(
            (seq, copy.deepcopy(fact))
            for seq, fact in sorted(deliveries_by_seq.items())
        )

    def __len__(self):
        return len(self._facts_by_msg_id)

    def is_empty(self):
        return not self._facts_by_msg_id

    def get(self, msg_id):
        fact = self._facts_by_msg_id.get(_safe_int(msg_id))
        return copy.deepcopy(fact) if fact is not None else None

    def head(self):
        if not self._facts_by_msg_id:
            return 0
        return next(reversed(self._facts_by_msg_id))

    def head_seq(self):
        if not self._deliveries_by_seq:
            return 0
        return next(reversed(self._deliveries_by_seq))

    def scan_after(self, cursor=None):
        cursor_id = _safe_int(cursor)
        for msg_id, fact in self._facts_by_msg_id.items():
            if cursor is not None and msg_id <= cursor_id:
                continue
            yield copy.deepcopy(fact)

    def scan_after_seq(self, cursor=None, *, include_edits=False):
        cursor_seq = _safe_int(cursor)
        for seq, fact in self._deliveries_by_seq.items():
            if cursor is not None and seq <= cursor_seq:
                continue
            if fact.is_edit and not include_edits:
                continue
            yield copy.deepcopy(fact)


class MessageBox:
    def __init__(self, chat_id=0, cap=5000):
        cap = int(cap or 0)
        if cap <= 0:
            raise ValueError("message box cap must be > 0")
        self.chat_id = _safe_int(chat_id)
        self.cap = cap
        self._facts_by_msg_id = OrderedDict()
        self._deliveries_by_seq = OrderedDict()
        self._dedupe_keys = set()
        self._next_seq = 1
        self._last_evicted_msg_id = 0
        self._last_evicted_seq = 0

    def __len__(self):
        return len(self._facts_by_msg_id)

    def is_empty(self):
        return not self._facts_by_msg_id

    @property
    def last_evicted_msg_id(self):
        return self._last_evicted_msg_id

    @property
    def last_evicted_seq(self):
        return self._last_evicted_seq

    def upsert(self, fact):
        if self.chat_id and fact.chat_id != self.chat_id:
            return False
        normalized = replace(
            fact,
            event_type=_clean_event_type(fact.event_type),
            text_hash=fact.text_hash or _text_hash(fact.raw_text),
        )
        key = normalized.dedupe_key()
        if key in self._dedupe_keys:
            return False

        previous = self._facts_by_msg_id.get(normalized.msg_id)
        if previous is not None and _event_revision_rank(previous.event_type) > _event_revision_rank(normalized.event_type):
            return False

        seq = self._next_seq
        self._next_seq += 1
        delivery = replace(normalized, ingest_seq=seq)
        if previous is not None:
            self._facts_by_msg_id.pop(normalized.msg_id, None)
            self._dedupe_keys.discard(previous.dedupe_key())
        self._facts_by_msg_id[delivery.msg_id] = copy.deepcopy(delivery)
        self._facts_by_msg_id = OrderedDict(sorted(self._facts_by_msg_id.items()))
        self._deliveries_by_seq[seq] = copy.deepcopy(delivery)
        self._dedupe_keys.add(key)

        while len(self._deliveries_by_seq) > self.cap:
            evicted_seq, evicted = self._deliveries_by_seq.popitem(last=False)
            self._dedupe_keys.discard(evicted.dedupe_key())
            if self._facts_by_msg_id.get(evicted.msg_id) == evicted:
                self._facts_by_msg_id.pop(evicted.msg_id, None)
                self._last_evicted_msg_id = evicted.msg_id
            self._last_evicted_seq = evicted_seq
        return True

    def get(self, msg_id):
        fact = self._facts_by_msg_id.get(_safe_int(msg_id))
        return copy.deepcopy(fact) if fact is not None else None

    def head(self):
        if not self._facts_by_msg_id:
            return 0
        return next(reversed(self._facts_by_msg_id))

    def head_seq(self):
        if not self._deliveries_by_seq:
            return 0
        return next(reversed(self._deliveries_by_seq))

    def scan_after(self, cursor=None):
        return self.snapshot().scan_after(cursor)

    def scan_after_seq(self, cursor=None, *, include_edits=False):
        return self.snapshot().scan_after_seq(cursor, include_edits=include_edits)

    def snapshot(self):
        return MessageBoxSnapshot(self.chat_id, self._facts_by_msg_id, self._deliveries_by_seq)


def build_message_fact_from_event(
    event,
    text=None,
    reply_context=None,
    *,
    reply_to=None,
    event_type="message",
    is_game_group=False,
    is_game_bot=False,
    source="telegram_event",
):
    context = dict(reply_context) if isinstance(reply_context, dict) else {}
    raw_text = str(text if text is not None else getattr(event, "raw_text", "") or "")
    normalized_event_type = _clean_event_type(event_type)
    reply_to_msg_id = (
        _safe_int(context.get("reply_to_msg_id"))
        or _safe_int(getattr(reply_to, "id", 0))
        or _event_reply_to_msg_id(event)
    )
    reply_to_sender_id = (
        _safe_int(context.get("reply_to_sender_id"))
        or _safe_int(getattr(reply_to, "sender_id", 0))
    )
    root_msg_id = _safe_int(context.get("root_msg_id")) or reply_to_msg_id
    identity_id = _safe_int(context.get("send_as_id"))
    family = str(context.get("family") or "").strip()
    route_source = f"{normalized_event_type}:reply_context"

    return MessageFact(
        event_type=normalized_event_type,
        chat_id=_safe_int(getattr(event, "chat_id", 0)),
        msg_id=_safe_int(getattr(event, "id", 0)),
        sender_id=_safe_int(getattr(event, "sender_id", 0)),
        raw_text=raw_text,
        text_hash=_text_hash(raw_text),
        reply_to_msg_id=reply_to_msg_id,
        reply_to_sender_id=reply_to_sender_id,
        root_msg_id=root_msg_id,
        topic_id=_event_topic_id(event),
        is_game_group=bool(is_game_group),
        is_game_bot=bool(is_game_bot),
        reply_context=context,
        identity_id=identity_id,
        family=family,
        route_source=route_source,
        source=str(source or "telegram_event"),
    )


def build_message_fact_from_fixture(sample, *, chat_id=0, msg_id=0, sender_id=0):
    raw_text = str(getattr(sample, "text", "") or "")
    event_type = _clean_event_type(getattr(sample, "event_type", "message"))
    family = str(getattr(sample, "family", "") or "").strip()
    context = {"family": family} if family else {}
    return MessageFact(
        event_type=event_type,
        chat_id=_safe_int(chat_id),
        msg_id=_safe_int(msg_id),
        sender_id=_safe_int(sender_id),
        raw_text=raw_text,
        text_hash=_text_hash(raw_text),
        reply_to_msg_id=0,
        reply_to_sender_id=0,
        root_msg_id=0,
        topic_id=0,
        is_game_group=False,
        is_game_bot=False,
        reply_context=context,
        identity_id=0,
        family=family,
        route_source=f"{event_type}:fixture" if family else event_type,
        source=str(getattr(sample, "source", "") or "replay_fixture"),
    )


def message_fact_to_dict(fact):
    return {
        "event_type": _clean_event_type(fact.event_type),
        "delivery_kind": fact.delivery_kind,
        "chat_id": _safe_int(fact.chat_id),
        "msg_id": _safe_int(fact.msg_id),
        "sender_id": _safe_int(fact.sender_id),
        "raw_text": str(fact.raw_text or ""),
        "text_hash": str(fact.text_hash or _text_hash(fact.raw_text)),
        "reply_to_msg_id": _safe_int(fact.reply_to_msg_id),
        "reply_to_sender_id": _safe_int(fact.reply_to_sender_id),
        "root_msg_id": _safe_int(fact.root_msg_id),
        "topic_id": _safe_int(fact.topic_id),
        "is_game_group": bool(fact.is_game_group),
        "is_game_bot": bool(fact.is_game_bot),
        "reply_context": dict(fact.reply_context) if isinstance(fact.reply_context, dict) else {},
        "identity_id": _safe_int(fact.identity_id),
        "family": str(fact.family or ""),
        "route_source": str(fact.route_source or ""),
        "source": str(fact.source or ""),
        "ingest_seq": _safe_int(fact.ingest_seq),
    }


def message_fact_from_dict(row):
    row = row if isinstance(row, dict) else {}
    raw_text = str(row.get("raw_text") if row.get("raw_text") is not None else row.get("text") or "")
    return MessageFact(
        event_type=str(row.get("event_type") or "message"),
        chat_id=_safe_int(row.get("chat_id")),
        msg_id=_safe_int(row.get("msg_id") or row.get("message_id")),
        sender_id=_safe_int(row.get("sender_id")),
        raw_text=raw_text,
        text_hash=str(row.get("text_hash") or ""),
        reply_to_msg_id=_safe_int(row.get("reply_to_msg_id")),
        reply_to_sender_id=_safe_int(row.get("reply_to_sender_id")),
        root_msg_id=_safe_int(row.get("root_msg_id")),
        topic_id=_safe_int(row.get("topic_id")),
        is_game_group=bool(row.get("is_game_group")),
        is_game_bot=bool(row.get("is_game_bot")),
        reply_context=row.get("reply_context") if isinstance(row.get("reply_context"), dict) else {},
        identity_id=_safe_int(row.get("identity_id")),
        family=str(row.get("family") or ""),
        route_source=str(row.get("route_source") or ""),
        source=str(row.get("source") or "shadow_json"),
        ingest_seq=_safe_int(row.get("ingest_seq")),
    )


def build_message_box_snapshot_payload(snapshot, *, include_edits=True, limit=None, now=None):
    if hasattr(snapshot, "snapshot"):
        snapshot = snapshot.snapshot()
    facts = list(snapshot.scan_after_seq(None, include_edits=include_edits))
    if limit is not None:
        safe_limit = max(0, int(limit or 0))
        if safe_limit:
            facts = facts[-safe_limit:]
        else:
            facts = []
    return {
        "schema": "xiuxian.message_box.shadow.v1",
        "created_at": float(now if now is not None else time.time()),
        "chat_id": _safe_int(getattr(snapshot, "chat_id", 0)),
        "head_msg_id": _safe_int(snapshot.head()),
        "head_seq": _safe_int(snapshot.head_seq()),
        "include_edits": bool(include_edits),
        "fact_count": len(facts),
        "facts": [message_fact_to_dict(fact) for fact in facts],
    }


def write_message_box_snapshot_payload(path, payload):
    target_path = os.path.abspath(os.fspath(path))
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".message_box_shadow.", suffix=".tmp", dir=os.path.dirname(target_path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=False, separators=(",", ":"))
            fp.write("\n")
        os.replace(tmp_path, target_path)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass
    return target_path


def adapt_message_fact_for_legacy_reply(fact, *, reply_to=None):
    if reply_to is None and fact.reply_to_msg_id:
        reply_to = SimpleNamespace(
            id=fact.reply_to_msg_id,
            sender_id=fact.reply_to_sender_id,
            raw_text="",
        )
    return LegacyReplyAdapter(
        text=fact.raw_text,
        reply_to=reply_to,
        reply_context=dict(fact.reply_context),
        matched_family=fact.family or None,
    )


def shadow_compare_verified_event(fact, verified):
    return {
        "event_type": fact.event_type == verified.event_type,
        "chat_id": fact.chat_id == verified.chat_id,
        "msg_id": fact.msg_id == verified.msg_id,
        "sender_id": fact.sender_id == verified.sender_id,
        "text": fact.raw_text == verified.text,
        "identity_id": fact.identity_id == verified.identity_id,
        "family": fact.family == verified.family,
        "root_msg_id": fact.root_msg_id == verified.root_msg_id,
        "route_source": fact.route_source == verified.route_source,
        "reply_to_sender_id": fact.reply_to_sender_id == verified.reply_to_sender_id,
    }


__all__ = [
    "LegacyReplyAdapter",
    "MessageBox",
    "MessageBoxSnapshot",
    "MessageFact",
    "adapt_message_fact_for_legacy_reply",
    "build_message_fact_from_event",
    "build_message_fact_from_fixture",
    "build_message_box_snapshot_payload",
    "message_fact_to_dict",
    "message_fact_from_dict",
    "shadow_compare_verified_event",
    "write_message_box_snapshot_payload",
]
