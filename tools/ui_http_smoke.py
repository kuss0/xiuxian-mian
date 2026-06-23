#!/usr/bin/env python3
"""Run a short-lived, real HTTP smoke test for the Web UI.

The smoke uses temporary data/session/state directories and a loopback random
port. It does not connect to Telegram, does not send game commands, and patches
UI startup audit logging to a local no-op before starting the HTTP server.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import http.client
import json
import os
import shutil
import socket
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _prepare_env(port: int, data_dir: Path) -> None:
    os.environ["XIUXIAN_TESTING"] = "1"
    os.environ["API_ID"] = "12345"
    os.environ["API_HASH"] = "00000000000000000000000000000000"
    os.environ["TG_PROXY_TYPE"] = ""
    os.environ["TG_PROXY_HOST"] = "127.0.0.1:7890"
    os.environ["LOG_GROUP_ID"] = "0"
    os.environ["LOG_SEND_MODE"] = "account"
    os.environ["ADMIN_ID"] = "1"
    os.environ["CHAOGU_UI_HOST"] = "127.0.0.1"
    os.environ["CHAOGU_UI_PORT"] = str(port)
    os.environ["CHAOGU_UI_PUBLIC_BASE_URL"] = f"http://127.0.0.1:{port}"
    os.environ["XIUXIAN_DATA_DIR"] = str(data_dir)
    os.environ["XIUXIAN_SESSION_DIR"] = str(data_dir / "session")
    os.environ["XIUXIAN_STATE_DIR"] = str(data_dir / "state")
    os.environ["XIUXIAN_MESSAGES_DIR"] = str(data_dir / "messages")
    os.environ["XIUXIAN_DB_FILE"] = str(data_dir / "state" / "chaogu_state.db")
    for key in ("XIUXIAN_SESSION_DIR", "XIUXIAN_STATE_DIR", "XIUXIAN_MESSAGES_DIR"):
        Path(os.environ[key]).mkdir(parents=True, exist_ok=True)


def _http_request(port: int, method: str, path: str, *, body: dict[str, Any] | None = None, cookie: str = ""):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Accept": "application/json, text/html;q=0.9, */*;q=0.8"}
    payload = None
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if cookie:
        headers["Cookie"] = cookie
    conn.request(method, path, body=payload, headers=headers)
    response = conn.getresponse()
    raw_body = response.read()
    response_headers = {key.lower(): value for key, value in response.getheaders()}
    conn.close()
    text = raw_body.decode("utf-8", errors="replace")
    return response.status, response_headers, text


async def _request(port: int, method: str, path: str, *, body: dict[str, Any] | None = None, cookie: str = ""):
    return await asyncio.to_thread(_http_request, port, method, path, body=body, cookie=cookie)


def _json_body(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _add(results: list[CheckResult], name: str, ok: bool, detail: str) -> None:
    results.append(CheckResult(name=name, ok=bool(ok), detail=detail))


async def _run_smoke(port: int) -> list[CheckResult]:
    sys.path.insert(0, str(PROJECT_ROOT))

    from model import runtime, state as state_module, ui  # noqa: WPS433

    async def _noop_audit(*_args, **_kwargs):
        return True

    ui.send_audit_log = _noop_audit

    state_module.ensure_identity_registered(990001)
    state_module.update_send_as_profile(
        990001,
        username="ui_smoke",
        label="UI Smoke",
        daohao="UI Smoke",
        realm="练气期",
        enabled=True,
    )

    results: list[CheckResult] = []
    await ui.start_ui_server()
    try:
        status, _headers, text = await _request(port, "GET", "/api/state")
        _add(results, "unauthenticated state is rejected", status == 401, f"status={status}")

        status, _headers, text = await _request(port, "GET", "/")
        _add(results, "unauthenticated root shows login page", status == 200 and "等待登录链接" in text, f"status={status}")

        status, _headers, text = await _request(port, "POST", "/api/login/exchange", body={})
        _add(results, "empty login token is rejected", status == 400, f"status={status}")

        token = runtime.issue_ui_login_token(1)
        status, headers, text = await _request(port, "POST", "/api/login/exchange", body={"token": token})
        payload = _json_body(text)
        cookie_header = headers.get("set-cookie", "")
        cookie = cookie_header.split(";", 1)[0]
        _add(
            results,
            "login token exchange returns session cookie",
            status == 200 and payload.get("ok") is True and cookie,
            f"status={status} cookie={'yes' if cookie else 'no'}",
        )

        status, _headers, text = await _request(port, "GET", "/", cookie=cookie)
        _add(results, "authenticated root renders boot data", status == 200 and "CHAOGU_BOOT_DATA" in text, f"status={status}")

        status, _headers, text = await _request(port, "GET", "/static/js/app.js", cookie=cookie)
        _add(results, "app javascript is served", status == 200 and "const appState" in text, f"status={status}")

        status, _headers, text = await _request(port, "GET", "/static/js/fishing_ui.js", cookie=cookie)
        _add(
            results,
            "fishing javascript is served",
            status == 200 and "/api/fishing-config" in text,
            f"status={status}",
        )

        status, _headers, text = await _request(port, "GET", "/static/css/app.css", cookie=cookie)
        _add(results, "app css is served", status == 200 and ".topbar" in text, f"status={status}")

        status, _headers, text = await _request(port, "GET", "/api/state", cookie=cookie)
        payload = _json_body(text)
        snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
        identities = snapshot.get("identities") if isinstance(snapshot.get("identities"), list) else []
        identity = identities[0] if identities else {}
        _add(
            results,
            "authenticated state exposes expected snapshot",
            status == 200 and payload.get("ok") is True and bool(identities) and "passive_inbox" in snapshot and "fishing" in identity,
            f"status={status} identities={len(identities)}",
        )

        status, _headers, text = await _request(
            port,
            "POST",
            "/api/fishing-config",
            body={
                "send_as_id": 990001,
                "pond": "青溪浅滩",
                "bait": "灵米饵",
                "auto_chum_enabled": True,
                "chum_name": "灵草窝",
                "auto_buy_bait_enabled": True,
                "auto_probe_enabled": True,
            },
            cookie=cookie,
        )
        payload = _json_body(text)
        snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
        identities = snapshot.get("identities") if isinstance(snapshot.get("identities"), list) else []
        fishing = (identities[0].get("fishing") if identities else {}) or {}
        _add(
            results,
            "fishing config api persists and returns snapshot",
            status == 200
            and payload.get("ok") is True
            and fishing.get("pond") == "青溪浅滩"
            and fishing.get("bait") == "灵米饵"
            and fishing.get("auto_chum_enabled") is True
            and fishing.get("chum_name") == "灵草窝"
            and fishing.get("auto_buy_bait_enabled") is True
            and fishing.get("auto_probe_enabled") is True,
            f"status={status}",
        )

        status, _headers, text = await _request(port, "GET", "/api/logs/days", cookie=cookie)
        payload = _json_body(text)
        _add(results, "logs days api returns json", status == 200 and payload.get("ok") is True and isinstance(payload.get("days"), list), f"status={status}")

        status, _headers, text = await _request(port, "GET", "/api/logs/entries?limit=5", cookie=cookie)
        payload = _json_body(text)
        _add(results, "logs entries api returns json", status == 200 and payload.get("ok") is True and "entries" in payload, f"status={status}")

        status, _headers, text = await _request(port, "GET", "/api/official-schedules", cookie=cookie)
        payload = _json_body(text)
        _add(
            results,
            "official schedules api returns json",
            status == 200 and payload.get("ok") is True and isinstance(payload.get("official_schedules"), list),
            f"status={status}",
        )
    finally:
        await ui.stop_ui_server()
    return results


def _print_results(results: list[CheckResult], *, json_output: bool = False) -> None:
    payload = {
        "ok": all(item.ok for item in results),
        "checks": [item.__dict__ for item in results],
    }
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    for item in results:
        marker = "OK" if item.ok else "FAIL"
        print(f"[{marker}] {item.name}: {item.detail}")
    print(f"result={'ok' if payload['ok'] else 'failed'} checks={len(results)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a temporary real-HTTP smoke test for the Web UI.")
    parser.add_argument("--port", type=int, default=0, help="Loopback port. Default: choose a free port.")
    parser.add_argument("--keep-data", action="store_true", help="Keep the temporary data directory for debugging.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args(argv)

    port = int(args.port or 0) or _pick_free_port()
    data_dir = Path(tempfile.mkdtemp(prefix="xiuxian-ui-smoke-"))
    try:
        _prepare_env(port, data_dir)
        if args.json:
            with contextlib.redirect_stdout(sys.stderr):
                results = asyncio.run(_run_smoke(port))
        else:
            results = asyncio.run(_run_smoke(port))
        _print_results(results, json_output=bool(args.json))
        return 0 if all(item.ok for item in results) else 1
    finally:
        if args.keep_data:
            print(f"kept_data_dir={data_dir}", file=sys.stderr)
        else:
            shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
