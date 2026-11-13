"""Avatar discovery, registration and confirmed soul sessions on the owning account.

Uses the upstream switch replies and quiet windows, adapted to the local send
queue. A missing switch receipt never authorizes another avatar command.
"""
import asyncio
import copy
import hashlib
import re
import time

from .activity_occupancy import get_activity_block_reason
from .persistence import mark_dirty, save_state
from .state import (ensure_identity_registered, get_avatar_entry, get_avatar_owner_id, get_expedition_run_state,
                    get_global_enabled, get_identity_account, get_identity_enabled, get_identity_ids, get_identity_state,
                    has_identity, is_avatar_identity, set_identity_account, set_identity_enabled, update_send_as_profile)

_LOCKS = {}
_WAITERS = {}
_EARLY = {}
_TASKS = {}
_SESSIONS = {}
_ECHOES = {}
RE_AVATAR = re.compile(r"切换成功[！!]?\s*你的神念已附着在\s*【([^】]+)】\s*之上")


def records():
    return get_expedition_run_state().setdefault("avatars", {})


def soul(owner):
    return records().setdefault("souls", {}).setdefault(str(owner), {"mode": "main"})


def save():
    mark_dirty()
    if save_state() is False:
        raise RuntimeError("分身状态保存失败")


def classify_switch_reply(text):
    match = RE_AVATAR.search(text)
    if match:
        return "avatar", match[1].strip()
    if "神念重归主魂肉身" in text:
        return "main", "主魂"
    return "unknown", ""


def recovery_busy(identity):
    record = get_identity_state(identity)
    operation = record.get("explore_rift_rebirth_operation") or {}
    return (record.get("explore_rift_rebirth_required") and not record.get("explore_rift_manual_required")
            and record.get("explore_rift_nascent_escape_weak_until", 0) <= time.time()
            and (operation.get("status") in {"sending", "sent", "options", "unknown"}
                 or record.get("explore_rift_rebirth_phase") in {"requesting", "selecting", "blind_selecting"}))


def switch_block(owner):
    identities = [owner] + [i for i in get_identity_ids() if get_avatar_owner_id(i) == owner]
    for identity in identities:
        if recovery_busy(identity):
            return "夺舍恢复尚未结束"
        reason = get_activity_block_reason(identity)
        if reason:
            return reason
        if get_identity_state(identity).get("pending_tasks"):
            return "原身份仍有命令等待回执"
    return ""


def ready(identity):
    owner = get_avatar_owner_id(identity)
    if not owner:
        return False
    status = records().get("souls", {}).get(str(owner), {"mode": "main"})
    return (status.get("mode") == "avatar" and status.get("source") == "program"
            and status.get("avatar_id") == identity and time.time() >= status.get("settle_until", 0)
            and (time.time() - status.get("confirmed_at", 0) <= 120 or recovery_busy(identity)))


def send_block_reason(identity, command):
    owner = get_avatar_owner_id(identity) or identity
    status = records().get("souls", {}).get(str(owner), {"mode": "main"})
    if command.startswith(".切换 "):
        return "" if not switch_block(owner) else "副本或命令回执未结束，暂缓切换"
    if status.get("settle_until", 0) > time.time():
        return "切换后的静默窗口"
    if is_avatar_identity(identity):
        return "" if ready(identity) else "分身神念未获确认"
    return "主魂神念不在本体" if status.get("mode") != "main" else ""


async def _switch(owner, target, avatar_id=0):
    from .runtime import send_game_command
    status = soul(owner)
    status.update(mode="switching", source="program", target=target, avatar_id=avatar_id,
                  started_at=time.time(), last_error="")
    save()
    future = asyncio.get_running_loop().create_future()
    _WAITERS[owner] = {"future": future, "message_id": 0, "chat_id": 0, "target": target}
    command = ".切换 " + target
    _ECHOES[(owner, command)] = time.time() + 60
    try:
        message = await send_game_command(command, track=False, send_as_id=owner, max_retry=0,
            source_module="分身切换", queue_timeout=30, operation_check=lambda: (
                get_global_enabled() and has_identity(owner) and soul(owner) is status
                and status.get("mode") == "switching" and not switch_block(owner)))
        if not getattr(message, "id", 0):
            status.update(mode="unknown", next_return_at=time.time() + 60, last_error="切换发送未确认，停止分身动作")
            save()
            return False
        waiter = _WAITERS[owner]
        waiter.update(message_id=message.id, chat_id=message.chat_id)
        early = _EARLY.pop((message.chat_id, message.id), None)
        if early and not future.done():
            future.set_result(early)
        result, name = await asyncio.wait_for(asyncio.shield(future), timeout=15)
        if result == "main" and target == "主魂" or result == "avatar" and name == target:
            if result == "avatar":
                _SESSIONS.pop(avatar_id, None)
            status.update(mode=result, source="program", confirmed_at=time.time(), settle_until=time.time() + 8,
                          avatar_id=avatar_id if result == "avatar" else 0, last_error="")
            save()
            await asyncio.sleep(8)
            return True
        status.update(mode="unknown", next_return_at=time.time() + 60, last_error="切换回执目标不符")
    except (Exception, asyncio.CancelledError) as exc:
        status.update(mode="unknown", next_return_at=time.time() + 60, last_error=type(exc).__name__)
        save()
        if isinstance(exc, asyncio.CancelledError):
            raise
    finally:
        _WAITERS.pop(owner, None)
        save()
    return False


async def ensure_avatar_soul(identity):
    if ready(identity):
        return True
    entry = get_avatar_entry(identity)
    owner = entry.get("owner_id")
    if not owner or not get_global_enabled() or not get_identity_enabled(identity) or switch_block(owner):
        return False
    lock = _LOCKS.setdefault(owner, asyncio.Lock())
    if lock.locked():
        return False
    async with lock:
        if ready(identity):
            return True
        status = soul(owner)
        if status.get("mode") != "main":
            return False
        if not get_identity_enabled(owner):
            return False
        return await _switch(owner, entry["dao_name"], identity)


def note_send(identity, message=None):
    if not is_avatar_identity(identity):
        return
    session = _SESSIONS.setdefault(identity, {})
    if message is None:
        session.update(last_send=time.time(), reply_seen=False)
    else:
        session["message_id"] = getattr(message, "id", 0)


def note_reply(identity, now):
    if is_avatar_identity(identity):
        session = _SESSIONS.setdefault(identity, {})
        session.update(last_reply=now, reply_seen=True)


async def game_event(event, **_kwargs):
    from .state import get_game_bot_ids
    if (getattr(event, "sender_id", 0) not in get_game_bot_ids() or getattr(event, "forwarded", False)
            or getattr(event, "fwd_from", None)):
        return False
    result = classify_switch_reply(str(getattr(event, "raw_text", "") or ""))
    if result[0] == "unknown":
        return False
    reply = int(getattr(event, "reply_to_msg_id", 0) or getattr(getattr(event, "reply_to", None), "reply_to_msg_id", 0) or 0)
    if not reply:
        return False
    for owner, waiter in _WAITERS.items():
        if waiter["message_id"] == reply and waiter["chat_id"] == event.chat_id:
            if not waiter["future"].done():
                waiter["future"].set_result(result)
            return True
    for status in records().get("souls", {}).values():
        if status.get("source") == "manual" and status.get("command_id") == reply and status.get("chat_id") == event.chat_id:
            status.update(mode=result[0], confirmed_at=event.date.timestamp(), settle_until=time.time() + 8)
            save()
            return True
    if _WAITERS:
        _EARLY[(event.chat_id, reply)] = result
        while len(_EARLY) > 50:
            _EARLY.pop(next(iter(_EARLY)))
    return False


def observe_own_switch(event):
    text = str(getattr(event, "raw_text", "") or "").strip()
    owner = int(getattr(event, "sender_id", 0) or 0)
    if not text.startswith(".切换 ") or not has_identity(owner) or get_identity_account(owner) != owner:
        return
    if _ECHOES.get((owner, text), 0) >= time.time():
        return
    soul(owner).update(mode="unknown", source="manual", command_id=event.id, chat_id=event.chat_id,
                       started_at=time.time(), target=text[4:].strip())
    save()


async def return_to_main(owner):
    if not has_identity(owner) or get_identity_account(owner) != owner or is_avatar_identity(owner) or switch_block(owner):
        return False
    lock = _LOCKS.setdefault(owner, asyncio.Lock())
    if lock.locked():
        return False
    async with lock:
        return await _switch(owner, "主魂")


async def supervisor(now):
    if not get_global_enabled():
        return
    for owner_key, status in records().get("souls", {}).items():
        owner = int(owner_key)
        if status.get("mode") not in {"avatar", "unknown"} or status.get("source") != "program" or owner in _TASKS:
            continue
        identity = status.get("avatar_id")
        session = _SESSIONS.get(identity, {})
        done = session.get("reply_seen") and now >= session.get("last_reply", 0) + 10
        expired = now >= max(status.get("confirmed_at", now), session.get("last_send", 0)) + 120
        recover_main = status.get("mode") == "unknown" and now >= status.get("next_return_at", now + 1)
        if (done or expired or recover_main) and not switch_block(owner):
            from .runtime import track_background_task
            task = track_background_task(asyncio.create_task(return_to_main(owner)))
            _TASKS[owner] = task
            task.add_done_callback(lambda _task, key=owner: _TASKS.pop(key, None))


def classify_choices(raw, owner_player_id):
    identity = raw.get("identity") or {}
    entries = []
    for choice in identity.get("choices", []):
        if not isinstance(choice, dict):
            continue
        source, label = str(choice.get("source") or ""), str(choice.get("sourceLabel") or "")
        raw_player = choice.get("playerId")
        if isinstance(raw_player, bool) or not re.fullmatch(r"-?[0-9]+", str(raw_player)):
            continue
        player = int(raw_player)
        if player == owner_player_id or player <= -1000000000000 or source == "channel" or "频道" in label:
            continue
        if source not in {"avatar", "clone", "incarnation"} and not any(word in label for word in ("分身", "化身")):
            continue
        parent = choice.get("ownerPlayerId") or choice.get("parentPlayerId") or choice.get("mainPlayerId")
        if parent is not None and str(parent) != str(owner_player_id):
            continue
        names = [str(choice.get(k) or "").strip() for k in ("daoName", "daohao", "displayName", "name", "label", "nickname")]
        name = next((n for n in names if re.search(r"[一-鿿]", n)), next((n for n in names if n), ""))
        if name and len(name) <= 100 and not re.search(r"[\r\n]", name):
            username = next((str(choice.get(k) or "").strip().lstrip("@") for k in ("username", "userName", "tgUsername") if choice.get(k)), "")
            entries.append({"player_id": player, "dao_name": name, "username": username, "source": source, "source_label": label})
    return entries


async def scan_account_avatars(owner):
    from .features import cave_treasure_runtime as cave
    from .features.changfeng_trial import _entry_url
    if not has_identity(owner) or get_identity_account(owner) != owner or is_avatar_identity(owner):
        return False, "请选择已登记的本人账号", []
    lock = cave._public_entry_lock(owner)
    if lock.locked() or switch_block(owner):
        return False, "本人账号仍有活动或入口操作", []
    token, url, error = cave._parse_public_cave_entry_url(_entry_url())
    if error:
        return False, "洞府公共入口不可用", []
    async with lock:
        result = await cave._load_cave_public_identity_session(owner, token, url, now=time.time(),
            capture_source=f"avatar_scan:{owner}", include_details=False,
            operation_check=lambda: has_identity(owner) and get_identity_account(owner) == owner)
    raw = (((result.get("result") or {}).get("data") or {}).get("raw") or {})
    if not result.get("ok") or not isinstance(raw.get("identity"), dict):
        return False, "洞府未返回可核对的身份列表", []
    entries = classify_choices(raw, result.get("player_id"))
    records().setdefault("scans", {})[str(owner)] = {"at": time.time(), "entries": entries}
    save()
    return True, f"发现 {len(entries)} 个分身", entries


def register_avatar_identity(owner, player_id):
    scan = records().get("scans", {}).get(str(owner), {})
    entry = next((e for e in scan.get("entries", []) if e["player_id"] == player_id), None)
    if not entry or time.time() - scan.get("at", 0) > 86400 or not has_identity(owner) or get_identity_account(owner) != owner:
        return False, "请先扫描并选择该账号真实分身"
    identity = 5_000_000_000_000_000 + int(hashlib.sha256(f"{owner}:{player_id}".encode()).hexdigest()[:15], 16) % 3_000_000_000_000_000
    existing = get_avatar_entry(identity)
    if existing and (existing.get("owner_id"), existing.get("player_id")) != (owner, player_id):
        return False, "分身身份冲突，停止注册"
    if not existing:
        if has_identity(identity):
            return False, "身份编号已占用"
        ensure_identity_registered(identity)
        set_identity_account(identity, owner)
        for key in get_identity_state(identity):
            if key.endswith("_enabled"):
                get_identity_state(identity)[key] = False
        set_identity_enabled(identity, False)
    records().setdefault("entries", {})[str(identity)] = {**entry, "owner_id": owner, "identity_id": identity}
    update_send_as_profile(identity, username=entry.get("username") or entry["dao_name"], label=entry["dao_name"], daohao=entry["dao_name"])
    save()
    return True, f"已登记分身 {entry['dao_name']}，可在身份设置中启用"


async def refresh_avatar_profile(identity):
    """Read this avatar's selected HTTP profile using its owner's credentials."""
    from .features import cave_treasure_runtime as cave
    from .features.changfeng_trial import _entry_url
    from .features.miniapp_common import MiniAppIdentityOwner
    from .profile_observation import apply_profile_observation
    from .state import REALM_SORT_ORDER, get_send_as_profile, is_cave_public_identity_available

    entry = copy.deepcopy(get_avatar_entry(identity))
    owner = entry.get("owner_id")
    captured = MiniAppIdentityOwner.capture(identity)
    account = MiniAppIdentityOwner.capture(owner)

    def allowed():
        return (captured is not None and captured.is_current() and account is not None and account.is_current()
                and get_identity_account(owner) == owner and is_cave_public_identity_available(owner)
                and entry == get_avatar_entry(identity) and not switch_block(owner))

    if not owner or not allowed():
        return False, "分身所属账号不可用或仍有活动"
    lock = cave._public_entry_lock(owner)
    if lock.locked():
        return False, "本人账号仍有入口操作"
    token, url, error = cave._parse_public_cave_entry_url(_entry_url())
    if error:
        return False, "洞府公共入口不可用"
    async with lock:
        now = time.time()
        session = await cave._load_cave_public_identity_session(owner, token, url, now=now,
            capture_source=f"avatar_profile:{identity}", include_details=False, operation_check=allowed)
        if not session.get("ok") or not allowed():
            return False, "洞府所属账号会话未确认"
        result = await cave.run_cave_dwelling_start_production_flow(owner, token=token, webview_url=url,
            player_id=entry["player_id"], init_data=session["init_data"], operation_check=allowed,
            capture_sink=cave._capture_store(now), capture_source=f"avatar_profile:{identity}:selected")
        overview = ((result.get("data") or {}).get("overview") or {})
        realm = str(overview.get("cultivation_level") or "").strip()
        if (not result.get("ok") or not allowed() or realm not in REALM_SORT_ORDER
                or cave._selected_player_error(overview, identity)):
            return False, "未确认准确分身资料，保留原资料"
        changes = {"realm": realm, "sect_name": str(overview.get("sect_name") or "").strip()}
        daohao = str(overview.get("dao_name") or "").strip()
        if daohao:
            changes["daohao"] = daohao
        before_profile = copy.deepcopy(get_send_as_profile(identity))
        before_clocks = copy.deepcopy(get_identity_state(identity).get("identity_profile_observed_at", {}))
        accepted = apply_profile_observation(identity, changes, int(now), evidence={"source": "api", "requested_at": now})
        try:
            if accepted is None:
                return False, "分身资料证据无效"
            save()
        except Exception:
            update_send_as_profile(identity, **before_profile)
            get_identity_state(identity)["identity_profile_observed_at"] = before_clocks
            return False, "分身资料保存失败"
        return True, f"已读取分身资料：{daohao or entry['dao_name']}，{realm}，{changes['sect_name'] or '无宗门'}"


def reset_on_startup():
    for status in records().get("souls", {}).values():
        if status.get("mode") != "main":
            status.update(mode="unknown", next_return_at=time.time() + 60, last_error="重启后神念位置需核对，等待收回主魂确认")
            mark_dirty()
