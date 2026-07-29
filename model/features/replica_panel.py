"""Read-only snapshots and renderers for replica log-group panels."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Callable


@dataclass(frozen=True)
class ReplicaPanelKindSnapshot:
    key: str
    name: str
    short: str
    requires_ticket: bool
    ready: int = 0
    blocked: int = 0
    missing: int = 0


@dataclass(frozen=True)
class ReplicaPanelSnapshot:
    mode: str
    summary_rows: tuple[ReplicaPanelKindSnapshot, ...] = ()
    room_line: str = ""
    opener_line: str = ""
    preview_lines: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReplicaCdKindSnapshot:
    key: str
    short: str
    ready: int = 0
    busy: int = 0
    limited: int = 0


@dataclass(frozen=True)
class ReplicaCdOverviewSnapshot:
    kinds: tuple[ReplicaCdKindSnapshot, ...] = ()
    detail_rows: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ReplicaPanelReadModel:
    build_panel_snapshot: Callable[[str, bool], ReplicaPanelSnapshot]
    build_cd_snapshot: Callable[[], ReplicaCdOverviewSnapshot]


_READ_MODEL: ReplicaPanelReadModel | None = None


def bind_replica_panel_read_model(read_model: ReplicaPanelReadModel) -> None:
    global _READ_MODEL
    _READ_MODEL = read_model


def _get_read_model() -> ReplicaPanelReadModel:
    if _READ_MODEL is None:
        raise RuntimeError("replica panel read model is not bound")
    return _READ_MODEL


def render_log_group_replica_panel(snapshot: ReplicaPanelSnapshot, *, html: bool = False) -> str:
    if snapshot.mode == "summary":
        lines = []
        for row in snapshot.summary_rows:
            if row.ready > 0:
                status = f"可开 {row.ready}"
            elif row.blocked > 0:
                status = f"不可开 {row.blocked}"
            else:
                status = "无票/无资格" if row.requires_ticket else "无资格"
            lines.append(f"{row.name}：{status}")
        lines.append("操作：先点查询按钮看单本；可开则可点开本按钮")
        text = "\n".join(lines)
        return escape(text) if html else text

    lines = [snapshot.room_line or "房间：无", snapshot.opener_line]
    lines.extend(line for line in snapshot.preview_lines if line)
    lines.append("操作：点按钮")
    return "\n".join(line for line in lines if line)


def render_log_group_replica_cd_overview(
    snapshot: ReplicaCdOverviewSnapshot,
    *,
    html: bool = False,
    max_rows: int = 10,
) -> str:
    ready_text = "｜".join(f"{row.short}{row.ready}" for row in snapshot.kinds)
    busy_text = "｜".join(f"{row.short}{row.busy}" for row in snapshot.kinds if row.busy > 0)
    limited_text = "｜".join(f"{row.short}{row.limited}" for row in snapshot.kinds if row.limited > 0)

    lines = [
        "副本 CD 概览",
        f"可开：{ready_text}",
        f"冷却/占用：{busy_text or '无'}",
    ]
    if limited_text:
        lines.append(f"条件受限：{limited_text}")
    if snapshot.detail_rows:
        lines.append("明细：")
        visible_rows = snapshot.detail_rows[:max(1, int(max_rows or 10))]
        for username, detail_text in visible_rows:
            lines.append(f"- {username}｜{detail_text}")
        if len(snapshot.detail_rows) > len(visible_rows):
            lines.append(f"- 另 {len(snapshot.detail_rows) - len(visible_rows)} 个略")
    else:
        lines.append("明细：无")
    text = "\n".join(lines)
    return escape(text) if html else text


def format_log_group_replica_panel(query_text: str = "", *, html: bool = False) -> str:
    snapshot = _get_read_model().build_panel_snapshot(str(query_text or "").strip(), html)
    return render_log_group_replica_panel(snapshot, html=html)


def format_log_group_replica_cd_overview(*, html: bool = False, max_rows: int = 10) -> str:
    snapshot = _get_read_model().build_cd_snapshot()
    return render_log_group_replica_cd_overview(snapshot, html=html, max_rows=max_rows)


def format_log_group_replica_help(*, html: bool = False) -> str:
    lines = [
        "查询：.查询昆 / .查询虚 / .查询苍 / .查询坠 / .查询黄 / .查询落 / .查询小",
        "总览：.查询副本",
        "冷却：.副本cd",
        "昆吾：点开昆 -> 加入/进入 -> 自动选路",
        "房间：面板按钮可加入推荐、进入、解散、刷新",
    ]
    text = "\n".join(lines)
    return escape(text) if html else text


__all__ = [
    "ReplicaCdKindSnapshot",
    "ReplicaCdOverviewSnapshot",
    "ReplicaPanelKindSnapshot",
    "ReplicaPanelReadModel",
    "ReplicaPanelSnapshot",
    "bind_replica_panel_read_model",
    "format_log_group_replica_cd_overview",
    "format_log_group_replica_help",
    "format_log_group_replica_panel",
    "render_log_group_replica_cd_overview",
    "render_log_group_replica_panel",
]
