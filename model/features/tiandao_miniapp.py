import asyncio
import random
import re
from urllib.parse import parse_qs, quote, urlparse

import requests
from telethon import functions

from ..config import (
    TG_REQUESTS_PROXIES,
    TIANDAO_MINIAPP_BOT_USERNAME,
    TIANDAO_MINIAPP_VERIFY_URL,
)
from ..runtime import _get_identity_client


RE_TIANDAO_MINIAPP_TOKEN = re.compile(r"\b(?P<token>(?P<kind>rpt|stk)_[A-Z0-9]+)\b", re.IGNORECASE)
RE_TIANDAO_MINIAPP_TARGET = re.compile(r"对象\s*(?:@[^\s，。！？、；：:,.!?()（）【】\[\]]+\s*)?[（(]?[【\[]\s*([^】\]]+?)\s*[】\]][）)]?")
RE_SENSITIVE_QUERY = re.compile(r"(?P<key>tgWebAppData|initData|query_id|hash|user)=([^&#\s]+)")
RE_SENSITIVE_MINIAPP_TOKEN = re.compile(r"\b(?P<kind>rpt|stk)_[A-Z0-9]+\b", re.IGNORECASE)

_MINIAPP_ALLOWED_TME_HOSTS = {"t.me", "telegram.me"}
_MINIAPP_VERIFY_SOURCE = "xianxia_verify_miniapp_drag"
_MINIAPP_TRACK_WIDTH = 402.0
_MINIAPP_KNOB_WIDTH = 74.0
_MINIAPP_TRACK_CENTER_Y = 38.0
_MINIAPP_HTTP_TIMEOUT = (5, 20)


class TiandaoMiniappError(Exception):
    pass


def sanitize_tiandao_miniapp_error(error):
    text = str(error or "")
    text = RE_SENSITIVE_QUERY.sub(lambda match: f"{match.group('key')}=<redacted>", text)
    text = RE_SENSITIVE_MINIAPP_TOKEN.sub(lambda match: f"{match.group('kind').lower()}_<redacted>", text)
    return text[:220]


def summarize_tiandao_miniapp_token(token):
    raw_token = str(token or "").strip()
    match = RE_TIANDAO_MINIAPP_TOKEN.fullmatch(raw_token)
    if not match:
        return ""
    kind = str(match.group("kind") or "").lower()
    payload = raw_token.split("_", 1)[1] if "_" in raw_token else ""
    return f"{kind}_...{payload[-4:]}" if payload else f"{kind}_<redacted>"


def _round1(value):
    return round(float(value) * 10) / 10


def _clamp(value, minimum, maximum):
    return max(minimum, min(maximum, value))


def _parse_controlled_startapp_url(url):
    raw_url = str(url or "").strip()
    if not raw_url:
        return None
    parsed = urlparse(raw_url)
    host = parsed.netloc.lower()
    path_parts = [part for part in parsed.path.strip("/").split("/") if part]
    query = parse_qs(parsed.query)
    token = (query.get("startapp") or query.get("start_param") or [""])[0]
    if not token:
        return None

    allowed = False
    if (
        host in _MINIAPP_ALLOWED_TME_HOSTS
        and path_parts
        and path_parts[0].lower() == TIANDAO_MINIAPP_BOT_USERNAME.lower()
        and (len(path_parts) == 1 or path_parts[1].lower() == "app")
    ):
        allowed = True
    verify = urlparse(TIANDAO_MINIAPP_VERIFY_URL)
    if host == verify.netloc.lower() and parsed.path == verify.path:
        allowed = True
    if not allowed:
        return None

    match = RE_TIANDAO_MINIAPP_TOKEN.fullmatch(token.strip())
    if not match:
        return None
    return {"token": match.group("token"), "kind": str(match.group("kind") or "").lower()}


def _extract_button_urls(event):
    message = getattr(event, "message", None) or event
    rows = getattr(message, "buttons", None) or []
    urls = []
    for row in rows or []:
        row_buttons = row if isinstance(row, (list, tuple)) else [row]
        for button in row_buttons or []:
            raw_button = getattr(button, "button", None) or button
            url = getattr(raw_button, "url", "") or getattr(raw_button, "webview", "") or getattr(raw_button, "web_view", "")
            if url:
                urls.append(str(url))
    return urls


def _extract_text_urls(text):
    return re.findall(r"https?://[^\s<>\"'）)】\]]+", str(text or ""))


def extract_tiandao_miniapp_challenge(text, event=None, *, timeout_sec=180):
    raw_text = str(text or "")
    target_match = RE_TIANDAO_MINIAPP_TARGET.search(raw_text)
    target = str(target_match.group(1) or "").strip() if target_match else ""

    text_urls = _extract_text_urls(raw_text)
    button_urls = _extract_button_urls(event) if event is not None else []
    for url in text_urls + button_urls:
        parsed = _parse_controlled_startapp_url(url)
        if parsed:
            return {**parsed, "target": target, "timeout_sec": timeout_sec}

    if "Mini App" not in raw_text and "拖动验证" not in raw_text:
        return None
    fallback_text = raw_text
    for url in text_urls:
        fallback_text = fallback_text.replace(url, " ")
    token_match = RE_TIANDAO_MINIAPP_TOKEN.search(fallback_text)
    if not token_match:
        return None
    return {
        "token": token_match.group("token"),
        "kind": str(token_match.group("kind") or "").lower(),
        "target": target,
        "timeout_sec": timeout_sec,
    }


def _get_webview_init_data(webview_url):
    parsed = urlparse(str(webview_url or ""))
    fragment = parse_qs(parsed.fragment)
    init_data = (fragment.get("tgWebAppData") or [""])[0]
    if not init_data:
        raise TiandaoMiniappError("WebView URL 缺少 tgWebAppData")
    return init_data


async def _request_webview_init_data(identity_id, token):
    client = _get_identity_client(identity_id)
    if client is None:
        raise TiandaoMiniappError("身份客户端不可用")
    bot = await client.get_entity(TIANDAO_MINIAPP_BOT_USERNAME)
    bot_input = await client.get_input_entity(bot)
    verify_url = f"{TIANDAO_MINIAPP_VERIFY_URL}?startapp={quote(token, safe='')}"
    result = await client(functions.messages.RequestWebViewRequest(
        peer=bot_input,
        bot=bot_input,
        platform="android",
        from_bot_menu=False,
        url=verify_url,
        start_param=token,
    ))
    return _get_webview_init_data(result.url)


def _post_miniapp_json(path, payload):
    url = TIANDAO_MINIAPP_VERIFY_URL.rstrip("/")
    parsed = urlparse(url)
    endpoint = f"{parsed.scheme}://{parsed.netloc}{path}"
    response = requests.post(
        endpoint,
        json=payload,
        headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"},
        proxies=TG_REQUESTS_PROXIES,
        timeout=_MINIAPP_HTTP_TIMEOUT,
    )
    try:
        data = response.json()
    except ValueError as exc:
        raise TiandaoMiniappError(f"HTTP {response.status_code} 返回非 JSON") from exc
    if not response.ok or not data.get("ok"):
        error = data.get("error") or data.get("message") or f"HTTP {response.status_code}"
        raise TiandaoMiniappError(str(error))
    return data


def _load_drag_challenge(token):
    data = _post_miniapp_json("/api/miniapp/xianxia-drag-challenge", {"token": token})
    challenge = data.get("challenge")
    if not isinstance(challenge, dict):
        raise TiandaoMiniappError("拖动挑战缺失")
    return challenge


def build_tiandao_miniapp_drag_proof(challenge):
    challenge = dict(challenge or {})
    challenge_id = str(challenge.get("challengeId") or "").strip()
    if not challenge_id:
        raise TiandaoMiniappError("challengeId 缺失")

    target_ratio = float(challenge.get("targetRatio") or 0.8)
    min_duration_ms = float(challenge.get("minDurationMs") or 800)
    min_points = int(challenge.get("minPoints") or 10)
    track_width = _MINIAPP_TRACK_WIDTH
    knob_width = _MINIAPP_KNOB_WIDTH
    max_x = max(1.0, track_width - knob_width)
    target_x = _clamp(max_x * target_ratio, 0.0, max_x)
    tolerance = max(18.0, min(42.0, max_x * 0.08))
    final_x = _clamp(target_x + random.uniform(-min(4.0, tolerance / 4), min(4.0, tolerance / 4)), 0.0, max_x)
    duration_ms = max(min_duration_ms, 1800.0) + random.uniform(300.0, 900.0)
    point_count = min(60, max(min_points, 16) + random.randint(2, 8))

    points = []
    last_x = None
    for index in range(point_count):
        progress = index / max(1, point_count - 1)
        ease = progress * progress * (3 - 2 * progress)
        x = final_x * ease
        if index not in {0, point_count - 1}:
            x += random.uniform(-1.2, 1.2)
        x = _clamp(x, 0.0, max_x)
        if last_x is not None:
            x = max(0.0, min(max_x, max(last_x - 0.8, x)))
        if index == point_count - 1:
            x = final_x
        last_x = x
        points.append({
            "x": _round1(x),
            "y": _round1(_MINIAPP_TRACK_CENTER_Y + random.uniform(-1.8, 1.8)),
            "t": _round1(duration_ms * progress),
        })

    return {
        "challengeId": challenge_id,
        "durationMs": _round1(duration_ms),
        "trackWidth": _round1(track_width),
        "knobWidth": _round1(knob_width),
        "maxX": _round1(max_x),
        "targetX": _round1(target_x),
        "finalX": _round1(final_x),
        "points": points,
    }


def _submit_drag_proof(token, init_data, proof):
    return _post_miniapp_json("/api/miniapp/xianxia-verify", {
        "token": token,
        "initData": init_data,
        "source": _MINIAPP_VERIFY_SOURCE,
        "dragProof": proof,
    })


async def run_tiandao_miniapp_drag_verification(identity_id, token):
    try:
        init_data = await _request_webview_init_data(identity_id, token)
        challenge = await asyncio.to_thread(_load_drag_challenge, token)
        proof = build_tiandao_miniapp_drag_proof(challenge)
        await asyncio.to_thread(_submit_drag_proof, token, init_data, proof)
        return {"ok": True, "status": "submitted", "error": ""}
    except Exception as exc:
        return {"ok": False, "status": "failed", "error": sanitize_tiandao_miniapp_error(exc)}


__all__ = [
    "build_tiandao_miniapp_drag_proof",
    "extract_tiandao_miniapp_challenge",
    "run_tiandao_miniapp_drag_verification",
    "sanitize_tiandao_miniapp_error",
    "summarize_tiandao_miniapp_token",
]
