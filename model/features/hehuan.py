import copy
import re
import time

from ..state import state
from ..timing import fmt_abs_ts, fmt_remaining, has_wait_time, parse_wait_time


HEHUAN_CONTRACT_SEC = 7 * 24 * 3600
HEHUAN_HEART_SEAL_SEC = 3 * 24 * 3600
HEHUAN_WARM_OBSERVED_CD_SEC = 60 * 60
HEHUAN_CD_BUFFER_SEC = 60

PATH_FANCHEN = "凡尘缘"
PATH_TONGCAN = "同参道"
PATH_MORAN = "魔染道"

RE_AT_NAME = re.compile(r"@[\w\d_]+")
RE_PARTNER = re.compile(r"你与\s*(?P<partner>@[\w\d_]+)")
RE_GAIN_LINE = re.compile(
    r"(?P<name>@[\w\d_]+)\s*修为增加了\s*(?P<gain>\d+)\s*点(?:，并获得\s*(?P<contrib>\d+)\s*点宗门贡献)?"
)
RE_INSIGHT = re.compile(r"共同领悟了【(?P<item>[^】]+)】")
RE_FINAL_GAIN = re.compile(r"本次闭关，你的修为最终增加了\s*(?P<gain>\d+)\s*点")
RE_BASE_GAIN = re.compile(r"基础修为增加了\s*(?P<gain>\d+)\s*点")
RE_BONUS_GAIN = re.compile(r"因【合欢宗】灵脉加持，你额外获得了\s*(?P<gain>\d+)\s*点修为")


def _default_hehuan_observation():
    return {
        "last_observed_at": 0,
        "last_path": "",
        "last_action": "",
        "last_result": "",
        "last_summary": "",
        "last_partner": "",
        "last_target": "",
        "last_error": "",
        "next_hehuan_time": 0,
        "contract_until": 0,
        "heart_seal_until": 0,
        "last_gains": {},
        "last_contrib_gain": 0,
        "last_insight": "",
        "recent": [],
    }


def normalize_hehuan_observation(value=None):
    observed = copy.deepcopy(_default_hehuan_observation())
    if isinstance(value, dict):
        observed.update(value)
    if not isinstance(observed.get("last_gains"), dict):
        observed["last_gains"] = {}
    if not isinstance(observed.get("recent"), list):
        observed["recent"] = []
    observed["recent"] = [
        item for item in observed.get("recent", []) if isinstance(item, dict)
    ][-8:]
    for key in ("last_observed_at", "next_hehuan_time", "contract_until", "heart_seal_until"):
        try:
            observed[key] = float(observed.get(key, 0) or 0)
        except (TypeError, ValueError):
            observed[key] = 0
    try:
        observed["last_contrib_gain"] = int(observed.get("last_contrib_gain", 0) or 0)
    except (TypeError, ValueError):
        observed["last_contrib_gain"] = 0
    return observed


def looks_like_hehuan_text(text):
    raw_text = str(text or "").strip()
    if not raw_text or raw_text.startswith("."):
        return False
    if "【入梦成功】" in raw_text:
        return False
    if all(keyword in raw_text for keyword in ("凡尘缘", "同参道", "魔染道")):
        return True
    if "【温养双修" in raw_text or "契印感应" in raw_text:
        return True
    if "无法进行双修" in raw_text or "道友若欲双修" in raw_text:
        return True
    if "闭关双修" in raw_text or ".双修 温养" in raw_text or ".双修 采补" in raw_text:
        return True
    if "种下心印" in raw_text or "挣脱心印" in raw_text:
        return True
    if "心印" in raw_text and any(keyword in raw_text for keyword in ("炉鼎", "采补", "拘我", "挣脱")):
        return True
    if "炉鼎" in raw_text and any(keyword in raw_text for keyword in ("玩物", "拘我", "采补", "沦为")):
        return True
    if "【闭关成功】" in raw_text and "因【合欢宗】灵脉加持" in raw_text:
        return True
    if "合欢宗" in raw_text and any(keyword in raw_text for keyword in ("双修", "同参", "心印", "采补")):
        return True
    return False


def _short_summary(text, limit=80):
    raw_text = " / ".join(part.strip() for part in str(text or "").splitlines() if part.strip())
    return raw_text[: int(limit or 80)]


def _extract_wait_until(text, now):
    if has_wait_time(text):
        wait_sec = parse_wait_time(text)
        if wait_sec > 0:
            return float(now + wait_sec + HEHUAN_CD_BUFFER_SEC)
    return 0


def _extract_warm_success(text, now):
    gains = {}
    contrib_gain = 0
    for match in RE_GAIN_LINE.finditer(text):
        name = str(match.group("name") or "").strip()
        if not name:
            continue
        gains[name] = int(match.group("gain") or 0)
        if match.group("contrib"):
            contrib_gain += int(match.group("contrib") or 0)
    partner_match = RE_PARTNER.search(text)
    insight_match = RE_INSIGHT.search(text)
    return {
        "path": PATH_TONGCAN,
        "action": "双修 温养",
        "result": "success",
        "summary": "温养双修成功",
        "partner": partner_match.group("partner") if partner_match else "",
        "target": "",
        "next_hehuan_time": float(now + HEHUAN_WARM_OBSERVED_CD_SEC + HEHUAN_CD_BUFFER_SEC),
        "contract_until": float(now + HEHUAN_CONTRACT_SEC),
        "heart_seal_until": 0,
        "last_gains": gains,
        "last_contrib_gain": contrib_gain,
        "last_insight": insight_match.group("item").strip() if insight_match else "",
        "error": "",
    }


def parse_hehuan_text(text, now=None, family=""):
    now = float(now if now is not None else time.time())
    raw_text = str(text or "").strip()
    family = str(family or "").strip()
    if not raw_text:
        return None

    if "【温养双修" in raw_text:
        return _extract_warm_success(raw_text, now)
    if "契印感应" in raw_text and "温养双修" in raw_text:
        return {
            "path": PATH_TONGCAN,
            "action": "双修 温养",
            "result": "pending",
            "summary": "契印感应，温养双修结算中",
            "partner": "",
            "target": "",
            "next_hehuan_time": 0,
            "contract_until": float(now + HEHUAN_CONTRACT_SEC),
            "heart_seal_until": 0,
            "last_gains": {},
            "last_contrib_gain": 0,
            "last_insight": "",
            "error": "",
        }
    if "心神尚未恢复" in raw_text and "无法进行双修" in raw_text:
        names = RE_AT_NAME.findall(raw_text)
        return {
            "path": PATH_TONGCAN if family == "hehuan_dual" or "温养" in family else "",
            "action": "双修",
            "result": "cooldown",
            "summary": "双修冷却中",
            "partner": "",
            "target": names[0] if names else "",
            "next_hehuan_time": _extract_wait_until(raw_text, now),
            "contract_until": 0,
            "heart_seal_until": 0,
            "last_gains": {},
            "last_contrib_gain": 0,
            "last_insight": "",
            "error": "心神尚未恢复",
        }
    if "双方或其中一方尚未踏入仙途" in raw_text and "无法进行双修" in raw_text:
        return {
            "path": PATH_FANCHEN if family == "hehuan_retreat" else PATH_TONGCAN if family == "hehuan_dual" else "",
            "action": "双修",
            "result": "realm_blocked",
            "summary": "双修失败：尚未踏入仙途",
            "partner": "",
            "target": "",
            "next_hehuan_time": 0,
            "contract_until": 0,
            "heart_seal_until": 0,
            "last_gains": {},
            "last_contrib_gain": 0,
            "last_insight": "",
            "error": "双方或其中一方尚未踏入仙途",
        }
    if (
        "道友若欲双修" in raw_text
        or ("合欢宗" in raw_text and "双修、同参、心印与采补" in raw_text)
        or all(keyword in raw_text for keyword in ("凡尘缘", "同参道", "魔染道"))
    ):
        return {
            "path": "指南",
            "action": "玩法指南",
            "result": "guide",
            "summary": "合欢宗三层玩法说明",
            "partner": "",
            "target": "",
            "next_hehuan_time": 0,
            "contract_until": 0,
            "heart_seal_until": 0,
            "last_gains": {},
            "last_contrib_gain": 0,
            "last_insight": "",
            "error": "",
        }
    if "对方只是凡人" in raw_text and "种下心印" in raw_text:
        return {
            "path": PATH_MORAN,
            "action": "种下心印",
            "result": "invalid_target",
            "summary": "种下心印失败：对方只是凡人",
            "partner": "",
            "target": "",
            "next_hehuan_time": 0,
            "contract_until": 0,
            "heart_seal_until": 0,
            "last_gains": {},
            "last_contrib_gain": 0,
            "last_insight": "",
            "error": "对方只是凡人",
        }
    if "炉鼎" in raw_text and any(keyword in raw_text for keyword in ("拘我", "玩物", "沦为")):
        is_controlled = "沦为炉鼎" in raw_text or "炉鼎玩物" in raw_text
        return {
            "path": PATH_MORAN,
            "action": "心印/炉鼎",
            "result": "controlled" if is_controlled else "challenged",
            "summary": "炉鼎文案已观察",
            "partner": "",
            "target": "",
            "next_hehuan_time": 0,
            "contract_until": 0,
            "heart_seal_until": float(now + HEHUAN_HEART_SEAL_SEC) if is_controlled else 0,
            "last_gains": {},
            "last_contrib_gain": 0,
            "last_insight": "",
            "error": "",
        }
    if "【闭关成功】" in raw_text and "因【合欢宗】灵脉加持" in raw_text:
        final_match = RE_FINAL_GAIN.search(raw_text)
        base_match = RE_BASE_GAIN.search(raw_text)
        bonus_match = RE_BONUS_GAIN.search(raw_text)
        gains = {}
        if base_match:
            gains["基础"] = int(base_match.group("gain") or 0)
        if bonus_match:
            gains["合欢宗加成"] = int(bonus_match.group("gain") or 0)
        if final_match:
            gains["最终"] = int(final_match.group("gain") or 0)
        return {
            "path": PATH_FANCHEN,
            "action": "闭关双修",
            "result": "success",
            "summary": "闭关成功，合欢宗灵脉加持",
            "partner": "",
            "target": "",
            "next_hehuan_time": _extract_wait_until(raw_text, now),
            "contract_until": 0,
            "heart_seal_until": 0,
            "last_gains": gains,
            "last_contrib_gain": 0,
            "last_insight": "",
            "error": "",
        }
    if not looks_like_hehuan_text(raw_text):
        return None
    return {
        "path": "",
        "action": "未知合欢宗文案",
        "result": "observed",
        "summary": _short_summary(raw_text),
        "partner": "",
        "target": "",
        "next_hehuan_time": 0,
        "contract_until": 0,
        "heart_seal_until": 0,
        "last_gains": {},
        "last_contrib_gain": 0,
        "last_insight": "",
        "error": "",
    }


def apply_hehuan_passive(text, now=None, family=""):
    now = float(now if now is not None else time.time())
    parsed = parse_hehuan_text(text, now=now, family=family)
    if not parsed:
        return False

    observed = normalize_hehuan_observation(state.get("hehuan_observation"))
    observed["last_observed_at"] = now
    observed["last_path"] = parsed.get("path") or observed.get("last_path", "")
    observed["last_action"] = parsed.get("action") or ""
    observed["last_result"] = parsed.get("result") or ""
    observed["last_summary"] = parsed.get("summary") or _short_summary(text)
    observed["last_partner"] = parsed.get("partner") or ""
    observed["last_target"] = parsed.get("target") or ""
    observed["last_error"] = parsed.get("error") or ""
    if parsed.get("next_hehuan_time"):
        observed["next_hehuan_time"] = float(parsed.get("next_hehuan_time") or 0)
    if parsed.get("contract_until"):
        observed["contract_until"] = float(parsed.get("contract_until") or 0)
    if parsed.get("heart_seal_until"):
        observed["heart_seal_until"] = float(parsed.get("heart_seal_until") or 0)
    observed["last_gains"] = parsed.get("last_gains") if isinstance(parsed.get("last_gains"), dict) else {}
    observed["last_contrib_gain"] = int(parsed.get("last_contrib_gain", 0) or 0)
    observed["last_insight"] = parsed.get("last_insight") or ""
    observed["recent"].append({
        "ts": now,
        "path": observed["last_path"],
        "action": observed["last_action"],
        "result": observed["last_result"],
        "summary": observed["last_summary"],
    })
    observed["recent"] = observed["recent"][-8:]
    state["hehuan_observation"] = observed
    return True


def _format_gain_map(gains):
    if not isinstance(gains, dict) or not gains:
        return "未记录"
    return "、".join(f"{key}:{value}" for key, value in gains.items())


def get_hehuan_status_text():
    observed = normalize_hehuan_observation(state.get("hehuan_observation"))
    lines = [
        "🌸 合欢宗",
        f"- 模块：{'开启' if state.get('hehuan_enabled') else '关闭'}（被动观察，不自动发送）",
        "- 三层：凡尘缘 .闭关双修｜同参道 .缔结同参/.双修 温养｜魔染道 .种下心印/.双修 采补/.挣脱心印",
        f"- 最近路径：{observed.get('last_path') or '未记录'}",
        f"- 最近动作：{observed.get('last_action') or '未记录'} / {observed.get('last_result') or '未记录'}",
        f"- 最近观察：{fmt_abs_ts(observed.get('last_observed_at', 0))}",
        f"- 下次可试：{fmt_abs_ts(observed.get('next_hehuan_time', 0))}（{fmt_remaining(observed.get('next_hehuan_time', 0))}）",
        f"- 同参契印：{fmt_abs_ts(observed.get('contract_until', 0))}（{fmt_remaining(observed.get('contract_until', 0))}）",
        f"- 心印/炉鼎：{fmt_abs_ts(observed.get('heart_seal_until', 0))}（{fmt_remaining(observed.get('heart_seal_until', 0))}）",
        f"- 修为/贡献：{_format_gain_map(observed.get('last_gains'))}｜贡献 {int(observed.get('last_contrib_gain', 0) or 0)}",
    ]
    if observed.get("last_partner"):
        lines.append(f"- 道友：{observed.get('last_partner')}")
    if observed.get("last_target"):
        lines.append(f"- 目标：{observed.get('last_target')}")
    if observed.get("last_insight"):
        lines.append(f"- 顿悟：{observed.get('last_insight')}")
    if observed.get("last_error"):
        lines.append(f"- 异常：{observed.get('last_error')}")
    recent = observed.get("recent") or []
    if recent:
        lines.append("- 最近事件：")
        for item in recent[-3:]:
            lines.append(
                f"  {fmt_abs_ts(item.get('ts', 0))} "
                f"{item.get('path') or '-'} {item.get('action') or '-'} {item.get('result') or '-'}"
            )
    return "\n".join(lines)


__all__ = [
    "apply_hehuan_passive",
    "get_hehuan_status_text",
    "looks_like_hehuan_text",
    "normalize_hehuan_observation",
    "parse_hehuan_text",
]
