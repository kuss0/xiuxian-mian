import os
import re
from datetime import timedelta, timezone
from urllib.parse import quote

import requests
from telethon import TelegramClient

APP_DIR = os.path.dirname(__file__)
PROJECT_ROOT_DIR = os.path.dirname(APP_DIR)
DATA_DIR = os.path.join(PROJECT_ROOT_DIR, "data")
SESSION_DIR = os.path.join(DATA_DIR, "session")
STATE_DIR = os.path.join(DATA_DIR, "state")
MESSAGES_DIR = os.path.join(DATA_DIR, "messages")
SESSION_FILE = os.path.join(SESSION_DIR, "ai_investor_session")

# ================= 从 .env 读取启动配置 =================
def _load_dotenv():
    env_path = os.path.join(PROJECT_ROOT_DIR, ".env")
    if not os.path.exists(env_path):
        raise FileNotFoundError(f"missing .env: {env_path}")
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
            os.environ[key] = value


_load_dotenv()


def _get_env_str(key, default=""):
    return str(os.environ.get(key, default) or default).strip()


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
LOG_GROUP_ID = int(os.environ["LOG_GROUP_ID"])
LOG_SEND_MODE = str(os.environ.get("LOG_SEND_MODE", "account") or "account").strip().lower()
if LOG_SEND_MODE not in {"account", "bot"}:
    LOG_SEND_MODE = "account"
LOG_BOT_TOKEN = str(os.environ.get("LOG_BOT_TOKEN", "") or "").strip()
ADMIN_ID = int(os.environ.get("ADMIN_ID", 0) or 0)

GAME_GROUP_ID = -1001680975844  # 游戏主群（初始化默认值，可在 UI 基础配置中修改）
GAME_BOT_IDS = {8388633812}  # 游戏 BOT ID（初始化默认值，可在 UI 基础配置中修改）
GAME_TOPIC_ID = 7310786  # 游戏话题 ID（初始化默认值，可在 UI 基础配置中修改）

RETRY_MIN_SEC = 60
RETRY_MAX_SEC = 70
RETRY_LIMIT = 3
MY_MSG_TTL = 3600
MY_MSG_MAX = 1000

PET_CD = 7200
YUANYING_CD = 28800
DEEP_RETREAT_CD = 28800
MODULE_PROTECT_SEC = 30            # 状态机模块执行后的保护间隔
POST_SUMMARY_WAIT_SEC = 30         # 总结后等待秒数
SUMMARY_TIMEOUT_SEC = 180          # 总结最长等待时间（3分钟）
YUANYING_PROTECT_SEC = MODULE_PROTECT_SEC
CD_BUFFER_SEC = 5                  # CD 修正时额外缓冲秒数
FREEZE_CD = 9999999                # 冻结某个定时器时使用的极大值
GUARD_INTERVAL_MIN = 300           # 守山间隔下限（秒）
GUARD_INTERVAL_MAX = 330           # 守山间隔上限（秒）
IRR_INTERVAL_MIN = 7200            # 灌溉间隔下限（秒）
IRR_INTERVAL_MAX = 7230            # 灌溉间隔上限（秒）
LAUNCHING_TIMEOUT_SEC = 120        # launching 状态超时（2分钟）
CHECKIN_WINDOW_START_HOUR_UTC = 2  # 宗门点卯窗口开始（UTC+0）
CHECKIN_WINDOW_END_HOUR_UTC = 3    # 宗门点卯窗口结束（UTC+0）
TOWER_WINDOW_START_HOUR_UTC = 1    # 闯塔窗口开始（UTC+0）
TOWER_WINDOW_END_HOUR_UTC = 2      # 闯塔窗口结束（UTC+0）
SECT_TEACH_DELAY_MIN_SEC = 5       # 宗门传功链路最小等待秒数
SECT_TEACH_DELAY_MAX_SEC = 10      # 宗门传功链路最大等待秒数
FLUSH_INTERVAL_SEC = 30            # 脏状态定期写盘间隔
BOT_SILENCE_TIMEOUT_SEC = 1800     # bot 静默超时，触发全局暂停（30分钟）
DB_FILE = os.path.join(STATE_DIR, "chaogu_state.db")
DB_SCHEMA_VERSION = 5
TZ_LOCAL = timezone(timedelta(hours=8))


CMD_TREE_WATER = ".灵树灌溉"
CMD_TREE_GUARD = ".协同守山"
CMD_TREE_STATUS = ".灵树状态"
CMD_TREE_HARVEST = ".采摘灵果"
CMD_PET = ".抚摸法宝"
DEFAULT_PET_NAME = "玄天斩灵剑"
CMD_STARGAZER_PANEL = ".观星台"
CMD_STARGAZER_GUIDE = ".牵引星辰"
CMD_STARGAZER_SOOTHE = ".安抚星辰"
CMD_STARGAZER_COLLECT = ".收集精华"
STARGAZER_STAR_CHOICES = ("赤血星", "庚金星", "建木星", "天雷星", "帝魂星")
GUANXING_SLOT_HOURS = 3
GUANXING_NOTIFY_ADVANCE_SEC = 10 * 60
GUANXING_TARGET_KEYWORDS = ("地磁暴动", "星辰异象")
STARGAZER_STAR_DURATIONS = {
    "赤血星": 4 * 3600,
    "庚金星": 6 * 3600,
    "建木星": 8 * 3600,
    "天雷星": 36 * 3600,
    "帝魂星": 48 * 3600,
}
CMD_YUANYING = ".元婴出窍"
CMD_YUANYING_STATUS = ".元婴状态"
CMD_DEEP_RETREAT = ".深度闭关"
CMD_DEEP_RETREAT_QUERY = ".查看闭关"
CMD_IDENTITY_INFO = ".我的灵根"
CMD_CHECKIN = ".宗门点卯"
CMD_SECT_TEACH = ".宗门传功"
CMD_TOWER = ".闯塔"
CMD_QUIZ_ANSWER = ".作答"
CMD_JIYIN_OFFER_SOUL = ".献上魂魄"
CMD_JIYIN_HIDE_AURA = ".收敛气息"
QUIZ_BANK_FILE = os.path.join(DATA_DIR, "quiz", "quiz_bank.json")
QUIZ_REPLY_TIMEOUT_SEC = 300
JIYIN_REPLY_TIMEOUT_SEC = 180 * 60


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
    CMD_TREE_HARVEST,
    CMD_PET,
    CMD_STARGAZER_PANEL,
    CMD_STARGAZER_GUIDE,
    CMD_STARGAZER_SOOTHE,
    CMD_STARGAZER_COLLECT,
    CMD_YUANYING,
    CMD_YUANYING_STATUS,
    CMD_DEEP_RETREAT,
    CMD_DEEP_RETREAT_QUERY,
    CMD_IDENTITY_INFO,
    CMD_BATTLE_POWER,
    CMD_CHECKIN,
    CMD_SECT_TEACH,
    CMD_TOWER,
    CMD_QUIZ_ANSWER,
    CMD_JIYIN_OFFER_SOUL,
    CMD_JIYIN_HIDE_AURA,
    "1",
]
MODULE_NAMES = ["灵树", "法宝", "观星台", "观星", "玄骨考校", "极阴祖师", "元婴", "深度闭关", "点卯", "闯塔"]
MODULE_KEY_MAP = {
    "灵树": "tree_enabled",
    "法宝": "pet_enabled",
    "观星台": "stargazer_enabled",
    "观星": "guanxing_enabled",
    "玄骨考校": "quiz_enabled",
    "极阴祖师": "jiyin_enabled",
    "元婴": "yuanying_enabled",
    "深度闭关": "deep_retreat_enabled",
    "点卯": "checkin_enabled",
    "闯塔": "tower_enabled",
}
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
    UI_AUTH_SESSION_TIMEOUT_SEC = max(60, int(os.environ.get("CHAOGU_UI_AUTH_SESSION_TIMEOUT_SEC", "86400")))
except (TypeError, ValueError):
    UI_AUTH_SESSION_TIMEOUT_SEC = 86400
UI_AUTH_COOKIE_NAME = (os.environ.get("CHAOGU_UI_AUTH_COOKIE_NAME") or "chaogu_ui_session").strip() or "chaogu_ui_session"


def prepare_storage_dirs():
    os.makedirs(SESSION_DIR, exist_ok=True)
    os.makedirs(STATE_DIR, exist_ok=True)
    os.makedirs(MESSAGES_DIR, exist_ok=True)


prepare_storage_dirs()
os.environ['PYTHONUNBUFFERED'] = '1'


def _create_telegram_client(session_path):
    return TelegramClient(session_path, API_ID, API_HASH, proxy=TELETHON_PROXY)


client = _create_telegram_client(SESSION_FILE)

# ================= 多账号 client 管理 =================
_clients: dict[int, TelegramClient] = {}  # account_id → TelegramClient


def get_client(account_id=None):
    if account_id is None:
        return client
    return _clients.get(int(account_id), client)


def register_client(account_id, tc):
    _clients[int(account_id)] = tc


def unregister_client(account_id):
    _clients.pop(int(account_id), None)


def get_all_clients():
    return dict(_clients)


def create_account_client(account_id):
    session_path = os.path.join(SESSION_DIR, f"account_{account_id}")
    return _create_telegram_client(session_path)

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
RE_CMD_STATUS = re.compile(r'^\.(状态|模块状态)$')
RE_CMD_SINGLE_STATUS_PATTERNS = [
    (re.compile(r'^\.灵树状态$'), "灵树"),
    (re.compile(r'^\.法宝状态$'), "法宝"),
    (re.compile(r'^\.观星台状态$'), "观星台"),
    (re.compile(r'^\.观星状态$'), "观星"),
    (re.compile(r'^\.玄骨考校状态$'), "玄骨考校"),
    (re.compile(r'^\.极阴祖师状态$'), "极阴祖师"),
    (re.compile(r'^\.元婴状态$'), "元婴"),
    (re.compile(r'^\.深度闭关状态$'), "深度闭关"),
    (re.compile(r'^\.点卯状态$'), "点卯"),
    (re.compile(r'^\.闯塔状态$'), "闯塔"),
]
RE_CMD_ENABLE_PATTERNS = [
    (re.compile(r'^\.(开启|打开)灵树$'), "灵树", True),
    (re.compile(r'^\.(关闭|关掉)灵树$'), "灵树", False),
    (re.compile(r'^\.(开启|打开)法宝$'), "法宝", True),
    (re.compile(r'^\.(关闭|关掉)法宝$'), "法宝", False),
    (re.compile(r'^\.(开启|打开)观星台$'), "观星台", True),
    (re.compile(r'^\.(关闭|关掉)观星台$'), "观星台", False),
    (re.compile(r'^\.(开启|打开)观星$'), "观星", True),
    (re.compile(r'^\.(关闭|关掉)观星$'), "观星", False),
    (re.compile(r'^\.(开启|打开)玄骨考校$'), "玄骨考校", True),
    (re.compile(r'^\.(关闭|关掉)玄骨考校$'), "玄骨考校", False),
    (re.compile(r'^\.(开启|打开)极阴祖师$'), "极阴祖师", True),
    (re.compile(r'^\.(关闭|关掉)极阴祖师$'), "极阴祖师", False),
    (re.compile(r'^\.(开启|打开)元婴$'), "元婴", True),
    (re.compile(r'^\.(关闭|关掉)元婴$'), "元婴", False),
    (re.compile(r'^\.(开启|打开)深度闭关$'), "深度闭关", True),
    (re.compile(r'^\.(关闭|关掉)深度闭关$'), "深度闭关", False),
    (re.compile(r'^\.(开启|打开)点卯$'), "点卯", True),
    (re.compile(r'^\.(关闭|关掉)点卯$'), "点卯", False),
    (re.compile(r'^\.(开启|打开)闯塔$'), "闯塔", True),
    (re.compile(r'^\.(关闭|关掉)闯塔$'), "闯塔", False),
]
