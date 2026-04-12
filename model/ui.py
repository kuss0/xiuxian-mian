import asyncio
import glob
import html
import importlib.util
import json
import os
import sys
import time
import traceback
from datetime import datetime
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlsplit

try:
    import segno
except ImportError:
    segno = None
    project_root = os.path.dirname(os.path.dirname(__file__))
    for segno_init in sorted(glob.glob(os.path.join(project_root, ".venv", "lib", "python*", "site-packages", "segno", "__init__.py"))):
        try:
            package_dir = os.path.dirname(segno_init)
            spec = importlib.util.spec_from_file_location("segno", segno_init, submodule_search_locations=[package_dir])
            if not spec or not spec.loader:
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules["segno"] = module
            spec.loader.exec_module(module)
            segno = module
            break
        except Exception:
            sys.modules.pop("segno", None)
            segno = None

from .config import (
    MODULE_KEY_MAP,
    STARGAZER_STAR_CHOICES,
    TIANTI_RANK_CHOICES,
    TZ_LOCAL,
    UI_AUTH_COOKIE_NAME,
    UI_AUTH_IDLE_TIMEOUT_SEC,
    UI_AUTH_SESSION_TIMEOUT_SEC,
    UI_AUTO_REFRESH_SEC,
    UI_HOST,
    UI_PORT,
    UI_PUBLIC_BASE_URL,
    create_account_client,
    get_client,
    register_client,
)
from .control import (
    delete_identity as delete_control_identity,
    get_identity_info_refresh_state,
    get_module_status_text,
    get_single_module_status_text,
    get_startup_module_alerts,
    refresh_identity_info,
    register_identity,
    set_identity_enabled as set_control_identity_enabled,
    set_module_enabled,
    set_module_window_config,
    toggle_global_enabled,
)
from .features.deep_retreat import get_deep_retreat_phase_text
from .features.guanxing import get_guanxing_round_summary_text
from .features.guanxing_monitor import get_guanxing_monitor_summary_text
from .features.jiyin import apply_jiyin_choice, get_jiyin_choice_label, normalize_jiyin_choice, resolve_jiyin_choice
from .features.stargazer import sync_stargazer_total_slots
from .features.tianti import sync_tianti_status
from .features.yuanying import get_yuanying_phase_text
from .persistence import save_state
from .runtime import consume_unseen_startup_alerts, fetch_forum_topics, redeem_ui_login_token, send_audit_log, touch_ui_session
from .state import (
    convert_window_hours_local_to_utc,
    format_window_text,
    get_accounts,
    get_available_module_names,
    get_forum_topics,
    get_forum_topics_updated_at,
    get_game_bot_ids,
    get_game_group_id,
    get_game_topic_id,
    get_global_enabled,
    get_guanxing_monitor_enabled,
    is_auto_delete_sent_messages_enabled,
    get_identity_display_name,
    get_identity_enabled,
    get_identity_ids,
    get_identity_ui_display_name,
    get_identity_state,
    get_module_window_hours_local,
    get_realm_sort_key,
    get_send_as_profile,
    get_stargazer_star_choice,
    get_stargazer_total_slots,
    get_tianti_rank_choice,
    set_account,
    set_auto_delete_sent_messages,
    set_forum_topics,
    set_game_bot_ids,
    set_game_group_id,
    set_game_topic_id,
    set_guanxing_monitor_enabled,
    set_identity_account,
    set_pet_name,
    set_stargazer_star_choice,
    set_tianti_rank_choice,
    state,
    use_identity,
)
from .timing import fmt_abs_ts

_ui_server = None
UI_FAVICON_PNG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "favicon.png")
UI_WEB_DIR = os.path.join(os.path.dirname(__file__), "web")
UI_TEMPLATE_DIR = os.path.join(UI_WEB_DIR, "pages")
UI_STATIC_DIR = os.path.join(UI_WEB_DIR, "static")
UI_STATIC_CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
}


def get_identity_ui_snapshot(send_as_id):
    send_as_id = int(send_as_id)
    now = time.time()
    identity_enabled = get_identity_enabled(send_as_id)
    global_enabled = get_global_enabled()
    with use_identity(send_as_id):
        identity_state = get_identity_state(send_as_id)
        profile = get_send_as_profile(send_as_id)
        modules = []
        available_module_names = get_available_module_names(send_as_id)
        for module_name in available_module_names:
            configured_enabled = bool(identity_state.get(MODULE_KEY_MAP[module_name], False))
            effective_enabled = bool(global_enabled and identity_enabled and configured_enabled)
            effective_reason = ""
            if configured_enabled and not global_enabled:
                effective_reason = "全局已暂停，恢复后会按保存状态继续运行。"
            elif configured_enabled and not identity_enabled:
                effective_reason = "当前身份已暂停，该模块配置已保留，重新开启身份后会按保存状态恢复运行。"
            elif not configured_enabled:
                effective_reason = "模块已关闭"
            modules.append({
                "name": module_name,
                "enabled": configured_enabled,
                "effective_enabled": effective_enabled,
                "effective_reason": effective_reason,
                "detail": _format_module_detail_for_ui(module_name, get_single_module_status_text(module_name, send_as_id)),
            })
        checkin_window_local = get_module_window_hours_local("点卯", send_as_id)
        tower_window_local = get_module_window_hours_local("闯塔", send_as_id)
        sect_refresh_state = get_identity_info_refresh_state(send_as_id)
        sect_refresh_pending = bool(sect_refresh_state.get("pending"))
        sect_refresh_error = sect_refresh_state.get("error") or ""
        saved_jiyin_choice = normalize_jiyin_choice(profile.get("jiyin_choice") or "")
        effective_jiyin_choice, _jiyin_choice_source = resolve_jiyin_choice(send_as_id)
        jiyin_reply_to_msg_id = int(identity_state.get("jiyin_reply_to_msg_id", 0) or 0)
        stargazer_followup_due_at = float(identity_state.get("stargazer_followup_due_at", 0) or 0)
        stargazer_next_panel_time = float(identity_state.get("next_stargazer_panel_time", 0) or 0)
        if stargazer_followup_due_at > 0 and stargazer_next_panel_time > 0:
            stargazer_next_action_time = min(stargazer_followup_due_at, stargazer_next_panel_time)
        else:
            stargazer_next_action_time = stargazer_followup_due_at or stargazer_next_panel_time
        jiyin_deadline_at = float(identity_state.get("next_jiyin_time", 0) or 0)
        identity_status_text = "运行中"
        if not global_enabled:
            identity_status_text = "全局暂停"
        elif not identity_enabled:
            identity_status_text = "已暂停"

        snapshot = {
            "send_as_id": send_as_id,
            "display_name": get_identity_ui_display_name(send_as_id),
            "identity_enabled": identity_enabled,
            "identity_status_text": identity_status_text,
            "username": profile.get("username") or "",
            "label": profile.get("label") or "",
            "daohao": profile.get("daohao") or "",
            "realm": profile.get("realm") or "",
            "pet_name": profile.get("pet_name") or "",
            "sect_name": profile.get("sect_name") or "",
            "xiuwei_current": int(profile.get("xiuwei_current") or 0),
            "xiuwei_max": int(profile.get("xiuwei_max") or 0),
            "sect_updated_at": fmt_abs_ts(profile.get("sect_updated_at") or 0),
            "sect_refresh_pending": sect_refresh_pending,
            "sect_refresh_error": sect_refresh_error,
            "jiyin_choice": saved_jiyin_choice,
            "jiyin_choice_label": get_jiyin_choice_label(saved_jiyin_choice),
            "stargazer_star_choice": get_stargazer_star_choice(send_as_id),
            "stargazer_star_choices": list(STARGAZER_STAR_CHOICES),
            "stargazer_total_slots": get_stargazer_total_slots(send_as_id),
            "tianti_rank_choice": get_tianti_rank_choice(send_as_id),
            "tianti_rank_choices": list(TIANTI_RANK_CHOICES),
            "jiyin_effective_choice": effective_jiyin_choice,
            "jiyin_effective_choice_label": get_jiyin_choice_label(effective_jiyin_choice),
            "jiyin_choice_source": _jiyin_choice_source,
            "jiyin_pending": bool(jiyin_reply_to_msg_id > 0 and jiyin_deadline_at > now),
            "jiyin_deadline_at": fmt_abs_ts(jiyin_deadline_at),
            "jiyin_reply_to_msg_id": jiyin_reply_to_msg_id,
            "jiyin_last_error": identity_state.get("jiyin_last_error") or "",
            "checkin_window_local": {
                "start_hour": checkin_window_local[0],
                "end_hour": checkin_window_local[1],
                "text": format_window_text("点卯", send_as_id),
            },
            "tower_window_local": {
                "start_hour": tower_window_local[0],
                "end_hour": tower_window_local[1],
                "text": format_window_text("闯塔", send_as_id),
            },
            "module_summary": get_module_status_text(send_as_id),
            "modules": modules,
            "timers": {
                "next_irr_time": fmt_abs_ts(identity_state.get("next_irr_time", 0)),
                "next_pet_time": fmt_abs_ts(identity_state.get("next_pet_time", 0)),
                "next_stargazer_panel_time": fmt_abs_ts(identity_state.get("next_stargazer_panel_time", 0)),
                "next_stargazer_action_time": fmt_abs_ts(stargazer_next_action_time),
                "stargazer_followup_due_at": fmt_abs_ts(stargazer_followup_due_at),
                "stargazer_collect_due_at": fmt_abs_ts(identity_state.get("stargazer_collect_due_at", 0)),
                "next_quiz_time": fmt_abs_ts(identity_state.get("next_quiz_time", 0)),
                "next_checkin_time": fmt_abs_ts(identity_state.get("next_checkin_time", 0)),
                "next_tower_time": fmt_abs_ts(identity_state.get("next_tower_time", 0)),
                "next_deep_retreat_time": fmt_abs_ts(identity_state.get("next_deep_retreat_time", 0)),
                "next_yuanying_time": fmt_abs_ts(identity_state.get("next_yuanying_time", 0)),
            },
            "phases": {
                "yuanying": get_yuanying_phase_text(identity_state.get("yuanying_phase"), now),
                "deep_retreat": get_deep_retreat_phase_text(identity_state.get("deep_retreat_phase"), now),
            },
            "pending_task_count": len(identity_state.get("pending_tasks", {})),
            "message_count": len(identity_state.get("my_msg_ids", {})),
        }
    return snapshot


def get_ui_snapshot(session_token=None):
    identities = sorted(
        (get_identity_ui_snapshot(identity_id) for identity_id in get_identity_ids()),
        key=lambda identity: get_realm_sort_key(
            identity.get("realm"),
            identity.get("send_as_id"),
            xiuwei_max=identity.get("xiuwei_max", 0),
            xiuwei_current=identity.get("xiuwei_current", 0),
        ),
    )
    startup_alerts = get_startup_module_alerts()
    if session_token:
        startup_alerts = consume_unseen_startup_alerts(session_token, startup_alerts)
    return {
        "generated_at": datetime.now(TZ_LOCAL).strftime("%Y-%m-%d %H:%M:%S UTC+8"),
        "ui_url": UI_PUBLIC_BASE_URL,
        "account_user_id": state.get("my_user_id") or 0,
        "game_group_id": get_game_group_id(),
        "game_bot_ids": get_game_bot_ids(),
        "game_topic_id": get_game_topic_id(),
        "forum_topics": get_forum_topics(),
        "forum_topics_updated_at": fmt_abs_ts(get_forum_topics_updated_at()),
        "auto_delete_sent_messages": is_auto_delete_sent_messages_enabled(),
        "global_enabled": get_global_enabled(),
        "guanxing_monitor_enabled": get_guanxing_monitor_enabled(),
        "guanxing_monitor_summary": get_guanxing_monitor_summary_text(),
        "guanxing_round_summary": get_guanxing_round_summary_text(),
        "auth_idle_timeout_sec": UI_AUTH_IDLE_TIMEOUT_SEC,
        "refresh_interval_sec": UI_AUTO_REFRESH_SEC,
        "startup_alerts": startup_alerts,
        "accounts": get_accounts(),
        "identities": identities,
        "config_needed": not get_game_group_id() or not get_game_bot_ids(),
    }


def html_escape(value):
    return html.escape(str(value or ""), quote=True)


def _format_module_detail_for_ui(module_name, detail_text):
    lines = str(detail_text or "").splitlines()

    filtered_lines = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if index == 0 and stripped.startswith("👤 "):
            continue
        if index <= 1 and module_name and module_name in stripped:
            continue
        if stripped.startswith("- 当前名称："):
            continue
        if stripped.startswith("- 执行窗口："):
            continue
        filtered_lines.append(line)

    text = "\n".join(filtered_lines).strip()
    return text or "暂无详情"


def _resolve_selected_send_as_id(snapshot, selected_send_as_id=None):
    identity_ids = [identity["send_as_id"] for identity in snapshot.get("identities", [])]
    if not identity_ids:
        return None
    try:
        selected_id = int(selected_send_as_id)
    except (TypeError, ValueError):
        selected_id = None
    if selected_id in identity_ids:
        return selected_id
    return identity_ids[0]


def _cookie_is_secure():
    return UI_PUBLIC_BASE_URL.lower().startswith("https://")


def _load_ui_template(template_name):
    with open(os.path.join(UI_TEMPLATE_DIR, template_name), "r", encoding="utf-8") as fp:
        return fp.read()


def _render_ui_template(template_name, context):
    template = _load_ui_template(template_name)
    for key, value in context.items():
        template = template.replace(f"{{{{{key}}}}}", str(value))
    return template


def _load_ui_static_asset(asset_path):
    normalized_path = (asset_path or "").lstrip("/")
    asset_full_path = os.path.normpath(os.path.join(UI_STATIC_DIR, normalized_path))
    if not asset_full_path.startswith(UI_STATIC_DIR + os.sep):
        return None, None
    if not os.path.isfile(asset_full_path):
        return None, None
    content_type = UI_STATIC_CONTENT_TYPES.get(os.path.splitext(asset_full_path)[1].lower())
    if not content_type:
        return None, None
    with open(asset_full_path, "rb") as fp:
        return fp.read(), content_type


def _build_session_cookie_header(session_token, *, clear=False):
    cookie = SimpleCookie()
    cookie[UI_AUTH_COOKIE_NAME] = "" if clear else (session_token or "")
    morsel = cookie[UI_AUTH_COOKIE_NAME]
    morsel["path"] = "/"
    morsel["httponly"] = True
    morsel["samesite"] = "Lax"
    if _cookie_is_secure():
        morsel["secure"] = True
    if clear:
        morsel["max-age"] = 0
        morsel["expires"] = "Thu, 01 Jan 1970 00:00:00 GMT"
    else:
        morsel["max-age"] = int(UI_AUTH_SESSION_TIMEOUT_SEC)
    return morsel.OutputString()


def _parse_cookies(headers):
    cookie = SimpleCookie()
    raw_cookie = headers.get("cookie", "")
    if raw_cookie:
        cookie.load(raw_cookie)
    return {key: morsel.value for key, morsel in cookie.items()}


def _get_authenticated_session(headers, now=None):
    if now is None:
        now = time.time()
    cookies = _parse_cookies(headers)
    session_token = (cookies.get(UI_AUTH_COOKIE_NAME) or "").strip()
    if not session_token:
        return None, None
    session = touch_ui_session(session_token, now)
    if not session:
        return None, _build_session_cookie_header("", clear=True)
    return session, _build_session_cookie_header(session["session_token"], clear=False)


def _parse_request_body(headers, body_bytes):
    if not body_bytes:
        return {}
    content_type = (headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    body_text = body_bytes.decode("utf-8", errors="ignore")
    if content_type == "application/json":
        try:
            data = json.loads(body_text or "{}")
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}
    if content_type == "application/x-www-form-urlencoded":
        parsed = parse_qs(body_text, keep_blank_values=False)
        return {key: values[0] if len(values) == 1 else values for key, values in parsed.items()}
    return {}


def _make_json_payload(ok, *, message="", error="", snapshot=None, extra=None):
    payload = {"ok": bool(ok)}
    if message:
        payload["message"] = message
    if error:
        payload["error"] = error
    if snapshot is not None:
        payload["snapshot"] = snapshot
    if isinstance(extra, dict):
        payload.update(extra)
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _render_login_page(message=""):
    message_html = f"<div class='flash'>{html_escape(message)}</div>" if message else ""
    login_timeout_minutes = max(1, UI_AUTH_IDLE_TIMEOUT_SEC // 60)
    session_timeout_hours = max(1, UI_AUTH_SESSION_TIMEOUT_SEC // 3600)
    secure_note = "HTTPS 公网地址下会自动使用 Secure Cookie。" if _cookie_is_secure() else "如需 Secure Cookie，请将 UI_PUBLIC_BASE_URL 配置为 https 地址。"
    return _render_ui_template(
        "login.html",
        {
            "message_html": message_html,
            "ui_public_base_url": html_escape(UI_PUBLIC_BASE_URL),
            "login_timeout_minutes": login_timeout_minutes,
            "session_timeout_hours": session_timeout_hours,
            "secure_note": html_escape(secure_note),
        },
    )


def render_ui_page(message="", selected_send_as_id=None, session_token=None):
    snapshot = get_ui_snapshot(session_token=session_token)
    selected_id = _resolve_selected_send_as_id(snapshot, selected_send_as_id)
    boot_data = json.dumps(
        {
            "snapshot": snapshot,
            "selected_send_as_id": selected_id,
            "flash_message": message or "",
        },
        ensure_ascii=False,
    ).replace("</", "<\\/")
    return _render_ui_template(
        "index.html",
        {
            "boot_data": boot_data,
            "ui_auto_refresh_sec": html_escape(str(UI_AUTO_REFRESH_SEC)),
            "poll_interval_ms": int(UI_AUTO_REFRESH_SEC) * 1000,
        },
    )



async def ui_set_identity_enabled(send_as_id, enabled, actor_id=None):
    send_as_id = int(send_as_id)
    if send_as_id not in get_identity_ids():
        return False, f"未知身份: {send_as_id}"
    ok, message = await set_control_identity_enabled(send_as_id, enabled, source="ui", actor_id=actor_id)
    if not ok:
        return False, message or f"切换失败: {get_identity_display_name(send_as_id)}"
    return True, message


async def ui_set_module_enabled(send_as_id, module_name, enabled):
    send_as_id = int(send_as_id)
    if send_as_id not in get_identity_ids():
        return False, f"未知身份: {send_as_id}"
    if module_name not in MODULE_KEY_MAP:
        return False, f"未知模块: {module_name}"
    ok, message = await set_module_enabled(module_name, enabled, send_as_id=send_as_id)
    if not ok:
        return False, message or f"切换失败: {module_name}"
    action_text = "开启" if enabled else "关闭"
    return True, f"已{action_text}{module_name}[{get_identity_display_name(send_as_id)}]"


async def ui_set_pet_name(send_as_id, pet_name):
    send_as_id = int(send_as_id)
    if send_as_id not in get_identity_ids():
        return False, f"未知身份: {send_as_id}"
    pet_name = (pet_name or "").strip()
    if not pet_name:
        return False, "法宝名称不能为空"
    set_pet_name(send_as_id, pet_name)
    save_state()
    await send_audit_log(
        f"🗡️ 已更新法宝名称：{pet_name}",
        scope="identity",
        send_as_id=send_as_id,
    )
    return True, f"已更新法宝名称[{get_identity_display_name(send_as_id)}]：{pet_name}"


async def ui_set_stargazer_star_choice(send_as_id, choice):
    send_as_id = int(send_as_id)
    if send_as_id not in get_identity_ids():
        return False, f"未知身份: {send_as_id}"
    choice = (choice or "").strip()
    if choice not in STARGAZER_STAR_CHOICES:
        return False, "无效的牵引星种"
    set_stargazer_star_choice(send_as_id, choice)
    save_state()
    await send_audit_log(
        f"🔭 已更新牵引星种：{choice}",
        scope="identity",
        send_as_id=send_as_id,
    )
    return True, f"已更新牵引星种[{get_identity_display_name(send_as_id)}]：{choice}"


async def ui_set_tianti_rank_choice(send_as_id, choice):
    send_as_id = int(send_as_id)
    if send_as_id not in get_identity_ids():
        return False, f"未知身份: {send_as_id}"
    choice = (choice or "").strip()
    if choice not in TIANTI_RANK_CHOICES:
        return False, "无效的登天阶档位"
    set_tianti_rank_choice(send_as_id, choice)
    save_state()
    await send_audit_log(
        f"☁️ 已更新登天阶档位：{choice}",
        scope="identity",
        send_as_id=send_as_id,
    )
    return True, f"已更新登天阶档位[{get_identity_display_name(send_as_id)}]：{choice}"


async def ui_sync_stargazer_total_slots(send_as_id):
    send_as_id = int(send_as_id)
    if send_as_id not in get_identity_ids():
        return False, f"未知身份: {send_as_id}"
    ok, message = await sync_stargazer_total_slots(send_as_id)
    return ok, message


async def ui_sync_tianti_status(send_as_id):
    send_as_id = int(send_as_id)
    if send_as_id not in get_identity_ids():
        return False, f"未知身份: {send_as_id}"
    ok, message = await sync_tianti_status(send_as_id)
    return ok, message


async def ui_set_jiyin_choice(send_as_id, choice):
    send_as_id = int(send_as_id)
    if send_as_id not in get_identity_ids():
        return False, f"未知身份: {send_as_id}"
    with use_identity(send_as_id):
        ok, message = await apply_jiyin_choice(choice)
    if not ok:
        return False, message
    return True, f"{message}[{get_identity_display_name(send_as_id)}]"


async def ui_set_module_window(send_as_id, module_name, start_hour_local, end_hour_local):
    send_as_id = int(send_as_id)
    if send_as_id not in get_identity_ids():
        return False, f"未知身份: {send_as_id}"
    try:
        start_hour_local = int(start_hour_local)
        end_hour_local = int(end_hour_local)
    except (TypeError, ValueError):
        return False, "窗口时间必须是整数小时"
    if not (0 <= start_hour_local <= 23 and 0 <= end_hour_local <= 23):
        return False, "窗口时间必须在 0-23 之间"
    if start_hour_local >= end_hour_local:
        return False, "开始时间必须早于结束时间，暂不支持跨天"
    start_hour_utc, end_hour_utc = convert_window_hours_local_to_utc(start_hour_local, end_hour_local)
    if start_hour_utc >= end_hour_utc:
        return False, "当前版本暂不支持跨 UTC 日期的窗口，请避免设置跨北京时间 08:00 的区间"
    ok, message = await set_module_window_config(module_name, start_hour_utc, end_hour_utc, send_as_id=send_as_id)
    if not ok:
        return False, message
    return True, f"已更新{module_name}执行窗口[{get_identity_display_name(send_as_id)}]：UTC+8 {start_hour_local:02d}:00-{end_hour_local:02d}:00"


async def ui_add_identity(send_as_id_raw, actor_id=None, account_id=None):
    ok, message, canonical_id = await register_identity(send_as_id_raw, source="ui", actor_id=actor_id, account_id=account_id)
    if ok and canonical_id and account_id:
        set_identity_account(canonical_id, int(account_id))
        save_state()
    return ok, message, canonical_id


async def ui_delete_identity(send_as_id, actor_id=None):
    send_as_id = int(send_as_id)
    if send_as_id not in get_identity_ids():
        return False, f"未知身份: {send_as_id}"
    return await delete_control_identity(send_as_id, source="ui", actor_id=actor_id)


# ================= 多账号登录 =================
_pending_login = {}  # {session_key: {mode, status, client, flow_id, ...}}


def _cleanup_pending_temp_session_files(session_key):
    from .config import SESSION_DIR

    temp_prefix = os.path.join(SESSION_DIR, f"account_pending_{session_key}")
    for temp_file in glob.glob(f"{temp_prefix}*"):
        try:
            os.remove(temp_file)
        except OSError:
            pass


async def _clear_pending_login(session_key, *, disconnect=True, remove_temp_files=False):
    pending = _pending_login.pop(session_key, None)
    if not pending:
        if remove_temp_files:
            _cleanup_pending_temp_session_files(session_key)
        return

    wait_task = pending.get("wait_task")
    current_task = asyncio.current_task()
    if wait_task and wait_task is not current_task and not wait_task.done():
        wait_task.cancel()

    tc = pending.get("client")
    if disconnect and tc:
        try:
            await tc.disconnect()
        except Exception:
            pass

    if remove_temp_files:
        _cleanup_pending_temp_session_files(session_key)


def _set_pending_login_state(session_key, flow_id=None, **updates):
    pending = _pending_login.get(session_key)
    if not pending:
        return False
    if flow_id is not None and str(pending.get("flow_id") or "") != str(flow_id):
        return False
    next_pending = dict(pending)
    next_pending.update(updates)
    _pending_login[session_key] = next_pending
    return True


def _build_qr_svg_markup(qr_url):
    qr_url = str(qr_url or "").strip()
    if not qr_url:
        return ""
    if segno is None:
        return ""
    try:
        return segno.make(qr_url).svg_inline(scale=6)
    except Exception:
        return ""


async def _finalize_account_login(session_key, tc, *, flow_id=None):
    me = await tc.get_me()
    account_id = int(getattr(me, "id", 0) or 0)
    if account_id <= 0:
        raise RuntimeError("无法解析有效账号 ID")
    username = me.username or me.first_name or str(account_id)

    if flow_id is not None:
        pending = _pending_login.get(session_key)
        if not pending or str(pending.get("flow_id") or "") != str(flow_id):
            try:
                await tc.disconnect()
            except Exception:
                pass
            return False, "登录流程已更新，请重新发起", None

    await tc.disconnect()

    from .config import SESSION_DIR

    temp_prefix = os.path.join(SESSION_DIR, f"account_pending_{session_key}")
    real_prefix = os.path.join(SESSION_DIR, f"account_{account_id}")
    for temp_file in glob.glob(f"{temp_prefix}*"):
        suffix = temp_file[len(temp_prefix):]
        real_file = f"{real_prefix}{suffix}"
        try:
            os.replace(temp_file, real_file)
        except OSError:
            pass

    real_tc = create_account_client(account_id)
    await real_tc.start()
    try:
        await real_tc.get_dialogs()
    except Exception:
        pass
    register_client(account_id, real_tc)

    from .app import _register_event_handlers
    _register_event_handlers(real_tc)

    set_account(account_id, {"session": f"account_{account_id}", "username": username})

    ok, _message, canonical_id = await register_identity(account_id, source="ui_login", account_id=account_id)
    if canonical_id:
        set_identity_account(canonical_id, account_id)

    try:
        from .control import hydrate_identity_profile
        entity = await real_tc.get_me()
        hydrate_identity_profile(entity)
    except Exception:
        pass

    save_state()
    await send_audit_log(f"🔑 新账号登录成功：@{username}｜{account_id}", scope="global")
    return True, f"登录成功: @{username}", account_id


async def _wait_pending_qr_login(session_key, flow_id, tc, qr_login):
    try:
        await qr_login.wait()
    except asyncio.TimeoutError:
        try:
            await tc.disconnect()
        except Exception:
            pass
        _cleanup_pending_temp_session_files(session_key)
        _set_pending_login_state(
            session_key,
            flow_id,
            status="expired",
            message="二维码已过期，请刷新后重试",
            client=None,
            wait_task=None,
            qr_url="",
            qr_expires_at=0,
        )
        return
    except Exception as e:
        err_str = str(e)
        if "Two-steps verification" in err_str or "SessionPasswordNeeded" in err_str or "2FA" in err_str:
            _set_pending_login_state(
                session_key,
                flow_id,
                status="need_2fa",
                message="扫码成功，请输入两步验证密码",
                wait_task=None,
                qr_url="",
                qr_expires_at=0,
            )
            return
        try:
            await tc.disconnect()
        except Exception:
            pass
        _cleanup_pending_temp_session_files(session_key)
        _set_pending_login_state(
            session_key,
            flow_id,
            status="error",
            message=f"二维码登录失败: {e}",
            client=None,
            wait_task=None,
            qr_url="",
            qr_expires_at=0,
        )
        return

    try:
        ok, message, account_id = await _finalize_account_login(session_key, tc, flow_id=flow_id)
    except Exception as e:
        _cleanup_pending_temp_session_files(session_key)
        _set_pending_login_state(
            session_key,
            flow_id,
            status="error",
            message=f"二维码登录失败: {e}",
            client=None,
            wait_task=None,
            qr_url="",
            qr_expires_at=0,
        )
        return

    if ok:
        _set_pending_login_state(
            session_key,
            flow_id,
            status="done",
            message=message,
            account_id=account_id,
            client=None,
            wait_task=None,
            qr_url="",
            qr_expires_at=0,
        )
    else:
        _cleanup_pending_temp_session_files(session_key)


async def ui_account_login_start(phone, session_key):
    phone = (phone or "").strip()
    if not phone:
        return False, "请输入手机号", None

    await _clear_pending_login(session_key, remove_temp_files=True)

    tc = create_account_client(f"pending_{session_key}")
    await tc.connect()
    try:
        sent = await tc.send_code_request(phone)
        _pending_login[session_key] = {
            "mode": "phone",
            "status": "waiting_code",
            "message": "验证码已发送，请查收",
            "client": tc,
            "phone": phone,
            "phone_code_hash": sent.phone_code_hash,
            "qr_url": "",
            "qr_expires_at": 0,
            "wait_task": None,
            "flow_id": str(time.time_ns()),
            "account_id": 0,
        }
        return True, "验证码已发送", None
    except Exception as e:
        try:
            await tc.disconnect()
        except Exception:
            pass
        _cleanup_pending_temp_session_files(session_key)
        return False, f"发送验证码失败: {e}", None


async def ui_account_login_qr_start(session_key):
    await _clear_pending_login(session_key, remove_temp_files=True)

    tc = create_account_client(f"pending_{session_key}")
    await tc.connect()
    try:
        ignored_ids = []
        for raw_account_id in get_accounts().keys():
            try:
                ignored_ids.append(int(raw_account_id))
            except (TypeError, ValueError):
                continue
        qr_login = await tc.qr_login(ignored_ids=ignored_ids or None)
        expires_at = float(qr_login.expires.timestamp()) if getattr(qr_login, "expires", None) else 0
        flow_id = str(time.time_ns())
        _pending_login[session_key] = {
            "mode": "qr",
            "status": "waiting_scan",
            "message": "请使用已登录 Telegram 的手机扫码确认",
            "client": tc,
            "phone": "",
            "phone_code_hash": "",
            "qr_url": qr_login.url,
            "qr_expires_at": expires_at,
            "wait_task": None,
            "flow_id": flow_id,
            "account_id": 0,
        }
        wait_task = asyncio.create_task(_wait_pending_qr_login(session_key, flow_id, tc, qr_login))
        _set_pending_login_state(session_key, flow_id, wait_task=wait_task)
        qr_svg = _build_qr_svg_markup(qr_login.url)
        return True, "二维码已生成，请使用 Telegram 扫码确认", {
            "status": "waiting_scan",
            "qr_url": qr_login.url,
            "qr_svg": qr_svg,
            "qr_expires_at": fmt_abs_ts(expires_at),
            "qr_expires_at_ts": expires_at,
            "remaining_sec": max(0, int(expires_at - time.time())),
        }
    except Exception as e:
        try:
            await tc.disconnect()
        except Exception:
            pass
        _cleanup_pending_temp_session_files(session_key)
        return False, f"生成二维码失败: {e}", None


def ui_account_login_qr_status(session_key):
    pending = _pending_login.get(session_key)
    if not pending or pending.get("mode") != "qr":
        return {
            "status": "cancelled",
            "message": "当前没有进行中的二维码登录",
        }

    qr_expires_at = float(pending.get("qr_expires_at", 0) or 0)
    status = str(pending.get("status") or "waiting_scan")
    payload = {
        "status": status,
        "message": pending.get("message") or "",
        "qr_expires_at": fmt_abs_ts(qr_expires_at),
        "qr_expires_at_ts": qr_expires_at,
    }
    if status == "waiting_scan":
        qr_url = pending.get("qr_url") or ""
        payload.update({
            "qr_url": qr_url,
            "qr_svg": _build_qr_svg_markup(qr_url),
            "remaining_sec": max(0, int(qr_expires_at - time.time())),
        })
    account_id = int(pending.get("account_id", 0) or 0)
    if account_id > 0:
        payload["account_id"] = account_id
    return payload


async def ui_account_login_cancel(session_key):
    await _clear_pending_login(session_key, remove_temp_files=True)
    return True, "已取消当前登录流程"


async def ui_account_login_verify(code, session_key, password=None):
    pending = _pending_login.get(session_key)
    if not pending:
        return False, "登录会话已过期，请重新开始", None

    tc = pending.get("client")
    mode = str(pending.get("mode") or "phone")
    phone = pending.get("phone") or ""
    phone_code_hash = pending.get("phone_code_hash") or ""
    flow_id = pending.get("flow_id")
    status = str(pending.get("status") or "")
    code = (code or "").strip()

    if mode == "qr" and password and status != "need_2fa":
        return False, "当前二维码登录尚未进入两步验证", None

    try:
        if password:
            await tc.sign_in(password=password)
        elif mode == "phone":
            await tc.sign_in(phone, code, phone_code_hash=phone_code_hash)
        else:
            return False, "当前二维码登录尚未进入两步验证", None
    except Exception as e:
        err_str = str(e)
        if "Two-steps verification" in err_str or "SessionPasswordNeeded" in err_str or "2FA" in err_str:
            _set_pending_login_state(session_key, flow_id, status="need_2fa", message="需要两步验证密码")
            return False, "need_2fa", None
        await _clear_pending_login(session_key, remove_temp_files=True)
        return False, f"登录失败: {e}", None

    try:
        ok, message, account_id = await _finalize_account_login(session_key, tc, flow_id=flow_id)
    except Exception as e:
        await _clear_pending_login(session_key, disconnect=False, remove_temp_files=True)
        return False, f"登录失败: {e}", None

    await _clear_pending_login(session_key, disconnect=False, remove_temp_files=False)
    if not ok:
        return False, message, None
    return True, message, account_id


async def ui_get_send_as_peers(account_id):
    """获取指定账号在游戏群中可用的 send_as 身份列表"""
    account_id = int(account_id)
    game_group_id = get_game_group_id()
    if not game_group_id:
        return False, "请先在基础配置中设置游戏群聊 ID", []
    tc = get_client(account_id)
    if not tc:
        return False, f"账号 {account_id} 未登录", []
    try:
        from telethon.tl.functions.channels import GetSendAsRequest
        result = await tc(GetSendAsRequest(peer=game_group_id))
        peers = []
        for send_as_peer in result.peers:
            peer_obj = send_as_peer.peer
            try:
                entity = await tc.get_entity(peer_obj)
                peer_id = int(entity.id)
                name = getattr(entity, "title", None) or getattr(entity, "first_name", None) or str(peer_id)
                username = getattr(entity, "username", "") or ""
                peer_type = "channel" if hasattr(entity, "title") else "user"
                peers.append({"id": peer_id, "name": name, "username": username, "type": peer_type})
            except Exception:
                continue
        existing_ids = list(get_identity_ids())
        return True, f"获取到 {len(peers)} 个可用身份", peers, existing_ids
    except Exception as e:
        return False, f"获取 send_as 列表失败: {e}", []


async def ui_refresh_identity_info(send_as_id, actor_id=None):
    send_as_id = int(send_as_id)
    if send_as_id not in get_identity_ids():
        return False, f"未知身份: {send_as_id}"
    ok, message = await refresh_identity_info(send_as_id, source="ui", actor_id=actor_id)
    return ok, message


async def ui_refresh_forum_topics(game_group_id, actor_id=None):
    ok, message, topics = await fetch_forum_topics(game_group_id)
    if not ok:
        return False, message, []
    set_forum_topics(topics, updated_at=time.time())
    save_state()
    actor_suffix = f"｜操作者：{actor_id}" if actor_id is not None else ""
    await send_audit_log(f"🧩 已刷新话题：群={int(game_group_id)}｜数量={len(topics)}{actor_suffix}", scope="global")
    return True, message, topics


async def ui_set_basic_config(game_group_id, game_bot_ids, game_topic_id, auto_delete_sent_messages, guanxing_monitor_enabled=False, actor_id=None):
    raw_group_id = (str(game_group_id or "")).strip()
    if not raw_group_id:
        return False, "游戏群聊 ID 不能为空"
    try:
        group_id = int(raw_group_id)
    except (TypeError, ValueError):
        return False, "游戏群聊 ID 必须是整数"
    if group_id == 0:
        return False, "游戏群聊 ID 不能为 0"

    raw_bot_ids = (str(game_bot_ids or "")).strip()
    if not raw_bot_ids:
        return False, "游戏 BOT ID 不能为空"
    parsed_bot_ids = []
    seen_bot_ids = set()
    for part in raw_bot_ids.replace("，", ",").split(","):
        item = part.strip()
        if not item:
            continue
        try:
            bot_id = int(item)
        except (TypeError, ValueError):
            return False, "游戏 BOT ID 必须是整数，多个请用逗号分隔"
        if bot_id in seen_bot_ids:
            continue
        seen_bot_ids.add(bot_id)
        parsed_bot_ids.append(bot_id)
    if not parsed_bot_ids:
        return False, "至少需要一个 游戏 BOT ID"

    raw_topic_id = (str(game_topic_id or "")).strip()
    if not raw_topic_id:
        topic_id = 0
    else:
        try:
            topic_id = int(raw_topic_id)
        except (TypeError, ValueError):
            return False, "话题 ID 必须是整数"
        if topic_id < 0:
            return False, "话题 ID 不能为负数"

    auto_delete_enabled = bool(auto_delete_sent_messages)
    guanxing_monitor_switch_enabled = bool(guanxing_monitor_enabled)
    set_game_group_id(group_id)
    normalized_bot_ids = set_game_bot_ids(parsed_bot_ids)
    set_game_topic_id(topic_id)
    set_auto_delete_sent_messages(auto_delete_enabled)
    set_guanxing_monitor_enabled(guanxing_monitor_switch_enabled)
    save_state()
    actor_suffix = f"｜操作者：{actor_id}" if actor_id is not None else ""
    display_topic = str(topic_id) if topic_id > 0 else "未启用"
    display_bots = ", ".join(str(bot_id) for bot_id in normalized_bot_ids)
    display_auto_delete = "开启" if auto_delete_enabled else "关闭"
    display_guanxing_monitor = "开启" if guanxing_monitor_switch_enabled else "关闭"
    await send_audit_log(
        f"🧩 已更新基础配置：群={group_id}｜bot={display_bots}｜话题={display_topic}｜自动删消息={display_auto_delete}｜观星监控={display_guanxing_monitor}{actor_suffix}",
        scope="global",
        limit=320,
    )
    return True, f"已更新基础配置：群聊 {group_id} ｜ bot {display_bots} ｜ 话题 {display_topic} ｜ 自动删消息 {display_auto_delete} ｜ 观星监控 {display_guanxing_monitor}"


def _write_response(writer, status_line, body, *, content_type, extra_headers=None):
    body_bytes = body if isinstance(body, bytes) else str(body).encode("utf-8")
    headers = [
        status_line,
        f"Content-Type: {content_type}",
        f"Content-Length: {len(body_bytes)}",
        "Connection: close",
        "Cache-Control: no-store",
    ]
    headers.extend(extra_headers or [])
    headers.extend(["", ""])
    writer.write("\r\n".join(headers).encode("utf-8") + body_bytes)


async def handle_ui_http(reader, writer):
    peer = writer.get_extra_info("peername")
    method = ""
    path = ""
    try:
        try:
            request_head = await reader.readuntil(b"\r\n\r\n")
        except asyncio.IncompleteReadError as e:
            request_head = e.partial
        except Exception:
            try:
                writer.close()
                await writer.wait_closed()
            except (ConnectionResetError, BrokenPipeError, OSError):
                pass
            return

        header_text = request_head.decode("utf-8", errors="ignore")
        request_lines = header_text.split("\r\n")
        request_line = request_lines[0] if request_lines else ""
        parts = request_line.split()
        if len(parts) < 2:
            writer.close()
            await writer.wait_closed()
            return

        method, raw_target = parts[0].upper(), parts[1]
        parsed = urlsplit(raw_target)
        path = parsed.path or "/"
        query = parse_qs(parsed.query, keep_blank_values=False)
        headers = {}
        for line in request_lines[1:]:
            if not line or ":" not in line:
                continue
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()

        content_length = 0
        try:
            content_length = max(0, int(headers.get("content-length", "0") or 0))
        except (TypeError, ValueError):
            content_length = 0
        body_bytes = b""
        if content_length > 0:
            try:
                body_bytes = await reader.readexactly(content_length)
            except asyncio.IncompleteReadError as e:
                body_bytes = e.partial

        payload = _parse_request_body(headers, body_bytes)
        now = time.time()

        if path == "/api/login/exchange":
            if method != "POST":
                _write_response(writer, "HTTP/1.1 405 Method Not Allowed", "Method Not Allowed", content_type="text/plain; charset=utf-8")
            else:
                login_token = (payload.get("token") or "").strip()
                if not login_token:
                    body = _make_json_payload(False, error="缺少 token")
                    _write_response(writer, "HTTP/1.1 400 Bad Request", body, content_type="application/json; charset=utf-8")
                else:
                    session_token = redeem_ui_login_token(login_token, now)
                    if not session_token:
                        body = _make_json_payload(False, error="登录 token 无效或已失效，请重新在日志群发送 .登录")
                        _write_response(
                            writer,
                            "HTTP/1.1 401 Unauthorized",
                            body,
                            content_type="application/json; charset=utf-8",
                            extra_headers=[f"Set-Cookie: {_build_session_cookie_header('', clear=True)}"],
                        )
                    else:
                        body = _make_json_payload(True, message="登录成功")
                        _write_response(
                            writer,
                            "HTTP/1.1 200 OK",
                            body,
                            content_type="application/json; charset=utf-8",
                            extra_headers=[f"Set-Cookie: {_build_session_cookie_header(session_token)}"],
                        )
        else:
            session, session_cookie_header = _get_authenticated_session(headers, now)
            auth_headers = [f"Set-Cookie: {session_cookie_header}"] if session_cookie_header else []

            # 初始化模式：无账号且无身份时跳过认证，允许直接使用 UI
            _setup_mode = not get_accounts() and not get_identity_ids()
            if _setup_mode and session is None:
                session = {"session_token": "__setup__", "sender_id": 0, "created_at": now, "last_active_at": now}

            if path == "/favicon.png":
                if method != "GET":
                    _write_response(writer, "HTTP/1.1 405 Method Not Allowed", "Method Not Allowed", content_type="text/plain; charset=utf-8")
                else:
                    with open(UI_FAVICON_PNG_PATH, "rb") as favicon_fp:
                        _write_response(writer, "HTTP/1.1 200 OK", favicon_fp.read(), content_type="image/png")
            elif path.startswith("/static/"):
                if method != "GET":
                    _write_response(writer, "HTTP/1.1 405 Method Not Allowed", "Method Not Allowed", content_type="text/plain; charset=utf-8")
                else:
                    asset_body, asset_content_type = _load_ui_static_asset(path[len("/static/"):])
                    if asset_body is None:
                        _write_response(writer, "HTTP/1.1 404 Not Found", "Not Found", content_type="text/plain; charset=utf-8")
                    else:
                        _write_response(writer, "HTTP/1.1 200 OK", asset_body, content_type=asset_content_type)
            elif path == "/":
                if method != "GET":
                    _write_response(writer, "HTTP/1.1 405 Method Not Allowed", "Method Not Allowed", content_type="text/plain; charset=utf-8")
                elif session is None:
                    message = "登录已失效，请重新在日志群发送 .登录" if session_cookie_header else ""
                    _write_response(
                        writer,
                        "HTTP/1.1 200 OK",
                        _render_login_page(message),
                        content_type="text/html; charset=utf-8",
                        extra_headers=auth_headers,
                    )
                else:
                    selected_send_as_id = query.get("send_as_id", [""])[0]
                    _write_response(
                        writer,
                        "HTTP/1.1 200 OK",
                        render_ui_page(selected_send_as_id=selected_send_as_id, session_token=(session or {}).get("session_token")),
                        content_type="text/html; charset=utf-8",
                        extra_headers=auth_headers,
                    )
            elif path == "/api/state":
                if session is None:
                    body = _make_json_payload(False, error="未登录或登录已失效")
                    _write_response(
                        writer,
                        "HTTP/1.1 401 Unauthorized",
                        body,
                        content_type="application/json; charset=utf-8",
                        extra_headers=auth_headers,
                    )
                elif method != "GET":
                    _write_response(writer, "HTTP/1.1 405 Method Not Allowed", "Method Not Allowed", content_type="text/plain; charset=utf-8")
                else:
                    body = _make_json_payload(True, snapshot=get_ui_snapshot(session_token=(session or {}).get("session_token")))
                    _write_response(
                        writer,
                        "HTTP/1.1 200 OK",
                        body,
                        content_type="application/json; charset=utf-8",
                        extra_headers=auth_headers,
                    )
            elif path == "/api/basic-config":
                if session is None:
                    body = _make_json_payload(False, error="未登录或登录已失效")
                    _write_response(
                        writer,
                        "HTTP/1.1 401 Unauthorized",
                        body,
                        content_type="application/json; charset=utf-8",
                        extra_headers=auth_headers,
                    )
                elif method != "POST":
                    _write_response(writer, "HTTP/1.1 405 Method Not Allowed", "Method Not Allowed", content_type="text/plain; charset=utf-8")
                else:
                    ok, message = await ui_set_basic_config(payload.get("game_group_id"), payload.get("game_bot_ids"), payload.get("game_topic_id"), payload.get("auto_delete_sent_messages"), payload.get("guanxing_monitor_enabled"), actor_id=(session or {}).get("sender_id"))
                    status_line = "HTTP/1.1 200 OK" if ok else "HTTP/1.1 400 Bad Request"
                    body = _make_json_payload(ok, message=message if ok else "", error="" if ok else message, snapshot=get_ui_snapshot(session_token=(session or {}).get("session_token")) if ok else None)
                    _write_response(writer, status_line, body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
            elif path == "/api/forum-topics":
                if session is None:
                    body = _make_json_payload(False, error="未登录或登录已失效")
                    _write_response(
                        writer,
                        "HTTP/1.1 401 Unauthorized",
                        body,
                        content_type="application/json; charset=utf-8",
                        extra_headers=auth_headers,
                    )
                elif method != "POST":
                    _write_response(writer, "HTTP/1.1 405 Method Not Allowed", "Method Not Allowed", content_type="text/plain; charset=utf-8")
                else:
                    ok, message, topics = await ui_refresh_forum_topics(payload.get("game_group_id"), actor_id=(session or {}).get("sender_id"))
                    status_line = "HTTP/1.1 200 OK" if ok else "HTTP/1.1 400 Bad Request"
                    body = _make_json_payload(
                        ok,
                        message=message if ok else "",
                        error="" if ok else message,
                        snapshot=get_ui_snapshot(session_token=(session or {}).get("session_token")) if ok else None,
                        extra={"forum_topics": topics, "forum_topics_updated_at": fmt_abs_ts(get_forum_topics_updated_at())} if ok else None,
                    )
                    _write_response(writer, status_line, body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
            elif path == "/api/account/send-as-peers":
                if session is None:
                    body = _make_json_payload(False, error="未登录或登录已失效")
                    _write_response(writer, "HTTP/1.1 401 Unauthorized", body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
                elif method != "POST":
                    _write_response(writer, "HTTP/1.1 405 Method Not Allowed", "Method Not Allowed", content_type="text/plain; charset=utf-8")
                else:
                    raw_account_id = payload.get("account_id")
                    if not raw_account_id:
                        body = _make_json_payload(False, error="缺少 account_id 参数")
                        _write_response(writer, "HTTP/1.1 400 Bad Request", body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
                    else:
                        try:
                            result = await ui_get_send_as_peers(raw_account_id)
                            if len(result) == 4:
                                ok, message, peers, existing_ids = result
                            else:
                                ok, message, peers = result
                                existing_ids = []
                            status_line = "HTTP/1.1 200 OK" if ok else "HTTP/1.1 400 Bad Request"
                            body = _make_json_payload(
                                ok,
                                message=message if ok else "",
                                error="" if ok else message,
                                extra={"peers": peers, "existing_ids": existing_ids} if ok else None,
                            )
                        except Exception as e:
                            body = _make_json_payload(False, error=f"获取失败: {e}")
                            status_line = "HTTP/1.1 500 Internal Server Error"
                        _write_response(writer, status_line, body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
            elif path == "/api/identity":
                if session is None:
                    body = _make_json_payload(False, error="未登录或登录已失效")
                    _write_response(
                        writer,
                        "HTTP/1.1 401 Unauthorized",
                        body,
                        content_type="application/json; charset=utf-8",
                        extra_headers=auth_headers,
                    )
                elif method != "POST":
                    _write_response(writer, "HTTP/1.1 405 Method Not Allowed", "Method Not Allowed", content_type="text/plain; charset=utf-8")
                else:
                    send_as_id_raw = payload.get("send_as_id")
                    send_as_ids_raw = payload.get("send_as_ids")
                    batch_account_id = payload.get("account_id")
                    actor_id = (session or {}).get("sender_id")
                    if send_as_ids_raw and isinstance(send_as_ids_raw, list):
                        # 批量添加
                        results = []
                        last_canonical_id = None
                        for raw_id in send_as_ids_raw:
                            ok, msg, cid = await ui_add_identity(raw_id, actor_id=actor_id, account_id=batch_account_id)
                            results.append({"id": raw_id, "ok": ok, "message": msg, "canonical_id": cid})
                            if ok and cid:
                                last_canonical_id = cid
                        success_count = sum(1 for r in results if r["ok"])
                        fail_count = len(results) - success_count
                        message = f"批量添加完成：成功 {success_count} 个"
                        if fail_count > 0:
                            message += f"，失败 {fail_count} 个"
                        body = _make_json_payload(
                            True,
                            message=message,
                            snapshot=get_ui_snapshot(session_token=(session or {}).get("session_token")),
                            extra={"send_as_id": last_canonical_id, "results": results},
                        )
                        _write_response(writer, "HTTP/1.1 200 OK", body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
                    elif send_as_id_raw in {None, ""}:
                        body = _make_json_payload(False, error="缺少 send_as_id 参数")
                        _write_response(
                            writer,
                            "HTTP/1.1 400 Bad Request",
                            body,
                            content_type="application/json; charset=utf-8",
                            extra_headers=auth_headers,
                        )
                    else:
                        ok, message, canonical_id = await ui_add_identity(send_as_id_raw, actor_id=(session or {}).get("sender_id"), account_id=batch_account_id)
                        status_line = "HTTP/1.1 200 OK" if ok else "HTTP/1.1 400 Bad Request"
                        body = _make_json_payload(
                            ok,
                            message=message if ok else "",
                            error="" if ok else message,
                            snapshot=get_ui_snapshot(session_token=(session or {}).get("session_token")) if ok else None,
                            extra={"send_as_id": canonical_id} if canonical_id is not None else None,
                        )
                        _write_response(writer, status_line, body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
            elif path == "/api/identity-refresh":
                if session is None:
                    body = _make_json_payload(False, error="未登录或登录已失效")
                    _write_response(
                        writer,
                        "HTTP/1.1 401 Unauthorized",
                        body,
                        content_type="application/json; charset=utf-8",
                        extra_headers=auth_headers,
                    )
                elif method != "POST":
                    _write_response(writer, "HTTP/1.1 405 Method Not Allowed", "Method Not Allowed", content_type="text/plain; charset=utf-8")
                else:
                    send_as_id = payload.get("send_as_id")
                    if send_as_id in {None, ""}:
                        body = _make_json_payload(False, error="缺少 send_as_id 参数")
                        _write_response(
                            writer,
                            "HTTP/1.1 400 Bad Request",
                            body,
                            content_type="application/json; charset=utf-8",
                            extra_headers=auth_headers,
                        )
                    else:
                        ok, message = await ui_refresh_identity_info(send_as_id, actor_id=(session or {}).get("sender_id"))
                        status_line = "HTTP/1.1 200 OK" if ok else "HTTP/1.1 400 Bad Request"
                        body = _make_json_payload(ok, message=message if ok else "", error="" if ok else message, snapshot=get_ui_snapshot(session_token=(session or {}).get("session_token")) if ok else None)
                        _write_response(writer, status_line, body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
            elif path == "/api/identity-delete":
                if session is None:
                    body = _make_json_payload(False, error="未登录或登录已失效")
                    _write_response(
                        writer,
                        "HTTP/1.1 401 Unauthorized",
                        body,
                        content_type="application/json; charset=utf-8",
                        extra_headers=auth_headers,
                    )
                elif method != "POST":
                    _write_response(writer, "HTTP/1.1 405 Method Not Allowed", "Method Not Allowed", content_type="text/plain; charset=utf-8")
                else:
                    send_as_id = payload.get("send_as_id")
                    if send_as_id in {None, ""}:
                        body = _make_json_payload(False, error="缺少 send_as_id 参数")
                        _write_response(
                            writer,
                            "HTTP/1.1 400 Bad Request",
                            body,
                            content_type="application/json; charset=utf-8",
                            extra_headers=auth_headers,
                        )
                    else:
                        ok, message = await ui_delete_identity(send_as_id, actor_id=(session or {}).get("sender_id"))
                        status_line = "HTTP/1.1 200 OK" if ok else "HTTP/1.1 400 Bad Request"
                        body = _make_json_payload(ok, message=message if ok else "", error="" if ok else message, snapshot=get_ui_snapshot(session_token=(session or {}).get("session_token")) if ok else None)
                        _write_response(writer, status_line, body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
            elif path == "/api/global-enabled":
                if session is None:
                    body = _make_json_payload(False, error="未登录或登录已失效")
                    _write_response(
                        writer,
                        "HTTP/1.1 401 Unauthorized",
                        body,
                        content_type="application/json; charset=utf-8",
                        extra_headers=auth_headers,
                    )
                elif method != "POST":
                    _write_response(writer, "HTTP/1.1 405 Method Not Allowed", "Method Not Allowed", content_type="text/plain; charset=utf-8")
                else:
                    enabled = bool(payload.get("enabled"))
                    ok, message = await toggle_global_enabled(enabled, source="ui", actor_id=(session or {}).get("sender_id"))
                    status_line = "HTTP/1.1 200 OK" if ok else "HTTP/1.1 400 Bad Request"
                    body = _make_json_payload(ok, message=message if ok else "", error="" if ok else message, snapshot=get_ui_snapshot(session_token=(session or {}).get("session_token")) if ok else None)
                    _write_response(writer, status_line, body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
            elif path == "/api/identity-enabled":
                if session is None:
                    body = _make_json_payload(False, error="未登录或登录已失效")
                    _write_response(
                        writer,
                        "HTTP/1.1 401 Unauthorized",
                        body,
                        content_type="application/json; charset=utf-8",
                        extra_headers=auth_headers,
                    )
                elif method != "POST":
                    _write_response(writer, "HTTP/1.1 405 Method Not Allowed", "Method Not Allowed", content_type="text/plain; charset=utf-8")
                else:
                    send_as_id = payload.get("send_as_id")
                    enabled = bool(payload.get("enabled"))
                    if send_as_id in {None, ""}:
                        body = _make_json_payload(False, error="缺少 send_as_id 参数")
                        _write_response(
                            writer,
                            "HTTP/1.1 400 Bad Request",
                            body,
                            content_type="application/json; charset=utf-8",
                            extra_headers=auth_headers,
                        )
                    else:
                        ok, message = await ui_set_identity_enabled(send_as_id, enabled, actor_id=(session or {}).get("sender_id"))
                        status_line = "HTTP/1.1 200 OK" if ok else "HTTP/1.1 400 Bad Request"
                        body = _make_json_payload(ok, message=message if ok else "", error="" if ok else message, snapshot=get_ui_snapshot(session_token=(session or {}).get("session_token")) if ok else None)
                        _write_response(writer, status_line, body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
            elif path == "/api/toggle":
                if session is None:
                    body = _make_json_payload(False, error="未登录或登录已失效")
                    _write_response(
                        writer,
                        "HTTP/1.1 401 Unauthorized",
                        body,
                        content_type="application/json; charset=utf-8",
                        extra_headers=auth_headers,
                    )
                elif method != "POST":
                    _write_response(writer, "HTTP/1.1 405 Method Not Allowed", "Method Not Allowed", content_type="text/plain; charset=utf-8")
                else:
                    send_as_id = payload.get("send_as_id")
                    module_name = payload.get("module")
                    enabled = bool(payload.get("enabled"))
                    if send_as_id in {None, ""} or not module_name:
                        body = _make_json_payload(False, error="缺少 send_as_id 或 module 参数")
                        _write_response(
                            writer,
                            "HTTP/1.1 400 Bad Request",
                            body,
                            content_type="application/json; charset=utf-8",
                            extra_headers=auth_headers,
                        )
                    else:
                        ok, message = await ui_set_module_enabled(send_as_id, module_name, enabled)
                        status_line = "HTTP/1.1 200 OK" if ok else "HTTP/1.1 400 Bad Request"
                        body = _make_json_payload(ok, message=message if ok else "", error="" if ok else message, snapshot=get_ui_snapshot(session_token=(session or {}).get("session_token")) if ok else None)
                        _write_response(writer, status_line, body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
            elif path == "/api/jiyin-choice":
                if session is None:
                    body = _make_json_payload(False, error="未登录或登录已失效")
                    _write_response(
                        writer,
                        "HTTP/1.1 401 Unauthorized",
                        body,
                        content_type="application/json; charset=utf-8",
                        extra_headers=auth_headers,
                    )
                elif method != "POST":
                    _write_response(writer, "HTTP/1.1 405 Method Not Allowed", "Method Not Allowed", content_type="text/plain; charset=utf-8")
                else:
                    send_as_id = payload.get("send_as_id")
                    choice = payload.get("choice")
                    if send_as_id in {None, ""} or not choice:
                        body = _make_json_payload(False, error="缺少 send_as_id 或 choice 参数")
                        _write_response(
                            writer,
                            "HTTP/1.1 400 Bad Request",
                            body,
                            content_type="application/json; charset=utf-8",
                            extra_headers=auth_headers,
                        )
                    else:
                        ok, message = await ui_set_jiyin_choice(send_as_id, choice)
                        status_line = "HTTP/1.1 200 OK" if ok else "HTTP/1.1 400 Bad Request"
                        body = _make_json_payload(ok, message=message if ok else "", error="" if ok else message, snapshot=get_ui_snapshot(session_token=(session or {}).get("session_token")) if ok else None)
                        _write_response(writer, status_line, body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
            elif path == "/api/pet-name":
                if session is None:
                    body = _make_json_payload(False, error="未登录或登录已失效")
                    _write_response(
                        writer,
                        "HTTP/1.1 401 Unauthorized",
                        body,
                        content_type="application/json; charset=utf-8",
                        extra_headers=auth_headers,
                    )
                elif method != "POST":
                    _write_response(writer, "HTTP/1.1 405 Method Not Allowed", "Method Not Allowed", content_type="text/plain; charset=utf-8")
                else:
                    send_as_id = payload.get("send_as_id")
                    pet_name = payload.get("pet_name")
                    if send_as_id in {None, ""}:
                        body = _make_json_payload(False, error="缺少 send_as_id 参数")
                        _write_response(
                            writer,
                            "HTTP/1.1 400 Bad Request",
                            body,
                            content_type="application/json; charset=utf-8",
                            extra_headers=auth_headers,
                        )
                    else:
                        ok, message = await ui_set_pet_name(send_as_id, pet_name)
                        status_line = "HTTP/1.1 200 OK" if ok else "HTTP/1.1 400 Bad Request"
                        body = _make_json_payload(ok, message=message if ok else "", error="" if ok else message, snapshot=get_ui_snapshot(session_token=(session or {}).get("session_token")) if ok else None)
                        _write_response(writer, status_line, body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
            elif path == "/api/stargazer-star-choice":
                if session is None:
                    body = _make_json_payload(False, error="未登录或登录已失效")
                    _write_response(
                        writer,
                        "HTTP/1.1 401 Unauthorized",
                        body,
                        content_type="application/json; charset=utf-8",
                        extra_headers=auth_headers,
                    )
                elif method != "POST":
                    _write_response(writer, "HTTP/1.1 405 Method Not Allowed", "Method Not Allowed", content_type="text/plain; charset=utf-8")
                else:
                    send_as_id = payload.get("send_as_id")
                    choice = payload.get("choice")
                    if send_as_id in {None, ""} or not choice:
                        body = _make_json_payload(False, error="缺少 send_as_id 或 choice 参数")
                        _write_response(
                            writer,
                            "HTTP/1.1 400 Bad Request",
                            body,
                            content_type="application/json; charset=utf-8",
                            extra_headers=auth_headers,
                        )
                    else:
                        ok, message = await ui_set_stargazer_star_choice(send_as_id, choice)
                        status_line = "HTTP/1.1 200 OK" if ok else "HTTP/1.1 400 Bad Request"
                        body = _make_json_payload(ok, message=message if ok else "", error="" if ok else message, snapshot=get_ui_snapshot(session_token=(session or {}).get("session_token")) if ok else None)
                        _write_response(writer, status_line, body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
            elif path == "/api/tianti-rank-choice":
                if session is None:
                    body = _make_json_payload(False, error="未登录或登录已失效")
                    _write_response(
                        writer,
                        "HTTP/1.1 401 Unauthorized",
                        body,
                        content_type="application/json; charset=utf-8",
                        extra_headers=auth_headers,
                    )
                elif method != "POST":
                    _write_response(writer, "HTTP/1.1 405 Method Not Allowed", "Method Not Allowed", content_type="text/plain; charset=utf-8")
                else:
                    send_as_id = payload.get("send_as_id")
                    choice = payload.get("choice")
                    if send_as_id in {None, ""} or not choice:
                        body = _make_json_payload(False, error="缺少 send_as_id 或 choice 参数")
                        _write_response(
                            writer,
                            "HTTP/1.1 400 Bad Request",
                            body,
                            content_type="application/json; charset=utf-8",
                            extra_headers=auth_headers,
                        )
                    else:
                        ok, message = await ui_set_tianti_rank_choice(send_as_id, choice)
                        status_line = "HTTP/1.1 200 OK" if ok else "HTTP/1.1 400 Bad Request"
                        body = _make_json_payload(ok, message=message if ok else "", error="" if ok else message, snapshot=get_ui_snapshot(session_token=(session or {}).get("session_token")) if ok else None)
                        _write_response(writer, status_line, body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
            elif path == "/api/stargazer-sync":
                if session is None:
                    body = _make_json_payload(False, error="未登录或登录已失效")
                    _write_response(
                        writer,
                        "HTTP/1.1 401 Unauthorized",
                        body,
                        content_type="application/json; charset=utf-8",
                        extra_headers=auth_headers,
                    )
                elif method != "POST":
                    _write_response(writer, "HTTP/1.1 405 Method Not Allowed", "Method Not Allowed", content_type="text/plain; charset=utf-8")
                else:
                    send_as_id = payload.get("send_as_id")
                    if send_as_id in {None, ""}:
                        body = _make_json_payload(False, error="缺少 send_as_id 参数")
                        _write_response(
                            writer,
                            "HTTP/1.1 400 Bad Request",
                            body,
                            content_type="application/json; charset=utf-8",
                            extra_headers=auth_headers,
                        )
                    else:
                        ok, message = await ui_sync_stargazer_total_slots(send_as_id)
                        status_line = "HTTP/1.1 200 OK" if ok else "HTTP/1.1 400 Bad Request"
                        body = _make_json_payload(ok, message=message if ok else "", error="" if ok else message, snapshot=get_ui_snapshot(session_token=(session or {}).get("session_token")) if ok else None)
                        _write_response(writer, status_line, body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
            elif path == "/api/tianti-sync":
                if session is None:
                    body = _make_json_payload(False, error="未登录或登录已失效")
                    _write_response(
                        writer,
                        "HTTP/1.1 401 Unauthorized",
                        body,
                        content_type="application/json; charset=utf-8",
                        extra_headers=auth_headers,
                    )
                elif method != "POST":
                    _write_response(writer, "HTTP/1.1 405 Method Not Allowed", "Method Not Allowed", content_type="text/plain; charset=utf-8")
                else:
                    send_as_id = payload.get("send_as_id")
                    if send_as_id in {None, ""}:
                        body = _make_json_payload(False, error="缺少 send_as_id 参数")
                        _write_response(
                            writer,
                            "HTTP/1.1 400 Bad Request",
                            body,
                            content_type="application/json; charset=utf-8",
                            extra_headers=auth_headers,
                        )
                    else:
                        ok, message = await ui_sync_tianti_status(send_as_id)
                        status_line = "HTTP/1.1 200 OK" if ok else "HTTP/1.1 400 Bad Request"
                        body = _make_json_payload(ok, message=message if ok else "", error="" if ok else message, snapshot=get_ui_snapshot(session_token=(session or {}).get("session_token")) if ok else None)
                        _write_response(writer, status_line, body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
            elif path == "/api/module-window":
                if session is None:
                    body = _make_json_payload(False, error="未登录或登录已失效")
                    _write_response(
                        writer,
                        "HTTP/1.1 401 Unauthorized",
                        body,
                        content_type="application/json; charset=utf-8",
                        extra_headers=auth_headers,
                    )
                elif method != "POST":
                    _write_response(writer, "HTTP/1.1 405 Method Not Allowed", "Method Not Allowed", content_type="text/plain; charset=utf-8")
                else:
                    send_as_id = payload.get("send_as_id")
                    module_name = payload.get("module")
                    start_hour_local = payload.get("start_hour_local")
                    end_hour_local = payload.get("end_hour_local")
                    if send_as_id in {None, ""} or not module_name:
                        body = _make_json_payload(False, error="缺少 send_as_id 或 module 参数")
                        _write_response(
                            writer,
                            "HTTP/1.1 400 Bad Request",
                            body,
                            content_type="application/json; charset=utf-8",
                            extra_headers=auth_headers,
                        )
                    else:
                        ok, message = await ui_set_module_window(send_as_id, module_name, start_hour_local, end_hour_local)
                        status_line = "HTTP/1.1 200 OK" if ok else "HTTP/1.1 400 Bad Request"
                        body = _make_json_payload(ok, message=message if ok else "", error="" if ok else message, snapshot=get_ui_snapshot(session_token=(session or {}).get("session_token")) if ok else None)
                        _write_response(writer, status_line, body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
            elif path == "/api/account/login-start":
                if session is None:
                    body = _make_json_payload(False, error="未登录或登录已失效")
                    _write_response(writer, "HTTP/1.1 401 Unauthorized", body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
                elif method != "POST":
                    _write_response(writer, "HTTP/1.1 405 Method Not Allowed", "Method Not Allowed", content_type="text/plain; charset=utf-8")
                else:
                    phone = payload.get("phone", "")
                    session_key = (session or {}).get("session_token", "")
                    ok, message, _extra = await ui_account_login_start(phone, session_key)
                    status_line = "HTTP/1.1 200 OK" if ok else "HTTP/1.1 400 Bad Request"
                    body = _make_json_payload(ok, message=message if ok else "", error="" if ok else message)
                    _write_response(writer, status_line, body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
            elif path == "/api/account/login-qr-start":
                if session is None:
                    body = _make_json_payload(False, error="未登录或登录已失效")
                    _write_response(writer, "HTTP/1.1 401 Unauthorized", body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
                elif method != "POST":
                    _write_response(writer, "HTTP/1.1 405 Method Not Allowed", "Method Not Allowed", content_type="text/plain; charset=utf-8")
                else:
                    session_key = (session or {}).get("session_token", "")
                    ok, message, qr_info = await ui_account_login_qr_start(session_key)
                    status_line = "HTTP/1.1 200 OK" if ok else "HTTP/1.1 400 Bad Request"
                    body = _make_json_payload(ok, message=message if ok else "", error="" if ok else message, extra=qr_info if ok else None)
                    _write_response(writer, status_line, body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
            elif path == "/api/account/login-qr-status":
                if session is None:
                    body = _make_json_payload(False, error="未登录或登录已失效")
                    _write_response(writer, "HTTP/1.1 401 Unauthorized", body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
                elif method != "GET":
                    _write_response(writer, "HTTP/1.1 405 Method Not Allowed", "Method Not Allowed", content_type="text/plain; charset=utf-8")
                else:
                    session_key = (session or {}).get("session_token", "")
                    qr_status = ui_account_login_qr_status(session_key)
                    extra = dict(qr_status)
                    status = str(extra.get("status") or "")
                    message = extra.pop("message", "")
                    account_id = int(extra.get("account_id", 0) or 0)
                    snapshot = get_ui_snapshot(session_token=(session or {}).get("session_token")) if status == "done" and account_id > 0 else None
                    body = _make_json_payload(True, message=message, snapshot=snapshot, extra=extra)
                    _write_response(writer, "HTTP/1.1 200 OK", body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
            elif path == "/api/account/login-cancel":
                if session is None:
                    body = _make_json_payload(False, error="未登录或登录已失效")
                    _write_response(writer, "HTTP/1.1 401 Unauthorized", body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
                elif method != "POST":
                    _write_response(writer, "HTTP/1.1 405 Method Not Allowed", "Method Not Allowed", content_type="text/plain; charset=utf-8")
                else:
                    session_key = (session or {}).get("session_token", "")
                    ok, message = await ui_account_login_cancel(session_key)
                    status_line = "HTTP/1.1 200 OK" if ok else "HTTP/1.1 400 Bad Request"
                    body = _make_json_payload(ok, message=message if ok else "", error="" if ok else message)
                    _write_response(writer, status_line, body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
            elif path == "/api/account/login-verify":
                if session is None:
                    body = _make_json_payload(False, error="未登录或登录已失效")
                    _write_response(writer, "HTTP/1.1 401 Unauthorized", body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
                elif method != "POST":
                    _write_response(writer, "HTTP/1.1 405 Method Not Allowed", "Method Not Allowed", content_type="text/plain; charset=utf-8")
                else:
                    code = payload.get("code", "")
                    password = payload.get("password")
                    session_key = (session or {}).get("session_token", "")
                    ok, message, account_id = await ui_account_login_verify(code, session_key, password=password)
                    if not ok and message == "need_2fa":
                        body = _make_json_payload(False, error="need_2fa")
                        _write_response(writer, "HTTP/1.1 200 OK", body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
                    else:
                        status_line = "HTTP/1.1 200 OK" if ok else "HTTP/1.1 400 Bad Request"
                        body = _make_json_payload(
                            ok,
                            message=message if ok else "",
                            error="" if ok else message,
                            snapshot=get_ui_snapshot(session_token=(session or {}).get("session_token")) if ok else None,
                            extra={"account_id": account_id} if account_id else None,
                        )
                        _write_response(writer, status_line, body, content_type="application/json; charset=utf-8", extra_headers=auth_headers)
            else:
                _write_response(writer, "HTTP/1.1 404 Not Found", "Not Found", content_type="text/plain; charset=utf-8")
    except (ConnectionResetError, BrokenPipeError, OSError):
        pass
    except Exception as e:
        traceback.print_exc()
        try:
            _write_response(writer, "HTTP/1.1 500 Internal Server Error", f"Internal Server Error\n{e}\n", content_type="text/plain; charset=utf-8")
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass
    finally:
        try:
            await writer.drain()
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass
        try:
            writer.close()
            await writer.wait_closed()
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass
        if peer:
            print(f"[{datetime.now(TZ_LOCAL).strftime('%Y-%m-%d %H:%M:%S')}] ui request: {peer} {method or '-'} {path or '-'}")


async def start_ui_server():
    global _ui_server
    if _ui_server is not None:
        return _ui_server
    _ui_server = await asyncio.start_server(handle_ui_http, UI_HOST, UI_PORT)
    sockets = _ui_server.sockets or []
    bind_text = ", ".join(str(sock.getsockname()) for sock in sockets) or f"{UI_HOST}:{UI_PORT}"
    await send_audit_log(f"🖥️ UI 已启动：{bind_text}", scope="global")
    return _ui_server


__all__ = [
    "get_identity_ui_snapshot",
    "get_ui_snapshot",
    "handle_ui_http",
    "html_escape",
    "render_ui_page",
    "start_ui_server",
    "ui_add_identity",
    "ui_refresh_forum_topics",
    "ui_refresh_identity_info",
    "ui_set_basic_config",
    "ui_set_identity_enabled",
    "ui_set_module_enabled",
    "ui_set_jiyin_choice",
    "ui_set_module_window",
    "ui_set_pet_name",
    "ui_set_stargazer_star_choice",
    "ui_sync_stargazer_total_slots",
    "ui_sync_tianti_status",
]
