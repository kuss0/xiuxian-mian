#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CAPTURE_DIR = PROJECT_ROOT / "data" / "state" / "miniapp_capture"
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"
TZ_LOCAL = ZoneInfo("Asia/Shanghai")


def _load_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def _iter_records(game_key: str, day: str, capture_dir: Path = CAPTURE_DIR):
    path = capture_dir / f"{game_key}-{day}.jsonl"
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _body(record: dict) -> dict:
    body = ((record.get("response") or {}).get("body") or {})
    return body if isinstance(body, dict) else {}


def _int(value, default: int = 0) -> int:
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError, OverflowError):
        return default


def _add_item(counter: Counter, name: str, qty=1):
    name = str(name or "").strip()
    amount = _int(qty, 0)
    if name and amount > 0:
        counter[name] += amount


def _add_gain(counter: Counter, name: str, amount=0):
    name = str(name or "").strip()
    value = _int(amount, 0)
    if name and value > 0:
        counter[name] += value


def _add_loot(counter: Counter, items):
    if isinstance(items, dict):
        if any(key in items for key in ("name", "itemName", "item_name", "label", "title")):
            name = items.get("name") or items.get("itemName") or items.get("item_name") or items.get("label") or items.get("title")
            qty = items.get("quantity", items.get("qty", items.get("count", items.get("amount", 1))))
            _add_item(counter, name, qty)
            return
        for name, qty in items.items():
            _add_item(counter, name, qty)
        return
    for item in items or ():
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("itemName") or item.get("label")
        qty = item.get("quantity", item.get("qty", item.get("count", 1)))
        _add_item(counter, name, qty)


def _first_int_by_keys(source: dict, keys: tuple[str, ...]) -> int:
    source = source if isinstance(source, dict) else {}
    for key in keys:
        if key in source:
            amount = _int(source.get(key), 0)
            if amount:
                return amount
    return 0


def _format_counter(counter: Counter, empty: str = "无") -> str:
    parts = [f"{name}x{amount}" for name, amount in sorted(counter.items()) if amount > 0]
    return "、".join(parts) if parts else empty


def _format_gains(counter: Counter, empty: str = "无") -> str:
    parts = [f"{name}+{amount}" for name, amount in sorted(counter.items()) if amount > 0]
    return "、".join(parts) if parts else empty


def summarize_fishing(day: str, capture_dir: Path = CAPTURE_DIR) -> dict:
    seen_sessions: set[str] = set()
    fish = Counter()
    rewards = Counter()
    gains = Counter()
    rods = caught = empty = 0
    for record in _iter_records("fishing", day, capture_dir) or ():
        if record.get("step_key") != "result" or not record.get("ok"):
            continue
        result = _body(record).get("result") or {}
        if not isinstance(result, dict) or not result.get("ready"):
            continue
        session_id = str(result.get("sessionId") or result.get("session_id") or "")
        if session_id and session_id in seen_sessions:
            continue
        if session_id:
            seen_sessions.add(session_id)
        rods += 1
        if result.get("caught"):
            caught += 1
            fish_info = result.get("fish") if isinstance(result.get("fish"), dict) else {}
            _add_item(fish, fish_info.get("name") or "未知灵鱼", 1)
        else:
            empty += 1
        _add_gain(gains, "钓术经验", result.get("expGain"))
        _add_loot(rewards, result.get("bonusLoot"))
    return {"rods": rods, "caught": caught, "empty": empty, "fish": fish, "rewards": rewards, "gains": gains}


def summarize_trial(day: str, capture_dir: Path = CAPTURE_DIR) -> dict:
    seen_events: set[str] = set()
    gains = Counter()
    rewards = Counter()
    count = 0
    errors = 0
    for record in _iter_records("trial", day, capture_dir) or ():
        if record.get("step_key") != "finish":
            continue
        if not record.get("ok"):
            errors += 1
            continue
        body = _body(record)
        event_id = str(body.get("event_id") or "")
        if event_id and event_id in seen_events:
            continue
        if event_id:
            seen_events.add(event_id)
        result = body.get("result") if isinstance(body.get("result"), dict) else {}
        count += 1
        _add_gain(gains, "天机残痕", _first_int_by_keys(result, ("reward_trace", "rewardTrace", "traceGain", "trace_gain", "trace")))
        _add_gain(gains, "经验", _first_int_by_keys(result, ("expGain", "exp_gain", "experienceGain", "experience")))
        for key in ("rewards", "reward", "bonusLoot", "loot", "drops", "items", "materials"):
            _add_loot(rewards, result.get(key))
    return {"count": count, "errors": errors, "gains": gains, "rewards": rewards}


def summarize_cave_treasure(day: str, capture_dir: Path = CAPTURE_DIR) -> dict:
    gains = Counter()
    rewards = Counter()
    count = 0
    found_main = 0
    for record in _iter_records("cave_treasure", day, capture_dir) or ():
        if record.get("step_key") != "action:settle" or not record.get("ok"):
            continue
        body = _body(record)
        result = body.get("huntResult") if isinstance(body.get("huntResult"), dict) else {}
        count += 1
        if result.get("foundMain"):
            found_main += 1
        _add_gain(gains, "洞府贡献", _first_int_by_keys(result, ("contribution", "contributionGain", "contribution_gain")))
        _add_gain(gains, "修为", _first_int_by_keys(result, ("cultivationGain", "xiuweiGain", "xiuwei_gain")))
        _add_gain(gains, "灵石", _first_int_by_keys(result, ("lingshiGain", "spiritStoneGain", "stoneGain", "stone_gain")))
        _add_gain(gains, "经验", _first_int_by_keys(result, ("expGain", "exp_gain", "experienceGain", "experience")))
        for key in ("loot", "rewards", "reward", "bonusLoot", "drops", "items", "materials", "item_deltas", "itemDeltas"):
            _add_loot(rewards, result.get(key))
    return {"count": count, "found_main": found_main, "gains": gains, "rewards": rewards}


def summarize_stargazer(day: str, capture_dir: Path = CAPTURE_DIR) -> dict:
    action_counts = Counter()
    rewards = Counter()
    for record in _iter_records("stargazer", day, capture_dir) or ():
        if not record.get("ok"):
            continue
        step = str(record.get("step_key") or "")
        if step.startswith("action_"):
            action_counts[step.removeprefix("action_")] += 1
        result = _body(record).get("actionResult")
        if isinstance(result, dict):
            for key in ("rewards", "reward", "loot", "drops", "items", "materials", "item_deltas", "itemDeltas"):
                _add_loot(rewards, result.get(key))
    return {"actions": action_counts, "rewards": rewards}


def summarize_tree(day: str, capture_dir: Path = CAPTURE_DIR) -> dict:
    runs = Counter()
    gains = Counter()
    rewards = Counter()
    for record in _iter_records("tree", day, capture_dir) or ():
        if record.get("step_key") != "run_submit" or not record.get("ok"):
            continue
        body = _body(record)
        run = body.get("run") if isinstance(body.get("run"), dict) else {}
        mode = str(run.get("mode") or "unknown")
        runs[mode] += 1
        result = body.get("result") if isinstance(body.get("result"), dict) else {}
        for source in (body, run, result):
            _add_gain(gains, "修为", _first_int_by_keys(source, ("cultivationGain", "xiuweiGain", "xiuwei_gain")))
            _add_gain(gains, "灵石", _first_int_by_keys(source, ("lingshiGain", "spiritStoneGain", "stoneGain", "stone_gain")))
            _add_gain(gains, "经验", _first_int_by_keys(source, ("expGain", "exp_gain", "experienceGain", "experience")))
            _add_gain(gains, "贡献", _first_int_by_keys(source, ("contributionGain", "contribution_gain", "contribution")))
            for key in ("rewards", "reward", "bonusLoot", "loot", "drops", "items", "materials", "item_deltas", "itemDeltas"):
                _add_loot(rewards, source.get(key))
    return {"runs": runs, "gains": gains, "rewards": rewards}


def build_report(day: str, capture_dir: Path = CAPTURE_DIR) -> str:
    fishing = summarize_fishing(day, capture_dir)
    trial = summarize_trial(day, capture_dir)
    cave = summarize_cave_treasure(day, capture_dir)
    stargazer = summarize_stargazer(day, capture_dir)
    tree = summarize_tree(day, capture_dir)
    lines = [f"🧩 MiniApp今日成果补播｜{day}"]
    if fishing["rods"]:
        parts = [f"{fishing['rods']}竿", f"中鱼{fishing['caught']}", f"空竿{fishing['empty']}"]
        if fishing["fish"]:
            parts.append("鱼获:" + _format_counter(fishing["fish"]))
        if fishing["gains"]:
            parts.append("收益:" + _format_gains(fishing["gains"]))
        if fishing["rewards"]:
            parts.append("伴生:" + _format_counter(fishing["rewards"]))
        lines.append("🎣 灵溪垂钓：" + "｜".join(parts))
    if trial["count"] or trial["errors"]:
        parts = [f"{trial['count']}次成功"]
        if trial["errors"]:
            parts.append(f"{trial['errors']}次失败")
        if trial["gains"]:
            parts.append("收益:" + _format_gains(trial["gains"]))
        if trial["rewards"]:
            parts.append("奖励:" + _format_counter(trial["rewards"]))
        lines.append("🧪 天机试炼：" + "｜".join(parts))
    if cave["count"]:
        parts = [f"{cave['count']}局", f"主宝{cave['found_main']}"]
        if cave["gains"]:
            parts.append("收益:" + _format_gains(cave["gains"]))
        if cave["rewards"]:
            parts.append("奖励:" + _format_counter(cave["rewards"]))
        lines.append("🕳️ 洞府寻宝：" + "｜".join(parts))
    if stargazer["rewards"]:
        lines.append("🔭 观星台：奖励:" + _format_counter(stargazer["rewards"]))
    if tree["gains"] or tree["rewards"]:
        parts = []
        if tree["gains"]:
            parts.append("收益:" + _format_gains(tree["gains"]))
        if tree["rewards"]:
            parts.append("奖励:" + _format_counter(tree["rewards"]))
        lines.append("🌳 灵树：" + "｜".join(parts))
    if len(lines) == 1:
        lines.append("暂无 MiniApp 结算成果。")
    lines.append("注：只统计 MiniApp 最终结算中的游戏收益/物资。")
    return "\n".join(lines)


def send_log_group(message: str, env_file: Path = DEFAULT_ENV_FILE):
    env = {**_load_env_file(env_file), **os.environ}
    token = str(env.get("LOG_BOT_TOKEN") or "").strip()
    chat_id = str(env.get("LOG_GROUP_ID") or "").strip()
    if not token or not chat_id:
        raise RuntimeError("LOG_BOT_TOKEN 或 LOG_GROUP_ID 为空，无法发送日志群")
    body = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    with urllib.request.urlopen(url, data=body, timeout=20) as response:
        payload = response.read().decode("utf-8", errors="replace")
        if response.status >= 400:
            raise RuntimeError(f"日志群发送失败 HTTP {response.status}: {payload}")
        return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MiniApp 日结成果汇总/补播")
    parser.add_argument("--day", default=datetime.now(TZ_LOCAL).strftime("%Y-%m-%d"))
    parser.add_argument("--capture-dir", default=str(CAPTURE_DIR))
    parser.add_argument("--send-log-group", action="store_true")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    return parser.parse_args()


def main():
    args = parse_args()
    report = build_report(args.day, Path(args.capture_dir))
    print(report)
    if args.send_log_group:
        send_log_group(report, Path(args.env_file))
        print("sent log group")


if __name__ == "__main__":
    main()
