#!/usr/bin/env python3
"""Build an offline material inventory report from saved game message logs.

This script is intentionally offline-first: by default it only reads JSONL logs
and prints a report. It never sends game commands.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable
from urllib import parse, request


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME_ROOT = PROJECT_ROOT
DEFAULT_PROTECTED_NAMES = ()
DEFAULT_TOP_HOLDERS = 4
DEFAULT_CHUNK_LIMIT = 3500
DEFAULT_FRESH_HOURS = 24

BAG_HEADER_RE = re.compile(r"^@(?P<owner>.+?)\s+的储物袋\s*(?:\n|$)")
ITEM_RE = re.compile(r"^-\s*(?P<name>.+?)\s*[x×]\s*(?P<count>\d+)(?:\s+.*)?$")


@dataclass(frozen=True)
class Identity:
    send_as_id: int
    username: str
    label: str
    daohao: str
    enabled: bool


@dataclass
class BagSnapshot:
    owner: str
    ts: str
    source_file: str
    line_no: int
    message_id: int
    materials: dict[str, int]
    source: str = ""


@dataclass
class ParseStats:
    scanned_lines: int = 0
    bag_messages: int = 0
    parsed_snapshots: int = 0
    skipped_protected: int = 0
    skipped_not_identity: int = 0
    invalid_json: int = 0
    empty_materials: int = 0


def normalize_name(value: str) -> str:
    return str(value or "").strip().lstrip("@").casefold()


def is_protected_name(name: str, protected_names: Iterable[str]) -> bool:
    normalized = normalize_name(name)
    if not normalized:
        return False
    protected = {normalize_name(item) for item in protected_names if normalize_name(item)}
    return normalized in protected


def resolve_default_messages_dir() -> Path:
    runtime_messages = DEFAULT_RUNTIME_ROOT / "data" / "messages"
    if runtime_messages.exists():
        return runtime_messages
    return PROJECT_ROOT / "data" / "messages"


def resolve_default_db_file() -> Path:
    runtime_db = DEFAULT_RUNTIME_ROOT / "data" / "state" / "chaogu_state.db"
    if runtime_db.exists():
        return runtime_db
    return PROJECT_ROOT / "data" / "state" / "chaogu_state.db"


def iter_log_files(messages_dir: Path, since: str = "", until: str = "") -> Iterable[Path]:
    if not messages_dir.exists():
        return []
    files = sorted(messages_dir.glob("*.log"))
    if since:
        files = [item for item in files if item.stem >= since]
    if until:
        files = [item for item in files if item.stem <= until]
    return files


def load_env_file(path: Path) -> dict[str, str]:
    env = {}
    if not path.exists():
        return env
    with path.open("r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            if key:
                env[key] = value
    return env


def load_identities(db_file: Path) -> dict[str, Identity]:
    if not db_file.exists():
        return {}
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT send_as_id, username, label, daohao, enabled
            FROM identities
            ORDER BY send_as_id
            """
        ).fetchall()
    finally:
        conn.close()

    identities = {}
    for row in rows:
        identity = Identity(
            send_as_id=int(row["send_as_id"] or 0),
            username=str(row["username"] or "").strip(),
            label=str(row["label"] or "").strip(),
            daohao=str(row["daohao"] or "").strip(),
            enabled=bool(row["enabled"]),
        )
        key = normalize_name(identity.username)
        if key:
            identities[key] = identity
    return identities


def load_storage_bag_records(db_file: Path) -> dict:
    if not db_file.exists():
        return {}
    conn = sqlite3.connect(str(db_file))
    try:
        row = conn.execute("SELECT value FROM meta WHERE key = 'storage_bag_records'").fetchone()
    finally:
        conn.close()
    if not row or not row[0]:
        return {}
    try:
        records = json.loads(row[0])
    except (TypeError, ValueError):
        return {}
    return records if isinstance(records, dict) else {}


def build_identity_alias_map(identities: dict[str, Identity]) -> dict[str, str]:
    aliases = {}
    for identity in identities.values():
        username_key = normalize_name(identity.username)
        if not username_key:
            continue
        for alias in (identity.username, identity.label, identity.daohao):
            alias_key = normalize_name(alias)
            if alias_key:
                aliases[alias_key] = username_key
    return aliases


def parse_materials_from_bag(text: str) -> tuple[str, dict[str, int]] | None:
    match = BAG_HEADER_RE.match(text or "")
    if not match:
        return None
    owner = match.group("owner").strip()
    materials = {}
    in_materials = False
    for raw_line in text.splitlines()[1:]:
        line = raw_line.strip()
        if not line:
            continue
        if line.endswith(":"):
            in_materials = line == "材料:"
            continue
        if not in_materials:
            continue
        item_match = ITEM_RE.match(line)
        if not item_match:
            continue
        item_name = item_match.group("name").strip()
        item_count = int(item_match.group("count"))
        if item_name:
            materials[item_name] = materials.get(item_name, 0) + item_count
    return owner, materials


def parse_record_time(record: dict) -> datetime | None:
    ts_text = str((record or {}).get("updated_at_text") or "").strip()
    parsed = parse_snapshot_time(ts_text)
    if parsed is not None:
        return parsed
    try:
        updated_at = float((record or {}).get("updated_at") or 0)
    except (TypeError, ValueError):
        updated_at = 0
    if updated_at > 0:
        return datetime.fromtimestamp(updated_at)
    return None


def format_record_time(record: dict) -> str:
    ts_text = str((record or {}).get("updated_at_text") or "").strip()
    if ts_text:
        return ts_text
    parsed = parse_record_time(record)
    if parsed is None:
        return ""
    return parsed.strftime("%Y-%m-%d %H:%M:%S UTC+8")


def _date_in_range(ts: datetime | None, since: str = "", until: str = "") -> bool:
    if ts is None:
        return not since and not until
    day_text = ts.date().isoformat()
    if since and day_text < since:
        return False
    if until and day_text > until:
        return False
    return True


def _coerce_positive_items(items: dict) -> dict[str, int]:
    normalized = {}
    if not isinstance(items, dict):
        return normalized
    for raw_name, raw_count in items.items():
        name = str(raw_name or "").strip()
        if not name:
            continue
        try:
            count = int(str(raw_count or 0).replace(",", ""))
        except (TypeError, ValueError):
            count = 0
        if count > 0:
            normalized[name] = normalized.get(name, 0) + count
    return normalized


def read_cached_snapshots(
    db_file: Path,
    identities: dict[str, Identity],
    protected_names: Iterable[str],
    *,
    since: str = "",
    until: str = "",
    require_identity: bool = True,
) -> tuple[dict[str, BagSnapshot], ParseStats]:
    stats = ParseStats()
    records = load_storage_bag_records(db_file)
    if not records:
        return {}, stats

    identities_by_id = {int(identity.send_as_id): identity for identity in identities.values()}
    identity_names = {
        normalize_name(identity.username)
        for identity in identities.values()
        if identity.username and not is_protected_name(identity.username, protected_names)
    }
    snapshots: dict[str, BagSnapshot] = {}
    for raw_identity_id, record in records.items():
        if not isinstance(record, dict):
            continue
        try:
            identity_id = int(raw_identity_id or 0)
        except (TypeError, ValueError):
            identity_id = 0
        identity = identities_by_id.get(identity_id)
        owner = (
            (identity.username if identity else "")
            or record.get("owner_username")
            or record.get("owner")
            or record.get("label")
            or str(raw_identity_id or "")
        )
        owner_key = normalize_name(owner)
        protected_candidates = (
            owner,
            record.get("owner_username"),
            record.get("owner"),
            record.get("label"),
            identity.username if identity else "",
            identity.label if identity else "",
            identity.daohao if identity else "",
        )
        if any(is_protected_name(str(candidate or ""), protected_names) for candidate in protected_candidates):
            stats.skipped_protected += 1
            continue
        if require_identity and identity_names and owner_key not in identity_names:
            stats.skipped_not_identity += 1
            continue

        parsed_time = parse_record_time(record)
        if not _date_in_range(parsed_time, since=since, until=until):
            continue

        items = _coerce_positive_items(record.get("items") or {})
        if not items:
            stats.empty_materials += 1
        snapshots[owner_key] = BagSnapshot(
            owner=owner,
            ts=format_record_time(record),
            source_file="sqlite:storage_bag_records",
            line_no=0,
            message_id=0,
            materials=items,
            source=str(record.get("source") or "storage_bag_cache"),
        )
        stats.parsed_snapshots += 1
    return snapshots, stats


def read_latest_snapshots(
    messages_dir: Path,
    identity_names: set[str],
    protected_names: Iterable[str],
    *,
    since: str = "",
    until: str = "",
    require_identity: bool = True,
) -> tuple[dict[str, BagSnapshot], ParseStats]:
    stats = ParseStats()
    snapshots: dict[str, BagSnapshot] = {}
    for log_file in iter_log_files(messages_dir, since=since, until=until):
        with log_file.open("r", encoding="utf-8", errors="replace") as f:
            for line_no, raw_line in enumerate(f, 1):
                stats.scanned_lines += 1
                try:
                    payload = json.loads(raw_line)
                except json.JSONDecodeError:
                    stats.invalid_json += 1
                    continue
                text = str(payload.get("text") or "")
                parsed = parse_materials_from_bag(text)
                if not parsed:
                    continue
                stats.bag_messages += 1
                owner, materials = parsed
                owner_key = normalize_name(owner)
                if is_protected_name(owner, protected_names):
                    stats.skipped_protected += 1
                    continue
                if require_identity and owner_key not in identity_names:
                    stats.skipped_not_identity += 1
                    continue
                if not materials:
                    stats.empty_materials += 1
                snapshots[owner_key] = BagSnapshot(
                    owner=owner,
                    ts=str(payload.get("ts") or ""),
                    source_file=log_file.name,
                    line_no=line_no,
                    message_id=int(payload.get("message_id") or 0),
                    materials=materials,
                    source="message_log",
                )
                stats.parsed_snapshots += 1
    return snapshots, stats


def format_number(value: int) -> str:
    return f"{int(value):,}"


def parse_snapshot_time(ts: str) -> datetime | None:
    raw = str(ts or "").strip().replace(" UTC+8", "")
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def format_age(seconds: float) -> str:
    seconds = max(0, int(seconds or 0))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days > 0:
        return f"{days}天{hours}小时"
    if hours > 0:
        return f"{hours}小时{minutes}分钟"
    return f"{minutes}分钟"


def get_snapshot_age_seconds(snapshot: BagSnapshot, now: datetime) -> float | None:
    snapshot_time = parse_snapshot_time(snapshot.ts)
    if snapshot_time is None:
        return None
    return max(0, (now - snapshot_time).total_seconds())


def get_eligible_identity_names(identities: dict[str, Identity], protected_names: Iterable[str]) -> set[str]:
    return {
        normalize_name(identity.username)
        for identity in identities.values()
        if identity.username and not is_protected_name(identity.username, protected_names)
    }


def get_freshness_summary(
    snapshots: dict[str, BagSnapshot],
    identities: dict[str, Identity],
    protected_names: Iterable[str],
    *,
    now: datetime,
    fresh_hours: int,
) -> tuple[str, list[str]]:
    eligible_names = get_eligible_identity_names(identities, protected_names)
    total_expected = len(eligible_names) if eligible_names else len(snapshots)
    fresh_limit = max(1, int(fresh_hours or DEFAULT_FRESH_HOURS)) * 3600
    fresh_count = 0
    stale_items = []
    for owner_key, snapshot in snapshots.items():
        age = get_snapshot_age_seconds(snapshot, now)
        if age is not None and age <= fresh_limit:
            fresh_count += 1
        else:
            stale_items.append((owner_key, snapshot, age))

    missing_names = sorted(eligible_names - set(snapshots.keys()))
    stale_count = len(stale_items)
    missing_count = len(missing_names)
    if total_expected:
        summary = f"{fresh_count}/{total_expected} 新鲜"
    else:
        summary = "0/0 新鲜"
    if stale_count:
        summary += f"，{stale_count} 过期"
    if missing_count:
        summary += f"，{missing_count} 缺失"

    details = []
    for _, snapshot, age in sorted(
        stale_items,
        key=lambda item: (999999999 if item[2] is None else item[2]),
        reverse=True,
    )[:8]:
        display_name = get_identity_display_name(snapshot.owner, identities)
        age_text = "未知时间" if age is None else f"{format_age(age)}前"
        details.append(f"{display_name}：{age_text}")
    for owner_key in missing_names[:8 - len(details)]:
        identity = identities.get(owner_key)
        display_name = (identity.username or identity.label or identity.daohao) if identity else owner_key
        details.append(f"{display_name}：无快照")
    if stale_count + missing_count > len(details):
        details.append(f"其余 {stale_count + missing_count - len(details)} 个未展开")
    return summary, details


def get_identity_display_name(owner: str, identities: dict[str, Identity]) -> str:
    identity = identities.get(normalize_name(owner))
    if not identity:
        return owner
    return identity.username or identity.label or identity.daohao or owner


def format_holder_list(
    holders: dict[str, int],
    identities: dict[str, Identity],
    top_holders: int = DEFAULT_TOP_HOLDERS,
) -> str:
    ranked = sorted(holders.items(), key=lambda item: (-item[1], item[0]))
    visible = ranked[:top_holders]
    parts = [f"{get_identity_display_name(owner, identities)} {format_number(count)}" for owner, count in visible]
    if len(ranked) > top_holders:
        rest = sum(count for _, count in ranked[top_holders:])
        parts.append(f"其他 {format_number(rest)}")
    return " / ".join(parts) if parts else "-"


def build_material_index(snapshots: dict[str, BagSnapshot]) -> dict[str, dict[str, int]]:
    index: dict[str, dict[str, int]] = defaultdict(dict)
    for snapshot in snapshots.values():
        for item_name, count in snapshot.materials.items():
            index[item_name][snapshot.owner] = index[item_name].get(snapshot.owner, 0) + count
    return dict(index)


def material_sort_key(item: tuple[str, dict[str, int]]) -> tuple[int, int, str]:
    name, holders = item
    total = sum(holders.values())
    if name == "灵石":
        priority = 0
    elif "妖丹" in name:
        priority = 1
    elif "木髓" in name:
        priority = 2
    else:
        priority = 3
    return (priority, -total, name)


def build_report(
    snapshots: dict[str, BagSnapshot],
    stats: ParseStats,
    identities: dict[str, Identity],
    protected_names: Iterable[str],
    *,
    messages_dir: Path,
    since: str = "",
    until: str = "",
    top_holders: int = DEFAULT_TOP_HOLDERS,
    only_names: Iterable[str] = (),
    verbose: bool = False,
    fresh_hours: int = DEFAULT_FRESH_HOURS,
    item_label: str = "材料",
    source_label: str = "历史消息日志",
) -> str:
    material_index = build_material_index(snapshots)
    latest_ts = max((snapshot.ts for snapshot in snapshots.values()), default="-")
    earliest_ts = min((snapshot.ts for snapshot in snapshots.values()), default="-")
    only_names = tuple(name for name in only_names if name)
    now = datetime.now()

    is_single = len(snapshots) == 1 and bool(only_names)
    item_label = item_label or "材料"
    title = f"【储物袋{item_label}盘点】"
    if is_single:
        snapshot = next(iter(snapshots.values()))
        age = get_snapshot_age_seconds(snapshot, now)
        if age is None:
            freshness_text = "未知"
        elif age <= max(1, int(fresh_hours or DEFAULT_FRESH_HOURS)) * 3600:
            freshness_text = f"{format_age(age)}前"
        else:
            freshness_text = f"过期，{format_age(age)}前"
        lines = [
            title,
            f"身份：{get_identity_display_name(snapshot.owner, identities)}",
            f"数据源：{source_label}",
            f"快照：{snapshot.ts or '-'}",
            f"时效：{freshness_text}",
            f"{item_label}：{len(material_index)} 种",
        ]
    else:
        freshness_summary, freshness_details = get_freshness_summary(
            snapshots,
            identities,
            protected_names,
            now=now,
            fresh_hours=fresh_hours,
        )
        lines = [
            title,
            f"身份：{len(snapshots)} 个",
            f"数据源：{source_label}",
            f"{item_label}：{len(material_index)} 种",
            f"快照：{earliest_ts} -> {latest_ts}",
            f"时效：{freshness_summary}",
        ]
        if only_names:
            lines.append(f"过滤：{', '.join(only_names)}")
        if freshness_details:
            lines.extend(["", "【时效提醒】"])
            lines.extend(freshness_details)

    lines.extend(["", f"【{item_label}明细】"])
    if material_index:
        for name, holders in sorted(material_index.items(), key=material_sort_key):
            total = format_number(sum(holders.values()))
            if is_single:
                lines.append(f"{name}：{total}")
            else:
                lines.append(f"{name}：{total}｜{format_holder_list(holders, identities, top_holders)}")
    else:
        lines.append(f"无可用{item_label}快照")

    if verbose:
        protected_identity_count = 0
        if identities:
            protected_identity_count = sum(
                1
                for item in {identity.username for identity in identities.values()}
                if is_protected_name(item, protected_names)
            )
        lines.extend(
            [
                "",
                "【解析统计】",
                f"数据源：{messages_dir}",
                f"范围：{since or '全部'} -> {until or '全部'}",
                f"保护账号：{', '.join(protected_names) if protected_names else '无'}",
                f"身份白名单：{len({item.username for item in identities.values() if item.username})} 个"
                f"（保护账号 {protected_identity_count} 个已排除）",
                f"扫描行数：{format_number(stats.scanned_lines)}",
                f"储物袋消息：{format_number(stats.bag_messages)}",
                f"通过过滤：{format_number(stats.parsed_snapshots)} 条",
                f"最终快照：{format_number(len(snapshots))} 个身份",
                f"跳过保护账号：{format_number(stats.skipped_protected)}",
                f"跳过非登记身份：{format_number(stats.skipped_not_identity)}",
                f"空材料快照：{format_number(stats.empty_materials)}",
                f"JSON 解析失败：{format_number(stats.invalid_json)}",
            ]
        )
    lines.extend(["", "离线快照，仅汇总，不转移。"])
    return "\n".join(lines)


def split_report(text: str, limit: int = DEFAULT_CHUNK_LIMIT) -> list[str]:
    limit = max(500, int(limit or DEFAULT_CHUNK_LIMIT))
    chunks = []
    current = []
    current_len = 0
    for line in text.splitlines():
        line_len = len(line) + 1
        if current and current_len + line_len > limit:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += line_len
    if current:
        chunks.append("\n".join(current))
    if len(chunks) <= 1:
        return chunks
    total = len(chunks)
    return [f"{chunk}\n\n({idx}/{total})" for idx, chunk in enumerate(chunks, 1)]


def send_log_group_chunks(chunks: list[str], env_file: Path, *, topic_id: int = 0) -> None:
    env = {**load_env_file(env_file), **os.environ}
    token = str(env.get("LOG_BOT_TOKEN") or "").strip()
    chat_id = str(env.get("LOG_GROUP_ID") or "").strip()
    if not token:
        raise RuntimeError("LOG_BOT_TOKEN 为空，无法用离线脚本发送日志群")
    if not chat_id:
        raise RuntimeError("LOG_GROUP_ID 为空，无法发送日志群")
    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    for chunk in chunks:
        body = parse.urlencode(
            {
                "chat_id": chat_id,
                "text": chunk,
                "disable_web_page_preview": "true",
                **({"message_thread_id": int(topic_id)} if int(topic_id or 0) > 0 else {}),
            }
        ).encode("utf-8")
        req = request.Request(api_url, data=body, method="POST")
        with request.urlopen(req, timeout=20) as resp:
            payload = resp.read().decode("utf-8", errors="replace")
            if resp.status >= 400:
                raise RuntimeError(f"日志群发送失败 HTTP {resp.status}: {payload}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="离线解析储物袋材料库存并生成汇总")
    parser.add_argument("--messages-dir", default=str(resolve_default_messages_dir()), help="消息 JSONL 日志目录")
    parser.add_argument("--db-file", default=str(resolve_default_db_file()), help="身份状态 SQLite 数据库")
    parser.add_argument("--env-file", default=str(DEFAULT_RUNTIME_ROOT / ".env"), help="日志群发送时读取的 .env")
    parser.add_argument("--since", default="", help="起始日志日期，格式 YYYY-MM-DD")
    parser.add_argument("--until", default="", help="结束日志日期，格式 YYYY-MM-DD")
    parser.add_argument(
        "--protected-name",
        action="append",
        default=list(DEFAULT_PROTECTED_NAMES),
        help="保护账号名，可重复传入；默认不排除任何账号",
    )
    parser.add_argument("--top-holders", type=int, default=DEFAULT_TOP_HOLDERS, help="每个物品展示的主要持有人数量")
    parser.add_argument("--chunk-limit", type=int, default=DEFAULT_CHUNK_LIMIT, help="日志群分段字符上限")
    parser.add_argument("--log-topic-id", type=int, default=0, help="发送日志群时指定 Telegram forum topic id")
    parser.add_argument("--fresh-hours", type=int, default=DEFAULT_FRESH_HOURS, help="快照新鲜阈值，单位小时")
    parser.add_argument("--only-name", action="append", default=[], help="只统计指定 username，可重复传入")
    parser.add_argument("--verbose", action="store_true", help="输出数据源、过滤统计等调试信息")
    parser.add_argument("--no-identity-filter", action="store_true", help="不使用登记身份白名单，默认不建议开启")
    parser.add_argument(
        "--source",
        choices=("auto", "cache", "logs"),
        default="auto",
        help="数据源：auto/cache/logs；默认优先读取 API/本地缓存，缓存为空再扫历史日志",
    )
    parser.add_argument("--send-log-group", action="store_true", help="发送到日志群；默认只打印")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    messages_dir = Path(args.messages_dir).expanduser().resolve()
    db_file = Path(args.db_file).expanduser().resolve()
    protected_names = tuple(dict.fromkeys(args.protected_name or DEFAULT_PROTECTED_NAMES))
    identities = load_identities(db_file)
    identity_aliases = build_identity_alias_map(identities)
    identity_names = {
        normalize_name(identity.username)
        for identity in identities.values()
        if identity.username and not is_protected_name(identity.username, protected_names)
    }
    source_label = "历史消息日志"
    item_label = "材料"
    snapshots: dict[str, BagSnapshot] = {}
    stats = ParseStats()
    if args.source in {"auto", "cache"}:
        snapshots, stats = read_cached_snapshots(
            db_file,
            identities,
            protected_names,
            since=args.since,
            until=args.until,
            require_identity=not args.no_identity_filter,
        )
        if snapshots or args.source == "cache":
            source_label = "API/本地缓存"
            item_label = "物资"
    if not snapshots and args.source in {"auto", "logs"}:
        snapshots, stats = read_latest_snapshots(
            messages_dir,
            identity_names,
            protected_names,
            since=args.since,
            until=args.until,
            require_identity=not args.no_identity_filter,
        )
    only_names = tuple(dict.fromkeys(args.only_name or ()))
    only_name_keys = {
        identity_aliases.get(normalize_name(name), normalize_name(name))
        for name in only_names
        if normalize_name(name)
    }
    if only_name_keys:
        snapshots = {
            owner_key: snapshot
            for owner_key, snapshot in snapshots.items()
            if owner_key in only_name_keys
        }
    report = build_report(
        snapshots,
        stats,
        identities,
        protected_names,
        messages_dir=messages_dir,
        since=args.since,
        until=args.until,
        top_holders=max(1, int(args.top_holders or DEFAULT_TOP_HOLDERS)),
        only_names=only_names,
        verbose=bool(args.verbose),
        fresh_hours=max(1, int(args.fresh_hours or DEFAULT_FRESH_HOURS)),
        item_label=item_label,
        source_label=source_label,
    )
    chunks = split_report(report, args.chunk_limit)
    if args.send_log_group:
        send_log_group_chunks(chunks, Path(args.env_file).expanduser().resolve(), topic_id=args.log_topic_id)
    else:
        print("\n\n".join(chunks))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
