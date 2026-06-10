#!/usr/bin/env python3
"""Read-only report for passive message contract gaps.

The report is intentionally passive: it only reads the passive event ledger and
real-message fixture metadata. It never sends Telegram/game commands.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import module_manifest  # noqa: E402
from model.features import passive_event_ledger  # noqa: E402
from model.message_contract import (  # noqa: E402
    build_replay_sample_suggestion,
    format_unhandled_reply_line,
    iter_message_contract_gaps,
    iter_unhandled_routed_replies,
    summarize_message_contract_gaps,
    summarize_unhandled_routed_replies,
)
from model.real_message_replay import load_real_message_samples  # noqa: E402


DEFAULT_FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "real_message_samples.json"


def _load_fixture_payload(path: Path):
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def _format_counter(title, items):
    if not items:
        return f"{title}: 无"
    parts = [f"{name}:{count}" for name, count in items.items()]
    return f"{title}: " + "、".join(parts)


def build_report(
    *,
    ledger_path: Path | None,
    limit: int,
    latest: int,
    module: str = "",
    family: str = "",
    identity_id: int = 0,
    reason: str = "",
    only_unhandled: bool = False,
    fixture_path: Path | None = None,
    include_coverage: bool = False,
):
    source_path = str(ledger_path) if ledger_path else passive_event_ledger.get_passive_event_ledger_path()
    iter_func = iter_unhandled_routed_replies if only_unhandled else iter_message_contract_gaps
    iter_kwargs = {
        "path": str(ledger_path) if ledger_path else None,
        "limit": limit,
        "module": module,
        "family": family,
        "identity_id": identity_id,
    }
    if not only_unhandled:
        iter_kwargs["reason"] = reason
    events = list(iter_func(**iter_kwargs))
    gap_summary = summarize_message_contract_gaps(events, latest_limit=latest)
    unhandled_events = [
        event
        for event in events
        if str(event.get("reason") or "") == "unhandled_routed_reply"
        and str(event.get("decision") or "") == "handler_not_matched"
    ]
    unhandled_summary = summarize_unhandled_routed_replies(unhandled_events, latest_limit=latest)
    summary = unhandled_summary if only_unhandled else gap_summary
    fixture_samples = {}
    coverage = None
    if include_coverage and fixture_path:
        fixture_samples = _load_fixture_payload(fixture_path)
        coverage = module_manifest.summarize_replay_family_coverage(fixture_samples)
    suggestions = []
    for event in summary["latest"]:
        key, payload = build_replay_sample_suggestion(event, source=f"{source_path}:{event.get('msg_id') or 'unknown'}")
        suggestions.append({"sample_id": key, "payload": payload})
    return {
        "ledger_path": source_path,
        "limit": limit,
        "filters": {
            "module": module,
            "family": family,
            "identity_id": identity_id,
            "reason": reason,
            "only_unhandled": only_unhandled,
        },
        "summary": summary,
        "gap_summary": gap_summary,
        "unhandled_summary": unhandled_summary,
        "suggestions": suggestions,
        "coverage": coverage,
    }


def format_report(report):
    summary = report["summary"]
    lines = [
        "【消息契约报告】",
        f"ledger: {report['ledger_path']}",
        f"消息契约缺口: {report['gap_summary']['total']}",
        f"未处理 routed reply: {report['unhandled_summary']['total']}",
        _format_counter("按原因", report["gap_summary"].get("by_reason") or {}),
        _format_counter("按模块", summary["by_module"]),
        _format_counter("按 family", summary["by_family"]),
    ]
    latest = summary.get("latest") or []
    if latest:
        lines.append("")
        lines.append("最近缺口:")
        for event in latest:
            lines.append(f"- {format_unhandled_reply_line(event)}")
    suggestions = report.get("suggestions") or []
    if suggestions:
        lines.append("")
        lines.append("fixture 建议:")
        for item in suggestions:
            lines.append(json.dumps({item["sample_id"]: item["payload"]}, ensure_ascii=False, indent=2))
    coverage = report.get("coverage")
    if coverage:
        lines.append("")
        lines.append(
            f"真实样本覆盖: {coverage['covered_families']}/{coverage['total_families']} families"
        )
        missing_by_module = {}
        for item in coverage["missing_families"]:
            missing_by_module.setdefault(item["module"], []).append(item["family"])
        for module_name, families in sorted(missing_by_module.items()):
            lines.append(f"- {module_name}: {', '.join(families)}")
    return "\n".join(lines)


def parse_args(argv):
    parser = argparse.ArgumentParser(description="只读汇总消息契约缺口与真实样本覆盖")
    parser.add_argument("--ledger-path", default="", help="passive_event_ledger JSONL；默认读取当天")
    parser.add_argument("--limit", type=int, default=200, help="读取 ledger 尾部行数")
    parser.add_argument("--latest", type=int, default=8, help="展示最近事件数量")
    parser.add_argument("--module", default="", help="只看指定模块中文名")
    parser.add_argument("--family", default="", help="只看指定 reply family")
    parser.add_argument("--identity-id", type=int, default=0, help="只看指定 identity_id")
    parser.add_argument("--reason", default="", help="只看指定缺口 reason")
    parser.add_argument("--only-unhandled", action="store_true", help="只看 handler_not_matched 的 routed reply")
    parser.add_argument("--fixture-path", default=str(DEFAULT_FIXTURE_PATH), help="真实文案 fixture 路径")
    parser.add_argument("--coverage", action="store_true", help="附带真实样本 reply family 覆盖报告")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    ledger_path = Path(args.ledger_path).expanduser().resolve() if args.ledger_path else None
    fixture_path = Path(args.fixture_path).expanduser().resolve() if args.fixture_path else None
    # Validate fixture shape when coverage is requested, so bad fixture metadata fails clearly.
    if args.coverage and fixture_path:
        load_real_message_samples(fixture_path)
    report = build_report(
        ledger_path=ledger_path,
        limit=max(1, int(args.limit or 1)),
        latest=max(1, int(args.latest or 1)),
        module=args.module,
        family=args.family,
        identity_id=int(args.identity_id or 0),
        reason=args.reason,
        only_unhandled=bool(args.only_unhandled),
        fixture_path=fixture_path,
        include_coverage=bool(args.coverage),
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
