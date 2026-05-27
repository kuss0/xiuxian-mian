import asyncio
import hashlib
import hmac
import json
import secrets
import time
import urllib.parse

import requests


class ReplicaQueryAggregatorError(Exception):
    pass


def _normalize_base_url(base_url):
    return str(base_url or "").strip().rstrip("/")


def _build_body(payload):
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _build_query_result_body(*, query_message_id, query_text, query_filter, source_id, reply_text, generated_at):
    return _build_body({
        "query_message_id": int(query_message_id or 0),
        "query_text": str(query_text or ""),
        "query_filter": str(query_filter or ""),
        "source_id": str(source_id or ""),
        "reply_text": str(reply_text or ""),
        "generated_at": float(generated_at if generated_at is not None else time.time()),
    })


def _build_virtual_hall_recommendation_body(*, room_id, text, query_message_id, source_id, generated_at):
    payload = {
        "room_id": str(room_id or ""),
        "text": str(text or ""),
        "source_id": str(source_id or ""),
        "generated_at": float(generated_at if generated_at is not None else time.time()),
    }
    try:
        payload["query_message_id"] = int(query_message_id or 0)
    except (TypeError, ValueError):
        payload["query_message_id"] = 0
    return _build_body(payload)


def _sign(secret, method, path, timestamp, nonce, body):
    body_hash = hashlib.sha256(body).hexdigest()
    signing_string = "\n".join([method.upper(), path, timestamp, nonce, body_hash])
    return hmac.new(str(secret or "").encode("utf-8"), signing_string.encode("utf-8"), hashlib.sha256).hexdigest()


def _post(config, path, body):
    base_url = _normalize_base_url((config or {}).get("base_url"))
    client_id = str((config or {}).get("client_id") or "").strip()
    secret = str((config or {}).get("secret") or "").strip()
    path = str(path or "").strip()
    if not base_url or not client_id or not secret:
        raise ReplicaQueryAggregatorError("replica query aggregator not configured")
    if not path.startswith("/"):
        raise ReplicaQueryAggregatorError("invalid replica query aggregator path")
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ReplicaQueryAggregatorError("invalid replica query aggregator base url")
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(16)
    headers = {
        "Content-Type": "application/json",
        "X-Client-Id": client_id,
        "X-Timestamp": timestamp,
        "X-Nonce": nonce,
        "X-Signature": _sign(secret, "POST", path, timestamp, nonce, body),
    }
    try:
        response = requests.post(base_url + path, data=body, headers=headers, timeout=(5, 10))
    except requests.RequestException as exc:
        raise ReplicaQueryAggregatorError(f"request error: {exc}") from exc
    response_body = response.text or ""
    if response.status_code >= 400:
        raise ReplicaQueryAggregatorError(f"http {response.status_code}: {response_body[:500]}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ReplicaQueryAggregatorError(f"invalid json: {response_body[:500]}") from exc
    if not payload.get("ok"):
        raise ReplicaQueryAggregatorError(f"server error: {response_body[:500]}")
    return payload


async def submit_replica_query_result(
    *,
    config,
    query_message_id,
    query_text,
    query_filter,
    source_id,
    reply_text,
    generated_at=None,
):
    body = _build_query_result_body(
        query_message_id=query_message_id,
        query_text=query_text,
        query_filter=query_filter,
        source_id=source_id,
        reply_text=reply_text,
        generated_at=generated_at,
    )
    return await asyncio.to_thread(_post, config, "/v1/replica-query-results", body)


async def submit_virtual_hall_recommendation(
    *,
    config,
    room_id,
    text,
    query_message_id=0,
    source_id="",
    generated_at=None,
):
    body = _build_virtual_hall_recommendation_body(
        room_id=room_id,
        text=text,
        query_message_id=query_message_id,
        source_id=source_id,
        generated_at=generated_at,
    )
    return await asyncio.to_thread(_post, config, "/v1/virtual-hall-recommendations", body)
