import hashlib
import time

_runtime_event_claims = {}
_runtime_message_consumed = {}
_runtime_log_claims = {}


def _gc_runtime_event_claims(now=None):
    now = float(now if now is not None else time.time())
    expired_keys = [key for key, expires_at in _runtime_event_claims.items() if float(expires_at or 0) <= now]
    for key in expired_keys:
        _runtime_event_claims.pop(key, None)


def _claim_runtime_event(event, *, scope, ttl=120.0):
    msg_id = int(getattr(event, "id", 0) or 0)
    chat_id = int(getattr(event, "chat_id", 0) or 0)
    if msg_id <= 0 or chat_id == 0:
        return True
    now = time.time()
    _gc_runtime_event_claims(now)
    claim_key = f"{scope}:{chat_id}:{msg_id}"
    if float(_runtime_event_claims.get(claim_key, 0) or 0) > now:
        return False
    _runtime_event_claims[claim_key] = now + float(ttl or 0)
    return True


def _claim_runtime_semantic_event(text, *, scope, ttl=120.0):
    normalized = "\n".join(
        line.strip()
        for line in str(text or "").splitlines()
        if line.strip()
    )
    if not normalized:
        return True
    now = time.time()
    _gc_runtime_event_claims(now)
    text_hash = hashlib.blake2s(
        normalized.encode("utf-8", "surrogatepass"),
        digest_size=12,
    ).hexdigest()
    claim_key = f"semantic:{scope}:{text_hash}"
    if float(_runtime_event_claims.get(claim_key, 0) or 0) > now:
        return False
    _runtime_event_claims[claim_key] = now + float(ttl or 0)
    return True


def _gc_runtime_message_consumed(now=None):
    now = float(now if now is not None else time.time())
    expired_keys = [key for key, expires_at in _runtime_message_consumed.items() if float(expires_at or 0) <= now]
    for key in expired_keys:
        _runtime_message_consumed.pop(key, None)


def _gc_runtime_log_claims(now=None):
    now = float(now if now is not None else time.time())
    expired_keys = [key for key, expires_at in _runtime_log_claims.items() if float(expires_at or 0) <= now]
    for key in expired_keys:
        _runtime_log_claims.pop(key, None)


def _claim_runtime_log_event(event, *, event_type, ttl=120.0):
    msg_id = int(getattr(event, "id", 0) or 0)
    chat_id = int(getattr(event, "chat_id", 0) or 0)
    event_type = str(event_type or "message").strip() or "message"
    if msg_id <= 0 or chat_id == 0:
        return True
    now = time.time()
    _gc_runtime_log_claims(now)
    claim_key = f"{event_type}:{chat_id}:{msg_id}"
    if event_type.endswith("edit"):
        raw_text = str(getattr(event, "raw_text", "") or "")
        text_hash = hashlib.blake2s(raw_text.encode("utf-8", "surrogatepass"), digest_size=8).hexdigest()
        claim_key = f"{claim_key}:{text_hash}"
    if float(_runtime_log_claims.get(claim_key, 0) or 0) > now:
        return False
    _runtime_log_claims[claim_key] = now + float(ttl or 0)
    return True


def _get_runtime_message_consumed_key(event, family):
    msg_id = int(getattr(event, "id", 0) or 0)
    chat_id = int(getattr(event, "chat_id", 0) or 0)
    family = str(family or "").strip()
    if msg_id <= 0 or chat_id == 0 or not family:
        return ""
    return f"{family}:{chat_id}:{msg_id}"


def _has_runtime_message_consumed(event, family):
    claim_key = _get_runtime_message_consumed_key(event, family)
    if not claim_key:
        return False
    now = time.time()
    _gc_runtime_message_consumed(now)
    return float(_runtime_message_consumed.get(claim_key, 0) or 0) > now


def _mark_runtime_message_consumed(event, family, *, ttl=120.0):
    claim_key = _get_runtime_message_consumed_key(event, family)
    if not claim_key:
        return
    now = time.time()
    _gc_runtime_message_consumed(now)
    _runtime_message_consumed[claim_key] = now + float(ttl or 0)

def _get_event_reply_header_msg_id(event):
    reply_header = getattr(event, "reply_to", None)
    return int(getattr(reply_header, "reply_to_msg_id", 0) or 0)


__all__ = [
    "_claim_runtime_event",
    "_claim_runtime_semantic_event",
    "_claim_runtime_log_event",
    "_get_event_reply_header_msg_id",
    "_has_runtime_message_consumed",
    "_mark_runtime_message_consumed",
]
