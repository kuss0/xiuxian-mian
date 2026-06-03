import copy
import re
import time

from ..config import (
    CMD_YINLUO_BANNER,
    CMD_YINLUO_BLOOD_FOREST,
    CMD_YINLUO_COLLECT,
    CMD_YINLUO_CONVERT,
    CMD_YINLUO_DEMON_SUMMON,
)
from ..persistence import save_state
from ..runtime import send_game_command
from ..state import REALM_SORT_INDEX, get_send_as_profile, infer_realm_from_xiuwei_max, state, use_identity
from ..timing import fmt_abs_ts, fmt_remaining, has_wait_time, parse_wait_time


YINLUO_TIME_BUFFER_SEC = 60
YINLUO_OBSERVATION_STALE_SEC = 24 * 3600
YINLUO_AUTO_STATUS_BACKOFF_SEC = 6 * 3600
YINLUO_AUTO_BLOCK_BACKOFF_SEC = 60 * 60
YINLUO_AUTO_SEND_FAIL_BACKOFF_SEC = 30 * 60
YINLUO_AUTO_CHAIN_STEP_SEC = 2 * 60
YINLUO_DEMON_SUMMON_OBSERVED_CD_SEC = 4 * 3600
YINLUO_BLOOD_FOREST_OBSERVED_CD_SEC = 4 * 3600
YINLUO_DEMON_SUMMON_MIN_REALM = "结丹初期"

RE_BANNER_TITLE = re.compile(r"【(?P<owner>[^】]+)的阴罗幡】")
RE_SHA_POOL = re.compile(r"煞气池[:：]\s*(?P<current>\d+)\s*/\s*(?P<max>\d+)\s*\((?P<pct>\d+)%\)")
RE_SOUL_TOTAL = re.compile(r"幡魂总炼化[:：]\s*(?P<value>\d+)\s*缕")
RE_BATTLE_BONUS = re.compile(r"武器战力加成[:：]\s*\+(?P<value>\d+)%")
RE_SOUL_STOCK = re.compile(r"^\s*-\s*(?P<name>[^:：]+)[:：]\s*(?P<count>\d+)\s*缕")
RE_SHA_GAIN = re.compile(r"煞气池增加了\s*(?P<gain>\d+)\s*点")
RE_EXTRA_SHA_GAIN = re.compile(r"额外获得了\s*(?P<gain>\d+)\s*点精纯煞气")
RE_COLLECT_SLOT = re.compile(r"你从\s*(?P<count>\d+)\s*个炼化槽中获得了[:：]\s*(?P<items>.+)")
RE_BONUS_GAIN = re.compile(r"因【阴罗宗】灵脉加持，你额外获得了\s*(?P<gain>\d+)\s*点修为")


def _default_yinluo_observation():
    return {
        "last_observed_at": 0,
        "last_action": "",
        "last_result": "",
        "last_summary": "",
        "last_error": "",
        "next_demon_summon_time": 0,
        "next_blood_forest_time": 0,
        "banner_owner": "",
        "banner_name": "",
        "banner_rank": "",
        "sha_current": 0,
        "sha_max": 0,
        "sha_percent": 0,
        "banner_status": "",
        "main_soul_path": "",
        "soul_total": 0,
        "battle_bonus_percent": 0,
        "soul_stocks": {},
        "ready_slots": 0,
        "refining_slots": 0,
        "empty_slots": 0,
        "last_resource": "",
        "last_sha_gain": 0,
        "last_extra_sha_gain": 0,
        "last_bonus_gain": 0,
        "last_sample_gap": "夺舍 @目标 成功/冷却文案未收录",
        "auto_next_time": 0,
        "auto_last_action": "",
        "auto_last_error": "",
        "recent": [],
    }


def normalize_yinluo_observation(value=None):
    observed = copy.deepcopy(_default_yinluo_observation())
    if isinstance(value, dict):
        observed.update(value)
    if not isinstance(observed.get("soul_stocks"), dict):
        observed["soul_stocks"] = {}
    if not isinstance(observed.get("recent"), list):
        observed["recent"] = []
    observed["recent"] = [item for item in observed.get("recent", []) if isinstance(item, dict)][-8:]
    for key in ("last_observed_at", "next_demon_summon_time", "next_blood_forest_time", "auto_next_time"):
        try:
            observed[key] = float(observed.get(key, 0) or 0)
        except (TypeError, ValueError):
            observed[key] = 0
    for key in ("sha_current", "sha_max", "sha_percent", "soul_total", "battle_bonus_percent", "ready_slots", "refining_slots", "empty_slots", "last_sha_gain", "last_extra_sha_gain", "last_bonus_gain"):
        try:
            observed[key] = int(observed.get(key, 0) or 0)
        except (TypeError, ValueError):
            observed[key] = 0
    return observed


def _short_summary(text, limit=80):
    raw_text = " / ".join(part.strip() for part in str(text or "").splitlines() if part.strip())
    return raw_text[: int(limit or 80)]


def _wait_until(text, now):
    if has_wait_time(text):
        wait_sec = parse_wait_time(text)
        if wait_sec > 0:
            return float(now + wait_sec + YINLUO_TIME_BUFFER_SEC)
    return 0


def _parse_non_member(raw_text):
    if "你并非阴罗宗弟子" not in raw_text:
        return None
    if "杀伐之术" in raw_text:
        action = "血洗山林"
        error = "不懂此等杀伐之术"
    elif "无法沟通魔域" in raw_text:
        action = "召唤魔影"
        error = "无法沟通魔域"
    elif "夺心魔功" in raw_text:
        action = "魔染红尘"
        error = "无法领悟夺心魔功"
    elif "阴罗幡" in raw_text:
        action = "阴罗幡"
        error = "无法催动阴罗幡"
    elif "转化魔功" in raw_text:
        action = "化功为煞"
        error = "不懂此等转化魔功"
    else:
        action = "阴罗宗"
        error = "非阴罗宗弟子"
    return {
        "action": action,
        "result": "not_member",
        "summary": f"{action}失败：非阴罗宗弟子",
        "last_error": error,
    }


def looks_like_yinluo_text(text):
    raw_text = str(text or "").strip()
    if not raw_text or raw_text.startswith("."):
        return False
    if "夺舍重生" in raw_text and "阴罗宗" not in raw_text:
        return False
    if "阴罗幡" in raw_text and any(keyword in raw_text for keyword in ("煞气池", "幡魂谱系", "魂魄储备", "炼化槽")):
        return True
    if "召唤成功，镇压成功" in raw_text or "魔域裂隙尚未平复" in raw_text:
        return True
    if "神魂之力不足以撕开魔域裂隙" in raw_text:
        return True
    if "【血洗功成】" in raw_text or ("生灵尚未恢复" in raw_text and "煞气稀薄" in raw_text):
        return True
    if "你并非阴罗宗弟子" in raw_text:
        return True
    if "因【阴罗宗】灵脉加持" in raw_text:
        return True
    if "你引动九幽煞气灌入幡中" in raw_text:
        return True
    if "收取成功！" in raw_text and "阴罗幡吞纳残魄" in raw_text:
        return True
    if "若道友拜入阴罗宗，可通过 .血洗山林" in raw_text:
        return True
    if "阴罗宗的夺舍神通" in raw_text and ".夺舍" in raw_text:
        return True
    if "阴罗宗有 .献祭魂魄" in raw_text or "阴罗宗弟子可施展 .下咒" in raw_text:
        return True
    return False


def parse_yinluo_text(text, now=None, family=""):
    now = float(now if now is not None else time.time())
    raw_text = str(text or "").strip()
    if not raw_text:
        return None

    non_member = _parse_non_member(raw_text)
    if non_member:
        return non_member

    if "夺舍重生" in raw_text and "阴罗宗" not in raw_text:
        return None

    title_match = RE_BANNER_TITLE.search(raw_text)
    if title_match:
        parsed = {
            "action": "阴罗幡",
            "result": "panel",
            "summary": "阴罗幡状态",
            "last_error": "",
            "banner_owner": title_match.group("owner").strip(),
            "soul_stocks": {},
        }
        for line in raw_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("本命魔兵"):
                parsed["banner_name"] = stripped.split(":", 1)[-1].strip() if ":" in stripped else stripped.split("：", 1)[-1].strip()
            elif stripped.startswith("幡体等阶"):
                parsed["banner_rank"] = stripped.split(":", 1)[-1].strip() if ":" in stripped else stripped.split("：", 1)[-1].strip()
            elif stripped.startswith("幡威状态"):
                parsed["banner_status"] = stripped.split(":", 1)[-1].strip() if ":" in stripped else stripped.split("：", 1)[-1].strip()
            elif stripped.startswith("主魂流派"):
                parsed["main_soul_path"] = stripped.split(":", 1)[-1].strip() if ":" in stripped else stripped.split("：", 1)[-1].strip()
            else:
                stock_match = RE_SOUL_STOCK.match(stripped)
                if stock_match:
                    parsed["soul_stocks"][stock_match.group("name").strip()] = int(stock_match.group("count") or 0)
        sha_match = RE_SHA_POOL.search(raw_text)
        soul_total_match = RE_SOUL_TOTAL.search(raw_text)
        battle_bonus_match = RE_BATTLE_BONUS.search(raw_text)
        if sha_match:
            parsed["sha_current"] = int(sha_match.group("current") or 0)
            parsed["sha_max"] = int(sha_match.group("max") or 0)
            parsed["sha_percent"] = int(sha_match.group("pct") or 0)
        if soul_total_match:
            parsed["soul_total"] = int(soul_total_match.group("value") or 0)
        if battle_bonus_match:
            parsed["battle_bonus_percent"] = int(battle_bonus_match.group("value") or 0)
        parsed["ready_slots"] = raw_text.count("[精华已成]")
        parsed["refining_slots"] = raw_text.count("[炼化中]")
        parsed["empty_slots"] = raw_text.count("[空闲]")
        return parsed

    if "召唤成功，镇压成功" in raw_text:
        resource = ""
        match = re.search(r"【([^】]+)】", raw_text)
        if match:
            resource = match.group(1).strip()
        return {
            "action": "召唤魔影",
            "result": "success",
            "summary": "召唤魔影成功，魔影镇压",
            "last_error": "",
            "last_resource": resource,
        }

    if "魔域裂隙尚未平复" in raw_text:
        return {
            "action": "召唤魔影",
            "result": "cooldown",
            "summary": "魔域裂隙尚未平复",
            "last_error": "魔域裂隙冷却中",
            "next_demon_summon_time": _wait_until(raw_text, now),
        }

    if "神魂之力不足以撕开魔域裂隙" in raw_text:
        return {
            "action": "召唤魔影",
            "result": "realm_blocked",
            "summary": "召唤魔影失败：境界未达结丹期",
            "last_error": "境界尚未达到结丹期",
        }

    if "你消耗了 5000 点修为" in raw_text and "召唤魔域的投影" in raw_text:
        return {
            "action": "召唤魔影",
            "result": "pending",
            "summary": "开始撕裂空间，召唤魔域投影",
            "last_error": "",
        }

    if "【血洗功成】" in raw_text:
        return {
            "action": "血洗山林",
            "result": "success",
            "summary": "血洗山林成功",
            "last_error": "",
            "next_blood_forest_time": float(now + YINLUO_BLOOD_FOREST_OBSERVED_CD_SEC + YINLUO_TIME_BUFFER_SEC),
        }

    if "生灵尚未恢复" in raw_text and "煞气稀薄" in raw_text:
        return {
            "action": "血洗山林",
            "result": "cooldown",
            "summary": "血洗山林冷却中",
            "last_error": "山林生灵尚未恢复",
            "next_blood_forest_time": _wait_until(raw_text, now),
        }

    if "你引动九幽煞气灌入幡中" in raw_text:
        sha_match = RE_SHA_GAIN.search(raw_text)
        extra_match = RE_EXTRA_SHA_GAIN.search(raw_text)
        return {
            "action": "化功为煞",
            "result": "success",
            "summary": "化功为煞成功",
            "last_error": "",
            "last_sha_gain": int(sha_match.group("gain") or 0) if sha_match else 0,
            "last_extra_sha_gain": int(extra_match.group("gain") or 0) if extra_match else 0,
        }

    if "收取成功！" in raw_text and "阴罗幡吞纳残魄" in raw_text:
        collect_match = RE_COLLECT_SLOT.search(raw_text)
        return {
            "action": "收取幡魂",
            "result": "success",
            "summary": "炼化槽收取成功",
            "last_error": "",
            "last_resource": collect_match.group("items").strip() if collect_match else "",
        }

    if "若道友拜入阴罗宗，可通过 .血洗山林" in raw_text:
        return {
            "action": "血洗山林",
            "result": "guide",
            "summary": "血洗山林线索：冷却四小时，有胜败风险",
            "last_error": "",
        }

    if "阴罗宗的夺舍神通" in raw_text and ".夺舍" in raw_text:
        return {
            "action": "夺舍",
            "result": "guide",
            "summary": "夺舍神通冷却任务线索",
            "last_error": "",
            "last_sample_gap": "夺舍 @目标 成功/冷却文案未收录",
        }

    if "阴罗宗有 .献祭魂魄" in raw_text:
        return {
            "action": "献祭魂魄",
            "result": "guide",
            "summary": "献祭魂魄可将魂魄转化为煞气",
            "last_error": "",
        }

    if "阴罗宗弟子可施展 .下咒" in raw_text:
        return {
            "action": "下咒",
            "result": "guide",
            "summary": "下咒可施加丹魔侵蚀",
            "last_error": "",
        }

    if "因【阴罗宗】灵脉加持" in raw_text:
        bonus_match = RE_BONUS_GAIN.search(raw_text)
        return {
            "action": "闭关",
            "result": "success",
            "summary": "闭关成功，阴罗宗灵脉加持",
            "last_error": "",
            "last_bonus_gain": int(bonus_match.group("gain") or 0) if bonus_match else 0,
        }

    if not looks_like_yinluo_text(raw_text):
        return None
    return {
        "action": "未知阴罗宗文案",
        "result": "observed",
        "summary": _short_summary(raw_text),
        "last_error": "",
    }


def apply_yinluo_passive(text, now=None, family=""):
    now = float(now if now is not None else time.time())
    parsed = parse_yinluo_text(text, now=now, family=family)
    if not parsed:
        return False

    observed = normalize_yinluo_observation(state.get("yinluo_observation"))
    observed["last_observed_at"] = now
    for key in (
        "last_error",
        "next_demon_summon_time",
        "next_blood_forest_time",
        "banner_owner",
        "banner_name",
        "banner_rank",
        "sha_current",
        "sha_max",
        "sha_percent",
        "banner_status",
        "main_soul_path",
        "soul_total",
        "battle_bonus_percent",
        "soul_stocks",
        "ready_slots",
        "refining_slots",
        "empty_slots",
        "last_resource",
        "last_sha_gain",
        "last_extra_sha_gain",
        "last_bonus_gain",
        "last_sample_gap",
    ):
        if key in parsed:
            observed[key] = parsed.get(key)
    observed["last_action"] = parsed.get("action") or ""
    observed["last_result"] = parsed.get("result") or ""
    observed["last_summary"] = parsed.get("summary") or _short_summary(text)
    if parsed.get("action") == "收取幡魂" and parsed.get("result") == "success":
        observed["ready_slots"] = 0
    if parsed.get("action") == "召唤魔影" and parsed.get("result") == "success":
        observed["next_demon_summon_time"] = float(now + YINLUO_DEMON_SUMMON_OBSERVED_CD_SEC + YINLUO_TIME_BUFFER_SEC)
    observed["auto_last_error"] = ""
    if int(observed.get("ready_slots", 0) or 0) > 0:
        observed["auto_next_time"] = min(float(observed.get("auto_next_time", 0) or 0) or now + 60, now + 60)
    elif any(float(observed.get(key, 0) or 0) > now for key in ("next_blood_forest_time", "next_demon_summon_time")):
        observed["auto_next_time"] = _yinluo_next_after_action(observed, now)
    elif observed.get("last_action") in {"阴罗幡", "召唤魔影", "血洗山林", "收取幡魂"}:
        observed["auto_next_time"] = min(float(observed.get("auto_next_time", 0) or 0) or now + 60, now + 60)
    else:
        observed["auto_next_time"] = max(float(observed.get("auto_next_time", 0) or 0), now + YINLUO_AUTO_STATUS_BACKOFF_SEC)
    observed["recent"].append({
        "ts": now,
        "action": observed.get("last_action", ""),
        "result": observed.get("last_result", ""),
        "summary": observed.get("last_summary", ""),
    })
    observed["recent"] = observed["recent"][-8:]
    state["yinluo_observation"] = observed
    return True


def _normalize_manual_action(action):
    raw = str(action or "").strip().lower()
    mapping = {
        "": "banner",
        "banner": "banner",
        "panel": "banner",
        "幡": "banner",
        "查幡": "banner",
        "阴罗幡": "banner",
        "summon": "demon_summon",
        "demon": "demon_summon",
        "demon_summon": "demon_summon",
        "召唤": "demon_summon",
        "召唤魔影": "demon_summon",
        "collect": "collect",
        "收取": "collect",
        "收魂": "collect",
        "收取幡魂": "collect",
        "convert": "convert",
        "化煞": "convert",
        "化功": "convert",
        "化功为煞": "convert",
        "blood": "blood_forest",
        "blood_forest": "blood_forest",
        "血洗": "blood_forest",
        "血洗山林": "blood_forest",
        "curse": "blocked_high_risk",
        "下咒": "blocked_high_risk",
        "possess": "blocked_high_risk",
        "夺舍": "blocked_high_risk",
    }
    return mapping.get(raw, raw)


def _manual_block(action, reason, command="", family=""):
    return {
        "allowed": False,
        "action": action,
        "command": command,
        "family": family,
        "reason": reason,
    }


def _manual_allow(action, command, family, now):
    return {
        "allowed": True,
        "action": action,
        "command": command,
        "family": family,
        "reason": "阴罗宗手动动作允许发送。",
        "source_module": "阴罗宗",
        "op_id": f"yinluo-{action}-{int(now)}",
        "delete_policy": "manual_keep",
        "max_retry": 0,
    }


def _has_recent_observation(observed, now):
    last_observed_at = float(observed.get("last_observed_at", 0) or 0)
    return last_observed_at > 0 and now - last_observed_at <= YINLUO_OBSERVATION_STALE_SEC


def _has_banner_hint(observed):
    return bool(
        str(observed.get("banner_owner") or "").strip()
        or str(observed.get("banner_name") or "").strip()
        or str(observed.get("banner_rank") or "").strip()
        or int(observed.get("soul_total", 0) or 0) > 0
        or int(observed.get("sha_max", 0) or 0) > 0
    )


def _profile_realm():
    profile = get_send_as_profile()
    realm = str(profile.get("realm") or "").strip() or infer_realm_from_xiuwei_max(profile.get("xiuwei_max", 0))
    return realm


def _realm_at_least(min_realm):
    realm = _profile_realm()
    realm_index = REALM_SORT_INDEX.get(realm, -1)
    min_index = REALM_SORT_INDEX.get(str(min_realm or "").strip(), 10**9)
    return realm_index >= min_index, realm


def _earliest_yinluo_next_time(observed, now):
    candidates = []
    for key in ("next_blood_forest_time", "next_demon_summon_time"):
        value = float(observed.get(key, 0) or 0)
        if value > now:
            candidates.append(value)
    candidates.append(now + YINLUO_AUTO_STATUS_BACKOFF_SEC)
    return min(candidates)


def _has_yinluo_due_followup(observed, now):
    if int(observed.get("ready_slots", 0) or 0) > 0:
        return True
    for key in ("next_blood_forest_time", "next_demon_summon_time"):
        if float(observed.get(key, 0) or 0) <= float(now):
            return True
    return False


def _yinluo_next_after_action(observed, now):
    if _has_yinluo_due_followup(observed, now):
        return float(now + YINLUO_AUTO_CHAIN_STEP_SEC)
    return _earliest_yinluo_next_time(observed, now)


def build_yinluo_manual_plan(action="banner", arg="", now=None):
    now = float(now if now is not None else time.time())
    action = _normalize_manual_action(action)
    arg = str(arg or "").strip()
    if not state.get("yinluo_enabled"):
        return _manual_block(action, "阴罗宗模块未开启。")

    if action == "blocked_high_risk":
        return _manual_block(action, "下咒、夺舍风险高且样本不足，当前只观察/人工处理，不由脚本发送。")

    if action == "banner":
        return _manual_allow(action, CMD_YINLUO_BANNER, "yinluo_banner", now)

    observed = normalize_yinluo_observation(state.get("yinluo_observation"))
    if not _has_recent_observation(observed, now):
        last_observed_at = float(observed.get("last_observed_at", 0) or 0)
        if last_observed_at <= 0:
            return _manual_block(action, "缺少阴罗宗真实文案状态，先手动查幡或等待消息盒子观察。")
        return _manual_block(action, f"阴罗宗状态过旧，最近观察 {fmt_abs_ts(last_observed_at)}。")
    if str(observed.get("last_result") or "") == "not_member":
        reason = str(observed.get("last_error") or observed.get("last_summary") or "非阴罗宗弟子").strip()
        return _manual_block(action, f"最近真实文案显示并非阴罗宗弟子：{reason}。")

    if action == "demon_summon":
        realm_ok, realm = _realm_at_least(YINLUO_DEMON_SUMMON_MIN_REALM)
        if not realm_ok:
            return _manual_block(action, f"召唤魔影需结丹期，当前境界：{realm or '未记录'}。", CMD_YINLUO_DEMON_SUMMON, "yinluo_demon_summon")
        next_time = float(observed.get("next_demon_summon_time", 0) or 0)
        if next_time > now:
            return _manual_block(action, f"召唤魔影仍在冷却中，{fmt_remaining(next_time)} 后再试。", CMD_YINLUO_DEMON_SUMMON, "yinluo_demon_summon")
        if str(observed.get("last_result") or "") == "pending" and str(observed.get("last_action") or "") == "召唤魔影":
            return _manual_block(action, "召唤魔影上一轮仍处于结算中，不重复发送。", CMD_YINLUO_DEMON_SUMMON, "yinluo_demon_summon")
        return _manual_allow(action, CMD_YINLUO_DEMON_SUMMON, "yinluo_demon_summon", now)

    if action == "blood_forest":
        next_time = float(observed.get("next_blood_forest_time", 0) or 0)
        if next_time > now:
            return _manual_block(action, f"血洗山林仍在冷却中，{fmt_remaining(next_time)} 后再试。", CMD_YINLUO_BLOOD_FOREST, "yinluo_blood_forest")
        return _manual_allow(action, CMD_YINLUO_BLOOD_FOREST, "yinluo_blood_forest", now)

    if action == "collect":
        if int(observed.get("ready_slots", 0) or 0) <= 0:
            return _manual_block(action, "未记录可收取的精华炼化槽，不发送收取幡魂。", CMD_YINLUO_COLLECT, "yinluo_collect")
        return _manual_allow(action, CMD_YINLUO_COLLECT, "yinluo_collect", now)

    if action == "convert":
        try:
            amount = int(arg or 0)
        except (TypeError, ValueError):
            amount = 0
        if amount <= 0:
            return _manual_block(action, "化功为煞必须指定正整数修为数量。", "", "yinluo_convert")
        if amount > 10000:
            return _manual_block(action, "单次化功为煞上限暂定 10000，避免误消耗过大。", "", "yinluo_convert")
        return _manual_allow(action, f"{CMD_YINLUO_CONVERT} {amount}", "yinluo_convert", now)

    return _manual_block(action, "未知阴罗宗手动动作。")


def _set_yinluo_auto_wait(observed, now, action, next_time=None, error=""):
    observed["auto_last_action"] = str(action or "")
    observed["auto_last_error"] = str(error or "")
    observed["auto_next_time"] = float(next_time or now + YINLUO_AUTO_BLOCK_BACKOFF_SEC)
    state["yinluo_observation"] = observed
    save_state()


async def run_yinluo_scheduler(now):
    now = float(now if now is not None else time.time())
    if not state.get("yinluo_enabled"):
        return

    observed = normalize_yinluo_observation(state.get("yinluo_observation"))
    auto_next_time = float(observed.get("auto_next_time", 0) or 0)
    if auto_next_time > 0 and now < auto_next_time:
        return

    if not _has_recent_observation(observed, now):
        plan = build_yinluo_manual_plan("banner", now=now)
    elif int(observed.get("ready_slots", 0) or 0) > 0:
        plan = build_yinluo_manual_plan("collect", now=now)
    elif not _has_banner_hint(observed):
        plan = build_yinluo_manual_plan("banner", now=now)
    elif float(observed.get("next_blood_forest_time", 0) or 0) <= now:
        plan = build_yinluo_manual_plan("blood_forest", now=now)
    elif float(observed.get("next_demon_summon_time", 0) or 0) <= now:
        plan = build_yinluo_manual_plan("demon_summon", now=now)
    else:
        _set_yinluo_auto_wait(observed, now, "idle", _earliest_yinluo_next_time(observed, now))
        return

    action = str(plan.get("action") or "")
    if not plan.get("allowed"):
        _set_yinluo_auto_wait(
            observed,
            now,
            action,
            now + YINLUO_AUTO_BLOCK_BACKOFF_SEC,
            plan.get("reason") or "阴罗宗自动调度未满足条件",
        )
        return

    msg = await send_game_command(
        plan["command"],
        track=True,
        max_retry=0,
        priority="normal",
        source_module="阴罗宗",
        op_id=f"yinluo-auto-{action}-{int(now)}",
    )
    sent_at = float(getattr(msg, "sent_at", 0) or now) if msg else now
    observed = normalize_yinluo_observation(state.get("yinluo_observation"))
    if not msg:
        _set_yinluo_auto_wait(
            observed,
            now,
            action,
            sent_at + YINLUO_AUTO_SEND_FAIL_BACKOFF_SEC,
            "阴罗宗自动调度发送失败或被安全策略拦截",
        )
        return

    observed["auto_last_action"] = action
    observed["auto_last_error"] = ""
    if action == "collect":
        observed["ready_slots"] = 0
        observed["auto_next_time"] = _yinluo_next_after_action(observed, sent_at)
    elif action == "blood_forest":
        observed["next_blood_forest_time"] = max(
            float(observed.get("next_blood_forest_time", 0) or 0),
            sent_at + YINLUO_BLOOD_FOREST_OBSERVED_CD_SEC + YINLUO_TIME_BUFFER_SEC,
        )
        observed["auto_next_time"] = _yinluo_next_after_action(observed, sent_at)
    elif action == "demon_summon":
        observed["next_demon_summon_time"] = max(
            float(observed.get("next_demon_summon_time", 0) or 0),
            sent_at + YINLUO_DEMON_SUMMON_OBSERVED_CD_SEC + YINLUO_TIME_BUFFER_SEC,
        )
        observed["auto_next_time"] = _yinluo_next_after_action(observed, sent_at)
    else:
        observed["auto_next_time"] = sent_at + YINLUO_AUTO_STATUS_BACKOFF_SEC
    state["yinluo_observation"] = observed
    save_state()


async def execute_yinluo_manual_action(action="banner", arg="", *, send_as_id=None, now=None):
    now = float(now if now is not None else time.time())
    if send_as_id is not None:
        with use_identity(send_as_id):
            plan = build_yinluo_manual_plan(action, arg, now=now)
    else:
        plan = build_yinluo_manual_plan(action, arg, now=now)
    if not plan.get("allowed"):
        return False, plan.get("reason") or "阴罗宗动作未允许", plan
    msg = await send_game_command(
        plan["command"],
        track=True,
        max_retry=int(plan.get("max_retry", 0) or 0),
        send_as_id=send_as_id,
        priority="normal",
        source_module=plan.get("source_module") or "阴罗宗",
        op_id=plan.get("op_id") or "",
        delete_policy=plan.get("delete_policy") or "manual_keep",
    )
    if not msg:
        return False, "发送被运行时安全策略拦截或账号不可用。", plan
    return True, f"已发送：{plan['command']}（msg_id={int(getattr(msg, 'id', 0) or 0)}）", plan


def _format_soul_stocks(stocks):
    if not isinstance(stocks, dict) or not stocks:
        return "未记录"
    return "、".join(f"{name}:{count}" for name, count in stocks.items())


def get_yinluo_status_text():
    observed = normalize_yinluo_observation(state.get("yinluo_observation"))
    lines = [
        "🌑 阴罗宗",
        f"- 模块：{'开启' if state.get('yinluo_enabled') else '关闭'}（被动观察，手动动作受控发送）",
        "- 已收录：阴罗幡、召唤魔影、化功为煞、血洗山林成功/冷却、闭关灵脉加成、非弟子失败",
        f"- 最近动作：{observed.get('last_action') or '未记录'} / {observed.get('last_result') or '未记录'}",
        f"- 最近观察：{fmt_abs_ts(observed.get('last_observed_at', 0))}",
        f"- 阴罗幡：{observed.get('banner_owner') or '未记录'}｜{observed.get('banner_name') or '-'}｜{observed.get('banner_rank') or '-'}｜{observed.get('banner_status') or '-'}",
        f"- 煞气池：{observed.get('sha_current', 0)} / {observed.get('sha_max', 0)}（{observed.get('sha_percent', 0)}%）｜战力+{observed.get('battle_bonus_percent', 0)}%",
        f"- 魂魄储备：{_format_soul_stocks(observed.get('soul_stocks'))}",
        f"- 炼化槽：精华 {observed.get('ready_slots', 0)}｜炼化 {observed.get('refining_slots', 0)}｜空闲 {observed.get('empty_slots', 0)}",
        f"- 血洗山林：{fmt_abs_ts(observed.get('next_blood_forest_time', 0))}（{fmt_remaining(observed.get('next_blood_forest_time', 0))}）",
        f"- 召唤魔影：{fmt_abs_ts(observed.get('next_demon_summon_time', 0))}（{fmt_remaining(observed.get('next_demon_summon_time', 0))}）",
        f"- 自动调度：{fmt_abs_ts(observed.get('auto_next_time', 0))}（{fmt_remaining(observed.get('auto_next_time', 0))}）",
    ]
    if observed.get("last_sha_gain") or observed.get("last_extra_sha_gain"):
        lines.append(f"- 最近煞气：+{observed.get('last_sha_gain', 0)}｜杀戮额外+{observed.get('last_extra_sha_gain', 0)}")
    if observed.get("last_bonus_gain"):
        lines.append(f"- 闭关加成：修为+{observed.get('last_bonus_gain')}")
    if observed.get("last_resource"):
        lines.append(f"- 最近资源：{observed.get('last_resource')}")
    if observed.get("last_sample_gap"):
        lines.append(f"- 样本缺口：{observed.get('last_sample_gap')}")
    if observed.get("last_error"):
        lines.append(f"- 异常：{observed.get('last_error')}")
    if observed.get("auto_last_error"):
        lines.append(f"- 自动异常：{observed.get('auto_last_error')}")
    recent = observed.get("recent") or []
    if recent:
        lines.append("- 最近事件：")
        for item in recent[-3:]:
            lines.append(f"  {fmt_abs_ts(item.get('ts', 0))} {item.get('action') or '-'} {item.get('result') or '-'}")
    return "\n".join(lines)


__all__ = [
    "apply_yinluo_passive",
    "build_yinluo_manual_plan",
    "execute_yinluo_manual_action",
    "get_yinluo_status_text",
    "looks_like_yinluo_text",
    "normalize_yinluo_observation",
    "parse_yinluo_text",
    "run_yinluo_scheduler",
]
