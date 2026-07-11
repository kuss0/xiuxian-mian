import asyncio
import json
import re
import urllib.parse
from dataclasses import dataclass
from http.cookies import SimpleCookie

import requests


DEFAULT_TIANJIGE_BASE_URL = "https://asc.aiopenai.app"
VERIFY_PATH = "/api/bootstrap"
REFRESH_PATH = "/api/me"
CULTIVATOR_PATH_PREFIX = "/api/cultivator/"
_DASHBOARD_API_TOKEN_RE = re.compile(r"window\.DASHBOARD_API_TOKEN\s*=\s*(['\"])(?P<token>.*?)\1")


class StorageBagApiError(Exception):
    def __init__(
        self,
        message,
        *,
        status_code=0,
        auth_failed=False,
        rate_limited=False,
        cookie="",
        api_token="",
    ):
        super().__init__(message)
        self.status_code = int(status_code or 0)
        self.auth_failed = bool(auth_failed)
        self.rate_limited = bool(rate_limited)
        self.cookie = str(cookie or "").strip()
        self.api_token = str(api_token or "").strip()


@dataclass(frozen=True)
class StorageBagApiResult:
    payload: object
    status_code: int
    cookie: str
    api_token: str
    path: str


def _normalize_base_url(base_url):
    value = str(base_url or "").strip().rstrip("/")
    return value or DEFAULT_TIANJIGE_BASE_URL


def normalize_storage_bag_api_cookie(raw_cookie):
    text = str(raw_cookie or "").strip()
    if not text:
        return ""
    if text.lower().startswith("cookie:"):
        text = text.split(":", 1)[1].strip()
    # Browser cookie viewers may display the Flask session as
    # ``session.<signed-value>`` instead of a Cookie header assignment.
    # Preserve the leading dot that belongs to the signed value.
    if text.lower().startswith("session.") and ";" not in text:
        text = f"session=.{text[len('session.'):]}"
    parts = [part.strip() for part in text.replace("\n", ";").split(";") if part.strip()]
    session_part = next((part for part in parts if part.lower().startswith("session=")), "")
    return session_part or "; ".join(parts)


def _normalize_path(path, default_path=REFRESH_PATH):
    path = str(path or default_path).strip() or default_path
    return path if path.startswith("/") else f"/{path}"


def build_cultivator_path(username):
    identifier = str(username or "").strip().lstrip("@")
    if not identifier:
        raise StorageBagApiError("缺少天机阁修士用户名")
    return CULTIVATOR_PATH_PREFIX + urllib.parse.quote(identifier, safe="")


def _validate_base_url(base_url):
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise StorageBagApiError("天机阁 Base URL 无效")


def _extract_dashboard_api_token(html_text):
    match = _DASHBOARD_API_TOKEN_RE.search(str(html_text or ""))
    return match.group("token").strip() if match else ""


def _extract_session_cookie_from_headers(headers):
    if not headers:
        return ""
    raw_values = []
    get_all = getattr(headers, "get_all", None)
    if callable(get_all):
        raw_values.extend(get_all("Set-Cookie") or [])
    getlist = getattr(headers, "getlist", None)
    if callable(getlist):
        raw_values.extend(getlist("Set-Cookie") or [])
    if hasattr(headers, "get"):
        raw = headers.get("Set-Cookie")
        if raw:
            raw_values.append(raw)
    for raw in raw_values:
        cookie = SimpleCookie()
        try:
            cookie.load(str(raw or ""))
        except Exception:
            cookie = SimpleCookie()
        if "session" in cookie:
            return f"session={cookie['session'].value}"
        for part in str(raw or "").split(";"):
            candidate = part.strip()
            if candidate.lower().startswith("session="):
                return candidate
    return ""


def _extract_session_cookie_from_response(response):
    if response is None:
        return ""
    try:
        jar_cookie = (response.cookies or {}).get("session")
    except Exception:
        jar_cookie = ""
    if jar_cookie:
        return f"session={jar_cookie}"
    return _extract_session_cookie_from_headers(getattr(response, "headers", None))


def _resolve_session_cookie(primary_cookie, fallback_cookie):
    primary = normalize_storage_bag_api_cookie(primary_cookie)
    fallback = normalize_storage_bag_api_cookie(fallback_cookie)
    return primary if primary.lower().startswith("session=") else fallback


def _html_headers(cookie):
    headers = {
        "Accept": "text/html,application/xhtml+xml",
        "User-Agent": "Mozilla/5.0",
    }
    if cookie:
        headers["Cookie"] = cookie
    return headers


def _json_headers(base_url, cookie, api_token):
    headers = {
        "Accept": "application/json,text/plain,*/*",
        "Referer": f"{base_url}/",
        "User-Agent": "Mozilla/5.0",
    }
    if cookie:
        headers["Cookie"] = cookie
    if api_token:
        headers["X-API-Token"] = api_token
    return headers


def _response_error(response, *, cookie="", api_token=""):
    status = int(getattr(response, "status_code", 0) or 0)
    body = str(getattr(response, "text", "") or "").strip()
    message = ""
    if body:
        try:
            payload = json.loads(body)
            if isinstance(payload, dict):
                message = str(payload.get("error") or payload.get("message") or "").strip()
        except ValueError:
            message = body[:200]
    if not message:
        message = f"HTTP {status}"
    return StorageBagApiError(
        message,
        status_code=status,
        auth_failed=status in {401, 403},
        rate_limited=status == 429,
        cookie=cookie,
        api_token=api_token,
    )


def _bootstrap_dashboard_auth(session, base_url, cookie):
    response = session.get(base_url + "/", headers=_html_headers(cookie), timeout=(5, 20))
    refreshed_cookie = _resolve_session_cookie(_extract_session_cookie_from_response(response), cookie)
    if response.status_code >= 400:
        raise _response_error(response, cookie=refreshed_cookie, api_token="")
    return refreshed_cookie, _extract_dashboard_api_token(response.text)


def _request_json_sync(config, path):
    config = config if isinstance(config, dict) else {}
    base_url = _normalize_base_url(config.get("base_url"))
    _validate_base_url(base_url)
    cookie = normalize_storage_bag_api_cookie(config.get("cookie"))
    api_token = str(config.get("api_token") or "").strip()
    if not cookie:
        raise StorageBagApiError("请先配置天机阁 session Cookie")
    path = _normalize_path(path)

    session = requests.Session()
    if not api_token:
        cookie, api_token = _bootstrap_dashboard_auth(session, base_url, cookie)

    def do_get(active_cookie, active_token):
        response = session.get(
            base_url + path,
            headers=_json_headers(base_url, active_cookie, active_token),
            timeout=(5, 20),
        )
        refreshed_cookie = _resolve_session_cookie(_extract_session_cookie_from_response(response), active_cookie)
        return response, refreshed_cookie

    try:
        response, refreshed_cookie = do_get(cookie, api_token)
        if response.status_code in {401, 403} and api_token:
            try:
                refreshed_cookie, api_token = _bootstrap_dashboard_auth(session, base_url, refreshed_cookie or cookie)
                response, refreshed_cookie = do_get(refreshed_cookie, api_token)
            except StorageBagApiError as exc:
                raise StorageBagApiError(
                    str(exc),
                    status_code=exc.status_code,
                    auth_failed=exc.auth_failed,
                    rate_limited=exc.rate_limited,
                    cookie=exc.cookie or refreshed_cookie,
                    api_token=exc.api_token or api_token,
                ) from exc

        if response.status_code >= 400:
            raise _response_error(response, cookie=refreshed_cookie, api_token=api_token)
        try:
            payload = response.json()
        except ValueError as exc:
            raise StorageBagApiError("天机阁返回的不是 JSON", cookie=refreshed_cookie, api_token=api_token) from exc
        return StorageBagApiResult(
            payload=payload,
            status_code=int(response.status_code or 0),
            cookie=refreshed_cookie or cookie,
            api_token=api_token,
            path=path,
        )
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()


async def fetch_storage_bag_result(config, path=REFRESH_PATH):
    return await asyncio.to_thread(_request_json_sync, config, path)


async def fetch_storage_bag_cultivator_result(config, username):
    return await fetch_storage_bag_result(config, build_cultivator_path(username))


async def fetch_storage_bag_payload(config, payload=None):
    result = await fetch_storage_bag_result(config, (payload or {}).get("path") or REFRESH_PATH)
    return result.payload


async def verify_storage_bag_api(config):
    result = await fetch_storage_bag_result(config, VERIFY_PATH)
    payload = result.payload if isinstance(result.payload, dict) else {}
    game_items = payload.get("game_items") if isinstance(payload, dict) else {}
    item_name_map = {}
    if isinstance(game_items, dict):
        for item_id, item in game_items.items():
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                if name:
                    item_name_map[str(item_id)] = name
    return {
        "ok": True,
        "verified": True,
        "cookie": result.cookie,
        "api_token": result.api_token,
        "item_name_map": item_name_map,
        "status_code": result.status_code,
    }
