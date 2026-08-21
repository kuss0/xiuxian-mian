"""Shared MiniApp plumbing.

`webapp_core` stays free of `requests`/`config` so it can be exercised as a pure
protocol kernel. Everything below is the thin layer that binds that kernel to
this project's HTTP stack, and it exists so the per-game modules stop carrying
their own near-identical copies.

Before this module each adapter defined its own `_requests_transport`,
`_flow_result` and `_append_http_event`. The copies had already drifted apart
(only the world-boss one reused a `requests.Session`), which meant a fix to the
transport had to be applied in seven places and was in practice applied in one.
"""

import atexit
import threading
import time

import requests
from requests.adapters import HTTPAdapter
from requests.exceptions import RequestException

from ..config import TG_REQUESTS_PROXIES
from ..state import get_current_identity_id
from ..webapp_core import safe_miniapp_event_detail, sanitize_webapp_secret_text


def resolve_identity_id(value=None):
    """Coerce an identity id, falling back to the current one."""
    try:
        return int(value if value is not None else get_current_identity_id() or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


DEFAULT_MINIAPP_HTTP_TIMEOUT = (5, 20)
MINIAPP_DEFAULT_USER_AGENT = "Mozilla/5.0"
MINIAPP_BUSINESS_CAPTURE_ADAPTERS = frozenset({
    "fishing",
    "trial",
    "cave_treasure",
    "stargazer",
    "tree",
})
MINIAPP_BUSINESS_CAPTURE_KEYS = frozenset({
    "settled_count",
    "rods",
    "caught",
    "empty",
    "catches",
    "found_main",
    "collect_count",
    "gains",
    "items",
})


def build_miniapp_transport(*, timeout=DEFAULT_MINIAPP_HTTP_TIMEOUT, session=None, proxies=None):
    """Build the transport callable consumed by `execute_miniapp_http_request`.

    Pass a `requests.Session` to reuse the underlying TCP/TLS connection across
    the steps of one flow. That matters: a cave-treasure run issues a dozen
    round trips through a proxy, and without a session every one of them pays a
    fresh handshake.

    The returned callable is synchronous by design — callers hand it to
    `execute_miniapp_http_request`, which they in turn dispatch through
    `asyncio.to_thread`, so the event loop is never blocked.
    """
    effective_proxies = TG_REQUESTS_PROXIES if proxies is None else proxies

    def _transport(request):
        requester = session.request if session is not None else requests.request
        return requester(
            str(request.get("method") or "POST"),
            request["url"],
            json=request.get("payload") or {},
            headers={
                "User-Agent": MINIAPP_DEFAULT_USER_AGENT,
                "Content-Type": "application/json",
                **dict(request.get("headers") or {}),
            },
            proxies=effective_proxies,
            timeout=timeout,
        )

    return _transport


class _MiniAppSessionPool:
    """Keep one HTTP session per adapter/identity on its configured route."""

    def __init__(self):
        self._lock = threading.RLock()
        self._entries = {}

    @staticmethod
    def _key(adapter_key, identity_id):
        return str(adapter_key or "miniapp"), int(identity_id or 0)

    @staticmethod
    def _new_session(route, proxies):
        session = requests.Session()
        session.trust_env = False
        adapter = HTTPAdapter(pool_connections=2, pool_maxsize=4, max_retries=0)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        if route == "config_proxy" and proxies:
            session.proxies.update(proxies)
        return session

    def acquire(self, adapter_key, identity_id, proxies):
        key = self._key(adapter_key, identity_id)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                route = "config_proxy" if proxies else "direct"
                entry = {
                    "route": route,
                    "session": self._new_session(route, proxies),
                    "request_lock": threading.Lock(),
                }
                self._entries[key] = entry
            return entry["session"], str(entry["route"]), entry["request_lock"]

    def is_current(self, adapter_key, identity_id, session, request_lock):
        key = self._key(adapter_key, identity_id)
        with self._lock:
            entry = self._entries.get(key)
            return bool(
                isinstance(entry, dict)
                and entry.get("session") is session
                and entry.get("request_lock") is request_lock
            )

    def invalidate(self, adapter_key, identity_id, session, proxies, *, request_lock=None):
        key = self._key(adapter_key, identity_id)
        old_session = None
        with self._lock:
            entry = self._entries.get(key)
            if not isinstance(entry, dict) or entry.get("session") is not session:
                return
            old_session = entry.get("session")
            route = str(entry.get("route") or "direct")
            self._entries[key] = {
                "route": route,
                "session": self._new_session(route, proxies),
                # Keep the per-identity lock across route replacement. This
                # prevents a waiting caller from using the new session while
                # the failed caller is still unwinding the old one.
                "request_lock": request_lock or threading.Lock(),
            }
        if old_session is not None:
            try:
                old_session.close()
            except Exception:
                pass

    def close(self, adapter_key=None, identity_id=None):
        adapter_key = str(adapter_key or "")
        identity_id = None if identity_id is None else int(identity_id or 0)
        sessions = []
        with self._lock:
            keys = [
                key for key in self._entries
                if (not adapter_key or key[0] == adapter_key)
                and (identity_id is None or key[1] == identity_id)
            ]
            for key in keys:
                entry = self._entries.pop(key, None)
                if isinstance(entry, dict) and entry.get("session") is not None:
                    sessions.append(entry["session"])
        for session in sessions:
            try:
                session.close()
            except Exception:
                pass
        return len(sessions)


_MINIAPP_SESSION_POOL = _MiniAppSessionPool()


def build_pooled_miniapp_transport(
    *,
    adapter_key,
    identity_id=0,
    timeout=DEFAULT_MINIAPP_HTTP_TIMEOUT,
    proxies=None,
):
    """Build a production transport with bounded session reuse.

    Configured proxies are used from the first request. Without configured
    proxies the route is direct. A connection-level failure replaces the stale
    session without changing the selected route. All retries still pass through
    the global MiniApp limiter in ``execute_miniapp_http_request``.
    """
    effective_proxies = dict(
        (TG_REQUESTS_PROXIES or {}) if proxies is None else (proxies or {})
    )
    adapter_key = str(adapter_key or "miniapp")
    identity_id = int(identity_id or 0)

    def _transport(request):
        while True:
            session, _route, request_lock = _MINIAPP_SESSION_POOL.acquire(
                adapter_key,
                identity_id,
                effective_proxies,
            )
            request_lock.acquire()
            if _MINIAPP_SESSION_POOL.is_current(
                adapter_key,
                identity_id,
                session,
                request_lock,
            ):
                break
            request_lock.release()
        try:
            try:
                return session.request(
                    str(request.get("method") or "POST"),
                    request["url"],
                    json=request.get("payload") or {},
                    headers={
                        "User-Agent": MINIAPP_DEFAULT_USER_AGENT,
                        "Content-Type": "application/json",
                        **dict(request.get("headers") or {}),
                    },
                    timeout=timeout,
                )
            except RequestException as exc:
                _MINIAPP_SESSION_POOL.invalidate(
                    adapter_key,
                    identity_id,
                    session,
                    effective_proxies,
                    request_lock=request_lock,
                )
                raise
        finally:
            request_lock.release()

    return _transport


def close_pooled_miniapp_sessions(*, adapter_key=None, identity_id=None):
    return _MINIAPP_SESSION_POOL.close(adapter_key=adapter_key, identity_id=identity_id)


atexit.register(close_pooled_miniapp_sessions)


def append_http_event(events, step, result):
    """Record one sanitized HTTP step onto a flow's event list.

    Kept byte-compatible with the per-adapter copies it replaces: the same keys
    in the same order, and `error` always routed through
    `sanitize_webapp_secret_text` so tokens never reach an event log.

    Note: `_flow_result` is deliberately *not* unified here. Its per-adapter
    variants differ in substance (fishing carries `_active_token`, trial and
    world boss carry `proof`, status fallbacks differ), so collapsing them
    would trade a real regression risk for a cosmetic win.
    """
    event = {
        "step": step,
        "ok": bool(result.ok),
        "status_code": int(result.status_code or 0),
        "error_type": result.error_type,
        "attempts": int(result.attempts or 0),
        "data_keys": sorted(result.data) if isinstance(result.data, dict) else [],
        "error": sanitize_webapp_secret_text(result.error),
    }
    if float(getattr(result, "retry_after_sec", 0) or 0) > 0:
        event["retry_after_sec"] = float(result.retry_after_sec)
    events.append(event)


def append_business_capture(capture_sink, *, adapter_key, detail, source="", created_at=None):
    """Append one settlement-only record without protocol credentials.

    Callers must reduce a response to the fixed business fields above before it
    reaches this helper. The second whitelist here is intentional defense in
    depth: an accidental raw response/token/session field is dropped instead of
    becoming another long-lived capture format.
    """
    if capture_sink is None:
        return {}
    adapter_key = str(adapter_key or "").strip()
    if adapter_key not in MINIAPP_BUSINESS_CAPTURE_ADAPTERS:
        return {}
    raw_detail = dict(detail or {})
    business = {
        key: raw_detail[key]
        for key in MINIAPP_BUSINESS_CAPTURE_KEYS
        if key in raw_detail
    }
    record = safe_miniapp_event_detail({
        "adapter_key": adapter_key,
        "step_key": f"business:{adapter_key}",
        "ok": True,
        "created_at": float(created_at if created_at is not None else time.time()),
        "source": sanitize_webapp_secret_text(source, limit=120),
        "business": business,
    })
    try:
        if hasattr(capture_sink, "append"):
            capture_sink.append(record)
        else:
            capture_sink(record)
    except Exception:
        # Capture is diagnostic/accounting plumbing and must never turn a
        # confirmed game settlement into a failed business action.
        return {}
    return record


__all__ = [
    "DEFAULT_MINIAPP_HTTP_TIMEOUT",
    "MINIAPP_DEFAULT_USER_AGENT",
    "append_business_capture",
    "append_http_event",
    "build_miniapp_transport",
    "build_pooled_miniapp_transport",
    "close_pooled_miniapp_sessions",
]
