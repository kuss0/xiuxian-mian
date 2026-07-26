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

import requests

from ..config import TG_REQUESTS_PROXIES
from ..webapp_core import sanitize_webapp_secret_text


DEFAULT_MINIAPP_HTTP_TIMEOUT = (5, 20)
MINIAPP_DEFAULT_USER_AGENT = "Mozilla/5.0"


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
    events.append({
        "step": step,
        "ok": bool(result.ok),
        "status_code": int(result.status_code or 0),
        "error_type": result.error_type,
        "attempts": int(result.attempts or 0),
        "data_keys": sorted(result.data) if isinstance(result.data, dict) else [],
        "error": sanitize_webapp_secret_text(result.error),
    })


__all__ = [
    "DEFAULT_MINIAPP_HTTP_TIMEOUT",
    "MINIAPP_DEFAULT_USER_AGENT",
    "append_http_event",
    "build_miniapp_transport",
]
