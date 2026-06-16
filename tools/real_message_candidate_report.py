#!/usr/bin/env python3
"""Read-only candidate report for real-message replay samples."""

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

from model.real_message_candidates import build_candidate_sample_suggestions  # noqa: E402
from model.real_message_replay import load_real_message_samples  # noqa: E402


DEFAULT_FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "real_message_samples.json"


def build_report(
    *,
    sources,
    fixture_path: Path | None,
    include_covered: bool = False,
    include_archived: bool = False,
    limit: int = 100,
    module: str = "",
    family: str = "",
):
    if fixture_path:
        load_real_message_samples(fixture_path)
    candidates = build_candidate_sample_suggestions(
        sources,
        fixture_path=fixture_path,
        include_covered=include_covered,
        include_archived=include_archived,
        limit=limit,
        module=module,
        family=family,
    )
    return {
        "sources": [str(Path(source).expanduser()) for source in sources],
        "fixture_path": str(fixture_path) if fixture_path else "",
        "include_covered": bool(include_covered),
        "include_archived": bool(include_archived),
        "limit": max(1, int(limit or 100)),
        "filters": {
            "module": module,
            "family": family,
        },
        "candidates": candidates,
    }


def format_report(report):
    candidates = report["candidates"]
    lines = [
        "【真实文案候选报告】",
        "只读: 不写 fixture，不发送游戏命令，不读取 API。",
        f"sources: {', '.join(report['sources']) if report['sources'] else '无'}",
        f"fixture: {report['fixture_path'] or '无'}",
        f"候选: {candidates['total']}",
    ]
    if candidates["by_family"]:
        parts = [f"{family}:{count}" for family, count in candidates["by_family"].items()]
        lines.append("按 family: " + "、".join(parts))
    skipped = {key: value for key, value in candidates["skipped"].items() if value}
    if skipped:
        parts = [f"{key}:{value}" for key, value in skipped.items()]
        lines.append("跳过: " + "、".join(parts))
    if candidates["suggestions"]:
        lines.append("")
        lines.append("fixture 建议:")
        for item in candidates["suggestions"]:
            lines.append(json.dumps({item["sample_id"]: item["payload"]}, ensure_ascii=False, indent=2))
    return "\n".join(lines)


def parse_args(argv):
    parser = argparse.ArgumentParser(description="只读收集真实文案 replay fixture 候选")
    parser.add_argument("--source", action="append", default=[], help="JSON/JSONL 本地来源，可重复")
    parser.add_argument("--fixture-path", default=str(DEFAULT_FIXTURE_PATH), help="现有真实文案 fixture")
    parser.add_argument("--include-covered", action="store_true", help="不只看缺失 family，也输出已覆盖 family 候选")
    parser.add_argument("--include-archived", action="store_true", help="包含已归档模块的历史 family 候选")
    parser.add_argument("--limit", type=int, default=100, help="最多输出候选数量")
    parser.add_argument("--module", default="", help="只看指定模块中文名")
    parser.add_argument("--family", default="", help="只看指定 reply family")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    fixture_path = Path(args.fixture_path).expanduser().resolve() if args.fixture_path else None
    sources = [Path(source).expanduser().resolve() for source in args.source or ()]
    report = build_report(
        sources=sources,
        fixture_path=fixture_path,
        include_covered=bool(args.include_covered),
        include_archived=bool(args.include_archived),
        limit=max(1, int(args.limit or 100)),
        module=args.module,
        family=args.family,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
