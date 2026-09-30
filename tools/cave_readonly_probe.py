"""Explicit one-shot protocol probe; no production runtime imports or DB writes."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import parse_qs, urlparse

import requests
from safety_watchdog import load_dotenv
from telethon import TelegramClient, functions
from telethon.crypto import AuthKey
from telethon.sessions import MemorySession


READS = (".天机盘", ".我的阵法", ".我的灵兽", "fishing_context")
BASE = "https://asc.aiopenai.app/api/miniapp/xianxia-dwelling/"


def entry_parts(value):
    url = urlparse(str(value))
    bot = url.path.strip("/")
    token = (parse_qs(url.query).get("startapp") or [""])[0]
    if (url.scheme != "https" or url.netloc != "t.me"
            or not re.fullmatch(r"(?:fanrenxiuxian|hantianzun\d+)_bot", bot)
            or not re.fullmatch(r"df_[A-Za-z0-9_-]{4,160}", token)):
        raise ValueError("invalid_public_entry")
    return bot, token


def load_owner(root, identity):
    with sqlite3.connect(f"file:{root}/data/state/chaogu_state.db?mode=ro", uri=True) as db:
        meta = {key: json.loads(value) for key, value in db.execute(
            "SELECT key,value FROM meta WHERE key IN ('identity_account_map','accounts','miniapp_auto_config')"
        )}
        owner = int(meta["identity_account_map"][str(identity)])
        if not db.execute("SELECT 1 FROM identities WHERE send_as_id=?", (identity,)).fetchone():
            raise ValueError("identity_missing")
    return owner, meta["accounts"][str(owner)], meta["miniapp_auto_config"]


async def probe(args, report):
    root = args.project_root.resolve()
    owner, account, config = load_owner(root, args.identity)
    urls = config.get("cave_public_entry_urls") or [config.get("cave_public_entry_url")]
    bot_name, token = entry_parts(urls[0])
    report.update(identity_id=args.identity, account_id=owner, read=args.read)
    env = load_dotenv(root / ".env")
    session_path = root / "data/session" / f"account_{owner}.session"
    with sqlite3.connect(f"file:{session_path}?mode=ro", uri=True) as db:
        dc, address, port, key = db.execute("SELECT dc_id,server_address,port,auth_key FROM sessions").fetchone()
    memory = MemorySession()
    memory.set_dc(dc, address, port)
    memory.auth_key = AuthKey(key)
    client = TelegramClient(
        memory, int(account.get("api_id") or env["API_ID"]), account.get("api_hash") or env["API_HASH"],
        receive_updates=False, request_retries=0, connection_retries=0, timeout=15,
    )
    try:
        await client.connect()
        if (await client.get_me()).id != owner:
            raise ValueError("account_mismatch")
        bot = await client.get_input_entity(bot_name)
        view = await client(functions.messages.RequestMainWebViewRequest(
            peer=bot, bot=bot, platform="android", start_param=token,
        ))
        url = urlparse(view.url)
        init = (parse_qs(url.fragment).get("tgWebAppData") or parse_qs(url.query).get("tgWebAppData") or [""])[0]
        if not init or url.hostname != "asc.aiopenai.app":
            raise ValueError("webview_contract_missing")
    finally:
        await client.disconnect()
    with requests.Session() as http:
        http.trust_env = False

        def request(endpoint, player=None):
            if load_owner(root, args.identity)[0] != owner:
                raise ValueError("account_changed")
            payload = {"token": token, "initData": init}
            if player is not None:
                payload["playerId"] = player
            if endpoint == "command-center":
                if args.read not in READS[:-1]:
                    raise ValueError("command_not_read_only")
                payload["command"] = args.read
            elif endpoint == "fishing/context":
                payload["siteId"] = "west-shore"
            elif endpoint not in {"start", "details"}:
                raise ValueError("endpoint_not_read_only")
            time.sleep(2)
            response = http.post(BASE + endpoint, json=payload, timeout=(8, 25), allow_redirects=False)
            step = {"endpoint": endpoint, "status": response.status_code, "at": time.time()}
            report["steps"].append(step)
            response.raise_for_status()
            data = response.json()
            if isinstance(data.get("data"), dict):
                data = data["data"]
            if not isinstance(data, dict):
                raise ValueError("response_not_object")
            step["ok"] = data.get("ok")
            step["keys"] = sorted(data)
            if data.get("ok") is not True:
                raise ValueError("server_rejected_read")
            if endpoint in {"start", "details", "command-center"} and player is not None:
                selected = (data.get("account") or {}).get("playerId")
                step["player_id"] = selected
                if type(selected) is not int or selected != player:
                    raise ValueError("player_mismatch")
            return data

        start = request("start")
        choices = (start.get("identity") or {}).get("choices") or []
        candidates = {args.identity, -1000000000000 - args.identity}
        selected = [row["playerId"] for row in choices if isinstance(row, dict) and row.get("playerId") in candidates]
        if len(selected) != 1:
            raise ValueError("identity_selection_ambiguous")
        player = selected[0]
        report["player_id"] = player
        request("start", player)
        details = request("details", player)
        entries = ((details.get("account") or {}).get("commandCenter") or {}).get("entries") or []
        report["entries"] = [
            {key: row.get(key) for key in ("key", "status", "title", "commands")}
            for row in entries if isinstance(row, dict) and (
                args.read in (row.get("commands") or []) or args.read == "fishing_context" and row.get("key") == "fishing"
            )
        ]
        result = request("fishing/context" if args.read == "fishing_context" else "command-center", player)
        if args.read == "fishing_context":
            context = result.get("context") or {}
            report["context_keys"] = sorted(context)
            report["context"] = {key: context[key] for key in ("enabled", "unavailable", "quota", "conflict", "rod") if key in context}
            if "baits" in context:
                baits = context["baits"]
                if not isinstance(baits, list) or any(not isinstance(bait, dict) for bait in baits):
                    raise ValueError("invalid_fishing_baits")
                report["context"]["baits"] = [
                    {key: bait[key] for key in ("itemId", "name", "count", "unlocked") if key in bait}
                    for bait in baits
                ]
            remote = result.get("session")
            report["session"] = {key: remote.get(key) for key in ("status", "phase", "siteId")} if isinstance(remote, dict) else None
        else:
            action = result.get("actionResult") or {}
            report["action"] = {key: action.get(key) for key in ("ok", "completed", "command", "rawMessage", "message")}
        report["status"] = "read_returned"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("/opt/xiuxian-main"))
    parser.add_argument("--identity", type=int, required=True)
    parser.add_argument("--read", choices=READS, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    report = {"steps": [], "status": "started", "started_at": time.time()}
    # Exclusive creation prevents an interrupted probe from silently replaying.
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(report, output)
    try:
        asyncio.run(asyncio.wait_for(probe(args, report), timeout=120))
    except Exception as exc:
        report.update(status="stopped_no_retry", error_type=type(exc).__name__)
    report["finished_at"] = time.time()
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "read_returned" else 1


if __name__ == "__main__":
    raise SystemExit(main())
