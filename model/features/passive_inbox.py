import json
import os
import tempfile
import time

from ..config import STATE_DIR
from ..persistence import save_state
from ..state import get_identity_ids, get_send_as_profile, get_send_as_tags, state, use_identity
from ..timing import get_checkin_day_key, get_day_key, has_wait_time, parse_wait_time
from . import checkin as checkin_mod
from . import concubine as concubine_mod
from . import pet as pet_mod
from . import second_soul as second_soul_mod
from . import small_world as small_world_mod
from . import stargazer as stargazer_mod
from . import storage_bag as storage_bag_mod
from . import tianti as tianti_mod
from . import tower as tower_mod
from . import tree as tree_mod
from . import wild_training as wild_training_mod


PASSIVE_INBOX_RECENT_LIMIT = 20
PASSIVE_INBOX_STATS_FILE = os.path.join(STATE_DIR, "passive_inbox_stats.json")
_PASSIVE_STATS_DEFAULT = {
    "total": 0,
    "changed": 0,
    "skipped": 0,
    "modules": {},
    "skip_reasons": {},
    "recent": [],
}
_passive_stats = dict(_PASSIVE_STATS_DEFAULT)


def _load_passive_stats():
    global _passive_stats
    try:
        with open(PASSIVE_INBOX_STATS_FILE, "r", encoding="utf-8") as fp:
            raw = json.load(fp)
    except (OSError, ValueError, TypeError):
        return
    if not isinstance(raw, dict):
        return
    loaded = dict(_PASSIVE_STATS_DEFAULT)
    for key in ("total", "changed", "skipped"):
        try:
            loaded[key] = int(raw.get(key, 0) or 0)
        except (TypeError, ValueError):
            loaded[key] = 0
    loaded["modules"] = raw.get("modules") if isinstance(raw.get("modules"), dict) else {}
    loaded["skip_reasons"] = raw.get("skip_reasons") if isinstance(raw.get("skip_reasons"), dict) else {}
    loaded["recent"] = raw.get("recent") if isinstance(raw.get("recent"), list) else []
    loaded["recent"] = loaded["recent"][-PASSIVE_INBOX_RECENT_LIMIT:]
    _passive_stats = loaded


def _save_passive_stats():
    os.makedirs(STATE_DIR, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".passive_inbox_stats.", suffix=".tmp", dir=STATE_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump(get_passive_inbox_snapshot(), fp, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp_path, PASSIVE_INBOX_STATS_FILE)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass


_load_passive_stats()


def _bump_counter(bucket, key, amount=1):
    normalized = str(key or "unknown").strip() or "unknown"
    bucket[normalized] = int(bucket.get(normalized, 0) or 0) + int(amount or 1)


def _record_passive_event(kind, *, module="", identity_id=0, reason="", summary=""):
    _passive_stats["total"] = int(_passive_stats.get("total", 0) or 0) + 1
    if kind == "changed":
        _passive_stats["changed"] = int(_passive_stats.get("changed", 0) or 0) + 1
        _bump_counter(_passive_stats["modules"], module or "unknown")
    else:
        _passive_stats["skipped"] = int(_passive_stats.get("skipped", 0) or 0) + 1
        _bump_counter(_passive_stats["skip_reasons"], reason or "unknown")

    recent = _passive_stats.setdefault("recent", [])
    recent.append({
        "ts": float(time.time()),
        "kind": str(kind or ""),
        "module": str(module or ""),
        "identity_id": int(identity_id or 0),
        "reason": str(reason or ""),
        "summary": str(summary or "")[:120],
    })
    del recent[:-PASSIVE_INBOX_RECENT_LIMIT]
    _save_passive_stats()


def record_passive_inbox_event(kind, *, module="", identity_id=0, reason="", summary=""):
    try:
        _record_passive_event(
            kind,
            module=module,
            identity_id=identity_id,
            reason=reason,
            summary=summary,
        )
    except Exception:
        return False
    return True


def get_passive_inbox_snapshot():
    return {
        "total": int(_passive_stats.get("total", 0) or 0),
        "changed": int(_passive_stats.get("changed", 0) or 0),
        "skipped": int(_passive_stats.get("skipped", 0) or 0),
        "modules": dict(_passive_stats.get("modules") or {}),
        "skip_reasons": dict(_passive_stats.get("skip_reasons") or {}),
        "recent": list(_passive_stats.get("recent") or []),
    }


def get_passive_inbox_status_text():
    snapshot = get_passive_inbox_snapshot()

    def format_map(items):
        if not items:
            return "无"
        ordered = sorted(items.items(), key=lambda pair: (-int(pair[1] or 0), str(pair[0])))
        return "、".join(f"{key}:{value}" for key, value in ordered[:8])

    lines = [
        "📥 消息盒子",
        f"- 总处理：{snapshot['total']}",
        f"- 成功更新：{snapshot['changed']}",
        f"- 跳过：{snapshot['skipped']}",
        f"- 命中模块：{format_map(snapshot.get('modules') or {})}",
        f"- 跳过原因：{format_map(snapshot.get('skip_reasons') or {})}",
    ]
    recent = snapshot.get("recent") or []
    if recent:
        lines.append("- 最近事件：")
        for item in recent[-8:]:
            kind = "更新" if item.get("kind") == "changed" else "跳过"
            subject = item.get("module") or item.get("reason") or "unknown"
            identity_id = int(item.get("identity_id") or 0)
            suffix = f"｜{identity_id}" if identity_id else ""
            summary = str(item.get("summary") or "").strip()
            lines.append(f"  {kind} {subject}{suffix}{'｜' + summary if summary else ''}")
    return "\n".join(lines)


def _normalize_tag(text):
    return str(text or "").strip().lstrip("@").casefold()


def _match_identity_by_owner_name(owner_name):
    owner_key = _normalize_tag(owner_name)
    if not owner_key:
        return None
    matched = []
    for identity_id in get_identity_ids():
        profile = get_send_as_profile(identity_id)
        candidates = [
            profile.get("daohao"),
            profile.get("label"),
            profile.get("username"),
            *get_send_as_tags(identity_id),
        ]
        if owner_key in {_normalize_tag(item) for item in candidates if str(item or "").strip()}:
            matched.append(identity_id)
    return matched[0] if len(matched) == 1 else None


def _match_identity_by_at_text(text):
    raw_text = str(text or "")
    matched = []
    for identity_id in get_identity_ids():
        tags = get_send_as_tags(identity_id) or []
        if any(tag and tag in raw_text for tag in tags):
            matched.append(identity_id)
    return matched[0] if len(matched) == 1 else None


def _identity_from_reply_context(reply_context):
    try:
        identity_id = int((reply_context or {}).get("send_as_id") or 0)
    except (TypeError, ValueError):
        identity_id = 0
    return identity_id if identity_id > 0 else None


def _family_from_reply_context(reply_context):
    return str((reply_context or {}).get("family") or "").strip()


def _apply_tianti_passive(text, now, family):
    raw_text = str(text or "")
    changed = False
    panel_payload = tianti_mod._parse_tianti_panel(raw_text)
    if panel_payload:
        changed = tianti_mod._apply_tianti_panel_payload(panel_payload, now=now) or changed
        tianti_mod._calc_tianti_wenxin_plan(now)
    # Wenxin replies are owned by the routed active handler. Replaying them here
    # duplicates the same success close-out when multiple clients see one reply.
    if family == "tianti_gangfeng":
        fail_match = tianti_mod.RE_TIANTI_GANGFENG_FAIL.search(raw_text)
        result_match = tianti_mod.RE_TIANTI_GANGFENG_RESULT.search(raw_text)
        if tianti_mod.RE_TIANTI_GANGFENG_PANEL.search(raw_text) and result_match:
            state["tianti_gangfeng_level"] = int(result_match.group(1) or 0)
            state["tianti_gangfeng_total"] = int(result_match.group(2) or 0)
            state["tianti_gangfeng_status"] = "已施展，下次登天阶成功率显著提高"
            tianti_mod._schedule_tianti_gangfeng_retry(now, persist=False)
            changed = True
        elif fail_match:
            wait_text = str(fail_match.group(1) or "").strip()
            wait_sec = parse_wait_time(wait_text) if has_wait_time(wait_text) else 0
            if wait_sec > 0:
                tianti_mod._schedule_tianti_gangfeng_retry(now, wait_sec=wait_sec, persist=False)
                changed = True
    climb_cost_match = tianti_mod.RE_TIANTI_CLIMB_COST.search(raw_text)
    climb_gain_match = tianti_mod.RE_TIANTI_CLIMB_GAIN.search(raw_text)
    climb_cycle_match = tianti_mod.RE_TIANTI_CLIMB_CYCLE.search(raw_text)
    climb_result_match = tianti_mod.RE_TIANTI_CLIMB_RESULT.search(raw_text)
    if climb_cost_match and climb_gain_match and climb_result_match:
        state["tianti_last_cost_xiuwei"] = int(climb_cost_match.group(1) or 0)
        state["tianti_last_gain_xiuwei"] = int(climb_gain_match.group(1) or 0)
        state["tianti_last_gain_contrib"] = int(climb_gain_match.group(2) or 0)
        if climb_cycle_match:
            state["tianti_cycle_count"] = int(climb_cycle_match.group(1) or 0)
        state["tianti_progress_current"] = int(climb_result_match.group(1) or 0)
        state["tianti_progress_total"] = int(climb_result_match.group(2) or 0)
        state["tianti_gangfeng_level"] = int(climb_result_match.group(3) or 0)
        state["tianti_gangfeng_total"] = int(climb_result_match.group(4) or 0)
        tianti_mod._schedule_tianti_climb_retry(now, persist=False)
        tianti_mod._calc_tianti_wenxin_plan(now)
        changed = True
    if changed:
        state["tianti_last_error"] = ""
    return changed


def _apply_second_soul_passive(text, now, family):
    raw_text = str(text or "")
    changed = False
    if second_soul_mod._is_second_soul_panel(raw_text):
        status, remain_sec = second_soul_mod._parse_status_field(raw_text)
        if status == "窍中温养":
            second_soul_mod._mark_ready_to_train(now)
            changed = True
        elif status == "修炼中":
            second_soul_mod._set_phase("cultivating")
            second_soul_mod._clear_heart_demon()
            second_soul_mod._clear_pending_msg_ids()
            state["next_second_soul_time"] = now + remain_sec + second_soul_mod.CD_BUFFER_SEC if remain_sec > 0 else now + second_soul_mod.SECOND_SOUL_RECHECK_MAX
            changed = True
        elif status == "受伤":
            second_soul_mod._set_phase("injured")
            second_soul_mod._clear_heart_demon()
            second_soul_mod._clear_pending_msg_ids()
            state["next_second_soul_time"] = now + remain_sec + second_soul_mod.CD_BUFFER_SEC if remain_sec > 0 else now + second_soul_mod.SECOND_SOUL_INJURED_NO_REMAIN_CD_SEC
            changed = True
        elif status == "心魔试炼中":
            second_soul_mod._set_phase("heart_demon_pending")
            state["second_soul_heart_demon_deadline"] = state.get("second_soul_heart_demon_deadline", 0) or now + second_soul_mod.SECOND_SOUL_HEART_DEMON_DEADLINE_SEC
            second_soul_mod._clear_pending_msg_ids()
            changed = True
    if "你的第二元神已开始闭关修炼" in raw_text and "24小时" in raw_text:
        second_soul_mod._set_phase("cultivating")
        state["next_second_soul_time"] = now + second_soul_mod.SECOND_SOUL_TRAIN_CD_SEC + second_soul_mod.CD_BUFFER_SEC
        state["second_soul_last_train_started_at"] = now
        second_soul_mod._clear_heart_demon()
        second_soul_mod._clear_pending_msg_ids()
        changed = True
    if changed:
        state["second_soul_last_error"] = ""
    return changed


def _apply_pet_passive(text, now, family):
    raw_text = str(text or "")
    changed = False
    if family == "pet" or pet_mod.RE_PET_TOUCH_SUCCESS.search(raw_text):
        if pet_mod.RE_PET_TOUCH_SUCCESS.search(raw_text):
            state["next_pet_time"] = float(now + pet_mod.PET_CD + pet_mod.CD_BUFFER_SEC)
            state["pet_last_error"] = ""
            changed = True
        elif has_wait_time(raw_text) and any(keyword in raw_text for keyword in pet_mod.PET_CD_HINT_KEYWORDS):
            state["next_pet_time"] = float(now + parse_wait_time(raw_text) + pet_mod.CD_BUFFER_SEC)
            state["pet_last_error"] = ""
            changed = True
    if family == "pet_warm" or pet_mod.RE_PET_WARM_SUCCESS.search(raw_text) or "后再行温养" in raw_text:
        if pet_mod.RE_PET_WARM_SUCCESS.search(raw_text):
            state["next_pet_warm_time"] = float(now + pet_mod.PET_WARM_CD + 180)
            state["pet_warm_last_error"] = ""
            changed = True
        elif has_wait_time(raw_text) and ("器灵方才吞纳过灵机" in raw_text or "后再行温养" in raw_text):
            state["next_pet_warm_time"] = float(now + parse_wait_time(raw_text) + pet_mod.CD_BUFFER_SEC)
            state["pet_warm_last_error"] = ""
            changed = True
    if family == "pet_trial" or pet_mod.RE_PET_TRIAL_SUCCESS.search(raw_text) or "后再启程" in raw_text:
        if pet_mod.RE_PET_TRIAL_SUCCESS.search(raw_text):
            state["next_pet_trial_time"] = float(now + pet_mod.PET_TRIAL_CD + pet_mod.CD_BUFFER_SEC)
            state["pet_trial_last_error"] = ""
            changed = True
        elif has_wait_time(raw_text) and ("器灵试炼刚结束不久" in raw_text or "后再启程" in raw_text):
            state["next_pet_trial_time"] = float(now + parse_wait_time(raw_text) + pet_mod.CD_BUFFER_SEC)
            state["pet_trial_last_error"] = ""
            changed = True
    return changed


def _apply_small_world_passive(text, now):
    panel = small_world_mod._parse_small_world_panel(text)
    if not panel or panel.get("realm_blocked"):
        return False
    state["small_world_last_panel_at"] = float(now)
    state["small_world_faith_value"] = int(panel.get("faith", 0) or 0)
    state["small_world_pending_incense"] = float(panel.get("pending_incense", 0) or 0)
    state["small_world_incense_stock"] = int(panel.get("stock", 0) or 0)
    if panel.get("has_wait"):
        state["next_small_world_time"] = float(now + int(panel.get("wait_sec", 0) or 0) + small_world_mod.CD_BUFFER_SEC)
        state["small_world_phase"] = "idle"
    elif panel.get("has_prayer"):
        state["small_world_phase"] = "idle"
        state["small_world_last_error"] = ""
    state["small_world_last_error"] = ""
    return True


def _apply_concubine_passive(text, now, family):
    raw_text = str(text or "")
    parsed = concubine_mod._parse_status_panel(raw_text, now)
    if parsed:
        concubine_mod._apply_status_snapshot(parsed, now)
        return True
    progress = concubine_mod._parse_fragment_progress(raw_text)
    changed = False
    if progress and ("入梦寻图" in raw_text or "虚天残图" in raw_text or "残图" in raw_text):
        state["concubine_fragment_count"] = progress[0]
        state["concubine_fragment_total"] = progress[1]
        state["concubine_dream_due_at"] = float(now + concubine_mod.CONCUBINE_DREAM_CD_SEC + concubine_mod.CD_BUFFER_SEC)
        state["concubine_last_error"] = ""
        changed = True
    if family == "concubine_tianji" and "【天机代卜链】" in raw_text:
        gua_match = concubine_mod.RE_TIANJI_GUA.search(raw_text)
        state["concubine_tianji_chain"] = gua_match.group("name").strip() if gua_match else ""
        state["concubine_tianji_due_at"] = float(now + concubine_mod.CONCUBINE_TIANJI_CD_SEC + concubine_mod.CD_BUFFER_SEC)
        state["concubine_tianji_chain_due_at"] = state["concubine_tianji_due_at"]
        state["concubine_tianji_last_error"] = ""
        changed = True
    if family == "concubine_heart" and "【坠魔心劫·结算】" in raw_text:
        state["concubine_heart_due_at"] = float(now + concubine_mod.CONCUBINE_HEART_CD_SEC + 20 * 60)
        state["concubine_heart_last_error"] = ""
        state["concubine_heart_prompt_msg_id"] = 0
        state["concubine_heart_round"] = 0
        state["concubine_heart_choice_prompt_msg_id"] = 0
        state["concubine_heart_choice_round"] = 0
        state["concubine_heart_choice_sent_at"] = 0
        changed = True
    return changed


def _is_tree_panel_text(text):
    raw_text = str(text or "")
    return "【落云宗 · 灵眼之树】" in raw_text or "落云宗·灵眼之树" in raw_text


def _is_tree_mature_broadcast(text):
    raw_text = str(text or "")
    return "🍎 灵果已完全成熟！ 采摘期开启！" in raw_text and "📊 天道榜单已定格！" in raw_text


def _apply_tree_passive(text, now, family):
    raw_text = str(text or "")
    changed = False
    is_panel = _is_tree_panel_text(raw_text)
    current_status_snapshot = "你的当前状态:" in raw_text or "你的当前状态：" in raw_text
    trusted_panel = family == "tree_panel" or (is_panel and current_status_snapshot and tree_mod._tree_panel_matches_current_identity(raw_text))

    if trusted_panel and is_panel:
        state["tree_bootstrap_check_needed"] = False
        state["tree_bootstrap_check_due_at"] = 0
        if "成熟采摘期" in raw_text or _is_tree_mature_broadcast(raw_text):
            state["is_maturing"] = True
            state["pending_irrigation"] = False
            state["next_irr_time"] = float(now + tree_mod.FREEZE_CD)
            if "你的当前状态: 已采摘" in raw_text or "你的当前状态：已采摘" in raw_text:
                state["is_harvested"] = True
                state["tree_harvest_inflight_until"] = 0
            elif state.get("is_harvested"):
                state["is_harvested"] = False
                state["tree_harvest_inflight_until"] = 0
            changed = True
        else:
            if state.get("is_maturing"):
                state["is_maturing"] = False
                state["tree_maturing_logged"] = False
                state["tree_harvest_followup_due_at"] = 0
                if float(state.get("next_irr_time", 0) or 0) > now + 24 * 3600:
                    state["next_irr_time"] = now
                changed = True
            if state.get("is_harvested"):
                state["is_harvested"] = False
                state["tree_harvest_inflight_until"] = 0
                changed = True
            if state.get("pending_irrigation") and not state.get("is_invading"):
                state["pending_irrigation"] = False
                state["next_irr_time"] = now
                changed = True

    is_irrigation_reply = family == "tree_panel"
    is_guard_reply = family == "tree_guard"
    if is_irrigation_reply and tree_mod._is_tree_irrigation_success(raw_text):
        tree_mod.reset_resource_shortage(tree_mod.TREE_IRRIGATION_RESOURCE_KEY)
        state["pending_irrigation"] = False
        changed = True
    if is_guard_reply and tree_mod._is_tree_guard_success(raw_text):
        tree_mod.reset_resource_shortage(tree_mod.TREE_GUARD_RESOURCE_KEY)
        changed = True

    if family in {"tree_panel", "tree_guard"} and has_wait_time(raw_text):
        wait_sec = parse_wait_time(raw_text)
        if wait_sec > 0:
            if family == "tree_guard" or "守山" in raw_text or "协同" in raw_text or "大阵注入灵力" in raw_text:
                tree_mod.reset_resource_shortage(tree_mod.TREE_GUARD_RESOURCE_KEY)
                state["next_guard_time"] = float(now + wait_sec + tree_mod.CD_BUFFER_SEC)
                changed = True
            elif family == "tree_panel" or "灌溉" in raw_text:
                tree_mod.reset_resource_shortage(tree_mod.TREE_IRRIGATION_RESOURCE_KEY)
                state["next_irr_time"] = float(now + wait_sec + tree_mod.CD_BUFFER_SEC)
                changed = True

    if _is_tree_mature_broadcast(raw_text):
        # Broadcasts are intentionally observational here. The active tree
        # handler owns all global harvest decisions to avoid passive-triggered chains.
        return changed
    return changed


def _apply_storage_bag_passive(text, now):
    parsed = storage_bag_mod.parse_storage_bag_reply(text)
    if not parsed:
        return False, None
    identity_id = storage_bag_mod.resolve_storage_bag_identity_id(parsed.get("owner"))
    if identity_id <= 0:
        return False, None
    records = storage_bag_mod.get_storage_bag_records()
    records[str(identity_id)] = {
        "identity_id": identity_id,
        "label": storage_bag_mod._get_storage_bag_identity_label(identity_id, parsed),
        "owner": parsed.get("owner") or "",
        "owner_username": parsed.get("owner_username") or "",
        "updated_at": float(now or 0),
        "updated_at_text": storage_bag_mod.fmt_abs_ts(float(now or 0)),
        "items": parsed.get("items") or {},
        "sections": parsed.get("sections") or {},
        "empty": bool(parsed.get("empty")),
    }
    storage_bag_mod.set_storage_bag_records(records)
    save_state()
    return True, identity_id


def _apply_wild_training_passive(text, now, family):
    raw_text = str(text or "").strip()
    if family != "wild_training" and not raw_text.startswith(wild_training_mod.WILD_TRAINING_TITLE):
        return False
    if has_wait_time(raw_text) and any(keyword in raw_text for keyword in wild_training_mod.WILD_TRAINING_CD_KEYWORDS):
        wait_sec = parse_wait_time(raw_text)
        state["next_wild_training_time"] = float(now + wait_sec + wild_training_mod.CD_BUFFER_SEC)
        state["wild_training_reply_to_msg_id"] = 0
        state["wild_training_reply_due_at"] = 0
        state["wild_training_retry_count"] = 0
        state["wild_training_last_result"] = "冷却中"
        state["wild_training_last_error"] = ""
        return True
    if wild_training_mod._is_start_notice(raw_text):
        state["wild_training_last_result"] = wild_training_mod._start_summary(raw_text)
        state["wild_training_last_error"] = ""
        if float(state.get("wild_training_reply_due_at", 0) or 0) <= now:
            state["wild_training_reply_due_at"] = float(now + wild_training_mod.WILD_TRAINING_REPLY_TIMEOUT_SEC)
        return True
    if not any(marker in raw_text for marker in wild_training_mod.WILD_TRAINING_RESULT_MARKERS):
        return False
    state["wild_training_reply_to_msg_id"] = 0
    state["wild_training_reply_due_at"] = 0
    state["wild_training_retry_count"] = 0
    state["wild_training_last_result"] = wild_training_mod._result_summary(raw_text)
    state["wild_training_last_error"] = ""
    wild_training_mod._schedule_next(now)
    return True


def _apply_tower_passive(text, now, family):
    if family != "tower":
        return False
    raw_text = str(text or "")
    if "【琉璃问心塔】" in raw_text or any(keyword in raw_text for keyword in tower_mod.TOWER_DONE_HINTS):
        tower_mod._mark_tower_done_today(now)
        return True
    if "闯塔" in raw_text or "塔" in raw_text:
        if float(state.get("next_tower_time", 0) or 0) <= now:
            tower_mod.schedule_next_tower(now, persist=False)
        return True
    return False


def _apply_checkin_passive(text, now, family):
    raw_text = str(text or "")
    if family == "checkin":
        if checkin_mod.is_no_sect_checkin_text(raw_text):
            return checkin_mod.disable_sect_modules_for_current_identity(now)
        day_key = get_checkin_day_key(now)
        state["last_checkin_done_day"] = day_key
        if float(state.get("next_checkin_time", 0) or 0) <= now or get_checkin_day_key(state.get("next_checkin_time", 0) or 0) == day_key:
            checkin_mod.schedule_next_checkin_after_completion(now, persist=False)
        state["last_checkin_msg_id"] = 0
        return "点卯成功" in raw_text or checkin_mod.is_checkin_already_done_text(raw_text) or "点卯" in raw_text
    if family == "sect_teach":
        day_key = get_checkin_day_key(now)
        if state["checkin_teach_day"] != day_key:
            checkin_mod.reset_checkin_daily_state(now)
        if "传功玉简已记录！" in raw_text:
            state["checkin_teach_count"] = min(3, int(state.get("checkin_teach_count", 0) or 0) + 1)
        if checkin_mod.is_sect_teach_already_done_text(raw_text) or state["checkin_teach_count"] >= 3:
            state["next_sect_teach_time"] = 0
            state["sect_teach_reply_to_msg_id"] = 0
        return "传功" in raw_text or "贡献" in raw_text or "宗门" in raw_text
    return False


def _apply_stargazer_passive(text, now, family):
    raw_text = str(text or "")
    changed = False
    followup_due_at = float(state.get("stargazer_followup_due_at", 0) or 0)
    if followup_due_at > now and str(state.get("stargazer_last_action") or "").startswith("queue_"):
        return False
    if family in {"stargazer_panel", "stargazer_sync"}:
        parsed = stargazer_mod._parse_stargazer_panel(raw_text)
        if not parsed:
            return False
        stargazer_mod._sync_stargazer_panel_state(parsed, now)
        if parsed.get("max_wait", 0) > 0:
            state["next_stargazer_panel_time"] = float(now + int(parsed.get("max_wait", 0) or 0) + stargazer_mod.CD_BUFFER_SEC)
            state["stargazer_last_action"] = "passive_waiting_panel"
        elif parsed.get("all_ready"):
            state["stargazer_last_action"] = "passive_all_ready"
        elif parsed.get("idle_slot_count", 0) > 0:
            state["stargazer_last_action"] = "passive_idle_slot"
        changed = True
    elif family == "stargazer_guide":
        if any(keyword in raw_text for keyword in stargazer_mod.STARGAZER_CD_HINT_KEYWORDS) and has_wait_time(raw_text):
            state["next_stargazer_panel_time"] = float(now + parse_wait_time(raw_text) + stargazer_mod.CD_BUFFER_SEC)
            state["stargazer_last_action"] = "passive_guide_cd"
            changed = True
        elif "牵引成功" in raw_text:
            state["stargazer_idle_slot_count"] = 0
            state["stargazer_last_action"] = "passive_guide_success"
            changed = True
    elif family == "stargazer_soothe":
        if any(keyword in raw_text for keyword in stargazer_mod.STARGAZER_CD_HINT_KEYWORDS) and has_wait_time(raw_text):
            state["next_stargazer_panel_time"] = float(now + parse_wait_time(raw_text) + stargazer_mod.CD_BUFFER_SEC)
            state["stargazer_last_action"] = "passive_soothe_cd"
            changed = True
        elif "安抚完成" in raw_text or stargazer_mod._is_stargazer_soothe_no_need(raw_text):
            state["stargazer_last_action"] = "passive_soothe_done"
            changed = True
    elif family == "stargazer_collect":
        if any(keyword in raw_text for keyword in stargazer_mod.STARGAZER_CD_HINT_KEYWORDS) and has_wait_time(raw_text):
            state["stargazer_collect_due_at"] = float(now + parse_wait_time(raw_text) + stargazer_mod.CD_BUFFER_SEC)
            state["stargazer_last_action"] = "passive_collect_cd"
            changed = True
        elif "收集完成" in raw_text:
            count = stargazer_mod._extract_stargazer_collected_slot_count(raw_text)
            state["stargazer_collect_due_at"] = 0
            state["stargazer_busy_until"] = 0
            state["stargazer_ready_slot_count"] = max(0, int(state.get("stargazer_ready_slot_count", 0) or 0) - count)
            state["stargazer_last_action"] = "passive_collect_success"
            changed = True
    return changed


def _looks_like_supported_passive(text, family):
    raw_text = str(text or "")
    family = str(family or "").strip()
    if (
        family.startswith("tianti_")
        or family.startswith("second_soul")
        or family.startswith("concubine_")
        or family.startswith("stargazer_")
        or family in {
            "pet",
            "pet_warm",
            "pet_trial",
            "tree_panel",
            "tree_guard",
            "tree_harvest",
            "wild_training",
            "checkin",
            "sect_teach",
            "tower",
        }
    ):
        return True
    if (
        "【第二元神归位】" in raw_text
        or second_soul_mod._is_second_soul_panel(raw_text)
        or small_world_mod.RE_SMALL_WORLD_PANEL.search(raw_text)
        or tianti_mod.RE_TIANTI_PANEL.search(raw_text)
        or _is_tree_panel_text(raw_text)
        or _is_tree_mature_broadcast(raw_text)
        or raw_text.startswith(wild_training_mod.WILD_TRAINING_TITLE)
    ):
        return True
    return False


async def handle_passive_module_card(text, now=None, reply_context=None):
    now = float(now or time.time())
    raw_text = str(text or "")
    family = _family_from_reply_context(reply_context)
    target_id = _identity_from_reply_context(reply_context)

    storage_changed, storage_identity_id = _apply_storage_bag_passive(raw_text, now)
    if storage_changed:
        _record_passive_event(
            "changed",
            module="storage_bag",
            identity_id=storage_identity_id or 0,
            summary="storage_bag",
        )
        return True

    if target_id is None and "【第二元神归位】" in raw_text:
        target_id = _match_identity_by_at_text(raw_text)
    if target_id is None:
        owner_match = small_world_mod.RE_SMALL_WORLD_PANEL.search(raw_text)
        if owner_match:
            target_id = _match_identity_by_owner_name(owner_match.group("owner"))

    if target_id is None:
        if _looks_like_supported_passive(raw_text, family):
            _record_passive_event("skipped", reason="no_identity")
        return False

    changed = False
    changed_modules = []
    with use_identity(target_id):
        if family.startswith("tianti_") or tianti_mod.RE_TIANTI_PANEL.search(raw_text):
            module_changed = _apply_tianti_passive(raw_text, now, family)
            if module_changed:
                changed_modules.append("tianti")
            changed = module_changed or changed
        if family.startswith("second_soul") or second_soul_mod._is_second_soul_panel(raw_text):
            module_changed = _apply_second_soul_passive(raw_text, now, family)
            if module_changed:
                changed_modules.append("second_soul")
            changed = module_changed or changed
        if family in {"pet", "pet_warm", "pet_trial"}:
            module_changed = _apply_pet_passive(raw_text, now, family)
            if module_changed:
                changed_modules.append(family)
            changed = module_changed or changed
        if small_world_mod.RE_SMALL_WORLD_PANEL.search(raw_text):
            module_changed = _apply_small_world_passive(raw_text, now)
            if module_changed:
                changed_modules.append("small_world")
            changed = module_changed or changed
        if family.startswith("concubine_"):
            module_changed = _apply_concubine_passive(raw_text, now, family)
            if module_changed:
                changed_modules.append("concubine")
            changed = module_changed or changed
        if family in {"tree_panel", "tree_guard", "tree_harvest"} or _is_tree_panel_text(raw_text) or _is_tree_mature_broadcast(raw_text):
            module_changed = _apply_tree_passive(raw_text, now, family)
            if module_changed:
                changed_modules.append("tree")
            changed = module_changed or changed
        if family == "wild_training" or raw_text.startswith(wild_training_mod.WILD_TRAINING_TITLE):
            module_changed = _apply_wild_training_passive(raw_text, now, family)
            if module_changed:
                changed_modules.append("wild_training")
            changed = module_changed or changed
        if family in {"checkin", "sect_teach"}:
            module_changed = _apply_checkin_passive(raw_text, now, family)
            if module_changed:
                changed_modules.append(family)
            changed = module_changed or changed
        if family == "tower":
            module_changed = _apply_tower_passive(raw_text, now, family)
            if module_changed:
                changed_modules.append("tower")
            changed = module_changed or changed
        if family.startswith("stargazer_"):
            module_changed = _apply_stargazer_passive(raw_text, now, family)
            if module_changed:
                changed_modules.append(family)
            changed = module_changed or changed
        if changed:
            save_state()
    if changed:
        _record_passive_event(
            "changed",
            module=",".join(changed_modules) or "unknown",
            identity_id=target_id,
            summary=family or "passive",
        )
    else:
        _record_passive_event("skipped", identity_id=target_id, reason="no_change", summary=family or "passive")
    return changed


__all__ = [
    "get_passive_inbox_snapshot",
    "get_passive_inbox_status_text",
    "handle_passive_module_card",
    "record_passive_inbox_event",
]
