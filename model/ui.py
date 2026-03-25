import asyncio
import html
import json
import time
import traceback
from datetime import datetime
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlsplit

from .config import (
    API_HASH,
    API_ID,
    MODULE_KEY_MAP,
    TZ_LOCAL,
    UI_AUTH_COOKIE_NAME,
    UI_AUTH_IDLE_TIMEOUT_SEC,
    UI_AUTO_REFRESH_SEC,
    UI_HOST,
    UI_PORT,
    UI_PUBLIC_BASE_URL,
    create_account_client,
    register_client,
)
from .control import (
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
    is_auto_delete_sent_messages_enabled,
    get_identity_display_name,
    get_identity_enabled,
    get_identity_ids,
    get_identity_ui_display_name,
    get_identity_state,
    get_module_window_hours_local,
    get_realm_sort_key,
    get_send_as_profile,
    set_account,
    set_auto_delete_sent_messages,
    set_forum_topics,
    set_game_bot_ids,
    set_game_group_id,
    set_game_topic_id,
    set_identity_account,
    set_pet_name,
    state,
    use_identity,
)
from .timing import fmt_abs_ts

_ui_server = None


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
        snapshot = {
            "send_as_id": send_as_id,
            "display_name": get_identity_ui_display_name(send_as_id),
            "identity_enabled": identity_enabled,
            "identity_status_text": "运行中" if identity_enabled else "已暂停",
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
        key=lambda identity: get_realm_sort_key(identity.get("realm"), identity.get("send_as_id"), xiuwei_max=identity.get("xiuwei_max", 0)),
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
        "auth_idle_timeout_sec": UI_AUTH_IDLE_TIMEOUT_SEC,
        "refresh_interval_sec": UI_AUTO_REFRESH_SEC,
        "startup_alerts": startup_alerts,
        "accounts": get_accounts(),
        "identities": identities,
    }


def html_escape(value):
    return html.escape(str(value or ""), quote=True)


def html_pre(value):
    return f"<pre>{html_escape(value)}</pre>"


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


def build_toggle_query(send_as_id, module_name, enabled):
    return {
        "send_as_id": int(send_as_id),
        "module": module_name,
        "enabled": bool(enabled),
    }


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
        morsel["max-age"] = int(UI_AUTH_IDLE_TIMEOUT_SEC)
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
    timeout_minutes = max(1, UI_AUTH_IDLE_TIMEOUT_SEC // 60)
    secure_note = "HTTPS 公网地址下会自动使用 Secure Cookie。" if _cookie_is_secure() else "如需 Secure Cookie，请将 UI_PUBLIC_BASE_URL 配置为 https 地址。"
    return (
        "<!doctype html>"
        "<html lang='zh-CN'><head><meta charset='utf-8'>"
        "<title>Xiuxian 登录</title>"
        "<style>"
        "body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0b1220;color:#e5e7eb;margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px;box-sizing:border-box;}"
        "a{color:#93c5fd;text-decoration:none;}"
        ".panel{width:min(560px,100%);background:#111827;border:1px solid #1f2937;border-radius:20px;padding:28px;box-shadow:0 20px 60px rgba(0,0,0,.35);}"
        "h1{margin:0 0 10px;font-size:28px;}"
        "p,li{color:#cbd5e1;line-height:1.65;}"
        ".muted{color:#94a3b8;font-size:14px;}"
        ".flash{background:#1d4ed8;color:#eff6ff;padding:10px 14px;border-radius:10px;margin:16px 0;}"
        ".status{margin-top:18px;padding:12px 14px;border-radius:12px;background:#0f172a;border:1px solid #334155;color:#cbd5e1;white-space:pre-wrap;}"
        "code{background:#020617;border:1px solid #1e293b;border-radius:8px;padding:2px 6px;color:#e2e8f0;}"
        "</style></head><body>"
        "<main class='panel'>"
        "<h1>Xiuxian 控制台登录</h1>"
        "<div class='muted'>浏览器访问地址：" + html_escape(UI_PUBLIC_BASE_URL) + "</div>"
        f"{message_html}"
        "<p>请先到日志群发送 <code>.登录</code>，再在浏览器里打开机器人回复的登录链接。</p>"
        "<ul>"
        f"<li>登录链接和登录后的会话都会在 {timeout_minutes} 分钟无请求后自动失效。</li>"
        "<li>支持多个登录链接与多个浏览器会话同时存在。</li>"
        f"<li>{html_escape(secure_note)}</li>"
        "</ul>"
        "<div id='login-status' class='status'>等待登录链接…</div>"
        "</main>"
        "<script>"
        "async function exchangeLoginToken(token){"
        "  const status=document.getElementById('login-status');"
        "  status.textContent='检测到登录 token，正在交换会话…';"
        "  try {"
        "    const response=await fetch('/api/login/exchange',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:token}),credentials:'same-origin',cache:'no-store'});"
        "    let data={};"
        "    try{data=await response.json();}catch(_error){}"
        "    if(!response.ok||!data.ok){throw new Error(data.error||data.message||'登录失败');}"
        "    history.replaceState(null,'','/');"
        "    window.location.href='/';"
        "  } catch (error) {"
        "    status.textContent=(error&&error.message)?error.message:'登录失败，请重新在日志群发送 .登录';"
        "  }"
        "}"
        "document.addEventListener('DOMContentLoaded',function(){"
        "  const status=document.getElementById('login-status');"
        "  const hash=new URLSearchParams(window.location.hash.slice(1));"
        "  const token=hash.get('token');"
        "  if(token){exchangeLoginToken(token);return;}"
        "  status.textContent='未检测到登录 token。请在日志群发送 .登录，并打开机器人回复的链接。';"
        "});"
        "</script>"
        "</body></html>"
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
    return (
        "<!doctype html>"
        "<html lang='zh-CN'><head><meta charset='utf-8'>"
        "<title>Xiuxian 控制台</title>"
        "<style>"
        "body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0b1220;color:#e5e7eb;margin:0;padding:24px;box-sizing:border-box;}"
        "a{color:#93c5fd;text-decoration:none;}"
        "button,input{font:inherit;}"
        ".topbar{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:20px;flex-wrap:wrap;}"
        ".topbar-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap;}"
        ".flash{position:fixed;top:20px;right:20px;z-index:40;min-width:280px;max-width:min(420px,calc(100vw - 40px));padding:12px 14px;border-radius:12px;background:#1d4ed8;color:#eff6ff;border:1px solid rgba(191,219,254,.28);box-shadow:0 18px 40px rgba(15,23,42,.35);opacity:1;transform:translateY(0);transition:opacity .2s ease,transform .2s ease;}"
        ".flash.error{background:#991b1b;color:#fee2e2;border-color:rgba(254,202,202,.24);}"
        ".flash.hidden{opacity:0;transform:translateY(-8px);pointer-events:none;}"
        ".hidden{display:none !important;}"
        ".layout{display:grid;grid-template-columns:300px minmax(0,1fr);gap:16px;align-items:start;}"
        ".sidebar,.main{min-width:0;}"
        ".card{background:#111827;border:1px solid #1f2937;border-radius:16px;padding:18px;box-shadow:0 10px 30px rgba(0,0,0,.25);margin-bottom:16px;}"
        ".meta,.summary-strip{display:flex;flex-wrap:wrap;gap:12px;color:#9ca3af;font-size:13px;margin:8px 0 12px;}"
        ".xiuwei-bar-wrap{display:flex;align-items:center;gap:10px;margin:8px 0 4px;}"
        ".xiuwei-bar-label{font-size:13px;color:#9ca3af;white-space:nowrap;}"
        ".xiuwei-bar-track{position:relative;flex:1;height:22px;background:#1e293b;border-radius:11px;overflow:hidden;}"
        ".xiuwei-bar-fill{height:100%;background:linear-gradient(90deg,#22c55e,#4ade80);border-radius:11px;transition:width .4s ease;}"
        ".xiuwei-bar-text{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:12px;color:#e2e8f0;font-weight:600;text-shadow:0 1px 2px rgba(0,0,0,.5);}"
        ".module-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;}"
        ".module-card,.identity-item{background:#0f172a;border:1px solid #1e293b;border-radius:12px;padding:12px;}"
        ".module-card{display:flex;flex-direction:column;height:100%;box-sizing:border-box;}"
        ".module-top{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:10px;}"
        ".module-title{font-weight:700;color:#e5e7eb;}"
        ".module-note{margin-top:6px;color:#94a3b8;font-size:12px;}"
        ".summary-chip{display:inline-flex;align-items:center;gap:6px;padding:6px 10px;border-radius:999px;background:#0f172a;border:1px solid #1e293b;color:#cbd5e1;white-space:nowrap;}"
        ".summary-groups{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-top:14px;}"
        ".summary-group{background:#0f172a;border:1px solid #1e293b;border-radius:14px;padding:12px;}"
        ".summary-group-title{font-size:12px;color:#94a3b8;margin-bottom:10px;}"
        ".summary-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;}"
        ".summary-item{background:#020617;border:1px solid #1e293b;border-radius:10px;padding:10px 12px;}"
        ".summary-item-label{display:block;font-size:12px;color:#94a3b8;margin-bottom:4px;}"
        ".summary-item-value{display:block;color:#e5e7eb;font-size:13px;line-height:1.5;word-break:break-word;}"
        ".module-tools{display:flex;gap:8px;align-items:center;flex-wrap:wrap;justify-content:flex-end;}"
        ".btn{display:inline-flex;align-items:center;justify-content:center;background:#2563eb;color:#fff;padding:6px 12px;border-radius:8px;border:1px solid transparent;cursor:pointer;line-height:1.4;text-decoration:none;min-height:34px;box-sizing:border-box;}"
        ".btn-secondary{background:#1e293b;color:#cbd5e1;border-color:#334155;}"
        ".btn-refresh{background:#1e293b;color:#cbd5e1;border-color:#334155;}"
        ".switch{display:inline-flex;align-items:center;gap:8px;padding:6px 10px;border-radius:999px;border:1px solid #334155;background:#0b1220;color:#cbd5e1;cursor:pointer;}"
        ".switch-track{position:relative;width:42px;height:24px;border-radius:999px;background:#475569;flex-shrink:0;transition:background .2s ease;}"
        ".switch-thumb{position:absolute;top:3px;left:3px;width:18px;height:18px;border-radius:50%;background:#fff;transition:transform .2s ease;}"
        ".switch-on .switch-track{background:#22c55e;}"
        ".switch-on .switch-thumb{transform:translateX(18px);}"
        ".switch-off .switch-track{background:#475569;}"
        ".switch-text{font-size:12px;white-space:nowrap;}"
        ".identity-list{display:grid;gap:8px;}"
        ".identity-mobile-picker{display:none;gap:8px;margin-top:12px;}"
        ".sidebar-actions{display:flex;gap:8px;align-items:center;justify-content:space-between;margin-bottom:12px;flex-wrap:wrap;}"
        ".identity-item{display:flex;flex-direction:column;gap:4px;color:#cbd5e1;width:100%;text-align:left;cursor:pointer;background:#0f172a;padding:10px 12px;}"
        ".identity-item strong{font-size:14px;line-height:1.4;}"
        ".identity-item span{color:#94a3b8;font-size:12px;line-height:1.35;}"
        ".identity-item-active{border-color:#60a5fa;background:#111c33;}"
        ".summary-head{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;}"
        ".summary-head h2{margin:0;}"
        ".summary-head-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap;}"
        ".btn[disabled]{opacity:.65;cursor:not-allowed;}"
        ".modal-backdrop{display:none;position:fixed;inset:0;background:rgba(2,6,23,.75);padding:24px;align-items:center;justify-content:center;z-index:20;box-sizing:border-box;}"
        ".modal-backdrop.show{display:flex;}"
        ".modal-card{width:min(460px,100%);background:#0f172a;border:1px solid #334155;border-radius:16px;padding:18px;box-shadow:0 20px 60px rgba(0,0,0,.45);box-sizing:border-box;}"
        ".modal-card-wide{width:min(760px,100%);}"
        ".modal-header{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:14px;}"
        ".icon-btn{display:inline-flex;align-items:center;justify-content:center;width:32px;height:32px;border-radius:999px;border:1px solid #334155;background:#111827;color:#e5e7eb;cursor:pointer;font-size:18px;line-height:1;}"
        ".form-label{color:#94a3b8;font-size:13px;margin-bottom:10px;}"
        ".startup-alert-list{display:grid;gap:12px;}"
        ".startup-alert-item{background:#020617;border:1px solid #1e293b;border-radius:12px;padding:12px;}"
        ".startup-alert-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap;}"
        ".startup-alert-title{font-weight:700;color:#e5e7eb;}"
        ".startup-alert-meta{color:#94a3b8;font-size:12px;margin-top:4px;}"
        ".startup-alert-reason{margin-top:10px;color:#cbd5e1;line-height:1.6;}"
        ".window-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;}"
        ".field-label{display:flex;flex-direction:column;gap:6px;color:#cbd5e1;font-size:13px;}"
        ".text-input{width:100%;background:#020617;color:#e5e7eb;border:1px solid #334155;border-radius:10px;padding:10px 12px;box-sizing:border-box;}"
        ".topic-picker{display:flex;gap:8px;align-items:flex-end;margin-top:10px;flex-wrap:wrap;}"
        ".topic-picker .btn{flex-shrink:0;}"
        ".topic-meta{margin-top:8px;color:#94a3b8;font-size:12px;line-height:1.5;}"
        ".topic-select{min-width:220px;flex:1;}"
        ".modal-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:14px;}"
        "pre{white-space:pre-wrap;word-break:break-word;background:#020617;border-radius:10px;padding:12px;color:#cbd5e1;margin:0;}"
        "@media (max-width: 980px){body{padding:16px;}.layout{grid-template-columns:1fr;}.module-grid,.summary-groups,.summary-grid{grid-template-columns:1fr;}.module-top{flex-direction:column;}.module-tools{justify-content:flex-start;}.topbar{align-items:flex-start;}.identity-mobile-picker{display:grid;}.identity-list{display:none;}}"
        "</style></head><body>"
        "<div class='topbar'>"
        "<div><h1 style='margin:0;'>Xiuxian 控制台</h1><div style='color:#9ca3af;'>生成时间：<span id='generated-at'>-</span> ｜ 自动轮询："
        + html_escape(str(UI_AUTO_REFRESH_SEC))
        + "s</div></div>"
        "<div class='topbar-actions'><span id='global-switch-container'></span><button type='button' class='btn btn-secondary' data-open-basic-config='1'>基础配置</button><button type='button' class='btn btn-refresh' data-refresh-now='1'>手动刷新</button><a class='btn btn-secondary' href='/api/state' target='_blank' rel='noreferrer'>JSON</a></div>"
        "</div>"
        "<div id='flash' class='flash hidden'></div>"
        "<div class='layout'>"
        "<aside class='sidebar'><section class='card'><h2 style='display:flex;align-items:center;justify-content:space-between;'>账号与身份<button type='button' class='btn btn-secondary' data-open-login-account='1' style='font-size:0.85em;padding:4px 10px;'>登录账号</button></h2><div class='meta'>当前登录账号：<span id='account-user-id'>-</span></div><div class='sidebar-actions'><span class='form-label' style='margin:0;'>管理 SEND_AS_ID</span><button type='button' class='btn btn-secondary' data-open-add-identity='1'>新增身份</button></div><div class='identity-mobile-picker'><label class='form-label' for='identity-select-mobile' style='margin:0;'>切换身份</label><select id='identity-select-mobile' class='text-input'></select></div><div id='identity-list' class='identity-list'></div></section></aside>"
        "<main class='main'><section id='summary-panel'></section><section class='card'><h2>模块详情</h2><div id='module-grid' class='module-grid'></div></section></main>"
        "</div>"
        "<div id='basic-config-modal' class='modal-backdrop'>"
        "  <div class='modal-card'>"
        "    <div class='modal-header'><h3 style='margin:0;'>基础配置</h3><button class='icon-btn' type='button' data-close-modal='basic'>×</button></div>"
        "    <form id='basic-config-form'>"
        "      <div class='form-label'>配置游戏群聊、允许处理的 bot ID，以及固定话题 ID。支持自动读取话题列表，也支持手动输入后强制保存。话题留空或填 0 表示不使用。</div>"
        "      <label class='field-label'>游戏群聊 ID<input class='text-input' name='game_group_id' inputmode='numeric' placeholder='例如 -1001234567890' /></label>"
        "      <label class='field-label'>bot ID<input class='text-input' name='game_bot_ids' placeholder='多个请用逗号分隔' /></label>"
        "      <label class='field-label'>话题 ID<input class='text-input' name='game_topic_id' inputmode='numeric' placeholder='例如 12345' /></label>"
        "      <div class='topic-picker'><label class='field-label topic-select'><span>自动识别的话题列表</span><select id='forum-topic-select' class='text-input'><option value=''>请先刷新话题列表</option></select></label><button class='btn btn-secondary' type='button' data-refresh-forum-topics='1'>刷新话题列表</button></div>"
        "      <div id='forum-topic-meta' class='topic-meta'>当前未读取话题列表。</div>"
        "      <label class='field-label'><span>自动删除已发送消息</span><label class='toggle-field'><input type='checkbox' name='auto_delete_sent_messages' /><span>开启后，脚本在可删除时会自动删除自己发出的指令消息</span></label></label>"
        "      <div class='modal-actions'><button class='btn btn-secondary' type='button' data-close-modal='basic'>取消</button><button class='btn' type='submit'>保存</button></div>"
        "    </form>"
        "  </div>"
        "</div>"
        "<div id='add-identity-modal' class='modal-backdrop'>"
        "  <div class='modal-card'>"
        "    <div class='modal-header'><h3 style='margin:0;'>新增角色ID</h3><button class='icon-btn' type='button' data-close-modal='identity'>×</button></div>"
        "    <form id='add-identity-form'>"
        "      <div class='form-label'>输入一个可被当前 Telegram 账号访问的角色ID。新增成功后会立即加入调度并显示在左侧列表。</div>"
        "      <input class='text-input' name='send_as_id' inputmode='numeric' placeholder='例如 1234567890' />"
        "      <div class='modal-actions'><button class='btn btn-secondary' type='button' data-close-modal='identity'>取消</button><button class='btn' type='submit'>新增</button></div>"
        "    </form>"
        "  </div>"
        "</div>"
        "<div id='login-account-modal' class='modal-backdrop'>"
        "  <div class='modal-card'>"
        "    <div class='modal-header'><h3 style='margin:0;'>登录 Telegram 账号</h3><button class='icon-btn' type='button' data-close-modal='login-account'>×</button></div>"
        "    <div id='login-step-phone'>"
        "      <div class='form-label'>输入 Telegram 绑定的手机号（含国际区号，如 +86...）</div>"
        "      <input class='text-input' id='login-phone' placeholder='+8613800138000' />"
        "      <div class='modal-actions'><button class='btn btn-secondary' type='button' data-close-modal='login-account'>取消</button><button class='btn' type='button' id='login-send-code-btn'>发送验证码</button></div>"
        "    </div>"
        "    <div id='login-step-code' style='display:none;'>"
        "      <div class='form-label'>请输入收到的验证码</div>"
        "      <input class='text-input' id='login-code' placeholder='12345' />"
        "      <div class='modal-actions'><button class='btn btn-secondary' type='button' data-close-modal='login-account'>取消</button><button class='btn' type='button' id='login-verify-btn'>验证</button></div>"
        "    </div>"
        "    <div id='login-step-2fa' style='display:none;'>"
        "      <div class='form-label'>该账号已开启两步验证，请输入密码</div>"
        "      <input class='text-input' id='login-2fa-password' type='password' placeholder='两步验证密码' />"
        "      <div class='modal-actions'><button class='btn btn-secondary' type='button' data-close-modal='login-account'>取消</button><button class='btn' type='button' id='login-2fa-btn'>验证</button></div>"
        "    </div>"
        "    <div id='login-status' class='form-label' style='color:#f59e0b;margin-top:8px;'></div>"
        "  </div>"
        "</div>"
        "<div id='pet-name-modal' class='modal-backdrop'>"
        "  <div class='modal-card'>"
        "    <div class='modal-header'><h3 style='margin:0;'>设置法宝名称</h3><button class='icon-btn' type='button' data-close-modal='pet'>×</button></div>"
        "    <form id='pet-name-form'>"
        "      <input type='hidden' name='send_as_id' />"
        "      <div id='pet-name-identity' class='form-label'></div>"
        "      <input class='text-input' name='pet_name' placeholder='输入法宝名称' />"
        "      <div class='modal-actions'><button class='btn btn-secondary' type='button' data-close-modal='pet'>取消</button><button class='btn' type='submit'>保存</button></div>"
        "    </form>"
        "  </div>"
        "</div>"
        "<div id='window-modal' class='modal-backdrop'>"
        "  <div class='modal-card'>"
        "    <div class='modal-header'><h3 id='window-modal-title' style='margin:0;'>设置执行窗口</h3><button class='icon-btn' type='button' data-close-modal='window'>×</button></div>"
        "    <form id='window-form'>"
        "      <input type='hidden' name='send_as_id' />"
        "      <input type='hidden' name='module' />"
        "      <div id='window-modal-identity' class='form-label'></div>"
        "      <div id='window-modal-current' class='form-label'></div>"
        "      <div class='window-grid'><label class='field-label'>开始（UTC+8）<input class='text-input' type='number' min='0' max='23' name='start_hour_local' /></label><label class='field-label'>结束（UTC+8）<input class='text-input' type='number' min='0' max='23' name='end_hour_local' /></label></div>"
        "      <div class='form-label'>仅支持整点小时，且开始时间必须早于结束时间。</div>"
        "      <div class='modal-actions'><button class='btn btn-secondary' type='button' data-close-modal='window'>取消</button><button class='btn' type='submit'>保存</button></div>"
        "    </form>"
        "  </div>"
        "</div>"
        "<div id='startup-alert-modal' class='modal-backdrop'>"
        "  <div class='modal-card modal-card-wide'>"
        "    <div class='modal-header'><h3 style='margin:0;'>启动自动关闭提醒</h3><button class='icon-btn' type='button' data-close-modal='startup-alert'>×</button></div>"
        "    <div class='form-label'>以下模块在本次启动扫描中检测到旧任务超时，已被自动关闭。你可以在这里直接重新开启。</div>"
        "    <div id='startup-alert-list' class='startup-alert-list'></div>"
        "    <div class='modal-actions'><button class='btn btn-secondary' type='button' data-close-modal='startup-alert'>知道了</button></div>"
        "  </div>"
        "</div>"
        "<script>const CHAOGU_BOOT_DATA="
        + boot_data
        + ";</script>"
        "<script>"
        "const appState={snapshot:CHAOGU_BOOT_DATA.snapshot||{identities:[]},selectedId:CHAOGU_BOOT_DATA.selected_send_as_id||null,flashMessage:CHAOGU_BOOT_DATA.flash_message||'',flashError:false,flashVersion:CHAOGU_BOOT_DATA.flash_message?1:0,renderedFlashVersion:0,lastSnapshotSerialized:'',startupAlerts:(CHAOGU_BOOT_DATA.snapshot&&Array.isArray(CHAOGU_BOOT_DATA.snapshot.startup_alerts)?CHAOGU_BOOT_DATA.snapshot.startup_alerts.slice():[]),startupAlertDismissed:false};"
        f"const POLL_INTERVAL_MS={int(UI_AUTO_REFRESH_SEC) * 1000};"
        "function serializeComparableSnapshot(snapshot){const comparable=Object.assign({},snapshot||{});delete comparable.generated_at;return JSON.stringify(comparable);}"
        "appState.lastSnapshotSerialized=serializeComparableSnapshot(appState.snapshot);"
        "function escapeHtml(value){return String(value==null?'':value).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;').replace(/'/g,'&#39;');}"
        "function getIdentities(){return appState.snapshot&&Array.isArray(appState.snapshot.identities)?appState.snapshot.identities:[];}"
        "function getStartupAlerts(){return Array.isArray(appState.startupAlerts)?appState.startupAlerts:[];}"
        "function mergeStartupAlerts(snapshot){const nextAlerts=snapshot&&Array.isArray(snapshot.startup_alerts)?snapshot.startup_alerts:[];if(!nextAlerts.length){return;}const merged=new Map(getStartupAlerts().filter(alert=>alert&&alert.key).map(alert=>[String(alert.key),alert]));let changed=false;nextAlerts.forEach(function(alert){if(!alert||!alert.key){return;}const key=String(alert.key);if(!merged.has(key)){changed=true;}merged.set(key,alert);});if(changed||merged.size!==getStartupAlerts().length){appState.startupAlerts=Array.from(merged.values());appState.startupAlertDismissed=false;}}"
        "function removeStartupAlert(alertKey){const key=String(alertKey||'');if(!key){return false;}const currentAlerts=getStartupAlerts();const nextAlerts=currentAlerts.filter(alert=>String((alert||{}).key||'')!==key);if(nextAlerts.length===currentAlerts.length){return false;}appState.startupAlerts=nextAlerts;return true;}"
        "function normalizeSelectedId(){const identities=getIdentities();if(!identities.length){appState.selectedId=null;return null;}const exists=identities.some(identity=>identity.send_as_id===Number(appState.selectedId));if(!exists){appState.selectedId=identities[0].send_as_id;}return Number(appState.selectedId);}"
        "function getSelectedIdentity(){const selectedId=normalizeSelectedId();return getIdentities().find(identity=>identity.send_as_id===selectedId)||null;}"
        "function isMobileLayout(){return window.matchMedia('(max-width: 980px)').matches;}"
        "let lastMobileLayout=isMobileLayout();"
        "function selectIdentity(sendAsId){appState.selectedId=Number(sendAsId)||null;renderAll();}"
        "function syncSelectedIdToUrl(){const url=new URL(window.location.href);if(appState.selectedId==null){url.searchParams.delete('send_as_id');}else{url.searchParams.set('send_as_id',String(appState.selectedId));}history.replaceState(null,'',url.pathname+url.search);}"
        "function updateFlash(message,isError){appState.flashMessage=message||'';appState.flashError=!!isError;appState.flashVersion+=1;}"
        "let flashTimer=null;function setFlash(){const flash=document.getElementById('flash');if(!appState.flashMessage){if(flashTimer){window.clearTimeout(flashTimer);flashTimer=null;}flash.textContent='';flash.classList.add('hidden');flash.classList.remove('error');return;}if(appState.renderedFlashVersion===appState.flashVersion){return;}appState.renderedFlashVersion=appState.flashVersion;if(flashTimer){window.clearTimeout(flashTimer);flashTimer=null;}flash.textContent=appState.flashMessage;flash.classList.remove('hidden');flash.classList.toggle('error',!!appState.flashError);flashTimer=window.setTimeout(function(){flash.classList.add('hidden');},5000);}"
        "function getSectStatusText(identity){if(identity.sect_refresh_pending){return '宗门：更新中';}if(identity.sect_refresh_error){return `宗门：${identity.sect_refresh_error}`;}if(identity.sect_name){return `宗门：${identity.sect_name}`;}return '宗门：未获取';}"
        "function getIdentityStatusText(identity){return identity&&identity.identity_status_text?String(identity.identity_status_text):(identity&&identity.identity_enabled?'运行中':'已暂停');}"
        "function renderIdentityList(identities,selectedId){const list=document.getElementById('identity-list');if(!list){return;}if(isMobileLayout()){list.innerHTML='';return;}list.innerHTML=identities.map(identity=>{const active=identity.send_as_id===selectedId?' identity-item-active':'';return `<button type=\"button\" class=\"identity-item${active}\" data-select-identity=\"${identity.send_as_id}\"><strong>${escapeHtml(identity.display_name)}</strong><span>${escapeHtml(getIdentityStatusText(identity))}</span></button>`;}).join('');}"
        "function renderIdentitySelect(identities,selectedId){const select=document.getElementById('identity-select-mobile');if(!select){return;}if(!isMobileLayout()){select.disabled=true;return;}if(!identities.length){select.innerHTML='<option value=\"\">暂无身份</option>';select.disabled=true;return;}select.innerHTML=identities.map(identity=>{const sectLabel=getSectStatusText(identity).replace(/^宗门：/,'');return `<option value=\"${identity.send_as_id}\">${escapeHtml(identity.display_name)} ｜ ${escapeHtml(getIdentityStatusText(identity))} ｜ ${escapeHtml(sectLabel)}</option>`;}).join('');select.disabled=identities.length<=1;select.value=selectedId==null?'':String(selectedId);}"
        "function renderForumTopicOptions(selectedTopicId){const select=document.getElementById('forum-topic-select');const meta=document.getElementById('forum-topic-meta');if(!select||!meta){return;}const topics=appState.snapshot&&Array.isArray(appState.snapshot.forum_topics)?appState.snapshot.forum_topics:[];const currentTopicId=Number(selectedTopicId!=null&&selectedTopicId!==''?selectedTopicId:(appState.snapshot&&appState.snapshot.game_topic_id)||0);const options=['<option value=\"\">不使用话题</option>'];topics.forEach(function(topic){options.push(`<option value=\"${topic.id}\">${escapeHtml(topic.title||('话题 '+topic.id))} ｜ ${topic.id}</option>`);});select.innerHTML=options.join('');select.value=currentTopicId>0?String(currentTopicId):'';const updatedAt=appState.snapshot&&appState.snapshot.forum_topics_updated_at||'未读取';meta.textContent=topics.length?`已读取 ${topics.length} 个话题，更新时间：${updatedAt}`:`当前未读取话题列表。`;select.disabled=false;}"
        "function renderSummary(identity){const panel=document.getElementById('summary-panel');if(!identity){panel.innerHTML=\"<section class='card'><h2>暂无身份</h2><div class='meta'>当前没有可展示的角色ID。</div></section>\";return;}const sortedModules=(identity.modules||[]).slice().sort((a,b)=>Number(Boolean(b.enabled))-Number(Boolean(a.enabled)));const availableModuleNames=new Set(sortedModules.map(module=>module.name));const moduleSummary=sortedModules.map(module=>{const effectiveMark=module.effective_enabled?'🟢':(module.enabled?'🟡':'🔴');const suffix=module.enabled&&(!module.effective_enabled)?'（暂停中）':'';return `<span class=\\\"summary-chip\\\">${effectiveMark} ${escapeHtml(module.name)}${escapeHtml(suffix)}</span>`;}).join('');const runtimeItemList=[{label:'身份状态',value:getIdentityStatusText(identity)},{label:'待响应',value:identity.pending_task_count||0},{label:'已追踪消息',value:identity.message_count||0}];if(availableModuleNames.has('元婴')){runtimeItemList.splice(1,0,{label:'元婴阶段',value:identity.phases&&identity.phases.yuanying||''});}if(availableModuleNames.has('深度闭关')){runtimeItemList.splice(availableModuleNames.has('元婴')?2:1,0,{label:'深度闭关阶段',value:identity.phases&&identity.phases.deep_retreat||''});}const runtimeItems=runtimeItemList.map(item=>`<div class=\\\"summary-item\\\"><span class=\\\"summary-item-label\\\">${escapeHtml(item.label)}</span><span class=\\\"summary-item-value\\\">${escapeHtml(item.value)}</span></div>`).join('');const timerItemList=[];if(availableModuleNames.has('灵树')){timerItemList.push({label:'灵树',value:identity.timers&&identity.timers.next_irr_time||''});}if(availableModuleNames.has('法宝')){timerItemList.push({label:'法宝',value:identity.timers&&identity.timers.next_pet_time||''});}if(availableModuleNames.has('玄骨考校')){timerItemList.push({label:'考校',value:identity.timers&&identity.timers.next_quiz_time||''});}if(availableModuleNames.has('点卯')){timerItemList.push({label:'点卯',value:identity.timers&&identity.timers.next_checkin_time||''});}if(availableModuleNames.has('闯塔')){timerItemList.push({label:'闯塔',value:identity.timers&&identity.timers.next_tower_time||''});}if(availableModuleNames.has('深度闭关')){timerItemList.push({label:'闭关',value:identity.timers&&identity.timers.next_deep_retreat_time||''});}if(availableModuleNames.has('元婴')){timerItemList.push({label:'元婴',value:identity.timers&&identity.timers.next_yuanying_time||''});}const timerItems=timerItemList.map(item=>`<div class=\\\"summary-item\\\"><span class=\\\"summary-item-label\\\">${escapeHtml(item.label)}</span><span class=\\\"summary-item-value\\\">${escapeHtml(item.value)}</span></div>`).join('');const refreshDisabled=identity.sect_refresh_pending?' disabled':'';const refreshText=identity.sect_refresh_pending?'更新中':'更新信息';const identityNextEnabled=identity.identity_enabled?0:1;const identitySwitchClass=identity.identity_enabled?'on':'off';const identitySwitchText=identity.identity_enabled?'运行中':'已暂停';const xwCur=identity.xiuwei_current||0;const xwMax=identity.xiuwei_max||0;const xwPct=xwMax>0?Math.min(100,Math.round(xwCur/xwMax*10000)/100):0;const xwBarHtml=xwMax>0?`<div class=\\\"xiuwei-bar-wrap\\\"><div class=\\\"xiuwei-bar-label\\\">修为</div><div class=\\\"xiuwei-bar-track\\\"><div class=\\\"xiuwei-bar-fill\\\" style=\\\"width:${xwPct}%\\\"></div><span class=\\\"xiuwei-bar-text\\\">${xwPct}%（${xwCur.toLocaleString()} / ${xwMax.toLocaleString()}）</span></div></div>`:'';panel.innerHTML=`<section class=\"card\"><div class=\"summary-head\"><h2>${escapeHtml(identity.display_name)}</h2><div class=\"summary-head-actions\"><button type=\"button\" class=\"switch switch-${identitySwitchClass}\" data-toggle-identity=\"${identity.send_as_id}\" data-enabled=\"${identityNextEnabled}\"><span class=\"switch-track\"><span class=\"switch-thumb\"></span></span><span class=\"switch-text\">${identitySwitchText}</span></button><span class=\"form-label\" style=\"margin:0;\">信息更新时间：${escapeHtml(identity.sect_updated_at||'未设置')}</span><button type=\"button\" class=\"btn btn-secondary\" data-refresh-identity=\"${identity.send_as_id}\"${refreshDisabled}>${refreshText}</button></div></div><div class=\"meta\">主账号：${escapeHtml(appState.snapshot.account_user_id||'未获取')} ｜ username: @${escapeHtml(identity.username||'未设置')} ｜ 角色名: ${escapeHtml(identity.label||'未设置')} ｜ 道号: ${escapeHtml(identity.daohao||'未获取')} ｜ 角色ID: ${escapeHtml(identity.send_as_id)} ｜ 境界: ${escapeHtml(identity.realm||'未获取')} ｜ ${escapeHtml(getSectStatusText(identity))}</div>${xwBarHtml}<div class=\"summary-strip\">${moduleSummary}</div><div class=\"summary-groups\"><section class=\"summary-group\"><div class=\"summary-group-title\">运行状态</div><div class=\"summary-grid\">${runtimeItems}</div></section><section class=\"summary-group\"><div class=\"summary-group-title\">下次执行时间</div><div class=\"summary-grid\">${timerItems}</div></section></div></section>`;}"
        "function renderModules(identity){const grid=document.getElementById('module-grid');if(!identity){grid.innerHTML='';return;}grid.innerHTML=(identity.modules||[]).map(module=>{let moduleNote='';let settingsButton='';if(module.name==='法宝'){moduleNote=`<div class=\"module-note\">当前名称：${escapeHtml(identity.pet_name||'')}</div>`;settingsButton='<button type=\"button\" class=\"btn btn-secondary\" data-open-pet-modal=\"1\">设置</button>';}else if(module.name==='点卯'){const win=identity.checkin_window_local||{};moduleNote=`<div class=\"module-note\">执行窗口：UTC+8 ${String(win.start_hour).padStart(2,'0')}:00-${String(win.end_hour).padStart(2,'0')}:00</div>`;settingsButton='<button type=\"button\" class=\"btn btn-secondary\" data-open-window-modal=\"点卯\">设置</button>';}else if(module.name==='闯塔'){const win=identity.tower_window_local||{};moduleNote=`<div class=\"module-note\">执行窗口：UTC+8 ${String(win.start_hour).padStart(2,'0')}:00-${String(win.end_hour).padStart(2,'0')}:00</div>`;settingsButton='<button type=\"button\" class=\"btn btn-secondary\" data-open-window-modal=\"闯塔\">设置</button>';}else if(module.name==='玄骨考校'){moduleNote='<div class=\"module-note\">监听题目后会按题库自动回复 .作答 &lt;选项&gt;</div>';}const nextEnabled=module.enabled?0:1;const switchClass=module.enabled?'on':'off';const switchText=module.enabled?'已开启':'已关闭';return `<div class=\"module-card\"><div class=\"module-top\"><div style=\"flex:1;min-width:0;\"><div class=\"module-title\">${escapeHtml(module.name)}</div>${moduleNote}</div><div class=\"module-tools\">${settingsButton}<button type=\"button\" class=\"switch switch-${switchClass}\" data-toggle-module=\"1\" data-module=\"${escapeHtml(module.name)}\" data-enabled=\"${nextEnabled}\"><span class=\"switch-track\"><span class=\"switch-thumb\"></span></span><span class=\"switch-text\">${switchText}</span></button></div></div><pre style=\"flex:1;\">${escapeHtml(module.detail||'')}</pre></div>`;}).join('');}"
        "function getStartupAlertByKey(alertKey){const key=String(alertKey||'');return getStartupAlerts().find(alert=>String((alert||{}).key||'')===key)||null;}"
        "function isStartupAlertResolved(alert){const identityId=Number((alert&&alert.send_as_id)||0);const moduleName=String((alert&&alert.module_name)||'');if(!identityId||!moduleName){return true;}const identity=getIdentities().find(item=>item.send_as_id===identityId);if(!identity){return true;}const module=(identity.modules||[]).find(item=>item.name===moduleName);return !!(module&&module.enabled); }"
        "function pruneResolvedStartupAlerts(){const currentAlerts=getStartupAlerts();if(!currentAlerts.length){return false;}const nextAlerts=currentAlerts.filter(alert=>!isStartupAlertResolved(alert));if(nextAlerts.length===currentAlerts.length){return false;}appState.startupAlerts=nextAlerts;return true;}"
        "function renderStartupAlertsModal(){const list=document.getElementById('startup-alert-list');if(!list){return;}const alerts=getStartupAlerts();if(!alerts.length){list.innerHTML='<div class=\"form-label\" style=\"margin:0;\">当前没有待处理的启动自动关闭提醒。</div>';return;}list.innerHTML=alerts.map(alert=>{const key=escapeHtml((alert&&alert.key)||'');const identityId=Number((alert&&alert.send_as_id)||0);const identityIdText=identityId>0?String(identityId):'-';const displayName=escapeHtml((alert&&alert.display_name)||identityIdText);const moduleName=escapeHtml((alert&&alert.module_name)||'未知模块');const reason=escapeHtml((alert&&alert.reason)||'');return `<div class=\"startup-alert-item\"><div class=\"startup-alert-head\"><div><div class=\"startup-alert-title\">${displayName} · ${moduleName}</div><div class=\"startup-alert-meta\">角色ID：${escapeHtml(identityIdText)}</div></div><button type=\"button\" class=\"btn\" data-recover-startup-alert=\"${key}\">开启模块</button></div><div class=\"startup-alert-reason\">${reason}</div></div>`;}).join('');}"
        "function openStartupAlertsModalIfNeeded(){renderStartupAlertsModal();const modal=document.getElementById('startup-alert-modal');if(!modal){return;}const shouldOpen=getStartupAlerts().length>0&&!appState.startupAlertDismissed;modal.classList.toggle('show',shouldOpen);}"
        "function closeStartupAlertModal(){appState.startupAlertDismissed=true;const modal=document.getElementById('startup-alert-modal');if(modal){modal.classList.remove('show');}}"
        "function renderAll(){const globalEnabled=!!(appState.snapshot&&appState.snapshot.global_enabled);const gc=document.getElementById('global-switch-container');if(gc){const gNextEnabled=globalEnabled?0:1;const gClass=globalEnabled?'on':'off';const gText=globalEnabled?'运行中':'已暂停';gc.innerHTML=`<button type=\"button\" class=\"switch switch-${gClass}\" data-toggle-global=\"1\" data-enabled=\"${gNextEnabled}\"><span class=\"switch-track\"><span class=\"switch-thumb\"></span></span><span class=\"switch-text\">${gText}</span></button>`;}document.getElementById('generated-at').textContent=appState.snapshot&&appState.snapshot.generated_at||'-';document.getElementById('account-user-id').textContent=String(appState.snapshot&&appState.snapshot.account_user_id||'未获取');pruneResolvedStartupAlerts();const identities=getIdentities();const selectedId=normalizeSelectedId();const identity=identities.find(item=>item.send_as_id===selectedId)||null;renderIdentityList(identities,selectedId);renderIdentitySelect(identities,selectedId);renderSummary(identity);renderModules(identity);openStartupAlertsModalIfNeeded();setFlash();syncSelectedIdToUrl();}"
        "function applySnapshot(nextSnapshot,options){if(!nextSnapshot){return false;}mergeStartupAlerts(nextSnapshot);const nextSerialized=serializeComparableSnapshot(nextSnapshot);if(appState.lastSnapshotSerialized===nextSerialized){if(!(options&&options.keepFlash)){appState.flashMessage='';appState.flashError=false;appState.renderedFlashVersion=0;}appState.snapshot=nextSnapshot;if(pruneResolvedStartupAlerts()){renderAll();return true;}openStartupAlertsModalIfNeeded();setFlash();return false;}appState.snapshot=nextSnapshot;appState.lastSnapshotSerialized=nextSerialized;renderAll();return true;}"
        "function openBasicConfigModal(){const modal=document.getElementById('basic-config-modal');const groupInput=modal.querySelector('input[name=\"game_group_id\"]');const botsInput=modal.querySelector('input[name=\"game_bot_ids\"]');const topicInput=modal.querySelector('input[name=\"game_topic_id\"]');const autoDeleteInput=modal.querySelector('input[name=\"auto_delete_sent_messages\"]');if(groupInput){const groupId=Number(appState.snapshot&&appState.snapshot.game_group_id||0);groupInput.value=groupId?String(groupId):'';}if(botsInput){const botIds=appState.snapshot&&Array.isArray(appState.snapshot.game_bot_ids)?appState.snapshot.game_bot_ids:[];botsInput.value=botIds.join(', ');}if(topicInput){const topicId=Number(appState.snapshot&&appState.snapshot.game_topic_id||0);topicInput.value=topicId>0?String(topicId):'';}if(autoDeleteInput){autoDeleteInput.checked=!!(appState.snapshot&&appState.snapshot.auto_delete_sent_messages);}renderForumTopicOptions(topicInput&&topicInput.value);modal.classList.add('show');if(groupInput){groupInput.focus();groupInput.select();}}"
        "function closeBasicConfigModal(){document.getElementById('basic-config-modal').classList.remove('show');}"
        "function openIdentityModal(){const modal=document.getElementById('add-identity-modal');modal.classList.add('show');const input=modal.querySelector('input[name=\"send_as_id\"]');if(input){input.value='';input.focus();}}"
        "function closeIdentityModal(){document.getElementById('add-identity-modal').classList.remove('show');}"
        "function openLoginAccountModal(){const modal=document.getElementById('login-account-modal');modal.classList.add('show');document.getElementById('login-step-phone').style.display='';document.getElementById('login-step-code').style.display='none';document.getElementById('login-step-2fa').style.display='none';document.getElementById('login-status').textContent='';document.getElementById('login-phone').value='';document.getElementById('login-code').value='';document.getElementById('login-phone').focus();}"
        "function closeLoginAccountModal(){document.getElementById('login-account-modal').classList.remove('show');document.getElementById('login-status').textContent='';}"
        "function openPetModal(){const identity=getSelectedIdentity();if(!identity){return;}const modal=document.getElementById('pet-name-modal');modal.querySelector('input[name=\"send_as_id\"]').value=identity.send_as_id;modal.querySelector('input[name=\"pet_name\"]').value=identity.pet_name||'';document.getElementById('pet-name-identity').textContent=`当前身份：${identity.display_name}`;modal.classList.add('show');const input=modal.querySelector('input[name=\"pet_name\"]');if(input){input.focus();input.select();}}"
        "function closePetModal(){document.getElementById('pet-name-modal').classList.remove('show');}"
        "function openWindowModal(moduleName){const identity=getSelectedIdentity();if(!identity){return;}const modal=document.getElementById('window-modal');const form=document.getElementById('window-form');const windowData=moduleName==='点卯'?(identity.checkin_window_local||{}):(identity.tower_window_local||{});document.getElementById('window-modal-title').textContent=`设置${moduleName}窗口`;document.getElementById('window-modal-identity').textContent=`当前身份：${identity.display_name}`;document.getElementById('window-modal-current').textContent=`当前窗口：${windowData.text||''}`;form.querySelector('input[name=\"send_as_id\"]').value=identity.send_as_id;form.querySelector('input[name=\"module\"]').value=moduleName;form.querySelector('input[name=\"start_hour_local\"]').value=windowData.start_hour||0;form.querySelector('input[name=\"end_hour_local\"]').value=windowData.end_hour||0;modal.classList.add('show');const input=form.querySelector('input[name=\"start_hour_local\"]');if(input){input.focus();input.select();}}"
        "function closeWindowModal(){document.getElementById('window-modal').classList.remove('show');}"
        "async function parseApiResponse(response){let data={};try{data=await response.json();}catch(_error){}if(response.status===401){window.location.href='/';return null;}if(!response.ok||!data.ok){throw new Error(data.error||data.message||`请求失败 (${response.status})`);}return data;}"
        "async function postJson(path,payload){const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),credentials:'same-origin',cache:'no-store'});return parseApiResponse(response);}"
        "async function refreshState(options){const opts=options||{};try{const response=await fetch('/api/state',{credentials:'same-origin',cache:'no-store'});const data=await parseApiResponse(response);if(!data){return;}if(!opts.keepFlash){appState.flashMessage='';appState.flashError=false;appState.renderedFlashVersion=0;}const changed=applySnapshot(data.snapshot||{identities:[]},{keepFlash:opts.keepFlash});if(!changed&&!opts.keepFlash){setFlash();}}catch(error){if(!opts.silent){updateFlash((error&&error.message)||'刷新失败',true);renderAll();}}}"
        "async function toggleIdentity(sendAsId,enabled){try{const data=await postJson('/api/identity-enabled',{send_as_id:sendAsId,enabled:!!enabled});updateFlash(data.message||'已更新身份状态',false);const changed=applySnapshot(data.snapshot||appState.snapshot,{keepFlash:true});if(!changed){renderAll();}}catch(error){updateFlash((error&&error.message)||'身份切换失败',true);renderAll();}}"
        "async function toggleGlobal(enabled){try{const data=await postJson('/api/global-enabled',{enabled:!!enabled});updateFlash(data.message||'已更新全局状态',false);const changed=applySnapshot(data.snapshot||appState.snapshot,{keepFlash:true});if(!changed){renderAll();}}catch(error){updateFlash((error&&error.message)||'全局切换失败',true);renderAll();}}"
        "async function toggleModule(moduleName,enabled){try{const data=await postJson('/api/toggle',{send_as_id:appState.selectedId,module:moduleName,enabled:!!enabled});updateFlash(data.message||'已更新模块状态',false);const changed=applySnapshot(data.snapshot||appState.snapshot,{keepFlash:true});if(!changed){renderAll();}}catch(error){updateFlash((error&&error.message)||'模块切换失败',true);renderAll();}}"
        "async function restoreStartupAlert(alertKey){const alert=getStartupAlertByKey(alertKey);if(!alert){removeStartupAlert(alertKey);updateFlash('该启动提醒已处理',false);renderAll();return;}try{const data=await postJson('/api/toggle',{send_as_id:alert.send_as_id,module:alert.module_name,enabled:true});appState.selectedId=Number(alert.send_as_id)||appState.selectedId;removeStartupAlert(alertKey);updateFlash(data.message||'已重新开启模块',false);const changed=applySnapshot(data.snapshot||appState.snapshot,{keepFlash:true});if(!changed){renderAll();}}catch(error){updateFlash((error&&error.message)||'模块开启失败',true);renderAll();}}"
        "async function submitIdentity(event){event.preventDefault();const form=event.currentTarget;const sendAsId=form.querySelector('input[name=\"send_as_id\"]').value;try{const data=await postJson('/api/identity',{send_as_id:sendAsId});if(data&&data.send_as_id!=null){appState.selectedId=Number(data.send_as_id)||appState.selectedId;}updateFlash(data.message||'已新增身份',false);closeIdentityModal();applySnapshot(data.snapshot||appState.snapshot,{keepFlash:true});}catch(error){updateFlash((error&&error.message)||'新增身份失败',true);renderAll();}}"
        "async function submitPetName(event){event.preventDefault();const form=event.currentTarget;const sendAsId=form.querySelector('input[name=\"send_as_id\"]').value;const petName=form.querySelector('input[name=\"pet_name\"]').value;try{const data=await postJson('/api/pet-name',{send_as_id:sendAsId,pet_name:petName});updateFlash(data.message||'已更新法宝名称',false);closePetModal();applySnapshot(data.snapshot||appState.snapshot,{keepFlash:true});}catch(error){updateFlash((error&&error.message)||'法宝名称更新失败',true);renderAll();}}"
        "async function submitWindow(event){event.preventDefault();const form=event.currentTarget;const payload={send_as_id:form.querySelector('input[name=\"send_as_id\"]').value,module:form.querySelector('input[name=\"module\"]').value,start_hour_local:form.querySelector('input[name=\"start_hour_local\"]').value,end_hour_local:form.querySelector('input[name=\"end_hour_local\"]').value};try{const data=await postJson('/api/module-window',payload);updateFlash(data.message||'已更新执行窗口',false);closeWindowModal();applySnapshot(data.snapshot||appState.snapshot,{keepFlash:true});}catch(error){updateFlash((error&&error.message)||'执行窗口更新失败',true);renderAll();}}"
        "async function submitBasicConfig(event){event.preventDefault();const form=event.currentTarget;const payload={game_group_id:form.querySelector('input[name=\"game_group_id\"]').value,game_bot_ids:form.querySelector('input[name=\"game_bot_ids\"]').value,game_topic_id:form.querySelector('input[name=\"game_topic_id\"]').value,auto_delete_sent_messages:!!form.querySelector('input[name=\"auto_delete_sent_messages\"]').checked};try{const data=await postJson('/api/basic-config',payload);updateFlash(data.message||'已更新基础配置',false);closeBasicConfigModal();applySnapshot(data.snapshot||appState.snapshot,{keepFlash:true});}catch(error){updateFlash((error&&error.message)||'基础配置更新失败',true);renderAll();}}"
        "async function refreshForumTopics(){const form=document.getElementById('basic-config-form');if(!form){return;}const groupId=form.querySelector('input[name=\"game_group_id\"]').value;const topicInput=form.querySelector('input[name=\"game_topic_id\"]');const manualTopicId=topicInput?topicInput.value:'';try{const data=await postJson('/api/forum-topics',{game_group_id:groupId});updateFlash(data.message||'已刷新话题列表',false);if(data.snapshot){applySnapshot(data.snapshot,{keepFlash:true});}const topics=Array.isArray(data.forum_topics)?data.forum_topics:[];appState.snapshot=Object.assign({},appState.snapshot,{forum_topics:topics,forum_topics_updated_at:data.forum_topics_updated_at||((appState.snapshot&&appState.snapshot.forum_topics_updated_at)||'未设置')});renderForumTopicOptions(manualTopicId);if(topicInput){topicInput.value=manualTopicId||'';}}catch(error){updateFlash((error&&error.message)||'刷新话题列表失败',true);renderAll();}}"
        "async function refreshIdentityInfo(sendAsId){try{const data=await postJson('/api/identity-refresh',{send_as_id:sendAsId});updateFlash(data.message||'已开始更新角色信息',false);applySnapshot(data.snapshot||appState.snapshot,{keepFlash:true});}catch(error){updateFlash((error&&error.message)||'角色信息更新失败',true);renderAll();}}"
        "async function loginSendCode(){const phone=document.getElementById('login-phone').value.trim();const status=document.getElementById('login-status');if(!phone){status.textContent='请输入手机号';return;}status.textContent='正在发送验证码…';document.getElementById('login-send-code-btn').disabled=true;try{const data=await postJson('/api/account/login-start',{phone:phone});status.textContent='验证码已发送，请查收';document.getElementById('login-step-phone').style.display='none';document.getElementById('login-step-code').style.display='';document.getElementById('login-code').focus();}catch(error){status.textContent=(error&&error.message)||'发送验证码失败';}finally{document.getElementById('login-send-code-btn').disabled=false;}}"
        "async function loginVerifyCode(password){const code=document.getElementById('login-code').value.trim();const status=document.getElementById('login-status');const payload=password?{code:'',password:password}:{code:code};if(!password&&!code){status.textContent='请输入验证码';return;}status.textContent='正在验证…';try{const data=await postJson('/api/account/login-verify',payload);if(data.error==='need_2fa'){status.textContent='需要两步验证密码';document.getElementById('login-step-code').style.display='none';document.getElementById('login-step-2fa').style.display='';document.getElementById('login-2fa-password').focus();return;}status.textContent='';updateFlash(data.message||'登录成功',false);closeLoginAccountModal();applySnapshot(data.snapshot||appState.snapshot,{keepFlash:true});}catch(error){const errMsg=(error&&error.message)||'验证失败';if(errMsg==='need_2fa'){status.textContent='需要两步验证密码';document.getElementById('login-step-code').style.display='none';document.getElementById('login-step-2fa').style.display='';document.getElementById('login-2fa-password').focus();}else{status.textContent=errMsg;}}}"
        "document.addEventListener('click',function(event){const globalToggleBtn=event.target.closest('[data-toggle-global]');if(globalToggleBtn){toggleGlobal(globalToggleBtn.getAttribute('data-enabled')==='1');return;}const selectBtn=event.target.closest('[data-select-identity]');if(selectBtn){selectIdentity(selectBtn.getAttribute('data-select-identity'));return;}const refreshIdentityBtn=event.target.closest('[data-refresh-identity]');if(refreshIdentityBtn){refreshIdentityInfo(refreshIdentityBtn.getAttribute('data-refresh-identity'));return;}const toggleIdentityBtn=event.target.closest('[data-toggle-identity]');if(toggleIdentityBtn){toggleIdentity(toggleIdentityBtn.getAttribute('data-toggle-identity'),toggleIdentityBtn.getAttribute('data-enabled')==='1');return;}const toggleBtn=event.target.closest('[data-toggle-module]');if(toggleBtn){toggleModule(toggleBtn.getAttribute('data-module'),toggleBtn.getAttribute('data-enabled')==='1');return;}const recoverBtn=event.target.closest('[data-recover-startup-alert]');if(recoverBtn){restoreStartupAlert(recoverBtn.getAttribute('data-recover-startup-alert'));return;}if(event.target.closest('[data-open-basic-config]')){openBasicConfigModal();return;}if(event.target.closest('[data-open-add-identity]')){openIdentityModal();return;}if(event.target.closest('[data-open-login-account]')){openLoginAccountModal();return;}if(event.target.closest('[data-open-pet-modal]')){openPetModal();return;}const windowBtn=event.target.closest('[data-open-window-modal]');if(windowBtn){openWindowModal(windowBtn.getAttribute('data-open-window-modal'));return;}if(event.target.closest('[data-refresh-now]')){refreshState({silent:false,keepFlash:true});return;}if(event.target.closest('[data-refresh-forum-topics]')){refreshForumTopics();return;}if(event.target.getAttribute('data-close-modal')==='basic'||event.target.id==='basic-config-modal'){closeBasicConfigModal();return;}if(event.target.getAttribute('data-close-modal')==='identity'||event.target.id==='add-identity-modal'){closeIdentityModal();return;}if(event.target.getAttribute('data-close-modal')==='login-account'||event.target.id==='login-account-modal'){closeLoginAccountModal();return;}if(event.target.getAttribute('data-close-modal')==='pet'||event.target.id==='pet-name-modal'){closePetModal();return;}if(event.target.getAttribute('data-close-modal')==='window'||event.target.id==='window-modal'){closeWindowModal();return;}if(event.target.getAttribute('data-close-modal')==='startup-alert'||event.target.id==='startup-alert-modal'){closeStartupAlertModal();return;}});"
        "document.addEventListener('keydown',function(event){if(event.key==='Escape'){closeBasicConfigModal();closeIdentityModal();closeLoginAccountModal();closePetModal();closeWindowModal();closeStartupAlertModal();}});"
        "document.getElementById('basic-config-form').addEventListener('submit',submitBasicConfig);"
        "document.getElementById('forum-topic-select').addEventListener('change',function(event){const form=document.getElementById('basic-config-form');const topicInput=form&&form.querySelector('input[name=\"game_topic_id\"]');if(topicInput){topicInput.value=event.target.value||'';}});"
        "document.getElementById('add-identity-form').addEventListener('submit',submitIdentity);"
        "document.getElementById('login-send-code-btn').addEventListener('click',loginSendCode);"
        "document.getElementById('login-verify-btn').addEventListener('click',function(){loginVerifyCode();});"
        "document.getElementById('login-2fa-btn').addEventListener('click',function(){const pw=document.getElementById('login-2fa-password').value;loginVerifyCode(pw);});"
        "document.getElementById('login-code').addEventListener('keydown',function(e){if(e.key==='Enter'){loginVerifyCode();}});"
        "document.getElementById('login-2fa-password').addEventListener('keydown',function(e){if(e.key==='Enter'){const pw=document.getElementById('login-2fa-password').value;loginVerifyCode(pw);}});"
        "document.getElementById('login-phone').addEventListener('keydown',function(e){if(e.key==='Enter'){loginSendCode();}});"
        "document.getElementById('identity-select-mobile').addEventListener('change',function(event){selectIdentity(event.target.value);});"
        "document.getElementById('pet-name-form').addEventListener('submit',submitPetName);"
        "document.getElementById('window-form').addEventListener('submit',submitWindow);"
        "window.addEventListener('resize',function(){const mobileLayout=isMobileLayout();if(mobileLayout===lastMobileLayout){return;}lastMobileLayout=mobileLayout;renderAll();});"
        "renderAll();"
        "window.setInterval(function(){refreshState({silent:true,keepFlash:true});},POLL_INTERVAL_MS);"
        "</script>"
        "</body></html>"
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
    await send_audit_log(f"🗡️ 已更新法宝名称[{get_identity_display_name(send_as_id)}]：{pet_name}")
    return True, f"已更新法宝名称[{get_identity_display_name(send_as_id)}]：{pet_name}"


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


async def ui_add_identity(send_as_id_raw, actor_id=None):
    ok, message, canonical_id = await register_identity(send_as_id_raw, source="ui", actor_id=actor_id)
    return ok, message, canonical_id


# ================= 多账号登录 =================
_pending_login = {}  # 临时存储登录中间态 {session_key: {client, phone, phone_code_hash}}


async def ui_account_login_start(phone, session_key):
    phone = (phone or "").strip()
    if not phone:
        return False, "请输入手机号", None
    # 使用临时 session 名称进行登录
    tc = create_account_client(f"pending_{session_key}")
    await tc.connect()
    try:
        sent = await tc.send_code_request(phone)
        _pending_login[session_key] = {
            "client": tc,
            "phone": phone,
            "phone_code_hash": sent.phone_code_hash,
        }
        return True, "验证码已发送", sent.phone_code_hash
    except Exception as e:
        await tc.disconnect()
        return False, f"发送验证码失败: {e}", None


async def ui_account_login_verify(code, session_key, password=None):
    pending = _pending_login.get(session_key)
    if not pending:
        return False, "登录会话已过期，请重新输入手机号", None
    tc = pending["client"]
    phone = pending["phone"]
    phone_code_hash = pending["phone_code_hash"]
    try:
        if password:
            await tc.sign_in(password=password)
        else:
            await tc.sign_in(phone, code, phone_code_hash=phone_code_hash)
    except Exception as e:
        err_str = str(e)
        if "Two-steps verification" in err_str or "SessionPasswordNeeded" in err_str or "2FA" in err_str:
            return False, "need_2fa", None
        _pending_login.pop(session_key, None)
        await tc.disconnect()
        return False, f"登录失败: {e}", None

    me = await tc.get_me()
    account_id = me.id
    username = me.username or me.first_name or str(account_id)

    # 临时 session 验证成功，将其断开
    await tc.disconnect()
    _pending_login.pop(session_key, None)

    # 将临时 session 文件重命名为正式路径
    import os, glob
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

    # 用正式 session 文件创建 client 并启动
    real_tc = create_account_client(account_id)
    await real_tc.start()
    # 预加载对话列表，确保新 client 能解析游戏群等实体
    try:
        await real_tc.get_dialogs()
    except Exception:
        pass
    register_client(account_id, real_tc)

    # 注册事件处理器
    from .app import _register_event_handlers
    _register_event_handlers(real_tc)

    # 保存账号信息（不存手机号）
    set_account(account_id, {"session": f"account_{account_id}", "username": username})

    # 自动将该账号的 user_id 注册为 identity 并关联到此账号
    ok, message, canonical_id = await register_identity(account_id, source="ui_login")
    if canonical_id:
        set_identity_account(canonical_id, account_id)

    # hydrate profile
    try:
        from .control import hydrate_identity_profile
        entity = await real_tc.get_me()
        hydrate_identity_profile(entity)
    except Exception:
        pass

    save_state()
    await send_audit_log(f"🔑 新账号登录成功: @{username} (ID: {account_id})")
    return True, f"登录成功: @{username}", account_id


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
    actor_suffix = f"，操作者：{actor_id}" if actor_id is not None else ""
    await send_audit_log(f"🧩 已刷新话题列表：群聊 ID = {int(game_group_id)}，共 {len(topics)} 个话题{actor_suffix}。")
    return True, message, topics


async def ui_set_basic_config(game_group_id, game_bot_ids, game_topic_id, auto_delete_sent_messages, actor_id=None):
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
        return False, "bot ID 不能为空"
    parsed_bot_ids = []
    seen_bot_ids = set()
    for part in raw_bot_ids.replace("，", ",").split(","):
        item = part.strip()
        if not item:
            continue
        try:
            bot_id = int(item)
        except (TypeError, ValueError):
            return False, "bot ID 必须是整数，多个请用逗号分隔"
        if bot_id in seen_bot_ids:
            continue
        seen_bot_ids.add(bot_id)
        parsed_bot_ids.append(bot_id)
    if not parsed_bot_ids:
        return False, "至少需要一个 bot ID"

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
    set_game_group_id(group_id)
    set_game_bot_ids(parsed_bot_ids)
    set_game_topic_id(topic_id)
    set_auto_delete_sent_messages(auto_delete_enabled)
    save_state()
    actor_suffix = f"，操作者：{actor_id}" if actor_id is not None else ""
    display_topic = str(topic_id) if topic_id > 0 else "未启用"
    display_bots = ", ".join(str(bot_id) for bot_id in parsed_bot_ids)
    display_auto_delete = "开启" if auto_delete_enabled else "关闭"
    await send_audit_log(f"🧩 已更新基础配置：游戏群聊 ID = {group_id}，bot ID = {display_bots}，话题 ID = {display_topic}，自动删消息 = {display_auto_delete}{actor_suffix}。")
    return True, f"已更新基础配置：群聊 {group_id} ｜ bot {display_bots} ｜ 话题 {display_topic} ｜ 自动删消息 {display_auto_delete}"


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
            writer.close()
            await writer.wait_closed()
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

            if path == "/":
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
                    ok, message = await ui_set_basic_config(payload.get("game_group_id"), payload.get("game_bot_ids"), payload.get("game_topic_id"), payload.get("auto_delete_sent_messages"), actor_id=(session or {}).get("sender_id"))
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
                    if send_as_id_raw in {None, ""}:
                        body = _make_json_payload(False, error="缺少 send_as_id 参数")
                        _write_response(
                            writer,
                            "HTTP/1.1 400 Bad Request",
                            body,
                            content_type="application/json; charset=utf-8",
                            extra_headers=auth_headers,
                        )
                    else:
                        ok, message, canonical_id = await ui_add_identity(send_as_id_raw, actor_id=(session or {}).get("sender_id"))
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
                    ok, message, phone_code_hash = await ui_account_login_start(phone, session_key)
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
    except Exception as e:
        traceback.print_exc()
        _write_response(writer, "HTTP/1.1 500 Internal Server Error", f"Internal Server Error\n{e}\n", content_type="text/plain; charset=utf-8")
    finally:
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        if peer:
            print(f"ui request: {peer} {method or '-'} {path or '-'}")


async def start_ui_server():
    global _ui_server
    if _ui_server is not None:
        return _ui_server
    _ui_server = await asyncio.start_server(handle_ui_http, UI_HOST, UI_PORT)
    sockets = _ui_server.sockets or []
    bind_text = ", ".join(str(sock.getsockname()) for sock in sockets) or f"{UI_HOST}:{UI_PORT}"
    await send_audit_log(f"🖥️ UI 控制台已启动：{bind_text}")
    return _ui_server


__all__ = [
    "build_toggle_query",
    "get_identity_ui_snapshot",
    "get_ui_snapshot",
    "handle_ui_http",
    "html_escape",
    "html_pre",
    "render_ui_page",
    "start_ui_server",
    "ui_add_identity",
    "ui_refresh_forum_topics",
    "ui_refresh_identity_info",
    "ui_set_basic_config",
    "ui_set_identity_enabled",
    "ui_set_module_enabled",
    "ui_set_module_window",
    "ui_set_pet_name",
]
