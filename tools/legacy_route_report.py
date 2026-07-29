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
        "key": "ranch_text_run",
        "label": "放养旧文本链",
        "policy": "retired_auto_send_passive_reply_only",
        "commands": (".一键放养",),
        "symbols": ("CMD_RANCH",),
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
    {
        "key": "world_boss_text_run",
        "label": "世界 Boss 旧文本行动链",
        "policy": "miniapp_only_auto",
        "commands": (".世界boss", ".讨伐青元子"),
        "symbols": (
            "CMD_WORLD_BOSS_STATUS",
            "CMD_QINGYUANZI_SUPPRESS",
            "CMD_QINGYUANZI_GUARD",
            "CMD_QINGYUANZI_ATTACK",
            "CMD_QINGYUANZI_BREAK",
            "WORLD_BOSS_STATUS_QUERY_COMMAND",
            "WORLD_BOSS_ACTION_COMMANDS",
        ),
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


def _parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


def _enclosing_scope(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> dict[str, str]:
    function_name = ""
    class_name = ""
    current = parents.get(node)
    while current is not None:
        if not function_name and isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function_name = current.name
        elif not class_name and isinstance(current, ast.ClassDef):
            class_name = current.name
        current = parents.get(current)
    scope = ".".join(part for part in (class_name, function_name) if part) or "<module>"
    return {
        "class": class_name,
        "function": function_name,
        "scope": scope,
    }


def _source_expr(source: str, node: ast.AST | None) -> str:
    if node is None:
        return ""
    return str(ast.get_source_segment(source, node) or "").strip()


def _call_keyword_expr(node: ast.Call, name: str, source: str, constants: dict[str, str]) -> str:
    for keyword in node.keywords:
        if keyword.arg != name:
            continue
        resolved = _resolve_command_expr(keyword.value, constants)
        if resolved:
            return resolved
        if isinstance(keyword.value, ast.Constant):
            return repr(keyword.value.value)
        return _source_expr(source, keyword.value)
    return ""


def _candidate_name_values(
    command_node: ast.AST | None,
    call_node: ast.Call,
    parents: dict[ast.AST, ast.AST],
    constants: dict[str, str],
) -> list[str]:
    if not isinstance(command_node, ast.Name):
        return []
    owner: ast.AST | None = parents.get(call_node)
    while owner is not None and not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
        owner = parents.get(owner)
    if owner is None:
        return []
    owners = [owner]
    module_owner = owner
    while module_owner is not None and not isinstance(module_owner, ast.Module):
        module_owner = parents.get(module_owner)
    if module_owner is not None and module_owner is not owner:
        owners.append(module_owner)
    values: list[str] = []
    for assignment_owner in owners:
        for candidate in ast.walk(assignment_owner):
            if int(getattr(candidate, "lineno", 0) or 0) >= int(getattr(call_node, "lineno", 0) or 0):
                continue
            target: ast.AST | None = None
            value: ast.AST | None = None
            if isinstance(candidate, ast.Assign):
                if len(candidate.targets) != 1:
                    continue
                target, value = candidate.targets[0], candidate.value
            elif isinstance(candidate, ast.AnnAssign):
                target, value = candidate.target, candidate.value
            elif isinstance(candidate, ast.NamedExpr):
                target, value = candidate.target, candidate.value
            if not isinstance(target, ast.Name) or target.id != command_node.id:
                continue
            resolved = _resolve_command_expr(value, constants)
            if resolved and resolved not in values:
                values.append(resolved)
    return values


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
        parents = _parent_map(tree)
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
            scope = _enclosing_scope(node, parents)
            candidate_commands = _candidate_name_values(command_node, node, parents, constants)
            evidence = {
                "path": relative,
                "line": int(getattr(node, "lineno", 0) or 0),
                "command": command,
                "command_expr": _source_expr(source, command_node),
                "candidate_commands": candidate_commands,
                "candidate_route_keys": sorted({
                    route_key
                    for candidate in candidate_commands
                    if (route_key := _route_for_command(candidate))
                }),
                **scope,
                "family_expr": _call_keyword_expr(node, "family", source, constants),
                "source_module_expr": _call_keyword_expr(node, "source_module", source, constants),
                "priority_expr": _call_keyword_expr(node, "priority", source, constants),
                "track_expr": _call_keyword_expr(node, "track", source, constants),
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
    unresolved_by_path: dict[str, int] = {}
    unresolved_by_scope: dict[str, int] = {}
    for item in unresolved_send_calls:
        path = str(item.get("path") or "")
        scope_name = f"{path}:{item.get('scope') or '<module>'}"
        unresolved_by_path[path] = unresolved_by_path.get(path, 0) + 1
        unresolved_by_scope[scope_name] = unresolved_by_scope.get(scope_name, 0) + 1
    return {
        "routes": routes,
        "unresolved_send_calls": unresolved_send_calls,
        "unresolved_summary": {
            "count": len(unresolved_send_calls),
            "by_path": dict(sorted(unresolved_by_path.items(), key=lambda item: (-item[1], item[0]))),
            "by_scope": dict(sorted(unresolved_by_scope.items(), key=lambda item: (-item[1], item[0]))),
        },
    }


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
        "unresolved_summary": source["unresolved_summary"],
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
    unresolved_summary = report.get("unresolved_summary") or {}
    lines.append(f"- unresolved dynamic send_game_command calls: {len(report['unresolved_send_calls'])}")
    for path, count in list((unresolved_summary.get("by_path") or {}).items())[:12]:
        lines.append(f"  - {path}: {count}")
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
