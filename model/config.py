import os
import re
import time
from datetime import timedelta, timezone
from urllib.parse import quote

import requests
from telethon import TelegramClient

APP_DIR = os.path.dirname(__file__)
PROJECT_ROOT_DIR = os.path.dirname(APP_DIR)
DATA_DIR = os.path.abspath(os.environ.get("XIUXIAN_DATA_DIR") or os.path.join(PROJECT_ROOT_DIR, "data"))
SESSION_DIR = os.path.abspath(os.environ.get("XIUXIAN_SESSION_DIR") or os.path.join(DATA_DIR, "session"))
STATE_DIR = os.path.abspath(os.environ.get("XIUXIAN_STATE_DIR") or os.path.join(DATA_DIR, "state"))
MESSAGES_DIR = os.path.abspath(os.environ.get("XIUXIAN_MESSAGES_DIR") or os.path.join(DATA_DIR, "messages"))
SESSION_FILE = os.path.join(SESSION_DIR, "ai_investor_session")

# ================= 从 .env 读取启动配置 =================
def _load_dotenv():
    env_path = os.path.join(PROJECT_ROOT_DIR, ".env")
    if not os.path.exists(env_path):
        if os.environ.get("XIUXIAN_TESTING") == "1":
            return
        raise FileNotFoundError(f"missing .env: {env_path}")
    testing = os.environ.get("XIUXIAN_TESTING") == "1"
    with open(env_path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key:
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            if testing and key in os.environ:
                continue
            os.environ[key] = value


_load_dotenv()


def _get_env_str(key, default=""):
    return str(os.environ.get(key, default) or default).strip()


def _get_env_int(key, default, *, minimum=None, maximum=None):
    try:
        value = int(os.environ.get(key, default))
    except (TypeError, ValueError):
        value = int(default)
    if minimum is not None:
        value = max(int(minimum), value)
    if maximum is not None:
        value = min(int(maximum), value)
    return value


MESSAGE_LOG_RETENTION_DAYS = _get_env_int("XIUXIAN_MESSAGE_LOG_RETENTION_DAYS", 30, minimum=1)
MESSAGE_LOG_MAX_MB = _get_env_int("XIUXIAN_MESSAGE_LOG_MAX_MB", 300, minimum=0)
PASSIVE_EVENT_LEDGER_RETENTION_DAYS = _get_env_int("XIUXIAN_PASSIVE_EVENT_RETENTION_DAYS", 14, minimum=1)
PASSIVE_EVENT_LEDGER_MAX_MB = _get_env_int("XIUXIAN_PASSIVE_EVENT_MAX_MB", 50, minimum=0)
WORKFLOW_LOG_RETENTION_DAYS = _get_env_int("XIUXIAN_WORKFLOW_LOG_RETENTION_DAYS", 14, minimum=1)
WORKFLOW_LOG_MAX_MB = _get_env_int("XIUXIAN_WORKFLOW_LOG_MAX_MB", 50, minimum=0)
LOG_RETENTION_CLEANUP_INTERVAL_SEC = _get_env_int("XIUXIAN_LOG_RETENTION_CLEANUP_INTERVAL_SEC", 3600, minimum=60)


def _split_host_port(raw_value):
    host_port = str(raw_value or "").strip()
    if not host_port:
        raise ValueError("TG_PROXY_HOST 不能为空，应填写为 host:port")
    host, sep, port_text = host_port.rpartition(":")
    host = host.strip()
    port_text = port_text.strip()
    if not sep or not host or not port_text:
        raise ValueError(f"TG_PROXY_HOST 格式错误: {host_port}，应填写为 host:port")
    try:
        port = int(port_text)
    except (TypeError, ValueError):
        raise ValueError(f"TG_PROXY_HOST 端口无效: {host_port}") from None
    if port <= 0 or port > 65535:
        raise ValueError(f"TG_PROXY_HOST 端口超出范围: {host_port}")
    return host, port


def _build_telethon_proxy_config():
    proxy_type = _get_env_str("TG_PROXY_TYPE", "").lower()
    if not proxy_type:
        return None
    if proxy_type not in {"http", "socks5"}:
        raise ValueError(f"TG_PROXY_TYPE 仅支持 http / socks5，当前为: {proxy_type}")
    host, port = _split_host_port(_get_env_str("TG_PROXY_HOST", ""))
    username = _get_env_str("TG_PROXY_USERNAME", "") or None
    password = _get_env_str("TG_PROXY_PASSWORD", "") or None
    if bool(username) != bool(password):
        raise ValueError("TG_PROXY_USERNAME 和 TG_PROXY_PASSWORD 需要同时填写")
    proxy = {
        "proxy_type": proxy_type,
        "addr": host,
        "port": port,
        "rdns": True,
    }
    if username and password:
        proxy["username"] = username
        proxy["password"] = password
    return proxy


def _build_requests_proxies():
    if not TG_PROXY_TYPE:
        return None
    host, port = _split_host_port(TG_PROXY_HOST)
    proxy_scheme = "socks5h" if TG_PROXY_TYPE == "socks5" else "http"
    if TG_PROXY_USERNAME and TG_PROXY_PASSWORD:
        username = quote(TG_PROXY_USERNAME, safe="")
        password = quote(TG_PROXY_PASSWORD, safe="")
        proxy_url = f"{proxy_scheme}://{username}:{password}@{host}:{port}"
    else:
        proxy_url = f"{proxy_scheme}://{host}:{port}"
    return {"http": proxy_url, "https": proxy_url}


API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
TG_PROXY_TYPE = _get_env_str("TG_PROXY_TYPE", "").lower()
TG_PROXY_HOST = _get_env_str("TG_PROXY_HOST", "")
TG_PROXY_USERNAME = _get_env_str("TG_PROXY_USERNAME", "")
TG_PROXY_PASSWORD = _get_env_str("TG_PROXY_PASSWORD", "")
TELETHON_PROXY = _build_telethon_proxy_config()
TG_REQUESTS_PROXIES = _build_requests_proxies()
TIANDAO_MINIAPP_VERIFY_URL = _get_env_str("TIANDAO_MINIAPP_VERIFY_URL", "https://asc.aiopenai.app/miniapp/xianxia-verify")
TIANDAO_MINIAPP_BOT_USERNAME = _get_env_str("TIANDAO_MINIAPP_BOT_USERNAME", "fanrenxiuxian_bot")
LOG_GROUP_ID = int(os.environ["LOG_GROUP_ID"])
LOG_SEND_MODE = str(os.environ.get("LOG_SEND_MODE", "account") or "account").strip().lower()
if LOG_SEND_MODE not in {"account", "bot"}:
    LOG_SEND_MODE = "account"
LOG_BOT_TOKEN = str(os.environ.get("LOG_BOT_TOKEN", "") or "").strip()
try:
    LOG_GROUP_LOW_PRIORITY_SUMMARY_INTERVAL_SEC = max(60, int(os.environ.get("LOG_GROUP_LOW_PRIORITY_SUMMARY_INTERVAL_SEC", "600")))
except (TypeError, ValueError):
    LOG_GROUP_LOW_PRIORITY_SUMMARY_INTERVAL_SEC = 600
try:
    LOG_GROUP_LOW_PRIORITY_SUMMARY_MAX_DETAILS = max(5, int(os.environ.get("LOG_GROUP_LOW_PRIORITY_SUMMARY_MAX_DETAILS", "20")))
except (TypeError, ValueError):
    LOG_GROUP_LOW_PRIORITY_SUMMARY_MAX_DETAILS = 20
_raw_admin_id = str(os.environ.get("ADMIN_ID", "") or "").strip()
ADMIN_IDS: frozenset[int] = frozenset()
_admin_id_parts: list[int] = []
for _part in _raw_admin_id.replace(";", ",").split(","):
    _part = _part.strip()
    if not _part:
        continue
    try:
        _admin_id = int(_part)
    except (TypeError, ValueError):
        raise ValueError(f"ADMIN_ID 包含非数字 {_part!r}，请检查 .env")
    if _admin_id > 0:
        _admin_id_parts.append(_admin_id)
ADMIN_IDS = frozenset(_admin_id_parts)
if not ADMIN_IDS:
    raise ValueError(
        "ADMIN_ID 必须是大于 0 的 Telegram 用户 ID（多个用逗号分隔）。"
        "未设置时日志群任何成员都能触发 .登录 / .全局暂停 等控制指令，已拒绝启动。"
        "请在 .env 中填写 ADMIN_ID=<你的 TG user_id>。"
    )
ADMIN_ID = next(iter(sorted(ADMIN_IDS)))  # 兼容只取第一个的旧引用

GAME_GROUP_ID = -1001680975844  # 游戏主群（初始化默认值，可在 UI 基础配置中修改）
GAME_BOT_IDS = {-1003983937918, 7900199668, 8349385938, 8388633812, 8400307678, 8547797815, 8567800706, 8609885831, 8757550896}  # 游戏 BOT ID（初始化默认值，可在 UI 基础配置中修改）
GAME_TOPIC_ID = 7310786  # 游戏话题 ID（初始化默认值，可在 UI 基础配置中修改）

RETRY_MIN_SEC = 600
RETRY_MAX_SEC = 900
RETRY_LIMIT = 1
MY_MSG_TTL = 3600
MY_MSG_MAX = 1000

PET_CD = 7200
PET_TRIAL_CD = 8 * 3600
YUANYING_CD = 28800
DEEP_RETREAT_CD = 28800
EXPLORE_RIFT_CD = 12 * 3600
EXPLORE_RIFT_JITTER_MIN_SEC = 5 * 60
EXPLORE_RIFT_JITTER_MAX_SEC = 15 * 60
EXPLORE_RIFT_REPLY_TIMEOUT_SEC = 5 * 60
EXPLORE_RIFT_FATAL_GRACE_SEC = 60
MODULE_PROTECT_SEC = 30            # 状态机模块执行后的保护间隔
POST_SUMMARY_WAIT_SEC = 30         # 总结后等待秒数
SUMMARY_TIMEOUT_SEC = 900          # 总结最长等待时间（15分钟，避免 bot 延迟时误判）
YUANYING_PROTECT_SEC = MODULE_PROTECT_SEC
CD_BUFFER_SEC = 5                  # CD 修正时额外缓冲秒数
FREEZE_CD = 9999999                # 冻结某个定时器时使用的极大值
TREE_GUARD_ACTIVE_COUNT = 3        # 每轮入侵最多主动守山的身份数
TREE_GUARD_SELECTION_WINDOW_SEC = 6 * 3600  # 守山身份稳定随机窗口，避免同一轮入侵重启后换人
TREE_GUARD_INITIAL_DELAY_MIN_SEC = 30       # 入侵后首次守山最小延迟
TREE_GUARD_INITIAL_DELAY_MAX_SEC = 180      # 入侵后首次守山最大延迟
GUARD_INTERVAL_MIN = 1500          # 守山重复间隔下限（秒）
GUARD_INTERVAL_MAX = 2100          # 守山重复间隔上限（秒）
IRR_INTERVAL_MIN = 7200            # 灌溉间隔下限（秒）
IRR_INTERVAL_MAX = 7230            # 灌溉间隔上限（秒）
LAUNCHING_TIMEOUT_SEC = 300        # launching 状态超时（5分钟，避免发送成功但回复丢失时长期卡住）
CHECKIN_WINDOW_START_HOUR_UTC = 2  # 宗门点卯窗口开始（UTC+0）
CHECKIN_WINDOW_END_HOUR_UTC = 3    # 宗门点卯窗口结束（UTC+0）
TOWER_WINDOW_START_HOUR_UTC = 1    # 闯塔窗口开始（UTC+0）
TOWER_WINDOW_END_HOUR_UTC = 2      # 闯塔窗口结束（UTC+0）
SECT_TEACH_DELAY_MIN_SEC = 5       # 宗门传功链路最小等待秒数
SECT_TEACH_DELAY_MAX_SEC = 10      # 宗门传功链路最大等待秒数
FLUSH_INTERVAL_SEC = 30            # 脏状态定期写盘间隔
BOT_SILENCE_TIMEOUT_SEC = 600      # bot 静默超时，触发全局暂停（10分钟）
DB_FILE = os.path.abspath(os.environ.get("XIUXIAN_DB_FILE") or os.path.join(STATE_DIR, "chaogu_state.db"))
DB_SCHEMA_VERSION = 8
TZ_LOCAL = timezone(timedelta(hours=8))


CMD_TREE_WATER = ".灵树灌溉"
CMD_TREE_GUARD = ".协同守山"
CMD_TREE_STATUS = ".灵树状态"
CMD_TREE_PULSE_STATUS = ".灵树定脉"
CMD_TREE_PULSE = ".定脉"
CMD_TREE_HARVEST = ".采摘灵果"
CMD_PET = ".抚摸法宝"
CMD_PET_WARM = ".温养器灵"
CMD_PET_TRIAL = ".器灵试炼"
DEFAULT_PET_NAME = "玄天斩灵剑"
CMD_STARGAZER_PANEL = ".观星台"
CMD_STARGAZER_GUIDE = ".牵引星辰"
CMD_STARGAZER_SOOTHE = ".安抚星辰"
CMD_STARGAZER_COLLECT = ".收集精华"
CMD_GUANXING = ".观星"
CMD_GUANXING_SHIFT = ".改换星移"
CMD_TIANTI_STATUS = ".天阶状态"
CMD_TIANTI_WENXIN = ".问心台"
CMD_TIANTI_CLIMB = ".登天阶"
CMD_TIANTI_GANGFENG = ".引九天罡风"
STARGAZER_STAR_CHOICES = ("赤血星", "庚金星", "建木星", "天雷星", "帝魂星")
TIANTI_RANK_CHOICES = ("普通", "长老", "太上长老")
TIANTI_RANK_CD_SECONDS = {
    "普通": 4 * 3600,
    "长老": 3 * 3600,
    "太上长老": 3 * 3600,
}
TIANTI_GANGFENG_CD_SECONDS = 12 * 3600
TIANTI_CD_RANDOM_MIN_SEC = 5
TIANTI_CD_RANDOM_MAX_SEC = 10
GUANXING_SLOT_HOURS = 3
GUANXING_MONITOR_JUDGE_DELAY_SEC = 10 * 60
GUANXING_NOTIFY_ADVANCE_SEC = 10 * 60
GUANXING_EXECUTE_ADVANCE_SEC = 3 * 60
GUANXING_SHIFT_START_DELAY_SEC = 10
GUANXING_TARGET_KEYWORDS = ("地磁暴动", "星辰异象", "封魔裂隙回响")
STARGAZER_STAR_DURATIONS = {
    "赤血星": 4 * 3600,
    "庚金星": 6 * 3600,
    "建木星": 8 * 3600,
    "天雷星": 36 * 3600,
    "帝魂星": 48 * 3600,
}
CMD_YUANYING = ".元婴出窍"
CMD_YUANYING_STATUS = ".元婴状态"
CMD_EXPLORE_RIFT = ".探寻裂缝"
CMD_WENDAO = ".问道"
CMD_DUEL = ".斗法"
CMD_DEEP_RETREAT = ".深度闭关"
CMD_DEEP_RETREAT_QUERY = ".查看闭关"
CMD_IDENTITY_INFO = ".我的灵根"
CMD_CHECKIN = ".宗门点卯"
CMD_SECT_TEACH = ".宗门传功"
CMD_TOWER = ".闯塔"
CMD_QUIZ_ANSWER = ".作答"
CMD_TIANDAO_JUDGEMENT_PROVE = ".自证"
CMD_JIYIN_OFFER_SOUL = ".献上魂魄"
CMD_JIYIN_HIDE_AURA = ".收敛气息"
CMD_NANLONG_EXCHANGE_FABAO = ".交换 法宝"
CMD_NANLONG_EXCHANGE_GONGFA = ".交换 功法"
CMD_NANLONG_REJECT = ".拒绝交易"
CMD_CONCUBINE_PLACE = ".安置侍妾"
CMD_CONCUBINE_RECALL = ".召回侍妾"
CMD_CONCUBINE_STATUS = ".我的侍妾"
CMD_CONCUBINE_DAILY_GREET = ".每日问安"
CMD_CONCUBINE_GIFT_STONE = ".赠予侍妾"
CMD_CONCUBINE_DREAM = ".入梦寻图"
CMD_CONCUBINE_FRAGMENT = ".残图"
CMD_CONCUBINE_PUZZLE = ".拼图"
CMD_CONCUBINE_SECT_MARRY = ".宗门赐婚"
CMD_CONCUBINE_ROMANCE = ".红尘寻缘"
CMD_CONCUBINE_TIANJI = ".天机代卜"
CMD_CONCUBINE_HEART = ".共历心劫"
CMD_CONCUBINE_HEART_STEADY = ".稳"
CMD_CONCUBINE_VOYAGE = ".侍妾远航"
CMD_CONCUBINE_VOYAGE_RETURN = ".远航归来"
CMD_CONCUBINE_VOYAGE_STATUS = ".远航状态"
CMD_FORMATION_START = ".启阵"
CMD_FORMATION_ASSIST = ".助阵"
CMD_HEHUAN_RETREAT = ".闭关双修"
CMD_HEHUAN_CONTRACT = ".缔结同参"
CMD_HEHUAN_DUAL = ".双修"
CMD_HEHUAN_SEAL = ".种下心印"
CMD_HEHUAN_ESCAPE = ".挣脱心印"
CMD_TIANXING_HELP = ".天星宗帮助"
CMD_TIANXING_PANEL = ".天机盘"
CMD_TIANXING_OBSERVE = ".观命"
CMD_TIANXING_SET_STAR = ".定命"
CMD_TIANXING_PREDICT = ".推命"
CMD_TIANXING_CHANGE_FATE = ".改命"
CMD_TIANXING_CLEAR_CALAMITY = ".消劫"
CMD_YINLUO_GUIDE = ".阴罗宗帮助"
CMD_YINLUO_BANNER = ".我的阴罗幡"
CMD_YINLUO_BLOOD_FOREST = ".血洗山林"
CMD_YINLUO_DEMON_SUMMON = ".召唤魔影"
CMD_YINLUO_DAILY_SACRIFICE = ".每日献祭"
CMD_YINLUO_CONVERT = ".化功为煞"
CMD_YINLUO_COLLECT = ".收取精华"
CMD_YINLUO_REFINE = ".囚禁魂魄"
CMD_YINLUO_SOOTHE = ".安抚幡灵"
CMD_YINLUO_CURSE = ".下咒"
CMD_YINLUO_POSSESS = ".夺舍"
CMD_WORLD_BOSS_STATUS = ".世界boss"
CMD_QINGYUANZI_SUPPRESS = ".讨伐青元子 镇魂"
CMD_QINGYUANZI_GUARD = ".讨伐青元子 护阵"
CMD_QINGYUANZI_ATTACK = ".讨伐青元子 强攻"
CMD_QINGYUANZI_BREAK = ".讨伐青元子 破幡"
CMD_SMALL_WORLD_QUERY = ".小世界"
CMD_SMALL_WORLD_MANIFEST = ".显灵"
CMD_SMALL_WORLD_HARVEST = ".收割香火"
CMD_SMALL_WORLD_REFINE = ".神识淬炼"
CMD_SMALL_WORLD_PREACH = ".神迹 布道"
CMD_SMALL_WORLD_RELIEF = ".神迹 赈灾"
CMD_DIVINATION = ".卜筮问天"
CMD_DIVINATION_EXCHANGE = ".换取"
CMD_RANCH = ".一键放养"
CMD_WILD_TRAINING = ".野外历练"
WILD_TRAINING_STRATEGIES = ("谨慎", "均衡", "深入")
CMD_SECOND_SOUL_STATUS = ".第二元神"
CMD_SECOND_SOUL_TRAIN = ".元神修炼"
CMD_SECOND_SOUL_CHOICE_BREAK = ".抉择 强行突破"
CMD_SECOND_SOUL_CHOICE_STABLE = ".抉择 稳固道心"
CMD_YINDAO = ".引道"
CMD_NODE_SEARCH = ".搜寻节点"
CMD_NODE_DEFINE = ".定星"
QUIZ_BANK_FILE = os.path.join(DATA_DIR, "quiz", "quiz_bank.json")
TIANJI_QUIZ_BANK_FILE = os.path.join(DATA_DIR, "quiz", "tianji_quiz_bank.json")
QUIZ_REPLY_TIMEOUT_SEC = 300
JIYIN_REPLY_TIMEOUT_SEC = 180 * 60
NANLONG_REPLY_TIMEOUT_SEC = 10 * 60
NANLONG_REPLY_DELAY_MIN_SEC = 20
NANLONG_REPLY_DELAY_MAX_SEC = 30
CONCUBINE_DREAM_CD_SEC = 8 * 3600
CONCUBINE_PHASE_TIMEOUT_SEC = 15 * 60
CONCUBINE_NO_PARTNER_RETRY_SEC = 12 * 3600
CONCUBINE_STATUS_STALE_SEC = 12 * 3600
CONCUBINE_STATUS_RECHECK_MIN_SEC = 30 * 60
CONCUBINE_STATUS_RECHECK_MAX_SEC = 60 * 60
CONCUBINE_CHAIN_DELAY_MIN_SEC = 10
CONCUBINE_CHAIN_DELAY_MAX_SEC = 30
CONCUBINE_REACQUIRE_RETRY_SEC = 12 * 3600
CONCUBINE_TIANJI_CD_SEC = 12 * 3600
CONCUBINE_HEART_CD_SEC = 12 * 3600
CONCUBINE_HEART_CHOICE_DELAY_MIN_SEC = 1
CONCUBINE_HEART_CHOICE_DELAY_MAX_SEC = 3
CONCUBINE_VOYAGE_DEFAULT_ROUTE = "冒险"
CONCUBINE_VOYAGE_REPLY_TIMEOUT_SEC = 3
FORMATION_INVITE_TTL_SEC = 60
FORMATION_ASSIST_DELAY_MIN_SEC = 3
FORMATION_ASSIST_DELAY_MAX_SEC = 8
FORMATION_ASSIST_REPLY_TIMEOUT_SEC = 30
FORMATION_SUCCESS_COOLDOWN_SEC = 12 * 3600
FORMATION_RECOVERY_DELAY_SEC = 5 * 60
WENDAO_CD = 12 * 3600
WENDAO_FAST_CD = int(WENDAO_CD * 0.7)
WENDAO_JITTER_MIN_SEC = 5
WENDAO_JITTER_MAX_SEC = 10
WENDAO_REPLY_TIMEOUT_SEC = 180
SMALL_WORLD_PREACH_REPLY_TIMEOUT_SEC = 60


SECOND_SOUL_TRAIN_CD_SEC = 24 * 3600
SECOND_SOUL_INJURED_NO_REMAIN_CD_SEC = 6 * 3600
SECOND_SOUL_RECHECK_MIN = 30 * 60
SECOND_SOUL_RECHECK_MAX = 60 * 60
SECOND_SOUL_PENDING_TIMEOUT_MIN = 30 * 60
SECOND_SOUL_PENDING_TIMEOUT_MAX = 60 * 60
SECOND_SOUL_HEART_DEMON_DEADLINE_SEC = 3600
SECOND_SOUL_NOT_UNLOCKED_RETRY_SEC = 7 * 24 * 3600

TAIYI_VALID_ELEMENTS = {"金", "木", "水", "火", "土"}
TAIYI_CYCLE_CD_SEC = 12 * 3600
TAIYI_CYCLE_JITTER_SEC = 30 * 60
TAIYI_RESOURCE_RETRY_SEC = 60 * 60
TAIYI_FROZEN_RETRY_SEC = 7 * 24 * 3600
TAIYI_PHASE_TIMEOUT_SEC = 15 * 60
TAIYI_DEFINE_DELAY_MIN = 1.5
TAIYI_DEFINE_DELAY_MAX = 3.5
TAIYI_FAILURE_WINDOW_SEC = 24 * 3600
TAIYI_FAILURE_LIMIT = 5


CMD_BATTLE_POWER = ".战力"


def format_identity_info_command():
    return CMD_IDENTITY_INFO


def format_battle_power_command(username=None):
    normalized_username = (username or "").strip().lstrip("@")
    if not normalized_username:
        return CMD_BATTLE_POWER
    return f"{CMD_BATTLE_POWER} @{normalized_username}"


def is_identity_info_command_text(text):
    raw_text = (text or "").strip()
    return raw_text == CMD_IDENTITY_INFO


def is_battle_power_command_text(text):
    raw_text = (text or "").strip()
    return raw_text == CMD_BATTLE_POWER or raw_text.startswith(f"{CMD_BATTLE_POWER} ")


def is_identity_refresh_command_text(text):
    return is_identity_info_command_text(text) or is_battle_power_command_text(text)


SCRIPT_COMMANDS = [
    CMD_TREE_WATER,
    CMD_TREE_GUARD,
    CMD_TREE_STATUS,
    CMD_TREE_PULSE_STATUS,
    CMD_TREE_PULSE,
    CMD_TREE_HARVEST,
    CMD_PET,
    CMD_PET_TRIAL,
    CMD_STARGAZER_PANEL,
    CMD_STARGAZER_GUIDE,
    CMD_STARGAZER_SOOTHE,
    CMD_STARGAZER_COLLECT,
    CMD_GUANXING,
    CMD_GUANXING_SHIFT,
    CMD_TIANTI_STATUS,
    CMD_TIANTI_WENXIN,
    CMD_TIANTI_CLIMB,
    CMD_TIANTI_GANGFENG,
    CMD_YUANYING,
    CMD_YUANYING_STATUS,
    CMD_EXPLORE_RIFT,
    CMD_WENDAO,
    CMD_DUEL,
    CMD_DEEP_RETREAT,
    CMD_DEEP_RETREAT_QUERY,
    CMD_IDENTITY_INFO,
    CMD_BATTLE_POWER,
    CMD_CHECKIN,
    CMD_SECT_TEACH,
    CMD_TOWER,
    CMD_QUIZ_ANSWER,
    CMD_TIANDAO_JUDGEMENT_PROVE,
    CMD_JIYIN_OFFER_SOUL,
    CMD_JIYIN_HIDE_AURA,
    CMD_NANLONG_EXCHANGE_FABAO,
    CMD_NANLONG_EXCHANGE_GONGFA,
    CMD_NANLONG_REJECT,
    CMD_CONCUBINE_PLACE,
    CMD_CONCUBINE_RECALL,
    CMD_CONCUBINE_STATUS,
    CMD_CONCUBINE_DAILY_GREET,
    CMD_CONCUBINE_GIFT_STONE,
    CMD_CONCUBINE_DREAM,
    CMD_CONCUBINE_FRAGMENT,
    CMD_CONCUBINE_PUZZLE,
    CMD_CONCUBINE_SECT_MARRY,
    CMD_CONCUBINE_ROMANCE,
    CMD_CONCUBINE_TIANJI,
    CMD_CONCUBINE_HEART,
    CMD_CONCUBINE_VOYAGE,
    CMD_CONCUBINE_VOYAGE_RETURN,
    CMD_CONCUBINE_VOYAGE_STATUS,
    CMD_FORMATION_START,
    CMD_FORMATION_ASSIST,
    CMD_HEHUAN_RETREAT,
    CMD_HEHUAN_CONTRACT,
    CMD_HEHUAN_DUAL,
    CMD_HEHUAN_SEAL,
    CMD_HEHUAN_ESCAPE,
    CMD_TIANXING_HELP,
    CMD_TIANXING_PANEL,
    CMD_TIANXING_OBSERVE,
    CMD_TIANXING_SET_STAR,
    CMD_TIANXING_PREDICT,
    CMD_TIANXING_CHANGE_FATE,
    CMD_TIANXING_CLEAR_CALAMITY,
    CMD_YINLUO_GUIDE,
    CMD_YINLUO_BANNER,
    CMD_YINLUO_BLOOD_FOREST,
    CMD_YINLUO_DEMON_SUMMON,
    CMD_YINLUO_DAILY_SACRIFICE,
    CMD_YINLUO_CONVERT,
    CMD_YINLUO_COLLECT,
    CMD_YINLUO_REFINE,
    CMD_YINLUO_SOOTHE,
    CMD_YINLUO_CURSE,
    CMD_YINLUO_POSSESS,
    CMD_WORLD_BOSS_STATUS,
    CMD_QINGYUANZI_SUPPRESS,
    CMD_QINGYUANZI_GUARD,
    CMD_QINGYUANZI_ATTACK,
    CMD_QINGYUANZI_BREAK,
    CMD_SMALL_WORLD_QUERY,
    CMD_SMALL_WORLD_MANIFEST,
    CMD_SMALL_WORLD_HARVEST,
    CMD_SMALL_WORLD_REFINE,
    CMD_SMALL_WORLD_PREACH,
    CMD_SMALL_WORLD_RELIEF,
    CMD_DIVINATION,
    CMD_DIVINATION_EXCHANGE,
    CMD_RANCH,
    CMD_WILD_TRAINING,
    CMD_SECOND_SOUL_STATUS,
    CMD_SECOND_SOUL_TRAIN,
    CMD_SECOND_SOUL_CHOICE_BREAK,
    CMD_SECOND_SOUL_CHOICE_STABLE,
    CMD_YINDAO,
    CMD_NODE_SEARCH,
    CMD_NODE_DEFINE,
    "1",
]
DIVINATION_DEFAULT_DAILY_LIMIT = 6
MODULE_NAMES = ["灵树", "法宝", "温养器灵", "器灵试炼", "放养", "野外历练", "观星台", "观星监控", "观星", "周天星斗", "登天阶", "玄骨考校", "极阴祖师", "侍妾", "天机代卜", "共历心劫", "侍妾远航", "合欢宗", "天星宗", "阴罗宗", "真仙试锋", "南陇侯", "元婴", "探寻裂缝", "问道", "斗法", "深度闭关", "小世界", "卜筮问天", "点卯", "宗门传功", "闯塔", "第二元神", "太一", "自动副本"]
MODULE_KEY_MAP = {
    "灵树": "tree_enabled",
    "法宝": "pet_enabled",
    "温养器灵": "pet_warm_enabled",
    "器灵试炼": "pet_trial_enabled",
    "放养": "ranch_enabled",
    "野外历练": "wild_training_enabled",
    "观星台": "stargazer_enabled",
    "观星": "guanxing_enabled",
    "观星监控": "guanxing_monitor_enabled",
    "周天星斗": "formation_enabled",
    "登天阶": "tianti_enabled",
    "玄骨考校": "quiz_enabled",
    "极阴祖师": "jiyin_enabled",
    "侍妾": "concubine_enabled",
    "天机代卜": "concubine_tianji_enabled",
    "共历心劫": "concubine_heart_enabled",
    "侍妾远航": "concubine_voyage_enabled",
    "合欢宗": "hehuan_enabled",
    "天星宗": "tianxing_enabled",
    "阴罗宗": "yinluo_enabled",
    "真仙试锋": "world_boss_enabled",
    "南陇侯": "nanlong_enabled",
    "元婴": "yuanying_enabled",
    "探寻裂缝": "explore_rift_enabled",
    "问道": "wendao_enabled",
    "斗法": "duel_enabled",
    "深度闭关": "deep_retreat_enabled",
    "小世界": "small_world_enabled",
    "卜筮问天": "divination_enabled",
    "点卯": "checkin_enabled",
    "宗门传功": "sect_teach_enabled",
    "闯塔": "tower_enabled",
    "第二元神": "second_soul_enabled",
    "太一": "taiyi_enabled",
    "自动副本": "dungeon_join_enabled",
}
CMD_DUNGEON_JOIN = ".加入副本"
CMD_DUNGEON_ZHUIMO_JOIN = ".加入坠魔谷"
CMD_DUNGEON_HUANGLONG_JOIN = ".加入黄龙山"
CMD_REPLICA_JOIN = CMD_DUNGEON_JOIN
CMD_REPLICA_ZHUIMO_JOIN = CMD_DUNGEON_ZHUIMO_JOIN
CMD_REPLICA_HUANGLONG_JOIN = CMD_DUNGEON_HUANGLONG_JOIN
CMD_REPLICA_CANGKUN_JOIN = ".加入苍坤洞府"
CMD_REPLICA_KUNWU_JOIN = ".加入昆吾山"
CMD_REPLICA_LUOYUN_JOIN = ".加入落云秘圃"
REPLICA_SUCCESS_COOLDOWN_SEC = 125 * 60
REPLICA_ZHUIMO_SUCCESS_COOLDOWN_SEC = 2 * 60 * 60
REPLICA_CANGKUN_SUCCESS_COOLDOWN_SEC = 6 * 60 * 60
REPLICA_ACTIVE_TTL_SEC = 2 * 60 * 60
REPLICA_FAILURE_GRACE_SEC = 3 * 60
UI_HOST = os.environ.get("CHAOGU_UI_HOST", "0.0.0.0")
try:
    UI_PORT = int(os.environ.get("CHAOGU_UI_PORT", "3030"))
except (TypeError, ValueError):
    UI_PORT = 3030


def _fetch_public_ip():
    lookup_urls = [
        "https://api.ipify.org",
        "https://ifconfig.me/ip",
        "https://ipv4.icanhazip.com",
    ]
    with requests.Session() as session:
        session.trust_env = False
        for lookup_url in lookup_urls:
            try:
                response = session.get(lookup_url, timeout=(3, 3), proxies={})
                ip_text = (response.text or "").strip()
                if response.ok and re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", ip_text):
                    return ip_text
            except Exception:
                continue
    return ""


raw_public_base_url = (os.environ.get("CHAOGU_UI_PUBLIC_BASE_URL") or "").strip().rstrip("/")
if raw_public_base_url:
    UI_PUBLIC_BASE_URL = raw_public_base_url
else:
    detected_public_ip = _fetch_public_ip()
    public_host = detected_public_ip or os.environ.get("CHAOGU_UI_FALLBACK_HOST", "127.0.0.1").strip() or "127.0.0.1"
    UI_PUBLIC_BASE_URL = f"http://{public_host}:{UI_PORT}"

try:
    UI_AUTO_REFRESH_SEC = max(1, int(os.environ.get("CHAOGU_UI_AUTO_REFRESH_SEC", "15")))
except (TypeError, ValueError):
    UI_AUTO_REFRESH_SEC = 15
try:
    UI_AUTH_IDLE_TIMEOUT_SEC = max(60, int(os.environ.get("CHAOGU_UI_AUTH_IDLE_TIMEOUT_SEC", "3600")))
except (TypeError, ValueError):
    UI_AUTH_IDLE_TIMEOUT_SEC = 3600
try:
    UI_AUTH_SESSION_TIMEOUT_SEC = max(60, int(os.environ.get("CHAOGU_UI_AUTH_SESSION_TIMEOUT_SEC", "604800")))
except (TypeError, ValueError):
    UI_AUTH_SESSION_TIMEOUT_SEC = 604800
UI_AUTH_COOKIE_NAME = (os.environ.get("CHAOGU_UI_AUTH_COOKIE_NAME") or "chaogu_ui_session").strip() or "chaogu_ui_session"


def prepare_storage_dirs():
    os.makedirs(SESSION_DIR, exist_ok=True)
    os.makedirs(STATE_DIR, exist_ok=True)
    os.makedirs(MESSAGES_DIR, exist_ok=True)


prepare_storage_dirs()
os.environ['PYTHONUNBUFFERED'] = '1'


def _normalize_telegram_api(api_id=None, api_hash=None):
    api_id_value = API_ID
    api_hash_value = API_HASH
    if api_id not in {None, ""}:
        try:
            api_id_value = int(api_id)
        except (TypeError, ValueError):
            raise ValueError("API_ID 必须是数字") from None
        if api_id_value <= 0:
            raise ValueError("API_ID 必须大于 0")
    if api_hash not in {None, ""}:
        api_hash_value = str(api_hash or "").strip()
        if not api_hash_value:
            raise ValueError("API_HASH 不能为空")
    if (api_id not in {None, ""}) != (api_hash not in {None, ""}):
        raise ValueError("API_ID 和 API_HASH 需要同时填写")
    return api_id_value, api_hash_value


def _create_telegram_client(session_path, *, api_id=None, api_hash=None):
    resolved_api_id, resolved_api_hash = _normalize_telegram_api(api_id, api_hash)
    return TelegramClient(session_path, resolved_api_id, resolved_api_hash, proxy=TELETHON_PROXY)


client = _create_telegram_client(SESSION_FILE)

# ================= 多账号 client 管理 =================
_clients: dict[int, TelegramClient] = {}  # account_id → TelegramClient
_offline_accounts: dict[int, dict] = {}  # account_id → {"reason": str, "marked_at": float}


def get_client(account_id=None):
    if account_id is None:
        return client
    return _clients.get(int(account_id), client)


def register_client(account_id, tc):
    account_id = int(account_id)
    _clients[account_id] = tc
    mark_account_online(account_id)


def unregister_client(account_id):
    _clients.pop(int(account_id), None)


def get_all_clients():
    return dict(_clients)


def get_registered_client(account_id):
    return _clients.get(int(account_id))


def mark_account_offline(account_id, reason=""):
    account_id = int(account_id)
    if account_id <= 0:
        return False
    reason = str(reason or "账号不可用").strip() or "账号不可用"
    previous = _offline_accounts.get(account_id) or {}
    changed = previous.get("reason") != reason
    _offline_accounts[account_id] = {
        "reason": reason,
        "marked_at": time.time(),
    }
    return changed


def mark_account_online(account_id):
    return _offline_accounts.pop(int(account_id), None) is not None


def is_account_offline(account_id):
    return int(account_id or 0) in _offline_accounts


def get_account_offline_reason(account_id):
    info = _offline_accounts.get(int(account_id or 0)) or {}
    return str(info.get("reason") or "").strip()


def create_account_client(account_id, *, api_id=None, api_hash=None):
    session_path = os.path.join(SESSION_DIR, f"account_{account_id}")
    return _create_telegram_client(session_path, api_id=api_id, api_hash=api_hash)

# ================= 预编译正则 =================
RE_HOURS = re.compile(r'(\d+)\s*小时')
RE_MINUTES = re.compile(r'(\d+)\s*分钟')
RE_SECONDS = re.compile(r'(\d+)\s*秒')
RE_WHITESPACE = re.compile(r"\s+")
RE_TREE_REMAINING = re.compile(r'剩余[：:]\s*([\s\S]+?)(?:\n|$)')

RE_CMD_ENABLE_ALL = re.compile(r'^\.(开启|打开)全部$')
RE_CMD_DISABLE_ALL = re.compile(r'^\.(关闭|关掉)全部$')
RE_CMD_GLOBAL_PAUSE = re.compile(r'^\.全局暂停$')
RE_CMD_GLOBAL_RESUME = re.compile(r'^\.全局(恢复|启动)$')
RE_CMD_LOGIN = re.compile(r'^\.登录$')
RE_CMD_HELP = re.compile(r'^\.(指令|帮助|help)$', re.I)
RE_CMD_STATUS = re.compile(r'^\.(状态|模块状态)$')
RE_CMD_ANALYSIS_SUMMARY = re.compile(r'^\.(离线分析|分析状态|玩法总览|指令总览)$')
RE_CMD_ANALYSIS_HEALTH = re.compile(r'^\.(发送健康码|发送健康|运行健康码|健康码|分析健康)$')
RE_CMD_RUNTIME_HEALTH = re.compile(r'^\.(运行健康|健康摘要|实时健康|运行摘要)$')
RE_CMD_ANALYSIS_LOG_GROUP = re.compile(r'^\.(日志群分析|日志群指令)$')
RE_CMD_ANALYSIS_WEBMINI = re.compile(r'^\.(webmini分析|webmini吸收|miniweb分析|miniweb吸收)$', re.I)
RE_CMD_ANALYSIS_UNKNOWN = re.compile(r'^\.(未知指令|未归类指令)$')
RE_CMD_STAGING_PREFLIGHT = re.compile(r'^\.(上线预检|待上线预检|预检|上线检查|预发布检查)$')
RE_CMD_AUDIT_PUSH_STATUS = re.compile(r'^\.(日志推送状态|推送状态|日志优先级|日志汇总状态|审计推送状态)$')
RE_CMD_AUDIT_FLUSH_SUMMARY = re.compile(r'^\.(发送日志汇总|刷新日志汇总|立即日志汇总|日志汇总)$')
RE_CMD_SINGLE_STATUS_PATTERNS = [
    (re.compile(r'^\.灵树状态$'), "灵树"),
    (re.compile(r'^\.灵树定脉$'), "灵树"),
    (re.compile(r'^\.法宝状态$'), "法宝"),
    (re.compile(r'^\.温养器灵状态$'), "温养器灵"),
    (re.compile(r'^\.器灵试炼状态$'), "器灵试炼"),
    (re.compile(r'^\.放养状态$'), "放养"),
    (re.compile(r'^\.野外历练状态$'), "野外历练"),
    (re.compile(r'^\.观星台状态$'), "观星台"),
    (re.compile(r'^\.观星状态$'), "观星"),
    (re.compile(r'^\.观星监控状态$'), "观星监控"),
    (re.compile(r'^\.周天星斗状态$'), "周天星斗"),
    (re.compile(r'^\.天阶状态$'), "登天阶"),
    (re.compile(r'^\.玄骨考校状态$'), "玄骨考校"),
    (re.compile(r'^\.极阴祖师状态$'), "极阴祖师"),
    (re.compile(r'^\.侍妾状态$'), "侍妾"),
    (re.compile(r'^\.天机代卜状态$'), "天机代卜"),
    (re.compile(r'^\.共历心劫状态$'), "共历心劫"),
    (re.compile(r'^\.合欢宗状态$'), "合欢宗"),
    (re.compile(r'^\.天星宗状态$'), "天星宗"),
    (re.compile(r'^\.阴罗宗状态$'), "阴罗宗"),
    (re.compile(r'^\.(真仙试锋|青元子|世界boss)状态$'), "真仙试锋"),
    (re.compile(r'^\.南陇侯状态$'), "南陇侯"),
    (re.compile(r'^\.元婴状态$'), "元婴"),
    (re.compile(r'^\.探寻裂缝状态$'), "探寻裂缝"),
    (re.compile(r'^\.问道状态$'), "问道"),
    (re.compile(r'^\.斗法状态$'), "斗法"),
    (re.compile(r'^\.深度闭关状态$'), "深度闭关"),
    (re.compile(r'^\.第二元神状态$'), "第二元神"),
    (re.compile(r'^\.太一状态$'), "太一"),
    (re.compile(r'^\.小世界状态$'), "小世界"),
    (re.compile(r'^\.卜筮问天状态$'), "卜筮问天"),
    (re.compile(r'^\.点卯状态$'), "点卯"),
    (re.compile(r'^\.宗门传功状态$'), "宗门传功"),
    (re.compile(r'^\.闯塔状态$'), "闯塔"),
    (re.compile(r'^\.(自动副本|副本)状态$'), "自动副本"),
    (re.compile(r'^\.查询副本$'), "自动副本"),
]
RE_CMD_ENABLE_PATTERNS = [
    (re.compile(r'^\.(开启|打开)灵树$'), "灵树", True),
    (re.compile(r'^\.(关闭|关掉)灵树$'), "灵树", False),
    (re.compile(r'^\.(开启|打开)法宝$'), "法宝", True),
    (re.compile(r'^\.(关闭|关掉)法宝$'), "法宝", False),
    (re.compile(r'^\.(开启|打开)温养器灵$'), "温养器灵", True),
    (re.compile(r'^\.(关闭|关掉)温养器灵$'), "温养器灵", False),
    (re.compile(r'^\.(开启|打开)器灵试炼$'), "器灵试炼", True),
    (re.compile(r'^\.(关闭|关掉)器灵试炼$'), "器灵试炼", False),
    (re.compile(r'^\.(开启|打开)放养$'), "放养", True),
    (re.compile(r'^\.(关闭|关掉)放养$'), "放养", False),
    (re.compile(r'^\.(开启|打开)野外历练$'), "野外历练", True),
    (re.compile(r'^\.(关闭|关掉)野外历练$'), "野外历练", False),
    (re.compile(r'^\.(开启|打开)观星台$'), "观星台", True),
    (re.compile(r'^\.(关闭|关掉)观星台$'), "观星台", False),
    (re.compile(r'^\.(开启|打开)观星$'), "观星", True),
    (re.compile(r'^\.(关闭|关掉)观星$'), "观星", False),
    (re.compile(r'^\.(开启|打开)观星监控$'), "观星监控", True),
    (re.compile(r'^\.(关闭|关掉)观星监控$'), "观星监控", False),
    (re.compile(r'^\.(开启|打开)周天星斗$'), "周天星斗", True),
    (re.compile(r'^\.(关闭|关掉)周天星斗$'), "周天星斗", False),
    (re.compile(r'^\.(开启|打开)登天阶$'), "登天阶", True),
    (re.compile(r'^\.(关闭|关掉)登天阶$'), "登天阶", False),
    (re.compile(r'^\.(开启|打开)玄骨考校$'), "玄骨考校", True),
    (re.compile(r'^\.(关闭|关掉)玄骨考校$'), "玄骨考校", False),
    (re.compile(r'^\.(开启|打开)极阴祖师$'), "极阴祖师", True),
    (re.compile(r'^\.(关闭|关掉)极阴祖师$'), "极阴祖师", False),
    (re.compile(r'^\.(开启|打开)侍妾$'), "侍妾", True),
    (re.compile(r'^\.(关闭|关掉)侍妾$'), "侍妾", False),
    (re.compile(r'^\.(开启|打开)天机代卜$'), "天机代卜", True),
    (re.compile(r'^\.(关闭|关掉)天机代卜$'), "天机代卜", False),
    (re.compile(r'^\.(开启|打开)共历心劫$'), "共历心劫", True),
    (re.compile(r'^\.(关闭|关掉)共历心劫$'), "共历心劫", False),
    (re.compile(r'^\.(开启|打开)合欢宗$'), "合欢宗", True),
    (re.compile(r'^\.(关闭|关掉)合欢宗$'), "合欢宗", False),
    (re.compile(r'^\.(开启|打开)天星宗$'), "天星宗", True),
    (re.compile(r'^\.(关闭|关掉)天星宗$'), "天星宗", False),
    (re.compile(r'^\.(开启|打开)阴罗宗$'), "阴罗宗", True),
    (re.compile(r'^\.(关闭|关掉)阴罗宗$'), "阴罗宗", False),
    (re.compile(r'^\.(开启|打开)(真仙试锋|青元子|世界boss)$'), "真仙试锋", True),
    (re.compile(r'^\.(关闭|关掉)(真仙试锋|青元子|世界boss)$'), "真仙试锋", False),
    (re.compile(r'^\.(开启|打开)南陇侯$'), "南陇侯", True),
    (re.compile(r'^\.(关闭|关掉)南陇侯$'), "南陇侯", False),
    (re.compile(r'^\.(开启|打开)元婴$'), "元婴", True),
    (re.compile(r'^\.(关闭|关掉)元婴$'), "元婴", False),
    (re.compile(r'^\.(开启|打开)探寻裂缝$'), "探寻裂缝", True),
    (re.compile(r'^\.(关闭|关掉)探寻裂缝$'), "探寻裂缝", False),
    (re.compile(r'^\.(开启|打开)问道$'), "问道", True),
    (re.compile(r'^\.(关闭|关掉)问道$'), "问道", False),
    (re.compile(r'^\.(开启|打开)斗法$'), "斗法", True),
    (re.compile(r'^\.(关闭|关掉)斗法$'), "斗法", False),
    (re.compile(r'^\.(开启|打开)深度闭关$'), "深度闭关", True),
    (re.compile(r'^\.(关闭|关掉)深度闭关$'), "深度闭关", False),
    (re.compile(r'^\.(开启|打开)小世界$'), "小世界", True),
    (re.compile(r'^\.(关闭|关掉)小世界$'), "小世界", False),
    (re.compile(r'^\.(开启|打开)卜筮问天$'), "卜筮问天", True),
    (re.compile(r'^\.(关闭|关掉)卜筮问天$'), "卜筮问天", False),
    (re.compile(r'^\.(开启|打开)点卯$'), "点卯", True),
    (re.compile(r'^\.(关闭|关掉)点卯$'), "点卯", False),
    (re.compile(r'^\.(开启|打开)宗门传功$'), "宗门传功", True),
    (re.compile(r'^\.(关闭|关掉)宗门传功$'), "宗门传功", False),
    (re.compile(r'^\.(开启|打开)闯塔$'), "闯塔", True),
    (re.compile(r'^\.(关闭|关掉)闯塔$'), "闯塔", False),
    (re.compile(r'^\.(开启|打开)第二元神$'), "第二元神", True),
    (re.compile(r'^\.(关闭|关掉)第二元神$'), "第二元神", False),
    (re.compile(r'^\.(开启|打开)太一$'), "太一", True),
    (re.compile(r'^\.(关闭|关掉)太一$'), "太一", False),
    (re.compile(r'^\.(开启|打开)自动副本$'), "自动副本", True),
    (re.compile(r'^\.(关闭|关掉)自动副本$'), "自动副本", False),
]
