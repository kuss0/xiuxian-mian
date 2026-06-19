#!/usr/bin/env python3
"""Read-only report for passive message contract gaps.

The report is intentionally passive: it only reads the passive event ledger and
real-message fixture metadata. It never sends Telegram/game commands.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("XIUXIAN_TESTING", "1")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "00000000000000000000000000000000")
os.environ.setdefault("LOG_GROUP_ID", "0")
os.environ.setdefault("ADMIN_ID", "1")
os.environ.setdefault("TG_PROXY_TYPE", "")
os.environ.setdefault("TG_PROXY_HOST", "127.0.0.1:7890")

from model import module_manifest  # noqa: E402
from model.features import passive_event_ledger  # noqa: E402
from model.message_contract import (  # noqa: E402
    build_replay_sample_suggestion,
    format_message_box_shadow_alignment,
    format_unhandled_reply_line,
    iter_message_contract_gaps,
    iter_unhandled_routed_replies,
    summarize_message_box_shadow_alignment,
    summarize_message_contract_gaps,
    summarize_unhandled_routed_replies,
)
from model.message_box import message_fact_from_dict  # noqa: E402
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


def _safe_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _load_shadow_facts(path: Path):
    if not path or not path.exists():
        return []
    with path.open("r", encoding="utf-8") as fp:
        payload = json.load(fp)
    if isinstance(payload, dict):
        rows = payload.get("facts") or payload.get("messages") or payload.get("items") or []
    else:
        rows = payload
    facts = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        facts.append(message_fact_from_dict(row))
    return facts


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
    include_admission: bool = False,
    include_contracts: bool = False,
    include_readiness: bool = False,
    shadow_path: Path | None = None,
    strict_modules: tuple = (),
):
    requested_limit = max(1, int(limit or 1))
    effective_limit = min(requested_limit, passive_event_ledger.PASSIVE_EVENT_ITER_LIMIT_CAP)
    source_path = str(ledger_path) if ledger_path else passive_event_ledger.get_passive_event_ledger_path()
    iter_func = iter_unhandled_routed_replies if only_unhandled else iter_message_contract_gaps
    iter_kwargs = {
        "path": str(ledger_path) if ledger_path else None,
        "limit": requested_limit,
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
    admission = None
    contracts = None
    readiness = None
    shadow_alignment = None
    if include_coverage and fixture_path:
        fixture_samples = _load_fixture_payload(fixture_path)
        coverage = module_manifest.summarize_replay_family_coverage(fixture_samples)
    if include_admission or include_contracts or include_readiness:
        if fixture_path and not fixture_samples:
            fixture_samples = _load_fixture_payload(fixture_path)
    if include_admission:
        admission = module_manifest.validate_module_admission_contract(
            fixture_samples if fixture_path else None,
            strict_modules=strict_modules,
        )
    if include_contracts:
        contracts = module_manifest.summarize_module_contracts(
            fixture_samples if fixture_path else None,
            strict_modules=strict_modules,
        )
    if include_readiness:
        readiness = module_manifest.summarize_module_readiness(
            fixture_samples if fixture_path else None,
            strict_modules=strict_modules,
        )
    if shadow_path:
        shadow_facts = _load_shadow_facts(shadow_path)
        passive_events = passive_event_ledger.iter_passive_events(path=str(ledger_path) if ledger_path else None, limit=requested_limit)
        shadow_alignment = summarize_message_box_shadow_alignment(
            shadow_facts,
            passive_events,
            latest_limit=latest,
        )
    suggestions = []
    for event in summary["latest"]:
        key, payload = build_replay_sample_suggestion(event, source=f"{source_path}:{event.get('msg_id') or 'unknown'}")
        suggestions.append({"sample_id": key, "payload": payload})
    return {
        "ledger_path": source_path,
        "limit": requested_limit,
        "effective_limit": effective_limit,
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
        "admission": admission,
        "contracts": contracts,
        "readiness": readiness,
        "shadow_alignment": shadow_alignment,
    }


def format_report(report):
    summary = report["summary"]
    lines = [
        "【消息契约报告】",
        f"ledger: {report['ledger_path']}",
        f"limit: requested={report['limit']} effective={report.get('effective_limit', report['limit'])}",
        f"消息契约缺口: {report['gap_summary']['total']}",
        f"待修/待归因: {report['gap_summary'].get('needs_attention_total', report['gap_summary']['total'])}",
        f"外部观察: {report['gap_summary'].get('external_observation_total', 0)}",
        f"未处理 routed reply: {report['unhandled_summary']['total']}",
        _format_counter("按分类", report["gap_summary"].get("by_class") or {}),
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
    shadow_alignment = report.get("shadow_alignment")
    if shadow_alignment:
        lines.append("")
        lines.append(format_message_box_shadow_alignment(shadow_alignment))
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
    readiness = report.get("readiness")
    if readiness:
        totals = readiness.get("totals") or {}
        lines.append("")
        lines.append(
            "模块就绪度: "
            f"complete={totals.get('sample_complete_modules', 0)}, "
            f"partial={totals.get('sample_partial_modules', 0)}, "
            f"missing={totals.get('sample_missing_modules', 0)}, "
            f"contract-only={totals.get('contract_only_modules', 0)}, "
            f"archived={totals.get('archived_modules', 0)}, "
            f"families={totals.get('covered_sample_families', 0)}/{totals.get('reply_families', 0)}"
        )
        unknown_strict = readiness.get("unknown_strict_modules") or []
        if unknown_strict:
            lines.append(f"- unknown_strict_modules: {', '.join(unknown_strict)}")
        strict_rows = [row for row in readiness.get("modules") or [] if row.get("strict")]
        rows = strict_rows or [
            row
            for row in readiness.get("modules") or []
            if row.get("readiness") != module_manifest.READINESS_SAMPLE_COMPLETE
        ]
        for row in rows:
            missing = row.get("missing_sample_families") or []
            missing_text = f" missing={','.join(missing)}" if missing else ""
            archived_text = " archived" if row.get("archived") else ""
            lines.append(
                f"- {row['module']}: {row['readiness']} "
                f"{row['covered_sample_count']}/{row['reply_family_count']}{missing_text}{archived_text}"
            )
    admission = report.get("admission")
    if admission:
        lines.append("")
        lines.append(f"准入合同: {'OK' if admission['ok'] else 'FAIL'}")
        for key in (
            "missing_duplicate_guard",
            "last_resort_without_passive_first",
            "passive_without_observation",
            "strict_unknown_modules",
            "strict_archived_modules",
            "strict_missing_replay_routes",
            "strict_missing_samples",
            "strict_missing_sample_families",
        ):
            values = admission.get(key) or []
            if values:
                lines.append(f"- {key}: {', '.join(values)}")
    contracts = report.get("contracts")
    if contracts:
        totals = contracts.get("totals") or {}
        lines.append("")
        lines.append(
            "模块合同: "
            f"{totals.get('modules', 0)} modules, "
            f"{totals.get('archived_modules', 0)} archived, "
            f"{totals.get('covered_sample_families', 0)}/{totals.get('reply_families', 0)} sample families, "
            f"{totals.get('passive_first_modules', 0)} passive-first, "
            f"{totals.get('last_resort_modules', 0)} last-resort"
        )
        unknown_strict = contracts.get("unknown_strict_modules") or []
        if unknown_strict:
            lines.append(f"- unknown_strict_modules: {', '.join(unknown_strict)}")
        strict_rows = [row for row in contracts.get("modules") or [] if row.get("strict")]
        rows = strict_rows or (contracts.get("modules") or [])
        for row in rows:
            missing = row.get("missing_sample_families") or []
            missing_text = f" missing={','.join(missing)}" if missing else ""
            lines.append(
                f"- {row['module']}: send={row['send_policy']} query={row['active_query_policy']} "
                f"guard={row['duplicate_guard']}{missing_text}"
            )
        report_only = contracts.get("report_only") or {}
        report_only_totals = report_only.get("totals") or {}
        if report_only_totals:
            lines.append(
                "未接入模块合同: "
                f"{report_only_totals.get('modules', 0)} report-only, "
                f"{report_only_totals.get('backup_api_modules', 0)} API-backup"
            )
            validation = report_only.get("validation") or {}
            if validation and not validation.get("ok", False):
                for key, values in validation.items():
                    if key == "ok" or not values:
                        continue
                    lines.append(f"- {key}: {', '.join(values)}")
            report_only_rows = [row for row in report_only.get("modules") or [] if row.get("strict")]
            if not strict_rows and not report_only_rows:
                report_only_rows = report_only.get("modules") or []
            for row in report_only_rows:
                parent = f" parent={row['parent_module']}" if row.get("parent_module") else ""
                lines.append(
                    f"- {row['name']}: stage={row['stage']} key={row['feature_key']} "
                    f"api={row['api_policy']}{parent} scheduler={row['scheduler_connected']} ui={row['ui_connected']}"
                )
        rust_alignment = contracts.get("rust_alignment") or {}
        rust_alignment_totals = rust_alignment.get("totals") or {}
        if rust_alignment_totals:
            lines.append("")
            lines.append(
                "Rust 对照候选: "
                f"{rust_alignment_totals.get('candidates', 0)} candidates, "
                f"{rust_alignment_totals.get('recommended_default_path', 0)} recommended-default, "
                f"{rust_alignment_totals.get('backup_api_candidates', 0)} API-backup"
            )
            unknown_strict = rust_alignment.get("unknown_strict_candidates") or []
            if unknown_strict:
                lines.append(f"- unknown_strict_candidates: {', '.join(unknown_strict)}")
            strict_rows = [row for row in rust_alignment.get("candidates") or [] if row.get("strict")]
            rows = strict_rows or (rust_alignment.get("candidates") or [])
            for row in rows:
                backup = f" backup={','.join(row['backup_inputs'])}" if row.get("backup_inputs") else ""
                lines.append(
                    f"- {row['name']}: cmd={row['rust_command']} category={row['category']} "
                    f"api={row['api_policy']}{backup}"
                )
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
    parser.add_argument("--admission", action="store_true", help="附带新模块准入合同检查")
    parser.add_argument("--contracts", action="store_true", help="附带所有模块的合约矩阵")
    parser.add_argument("--readiness", action="store_true", help="附带现有模块真实文案就绪度看板")
    parser.add_argument("--shadow-path", default="", help="MessageBox shadow JSON 快照；只读对账 passive ledger")
    parser.add_argument("--strict-module", action="append", default=[], help="准入合同中需要真实样本硬校验的模块，可重复")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    ledger_path = Path(args.ledger_path).expanduser().resolve() if args.ledger_path else None
    fixture_path = Path(args.fixture_path).expanduser().resolve() if args.fixture_path else None
    # Validate fixture shape when coverage is requested, so bad fixture metadata fails clearly.
    if (args.coverage or args.admission or args.contracts or args.readiness) and fixture_path:
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
        include_admission=bool(args.admission),
        include_contracts=bool(args.contracts),
        include_readiness=bool(args.readiness),
        shadow_path=Path(args.shadow_path).expanduser().resolve() if args.shadow_path else None,
        strict_modules=tuple(args.strict_module or ()),
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
