#!/usr/bin/env python3
"""Read-only source and message-log evidence for migrated command routes."""

from __future__ import annotations

import argparse
import ast
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]

ROUTE_CATALOG = (
    {
        "key": "fishing_text_followup",
        "label": "钓鱼旧竿内链",
        "policy": "retired_auto_send",
        "commands": (".钓鱼状态", ".试探咬饵", ".提竿", ".收竿", ".开鱼", ".鱼篓"),
        "symbols": (
            "CMD_FISHING_STATUS",
            "CMD_FISHING_PROBE",
            "CMD_FISHING_LIFT",
            "CMD_FISHING_CANCEL",
            "CMD_FISHING_OPEN",
            "CMD_FISHING_BASKET",
        ),
    },
    {
        "key": "tower_text_run",
        "label": "闯塔旧文本链",
        "policy": "miniapp_only_auto",
        "commands": (".闯塔", ".继续闯塔", ".重置古塔"),
        "symbols": ("CMD_TOWER", "CMD_TOWER_CONTINUE", "CMD_TOWER_RESET"),
    },
    {
        "key": "wild_training_text_run",
        "label": "野外历练旧文本链",
        "policy": "miniapp_only_auto",
        "commands": (".野外历练",),
        "symbols": ("CMD_WILD_TRAINING",),
    },
    {
        "key": "fishing_entry",
        "label": "钓鱼入口命令",
        "policy": "manual_compat_public_miniapp_preferred",
        "commands": (".钓鱼",),
        "symbols": ("CMD_FISHING",),
    },
    {
        "key": "trial_entry",
        "label": "天机试炼入口命令",
        "policy": "manual_compat_public_miniapp_preferred",
        "commands": (".天机试炼",),
        "symbols": ("CMD_TIANJI_TRIAL",),
    },
    {
        "key": "stargazer_entry",
        "label": "观星台入口命令",
        "policy": "dual_track_public_miniapp_preferred",
        "commands": (".观星台",),
        "symbols": ("CMD_STARGAZER_PANEL",),
    },
    {
        "key": "tree_entry",
        "label": "灵树入口命令",
        "policy": "dual_track_public_miniapp_preferred",
        "commands": (".灵树",),
        "symbols": ("CMD_TREE",),
    },
    {
        "key": "cave_entry",
        "label": "洞府入口命令",
        "policy": "dual_track_preserve",
        "commands": (".洞府",),
        "symbols": ("CMD_CAVE",),
    },
    {
        "key": "small_world_harvest",
        "label": "收割香火命令",
        "policy": "miniapp_preferred_legacy_fallback",
        "commands": (".收割香火",),
        "symbols": ("CMD_SMALL_WORLD_HARVEST",),
    },
    {
        "key": "tianti_status",
        "label": "天阶状态命令",
        "policy": "gated_public_miniapp_with_command_fallback",
        "commands": (".天阶状态",),
        "symbols": ("CMD_TIANTI_STATUS",),
    },
)


def _iter_json_lines(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                yield value


def _command_matches(text: str, prefix: str) -> bool:
    text = str(text or "").strip()
    prefix = str(prefix or "").strip()
    return bool(prefix and (text == prefix or text.startswith(f"{prefix} ")))


def _route_for_command(command: str) -> str:
    for route in ROUTE_CATALOG:
        if any(_command_matches(command, prefix) for prefix in route["commands"]):
            return str(route["key"])
    return ""


def _config_constants(config_path: Path) -> dict[str, str]:
    try:
        tree = ast.parse(config_path.read_text(encoding="utf-8"), filename=str(config_path))
    except (OSError, SyntaxError):
        return {}
    values: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                values[target.id] = value.value
    return values


def _resolve_command_expr(node: ast.AST | None, constants: dict[str, str]) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id, "")
    if not isinstance(node, ast.JoinedStr):
        return ""
    parts: list[str] = []
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            parts.append(value.value)
            continue
        if isinstance(value, ast.FormattedValue):
            resolved = _resolve_command_expr(value.value, constants)
            parts.append(resolved or "*")
            continue
        parts.append("*")
    return "".join(parts).strip()


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _call_command_node(node: ast.Call) -> ast.AST | None:
    if node.args:
        return node.args[0]
    for keyword in node.keywords:
        if keyword.arg == "command":
            return keyword.value
    return None


def _source_paths(model_dir: Path) -> list[Path]:
    return sorted(path for path in model_dir.rglob("*.py") if path.is_file())


def build_source_evidence(model_dir: Path) -> dict[str, Any]:
    constants = _config_constants(model_dir / "config.py")
    routes = {
        str(route["key"]): {
            "label": route["label"],
            "policy": route["policy"],
            "commands": list(route["commands"]),
            "direct_send_calls": [],
            "symbol_refs": [],
            "literal_refs": [],
        }
        for route in ROUTE_CATALOG
    }
    symbol_routes = {
        symbol: str(route["key"])
        for route in ROUTE_CATALOG
        for symbol in route["symbols"]
    }
    unresolved_send_calls: list[dict[str, Any]] = []

    for path in _source_paths(model_dir):
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, SyntaxError):
            continue
        relative = str(path.relative_to(model_dir.parent))
        lines = source.splitlines()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in symbol_routes:
                routes[symbol_routes[node.id]]["symbol_refs"].append({
                    "path": relative,
                    "line": int(getattr(node, "lineno", 0) or 0),
                    "symbol": node.id,
                })
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                route_key = _route_for_command(node.value)
                if route_key:
                    routes[route_key]["literal_refs"].append({
                        "path": relative,
                        "line": int(getattr(node, "lineno", 0) or 0),
                        "text": node.value,
                    })
            if not isinstance(node, ast.Call) or _call_name(node) != "send_game_command":
                continue
            command_node = _call_command_node(node)
            command = _resolve_command_expr(command_node, constants)
            evidence = {
                "path": relative,
                "line": int(getattr(node, "lineno", 0) or 0),
                "command": command,
                "source": lines[int(getattr(node, "lineno", 1) or 1) - 1].strip() if lines else "",
            }
            route_key = _route_for_command(command)
            if route_key:
                routes[route_key]["direct_send_calls"].append(evidence)
            elif not command:
                unresolved_send_calls.append(evidence)

    for route in routes.values():
        for key in ("direct_send_calls", "symbol_refs", "literal_refs"):
            route[key].sort(key=lambda item: (item["path"], item["line"]))
    unresolved_send_calls.sort(key=lambda item: (item["path"], item["line"]))
    return {"routes": routes, "unresolved_send_calls": unresolved_send_calls}


def _message_paths(messages_dir: Path, day: str, days: int) -> list[Path]:
    end = datetime.strptime(day, "%Y-%m-%d").date()
    return [messages_dir / f"{end - timedelta(days=offset):%Y-%m-%d}.log" for offset in range(max(1, days))]


def build_log_evidence(messages_dir: Path, *, day: str, days: int) -> dict[str, list[dict[str, Any]]]:
    evidence = {str(route["key"]): [] for route in ROUTE_CATALOG}
    for path in _message_paths(messages_dir, day, days):
        for row in _iter_json_lines(path):
            if row.get("event_type") != "sent":
                continue
            route_key = _route_for_command(str(row.get("text") or ""))
            if not route_key:
                continue
            evidence[route_key].append({
                "ts": str(row.get("ts") or ""),
                "message_id": int(row.get("message_id") or 0),
                "sender_id": int(row.get("sender_id") or 0),
                "text": str(row.get("text") or ""),
                "family": str(row.get("family") or ""),
                "source_module": str(row.get("source_module") or ""),
            })
    for rows in evidence.values():
        rows.sort(key=lambda item: (item["ts"], item["message_id"]))
    return evidence


def _summarize_sent_events(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_day: dict[str, int] = {}
    by_source_module: dict[str, int] = {}
    for row in rows:
        day = str(row.get("ts") or "")[:10]
        if day:
            by_day[day] = by_day.get(day, 0) + 1
        source_module = str(row.get("source_module") or "").strip() or "(none)"
        by_source_module[source_module] = by_source_module.get(source_module, 0) + 1
    return {
        "count": len(rows),
        "first_ts": str(rows[0].get("ts") or "") if rows else "",
        "last_ts": str(rows[-1].get("ts") or "") if rows else "",
        "by_day": by_day,
        "by_source_module": by_source_module,
    }


def build_report(
    project_root: Path = PROJECT_ROOT,
    *,
    day: str | None = None,
    days: int = 7,
) -> dict[str, Any]:
    day = day or datetime.now().strftime("%Y-%m-%d")
    source = build_source_evidence(project_root / "model")
    logs = build_log_evidence(project_root / "data" / "messages", day=day, days=days)
    routes = source["routes"]
    for key, rows in logs.items():
        routes[key]["sent_events"] = rows
        routes[key]["sent_summary"] = _summarize_sent_events(rows)
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "day": day,
        "days": max(1, int(days)),
        "routes": routes,
        "unresolved_send_calls": source["unresolved_send_calls"],
    }


def format_report(report: dict[str, Any]) -> str:
    lines = [
        f"legacy route report: {report['day']} lookback={report['days']}d",
        "read-only: source scan + persisted sent logs; no Telegram/HTTP calls",
    ]
    for key, route in report["routes"].items():
        sent_summary = route.get("sent_summary") or {}
        lines.append(
            f"- {key} [{route['policy']}]: direct_send={len(route['direct_send_calls'])} "
            f"symbol_refs={len(route['symbol_refs'])} literal_refs={len(route['literal_refs'])} "
            f"sent={sent_summary.get('count', 0)} last={sent_summary.get('last_ts') or '-'} "
            f"by_day={json.dumps(sent_summary.get('by_day') or {}, ensure_ascii=False, sort_keys=True)}"
        )
    lines.append(f"- unresolved dynamic send_game_command calls: {len(report['unresolved_send_calls'])}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--day", default="")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = build_report(args.project_root, day=args.day or None, days=args.days)
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
