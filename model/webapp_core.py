import hashlib
import hmac
import json
import os
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, urljoin, urlparse


SENSITIVE_WEBAPP_QUERY_KEYS = {
    "tgWebAppData",
    "initData",
    "query_id",
    "hash",
    "user",
    "signature",
}
START_PARAM_QUERY_KEYS = (
    "startapp",
    "start_param",
    "startattach",
    "tgWebAppStartParam",
)
DEFAULT_TELEGRAM_WEBAPP_HOSTS = ("t.me", "telegram.me")
DEFAULT_MINIAPP_INIT_DATA_TTL_SEC = 10 * 60
DEFAULT_MINIAPP_HTTP_BACKOFF_SEC = (1.0, 2.0, 4.0)
DEFAULT_MINIAPP_REQUEST_MIN_INTERVAL_SEC = 1.0
DEFAULT_MINIAPP_REQUEST_MAX_PER_RUN = 32
DEFAULT_MINIAPP_REQUEST_MAX_ATTEMPTS = 2
DEFAULT_MINIAPP_REQUEST_MAX_CONSECUTIVE_FAILURES = 2
DEFAULT_MINIAPP_GLOBAL_REQUEST_LIMIT = 90
DEFAULT_MINIAPP_GLOBAL_WINDOW_SEC = 60.0
MINIAPP_CAPTURE_MAX_TEXT = 400
# 形状摘要只用于定位协议结构变化，深层细节由 body_digest 兜底。深度/宽度都要有
# 上限：洞府 start 这类响应会带回整棵账号树，不设限时单条 capture 可达 22KiB。
MINIAPP_CAPTURE_SHAPE_MAX_DEPTH = 2
MINIAPP_CAPTURE_LIST_SAMPLE_LIMIT = 1
MINIAPP_CAPTURE_SHAPE_MAX_KEYS = 12
MINIAPP_CAPTURE_RETENTION_DAYS = 7
MINIAPP_HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
MINIAPP_LOCAL_METHODS = {"LOCAL", "TELEGRAM"}


class MiniAppGlobalRateLimiter:
    """Process-wide sliding-window limiter with a priority lease."""

    def __init__(self, limit=DEFAULT_MINIAPP_GLOBAL_REQUEST_LIMIT, window_sec=DEFAULT_MINIAPP_GLOBAL_WINDOW_SEC):
        self.limit = max(1, int(limit))
        self.window_sec = max(1.0, float(window_sec))
        self._lock = threading.Lock()
        self._request_times = deque()
        self._priority_leases = set()

    def begin_priority(self, owner):
        owner = str(owner or "").strip()
        if owner:
            with self._lock:
                self._priority_leases.add(owner)

    def end_priority(self, owner):
        owner = str(owner or "").strip()
        if owner:
            with self._lock:
                self._priority_leases.discard(owner)

    def acquire(self, *, priority=False, clock=None, sleeper=None):
        clock = clock or time.monotonic
        sleeper = sleeper or time.sleep
        total_delay = 0.0
        while True:
            now = float(clock())
            with self._lock:
                cutoff = now - self.window_sec
                while self._request_times and self._request_times[0] <= cutoff:
                    self._request_times.popleft()
                priority_blocked = bool(self._priority_leases) and not priority
                if not priority_blocked and len(self._request_times) < self.limit:
                    self._request_times.append(now)
                    return total_delay
                if priority_blocked:
                    delay = 0.1
                else:
                    delay = max(0.01, self._request_times[0] + self.window_sec - now)
            sleeper(delay)
            total_delay += delay

    def snapshot(self):
        with self._lock:
            cutoff = time.monotonic() - self.window_sec
            while self._request_times and self._request_times[0] <= cutoff:
                self._request_times.popleft()
            return {
                "limit": self.limit,
                "window_sec": self.window_sec,
                "request_count": len(self._request_times),
                "priority_active": bool(self._priority_leases),
                "priority_owners": sorted(self._priority_leases),
            }


_GLOBAL_MINIAPP_RATE_LIMITER = MiniAppGlobalRateLimiter()


def begin_miniapp_priority_window(owner):
    _GLOBAL_MINIAPP_RATE_LIMITER.begin_priority(owner)


def end_miniapp_priority_window(owner):
    _GLOBAL_MINIAPP_RATE_LIMITER.end_priority(owner)


def get_miniapp_global_rate_limit_snapshot():
    return _GLOBAL_MINIAPP_RATE_LIMITER.snapshot()


RE_SENSITIVE_QUERY_ASSIGNMENT = re.compile(
    r"(?P<key>tgWebAppData|initData|query_id|hash|user|signature|token|startapp|start_param)=([^&#\s]+)",
    re.IGNORECASE,
)
RE_MINIAPP_START_TOKEN = re.compile(
    r"\b(?P<kind>fish|farm|boss|qyz|nqb|rpt|stk|trial|df|tree|fate)[_-][A-Za-z0-9_-]{4,}\b",
    re.IGNORECASE,
)
RE_WEBAPP_URL = re.compile(
    r"(?:https?|tg)://[^\s<>'\"）)]+|(?:t\.me|telegram\.me)/[^\s<>'\"）)]+",
    re.IGNORECASE,
)
RE_SECRET_HEADER_ASSIGNMENT = re.compile(
    r"\b(?P<key>authorization|proxy-authorization|cookie|set-cookie|x-telegram-bot-api-secret-token)\s*[:=]\s*(?P<value>[^\s,;]+(?:\s+[^\s,;]+)?)",
    re.IGNORECASE,
)
RE_BEARER_SECRET = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{6,}", re.IGNORECASE)
SENSITIVE_MINIAPP_EVENT_KEYWORDS = (
    "tgwebappdata",
    "initdata",
    "init_data",
    "query_id",
    "hash",
    "signature",
    "user",
    "token",
    "startapp",
    "start_param",
)


def _string(value):
    return str(value or "").strip()


def _parse_url(value):
    raw_url = _string(value)
    parsed = urlparse(raw_url)
    if not parsed.netloc and parsed.scheme == "" and raw_url:
        parsed = urlparse("//" + raw_url)
    return parsed


def _url_host(url):
    return _parse_url(url).netloc.lower()


def _url_hostname(url):
    return (_parse_url(url).hostname or "").lower()


def _url_origin(url):
    parsed = _parse_url(url)
    if not parsed.scheme or not parsed.netloc:
        return _string(url).rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}"


def _digest(value):
    raw_value = _string(value)
    if not raw_value:
        return ""
    return hashlib.blake2s(raw_value.encode("utf-8", "surrogatepass"), digest_size=8).hexdigest()


def _summarize_start_param(value):
    raw_value = _string(value)
    if not raw_value:
        return {}
    match = re.match(r"^(?P<kind>[A-Za-z0-9]{1,16})[_-]", raw_value)
    kind = match.group("kind").lower() if match else ""
    return {
        "present": True,
        "kind": kind,
        "suffix": raw_value[-4:],
        "digest": _digest(raw_value),
    }


def _redact_miniapp_start_token_match(match):
    kind = str(match.group("kind") or "").lower()
    raw_value = str(match.group(0) or "")
    suffix = raw_value.split("_", 1)[1] if "_" in raw_value else ""
    # MiniApp app errors use stable lowercase snake_case values such as
    # trial_invalid_proof. Keep those visible so captures remain diagnosable.
    if suffix in {"title", "type"}:
        return raw_value
    if "_" in suffix and re.fullmatch(r"[a-z_]+", suffix):
        return raw_value
    return f"{kind}_<redacted>"


def sanitize_webapp_secret_text(text, *, limit=220):
    raw_text = str(text or "")
    sanitized = RE_SECRET_HEADER_ASSIGNMENT.sub(
        lambda match: f"{match.group('key')}: <redacted>",
        raw_text,
    )
    sanitized = RE_SENSITIVE_QUERY_ASSIGNMENT.sub(
        lambda match: f"{match.group('key')}=<redacted>",
        sanitized,
    )
    sanitized = RE_BEARER_SECRET.sub(
        "Bearer <redacted>",
        sanitized,
    )
    sanitized = RE_MINIAPP_START_TOKEN.sub(
        _redact_miniapp_start_token_match,
        sanitized,
    )
    return sanitized[: max(0, int(limit or 0))]


def _is_sensitive_event_key(key):
    normalized = str(key or "").lower()
    return any(keyword in normalized for keyword in SENSITIVE_MINIAPP_EVENT_KEYWORDS)


def _safe_event_value(key, value):
    if value is None:
        return None
    key_text = str(key or "")
    if _is_sensitive_event_key(key_text):
        return {"present": bool(_string(value)), "digest": _digest(value)}
    if "url" in key_text.lower():
        return {"host": _url_host(value), "digest": _digest(value)}
    if isinstance(value, dict):
        return {str(item_key): _safe_event_value(item_key, item_value) for item_key, item_value in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_event_value(key_text, item) for item in value]
    if isinstance(value, (int, float, bool)):
        return value
    return sanitize_webapp_secret_text(value, limit=120)


def safe_miniapp_event_detail(detail):
    return {str(key): _safe_event_value(key, value) for key, value in dict(detail or {}).items()}


def _parse_init_data_pairs(init_data):
    return [(str(key), str(value)) for key, value in parse_qsl(_string(init_data), keep_blank_values=True)]


def parse_miniapp_init_data(init_data):
    """Parse raw Telegram.WebApp.initData as a decoded key/value dict."""
    fields = {}
    for key, value in _parse_init_data_pairs(init_data):
        if key:
            fields[key] = value
    return fields


def build_miniapp_init_data_check_string(init_data):
    pairs = []
    seen = set()
    duplicates = []
    for key, value in _parse_init_data_pairs(init_data):
        if not key:
            continue
        if key in seen:
            duplicates.append(key)
        seen.add(key)
        if key == "hash":
            continue
        pairs.append(f"{key}={value}")
    if duplicates:
        raise ValueError(f"duplicate initData field: {', '.join(sorted(set(duplicates)))}")
    return "\n".join(sorted(pairs))


def sign_miniapp_init_data(init_data, bot_token):
    data_check_string = build_miniapp_init_data_check_string(init_data)
    secret_key = hmac.new(b"WebAppData", _string(bot_token).encode("utf-8"), hashlib.sha256).digest()
    return hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class MiniAppInitDataValidation:
    ok: bool
    fields: dict = field(default_factory=dict, repr=False)
    auth_date: int = 0
    age_sec: float = 0.0
    error: str = ""
    error_type: str = ""
    data_check_string_digest: str = ""
    hash_digest: str = ""

    def safe_summary(self):
        summary = {
            "ok": bool(self.ok),
            "field_keys": sorted(str(key) for key in self.fields),
            "has_user": "user" in self.fields,
            "has_query_id": "query_id" in self.fields,
            "has_signature": "signature" in self.fields,
            "auth_date": int(self.auth_date or 0),
            "age_sec": max(0, int(self.age_sec or 0)),
            "data_check_string_digest": self.data_check_string_digest,
            "hash_digest": self.hash_digest,
        }
        if self.error:
            summary["error"] = sanitize_webapp_secret_text(self.error)
        if self.error_type:
            summary["error_type"] = self.error_type
        return summary


def validate_miniapp_init_data(
    init_data,
    bot_token,
    *,
    max_age_sec=DEFAULT_MINIAPP_INIT_DATA_TTL_SEC,
    max_future_skew_sec=60,
    now=None,
):
    raw_init_data = _string(init_data)
    fields = parse_miniapp_init_data(raw_init_data)
    hash_value = _string(fields.get("hash"))
    try:
        data_check_string = build_miniapp_init_data_check_string(raw_init_data)
    except ValueError as exc:
        return MiniAppInitDataValidation(False, fields, error=str(exc), error_type="invalid_format")

    hash_digest = _digest(hash_value)
    check_digest = _digest(data_check_string)
    if not raw_init_data:
        return MiniAppInitDataValidation(False, fields, error="initData is empty", error_type="missing", data_check_string_digest=check_digest, hash_digest=hash_digest)
    if not hash_value:
        return MiniAppInitDataValidation(False, fields, error="initData hash missing", error_type="missing", data_check_string_digest=check_digest, hash_digest=hash_digest)
    if not _string(bot_token):
        return MiniAppInitDataValidation(False, fields, error="bot token missing", error_type="missing", data_check_string_digest=check_digest, hash_digest=hash_digest)

    expected = sign_miniapp_init_data(raw_init_data, bot_token)
    if not hmac.compare_digest(expected, hash_value.lower()):
        return MiniAppInitDataValidation(False, fields, error="initData hash mismatch", error_type="signature", data_check_string_digest=check_digest, hash_digest=hash_digest)

    auth_date = 0
    age_sec = 0.0
    if max_age_sec is not None and float(max_age_sec or 0) > 0:
        raw_auth_date = _string(fields.get("auth_date"))
        if not raw_auth_date:
            return MiniAppInitDataValidation(False, fields, error="initData auth_date missing", error_type="expired", data_check_string_digest=check_digest, hash_digest=hash_digest)
        try:
            auth_date = int(raw_auth_date)
        except (TypeError, ValueError, OverflowError):
            return MiniAppInitDataValidation(False, fields, error="initData auth_date invalid", error_type="invalid_format", data_check_string_digest=check_digest, hash_digest=hash_digest)
        now_value = float(now if now is not None else time.time())
        age_sec = now_value - float(auth_date)
        if age_sec > float(max_age_sec):
            return MiniAppInitDataValidation(False, fields, auth_date, age_sec, "initData expired", "expired", check_digest, hash_digest)
        if age_sec < -float(max_future_skew_sec or 0):
            return MiniAppInitDataValidation(False, fields, auth_date, age_sec, "initData auth_date is in the future", "expired", check_digest, hash_digest)

    return MiniAppInitDataValidation(True, fields, auth_date, age_sec, data_check_string_digest=check_digest, hash_digest=hash_digest)


def extract_miniapp_init_data_from_url(url):
    parsed = _parse_url(url)
    for raw_part in (parsed.fragment, parsed.query):
        values = parse_qs(raw_part, keep_blank_values=True).get("tgWebAppData") if raw_part else []
        if values:
            return values[0]
    return ""


@dataclass(frozen=True)
class MiniAppRequestPolicy:
    min_interval_sec: float = DEFAULT_MINIAPP_REQUEST_MIN_INTERVAL_SEC
    max_requests_per_run: int = DEFAULT_MINIAPP_REQUEST_MAX_PER_RUN
    max_attempts_per_request: int = DEFAULT_MINIAPP_REQUEST_MAX_ATTEMPTS
    max_consecutive_failures: int = DEFAULT_MINIAPP_REQUEST_MAX_CONSECUTIVE_FAILURES

    def __post_init__(self):
        object.__setattr__(self, "min_interval_sec", max(0.0, float(self.min_interval_sec or 0)))
        object.__setattr__(self, "max_requests_per_run", max(1, int(self.max_requests_per_run or 1)))
        object.__setattr__(self, "max_attempts_per_request", max(1, int(self.max_attempts_per_request or 1)))
        object.__setattr__(self, "max_consecutive_failures", max(1, int(self.max_consecutive_failures or 1)))

    def safe_summary(self):
        return {
            "min_interval_sec": self.min_interval_sec,
            "max_requests_per_run": self.max_requests_per_run,
            "max_attempts_per_request": self.max_attempts_per_request,
            "max_consecutive_failures": self.max_consecutive_failures,
        }


class MiniAppRequestBudget:
    """Per-flow request budget; callers create one budget for one identity run."""

    def __init__(self, policy=None, *, clock=None, sleeper=None):
        self.policy = policy if isinstance(policy, MiniAppRequestPolicy) else MiniAppRequestPolicy(**dict(policy or {}))
        self.clock = clock or time.monotonic
        self.sleeper = sleeper or time.sleep
        self.request_count = 0
        self.consecutive_failures = 0
        self.last_request_at = None

    def acquire(self):
        if self.request_count >= self.policy.max_requests_per_run:
            return False, 0.0, "request_budget_exhausted"
        if self.consecutive_failures >= self.policy.max_consecutive_failures:
            return False, 0.0, "consecutive_failure_limit"

        now = float(self.clock())
        delay = 0.0
        if self.last_request_at is not None:
            delay = max(0.0, self.policy.min_interval_sec - (now - self.last_request_at))
        if delay > 0:
            self.sleeper(delay)
            now = float(self.clock())
        self.request_count += 1
        self.last_request_at = now
        return True, delay, ""

    def note_result(self, result):
        if bool(getattr(result, "ok", False)):
            self.consecutive_failures = 0
        else:
            self.consecutive_failures += 1

    def safe_summary(self):
        return {
            **self.policy.safe_summary(),
            "request_count": int(self.request_count),
            "consecutive_failures": int(self.consecutive_failures),
        }


@dataclass
class MiniAppAdapter:
    game_key: str
    label: str
    bot_username: str = ""
    allowed_bot_username_patterns: tuple[str, ...] = ()
    webview_url: str = ""
    api_base_url: str = ""
    allowed_web_hosts: tuple[str, ...] = DEFAULT_TELEGRAM_WEBAPP_HOSTS
    allowed_api_hosts: tuple[str, ...] = ()
    allowed_api_paths: tuple[str, ...] = ()
    endpoints: dict[str, str] = field(default_factory=dict)
    start_param_pattern: str = ""
    platform: str = "android"
    default_enabled: bool = False
    manual_only: bool = True
    request_policy: MiniAppRequestPolicy = field(default_factory=MiniAppRequestPolicy)

    def __post_init__(self):
        self.game_key = _string(self.game_key)
        self.label = _string(self.label)
        self.bot_username = _string(self.bot_username).lstrip("@")
        self.allowed_bot_username_patterns = tuple(_string(item) for item in (self.allowed_bot_username_patterns or ()) if _string(item))
        self.webview_url = _string(self.webview_url)
        self.api_base_url = _string(self.api_base_url).rstrip("/")
        self.allowed_web_hosts = tuple(_string(item).lower() for item in (self.allowed_web_hosts or ()) if _string(item))
        self.allowed_api_hosts = tuple(_string(item).lower() for item in (self.allowed_api_hosts or ()) if _string(item))
        self.allowed_api_paths = tuple(_string(item) for item in (self.allowed_api_paths or ()) if _string(item))
        self.endpoints = {str(key): _string(value) for key, value in dict(self.endpoints or {}).items() if _string(key) and _string(value)}
        self.start_param_pattern = _string(self.start_param_pattern)
        self.platform = _string(self.platform) or "android"
        self.request_policy = self.request_policy if isinstance(self.request_policy, MiniAppRequestPolicy) else MiniAppRequestPolicy(**dict(self.request_policy or {}))

    def api_endpoint(self, key_or_path):
        raw = _string(key_or_path)
        return self.endpoints.get(raw, raw)

    def safe_summary(self):
        return {
            "game_key": self.game_key,
            "label": self.label,
            "bot_username": self.bot_username,
            "bot_pattern_count": len(self.allowed_bot_username_patterns),
            "web_host_count": len(self.allowed_web_hosts),
            "api_hosts": list(self.allowed_api_hosts),
            "endpoint_keys": sorted(self.endpoints),
            "manual_only": bool(self.manual_only),
            "default_enabled": bool(self.default_enabled),
            "request_policy": self.request_policy.safe_summary(),
        }


@dataclass(frozen=True)
class MiniAppFlowStep:
    key: str
    endpoint: str = ""
    method: str = "POST"
    required_payload_keys: tuple[str, ...] = ()
    optional_payload_keys: tuple[str, ...] = ()
    sends_init_data: bool = True
    waits_for: str = ""
    poll_until_key: str = ""
    note: str = ""

    def __post_init__(self):
        object.__setattr__(self, "key", _string(self.key))
        object.__setattr__(self, "endpoint", _string(self.endpoint))
        object.__setattr__(self, "method", (_string(self.method) or "POST").upper())
        object.__setattr__(self, "required_payload_keys", tuple(_string(item) for item in (self.required_payload_keys or ()) if _string(item)))
        object.__setattr__(self, "optional_payload_keys", tuple(_string(item) for item in (self.optional_payload_keys or ()) if _string(item)))
        object.__setattr__(self, "waits_for", _string(self.waits_for))
        object.__setattr__(self, "poll_until_key", _string(self.poll_until_key))
        object.__setattr__(self, "note", sanitize_webapp_secret_text(self.note, limit=120))

    def safe_summary(self):
        return {
            "key": self.key,
            "endpoint": self.endpoint,
            "method": self.method,
            "required_payload_keys": list(self.required_payload_keys),
            "optional_payload_keys": list(self.optional_payload_keys),
            "sends_init_data": bool(self.sends_init_data),
            "waits_for": self.waits_for,
            "poll_until_key": self.poll_until_key,
            "note": self.note,
        }


@dataclass(frozen=True)
class MiniAppFlowPlan:
    adapter_key: str
    label: str
    steps: tuple[MiniAppFlowStep, ...] = ()
    manual_only: bool = True
    default_enabled: bool = False
    note: str = ""
    replaces_commands: tuple[str, ...] = ()
    read_scope: str = "single_identity_command_replacement"
    state_outputs: tuple[str, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "adapter_key", _string(self.adapter_key))
        object.__setattr__(self, "label", _string(self.label))
        object.__setattr__(self, "steps", tuple(step if isinstance(step, MiniAppFlowStep) else MiniAppFlowStep(**dict(step)) for step in (self.steps or ())))
        object.__setattr__(self, "note", sanitize_webapp_secret_text(self.note, limit=180))
        object.__setattr__(self, "replaces_commands", tuple(_string(item) for item in (self.replaces_commands or ()) if _string(item)))
        object.__setattr__(self, "read_scope", _string(self.read_scope) or "single_identity_command_replacement")
        object.__setattr__(self, "state_outputs", tuple(_string(item) for item in (self.state_outputs or ()) if _string(item)))

    def safe_summary(self):
        return {
            "adapter_key": self.adapter_key,
            "label": self.label,
            "manual_only": bool(self.manual_only),
            "default_enabled": bool(self.default_enabled),
            "replaces_commands": list(self.replaces_commands),
            "read_scope": self.read_scope,
            "state_outputs": list(self.state_outputs),
            "step_count": len(self.steps),
            "steps": [step.safe_summary() for step in self.steps],
            "note": self.note,
        }


class MiniAppAdapterRegistry:
    """In-memory adapter registry for lab/manual MiniApp flows."""

    def __init__(self, adapters=None):
        self._adapters = {}
        for adapter in adapters or ():
            self.register(adapter)

    def register(self, adapter, *, replace=False):
        adapter = adapter if isinstance(adapter, MiniAppAdapter) else MiniAppAdapter(**dict(adapter or {}))
        if not adapter.game_key:
            raise ValueError("miniapp adapter game_key missing")
        if adapter.game_key in self._adapters and not replace:
            raise ValueError(f"miniapp adapter already registered: {adapter.game_key}")
        self._adapters[adapter.game_key] = adapter
        return adapter

    def get(self, game_key):
        return self._adapters.get(_string(game_key))

    def require(self, game_key):
        adapter = self.get(game_key)
        if adapter is None:
            raise KeyError(f"miniapp adapter not registered: {_string(game_key)}")
        return adapter

    def keys(self):
        return tuple(sorted(self._adapters))

    def safe_snapshot(self):
        return [self._adapters[key].safe_summary() for key in self.keys()]

    def infer(self, *, button_text="", message_text="", host=""):
        game_hint = infer_webapp_game_hint(button_text=button_text, message_text=message_text, host=host)
        return self.get(game_hint) if game_hint else None


@dataclass
class MiniAppLaunchRequest:
    adapter_key: str
    bot_username: str
    host: str
    webview_url: str = field(repr=False)
    start_param: str = field(default="", repr=False)
    start_param_key: str = ""
    platform: str = "android"
    allowed: bool = False
    reason: str = ""

    def safe_summary(self):
        summary = {
            "adapter_key": self.adapter_key,
            "bot_username": self.bot_username,
            "host": self.host,
            "platform": self.platform,
            "allowed": bool(self.allowed),
            "reason": self.reason,
            "has_start_param": bool(self.start_param),
        }
        if self.start_param:
            start = _summarize_start_param(self.start_param)
            start["key"] = self.start_param_key
            summary["start_param"] = start
        return summary


@dataclass
class MiniAppInitDataSession:
    session_id: str
    adapter_key: str
    identity_id: int
    bot_username: str
    host: str
    init_data: str = field(repr=False)
    created_at: float
    expires_at: float
    source: str = ""
    start_param_digest: str = ""

    def expired(self, now=None):
        now = float(now if now is not None else time.time())
        return self.expires_at <= now

    def safe_summary(self, now=None):
        now = float(now if now is not None else time.time())
        return {
            "session_id": self.session_id,
            "adapter_key": self.adapter_key,
            "identity_id": int(self.identity_id or 0),
            "bot_username": self.bot_username,
            "host": self.host,
            "source": self.source,
            "init_data_digest": _digest(self.init_data),
            "start_param_digest": self.start_param_digest,
            "created_at": float(self.created_at),
            "expires_at": float(self.expires_at),
            "ttl_remaining_sec": max(0, int(self.expires_at - now)),
        }


class MiniAppInitDataStore:
    """Short-lived in-memory storage for WebApp initData.

    This object is intentionally not persistence-aware. It exists so adapters can
    hand raw initData across one running flow without writing it to DB or logs.
    """

    def __init__(self, *, ttl_sec=DEFAULT_MINIAPP_INIT_DATA_TTL_SEC, clock=None):
        self.ttl_sec = max(30, int(ttl_sec or DEFAULT_MINIAPP_INIT_DATA_TTL_SEC))
        self.clock = clock or time.time
        self._sessions = {}

    def cleanup(self, now=None):
        now = float(now if now is not None else self.clock())
        expired = [key for key, item in self._sessions.items() if item.expired(now)]
        for key in expired:
            self._sessions.pop(key, None)
        return len(expired)

    def put(self, *, adapter_key, identity_id, bot_username, host, init_data, start_param="", source=""):
        now = float(self.clock())
        self.cleanup(now)
        raw_init_data = _string(init_data)
        if not raw_init_data:
            raise ValueError("initData is empty")
        seed = f"{adapter_key}:{identity_id}:{bot_username}:{host}:{_digest(start_param)}:{_digest(raw_init_data)}:{now:.3f}"
        session_id = _digest(seed)
        self._sessions[session_id] = MiniAppInitDataSession(
            session_id=session_id,
            adapter_key=_string(adapter_key),
            identity_id=int(identity_id or 0),
            bot_username=_string(bot_username).lstrip("@"),
            host=_string(host).lower(),
            init_data=raw_init_data,
            created_at=now,
            expires_at=now + self.ttl_sec,
            source=_string(source),
            start_param_digest=_digest(start_param),
        )
        return session_id

    def get(self, session_id, *, now=None):
        self.cleanup(now)
        return self._sessions.get(_string(session_id))

    def get_init_data(self, session_id, *, now=None):
        session = self.get(session_id, now=now)
        return session.init_data if session else ""

    def discard(self, session_id):
        return self._sessions.pop(_string(session_id), None) is not None

    def safe_snapshot(self, *, now=None):
        self.cleanup(now)
        now = float(now if now is not None else self.clock())
        return [item.safe_summary(now) for item in self._sessions.values()]


def _first_query_value(parsed, keys=START_PARAM_QUERY_KEYS):
    query = parse_qs(parsed.query, keep_blank_values=True)
    fragment = parse_qs(parsed.fragment, keep_blank_values=True)
    for key in keys:
        values = query.get(key) or fragment.get(key) or []
        if values:
            return key, values[0]
    return "", ""


def _webapp_path_bot_username(parsed):
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    return parts[0].lstrip("@") if parts else ""


def _bot_username_allowed(adapter, actual_bot):
    actual = _string(actual_bot).lstrip("@")
    restricted = bool(adapter.bot_username or adapter.allowed_bot_username_patterns)
    if not restricted:
        return True
    if not actual:
        return False
    if adapter.bot_username and actual.lower() == adapter.bot_username.lower():
        return True
    for pattern in adapter.allowed_bot_username_patterns:
        try:
            if re.fullmatch(pattern, actual, flags=re.IGNORECASE):
                return True
        except re.error:
            continue
    return False


def build_miniapp_launch_request(adapter, url="", *, start_param="", bot_username=""):
    adapter = adapter if isinstance(adapter, MiniAppAdapter) else MiniAppAdapter(**dict(adapter or {}))
    raw_url = _string(url) or adapter.webview_url
    parsed = _parse_url(raw_url)
    host = _url_hostname(raw_url)
    if not host:
        return MiniAppLaunchRequest(adapter.game_key, "", "", raw_url, allowed=False, reason="webview url missing host")
    if adapter.allowed_web_hosts and host not in adapter.allowed_web_hosts:
        return MiniAppLaunchRequest(adapter.game_key, "", host, raw_url, allowed=False, reason=f"webview host not allowed: {host}")

    path_bot = _webapp_path_bot_username(parsed) if host in DEFAULT_TELEGRAM_WEBAPP_HOSTS else ""
    expected_bot = adapter.bot_username
    actual_bot = _string(bot_username).lstrip("@") or path_bot or expected_bot
    if not _bot_username_allowed(adapter, actual_bot):
        return MiniAppLaunchRequest(adapter.game_key, actual_bot, host, raw_url, allowed=False, reason="bot username not allowed")

    start_key, found_start = _first_query_value(parsed)
    effective_start = _string(start_param) or found_start
    if adapter.start_param_pattern and effective_start and not re.fullmatch(adapter.start_param_pattern, effective_start):
        return MiniAppLaunchRequest(adapter.game_key, actual_bot, host, raw_url, effective_start, start_key, adapter.platform, False, "start_param not allowed")
    return MiniAppLaunchRequest(
        adapter_key=adapter.game_key,
        bot_username=actual_bot,
        host=host,
        webview_url=raw_url,
        start_param=effective_start,
        start_param_key=start_key,
        platform=adapter.platform,
        allowed=True,
        reason="allowed",
    )


def build_request_webview_args(adapter, launch_request):
    adapter = adapter if isinstance(adapter, MiniAppAdapter) else MiniAppAdapter(**dict(adapter or {}))
    request = launch_request if isinstance(launch_request, MiniAppLaunchRequest) else build_miniapp_launch_request(adapter, launch_request)
    if not request.allowed:
        raise ValueError(request.reason or "miniapp launch not allowed")
    return {
        "bot_username": request.bot_username or adapter.bot_username,
        "platform": request.platform or adapter.platform,
        "url": request.webview_url,
        "start_param": request.start_param,
    }


def _flatten_webapp_buttons(value):
    if value is None:
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _flatten_webapp_buttons(item)
        return
    for attr in ("rows", "buttons"):
        child = getattr(value, attr, None)
        if child is not None and child is not value:
            yield from _flatten_webapp_buttons(child)
            return
    yield value


def _button_text(button):
    for source in (button, getattr(button, "button", None)):
        if source is None:
            continue
        text = getattr(source, "text", "")
        if text:
            return _string(text)
    return ""


def _button_url(button):
    for source in (button, getattr(button, "button", None)):
        if source is None:
            continue
        for attr in ("url", "webview", "web_view"):
            value = getattr(source, attr, "")
            if isinstance(value, str) and value.strip():
                return value.strip()
        for attr in ("web_app", "webview", "web_view"):
            nested = getattr(source, attr, None)
            value = getattr(nested, "url", "") if nested is not None else ""
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def iter_webapp_button_links(event):
    """Yield button text and raw URL from common Telegram button shapes."""
    message = getattr(event, "message", None)
    for source in (
        getattr(message, "buttons", None),
        getattr(event, "buttons", None),
        getattr(message, "reply_markup", None),
        getattr(event, "reply_markup", None),
    ):
        for button in _flatten_webapp_buttons(source):
            url = _button_url(button)
            if url:
                yield _button_text(button), url


def _event_text_candidates(event, message_text=""):
    seen = set()
    message = getattr(event, "message", None)
    for value in (
        message_text,
        getattr(event, "raw_text", ""),
        getattr(event, "text", ""),
        getattr(message, "message", ""),
        getattr(message, "text", ""),
        getattr(message, "raw_text", ""),
    ):
        text = str(value or "")
        if text and text not in seen:
            seen.add(text)
            yield text


def iter_webapp_entry_links(event, *, message_text=""):
    """Yield MiniApp entry URLs from buttons first, then text URL fallbacks."""
    seen_urls = set()
    for button_text, url in iter_webapp_button_links(event):
        raw_url = _string(url)
        if not raw_url or raw_url in seen_urls:
            continue
        seen_urls.add(raw_url)
        yield button_text, raw_url
    for text in _event_text_candidates(event, message_text):
        for match in RE_WEBAPP_URL.finditer(text):
            raw_url = match.group(0).rstrip("，。,.、；;：:")
            if not raw_url or raw_url in seen_urls:
                continue
            seen_urls.add(raw_url)
            yield "", raw_url


def _path_allowed(path, allowed_prefixes):
    if not allowed_prefixes:
        return True
    raw_path = _string(path)
    return any(raw_path == prefix or raw_path.startswith(prefix) for prefix in allowed_prefixes)


def build_miniapp_api_url(adapter, endpoint_key_or_path):
    adapter = adapter if isinstance(adapter, MiniAppAdapter) else MiniAppAdapter(**dict(adapter or {}))
    endpoint = adapter.api_endpoint(endpoint_key_or_path)
    if not adapter.api_base_url:
        raise ValueError("miniapp api_base_url missing")
    url = urljoin(_url_origin(adapter.api_base_url) + "/", endpoint.lstrip("/"))
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if adapter.allowed_api_hosts and host not in adapter.allowed_api_hosts:
        raise ValueError(f"miniapp api host not allowed: {host}")
    if not _path_allowed(parsed.path, adapter.allowed_api_paths):
        raise ValueError(f"miniapp api path not allowed: {parsed.path}")
    return url


def build_miniapp_http_request(
    adapter,
    endpoint_key_or_path,
    payload=None,
    *,
    init_data_session=None,
    init_data="",
    method="POST",
    headers=None,
    timeout_sec=20,
):
    payload = dict(payload or {})
    raw_init_data = _string(init_data)
    if init_data_session is not None:
        raw_init_data = getattr(init_data_session, "init_data", "") or raw_init_data
    if raw_init_data:
        payload.setdefault("initData", raw_init_data)
    secret_keys = sorted(key for key in payload if key in SENSITIVE_WEBAPP_QUERY_KEYS)
    url = build_miniapp_api_url(adapter, endpoint_key_or_path)
    headers = dict(headers or {})
    return {
        "method": (_string(method) or "POST").upper(),
        "url": url,
        "headers": headers,
        "timeout_sec": max(1, int(timeout_sec or 20)),
        "payload": payload,
        "safe_summary": {
            "adapter_key": adapter.game_key,
            "method": (_string(method) or "POST").upper(),
            "url_host": _url_hostname(url),
            "endpoint": _string(endpoint_key_or_path),
            "header_keys": sorted(headers),
            "payload_keys": sorted(payload),
            "secret_keys": secret_keys,
            "has_init_data": bool(raw_init_data),
            "init_data_digest": _digest(raw_init_data),
            "timeout_sec": max(1, int(timeout_sec or 20)),
        },
    }


@dataclass(frozen=True)
class MiniAppHttpResult:
    ok: bool
    status_code: int = 0
    data: dict = field(default_factory=dict)
    error: str = ""
    error_type: str = ""
    retryable: bool = False
    attempts: int = 1

    def safe_summary(self):
        summary = {
            "ok": bool(self.ok),
            "status_code": int(self.status_code or 0),
            "error_type": self.error_type,
            "retryable": bool(self.retryable),
            "attempts": int(self.attempts or 0),
        }
        if self.error:
            summary["error"] = sanitize_webapp_secret_text(self.error)
        if isinstance(self.data, dict):
            summary["data_keys"] = sorted(str(key) for key in self.data)
        return summary


@dataclass(frozen=True)
class MiniAppFlowEvent:
    step_key: str
    event: str
    status: str = "ok"
    detail: dict = field(default_factory=dict)

    def safe_summary(self):
        return {
            "step_key": self.step_key,
            "event": self.event,
            "status": self.status,
            "detail": safe_miniapp_event_detail(self.detail),
        }


@dataclass(frozen=True)
class MiniAppFlowRunResult:
    ok: bool
    adapter_key: str
    events: tuple[MiniAppFlowEvent, ...] = ()
    context: dict = field(default_factory=dict, repr=False)
    error: str = ""

    def safe_summary(self):
        summary = {
            "ok": bool(self.ok),
            "adapter_key": self.adapter_key,
            "event_count": len(self.events),
            "events": [event.safe_summary() for event in self.events],
            "context_keys": sorted(str(key) for key in self.context),
        }
        if self.error:
            summary["error"] = sanitize_webapp_secret_text(self.error)
        return summary


def summarize_miniapp_json_shape(
    value,
    *,
    max_depth=MINIAPP_CAPTURE_SHAPE_MAX_DEPTH,
    list_sample_limit=MINIAPP_CAPTURE_LIST_SAMPLE_LIMIT,
    max_keys=MINIAPP_CAPTURE_SHAPE_MAX_KEYS,
):
    """Return a compact JSON shape for protocol fixtures without raw values.

    Depth is bounded by ``max_depth`` and object width by ``max_keys``. Without
    the width bound a single wide response (e.g. the cave-treasure ``start``
    payload, which carries the whole account tree) expands into tens of KiB per
    captured request; keys beyond the bound are reported as a count only.
    """

    def visit(item, depth):
        if isinstance(item, dict):
            keys = sorted(str(key) for key in item)
            limit = max(1, int(max_keys or 1))
            shown_keys = keys[:limit]
            summary = {"type": "object", "keys": shown_keys}
            if len(keys) > limit:
                summary["keys_truncated"] = len(keys) - limit
            if depth < int(max_depth or 0):
                shown = set(shown_keys)
                summary["children"] = {
                    str(key): visit(child, depth + 1)
                    for key, child in sorted(item.items(), key=lambda pair: str(pair[0]))
                    if str(key) in shown
                }
            return summary
        if isinstance(item, list):
            summary = {"type": "array", "length": len(item)}
            if depth < int(max_depth or 0) and item:
                summary["items"] = [visit(child, depth + 1) for child in item[: max(1, int(list_sample_limit or 1))]]
            return summary
        if isinstance(item, bool):
            return {"type": "bool"}
        if isinstance(item, int) and not isinstance(item, bool):
            return {"type": "int"}
        if isinstance(item, float):
            return {"type": "float"}
        if item is None:
            return {"type": "null"}
        if isinstance(item, str):
            return {"type": "string", "length": len(item)}
        return {"type": type(item).__name__}

    return visit(value, 0)


def _safe_capture_body(value):
    if isinstance(value, dict):
        return safe_miniapp_event_detail(value)
    if isinstance(value, list):
        return safe_miniapp_event_detail({"items": value}).get("items", [])
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return sanitize_webapp_secret_text(value, limit=MINIAPP_CAPTURE_MAX_TEXT)


def _capture_body_digest(value):
    try:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        payload = str(value)
    return _digest(payload)


def _response_status_and_body(response):
    if isinstance(response, tuple) and len(response) == 2:
        return int(response[0] or 0), response[1]
    if isinstance(response, dict):
        if "status_code" in response:
            return int(response.get("status_code") or 0), response.get("json", response.get("body", response))
        if "status" in response:
            return int(response.get("status") or 0), response.get("json", response.get("body", response))
        return 200, response
    status_code = int(getattr(response, "status_code", 0) or 0)
    if hasattr(response, "json"):
        try:
            return status_code, response.json()
        except Exception:
            pass
    return status_code, getattr(response, "text", "")


def _coerce_json_body(body):
    if isinstance(body, dict):
        return body, ""
    if isinstance(body, bytes):
        body = body.decode("utf-8", "replace")
    if isinstance(body, str):
        try:
            value = json.loads(body)
        except ValueError:
            return {}, "non_json"
        return value if isinstance(value, dict) else {}, "" if isinstance(value, dict) else "non_object_json"
    return {}, "non_json"


def classify_miniapp_http_response(status_code, body, *, attempts=1):
    status_code = int(status_code or 0)
    data, parse_error = _coerce_json_body(body)
    if parse_error:
        return MiniAppHttpResult(
            ok=False,
            status_code=status_code,
            error=f"HTTP {status_code} returned non JSON",
            error_type="transient",
            retryable=True,
            attempts=attempts,
        )
    if 200 <= status_code < 300 and data.get("ok") is True:
        return MiniAppHttpResult(ok=True, status_code=status_code, data=data, attempts=attempts)

    reason = _string(data.get("error") or data.get("message") or f"HTTP {status_code}")
    retryable = status_code >= 500 or status_code == 429 or status_code <= 0
    return MiniAppHttpResult(
        ok=False,
        status_code=status_code,
        data=data,
        error=sanitize_webapp_secret_text(reason),
        error_type="transient" if retryable else "app",
        retryable=retryable,
        attempts=attempts,
    )


@dataclass(frozen=True)
class MiniAppCaptureRecord:
    """Sanitized MiniApp protocol sample.

    The record intentionally stores only safe request/response summaries. Raw
    token, initData, hash, user and header values must never be placed here.
    """

    adapter_key: str
    step_key: str
    endpoint: str
    method: str
    url_host: str
    url_path: str
    status_code: int = 0
    ok: bool = False
    error_type: str = ""
    error: str = ""
    attempt: int = 1
    elapsed_ms: int = 0
    created_at: float = 0.0
    source: str = ""
    request: dict = field(default_factory=dict)
    response: dict = field(default_factory=dict)

    def safe_record(self):
        return {
            "adapter_key": self.adapter_key,
            "step_key": self.step_key,
            "endpoint": self.endpoint,
            "method": self.method,
            "url_host": self.url_host,
            "url_path": self.url_path,
            "status_code": int(self.status_code or 0),
            "ok": bool(self.ok),
            "error_type": self.error_type,
            "error": sanitize_webapp_secret_text(self.error),
            "attempt": int(self.attempt or 0),
            "elapsed_ms": int(self.elapsed_ms or 0),
            "created_at": float(self.created_at or 0),
            "source": sanitize_webapp_secret_text(self.source, limit=120),
            "request": safe_miniapp_event_detail(self.request),
            "response": safe_miniapp_event_detail(self.response),
        }


class MiniAppCaptureStore:
    """Append-only sink for sanitized MiniApp protocol samples.

    This is lab/capture plumbing only. It never receives raw HTTP credentials
    from the core helpers, and it writes JSONL only when an explicit path is
    supplied by the caller.

    Capture files are day-sharded by their callers. Retention is enforced here
    rather than by each caller so every adapter gets it for free: without a
    bound this directory only ever grows (it reached 248 MiB / 87 files before
    ``retention_days`` existed).
    """

    def __init__(self, path=None, *, keep_memory=True, retention_days=MINIAPP_CAPTURE_RETENTION_DAYS):
        self.path = Path(path).expanduser() if path else None
        self.keep_memory = bool(keep_memory)
        self.retention_days = max(0, int(retention_days or 0))
        self.records = []
        self.prune_error = ""
        self._pruned = False

    def _prune_expired(self, now=None):
        """Drop day-sharded siblings older than the retention window.

        Runs at most once per store instance, and never raises: capture is
        diagnostic plumbing and must not break a live MiniApp run. A failure
        is still recorded on the instance (`prune_error`) so it does not vanish
        entirely — silently swallowing it would recreate the very problem this
        module's retention was added to solve.
        """
        if self._pruned or self.path is None or self.retention_days <= 0:
            return
        self._pruned = True
        try:
            cutoff = float(now if now is not None else time.time()) - self.retention_days * 86400
            stem = self.path.name.rsplit("-", 3)[0] if "-" in self.path.name else ""
            for sibling in self.path.parent.glob(f"{stem}-*.jsonl" if stem else "*.jsonl"):
                if sibling == self.path:
                    continue
                try:
                    if sibling.stat().st_mtime < cutoff:
                        sibling.unlink()
                except OSError:
                    continue
        except Exception as exc:
            self.prune_error = f"{type(exc).__name__}: {str(exc)[:120]}"

    def append(self, record):
        safe = record.safe_record() if hasattr(record, "safe_record") else safe_miniapp_event_detail(dict(record or {}))
        if self.keep_memory:
            self.records.append(safe)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._prune_expired()
            with self.path.open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(safe, ensure_ascii=False, sort_keys=True) + "\n")
        return safe


def build_miniapp_capture_record(
    request,
    response=None,
    *,
    result=None,
    step_key="",
    source="",
    started_at=None,
    ended_at=None,
    attempt=1,
):
    safe_request = dict((request or {}).get("safe_summary") or {})
    url = _string((request or {}).get("url"))
    parsed = _parse_url(url)
    status_code = 0
    body = {}
    parse_error = ""
    if isinstance(response, MiniAppHttpResult):
        result = response
        status_code = int(response.status_code or 0)
        body = response.data
    elif response is not None:
        status_code, body = _response_status_and_body(response)
    if result is None:
        result = classify_miniapp_http_response(status_code, body, attempts=attempt)
    elif not body and isinstance(result, MiniAppHttpResult):
        body = result.data
    data, parse_error = _coerce_json_body(body)
    safe_body = data if data else body
    started = float(started_at or 0)
    ended = float(ended_at or time.time())
    elapsed_ms = int(max(0.0, ended - started) * 1000) if started else 0
    endpoint = _string(safe_request.get("endpoint") or parsed.path)
    method = _string((request or {}).get("method") or safe_request.get("method") or "POST").upper()
    request_payload = dict((request or {}).get("payload") or {})
    response_detail = {
        "body_shape": summarize_miniapp_json_shape(safe_body),
        "body_digest": _capture_body_digest(safe_body),
        "parse_error": parse_error,
        "data_keys": sorted(str(key) for key in data) if isinstance(data, dict) else [],
    }
    return MiniAppCaptureRecord(
        adapter_key=_string(safe_request.get("adapter_key") or safe_request.get("game_key")),
        step_key=_string(step_key or safe_request.get("step_key") or endpoint),
        endpoint=endpoint,
        method=method,
        url_host=(parsed.hostname or safe_request.get("url_host") or "").lower(),
        url_path=parsed.path,
        status_code=int(result.status_code or status_code or 0),
        ok=bool(result.ok),
        error_type=_string(result.error_type),
        error=sanitize_webapp_secret_text(result.error),
        attempt=int(result.attempts or attempt or 0),
        elapsed_ms=elapsed_ms,
        created_at=ended,
        source=_string(source),
        request={
            "summary": safe_request,
            "payload": _safe_capture_body(request_payload),
            "payload_shape": summarize_miniapp_json_shape(request_payload),
            "header_keys": sorted(str(key) for key in dict((request or {}).get("headers") or {})),
        },
        response=response_detail,
    )


def _emit_miniapp_capture(capture_sink, record):
    if capture_sink is None:
        return
    safe = record.safe_record() if hasattr(record, "safe_record") else dict(record or {})
    if hasattr(capture_sink, "append"):
        capture_sink.append(record if isinstance(capture_sink, MiniAppCaptureStore) else safe)
        return
    capture_sink(safe)


def execute_miniapp_http_request(
    request,
    transport,
    *,
    backoff_sec=DEFAULT_MINIAPP_HTTP_BACKOFF_SEC,
    sleeper=None,
    capture_sink=None,
    capture_source="",
    step_key="",
    request_budget=None,
):
    if transport is None:
        raise ValueError("miniapp transport missing")
    delays = tuple(float(delay) for delay in (backoff_sec or ()))
    if request_budget is not None:
        max_retries = max(0, int(request_budget.policy.max_attempts_per_request) - 1)
        delays = delays[:max_retries]
    attempts_total = len(delays) + 1
    last_result = None
    for attempt in range(1, attempts_total + 1):
        started = time.time()
        response_for_capture = None
        if request_budget is not None:
            allowed, _delay, reason = request_budget.acquire()
            if not allowed:
                result = MiniAppHttpResult(
                    ok=False,
                    error=reason,
                    error_type="request_budget",
                    retryable=False,
                    attempts=max(0, attempt - 1),
                )
                _emit_miniapp_capture(
                    capture_sink,
                    build_miniapp_capture_record(
                        request,
                        (0, {"ok": False, "error": reason}),
                        result=result,
                        step_key=step_key,
                        source=capture_source,
                        started_at=started,
                        ended_at=time.time(),
                        attempt=attempt,
                    ),
                )
                return result
        try:
            if request.get("global_rate_limit", True) and not os.environ.get("PYTEST_CURRENT_TEST"):
                _GLOBAL_MINIAPP_RATE_LIMITER.acquire(
                    priority=str(request.get("global_priority") or "").lower() == "world_boss",
                    sleeper=sleeper or time.sleep,
                )
            response = transport(request)
            status_code, body = _response_status_and_body(response)
            response_for_capture = (status_code, body)
            result = classify_miniapp_http_response(status_code, body, attempts=attempt)
        except Exception as exc:
            result = MiniAppHttpResult(
                ok=False,
                error=sanitize_webapp_secret_text(exc),
                error_type="transient",
                retryable=True,
                attempts=attempt,
            )
            response_for_capture = (0, {"ok": False, "error": str(exc)})
        last_result = result
        if request_budget is not None:
            request_budget.note_result(result)
        _emit_miniapp_capture(
            capture_sink,
            build_miniapp_capture_record(
                request,
                response_for_capture,
                result=result,
                step_key=step_key,
                source=capture_source,
                started_at=started,
                ended_at=time.time(),
                attempt=attempt,
            ),
        )
        if result.ok or not result.retryable or attempt >= attempts_total:
            return result
        if sleeper is not None:
            sleeper(delays[attempt - 1])
    return last_result or MiniAppHttpResult(ok=False, error="miniapp request not executed", error_type="transient", retryable=True)


def validate_miniapp_flow_plan(plan, adapter=None):
    plan = plan if isinstance(plan, MiniAppFlowPlan) else MiniAppFlowPlan(**dict(plan or {}))
    errors = []
    seen = set()
    if adapter is not None:
        adapter = adapter if isinstance(adapter, MiniAppAdapter) else MiniAppAdapter(**dict(adapter or {}))
        if plan.adapter_key != adapter.game_key:
            errors.append(f"plan adapter_key mismatch: {plan.adapter_key} != {adapter.game_key}")
    if not plan.adapter_key:
        errors.append("flow plan adapter_key missing")
    for step in plan.steps:
        if not step.key:
            errors.append("flow step key missing")
            continue
        if step.key in seen:
            errors.append(f"duplicate flow step key: {step.key}")
        seen.add(step.key)
        method = (_string(step.method) or "POST").upper()
        if method not in MINIAPP_HTTP_METHODS and method not in MINIAPP_LOCAL_METHODS:
            errors.append(f"{step.key}: unsupported method {method}")
        if method in MINIAPP_HTTP_METHODS and not step.endpoint:
            errors.append(f"{step.key}: endpoint missing")
    return errors


def _step_payload_from_context(step, context, raw_init_data):
    payload = {}
    missing = []
    for key in step.required_payload_keys:
        if key == "initData":
            if not raw_init_data and not _string(context.get("initData")):
                missing.append(key)
            continue
        if key not in context or context.get(key) in (None, ""):
            missing.append(key)
        else:
            payload[key] = context.get(key)
    for key in step.optional_payload_keys:
        if key == "initData":
            continue
        if key in context and context.get(key) not in (None, ""):
            payload[key] = context.get(key)
    return payload, missing


def run_miniapp_flow_plan(
    plan,
    adapter,
    context=None,
    *,
    init_data_session=None,
    init_data="",
    transport=None,
    backoff_sec=DEFAULT_MINIAPP_HTTP_BACKOFF_SEC,
    sleeper=None,
    capture_sink=None,
    capture_source="",
):
    adapter = adapter if isinstance(adapter, MiniAppAdapter) else MiniAppAdapter(**dict(adapter or {}))
    plan = plan if isinstance(plan, MiniAppFlowPlan) else MiniAppFlowPlan(**dict(plan or {}))
    errors = validate_miniapp_flow_plan(plan, adapter)
    if errors:
        return MiniAppFlowRunResult(False, adapter.game_key, error="; ".join(errors))

    context = dict(context or {})
    raw_init_data = _string(init_data) or _string(getattr(init_data_session, "init_data", ""))
    if raw_init_data:
        context.setdefault("initData", raw_init_data)

    events = []
    for step in plan.steps:
        method = (_string(step.method) or "POST").upper()
        payload, missing = _step_payload_from_context(step, context, raw_init_data)
        if missing:
            events.append(MiniAppFlowEvent(step.key, "validate", "missing_payload", {"missing": missing}))
            return MiniAppFlowRunResult(False, adapter.game_key, tuple(events), context, f"{step.key}: missing payload {', '.join(missing)}")

        if method in MINIAPP_LOCAL_METHODS:
            events.append(MiniAppFlowEvent(step.key, "local", "prepared", {"method": method, "waits_for": step.waits_for}))
            continue

        request = build_miniapp_http_request(
            adapter,
            step.endpoint or step.key,
            payload,
            init_data_session=init_data_session,
            init_data=raw_init_data if step.sends_init_data else "",
            method=method,
        )
        events.append(MiniAppFlowEvent(step.key, "request", "prepared", request["safe_summary"]))
        if transport is None:
            continue
        result = execute_miniapp_http_request(
            request,
            transport,
            backoff_sec=backoff_sec,
            sleeper=sleeper,
            capture_sink=capture_sink,
            capture_source=capture_source or plan.adapter_key,
            step_key=step.key,
        )
        events.append(MiniAppFlowEvent(step.key, "response", "ok" if result.ok else result.error_type or "failed", result.safe_summary()))
        if not result.ok:
            return MiniAppFlowRunResult(False, adapter.game_key, tuple(events), context, result.error)
        context[step.key] = result.data
        if step.poll_until_key and result.data.get(step.poll_until_key) is not True:
            return MiniAppFlowRunResult(False, adapter.game_key, tuple(events), context, f"{step.key}: {step.poll_until_key} not ready")

    return MiniAppFlowRunResult(True, adapter.game_key, tuple(events), context)


def summarize_webapp_url(url, *, button_text="", message_text=""):
    """Return a safe WebApp/MiniApp URL summary without persisting credentials."""
    raw_url = _string(url)
    host = _url_host(raw_url)
    if not raw_url and not host:
        return {}
    parsed = _parse_url(raw_url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    fragment = parse_qs(parsed.fragment, keep_blank_values=True)
    all_keys = set(query) | set(fragment)

    start_param = {}
    for key in START_PARAM_QUERY_KEYS:
        values = query.get(key) or fragment.get(key) or []
        if values:
            start_param = _summarize_start_param(values[0])
            start_param["key"] = key
            break

    sensitive_keys = sorted(key for key in all_keys if key in SENSITIVE_WEBAPP_QUERY_KEYS)
    summary = {
        "host": host,
        "has_start_param": bool(start_param),
        "has_sensitive_init_data": bool(sensitive_keys),
    }
    if start_param:
        summary["start_param"] = start_param
    if sensitive_keys:
        summary["sensitive_keys"] = sensitive_keys

    game_hint = infer_webapp_game_hint(button_text=button_text, message_text=message_text, host=host)
    if game_hint:
        summary["game_hint"] = game_hint
    return summary


def infer_webapp_game_hint(*, button_text="", message_text="", host=""):
    text = f"{button_text}\n{message_text}\n{host}".lower()
    if any(keyword in text for keyword in ("天机命脉", "xianxia-fate-cards", "fate_cards", "fate_", "fate-")):
        return "fate_cards"
    if any(keyword in text for keyword in ("灵溪", "垂钓", "钓鱼", "fish")):
        return "fishing"
    if any(keyword in text for keyword in ("洞府", "寻宝", "外府石室", "df_")):
        return "cave_treasure"
    if any(keyword in text for keyword in ("天机试炼", "试炼台", "灵脉点穴", "trial")):
        return "trial"
    if any(keyword in text for keyword in ("世界boss", "世界 boss", "真仙试锋", "南宫阙", "nqb_", "boss")):
        return "world_boss"
    if any(keyword in text for keyword in ("观星", "星台", "观星台", "stargazer")):
        return "stargazer"
    if any(keyword in text for keyword in ("灵眼之树", "进入灵树", "灵树", "tree_")):
        return "tree"
    if any(keyword in text for keyword in ("天道", "审判", "问心", "xianxia-verify", "fanrenxiuxian_bot")):
        return "tiandao_judgement"
    return ""
