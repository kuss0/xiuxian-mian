import asyncio
import json
import time
from datetime import datetime

from telethon import functions, types
from telethon.errors import FloodWaitError

from .config import (
    CMD_DEEP_RETREAT,
    CMD_PET,
    CMD_PET_TRIAL,
    CMD_PET_WARM,
    DEEP_RETREAT_CD,
    TZ_LOCAL,
    get_account_offline_reason,
    get_registered_client,
    is_account_offline,
    mark_account_offline,
)
from .persistence import get_db_conn, init_db
from .state import get_game_group_id, get_game_topic_id, get_identity_account


OFFICIAL_SCHEDULE_SOURCE = "official_schedule"
PRESET_DEEP_RETREAT = "deep_retreat"
PRESET_PET_TOUCH = "pet_touch"
PRESET_PET_WARM = "pet_warm"
PRESET_PET_TRIAL = "pet_trial"

PRESET_LABELS = {
    PRESET_DEEP_RETREAT: "深度闭关",
    PRESET_PET_TOUCH: "抚摸法宝",
    PRESET_PET_WARM: "温养器灵",
    PRESET_PET_TRIAL: "器灵试炼",
}

DEFAULT_HORIZON_DAYS = 3


def _now():
    return time.time()


def _to_float_ts(value, default=None):
    if value in {None, ""}:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _positive_int(value, default):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _fmt_schedule_at(ts):
    if not ts:
        return ""
    return datetime.fromtimestamp(float(ts), TZ_LOCAL).strftime("%Y-%m-%d %H:%M:%S UTC+8")


def _normalize_template_key(template_key):
    key = str(template_key or "").strip()
    return key if key in PRESET_LABELS else ""


def _normalize_command(command):
    return str(command or "").strip()


def _clamp_horizon_days(value):
    return min(7, _positive_int(value, DEFAULT_HORIZON_DAYS))


def build_deep_retreat_plan(anchor_at, *, horizon_days=DEFAULT_HORIZON_DAYS):
    anchor_at = _to_float_ts(anchor_at, default=_now()) or _now()
    horizon_days = _clamp_horizon_days(horizon_days)
    end_at = anchor_at + horizon_days * 86400
    items = []
    due_at = anchor_at + DEEP_RETREAT_CD
    while due_at <= end_at:
        items.append({"command": CMD_DEEP_RETREAT, "schedule_at": due_at + 180, "offset_sec": int(due_at + 180 - anchor_at)})
        due_at += DEEP_RETREAT_CD
    return items


def build_pet_plan(command_prefix, pet_name, anchor_at, *, interval_sec, horizon_days=DEFAULT_HORIZON_DAYS):
    command_prefix = _normalize_command(command_prefix)
    pet_name = _normalize_command(pet_name)
    if not command_prefix:
        return []
    command = command_prefix if not pet_name else f"{command_prefix} {pet_name}"
    anchor_at = _to_float_ts(anchor_at, default=_now()) or _now()
    horizon_days = _clamp_horizon_days(horizon_days)
    interval_sec = _positive_int(interval_sec, 7200)
    end_at = anchor_at + horizon_days * 86400
    items = []
    due_at = anchor_at + interval_sec
    while due_at <= end_at:
        schedule_at = due_at + 180
        items.append({"command": command, "schedule_at": schedule_at, "offset_sec": int(schedule_at - anchor_at)})
        due_at += interval_sec
    return items


def build_preset_plan(template_key, *, anchor_at=None, horizon_days=DEFAULT_HORIZON_DAYS, pet_name=""):
    key = _normalize_template_key(template_key)
    anchor = _to_float_ts(anchor_at, default=_now()) or _now()
    horizon_days = _clamp_horizon_days(horizon_days)
    if key == PRESET_DEEP_RETREAT:
        items = build_deep_retreat_plan(anchor, horizon_days=horizon_days)
    elif key == PRESET_PET_TOUCH:
        items = build_pet_plan(CMD_PET, pet_name, anchor, interval_sec=2 * 3600, horizon_days=horizon_days)
    elif key == PRESET_PET_WARM:
        items = build_pet_plan(CMD_PET_WARM, pet_name, anchor, interval_sec=6 * 3600, horizon_days=horizon_days)
    elif key == PRESET_PET_TRIAL:
        items = build_pet_plan(CMD_PET_TRIAL, pet_name, anchor, interval_sec=8 * 3600, horizon_days=horizon_days)
    else:
        items = []
    return {
        "template_key": key,
        "template_label": PRESET_LABELS.get(key, key),
        "anchor_at": anchor,
        "anchor_text": _fmt_schedule_at(anchor),
        "horizon_days": _clamp_horizon_days(horizon_days),
        "items": [
            {
                **item,
                "schedule_text": _fmt_schedule_at(item.get("schedule_at")),
            }
            for item in items
        ],
    }


def create_schedule_batch(send_as_id, template_key, *, anchor_at, horizon_days, options=None, source=OFFICIAL_SCHEDULE_SOURCE):
    init_db()
    now = _now()
    conn = get_db_conn()
    cur = conn.execute(
        """
        INSERT INTO official_schedule_batches
            (send_as_id, template_key, name, anchor_at, horizon_days, status, source, options_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
        """,
        (
            int(send_as_id),
            str(template_key or ""),
            PRESET_LABELS.get(str(template_key or ""), str(template_key or "")),
            float(anchor_at or 0),
            int(horizon_days or 0),
            str(source or ""),
            json.dumps(options or {}, ensure_ascii=False, sort_keys=True),
            now,
            now,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def replace_planned_batch(send_as_id, template_key, plan_items, *, anchor_at, horizon_days, options=None, source=OFFICIAL_SCHEDULE_SOURCE):
    init_db()
    conn = get_db_conn()
    now = _now()
    conn.execute(
        """
        UPDATE official_schedule_batches
        SET status = 'replaced', updated_at = ?
        WHERE send_as_id = ? AND template_key = ? AND status = 'active'
        """,
        (now, int(send_as_id), str(template_key or "")),
    )
    batch_id = create_schedule_batch(
        send_as_id,
        template_key,
        anchor_at=anchor_at,
        horizon_days=horizon_days,
        options=options,
        source=source,
    )
    for item in plan_items or []:
        conn.execute(
            """
            INSERT INTO official_scheduled_messages
                (batch_id, send_as_id, template_key, command, schedule_at, scheduled_msg_id, status, source, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 0, 'planned', ?, ?, ?)
            """,
            (
                batch_id,
                int(send_as_id),
                str(template_key or ""),
                _normalize_command((item or {}).get("command")),
                float((item or {}).get("schedule_at") or 0),
                str(source or ""),
                now,
                now,
            ),
        )
    conn.commit()
    return batch_id


def list_local_schedules(send_as_id=None, *, include_inactive=False, limit=200):
    init_db()
    conn = get_db_conn()
    params = []
    where = []
    if send_as_id not in {None, ""}:
        where.append("m.send_as_id = ?")
        params.append(int(send_as_id))
    if not include_inactive:
        where.append("COALESCE(b.status, '') NOT IN ('deleted', 'replaced')")
        where.append("m.status != 'deleted'")
    where_sql = "WHERE " + " AND ".join(where) if where else ""
    rows = conn.execute(
        f"""
        SELECT
            m.id, m.batch_id, m.send_as_id, m.template_key, m.command, m.schedule_at,
            m.scheduled_msg_id, m.status, m.source, m.last_error, m.created_at, m.updated_at,
            b.name AS batch_name, b.anchor_at, b.horizon_days, b.status AS batch_status
        FROM official_scheduled_messages m
        LEFT JOIN official_schedule_batches b ON b.id = m.batch_id
        {where_sql}
        ORDER BY m.schedule_at ASC, m.id ASC
        LIMIT ?
        """,
        (*params, int(limit or 200)),
    ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "batch_id": int(row["batch_id"] or 0),
            "send_as_id": int(row["send_as_id"]),
            "template_key": row["template_key"] or "",
            "template_label": PRESET_LABELS.get(row["template_key"] or "", row["template_key"] or ""),
            "command": row["command"] or "",
            "schedule_at": float(row["schedule_at"] or 0),
            "schedule_text": _fmt_schedule_at(row["schedule_at"] or 0),
            "scheduled_msg_id": int(row["scheduled_msg_id"] or 0),
            "status": row["status"] or "",
            "source": row["source"] or "",
            "last_error": row["last_error"] or "",
            "batch_name": row["batch_name"] or "",
            "anchor_at": float(row["anchor_at"] or 0),
            "horizon_days": int(row["horizon_days"] or 0),
            "batch_status": row["batch_status"] or "",
        }
        for row in rows
    ]


def mark_scheduled_message_result(local_id, *, scheduled_msg_id=0, status="", last_error=""):
    init_db()
    updates = ["updated_at = ?"]
    params = [_now()]
    if scheduled_msg_id:
        updates.append("scheduled_msg_id = ?")
        params.append(int(scheduled_msg_id))
    if status:
        updates.append("status = ?")
        params.append(str(status))
    if last_error:
        updates.append("last_error = ?")
        params.append(str(last_error))
    params.append(int(local_id))
    get_db_conn().execute(
        f"UPDATE official_scheduled_messages SET {', '.join(updates)} WHERE id = ?",
        params,
    )
    get_db_conn().commit()


async def create_official_messages_for_batch(batch_id, *, limit=80, spacing_sec=1.5):
    init_db()
    conn = get_db_conn()
    rows = conn.execute(
        """
        SELECT id, send_as_id, command, schedule_at
        FROM official_scheduled_messages
        WHERE batch_id = ? AND status IN ('planned', 'failed') AND schedule_at > ?
        ORDER BY schedule_at ASC, id ASC
        LIMIT ?
        """,
        (int(batch_id), _now() + 60, int(limit or 80)),
    ).fetchall()
    created = 0
    failed = 0
    errors = []
    for index, row in enumerate(rows):
        local_id = int(row["id"])
        try:
            await create_official_scheduled_message(
                int(row["send_as_id"]),
                row["command"] or "",
                float(row["schedule_at"] or 0),
                local_id=local_id,
            )
            created += 1
        except Exception as e:
            failed += 1
            error_text = str(e or "创建失败")
            mark_scheduled_message_result(local_id, status="failed", last_error=error_text)
            errors.append({"id": local_id, "error": error_text})
            break
        if index < len(rows) - 1 and spacing_sec > 0:
            await asyncio.sleep(float(spacing_sec))
    now = _now()
    if rows:
        status = "scheduled" if failed == 0 else "partial_failed"
        conn.execute(
            "UPDATE official_schedule_batches SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, int(batch_id)),
        )
        conn.commit()
    return {
        "batch_id": int(batch_id),
        "created": created,
        "failed": failed,
        "total": len(rows),
        "errors": errors,
    }


async def _resolve_schedule_context(send_as_id):
    from . import runtime

    send_as_id = int(send_as_id)
    account_id = int(get_identity_account(send_as_id) or 0)
    if account_id and is_account_offline(account_id):
        raise RuntimeError(get_account_offline_reason(account_id) or "账号离线")
    if account_id:
        active_client = get_registered_client(account_id)
        if active_client is None:
            reason = "账号 client 未注册或启动失败"
            mark_account_offline(account_id, reason)
            raise RuntimeError(reason)
        await runtime._ensure_account_client_ready(active_client)
    else:
        active_client = runtime._get_any_authed_client()
    game_group_id = get_game_group_id()
    if not game_group_id:
        raise ValueError("游戏群聊 ID 未配置，请在 UI 基础配置中设置")
    try:
        peer = await active_client.get_input_entity(game_group_id)
    except ValueError:
        await active_client.get_dialogs()
        peer = await active_client.get_input_entity(game_group_id)
    send_as_peer = await active_client.get_input_entity(send_as_id)
    topic_id = int(get_game_topic_id() or 0)
    reply_to = types.InputReplyToMessage(reply_to_msg_id=topic_id) if topic_id > 0 else None
    return active_client, peer, send_as_peer, reply_to


async def create_official_scheduled_message(send_as_id, command, schedule_at, *, local_id=None):
    from . import runtime

    command = _normalize_command(command)
    if not command:
        raise ValueError("定时消息内容不能为空")
    schedule_at = float(schedule_at or 0)
    if schedule_at <= _now() + 60:
        raise ValueError("官方定时时间至少需要晚于当前 60 秒")
    active_client, peer, send_as_peer, reply_to = await _resolve_schedule_context(send_as_id)
    try:
        result = await active_client(
            functions.messages.SendMessageRequest(
                peer=peer,
                message=command,
                reply_to=reply_to,
                send_as=send_as_peer,
                schedule_date=datetime.fromtimestamp(schedule_at, TZ_LOCAL),
            )
        )
    except FloodWaitError as flood_err:
        raise RuntimeError(f"TG FloodWait {int(flood_err.seconds)}s") from flood_err
    scheduled_msg_id = runtime._extract_sent_message_id(result)
    if scheduled_msg_id <= 0:
        raise ValueError("无法从官方定时结果中解析消息 ID")
    if local_id:
        mark_scheduled_message_result(local_id, scheduled_msg_id=scheduled_msg_id, status="scheduled")
    return scheduled_msg_id


async def list_official_scheduled_messages(send_as_id):
    active_client, peer, _send_as_peer, _reply_to = await _resolve_schedule_context(send_as_id)
    result = await active_client(functions.messages.GetScheduledHistoryRequest(peer=peer, hash=0))
    messages = getattr(result, "messages", None) or []
    items = []
    for message in messages:
        from_id = getattr(message, "from_id", None)
        schedule_date = getattr(message, "date", None)
        schedule_at = schedule_date.timestamp() if hasattr(schedule_date, "timestamp") else 0
        items.append(
            {
                "scheduled_msg_id": int(getattr(message, "id", 0) or 0),
                "message": str(getattr(message, "message", "") or ""),
                "schedule_at": schedule_at,
                "schedule_text": _fmt_schedule_at(schedule_at),
                "from_id": str(from_id or ""),
            }
        )
    return items


async def delete_official_scheduled_messages(send_as_id, scheduled_msg_ids):
    ids = [int(item) for item in (scheduled_msg_ids or []) if int(item or 0) > 0]
    if not ids:
        return 0
    active_client, peer, _send_as_peer, _reply_to = await _resolve_schedule_context(send_as_id)
    await active_client(functions.messages.DeleteScheduledMessagesRequest(peer=peer, id=ids))
    return len(ids)


async def delete_local_schedule_records(record_ids=None, batch_id=None, *, delete_official=False):
    init_db()
    conn = get_db_conn()
    params = []
    where = []
    if batch_id not in {None, ""}:
        where.append("batch_id = ?")
        params.append(int(batch_id))
    if record_ids:
        ids = [int(item) for item in record_ids if int(item or 0) > 0]
        if ids:
            where.append("id IN (%s)" % ",".join("?" for _ in ids))
            params.extend(ids)
    if not where:
        raise ValueError("缺少 record_ids 或 batch_id")
    rows = conn.execute(
        f"SELECT id, send_as_id, scheduled_msg_id FROM official_scheduled_messages WHERE {' AND '.join(where)}",
        params,
    ).fetchall()
    deleted_official = 0
    if delete_official:
        by_identity = {}
        for row in rows:
            scheduled_msg_id = int(row["scheduled_msg_id"] or 0)
            if scheduled_msg_id > 0:
                by_identity.setdefault(int(row["send_as_id"]), []).append(scheduled_msg_id)
        for send_as_id, scheduled_ids in by_identity.items():
            deleted_official += await delete_official_scheduled_messages(send_as_id, scheduled_ids)
    now = _now()
    conn.execute(
        f"UPDATE official_scheduled_messages SET status = 'deleted', updated_at = ? WHERE {' AND '.join(where)}",
        (now, *params),
    )
    if batch_id not in {None, ""}:
        conn.execute(
            "UPDATE official_schedule_batches SET status = 'deleted', updated_at = ? WHERE id = ?",
            (now, int(batch_id)),
        )
    conn.commit()
    return {"records": len(rows), "official": deleted_official}
