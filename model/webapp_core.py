import hashlib
from urllib.parse import parse_qs, urlparse


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


def _string(value):
    return str(value or "").strip()


def _url_host(url):
    raw_url = _string(url)
    parsed = urlparse(raw_url)
    if parsed.netloc:
        return parsed.netloc.lower()
    if parsed.scheme or not raw_url:
        return ""
    return urlparse("//" + raw_url).netloc.lower()


def _digest(value):
    raw_value = _string(value)
    if not raw_value:
        return ""
    return hashlib.blake2s(raw_value.encode("utf-8", "surrogatepass"), digest_size=8).hexdigest()


def _summarize_start_param(value):
    raw_value = _string(value)
    if not raw_value:
        return {}
    kind = ""
    if "_" in raw_value:
        kind = raw_value.split("_", 1)[0].lower()[:16]
    return {
        "present": True,
        "kind": kind,
        "suffix": raw_value[-4:],
        "digest": _digest(raw_value),
    }


def summarize_webapp_url(url, *, button_text="", message_text=""):
    """Return a safe WebApp/MiniApp URL summary without persisting credentials."""
    raw_url = _string(url)
    host = _url_host(raw_url)
    if not raw_url and not host:
        return {}
    parsed = urlparse(raw_url)
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
    if any(keyword in text for keyword in ("灵溪", "垂钓", "钓鱼", "fish")):
        return "fishing"
    if any(keyword in text for keyword in ("世界boss", "世界 boss", "真仙试锋", "boss")):
        return "world_boss"
    if any(keyword in text for keyword in ("天道", "审判", "问心", "xianxia-verify", "fanrenxiuxian_bot")):
        return "tiandao_judgement"
    return ""

