"""Pure parsers and command planning for the fishing module."""

from dataclasses import dataclass
import json
import re

from ..config import (
    CMD_FISHING,
    CMD_FISHING_BUY_BAIT,
    CMD_FISHING_CHUM,
)


FISHING_PONDS = ("青溪浅滩", "灵眼寒潭", "乱星海礁")
FISHING_BAITS = ("凡饵", "灵米饵", "灵虫饵", "妖血饵", "月华饵")
FISHING_CHUMS = ("米糠小窝", "灵草窝", "妖腥窝")
FISHING_CHUM_DAILY_LIMITS = {
    "米糠小窝": 2,
    "灵草窝": 2,
    "妖腥窝": 1,
}
FISHING_DEFAULT_DAILY_LIMIT = 20
FISHING_DEFAULT_BUY_BAIT_COUNT = 20
FISHING_MAX_DAILY_LIMIT = 20
FISHING_MAX_BUY_BAIT_COUNT = 99
FISHING_DEFAULT_CHUM_NAMES = ("米糠小窝",)
FISHING_BAIT_ITEM_KEYS = {
    "凡饵": "item_fishing_bait_plain",
    "灵米饵": "item_fishing_bait_spirit_rice",
    "妖血饵": "item_fishing_bait_demon_blood",
}
FISHING_ITEM_KEY_TO_BAIT = {item_key: bait for bait, item_key in FISHING_BAIT_ITEM_KEYS.items()}

_RESOURCE_SHORTAGE_RE = re.compile(r"打窝失败，资源不足：(?P<item>[A-Za-z0-9_]+)x(?P<count>\d+)。")
_GENERIC_RESOURCE_SHORTAGE_KEYWORDS = ("灵石不足", "灵石不够", "妖丹不足", "资源不足", "材料不足", "数量不足")
_BUY_BAIT_SUCCESS_RE = re.compile(r"【渔具铺】\s*你购得 【(?P<bait>[^】]+)】x(?P<count>\d+)", re.S)
_MISSING_BAIT_RE = re.compile(r"你的鱼篓中没有【(?P<bait>[^】]+)】。可用 \.买鱼饵 (?P=bait) 购买。")
_FISH_BASKET_ITEM_RE = re.compile(r"^-\s*(?P<name>.+?)\s*x(?P<count>\d+)\s*$")
_FISHING_STATUS_RE = re.compile(
    r"【?灵溪垂钓】\s*"
    r"钓者：(?P<angler>@[^\n]+)\s*"
    r"鱼塘：(?P<pond>[^\n]+)\s*"
    r"天象：(?P<weather>[^\n]+)\s*"
    r"鱼讯：(?P<signal>[^\n]+)\s*"
    r"进度：(?P<bar>[■□]+)\s*(?P<progress>\d+)%",
    re.S,
)
_WAIT_SECONDS_RE = re.compile(r"(?:鱼讯未至，还需|鱼讯倒计时：)(?P<seconds>\d+)秒")
_EXPECTED_SECONDS_RE = re.compile(r"预计\s*(?P<seconds>\d+)秒\s*内会有鱼讯")
_LIFT_SECONDS_RE = re.compile(r"提竿剩余：(?P<seconds>\d+)秒")
_AVAILABLE_RE = re.compile(r"可用：(?P<commands>[^\n]+)")
_START_BAIT_RE = re.compile(r"你挂上 【(?P<bait>[^】]+)】")
_CHUM_SUCCESS_RE = re.compile(r"【打窝已成】\s*你在.*?撒下 【(?P<chum>[^】]+)】.*?接下来\s*(?P<rods>\d+)\s*竿", re.S)
_NO_ROD_RE = re.compile(r"你尚无【青竹钓竿】")
_NO_FISH_RE = re.compile(r"你的鱼篓中只有【(?P<fish>[^】]+)】x0。")
_FISHING_IN_PROGRESS_RE = re.compile(r"你已有一竿尚未收起。可用 \.钓鱼状态 查看，或 \.收竿 放弃。")
_NO_ACTIVE_FISHING_RE = re.compile(r"你当前没有正在进行的垂钓。")
_DAILY_LIMIT_RE = re.compile(r"你今日已垂钓\s*(?P<used>\d+)\s*/\s*(?P<limit>\d+)\s*竿，神识已乏，明日再来。")
_CATCH_OPEN_COMMAND_RE = re.compile(r"可用\s*(?P<command>\.开鱼\s+[^\s]+)\s+查看鱼腹机缘")
_RAISE_SUCCESS_RE = re.compile(
    r"【提竿成功】\s*"
    r"(?P<angler>@[^\s]+) 在 (?P<pond>.*?) 猛然提竿.*?"
    r"竟是一尾 【(?P<fish>[^】]+)】！\s*"
    r"品阶：(?P<grade>[^\n]+)\s*"
    r"重量：(?P<weight>[0-9.]+)斤\s*"
    r"钓术：(?P<skill>[^\n]+?) \(\+(?P<skill_gain>\d+)\)",
    re.S,
)
_OPEN_FISH_RE = re.compile(r"【剖鱼取机缘】\s*你剖开 【(?P<fish>[^】]+)】x(?P<count>\d+).*?获得：(?P<rewards>.+)", re.S)
_REWARD_ITEM_RE = re.compile(r"(?P<name>[^、，,\s]+)x(?P<count>\d+)")
_REWARD_XIUWEI_RE = re.compile(r"修为\+(?P<count>\d+)")


@dataclass(frozen=True)
class FishingResourceCost:
    item_key: str
    count: int
    evidence: str
    item_costs: tuple = ()


@dataclass(frozen=True)
class FishingResourceShortage:
    label: str
    item_key: str = ""
    count: int = 0
    evidence: str = ""


@dataclass(frozen=True)
class FishingChumDecision:
    allow_send: bool
    reason: str
    cost: FishingResourceCost | None = None


@dataclass(frozen=True)
class FishingAutomationConfig:
    pond: str = "青溪浅滩"
    bait: str = "凡饵"
    auto_chum_enabled: bool = True
    chum_name: str = "米糠小窝"
    chum_names: tuple = FISHING_DEFAULT_CHUM_NAMES
    auto_buy_bait_enabled: bool = True
    auto_buy_bait_count: int = FISHING_DEFAULT_BUY_BAIT_COUNT
    auto_probe_enabled: bool = False


@dataclass(frozen=True)
class FishingCommandPlan:
    allow_start: bool
    commands: tuple = ()
    purchase_commands: tuple = ()
    bait_requirements: tuple = ()
    resource_requirements: tuple = ()
    blocked_reason: str = ""
    config: FishingAutomationConfig | None = None
    chum_decision: FishingChumDecision | None = None


@dataclass(frozen=True)
class FishingStatus:
    angler: str
    pond: str
    weather: str
    signal: str
    progress_percent: int
    bait: str = ""
    wait_seconds: int | None = None
    lift_seconds: int | None = None
    available_commands: tuple = ()
    suggested_command: str = ""
    expected_wait_seconds: int | None = None


@dataclass(frozen=True)
class FishingBaitRequirement:
    bait: str
    item_key: str
    required_count: int
    available_count: int | None = None
    missing_count: int = 0


@dataclass(frozen=True)
class FishingResourceRequirement:
    item_name: str
    required_count: int
    available_count: int | None = None
    missing_count: int = 0


@dataclass(frozen=True)
class FishingBuyResult:
    bait: str
    count: int


@dataclass(frozen=True)
class FishingBasket:
    rod_owned: bool
    skill: str
    daily_rods_used: int | None
    daily_rods_limit: int | None
    current_chum: str
    baits: dict
    fish: dict


@dataclass(frozen=True)
class FishingCatch:
    angler: str
    pond: str
    fish: str
    grade: str
    weight_jin: float
    skill: str
    skill_gain: int
    open_command: str


@dataclass(frozen=True)
class OpenFishResult:
    fish: str
    count: int
    items: dict
    xiuwei_gain: int = 0


@dataclass(frozen=True)
class FishingDailyLimitReached:
    used: int
    limit: int


@dataclass(frozen=True)
class FishingChumSuccess:
    chum: str
    rods: int


KNOWN_CHUM_COSTS = {
    "米糠小窝": FishingResourceCost(
        item_key="item_fishing_bait_plain",
        count=2,
        evidence="data/messages/2026-06-23.log:57544",
        item_costs=(("灵石", 30),),
    ),
    "灵草窝": FishingResourceCost(
        item_key="item_fishing_bait_spirit_rice",
        count=3,
        evidence="data/messages/2026-06-23.log:57544",
        item_costs=(("凝血草", 5),),
    ),
    "妖腥窝": FishingResourceCost(
        item_key="item_fishing_bait_demon_blood",
        count=2,
        evidence="data/messages/2026-06-23.log:57544",
        item_costs=(("一阶妖丹", 3), ("灵石", 200)),
    ),
}

FISHING_BAIT_COSTS = {
    "凡饵": (("灵石", 12),),
    "灵米饵": (("灵石", 35),),
    "灵虫饵": (("灵石", 90), ("凝血草", 2)),
    "妖血饵": (("灵石", 220), ("一阶妖丹", 1)),
    "月华饵": (("灵石", 650), ("二级妖丹", 1)),
}


def _parse_optional_int(regex, text):
    match = regex.search(str(text or ""))
    if not match:
        return None
    return int(match.group("seconds"))


def _parse_available_commands(text):
    match = _AVAILABLE_RE.search(str(text or ""))
    if not match:
        return ()
    return tuple(command.strip() for command in re.split(r"\s*/\s*", match.group("commands")) if command.strip())


def _suggest_fishing_command(signal, available_commands, *, auto_probe_enabled=False):
    available = set(available_commands or ())
    if str(signal or "").strip() == "正口黑漂" and ".提竿" in available:
        return ".提竿"
    if str(signal or "").strip() == "鱼在试口" and ".试探咬饵" in available:
        if auto_probe_enabled:
            return ".试探咬饵"
        if ".提竿" in available:
            return ".提竿"
    return ""


def parse_fishing_status(text, *, auto_probe_enabled=False):
    raw_text = str(text or "")
    match = _FISHING_STATUS_RE.search(raw_text)
    if not match:
        return None
    available_commands = _parse_available_commands(raw_text)
    bait_match = _START_BAIT_RE.search(raw_text)
    signal = match.group("signal").strip()
    return FishingStatus(
        angler=match.group("angler").strip(),
        pond=match.group("pond").strip(),
        weather=match.group("weather").strip(),
        signal=signal,
        progress_percent=int(match.group("progress")),
        bait=bait_match.group("bait").strip() if bait_match else "",
        wait_seconds=_parse_optional_int(_WAIT_SECONDS_RE, raw_text),
        lift_seconds=_parse_optional_int(_LIFT_SECONDS_RE, raw_text),
        available_commands=available_commands,
        suggested_command=_suggest_fishing_command(signal, available_commands, auto_probe_enabled=auto_probe_enabled),
        expected_wait_seconds=_parse_optional_int(_EXPECTED_SECONDS_RE, raw_text),
    )


def parse_buy_bait_result(text):
    match = _BUY_BAIT_SUCCESS_RE.search(str(text or ""))
    if not match:
        return None
    return FishingBuyResult(
        bait=match.group("bait").strip(),
        count=int(match.group("count")),
    )


def parse_missing_bait_reply(text):
    match = _MISSING_BAIT_RE.search(str(text or ""))
    return match.group("bait").strip() if match else ""


def _parse_basket_items(lines):
    items = {}
    for line in lines:
        match = _FISH_BASKET_ITEM_RE.match(str(line or "").strip())
        if match:
            items[match.group("name").strip()] = int(match.group("count"))
    return items


def parse_fishing_basket(text):
    raw_text = str(text or "")
    if "【鱼篓】" not in raw_text:
        return None
    lines = [line.strip() for line in raw_text.splitlines()]
    rod_owned = "青竹钓竿：已持有" in raw_text
    skill = ""
    daily_rods_used = None
    daily_rods_limit = None
    current_chum = ""
    bait_lines = []
    fish_lines = []
    section = ""
    for line in lines:
        if line.startswith("钓术："):
            skill = line.split("：", 1)[1].strip()
            continue
        if line.startswith("今日竿数："):
            match = re.search(r"(\d+)\s*/\s*(\d+)", line)
            if match:
                daily_rods_used = int(match.group(1))
                daily_rods_limit = int(match.group(2))
            continue
        if line.startswith("当前窝料："):
            current_chum = line.split("：", 1)[1].strip()
            continue
        if line == "鱼饵":
            section = "bait"
            continue
        if line == "鱼获":
            section = "fish"
            continue
        if line.startswith("可用 "):
            section = ""
            continue
        if section == "bait":
            bait_lines.append(line)
        elif section == "fish":
            fish_lines.append(line)
    return FishingBasket(
        rod_owned=rod_owned,
        skill=skill,
        daily_rods_used=daily_rods_used,
        daily_rods_limit=daily_rods_limit,
        current_chum=current_chum,
        baits=_parse_basket_items(bait_lines),
        fish=_parse_basket_items(fish_lines),
    )


def parse_fishing_catch(text):
    raw_text = str(text or "")
    match = _RAISE_SUCCESS_RE.search(raw_text)
    if not match:
        return None
    fish = match.group("fish").strip()
    open_match = _CATCH_OPEN_COMMAND_RE.search(raw_text)
    return FishingCatch(
        angler=match.group("angler").strip(),
        pond=match.group("pond").strip(),
        fish=fish,
        grade=match.group("grade").strip(),
        weight_jin=float(match.group("weight")),
        skill=match.group("skill").strip(),
        skill_gain=int(match.group("skill_gain")),
        open_command=(open_match.group("command").strip() if open_match else f".开鱼 {fish}"),
    )


def parse_open_fish_result(text):
    match = _OPEN_FISH_RE.search(str(text or ""))
    if not match:
        return None
    rewards_text = match.group("rewards").strip()
    items = {}
    for item_match in _REWARD_ITEM_RE.finditer(rewards_text):
        items[item_match.group("name").strip()] = int(item_match.group("count"))
    xiuwei_match = _REWARD_XIUWEI_RE.search(rewards_text)
    return OpenFishResult(
        fish=match.group("fish").strip(),
        count=int(match.group("count")),
        items=items,
        xiuwei_gain=int(xiuwei_match.group("count")) if xiuwei_match else 0,
    )


def parse_chum_success_detail(text):
    match = _CHUM_SUCCESS_RE.search(str(text or ""))
    if not match:
        return None
    return FishingChumSuccess(
        chum=match.group("chum").strip(),
        rods=int(match.group("rods")),
    )


def parse_chum_success(text):
    detail = parse_chum_success_detail(text)
    return detail.chum if detail else ""


def parse_no_rod_reply(text):
    return bool(_NO_ROD_RE.search(str(text or "")))


def parse_no_fish_reply(text):
    match = _NO_FISH_RE.search(str(text or ""))
    return match.group("fish").strip() if match else ""


def parse_fishing_in_progress_reply(text):
    return bool(_FISHING_IN_PROGRESS_RE.search(str(text or "")))


def parse_no_active_fishing_reply(text):
    return bool(_NO_ACTIVE_FISHING_RE.search(str(text or "")))


def parse_fishing_daily_limit_reached(text):
    match = _DAILY_LIMIT_RE.search(str(text or ""))
    if not match:
        return None
    return FishingDailyLimitReached(
        used=int(match.group("used")),
        limit=int(match.group("limit")),
    )


def parse_empty_fishing_result(text):
    raw_text = str(text or "").strip()
    if not raw_text.startswith("【空竿】"):
        return ""
    for line in raw_text.splitlines():
        line = line.strip()
        if line and not line.startswith("【空竿】"):
            return line[:80]
    return "空竿"


def parse_chum_shortage(text):
    """Return the internal resource key exposed by a real chum failure reply."""
    match = _RESOURCE_SHORTAGE_RE.search(str(text or ""))
    if not match:
        return None
    return FishingResourceCost(
        item_key=match.group("item"),
        count=int(match.group("count")),
        evidence="resource_shortage_reply",
    )


def parse_generic_resource_shortage(text):
    raw_text = str(text or "")
    for keyword in _GENERIC_RESOURCE_SHORTAGE_KEYWORDS:
        if keyword in raw_text:
            return FishingResourceShortage(
                label=keyword,
                evidence="resource_shortage_reply",
            )
    return None


def fishing_bait_name_for_item_key(item_key):
    return FISHING_ITEM_KEY_TO_BAIT.get(str(item_key or "").strip(), "")


def get_known_chum_cost(chum_name):
    return KNOWN_CHUM_COSTS.get(str(chum_name or "").strip())


def decide_chum_send(chum_name, *, auto_chum_enabled=False):
    """Fail closed until the exact cost for a chum is known from evidence."""
    if not auto_chum_enabled:
        return FishingChumDecision(False, "auto_chum_disabled")
    cost = get_known_chum_cost(chum_name)
    if cost is None:
        return FishingChumDecision(False, "unknown_chum_cost")
    return FishingChumDecision(True, "known_chum_cost", cost)


def clamp_fishing_daily_limit(value):
    try:
        parsed = int(str(value if value is not None else FISHING_DEFAULT_DAILY_LIMIT).strip() or str(FISHING_DEFAULT_DAILY_LIMIT))
    except (TypeError, ValueError):
        parsed = FISHING_DEFAULT_DAILY_LIMIT
    return max(1, min(FISHING_MAX_DAILY_LIMIT, parsed))


def clamp_fishing_buy_bait_count(value):
    try:
        parsed = int(str(value if value is not None else FISHING_DEFAULT_BUY_BAIT_COUNT).strip() or str(FISHING_DEFAULT_BUY_BAIT_COUNT))
    except (TypeError, ValueError):
        parsed = FISHING_DEFAULT_BUY_BAIT_COUNT
    return max(1, min(FISHING_MAX_BUY_BAIT_COUNT, parsed))


def normalize_fishing_chum_names(chum_names=None, fallback_chum_name=""):
    candidates = []
    if isinstance(chum_names, str):
        raw = chum_names.strip()
        if raw:
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                parsed = re.split(r"[,\s，、]+", raw)
            if isinstance(parsed, (list, tuple)):
                candidates.extend(parsed)
            elif parsed:
                candidates.append(parsed)
    elif isinstance(chum_names, (list, tuple, set)):
        candidates.extend(chum_names)
    if not candidates and fallback_chum_name:
        candidates.append(fallback_chum_name)
    selected = []
    for chum in candidates:
        name = str(chum or "").strip()
        if not name or name == "无":
            continue
        if name not in FISHING_CHUMS:
            raise ValueError(f"unsupported_fishing_chum:{name}")
        if name not in selected:
            selected.append(name)
    return tuple(chum for chum in FISHING_CHUMS if chum in selected)


def format_fishing_chum_names(chum_names):
    normalized = normalize_fishing_chum_names(chum_names)
    return json.dumps(list(normalized), ensure_ascii=False)


def normalize_fishing_config(
    pond="青溪浅滩",
    bait="凡饵",
    *,
    auto_chum_enabled=True,
    chum_name="米糠小窝",
    chum_names=None,
    auto_buy_bait_enabled=True,
    auto_buy_bait_count=FISHING_DEFAULT_BUY_BAIT_COUNT,
    auto_probe_enabled=False,
):
    """Normalize operator/UI choices without connecting them to runtime sends."""
    normalized_pond = str(pond or "").strip()
    normalized_bait = str(bait or "").strip()
    normalized_chums = normalize_fishing_chum_names(
        FISHING_DEFAULT_CHUM_NAMES if chum_names is None and chum_name is None else chum_names,
        fallback_chum_name=chum_name or "",
    )
    if normalized_pond not in FISHING_PONDS:
        raise ValueError(f"unsupported_fishing_pond:{normalized_pond}")
    if normalized_bait not in FISHING_BAITS:
        raise ValueError(f"unsupported_fishing_bait:{normalized_bait}")
    enabled = bool(auto_chum_enabled and normalized_chums)
    return FishingAutomationConfig(
        pond=normalized_pond,
        bait=normalized_bait,
        auto_chum_enabled=enabled,
        chum_name=normalized_chums[0] if normalized_chums else "",
        chum_names=normalized_chums,
        auto_buy_bait_enabled=bool(auto_buy_bait_enabled),
        auto_buy_bait_count=clamp_fishing_buy_bait_count(auto_buy_bait_count),
        auto_probe_enabled=bool(auto_probe_enabled),
    )


def _normalize_inventory(inventory):
    if inventory is None:
        return None
    if isinstance(inventory, FishingBasket):
        source = {}
        source.update(inventory.baits)
        source.update(inventory.fish)
    elif isinstance(inventory, dict):
        source = inventory.get("baits") if isinstance(inventory.get("baits"), dict) else inventory
        if isinstance(source.get("items"), dict):
            source = source.get("items")
    else:
        return {}
    normalized = {}
    for raw_name, raw_count in source.items():
        item_name = fishing_bait_name_for_item_key(raw_name) or str(raw_name or "").strip()
        try:
            count = int(raw_count or 0)
        except (TypeError, ValueError):
            count = 0
        if item_name:
            normalized[item_name] = normalized.get(item_name, 0) + max(0, count)
    return normalized


def _normalize_bait_inventory(bait_inventory):
    inventory = _normalize_inventory(bait_inventory)
    if inventory is None:
        return None
    return {bait: int(inventory.get(bait, 0) or 0) for bait in FISHING_BAITS if bait in inventory}


def _add_item_costs(target, item_costs, multiplier=1):
    multiplier = max(0, int(multiplier or 0))
    for item_name, count in item_costs or ():
        name = str(item_name or "").strip()
        if not name:
            continue
        target[name] = target.get(name, 0) + int(count or 0) * multiplier


def _build_resource_requirements(resource_costs, inventory):
    normalized_inventory = _normalize_inventory(inventory)
    requirements = []
    for item_name, required_count in sorted((resource_costs or {}).items()):
        required = int(required_count or 0)
        available = None if normalized_inventory is None else int(normalized_inventory.get(item_name, 0) or 0)
        missing = 0 if available is None else max(0, required - available)
        requirements.append(FishingResourceRequirement(
            item_name=item_name,
            required_count=required,
            available_count=available,
            missing_count=missing,
        ))
    return tuple(requirements)


def _format_missing_resources(requirements):
    parts = [f"{item.item_name}x{item.missing_count}" for item in requirements or () if int(item.missing_count or 0) > 0]
    return "、".join(parts)


def _selected_chum_names(config):
    return tuple(getattr(config, "chum_names", ()) or ((config.chum_name,) if getattr(config, "chum_name", "") else ()))


def _next_chum_name(config, usage_counts):
    if not config.auto_chum_enabled:
        return ""
    usage_counts = _normalize_inventory(usage_counts) or {}
    for chum_name in _selected_chum_names(config):
        limit = FISHING_CHUM_DAILY_LIMITS.get(chum_name)
        used = int(usage_counts.get(chum_name, 0) or 0)
        if limit is None or used < int(limit or 0):
            return chum_name
    return ""


def _build_bait_requirements(config, bait_inventory, *, chum_name=""):
    required = {config.bait: 1}
    if config.auto_chum_enabled and chum_name:
        cost = get_known_chum_cost(chum_name)
        bait_name = fishing_bait_name_for_item_key(cost.item_key) if cost else ""
        if bait_name:
            required[bait_name] = required.get(bait_name, 0) + int(cost.count or 0)
    inventory = _normalize_bait_inventory(bait_inventory)
    requirements = []
    for bait_name, required_count in required.items():
        item_key = FISHING_BAIT_ITEM_KEYS.get(bait_name, "")
        available = None if inventory is None else int(inventory.get(bait_name, 0) or 0)
        missing = 0 if available is None else max(0, int(required_count or 0) - available)
        requirements.append(FishingBaitRequirement(
            bait=bait_name,
            item_key=item_key,
            required_count=int(required_count or 0),
            available_count=available,
            missing_count=missing,
        ))
    return tuple(requirements)


def plan_fishing_commands(config, *, bait_inventory=None, active_chum_name="", active_chum_rods_remaining=0, chum_usage_counts=None):
    """Build the future send plan, failing closed for unproven chum costs."""
    if not isinstance(config, FishingAutomationConfig):
        config = normalize_fishing_config(
            getattr(config, "pond", "青溪浅滩") if config is not None else "青溪浅滩",
            getattr(config, "bait", "凡饵") if config is not None else "凡饵",
            auto_chum_enabled=getattr(config, "auto_chum_enabled", False) if config is not None else False,
            chum_name=getattr(config, "chum_name", "") if config is not None else "",
            chum_names=getattr(config, "chum_names", None) if config is not None else None,
            auto_buy_bait_enabled=getattr(config, "auto_buy_bait_enabled", False) if config is not None else False,
            auto_buy_bait_count=getattr(config, "auto_buy_bait_count", FISHING_DEFAULT_BUY_BAIT_COUNT) if config is not None else FISHING_DEFAULT_BUY_BAIT_COUNT,
            auto_probe_enabled=getattr(config, "auto_probe_enabled", False) if config is not None else False,
        )

    commands = []
    chum_decision = None
    has_active_chum = (
        config.auto_chum_enabled
        and str(active_chum_name or "").strip()
        and int(active_chum_rods_remaining or 0) > 0
    )
    target_chum = "" if has_active_chum else _next_chum_name(config, chum_usage_counts)
    if target_chum:
        chum_decision = decide_chum_send(target_chum, auto_chum_enabled=True)
        if not chum_decision.allow_send:
            return FishingCommandPlan(
                allow_start=False,
                commands=(),
                purchase_commands=(),
                bait_requirements=(),
                blocked_reason=chum_decision.reason,
                config=config,
                chum_decision=chum_decision,
            )
        commands.append(f".打窝 {target_chum}")

    commands.append(f".钓鱼 {config.pond} {config.bait}")
    inventory_unknown = bait_inventory is None
    bait_requirements = _build_bait_requirements(config, bait_inventory, chum_name=target_chum)
    purchase_commands = []
    planned_purchase_counts = {}
    for requirement in bait_requirements:
        should_buy = requirement.missing_count > 0 or (inventory_unknown and config.auto_buy_bait_enabled)
        if not should_buy:
            continue
        missing_count = max(1, int(requirement.missing_count or requirement.required_count or 1))
        buy_count = max(missing_count, int(config.auto_buy_bait_count or FISHING_DEFAULT_BUY_BAIT_COUNT))
        purchase_commands.append(f".买鱼饵 {requirement.bait} {buy_count}")
        planned_purchase_counts[requirement.bait] = planned_purchase_counts.get(requirement.bait, 0) + buy_count
    purchase_commands = tuple(purchase_commands)
    if purchase_commands:
        if not config.auto_buy_bait_enabled:
            return FishingCommandPlan(
                allow_start=False,
                commands=(),
                purchase_commands=purchase_commands,
                bait_requirements=bait_requirements,
                blocked_reason="insufficient_bait",
                config=config,
                chum_decision=chum_decision,
            )
        commands = list(purchase_commands) + commands

    resource_costs = {}
    if target_chum and chum_decision and chum_decision.cost:
        _add_item_costs(resource_costs, chum_decision.cost.item_costs, 1)
    for bait_name, buy_count in planned_purchase_counts.items():
        _add_item_costs(resource_costs, FISHING_BAIT_COSTS.get(bait_name, ()), buy_count)
    resource_requirements = _build_resource_requirements(resource_costs, bait_inventory)
    missing_resources = _format_missing_resources(resource_requirements)
    if missing_resources:
        return FishingCommandPlan(
            allow_start=False,
            commands=(),
            purchase_commands=purchase_commands,
            bait_requirements=bait_requirements,
            resource_requirements=resource_requirements,
            blocked_reason=f"insufficient_resources:{missing_resources}",
            config=config,
            chum_decision=chum_decision,
        )
    return FishingCommandPlan(
        allow_start=True,
        commands=tuple(commands),
        purchase_commands=purchase_commands,
        bait_requirements=bait_requirements,
        resource_requirements=resource_requirements,
        config=config,
        chum_decision=chum_decision,
    )
