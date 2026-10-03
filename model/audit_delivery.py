"""Content-free telemetry for notification transport attempts, not game sends."""

import hashlib
import json
import re
import time
import uuid

from telethon.extensions import html as telegram_html


RECEIPT_PREFIX = "TG_NOTIFICATION_DELIVERY "


def delivery_receipt(text, *, chat_id, transport, outcome, elapsed_sec, parse_mode=None):
    if transport not in {"bot", "account"} or outcome not in {"confirmed", "unconfirmed", "unknown"}:
        raise ValueError("Invalid notification transport result")
    text = str(text)
    visible = telegram_html.parse(text)[0] if str(parse_mode or "").lower() == "html" else text
    return {
        "schema": 1,
        "receipt_id": uuid.uuid4().hex,
        "at": time.time(),
        "chat_id": int(chat_id),
        "transport": transport,
        "outcome": outcome,
        "elapsed_ms": round(max(0, elapsed_sec) * 1000),
        "payload_sha256": hashlib.sha256(text.encode("utf-8", errors="surrogatepass")).hexdigest(),
        "utf16_units": len(visible.encode("utf-16-le", errors="surrogatepass")) // 2,
        "lines": visible.count("\n") + 1,
        "explicit_mention_links": len(re.findall(r'<a\s+href=[\"\']tg://user\?id=\d+', text))
        if str(parse_mode or "").lower() == "html" else 0,
    }


def emit_delivery_receipt(text, **metadata):
    # Never let diagnostics turn a successful send into a failure/retry.
    try:
        print(RECEIPT_PREFIX + json.dumps(delivery_receipt(text, **metadata), separators=(",", ":")))
    except Exception as exc:
        try:
            print(f"notification receipt unavailable: {type(exc).__name__}")
        except Exception:
            pass
