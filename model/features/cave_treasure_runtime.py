import asyncio
import hashlib
import logging
import math
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

from telethon import functions

from ..config import CD_BUFFER_SEC, CMD_TIANTI_STATUS, STATE_DIR
from ..inventory_delta import prepare_inventory_delta, record_inventory_delta, stable_payload_digest
from ..miniapp_state import prepare_miniapp_state, record_miniapp_state
from ..persistence import save_state
from ..runtime import _get_any_authed_client_with_account, account_rpc_slot, console_log, send_audit_log
from ..state import get_game_bot_ids, get_game_group_ids, get_global_enabled, get_global_pause_source, get_identity_account, get_miniapp_auto_config, get_miniapp_state_records, get_send_as_profile, get_storage_bag_records, is_cave_public_identity_available, set_miniapp_auto_config, set_storage_bag_records, state, use_identity
from ..timing import fmt_abs_ts, get_day_key
from ..webapp_core import MiniAppCaptureStore, MiniAppRequestAborted, MiniAppRequestBudget, miniapp_retry_after_sec, require_miniapp_operation
from . import concubine, deep_retreat, fishing_behavior, stargazer, tianti, tree_runtime, yinluo, yuanying
from .small_world import (
    SMALL_WORLD_PREACH_FAITH_RATIO_TRIGGER,
    _calc_refine_amount,
    _parse_wait_from_text,
)
from .cave_treasure_miniapp import (
    _parse_cave_journey_overview,
    _require_cave_action_player_id,
    CAVE_TIANJIGE_READ_ONLY_COMMANDS,
    build_cave_treasure_launch_args,
    cave_action_player_error,
    extract_cave_treasure_miniapp_launch,
    find_cave_external_app,
    merge_cave_dwelling_snapshot_data,
    parse_cave_inventory_snapshot,
    parse_cave_dwelling_overview,
    request_cave_treasure_miniapp_init_data,
    run_cave_deep_seclusion_action_production_flow,
    run_cave_dwelling_start_production_flow,
    run_cave_dwelling_snapshot_production_flow,
    run_cave_external_action_production_flow,
    run_cave_journey_action_production_flow,
    run_cave_meditation_settle_production_flow,
    run_cave_small_world_production_flow,
    run_cave_tianjige_command_production_flow,
    run_cave_treasure_miniapp_production_flow,
)
from .trial_miniapp import build_trial_launch_args
from .treasure_receipts import project_treasure_settlement, treasure_integer, treasure_quota_exhausted, treasure_session_id
from .trial_runtime import _format_trial_summary, _record_trial_business_capture, _run_lock as _trial_run_lock, _trial_batch_materials, _trial_has_settlements, _trial_result_completed_ok, _trial_recovery_response, _trial_miniapp_capture_store, run_trial_miniapp_production_flow
from . import treasure_operations, treasure_results, trial_operations
from .stargazer_miniapp import build_stargazer_launch_args, run_stargazer_miniapp_production_flow
from .tree_miniapp import build_tree_launch_args
from .fishing_miniapp import extract_fishing_miniapp_launch_from_dwelling_payload, run_fishing_miniapp_production_flow
from . import fishing_operations
from .tower_miniapp import build_tower_launch_args, format_tower_delta, run_tower_miniapp_production_flow
from .fate_cards_miniapp import (
    FATE_CARDS_FRONTEND_DEFAULT_QUESTION_KEY,
    extract_fate_cards_launch_from_payload,
    find_fate_cards_external_app,
    normalize_fate_cards_choice_key,
    request_fate_cards_miniapp_init_data,
    run_fate_cards_action_production,
    run_fate_cards_start_probe_production,
)
from .miniapp_common import MiniAppFlowCancelled, MiniAppIdentityOwner, append_business_capture, resolve_identity_id as _identity_id
from .fishing_runtime import (
    FishingMiniAppCommitError,
    FishingMiniAppOperation,
    _apply_fishing_miniapp_result,
    _fishing_miniapp_capture_store,
    _fishing_reset_jitter_sec,
    _fishing_result_commit_response,
    _fishing_send_lock,
    _record_fishing_business_capture,
    _remaining_miniapp_chain_rounds,
    _send_fishing_daily_completion_summary,
    fishing_miniapp_has_confirmed_outcome,
    recover_fishing_result_pending,
)


CAVE_TREASURE_MANUAL_AUTH_TTL_SEC = 10 * 60
CAVE_TREASURE_MANUAL_MAX_STEPS = 48
CAVE_TREASURE_MINIAPP_CAPTURE_DIR = Path(STATE_DIR) / "miniapp_capture"
CAVE_SMALL_WORLD_RESOURCE_PAUSE_SEC = 6 * 3600
CAVE_SMALL_WORLD_CYCLE_SEC = 6 * 3600
CAVE_SMALL_WORLD_HARVEST_INTERVAL_SEC = 8 * 3600
CAVE_SMALL_WORLD_HARVEST_RETRY_SEC = 30 * 60
CAVE_SMALL_WORLD_GOD_COOLDOWN_SEC = 3 * 3600
CAVE_SMALL_WORLD_REFRESH_SEC = 10 * 60
CAVE_SMALL_WORLD_MAX_REFRESH_ATTEMPTS = 5
CAVE_SMALL_WORLD_MIN_REQUEST_SEC = 10 * 60
CAVE_DEEP_STATUS_RECHECK_SEC = 30 * 60
CAVE_YUANYING_STATUS_RECHECK_SEC = yuanying.YUANYING_SPEC.cd_sec
CAVE_YUANYING_UNKNOWN_RECHECK_SEC = 30 * 60
WILD_TRAINING_NO_COOLDOWN_FOLLOWUP_SEC = 60
FATE_CARDS_WAIT_RETRY_SEC = 30 * 60
CAVE_PUBLIC_ENTRY_CANARY_LEASE_SEC = 20 * 60
CAVE_PUBLIC_ENTRY_RETRY_BASE_SEC = 6 * 60 * 60
CAVE_PUBLIC_ENTRY_RETRY_MAX_SEC = 24 * 60 * 60

_MANUAL_AUTH_UNTIL = {}
_RUN_LOCKS = {}
_PUBLIC_ENTRY_LOCKS = {}


def _miniapp_result_extra(extra=None, *results):
    merged = dict(extra or {})
    retry_after_sec = max((miniapp_retry_after_sec(result) for result in results), default=0.0)
    if retry_after_sec > 0:
        merged["retry_after_sec"] = retry_after_sec
    shared_rate_limited = False
    pending = list(results)
    seen = set()
    while pending:
        result = pending.pop()
        if not isinstance(result, (dict, list, tuple)) or id(result) in seen:
            continue
        seen.add(id(result))
        if not isinstance(result, dict):
            pending.extend(result)
            continue
        if (
            result.get("shared_rate_limit") is True
            or str(result.get("error") or "").strip().lower() == "external_action_rate_limited"
        ):
            shared_rate_limited = True
            break
        pending.extend(result.get(key) for key in ("events", "result", "extra"))
    if shared_rate_limited:
        merged["shared_rate_limit"] = True
        if retry_after_sec > 0:
            merged["shared_retry_after_sec"] = retry_after_sec
    return merged


def _cave_public_entry_urls(value):
    if isinstance(value, str):
        values = re.split(r"[\r\n,，\s]+", value)
    elif isinstance(value, (list, tuple, set)):
        values = value
    else:
        values = ()
    result = []
    seen = set()
    for item in values:
        item = str(item or "").strip()
        key = item.casefold()
        if item and key not in seen:
            seen.add(key)
            result.append(item)
    return result


def cave_public_entry_urls_signature(urls):
    normalized = _cave_public_entry_urls(urls)
    return hashlib.sha256("\n".join(normalized).encode("utf-8")).hexdigest() if normalized else ""


@dataclass(frozen=True)
class CavePublicEntryHealthSnapshot:
    """Compare existing entry-health fields without adding persistent control state."""

    urls: tuple
    blocked_signature: str
    claimed_at: float
    failure_evidence: tuple

    @classmethod
    def _from_config(cls, config):
        return cls(
            tuple(_cave_public_entry_urls(config.get("cave_public_entry_urls") or config.get("cave_public_entry_url"))),
            str(config.get("cave_public_entry_token_blocked_signature") or ""),
            config.get("cave_public_entry_token_canary_at") or 0,
            (
                config.get("cave_public_entry_token_blocked_at") or 0,
                str(config.get("cave_public_entry_token_blocked_reason") or ""),
                config.get("cave_public_entry_token_retry_at") or 0,
                config.get("cave_public_entry_token_failure_count") or 0,
            ),
        )

    @classmethod
    def capture(cls):
        return cls._from_config(dict(get_miniapp_auto_config() or {}))

    def current_config(self):
        current = dict(get_miniapp_auto_config() or {})
        return current if self._from_config(current) == self else None

    def release_canary(self, claimed_at):
        if not claimed_at or claimed_at != self.claimed_at:
            return False
        current = self.current_config()
        if current is None:
            return False
        current["cave_public_entry_token_canary_at"] = 0
        set_miniapp_auto_config(current)
        save_state()
        return True


@dataclass
class CavePublicEntryObservation:
    identity_id: int
    token_digest: str
    operation_check: object = None
    verified: bool = False
    invalidated: bool = False

    def permits(self, identity_id, token):
        if self.invalidated:
            return False
        try:
            require_miniapp_operation(self.operation_check)
            allowed = self.identity_id == identity_id and self.token_digest == stable_payload_digest(token)
        except MiniAppRequestAborted:
            allowed = False
        self.invalidated = not allowed
        return allowed


_CAVE_PUBLIC_ENTRY_OBSERVATION = ContextVar("cave_public_entry_observation", default=None)


@contextmanager
def observe_cave_public_entry(identity_id, public_entry_url, *, operation_check=None):
    """Bind read evidence to this caller, not to a module's business-result wording."""
    token, _webview_url, error = _parse_public_cave_entry_url(public_entry_url)
    observation = CavePublicEntryObservation(
        int(identity_id), stable_payload_digest(token), operation_check, invalidated=bool(error),
    )
    marker = _CAVE_PUBLIC_ENTRY_OBSERVATION.set(observation)
    try:
        yield observation
    finally:
        _CAVE_PUBLIC_ENTRY_OBSERVATION.reset(marker)


def get_cave_public_entry_gate(urls=None, *, now=None):
    """Return the shared entry health gate without claiming a canary."""
    config = dict(get_miniapp_auto_config() or {})
    current_urls = _cave_public_entry_urls(urls or config.get("cave_public_entry_urls") or config.get("cave_public_entry_url"))
    signature = cave_public_entry_urls_signature(current_urls)
    blocked_signature = str(config.get("cave_public_entry_token_blocked_signature") or "").strip()
    if not current_urls or not blocked_signature or blocked_signature != signature:
        return {"allowed": bool(current_urls), "blocked": False, "canary_due": False, "urls": current_urls}
    now = float(now or time.time())
    try:
        retry_at = float(config.get("cave_public_entry_token_retry_at") or 0)
    except (TypeError, ValueError, OverflowError):
        retry_at = 0.0
    if retry_at <= 0:
        try:
            blocked_at = float(config.get("cave_public_entry_token_blocked_at") or 0)
        except (TypeError, ValueError, OverflowError):
            blocked_at = 0.0
        retry_at = (blocked_at or now) + CAVE_PUBLIC_ENTRY_RETRY_BASE_SEC
    try:
        claimed_at = float(config.get("cave_public_entry_token_canary_at") or 0)
    except (TypeError, ValueError, OverflowError):
        claimed_at = 0.0
    if claimed_at and now < claimed_at + CAVE_PUBLIC_ENTRY_CANARY_LEASE_SEC:
        return {
            "allowed": False,
            "blocked": True,
            "canary_due": False,
            "retry_at": claimed_at + CAVE_PUBLIC_ENTRY_CANARY_LEASE_SEC,
            "urls": current_urls,
            "reason": "洞府公共入口正在进行单次复核",
        }
    if retry_at <= 0 or now < retry_at:
        return {
            "allowed": False,
            "blocked": True,
            "canary_due": False,
            "retry_at": retry_at,
            "urls": current_urls,
            "reason": str(config.get("cave_public_entry_token_blocked_reason") or "洞府公共入口授权已过期"),
        }
    return {
        "allowed": False,
        "blocked": True,
        "canary_due": True,
        "retry_at": retry_at,
        "urls": current_urls,
        "reason": "洞府公共入口进入低频单次复核窗口",
    }


def prepare_cave_public_entry_attempt(urls=None, *, now=None):
    """Claim at most one URL for a post-failure canary; normal runs keep all candidates."""
    now = float(now or time.time())
    gate = get_cave_public_entry_gate(urls, now=now)
    if not gate.get("blocked"):
        return {**gate, "canary": False}
    if not gate.get("canary_due"):
        return {**gate, "canary": False}
    config = dict(get_miniapp_auto_config() or {})
    config["cave_public_entry_token_canary_at"] = now
    set_miniapp_auto_config(config)
    save_state()
    return {**gate, "allowed": True, "blocked": False, "canary": True, "urls": gate.get("urls", [])[:1]}


def note_cave_public_entry_success(urls=None):
    config = dict(get_miniapp_auto_config() or {})
    configured = _cave_public_entry_urls(config.get("cave_public_entry_urls") or config.get("cave_public_entry_url"))
    attempted = _cave_public_entry_urls(urls or configured)
    configured_keys = {url.casefold() for url in configured}
    if configured and attempted and not any(item.casefold() in configured_keys for item in attempted):
        return False
    had_block = bool(
        config.get("cave_public_entry_token_blocked_signature")
        or config.get("cave_public_entry_token_retry_at")
        or config.get("cave_public_entry_token_canary_at")
        or config.get("cave_public_entry_token_failure_count")
    )
    if not had_block:
        return False
    for key in (
        "cave_public_entry_token_blocked_signature",
        "cave_public_entry_token_blocked_at",
        "cave_public_entry_token_blocked_reason",
        "cave_public_entry_token_retry_at",
        "cave_public_entry_token_failure_count",
        "cave_public_entry_token_canary_at",
    ):
        config[key] = 0 if key.endswith(("_at", "_count")) else ""
    set_miniapp_auto_config(config)
    save_state()
    return True


def note_cave_public_entry_token_failure(urls=None, reason="", *, now=None):
    config = dict(get_miniapp_auto_config() or {})
    configured = _cave_public_entry_urls(config.get("cave_public_entry_urls") or config.get("cave_public_entry_url"))
    attempted = _cave_public_entry_urls(urls or configured)
    configured_keys = {url.casefold() for url in configured}
    if not configured or not attempted or not any(item.casefold() in configured_keys for item in attempted):
        return False
    signature = cave_public_entry_urls_signature(configured)
    previous = int(config.get("cave_public_entry_token_failure_count") or 0)
    failure_count = previous + 1
    delay = min(CAVE_PUBLIC_ENTRY_RETRY_MAX_SEC, CAVE_PUBLIC_ENTRY_RETRY_BASE_SEC * (2 ** min(failure_count - 1, 2)))
    now = float(now or time.time())
    config.update({
        "cave_public_entry_token_blocked_signature": signature,
        "cave_public_entry_token_blocked_at": now,
        "cave_public_entry_token_blocked_reason": str(reason or "洞府公共入口授权已过期")[:240],
        "cave_public_entry_token_retry_at": now + delay,
        "cave_public_entry_token_failure_count": failure_count,
        "cave_public_entry_token_canary_at": 0,
    })
    set_miniapp_auto_config(config)
    save_state()
    return True


def defer_cave_public_entry_canary(urls=None, reason="", *, now=None):
    """Keep a stale entry blocked after an inconclusive canary failure."""
    config = dict(get_miniapp_auto_config() or {})
    configured = _cave_public_entry_urls(config.get("cave_public_entry_urls") or config.get("cave_public_entry_url"))
    attempted = _cave_public_entry_urls(urls or configured)
    configured_keys = {url.casefold() for url in configured}
    if not configured or not attempted or not any(item.casefold() in configured_keys for item in attempted):
        return False
    signature = cave_public_entry_urls_signature(configured)
    if str(config.get("cave_public_entry_token_blocked_signature") or "").strip() != signature:
        return False
    now = float(now or time.time())
    config["cave_public_entry_token_retry_at"] = now + CAVE_PUBLIC_ENTRY_RETRY_BASE_SEC
    config["cave_public_entry_token_canary_at"] = 0
    if reason:
        config["cave_public_entry_token_blocked_reason"] = str(reason)[:240]
    set_miniapp_auto_config(config)
    save_state()
    return True


def is_cave_public_entry_token_failure(value):
    return "dwelling_token_expired" in str(value or "").casefold() or "入口授权已过期" in str(value or "")


def record_cave_public_entry_url(public_entry_url, *, max_urls=3):
    """Persist a validated public cave URL discovered from a live official button."""
    request, _args = build_cave_treasure_launch_args(str(public_entry_url or "").strip())
    if not request.allowed or not request.start_param:
        return False
    url = str(request.webview_url or "").strip()
    if not url:
        return False
    config = dict(get_miniapp_auto_config() or {})
    had_block = bool(config.get("cave_public_entry_token_blocked_signature"))
    current = _cave_public_entry_urls(config.get("cave_public_entry_urls") or config.get("cave_public_entry_url"))
    urls = [url] + [item for item in current if item.casefold() != url.casefold()]
    urls = urls[:max(1, int(max_urls or 3))]
    changed = urls != current
    config["cave_public_entry_url"] = urls[0]
    config["cave_public_entry_urls"] = urls
    # A newly observed, validated URL supersedes the old token circuit.
    for key in (
        "cave_public_entry_token_blocked_signature",
        "cave_public_entry_token_blocked_at",
        "cave_public_entry_token_blocked_reason",
        "cave_public_entry_token_retry_at",
        "cave_public_entry_token_failure_count",
        "cave_public_entry_token_canary_at",
    ):
        config[key] = 0 if key.endswith(("_at", "_count")) else ""
    set_miniapp_auto_config(config)
    if changed or had_block:
        save_state()
    return True


def _event_sender_username(event):
    sender = getattr(event, "sender", None)
    return str(
        getattr(sender, "username", "")
        or getattr(event, "sender_username", "")
        or ""
    ).strip().lstrip("@").casefold()


async def capture_cave_public_entry_event(event, text=""):
    """Capture an official dwelling button without authorizing or running gameplay."""
    launch = extract_cave_treasure_miniapp_launch(event, message_text=text)
    if not launch:
        return False
    sender_id = int(getattr(event, "sender_id", 0) or 0)
    known = sender_id in set(get_game_bot_ids() or ())
    sender = getattr(event, "sender", None)
    if sender is None and not known:
        try:
            sender = await event.get_sender()
        except Exception:
            sender = None
    username = str(
        getattr(sender, "username", "")
        or getattr(event, "sender_username", "")
        or ""
    ).strip().lstrip("@").casefold()
    official_bot = bool(
        getattr(sender, "bot", False)
        and (
            username == "fanrenxiuxian_bot"
            or re.fullmatch(r"hantianzun\d+_bot", username, flags=re.IGNORECASE)
        )
    )
    if not known and not official_bot:
        return False
    return record_cave_public_entry_url(launch.get("webview_url") or "")


async def _get_pinned_cave_entry_message(client, group_id):
    """Read one group's pinned message without traversing its full history."""
    entity = await client.get_entity(group_id)
    full = await client(functions.channels.GetFullChannelRequest(channel=entity))
    full_chat = getattr(full, "full_chat", None)
    pinned_id = int(getattr(full_chat, "pinned_msg_id", 0) or 0)
    if pinned_id <= 0:
        return None
    return await client.get_messages(group_id, ids=pinned_id)


async def discover_cave_public_entry_from_history(*, group_ids=None, per_group_limit=80):
    """Read recent configured-group history once and adopt the newest official entry."""
    account_id, client = _get_any_authed_client_with_account()
    if client is None:
        return {"captured": False, "reason": "client_unavailable"}
    groups = []
    for value in group_ids or get_game_group_ids():
        try:
            group_id = int(value or 0)
        except (TypeError, ValueError, OverflowError):
            continue
        if group_id and group_id not in groups:
            groups.append(group_id)
    if not groups:
        return {"captured": False, "reason": "group_missing"}

    candidates = []
    for group_id in groups:
        try:
            async with account_rpc_slot(account_id=account_id, client_obj=client):
                messages = await client.get_messages(
                    group_id,
                    limit=max(1, min(200, int(per_group_limit or 80))),
                )
        except Exception as exc:
            console_log(
                f"🧩 洞府公共入口历史采集跳过群 {group_id}：{type(exc).__name__}",
                scope="global",
                limit=180,
            )
            continue
        for message in messages or ():
            launch = extract_cave_treasure_miniapp_launch(
                message,
                message_text=str(getattr(message, "raw_text", "") or ""),
            )
            if not launch:
                continue
            message_at = getattr(message, "date", None)
            try:
                timestamp = float(message_at.timestamp()) if message_at is not None else 0.0
            except (AttributeError, TypeError, ValueError, OverflowError):
                timestamp = 0.0
            candidates.append((timestamp, int(getattr(message, "id", 0) or 0), group_id, message))

    # Busy game groups can push the public dwelling card outside the recent
    # window within minutes. Fall back to Telegram's server-side text search so
    # startup recovery can still find an older official card without sending a
    # game command or scanning a large unfiltered history range.
    if not candidates:
        for group_id in groups:
            try:
                async with account_rpc_slot(account_id=account_id, client_obj=client):
                    messages = await client.get_messages(
                        group_id,
                        limit=max(1, min(200, int(per_group_limit or 80))),
                        search="洞府",
                    )
            except Exception as exc:
                console_log(
                    f"🧩 洞府公共入口关键词回溯跳过群 {group_id}：{type(exc).__name__}",
                    scope="global",
                    limit=180,
                )
                continue
            for message in messages or ():
                launch = extract_cave_treasure_miniapp_launch(
                    message,
                    message_text=str(getattr(message, "raw_text", "") or ""),
                )
                if not launch:
                    continue
                message_at = getattr(message, "date", None)
                try:
                    timestamp = float(message_at.timestamp()) if message_at is not None else 0.0
                except (AttributeError, TypeError, ValueError, OverflowError):
                    timestamp = 0.0
                candidates.append((timestamp, int(getattr(message, "id", 0) or 0), group_id, message))

    # The official card is commonly pinned. Pinned lookup is the authoritative
    # fallback when both the recent window and server-side search miss it.
    if not candidates:
        for group_id in groups:
            try:
                async with account_rpc_slot(account_id=account_id, client_obj=client):
                    message = await _get_pinned_cave_entry_message(client, group_id)
            except Exception as exc:
                console_log(
                    f"🧩 洞府公共入口置顶采集跳过群 {group_id}：{type(exc).__name__}",
                    scope="global",
                    limit=180,
                )
                continue
            if message is None:
                continue
            launch = extract_cave_treasure_miniapp_launch(
                message,
                message_text=str(getattr(message, "raw_text", "") or ""),
            )
            if not launch:
                continue
            message_at = getattr(message, "date", None)
            try:
                timestamp = float(message_at.timestamp()) if message_at is not None else 0.0
            except (AttributeError, TypeError, ValueError, OverflowError):
                timestamp = 0.0
            candidates.append((timestamp, int(getattr(message, "id", 0) or 0), group_id, message))

    for _timestamp, message_id, group_id, message in sorted(candidates, reverse=True):
        if await capture_cave_public_entry_event(
            message,
            str(getattr(message, "raw_text", "") or ""),
        ):
            console_log(
                f"🧩 洞府公共入口已从群历史动态更新：group={group_id}｜msg={message_id}",
                scope="global",
                limit=200,
            )
            return {"captured": True, "group_id": group_id, "message_id": message_id}
    return {"captured": False, "reason": "entry_not_found"}
_GAIN_KEYS = {
    "expgain": "经验",
    "experiencegain": "经验",
    "cultivationgain": "修为",
    "xiuweigain": "修为",
    "lingshigain": "灵石",
    "spiritstonegain": "灵石",
    "stonegain": "灵石",
    "contribution": "贡献",
}
_REWARD_CONTAINER_KEYS = {"rewards", "reward", "bonusloot", "loot", "drops", "items", "materials", "gains"}
_LOG_KEYS = {"logs", "log"}
_TECHNICAL_KEYS = {
    "score",
    "session",
    "sessionid",
    "ready",
    "rounds",
    "qualitybonus",
    "status",
    "phase",
    "mode",
    "step",
    "steps",
    "events",
    "proof",
}


_ITEM_TEXT_RE = re.compile(
    r"(?:获得|奖励|收获|掉落|战利品|材料)?\s*(?:【(?P<bracket>[^】]+)】|(?P<plain>[\u4e00-\u9fffA-Za-z0-9_·-]{2,24}))\s*[xX×]\s*(?P<count>[\d,]+)"
)
_GAIN_TEXT_RE = re.compile(r"(?P<name>修为|经验|灵石|天机残痕)\s*[+＋]\s*(?P<count>[\d,]+)")
_MENTION_RE = re.compile(r"@([A-Za-z0-9_]{3,64})")
_INVENTORY_GAIN_NAMES = {"灵石"}
_CAVE_TREASURE_STATE_OUTPUTS = ("module_snapshot", "daily_counter", "inventory_delta")




def _miniapp_http_allowed_during_pause():
    """天尊维护暂停期间仍允许 MiniApp HTTP。

    刻意保留在各模块本地而不是收进 miniapp_common：测试普遍用
    patch.object(<该模块>, "get_global_enabled") 打桩，判断一旦搬走，
    62 处 patch 点就都失效了。这点重复换来的是打桩位置符合直觉。
    """
    return (not get_global_enabled()) and get_global_pause_source() == "tianzun_maintenance"


def authorize_cave_treasure_miniapp_manual_run(identity_id, *, now=None, ttl_sec=CAVE_TREASURE_MANUAL_AUTH_TTL_SEC):
    identity_id = _identity_id(identity_id)
    if (identity_id <= 0 or is_cave_treasure_busy(identity_id) or treasure_results.hold_reason(identity_id)
            or treasure_operations.hold_reason(identity_id)) and not (
                identity_id > 0 and not is_cave_treasure_busy(identity_id) and treasure_operations.resume_allowed(identity_id)):
        return 0
    now = float(now or time.time())
    _MANUAL_AUTH_UNTIL[identity_id] = now + max(30, float(ttl_sec or CAVE_TREASURE_MANUAL_AUTH_TTL_SEC))
    return _MANUAL_AUTH_UNTIL[identity_id]


def revoke_cave_treasure_miniapp_manual_run(identity_id):
    _MANUAL_AUTH_UNTIL.pop(_identity_id(identity_id), None)


def _has_manual_auth(identity_id, now):
    identity_id = _identity_id(identity_id)
    expires_at = float(_MANUAL_AUTH_UNTIL.get(identity_id, 0) or 0)
    if expires_at <= 0:
        return False
    if float(now or time.time()) > expires_at:
        _MANUAL_AUTH_UNTIL.pop(identity_id, None)
        return False
    return True


def _run_lock(identity_id):
    identity_id = _identity_id(identity_id)
    account_id = get_identity_account(identity_id) or identity_id
    lock = _RUN_LOCKS.get(account_id)
    if lock is None:
        lock = asyncio.Lock()
        _RUN_LOCKS[account_id] = lock
    return lock


def _public_entry_lock(identity_id):
    identity_id = _identity_id(identity_id)
    lock = _PUBLIC_ENTRY_LOCKS.get(identity_id)
    if lock is None:
        lock = asyncio.Lock()
        _PUBLIC_ENTRY_LOCKS[identity_id] = lock
    return lock


def is_cave_treasure_busy(identity_id):
    identity_id = _identity_id(identity_id)
    owner = MiniAppIdentityOwner.capture(identity_id)
    accounts = {get_identity_account(identity_id) or identity_id}
    for key in (treasure_results.STATE_KEY, treasure_operations.STATE_KEY):
        record = owner.identity.get(key) if owner else None
        if isinstance(record, dict) and type(record.get("account_id")) is int:
            original_account = record["account_id"] or record.get("identity_id")
            if type(original_account) is int and original_account > 0:
                accounts.add(original_account)
    locks = [_PUBLIC_ENTRY_LOCKS.get(identity_id), *(_RUN_LOCKS.get(account) for account in accounts)]
    return any(lock is not None and lock.locked() for lock in locks)


def recover_cave_treasure_result(identity_id):
    if is_cave_treasure_busy(identity_id):
        return {"ok": False, "message": "洞府寻宝仍在执行，未恢复或新建入口",
                "extra": {"status": "busy", "persistence_only": True}}
    return _recover_owned_cave_treasure(identity_id)


def _recover_owned_cave_treasure(identity_id):
    owner = MiniAppIdentityOwner.capture(identity_id)
    if owner and treasure_results.pending(owner.identity):
        return treasure_results.recover_local(identity_id)
    recovered = treasure_operations.recover_local(identity_id, _commit_cave_treasure_result)
    return recovered if recovered is not None else treasure_results.recover_local(identity_id)


def is_cave_public_entry_busy(identity_id):
    """Return whether this identity currently owns an in-process public-entry operation."""
    lock = _PUBLIC_ENTRY_LOCKS.get(_identity_id(identity_id))
    return bool(lock and lock.locked())


def _public_entry_allowed():
    return get_global_enabled() or get_global_pause_source() == "tianzun_maintenance"


def _parse_public_cave_entry_url(public_entry_url):
    launch, _args = build_cave_treasure_launch_args(str(public_entry_url or "").strip())
    if not launch.allowed or not launch.start_param:
        return "", "", launch.reason or "invalid cave public entry"
    return launch.start_param, launch.webview_url, ""


def _channel_identity_treasure_allowed(identity_id):
    """Lab allowlist: channel identities permitted to run treasure themselves.

    2026-07-27 live probe settled this: the selected panel does return a
    dwelling.hunt block per playerId, but its *values* are the login account's
    shared quota. xuruode8 (channel identity of 301299112, which had already
    used 3/3 that day) got HTTP 409 daily_limit on its very first hunt without
    ever running treasure itself. The account-shared gate below is therefore
    correct; this allowlist (default empty) exists only for re-testing if the
    game ever changes that behavior.
    """
    try:
        from ..ui import normalize_miniapp_auto_config

        allowed = normalize_miniapp_auto_config().get("cave_public_treasure_channel_identity_ids") or ()
        return int(identity_id or 0) in {
            int(value) for value in allowed if str(value or "").strip().lstrip("-").isdigit()
        }
    except Exception:
        return False


def _public_entry_account_identity_error(identity_id):
    """Treasure attempts are shared by the physical Telegram login account."""
    identity_id = _identity_id(identity_id)
    try:
        account_id = int(get_identity_account(identity_id) or 0)
    except (TypeError, ValueError, OverflowError):
        account_id = 0
    if account_id > 0 and account_id != identity_id:
        if _channel_identity_treasure_allowed(identity_id):
            return ""
        return "洞府寻宝次数按登录账号共享，请使用该账号本体身份执行"
    return ""


def _iter_dicts(value, *, depth=0):
    if depth > 8:
        return
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_dicts(child, depth=depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_dicts(child, depth=depth + 1)


def _find_trial_launch_in_cave_payload(value):
    for item in _iter_dicts(value):
        url = str(item.get("url") or item.get("webviewUrl") or item.get("webview_url") or "").strip()
        if not url:
            continue
        if url.startswith("/"):
            url = urljoin("https://asc.aiopenai.app/", url)
        launch, _args = build_trial_launch_args(url)
        if launch.allowed and launch.start_param:
            return {
                "token": launch.start_param,
                "webview_url": launch.webview_url,
                "title": str(item.get("title") or item.get("buttonText") or item.get("key") or "").strip(),
                "safe_summary": launch.safe_summary(),
            }
    return {}


def _find_trial_external_app_in_cave_payload(value):
    item = find_cave_external_app(
        value,
        keys=("trial", "tianji_trial"),
        actions=("trial", "tianji_trial"),
        title_terms=("天机试炼",),
        url_terms=("xianxia-trial",),
    )
    if not item:
        return {}
    key = str(item.get("key") or "").strip().lower()
    action = str(item.get("action") or "").strip().lower()
    url = str(item.get("url") or item.get("webviewUrl") or item.get("webview_url") or "").strip()
    normalized_action = action if action in {"trial", "tianji_trial"} else ""
    if not normalized_action and key == "tianji_trial" and url in {"", "#"}:
        normalized_action = "tianji_trial"
    return {
        "action": normalized_action,
        "url": url,
        "title": str(item.get("title") or item.get("buttonText") or key).strip(),
        "available": bool(item.get("available", True)),
    }


def _find_fishing_external_app_in_cave_payload(value):
    item = find_cave_external_app(
        value,
        keys=("fishing", "fish"),
        actions=("fishing",),
        title_terms=("钓",),
    )
    if not item:
        return {}
    key = str(item.get("key") or "").strip().lower()
    action = str(item.get("action") or "").strip().lower()
    url = str(item.get("url") or item.get("webviewUrl") or item.get("webview_url") or "").strip()
    return {
        "action": "fishing" if action == "fishing" or url in {"", "#"} else "",
        "url": url,
        "title": str(item.get("title") or item.get("buttonText") or key).strip(),
        "available": bool(item.get("available", True)),
    }


def _find_stargazer_external_app_in_cave_payload(value):
    root = value.get("data") if isinstance(value, dict) and isinstance(value.get("data"), dict) else value
    account = root.get("account") if isinstance(root, dict) and isinstance(root.get("account"), dict) else {}
    star_palace = account.get("starPalace") if isinstance(account.get("starPalace"), dict) else {}
    observatory = star_palace.get("observatory") if isinstance(star_palace.get("observatory"), dict) else {}
    observatory_url = str(
        observatory.get("url") or observatory.get("webviewUrl") or observatory.get("webview_url") or ""
    ).strip()
    observatory_action = str(observatory.get("action") or "").strip().lower()
    if observatory_url or observatory_action:
        return {
            "action": observatory_action,
            "url": observatory_url,
            "title": str(observatory.get("title") or "观星台").strip(),
            "available": bool(observatory.get("available", True)),
            "key": "stargazer",
        }
    item = find_cave_external_app(
        value,
        keys=("sect_farm", "stargazer", "star_palace", "star_farm"),
        actions=("sect_farm", "stargazer", "star_palace", "star_farm"),
        title_terms=("观星台", "星宫"),
        url_terms=("xianxia-sect-farm", "startapp=farm_"),
    )
    if not item:
        return {}
    key = str(item.get("key") or "").strip().lower()
    return {
        "action": str(item.get("action") or "").strip().lower(),
        "url": str(item.get("url") or item.get("webviewUrl") or item.get("webview_url") or "").strip(),
        "title": str(item.get("title") or item.get("subtitle") or item.get("buttonText") or key).strip(),
        "available": bool(item.get("available", True)),
        "key": key,
    }


def _find_tree_external_app_in_cave_payload(value):
    item = find_cave_external_app(
        value,
        keys=("spirit_tree", "tree", "luoyun_tree"),
        actions=("spirit_tree", "tree", "luoyun_tree"),
        title_terms=("灵树",),
        url_terms=("xianxia-spirit-tree", "startapp=tree_"),
    )
    if not item:
        return {}
    key = str(item.get("key") or "").strip().lower()
    return {
        "action": str(item.get("action") or "").strip().lower(),
        "url": str(item.get("url") or item.get("webviewUrl") or item.get("webview_url") or "").strip(),
        "title": str(item.get("title") or item.get("subtitle") or item.get("buttonText") or key).strip(),
        "available": bool(item.get("available", True)),
        "key": key,
    }


def _find_tower_external_app_in_cave_payload(value):
    item = find_cave_external_app(
        value,
        keys=("pagoda", "tower", "liuli_pagoda"),
        actions=("pagoda",),
        title_terms=("问心塔", "琉璃塔"),
        url_terms=("xianxia-pagoda", "startapp=pagoda_"),
    )
    if not item:
        return {}
    key = str(item.get("key") or "").strip().lower()
    action = str(item.get("action") or "").strip().lower()
    url = str(item.get("url") or item.get("webviewUrl") or item.get("webview_url") or "").strip()
    return {
        "action": "pagoda" if action == "pagoda" or url in {"", "#"} else "",
        "url": url,
        "title": str(item.get("title") or item.get("subtitle") or item.get("buttonText") or key).strip(),
        "available": bool(item.get("available", True)),
        "key": key,
    }


def _tree_launch_from_external_app(external_app):
    url = str((external_app or {}).get("url") or "").strip()
    if not url:
        return {}
    if url.startswith("/"):
        url = urljoin("https://asc.aiopenai.app/", url)
    elif "://" not in url:
        url = urljoin("https://asc.aiopenai.app/miniapp/xianxia-dwelling", url)
    launch, _args = build_tree_launch_args(url)
    if not launch.allowed or not launch.start_param:
        return {}
    return {
        "token": launch.start_param,
        "webview_url": launch.webview_url,
        "title": str((external_app or {}).get("title") or "").strip(),
        "safe_summary": launch.safe_summary(),
    }


def _find_tree_launch_in_cave_payload(value):
    for item in _iter_dicts(value):
        launch = _tree_launch_from_external_app(item)
        if launch:
            return launch
    return {}


def _stargazer_launch_from_external_app(external_app):
    url = str((external_app or {}).get("url") or "").strip()
    if not url:
        return {}
    if url.startswith("/"):
        url = urljoin("https://asc.aiopenai.app/", url)
    launch, _args = build_stargazer_launch_args(url)
    if not launch.allowed or not launch.start_param:
        return {}
    return {
        "token": launch.start_param,
        "webview_url": launch.webview_url,
        "title": str((external_app or {}).get("title") or "").strip(),
        "safe_summary": launch.safe_summary(),
    }


def _find_stargazer_launch_in_cave_payload(value):
    for item in _iter_dicts(value):
        launch = _stargazer_launch_from_external_app(item)
        if launch:
            return launch
    return {}


def _find_tower_launch_in_cave_payload(value):
    for item in _iter_dicts(value):
        url = str(item.get("url") or item.get("webviewUrl") or item.get("webview_url") or "").strip()
        if not url:
            continue
        if url.startswith("/"):
            url = urljoin("https://asc.aiopenai.app/", url)
        elif "://" not in url:
            url = urljoin("https://asc.aiopenai.app/miniapp/xianxia-dwelling", url)
        launch, _args = build_tower_launch_args(url)
        if launch.allowed and launch.start_param:
            return {
                "token": launch.start_param,
                "webview_url": launch.webview_url,
                "title": str(item.get("title") or item.get("buttonText") or item.get("key") or "").strip(),
                "safe_summary": launch.safe_summary(),
            }
    return {}


def _find_fate_cards_launch_in_cave_payload(value):
    return extract_fate_cards_launch_from_payload(value)


def _selected_player_error(overview, identity_id):
    selected_player_id = _parse_int((overview or {}).get("player_id"), 0)
    if not selected_player_id:
        return "洞府回包缺少 playerId"
    if _normalize_dwelling_identity_id(selected_player_id) != _normalize_dwelling_identity_id(identity_id):
        return f"洞府身份校验失败：期望 {int(identity_id or 0)}，实际 {selected_player_id}"
    return ""


def _normalize_dwelling_identity_id(player_id):
    player_id = _parse_int(player_id, 0)
    if player_id <= -1_000_000_000_000:
        return -player_id - 1_000_000_000_000
    return player_id


def _resolve_dwelling_player_id(payload, identity_id):
    root = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else payload
    identity = root.get("identity") if isinstance(root, dict) and isinstance(root.get("identity"), dict) else {}
    target_identity_id = _normalize_dwelling_identity_id(identity_id)
    for choice in identity.get("choices") or ():
        if not isinstance(choice, dict):
            continue
        player_id = _parse_int(choice.get("playerId"), 0)
        if player_id and _normalize_dwelling_identity_id(player_id) == target_identity_id:
            return player_id
    return 0


def _has_cave_details_snapshot(payload):
    root = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else payload
    account = root.get("account") if isinstance(root, dict) and isinstance(root.get("account"), dict) else {}
    if account.get("deferredPending") is False:
        return True
    external = account.get("externalApps") if isinstance(account.get("externalApps"), dict) else {}
    if isinstance(external.get("groups"), list) and external.get("groups"):
        return True
    if isinstance(account.get("journey"), dict) and account.get("journey"):
        return True
    if isinstance(account.get("smallWorld"), dict) and account.get("smallWorld"):
        return True
    if isinstance(account.get("starPalace"), dict) and account.get("starPalace"):
        return True
    command_center = account.get("commandCenter") if isinstance(account.get("commandCenter"), dict) else {}
    return isinstance(command_center.get("entries"), list) and bool(command_center.get("entries"))


def _cave_entry_safe_directory(result):
    """Build a stable, secret-free catalog from an existing dwelling response."""
    data = (result or {}).get("data") if isinstance((result or {}).get("data"), dict) else {}
    overview = data.get("overview") if isinstance(data.get("overview"), dict) else {}
    if not overview:
        overview = parse_cave_dwelling_overview(data.get("raw") or {})

    external_apps = []
    seen_apps = set()
    for app in overview.get("external_apps") or ():
        if not isinstance(app, dict):
            continue
        item = {
            "key": str(app.get("key") or "").strip(),
            "title": str(app.get("title") or "").strip(),
            "action": str(app.get("action") or "").strip(),
            "start_kind": str(app.get("start_kind") or "").strip(),
            "group": str(app.get("group_key") or app.get("group_title") or "").strip(),
        }
        signature = tuple(item.values())
        if not any(signature) or signature in seen_apps:
            continue
        seen_apps.add(signature)
        external_apps.append(item)
    external_apps.sort(key=lambda item: (
        item.get("group") or "",
        item.get("key") or "",
        item.get("title") or "",
        item.get("action") or "",
        item.get("start_kind") or "",
    ))

    command_center = overview.get("command_center") if isinstance(overview.get("command_center"), dict) else {}
    center_entries = []
    seen_entries = set()
    for entry in command_center.get("entries") or ():
        if not isinstance(entry, dict):
            continue
        commands = tuple(
            str(command or "").strip()
            for command in entry.get("commands") or ()
            if str(command or "").strip()
        )
        item = {
            "key": str(entry.get("key") or "").strip(),
            "title": str(entry.get("title") or "").strip(),
            "status": str(entry.get("status") or "").strip(),
            "target_tab": str(entry.get("target_tab") or "").strip(),
            "button_text": str(entry.get("button_text") or "").strip(),
            "note": str(entry.get("note") or "").strip(),
            "commands": list(commands),
        }
        signature = (
            item["key"],
            item["title"],
            item["status"],
            item["target_tab"],
            item["button_text"],
            item["note"],
            commands,
        )
        if not any(signature[:-1]) and not commands:
            continue
        if signature in seen_entries:
            continue
        seen_entries.add(signature)
        center_entries.append(item)
    center_entries.sort(key=lambda item: (
        item.get("target_tab") or "",
        item.get("key") or "",
        item.get("title") or "",
    ))

    directory = {}
    if external_apps:
        directory["external_apps"] = external_apps
    if center_entries:
        security = command_center.get("security") if isinstance(command_center.get("security"), dict) else {}
        directory["command_center"] = {
            "entry_count": len(center_entries),
            "security": {
                "mode": str(security.get("mode") or "").strip(),
                "direct_raw_command": bool(security.get("direct_raw_command")),
                "max_input_length": _parse_int(security.get("max_input_length"), 0),
                "text": str(security.get("text") or "").strip(),
            },
            "entries": center_entries,
        }
    return directory


def _record_cave_entry_safe_directory(identity_id, result, *, now):
    directory = _cave_entry_safe_directory(result)
    if not directory:
        return {"changed": False, "record": {}, "record_key": ""}
    record_key = f"{int(identity_id)}:cave_entry_directory"
    previous = dict(get_miniapp_state_records().get(record_key) or {})
    if previous.get("state") == directory:
        return {"changed": False, "record": previous, "record_key": record_key}
    return record_miniapp_state(
        identity_id,
        "cave_entry_directory",
        directory,
        source="cave_dwelling_miniapp",
        source_id="cave_entry_directory:v1",
        now=now,
        outputs=("module_catalog",),
    )


async def _load_cave_public_identity_session(
    identity_id,
    token,
    webview_url,
    *,
    now,
    capture_source,
    include_details=False,
    operation_check=None,
):
    owner = MiniAppIdentityOwner.capture(identity_id)
    observation = _CAVE_PUBLIC_ENTRY_OBSERVATION.get()
    cancelled = {"ok": False, "status": "cancelled", "error": "洞府公共入口操作已失效"}

    def can_continue():
        return (
            owner is not None
            and owner.is_current()
            and is_cave_public_identity_available(identity_id)
            and (observation is None or observation.permits(identity_id, token))
            and (operation_check is None or operation_check() is True)
        )

    if not can_continue():
        return cancelled
    try:
        init_data = await request_cave_treasure_miniapp_init_data(
            identity_id,
            token=token,
            webview_url=webview_url,
            operation_check=can_continue,
        )
    except Exception as exc:
        if not can_continue():
            return cancelled
        return {"ok": False, "error": f"会话初始化失败：{type(exc).__name__}: {exc}"}

    if not can_continue():
        return cancelled
    initial_result = await run_cave_dwelling_start_production_flow(
        identity_id,
        token=token,
        webview_url=webview_url,
        init_data=init_data,
        capture_sink=_capture_store(now),
        capture_source=f"{capture_source}:initial",
        operation_check=can_continue,
    )
    if not can_continue():
        return cancelled
    if not initial_result.get("ok"):
        return {
            "ok": False,
            "error": initial_result.get("error") or initial_result.get("status") or "initial_start_failed",
            "result": initial_result,
        }
    initial_data = dict(initial_result.get("data") or {})
    initial_overview = initial_data.get("overview") if isinstance(initial_data.get("overview"), dict) else {}
    initial_player_id = _parse_int(initial_overview.get("player_id"), 0)
    if initial_player_id and observation is not None:
        observation.verified = True
    if initial_player_id and _normalize_dwelling_identity_id(initial_player_id) == _normalize_dwelling_identity_id(identity_id):
        session = {
            "ok": True,
            "init_data": init_data,
            "player_id": initial_player_id,
            "result": initial_result,
        }
    else:
        selected_player_id = _resolve_dwelling_player_id(initial_data.get("raw") or {}, identity_id)
        if not selected_player_id:
            return {"ok": False, "error": "洞府公共入口不包含目标身份"}
        selected_result = await run_cave_dwelling_start_production_flow(
            identity_id,
            token=token,
            webview_url=webview_url,
            init_data=init_data,
            player_id=selected_player_id,
            capture_sink=_capture_store(now),
            capture_source=f"{capture_source}:selected",
            operation_check=can_continue,
        )
        if not can_continue():
            return cancelled
        if not selected_result.get("ok"):
            return {
                "ok": False,
                "error": selected_result.get("error") or selected_result.get("status") or "selected_start_failed",
                "result": selected_result,
            }
        selected_data = dict(selected_result.get("data") or {})
        player_error = _selected_player_error(selected_data.get("overview") or {}, identity_id)
        if player_error:
            return {"ok": False, "error": player_error}
        session = {
            "ok": True,
            "init_data": init_data,
            "player_id": selected_player_id,
            "result": selected_result,
        }
        if observation is not None:
            observation.verified = True

    session_raw = dict((session.get("result") or {}).get("data") or {}).get("raw") or {}
    if not include_details or _has_cave_details_snapshot(session_raw):
        _record_cave_entry_safe_directory(identity_id, session.get("result") or {}, now=now)
        return session
    details_result = await run_cave_dwelling_snapshot_production_flow(
        identity_id,
        token=token,
        webview_url=webview_url,
        endpoint="details",
        init_data=init_data,
        player_id=session.get("player_id"),
        capture_sink=_capture_store(now),
        capture_source=f"{capture_source}:details",
        operation_check=can_continue,
    )
    if not can_continue():
        return cancelled
    if not details_result.get("ok"):
        return {
            "ok": False,
            "error": details_result.get("error") or details_result.get("status") or "details_failed",
            "result": details_result,
        }
    start_data = dict((session.get("result") or {}).get("data") or {})
    merged_raw = merge_cave_dwelling_snapshot_data(
        start_data.get("raw") or {},
        details_result.get("data") or {},
    )
    session["result"] = {
        **dict(session.get("result") or {}),
        "events": list((session.get("result") or {}).get("events") or []) + list(details_result.get("events") or []),
        "data": {
            **start_data,
            "overview": parse_cave_dwelling_overview(merged_raw),
            "raw": merged_raw,
        },
    }
    _record_cave_entry_safe_directory(identity_id, session.get("result") or {}, now=now)
    return session


def apply_cave_inventory_snapshot(identity_id, payload, *, expected_player_id=None, now=None):
    """Atomically replace one local bag only from a complete, matching snapshot."""

    identity_id = _identity_id(identity_id)
    if identity_id <= 0:
        raise ValueError("身份不存在")
    snapshot = parse_cave_inventory_snapshot(
        payload,
        expected_player_id=expected_player_id if expected_player_id is not None else identity_id,
    )
    if _normalize_dwelling_identity_id(snapshot.get("player_id")) != _normalize_dwelling_identity_id(identity_id):
        raise ValueError("洞府 MiniApp 库存回包不属于目标身份")

    now = float(now or time.time())
    records = dict(get_storage_bag_records() or {})
    previous = records.get(str(identity_id)) if isinstance(records.get(str(identity_id)), dict) else {}
    previous_items = previous.get("items") if isinstance(previous.get("items"), dict) else {}
    items = dict(snapshot.get("items") or {})
    sections = {
        str(name): dict(section_items or {})
        for name, section_items in (snapshot.get("sections") or {}).items()
        if str(name or "").strip() and isinstance(section_items, dict)
    }
    profile = get_send_as_profile(identity_id)
    records[str(identity_id)] = {
        "identity_id": identity_id,
        "label": profile.get("label") or profile.get("username") or profile.get("daohao") or str(identity_id),
        "owner": profile.get("username") or profile.get("label") or profile.get("daohao") or str(identity_id),
        "owner_username": profile.get("username") or "",
        "updated_at": now,
        "updated_at_text": fmt_abs_ts(now),
        "items": items,
        "sections": sections,
        "empty": bool(snapshot.get("empty")),
        "source": "cave_inventory_miniapp",
    }
    set_storage_bag_records(records)
    save_state()
    return {
        "updated": True,
        "changed": dict(previous_items or {}) != items,
        "identity_id": identity_id,
        "item_count": len(items),
        "empty": not bool(items),
        "record": records[str(identity_id)],
    }


async def run_cave_public_inventory(identity_id, public_entry_url, *, now=None):
    """Refresh one complete storage-bag snapshot through the public dwelling entry."""

    identity_id = _identity_id(identity_id)
    now = float(now or time.time())
    if identity_id <= 0:
        return {"ok": False, "message": "身份不存在", "extra": {}}
    if not is_cave_public_identity_available(identity_id):
        return {"ok": False, "message": "身份已停用", "extra": {}}
    if not _public_entry_allowed():
        return {"ok": False, "message": "全局暂停来源不允许洞府公共入口 MiniApp HTTP", "extra": {}}
    token, webview_url, error = _parse_public_cave_entry_url(public_entry_url)
    if error:
        return {"ok": False, "message": error, "extra": {}}
    lock = _public_entry_lock(identity_id)
    if lock.locked():
        return {"ok": False, "message": "洞府公共入口操作执行中", "extra": {}}

    async with lock:
        session = await _load_cave_public_identity_session(
            identity_id,
            token,
            webview_url,
            now=now,
            capture_source=f"cave_public_inventory_start:{identity_id}",
            include_details=False,
        )
        if not session.get("ok"):
            return {
                "ok": False,
                "message": f"洞府储物袋身份读取失败：{session.get('error') or 'unknown'}",
                "extra": {"phase": "session_failed"},
            }
        result = await run_cave_dwelling_snapshot_production_flow(
            identity_id,
            token=token,
            webview_url=webview_url,
            endpoint="section",
            section="inventory",
            init_data=session.get("init_data") or "",
            player_id=session.get("player_id"),
            capture_sink=_capture_store(now),
            capture_source=f"cave_public_inventory:{identity_id}",
        )
        if not result.get("ok"):
            return {
                "ok": False,
                "message": f"洞府储物袋读取失败：{result.get('error') or result.get('status') or 'unknown'}",
                "extra": {"phase": "inventory_failed"},
            }
        try:
            applied = apply_cave_inventory_snapshot(
                identity_id,
                result.get("data") or {},
                expected_player_id=session.get("player_id"),
                now=now,
            )
        except ValueError as exc:
            return {
                "ok": False,
                "message": str(exc),
                "extra": {"phase": "inventory_rejected", "snapshot_preserved": True},
            }
        return {
            "ok": True,
            "message": f"洞府 MiniApp 储物袋已刷新：{applied['item_count']} 项",
            "extra": {
                "phase": "inventory_applied",
                "changed": bool(applied.get("changed")),
                "empty": bool(applied.get("empty")),
                "item_count": int(applied.get("item_count") or 0),
            },
        }


async def probe_cave_public_entry(identity_id, public_entry_url, *, now=None):
    """Perform one read-only dwelling start request for shared entry revalidation."""
    identity_id = _identity_id(identity_id)
    now = float(now or time.time())
    token, webview_url, error = _parse_public_cave_entry_url(public_entry_url)
    if identity_id <= 0 or error:
        return {"ok": False, "message": error or "身份不存在", "extra": {}}
    owner = MiniAppIdentityOwner.capture(identity_id)
    health = CavePublicEntryHealthSnapshot.capture()

    def can_continue():
        return (
            owner is not None
            and owner.is_current()
            and is_cave_public_identity_available(identity_id)
            and _public_entry_allowed()
            and (not health.claimed_at or health.claimed_at == now)
            and health.current_config() is not None
        )

    def cancelled_result():
        # Cancellation is not entry-health evidence and cannot release a newer claim.
        health.release_canary(now)
        return {
            "ok": False,
            "message": "洞府公共入口复核已取消或上下文已变更",
            "extra": {"canary": True, "status": "cancelled"},
        }

    if not can_continue():
        return cancelled_result()
    try:
        session = await _load_cave_public_identity_session(
            identity_id,
            token,
            webview_url,
            now=now,
            capture_source=f"cave_public_entry_canary:{identity_id}",
            include_details=False,
            operation_check=can_continue,
        )
    except asyncio.CancelledError:
        cancelled_result()
        raise
    if not can_continue() or session.get("status") in {"cancelled", "operation_cancelled"}:
        return cancelled_result()
    if session.get("ok"):
        note_cave_public_entry_success([public_entry_url])
        return {"ok": True, "message": "洞府公共入口单次复核成功", "extra": {"canary": True}}
    message = str(session.get("error") or "洞府公共入口单次复核失败")
    if is_cave_public_entry_token_failure(message):
        note_cave_public_entry_token_failure([public_entry_url], message, now=now)
    else:
        defer_cave_public_entry_canary([public_entry_url], message, now=now)
    return {"ok": False, "message": message, "extra": {"canary": True}}


def _entry_mentions_current_identity(text):
    usernames = {
        str(match.group(1) or "").strip().lower()
        for match in _MENTION_RE.finditer(str(text or ""))
    }
    usernames.discard("")
    if not usernames:
        return False
    profile_username = str((get_send_as_profile() or {}).get("username") or "").strip().lstrip("@").lower()
    return bool(profile_username and profile_username in usernames)


def _normalize_key(key):
    return re.sub(r"[^A-Za-z0-9]", "", str(key or "")).lower()


def _parse_int(value, default=0):
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError, OverflowError):
        return default


def _reward_from_value(value, *, fallback_name=""):
    if isinstance(value, str):
        name = value.strip()
        return {"name": name, "qty": 1} if name else {}
    if isinstance(value, (int, float)) and fallback_name:
        qty = _parse_int(value, 0)
        return {"name": str(fallback_name).strip(), "qty": qty} if qty > 0 else {}
    if not isinstance(value, dict):
        return {}
    name = ""
    for key in ("name", "itemName", "item_name", "title", "label"):
        if value.get(key) not in (None, ""):
            name = str(value.get(key) or "").strip()
            break
    if not name and fallback_name:
        name = str(fallback_name).strip()
    if not name:
        return {}
    qty = value.get("qty", value.get("count", value.get("quantity", value.get("amount", 1))))
    return {"name": name, "qty": max(1, _parse_int(qty, 1))}


def _rewards_from_container(value):
    rewards = []
    if isinstance(value, list):
        for item in value:
            reward = _reward_from_value(item)
            if reward:
                rewards.append(reward)
        return rewards
    if isinstance(value, dict):
        direct = _reward_from_value(value)
        if direct:
            return [direct]
        for name, amount in value.items():
            reward = _reward_from_value(amount, fallback_name=name)
            if reward:
                rewards.append(reward)
    return rewards


def _merge_reward_counts(target, rewards):
    for reward in rewards or ():
        if not isinstance(reward, dict):
            continue
        name = str(reward.get("name") or "").strip()
        if not name:
            continue
        target[name] = int(target.get(name, 0) or 0) + max(1, _parse_int(reward.get("qty"), 1))


def _collect_from_text(text, *, rewards, gains):
    text = str(text or "")
    for match in _ITEM_TEXT_RE.finditer(text):
        name = str(match.group("bracket") or match.group("plain") or "").strip(" ：:，,。")
        name = re.sub(r"^(?:获得|奖励|收获|掉落|战利品|材料)", "", name).strip(" ：:，,。")
        qty = _parse_int(match.group("count"), 0)
        if name and qty > 0 and name not in {"神识", "游戏", "次数"}:
            rewards[name] = int(rewards.get(name, 0) or 0) + qty
    for match in _GAIN_TEXT_RE.finditer(text):
        name = str(match.group("name") or "").strip()
        amount = _parse_int(match.group("count"), 0)
        if name and amount > 0:
            gains[name] = int(gains.get(name, 0) or 0) + amount


def _collect_materials(value, *, rewards=None, gains=None, depth=0):
    rewards = rewards if rewards is not None else {}
    gains = gains if gains is not None else {}
    if depth > 5:
        return rewards, gains
    if isinstance(value, str):
        _collect_from_text(value, rewards=rewards, gains=gains)
        return rewards, gains
    if isinstance(value, list):
        for item in value:
            _collect_materials(item, rewards=rewards, gains=gains, depth=depth + 1)
        return rewards, gains
    if not isinstance(value, dict):
        return rewards, gains
    for key, child in value.items():
        normalized = _normalize_key(key)
        if normalized in _TECHNICAL_KEYS:
            continue
        if normalized in _LOG_KEYS:
            log_rewards, log_gains = _collect_materials(child, rewards={}, gains={}, depth=depth + 1)
            for name, qty in log_rewards.items():
                rewards[name] = max(int(rewards.get(name, 0) or 0), int(qty or 0))
            for name, amount in log_gains.items():
                gains[name] = max(int(gains.get(name, 0) or 0), int(amount or 0))
            continue
        if normalized in _REWARD_CONTAINER_KEYS:
            _merge_reward_counts(rewards, _rewards_from_container(child))
            _collect_materials(child, rewards=rewards, gains=gains, depth=depth + 1)
            continue
        gain_label = _GAIN_KEYS.get(normalized)
        if gain_label:
            amount = _parse_int(child, 0)
            if amount > 0:
                gains[gain_label] = int(gains.get(gain_label, 0) or 0) + amount
            continue
        _collect_materials(child, rewards=rewards, gains=gains, depth=depth + 1)
    return rewards, gains


def _format_material_summary(data):
    rewards, gains = _collect_materials(data or {})
    return _format_material_counts(rewards, gains)


def _format_material_counts(rewards, gains):
    parts = []
    if gains:
        parts.append("收益:" + "、".join(f"{name}+{amount}" for name, amount in sorted(gains.items()) if amount > 0))
    if rewards:
        parts.append("奖励:" + "、".join(f"{name}x{amount}" for name, amount in sorted(rewards.items()) if amount > 0))
    return "｜".join(parts)


def _fate_cards_state_from_result(result):
    data = (result or {}).get("data") if isinstance((result or {}).get("data"), dict) else {}
    return data.get("state") if isinstance(data.get("state"), dict) else {}


def _fate_cards_transition_error(previous, current):
    if not isinstance(current, dict) or current.get("state_verified") is not True:
        return "fate_state_unverified"
    previous = dict(previous or {})
    if previous.get("challenge_date") and previous["challenge_date"] != current.get("challenge_date"):
        return "fate_day_changed"
    if previous.get("record_key") and previous["record_key"] != current.get("record_key"):
        return "fate_record_changed"
    for key in ("has_drawn", "has_ai_reading"):
        if previous.get(key) is True and current.get(key) is not True:
            return "fate_state_regressed"
    if previous.get("choice_key") and previous["choice_key"] != current.get("choice_key"):
        return "fate_choice_changed"
    old_quest = previous.get("quest") or {}
    quest = current.get("quest") or {}
    if previous.get("choice_key"):
        for key in ("key", "started_at", "metric", "target"):
            if old_quest.get(key) not in (None, "") and old_quest[key] != quest.get(key):
                return "fate_quest_changed"
        if old_quest.get("status") in {"settled", "expired"} and old_quest["status"] != quest.get("status"):
            return "fate_quest_regressed"
        if _parse_int(quest.get("progress"), 0) < _parse_int(old_quest.get("progress"), 0):
            return "fate_progress_regressed"
    return ""


def _fate_cards_action_confirmed(action, fate_state, *, expected="", previous=None):
    action = str(action or "").strip().lower()
    fate_state = dict(fate_state or {})
    if _fate_cards_transition_error(previous, fate_state):
        return False
    if action == "draw":
        return bool(fate_state.get("has_drawn") and (not expected or fate_state.get("question_key") == expected))
    if action == "interpret":
        return bool(fate_state.get("has_ai_reading"))
    if action == "choose":
        return str(fate_state.get("choice_key") or "").strip() == str(expected or "").strip()
    if action == "settle":
        quest = fate_state.get("quest") if isinstance(fate_state.get("quest"), dict) else {}
        return str(quest.get("status") or "").strip().lower() == "settled"
    return False


def _fate_cards_prerequisite_superseded(pending, fate_state):
    if pending.get("action") not in {"meditation", "deep_start", "deep_settle", "deep_force"}:
        return False
    before = pending.get("before") or {}
    if before.get("state_verified") is not True or _fate_cards_transition_error(before, fate_state):
        return False
    if any(not before.get(key) or before[key] != fate_state.get(key) for key in (
        "challenge_date", "record_key", "choice_key",
    )):
        return False
    old_quest = before.get("quest") or {}
    quest = fate_state.get("quest") or {}
    if any(old_quest.get(key) in (None, "") or old_quest[key] != quest.get(key) for key in (
        "key", "started_at", "metric", "target",
    )):
        return False
    progress, target = quest.get("progress"), quest.get("target")
    return quest.get("status") in {"settled", "expired"} or (
        quest.get("can_settle") is True and type(progress) is int
        and type(target) is int and target > 0 and progress >= target
    )


def _fate_cards_retry_after_sec(fate_state):
    fate_state = dict(fate_state or {})
    quest = fate_state.get("quest") if isinstance(fate_state.get("quest"), dict) else {}
    if str(quest.get("metric") or "").strip().lower() == "wait_seconds":
        remaining = max(0, _parse_int(quest.get("target"), 0) - _parse_int(quest.get("progress"), 0))
        if remaining > 0:
            return max(30, min(FATE_CARDS_WAIT_RETRY_SEC, remaining + 5))
    return FATE_CARDS_WAIT_RETRY_SEC


def _record_fate_cards_state(
    identity_id, fate_state, *, now, status, reward=None, meditation=None, deep_retreat=None,
    base_record=None, pending=None, receipts=None, owner_account_id=None, unconfirmed_prerequisite=None,
):
    fate_state = dict(fate_state or {})
    quest = fate_state.get("quest") if isinstance(fate_state.get("quest"), dict) else {}
    challenge_date = str(fate_state.get("challenge_date") or "")
    previous = dict(base_record if base_record is not None else get_miniapp_state_records().get(f"{int(identity_id)}:fate_cards") or {})
    previous_state = previous.get("state") if isinstance(previous.get("state"), dict) else {}
    cumulative_gains = {}
    if str(previous_state.get("challenge_date") or "") == challenge_date:
        previous_gains = previous_state.get("gains") if isinstance(previous_state.get("gains"), dict) else {}
        previous_sources = (previous_gains,)
        if not previous_gains:
            previous_meditation = (
                previous_state.get("meditation")
                if isinstance(previous_state.get("meditation"), dict)
                else {}
            )
            previous_sources = (
                previous_meditation.get("gains")
                if isinstance(previous_meditation.get("gains"), dict)
                else {},
                previous_state.get("reward")
                if isinstance(previous_state.get("reward"), dict)
                else {},
            )
        for source in previous_sources:
            for name, amount in source.items():
                parsed = _parse_int(amount, 0)
                if parsed > 0:
                    key = str(name)
                    cumulative_gains[key] = int(cumulative_gains.get(key, 0) or 0) + parsed
    current_gains = {}
    for source in (dict((meditation or {}).get("gains") or {}), dict(reward or {})):
        for name, amount in source.items():
            parsed = _parse_int(amount, 0)
            if parsed > 0:
                current_gains[str(name)] = int(current_gains.get(str(name), 0) or 0) + parsed
    for name, amount in current_gains.items():
        cumulative_gains[name] = int(cumulative_gains.get(name, 0) or 0) + amount
    payload = {
        "challenge_date": challenge_date,
        "status": str(status or ""),
        "question_key": str(fate_state.get("question_key") or fate_state.get("default_question_key") or ""),
        "choice_key": str(fate_state.get("choice_key") or ""),
        "trace_balance": _parse_int(fate_state["trace_balance"], 0) if fate_state.get("trace_balance") is not None else None,
        "quest": {
            "title": str(quest.get("title") or ""),
            "metric": str(quest.get("metric") or ""),
            "progress": _parse_int(quest.get("progress"), 0),
            "target": _parse_int(quest.get("target"), 0),
            "status": str(quest.get("status") or ""),
            "can_settle": bool(quest.get("can_settle")),
        },
        "reward": dict(reward or {}),
        "meditation": dict(meditation or {}),
        "deep_retreat": dict(deep_retreat or {}),
        "gains": cumulative_gains,
    }
    if base_record is not None:
        payload.update({
            "snapshot": fate_state, "pending": dict(pending or {}),
            "receipts": dict(receipts or {}), "owner_account_id": owner_account_id,
            "unconfirmed_prerequisite": dict(unconfirmed_prerequisite or {}),
        })
    source_id = (
        f"fate_cards:{payload['challenge_date'] or get_day_key(now)}:"
        f"{payload['choice_key'] or '-'}:{stable_payload_digest(payload)}"
    )
    return record_miniapp_state(
        identity_id,
        "fate_cards",
        payload,
        source="fate_cards_miniapp",
        source_id=source_id,
        now=now,
        outputs=("daily_record", "quest_state", "reward_delta"),
    )


async def _run_fate_cards_action_and_reconcile(
    identity_id,
    action,
    *,
    token,
    webview_url,
    init_data,
    payload,
    capture_sink,
    capture_source,
    expected="",
    previous=None,
    operation_check=None,
    request_budget=None,
):
    def current():
        try:
            require_miniapp_operation(operation_check)
            return True
        except MiniAppRequestAborted:
            return False

    require_miniapp_operation(operation_check)
    cancelled = None
    try:
        action_result = await run_fate_cards_action_production(
            identity_id, action, token=token, webview_url=webview_url, init_data=init_data,
            payload=payload, capture_sink=capture_sink, capture_source=f"{capture_source}:{action}",
            operation_check=operation_check, request_budget=request_budget,
        )
    except MiniAppFlowCancelled as exc:
        cancelled = exc
        action_result = exc.result if isinstance(exc.result, dict) else {"outcome_unknown": True}
    direct_state = _fate_cards_state_from_result(action_result)
    direct_conflict = ""
    if action_result.get("ok") and direct_state.get("state_verified"):
        direct_conflict = _fate_cards_transition_error(previous, direct_state)
        if action == "draw" and expected and direct_state.get("question_key") != expected:
            direct_conflict = "fate_question_changed"
    confirmed = bool(action_result.get("ok") and _fate_cards_action_confirmed(
        action, direct_state, expected=expected, previous=previous,
    ))
    fate_state = direct_state if confirmed else {}
    probe_result = {}
    probe_valid = False
    if cancelled is None and current():
        try:
            probe_result = await run_fate_cards_start_probe_production(
                identity_id, token=token, webview_url=webview_url, init_data=init_data,
                capture_sink=capture_sink, capture_source=f"{capture_source}:{action}:reconcile",
                operation_check=operation_check, request_budget=request_budget,
            )
        except MiniAppFlowCancelled as exc:
            cancelled = exc
            probe_result = exc.result if isinstance(exc.result, dict) else {}
        observed = _fate_cards_state_from_result(probe_result)
        probe_valid = bool(probe_result.get("ok") and not _fate_cards_transition_error(fate_state or previous, observed))
        if probe_valid and not direct_conflict:
            fate_state = observed
            confirmed = _fate_cards_action_confirmed(action, observed, expected=expected, previous=previous)
    outcome = {
        "ok": confirmed,
        "action_result": action_result,
        "probe_result": probe_result,
        "state": fate_state,
        "can_continue": bool(confirmed and probe_valid and not direct_conflict and cancelled is None and current()),
        "conflict": direct_conflict,
        "action_dispatched": action_result.get("action_dispatched"),
        "outcome_unknown": not confirmed and (
            action_result.get("outcome_unknown") is True
            or action_result.get("action_dispatched") is not False and (
                action_result.get("ok") is not False or "outcome_unknown" not in action_result
            )
        ),
        "error": "" if confirmed else str(direct_conflict or action_result.get("error") or probe_result.get("error") or f"{action}_not_confirmed"),
    }
    if cancelled is not None:
        raise MiniAppFlowCancelled(outcome) from None
    return outcome


def _cave_public_deep_retry_after(deep_state, *, now):
    deep_state = dict(deep_state or {})
    end_ms = _parse_int(deep_state.get("end_ms"), 0)
    if end_ms > int(float(now) * 1000):
        return max(60, min(12 * 3600, end_ms / 1000.0 - float(now) + deep_retreat.CD_BUFFER_SEC))
    remaining = _parse_int(deep_state.get("remaining_seconds"), 0)
    if remaining > 0:
        return max(60, min(12 * 3600, remaining + deep_retreat.CD_BUFFER_SEC))
    return CAVE_DEEP_STATUS_RECHECK_SEC


async def _run_cave_public_deep_action_locked(
    identity_id,
    *,
    token,
    webview_url,
    action,
    session=None,
    init_data="",
    now,
    capture_sink=None,
    capture_source="",
    operation=None,
    operation_check=None,
):
    """Reconcile one identity-bound action while retaining the caller's lock."""
    action = str(action or "").strip()
    operation = operation or _CaveDeepRetreatOperation.capture(identity_id)
    observation = _CAVE_PUBLIC_ENTRY_OBSERVATION.get()
    cancelled = {"ok": False, "status": "cancelled", "sent": False, "error": "洞府闭关操作已取消或身份状态已变更", "data": {}}

    def can_continue():
        return (
            operation is not None and operation.can_dispatch()
            and (operation_check is None or operation_check() is True)
            and (observation is None or observation.permits(identity_id, token))
        )

    if not can_continue():
        return cancelled
    player_id = (session or {}).get("player_id")
    player_error = cave_action_player_error({"account": {"playerId": player_id}}, identity_id)
    if player_error:
        return {"ok": False, "status": "identity_unverified", "sent": False, "error": player_error, "data": {}}
    if action != "status":
        preflight = _cave_public_deep_action_preflight(identity_id, session, action)
        if preflight.get("error"):
            return {"ok": False, "status": "identity_unverified", "sent": False, "error": preflight["error"], "data": {}}
        if not preflight.get("send"):
            raw = preflight["raw"]
            sync_result = await sync_cave_deep_seclusion_action_result(identity_id, "status", raw, now=now)
            snapshot = extract_cave_deep_seclusion_state(raw)
            cannot_restart = sync_result.get("phase") == "post_summary_wait" and snapshot.get("can_start") is not True
            if not sync_result.get("handled") or cannot_restart:
                _defer_cave_deep_status(identity_id, now)
                sync_result = {**sync_result, "phase": "launching"}
            result = {"ok": True, "status": "preflight_skip", "data": raw, "action_dispatched": False}
            record = _record_cave_deep_retreat_state(identity_id, action, result, sync_result, now=now)
            retry_after = max(30, float(operation.owner.identity.get("next_deep_retreat_time") or 0) - now)
            return {
                "ok": True, "status": "preflight_skip", "sent": False,
                "reason": preflight["reason"], "data": raw, "result": result,
                "sync": sync_result, "record": record, "retry_after_sec": retry_after,
                "outcome_unknown": bool((record.get("record", {}).get("state") or {}).get("outcome_unknown")),
            }
    cancelled_flow = None
    try:
        result = await run_cave_deep_seclusion_action_production_flow(
            identity_id, token=token, webview_url=webview_url, action=action,
            player_id=player_id, init_data=init_data, capture_sink=capture_sink,
            capture_source=f"{capture_source}:deep_{action}", operation_check=can_continue,
        )
    except MiniAppFlowCancelled as exc:
        cancelled_flow = exc
        result = exc.result if isinstance(exc.result, dict) else {"error": "cancelled_without_result"}
    if not operation.result_is_current():
        if cancelled_flow is not None:
            raise MiniAppFlowCancelled(cancelled) from None
        return cancelled
    dispatched = result.get("action_dispatched") is True
    dispatch_known = isinstance(result.get("action_dispatched"), bool)
    if dispatch_known and not dispatched and not can_continue():
        if cancelled_flow is not None:
            raise MiniAppFlowCancelled(cancelled) from None
        return cancelled
    raw = result.get("data") if isinstance(result.get("data"), dict) else {}
    sync_result = {"handled": False, "reason": result.get("error") or "action_not_sent"}
    player_error = str(result.get("error") or "") if result.get("status") == "identity_unverified" else ""
    if dispatched and result.get("ok"):
        sync_result = await sync_cave_deep_seclusion_action_result(identity_id, action, raw, now=now)
        player_error = cave_action_player_error(raw, identity_id)
        if player_error:
            result = {**result, "ok": False, "status": "identity_unverified", "error": player_error, "data": {}}
            raw = {}
    rejected = extract_cave_deep_seclusion_state(raw).get("ok") is False
    uncertain = action != "status" and bool(
        not dispatch_known or result.get("outcome_unknown")
        or (dispatched and not rejected and not sync_result.get("handled") and (result.get("ok") or player_error))
    )
    result = {
        **result, "transport_ok": bool(result.get("ok")),
        "ok": bool(result.get("ok") and sync_result.get("handled")), "outcome_unknown": uncertain,
    }
    if uncertain and not result.get("status"):
        result["status"] = "action_unknown"
    if not player_error and (uncertain or (dispatched and not sync_result.get("handled"))):
        _defer_cave_deep_status(identity_id, now)
        sync_result = {**sync_result, "phase": "launching"}
    record = _record_cave_deep_retreat_state(identity_id, action, result, sync_result, now=now)
    response = {
        "ok": bool(result.get("ok") and sync_result.get("handled")),
        "status": str(result.get("status") or ""),
        "sent": dispatched,
        "result": result,
        "sync": sync_result,
        "record": record,
        "data": dict(result.get("data") or {}),
        "error": result.get("error") or ("" if sync_result.get("handled") else sync_result.get("reason")),
        "outcome_unknown": bool((record.get("record", {}).get("state") or {}).get("outcome_unknown")),
    }
    if cancelled_flow is not None:
        raise MiniAppFlowCancelled(response) from None
    return response


def _format_cave_treasure_summary(result):
    result = dict(result or {})
    status = str(result.get("status") or "unknown").strip() or "unknown"
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    state = data.get("state") if isinstance(data.get("state"), dict) else {}
    games = ""
    if state:
        games_used = _parse_int(state.get("games_used"), 0)
        games_limit = _parse_int(state.get("games_limit"), 0)
        if games_limit > 0:
            games = f"｜游戏 {games_used}/{games_limit}"
    material_text = _format_material_counts(*_cave_treasure_materials(result))
    if data.get("material_errors") or any(receipt["material_error"] for receipt in _cave_treasure_receipts(result)):
        material_text = (material_text + "｜" if material_text else "") + "部分奖励字段无效，未计入"
    if result.get("ok"):
        if status == "daily_limit" and not material_text:
            return f"MiniApp {status}{games}｜今日次数已尽"
        return f"MiniApp {status}{games}｜{material_text or '未解析到新增物资'}"
    error = str(result.get("error") or "").strip()
    partial = f"｜已确认{material_text}" if material_text else ""
    return f"MiniApp {status}{games}{partial}｜{error or '未完成'}"


def _iter_nested_dicts(value, *, depth=0):
    if depth > 5:
        return
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_nested_dicts(child, depth=depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_nested_dicts(child, depth=depth + 1)


def extract_cave_deep_seclusion_action_message(data):
    data = data if isinstance(data, dict) else {}
    for container_key in ("actionResult", "result", "data"):
        container = data.get(container_key)
        if not isinstance(container, dict):
            continue
        for key in ("rawMessage", "raw_message", "message", "text", "statusText", "status_text"):
            text = str(container.get(key) or "").strip()
            if text:
                return text
    for item in _iter_nested_dicts(data or {}):
        for key in ("rawMessage", "raw_message", "message", "text", "statusText", "status_text"):
            text = str(item.get(key) or "").strip()
            if text:
                return text
    return ""


def _cave_deep_payload_parts(data):
    data = data if isinstance(data, dict) else {}
    root = data.get("data") if isinstance(data.get("data"), dict) else data
    action_result = root.get("actionResult") if isinstance(root.get("actionResult"), dict) else {}
    dwelling = root.get("dwelling") if isinstance(root.get("dwelling"), dict) else {}
    meditation = dwelling.get("meditation") if isinstance(dwelling.get("meditation"), dict) else {}
    deep_state = meditation.get("deepSeclusion") or root.get("deep_seclusion") or root.get("deepSeclusion") or {}
    deep_state = deep_state if isinstance(deep_state, dict) else {}
    return root, action_result, deep_state


def extract_cave_deep_seclusion_state(data):
    """Extract authoritative deep-seclusion fields from action or dwelling payloads."""
    _root, action_result, deep_state = _cave_deep_payload_parts(data)

    def optional_bool(container, *keys):
        for key in keys:
            if key in container and isinstance(container.get(key), bool):
                return container.get(key)
        return None

    remaining_raw = action_result.get(
        "remainingSeconds",
        action_result.get(
            "remaining_seconds",
            deep_state.get("remainingSeconds", deep_state.get("remaining_seconds")),
        ),
    )
    end_ms_raw = action_result.get(
        "endMs",
        action_result.get("end_ms", deep_state.get("endMs", deep_state.get("end_ms"))),
    )
    def optional_nonnegative_int(value):
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            return None
        text = str(value).strip()
        if len(text) > 16 or not text.isascii() or not text.isdigit():
            return None
        parsed = int(text)
        return parsed if parsed <= 2**53 - 1 else None

    remaining_seconds = optional_nonnegative_int(remaining_raw)
    end_ms = optional_nonnegative_int(end_ms_raw)
    # Command-center completed acknowledges the command, not the retreat.
    # Keep the older direct deep-action format only when no panel/command exists.
    completed = optional_bool(deep_state, "completed")
    if not deep_state and "command" not in action_result:
        completed = optional_bool(action_result, "completed")
    active = optional_bool(action_result, "active")
    if active is None:
        active = optional_bool(deep_state, "active")
    can_settle = optional_bool(action_result, "canSettle", "can_settle")
    if can_settle is None:
        can_settle = optional_bool(deep_state, "canSettle", "can_settle")
    can_start = optional_bool(deep_state, "canStart", "can_start")
    can_force_exit = optional_bool(deep_state, "canForceExit", "can_force_exit")
    conflicting = (
        (can_start is True and (active is True or can_settle is True or completed is True))
        or ((remaining_seconds or 0) > 0 and (active is False or can_settle is True or completed is True))
    )
    message = ""
    for container in (action_result, deep_state):
        for key in ("rawMessage", "raw_message", "message", "text", "statusText", "status_text"):
            if isinstance(container.get(key), str) and container[key].strip():
                message = container[key].strip()
                break
        if message:
            break
    return {
        "known": bool(action_result or deep_state),
        "conflicting": bool(conflicting),
        "ok": optional_bool(action_result, "ok"),
        "completed": completed,
        "active": active,
        "can_settle": can_settle,
        "can_start": can_start,
        "can_force_exit": can_force_exit,
        "remaining_seconds": remaining_seconds,
        "end_ms": end_ms,
        "message": message,
    }


def _cave_public_deep_action_preflight(identity_id, session, action):
    """A selector or truthy placeholder never grants a mutation permission."""
    result_data = dict((session.get("result") or {}).get("data") or {})
    snapshot_data = result_data.get("raw") if isinstance(result_data.get("raw"), dict) else result_data
    error = cave_action_player_error(snapshot_data, identity_id)
    if error:
        return {"send": False, "error": error}
    root, _action_result, deep_state = _cave_deep_payload_parts(snapshot_data)
    snapshot_data = {"account": root["account"], "deep_seclusion": deep_state}
    snapshot = extract_cave_deep_seclusion_state(snapshot_data)
    previous = get_miniapp_state_records().get(f"{identity_id}:cave_deep_retreat") or {}
    pending = bool((previous.get("state") or {}).get("outcome_unknown"))
    permission = {"start": "can_start", "settle": "can_settle", "force": "can_force_exit"}.get(action)
    allowed = snapshot.get(permission) is True
    if action == "settle" and not any(key in deep_state for key in ("canSettle", "can_settle")):
        allowed = snapshot.get("completed") is True
    if action == "start" and (snapshot.get("active") is True or snapshot.get("completed") is True):
        allowed = False
    if action == "settle" and (snapshot.get("remaining_seconds") or 0) > 0:
        allowed = False
    return {
        "send": allowed and not pending and not snapshot["conflicting"],
        "raw": snapshot_data,
        "reason": "previous_action_unknown" if pending else f"{permission}_not_granted",
    }


def _defer_cave_deep_status(identity_id, now):
    with use_identity(identity_id):
        deep_retreat.clear_deep_retreat_summary_flags()
        deep_retreat.set_deep_retreat_phase("launching")
        state["deep_retreat_probe_pending"] = False
        state["next_deep_retreat_time"] = now + CAVE_DEEP_STATUS_RECHECK_SEC
        save_state()


def extract_cave_tianjige_command_message(data):
    """Extract the player-facing command-center reply without exposing request secrets."""
    return extract_cave_deep_seclusion_action_message(data)


def _cave_tianjige_action_succeeded(data):
    data = data if isinstance(data, dict) else {}
    if data.get("ok", True) is not True:
        return False
    root = data.get("data") if isinstance(data.get("data"), dict) else data
    action_result = root.get("actionResult") if isinstance(root.get("actionResult"), dict) else {}
    return root.get("ok", True) is True and action_result.get("ok") is True and action_result.get("completed", True) is True


def _cave_tianjige_session_player_error(session, identity_id):
    selected = cave_action_player_error({"account": {"playerId": session.get("player_id")}}, identity_id)
    result = session.get("result") if isinstance(session.get("result"), dict) else {}
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    return selected or cave_action_player_error(data.get("raw"), identity_id)


async def sync_cave_tianjige_yuanying_result(identity_id, data, *, now, command=None):
    """Replay only safe Tianjige YuanYing outcomes into the existing state machine.

    The normal status handler can emit a legacy group command for `窍中温养`.
    A public-entry response must never trigger that side effect, so this bridge
    handles success and explicit cooldown wording only.
    """
    identity_id = _identity_id(identity_id)
    player_error = cave_action_player_error(data, identity_id)
    if player_error:
        return {"handled": False, "ready": False, "reason": player_error, "message": "", "phase": ""}
    message = extract_cave_tianjige_command_message(data)
    if MiniAppIdentityOwner.capture(identity_id) is None or not message:
        return {"handled": False, "reason": "missing_identity_or_message", "message": "", "phase": ""}

    command = str(command or yuanying.CMD_YUANYING).strip()
    with use_identity(identity_id):
        if not _cave_tianjige_action_succeeded(data):
            return {
                "handled": False,
                "ready": False,
                "reason": "action_rejected",
                "message": message,
                "phase": str(state.get("yuanying_phase") or ""),
            }

        plain_message = re.sub(r"[*_`]+", "", message)
        ready_status = command == yuanying.CMD_YUANYING_STATUS and re.search(r"状态\s*[:：]\s*窍中温养", plain_message)
        retreat_status = command == yuanying.CMD_YUANYING_STATUS and re.search(r"状态\s*[:：]\s*元婴闭关", plain_message)
        if ready_status and (retreat_status or "归来倒计时" in plain_message):
            return {
                "handled": False, "ready": False, "reason": "conflicting_yuanying_status",
                "message": message, "phase": str(state.get("yuanying_phase") or ""),
            }
        status_ready = bool(
            ready_status
            and not any(token in plain_message for token in ("不可", "不能", "暂不", "尚未", "冷却", "等待", "休息", "不足"))
        )
        if status_ready:
            state["yuanying_probe_pending"] = False
            yuanying.clear_yuanying_summary_flags()
            yuanying.set_yuanying_phase("idle")
            state["next_yuanying_time"] = float(now)
            save_state()
            return {
                "handled": True,
                "ready": True,
                "kind": "ready",
                "reason": "",
                "message": message,
                "phase": str(state.get("yuanying_phase") or ""),
            }

        if retreat_status:
            previous_next_time = float(state.get("next_yuanying_time", 0) or 0)
            state["yuanying_probe_pending"] = False
            yuanying.clear_yuanying_summary_flags()
            yuanying.set_yuanying_phase("running")
            state["next_yuanying_time"] = max(
                previous_next_time,
                float(now) + CAVE_YUANYING_STATUS_RECHECK_SEC,
            )
            save_state()
            return {
                "handled": True,
                "ready": False,
                "reason": "active_yuanying_retreat",
                "kind": "retreat",
                "message": message,
                "phase": str(state.get("yuanying_phase") or ""),
            }

        launched = (
            command == yuanying.CMD_YUANYING
            and "你心念一动" in message and "元婴化作一道流光飞出" in message
        )
        cooldown = (
            any(token in message for token in ("尚未恢复", "冷却", "等待", "不足", "休息", "归来倒计时"))
            and "窍中温养" not in message and yuanying.has_wait_time(message)
        )
        if launched or cooldown:
            wait_sec = yuanying.parse_wait_time(message)
            if launched and wait_sec <= 0:
                wait_sec = yuanying.YUANYING_SPEC.cd_sec
            yuanying.mark_yuanying_success(now, now + wait_sec + CD_BUFFER_SEC)
            kind = "running" if wait_sec > 0 and "归来倒计时" in message else "cooldown"
            return {
                "handled": True,
                "ready": False,
                "kind": "launched" if launched else kind,
                "reason": "",
                "message": message,
                "phase": str(state.get("yuanying_phase") or ""),
            }
        return {
            "handled": False,
            "ready": False,
            "reason": "unrecognized_or_nonterminal_message",
            "message": message,
            "phase": str(state.get("yuanying_phase") or ""),
        }


async def sync_cave_deep_seclusion_action_result(identity_id, action, data, *, now):
    """Apply identity-bound retreat evidence without invoking Telegram handlers."""

    identity_id = _identity_id(identity_id)
    action = str(action or "").strip()
    player_error = cave_action_player_error(data, identity_id)
    if player_error:
        return {"handled": False, "reason": player_error, "message_kind": ""}
    snapshot = extract_cave_deep_seclusion_state(data)
    message = snapshot["message"]
    if MiniAppIdentityOwner.capture(identity_id) is None or (not message and not snapshot.get("known")):
        return {"handled": False, "reason": "missing_identity_or_message", "message_kind": ""}
    if snapshot["conflicting"]:
        return {"handled": False, "reason": "conflicting_deep_snapshot", "message_kind": ""}
    previous = get_miniapp_state_records().get(f"{identity_id}:cave_deep_retreat") or {}
    pending = previous.get("state") or {}
    if action == "status" and pending.get("outcome_unknown") and not _cave_deep_unknown_postcondition(pending, snapshot):
        return {"handled": False, "reason": "previous_action_unknown", "message_kind": ""}

    with use_identity(identity_id):
        remaining = snapshot.get("remaining_seconds")
        end_ms = snapshot.get("end_ms")
        started = (
            "你已进入深度闭关状态" in message and "神魂将自行吐纳" in message
            and snapshot.get("ok") is not False and snapshot.get("active") is not False
        )
        running = (
            snapshot.get("active") is True and snapshot.get("can_settle") is not True
            and snapshot.get("completed") is not True
        ) or (remaining is not None and remaining > 0)
        running = running or "你已在深度闭关之中" in message or (
            ("闭关中" in message and "剩余" in message)
            or ("预计还需" in message and "即可功成圆满" in message)
        )
        summary = deep_retreat._is_deep_retreat_summary_text(message) or (
            snapshot.get("ok") is True and snapshot.get("active") is False and snapshot.get("can_start") is True
        )
        if action in {"settle", "force"} and summary and snapshot.get("ok") is not False and not running:
            deep_retreat.begin_post_summary_wait(deep_retreat.DEEP_RETREAT_SPEC, now, confirmed=True)
            state["last_deep_retreat_command_time"] = now
            save_state()
            return {"handled": True, "reason": "", "message_kind": "summary", "phase": "post_summary_wait"}
        if started or running:
            wait_sec = remaining if remaining is not None and remaining > 0 else deep_retreat.parse_wait_time(message)
            next_time = (
                end_ms / 1000.0 + deep_retreat.CD_BUFFER_SEC
                if end_ms and end_ms > now * 1000
                else now + (wait_sec if wait_sec > 0 else CAVE_DEEP_STATUS_RECHECK_SEC) + deep_retreat.CD_BUFFER_SEC
            )
            deep_retreat.mark_deep_retreat_success(now, next_time)
            return {
                "handled": True, "ready": False, "reason": "still_running" if running else "",
                "message_kind": "start" if action == "start" else "status" if action == "status" else "running",
                "phase": "running", "remaining_seconds": remaining,
            }
        if deep_retreat._is_deep_retreat_short_cd_text(message):
            deep_retreat.clear_deep_retreat_summary_flags()
            deep_retreat.set_deep_retreat_phase("idle")
            state["deep_retreat_probe_pending"] = False
            state["last_deep_retreat_command_time"] = now
            state["next_deep_retreat_time"] = now + deep_retreat.parse_wait_time(message) + deep_retreat.CD_BUFFER_SEC
            save_state()
            return {"handled": True, "reason": "cooldown", "message_kind": "status", "phase": "idle"}
        if action == "status" and (
            snapshot.get("can_settle") is True
            or (snapshot.get("can_settle") is None and snapshot.get("completed") is True)
        ):
            deep_retreat.set_deep_retreat_phase("summary_due")
            state["deep_retreat_probe_pending"] = False
            state["next_deep_retreat_time"] = now + deep_retreat.CD_BUFFER_SEC
            save_state()
            return {"handled": True, "ready": True, "reason": "settlement_due", "message_kind": "status", "phase": "summary_due"}
        if action == "status" and (
            snapshot.get("can_start") is True
            or (snapshot.get("can_start") is None and "未处于深度闭关" in message and snapshot.get("ok") is not False)
        ):
            deep_retreat.begin_post_summary_wait(deep_retreat.DEEP_RETREAT_SPEC, now, confirmed=True)
            return {"handled": True, "reason": "not_running", "message_kind": "status", "phase": "post_summary_wait"}
        if action == "settle":
            deep_retreat.clear_deep_retreat_summary_flags()
            deep_retreat.set_deep_retreat_phase("launching")
            state["deep_retreat_probe_pending"] = False
            state["next_deep_retreat_time"] = now + CAVE_DEEP_STATUS_RECHECK_SEC
            save_state()
            return {"handled": False, "reason": "ambiguous_settle_recheck_status", "message_kind": "other", "phase": "launching"}
        if action in {"start", "status", "force"}:
            return {"handled": False, "reason": f"{action}_message_not_handled", "message_kind": action, "phase": str(state.get("deep_retreat_phase") or "")}

    return {"handled": False, "reason": "unsupported_action", "message_kind": ""}


def _cave_deep_unknown_postcondition(pending, snapshot):
    if snapshot.get("ok") is False or snapshot.get("conflicting"):
        return False
    action = pending.get("unknown_action") or pending.get("action")
    if action == "start":
        return snapshot.get("active") is True or snapshot.get("completed") is True or snapshot.get("can_settle") is True
    if action in {"settle", "force"}:
        return (
            snapshot.get("active") is False and snapshot.get("can_start") is True
            and snapshot.get("completed") is not True and snapshot.get("can_settle") is not True
        )
    return False


def _cave_treasure_inventory_source_id(data, *, result_msg_id=0):
    data = data if isinstance(data, dict) else {}
    result_payload = [receipt["result"] for receipt in _cave_treasure_receipts({"data": data})]
    sessions = sorted({treasure_session_id(row.get("sessionId") or row.get("session_id"))
                       for row in result_payload} - {""})
    result_digest = stable_payload_digest(result_payload)
    if sessions:
        return f"sessions:{stable_payload_digest(sessions)}:{result_digest}"
    if int(result_msg_id or 0) > 0:
        return f"msg:{int(result_msg_id or 0)}:{result_digest}"
    return f"payload:{result_digest}"


def _cave_treasure_receipts(result):
    result = dict(result or {})
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    rows = data.get("results") if isinstance(data.get("results"), list) else []
    receipts, seen = [], set()
    for row in rows:
        receipt = project_treasure_settlement(row)
        if not receipt["confirmed"]:
            continue
        session_id = treasure_session_id(receipt["result"].get("sessionId") or receipt["result"].get("session_id"))
        if session_id and session_id in seen:
            continue
        if session_id:
            seen.add(session_id)
        receipts.append(receipt)
    return receipts


def _cave_treasure_materials(result):
    rewards, gains = {}, {}
    for receipt in _cave_treasure_receipts(result):
        for target, values in ((rewards, receipt["rewards"]), (gains, receipt["gains"])):
            for name, amount in values.items():
                target[name] = target.get(name, 0) + amount
    return tuple({name: amount for name, amount in values.items() if treasure_integer(amount) not in (None, 0)}
                 for values in (rewards, gains))


def _cave_treasure_inventory_items(result):
    rewards, gains = _cave_treasure_materials(result)
    items = dict(rewards)
    for name in _INVENTORY_GAIN_NAMES:
        amount = _parse_int(gains.get(name), 0)
        if amount > 0:
            items[name] = _parse_int(items.get(name), 0) + amount
    return {name: count for name, count in items.items() if str(name or "").strip() and _parse_int(count, 0) > 0}


def _record_cave_treasure_inventory_delta(identity_id, result, *, now, result_msg_id=0, prepare=False, operation_id=""):
    data = (result or {}).get("data") if isinstance((result or {}).get("data"), dict) else {}
    items = _cave_treasure_inventory_items(result)
    if not items:
        return {"changed": False, "record": {}, "record_key": ""}
    return (prepare_inventory_delta if prepare else record_inventory_delta)(
        identity_id,
        source="cave_treasure_miniapp",
        source_id=("operation:" + operation_id) if operation_id else _cave_treasure_inventory_source_id(data, result_msg_id=result_msg_id),
        items=items,
        now=now,
        source_summary={
            "status": (result or {}).get("status") or "",
            "settled_count": len(_cave_treasure_receipts(result)),
            "result_msg_id": int(result_msg_id or 0),
        },
    )


def _cave_treasure_state_source_id(result, *, result_msg_id=0):
    result = dict(result or {})
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    state = data.get("state") if isinstance(data.get("state"), dict) else {}
    digest = stable_payload_digest({
        "status": result.get("status") or "",
        "state": state,
        "settled_count": _parse_int(result.get("settled_count") or data.get("settled_count"), 0),
    })
    if int(result_msg_id or 0) > 0:
        return f"msg:{int(result_msg_id or 0)}:{digest}"
    return f"payload:{digest}"


def _record_cave_treasure_miniapp_state(identity_id, result, *, now, result_msg_id=0, prepare=False, operation_record=None):
    data = (result or {}).get("data") if isinstance((result or {}).get("data"), dict) else {}
    state = dict(data.get("state") or {}) if isinstance(data.get("state"), dict) else {}
    unknown = bool((result or {}).get("outcome_unknown") or state.get("outcome_unknown")
                   or str((result or {}).get("status") or "").strip() == "result_unknown")
    previous = dict(get_miniapp_state_records().get(f"{int(identity_id)}:cave_treasure") or {})
    owner = MiniAppIdentityOwner.capture(identity_id)
    reconciled = bool(prepare and owner and treasure_operations.valid_record(operation_record)
                      and operation_record["version"] == 2
                      and treasure_results._same(owner.identity.get(treasure_operations.STATE_KEY), operation_record)
                      and operation_record["miniapp_before"] == treasure_results._basis(
                          get_miniapp_state_records(), f"{int(identity_id)}:cave_treasure"))
    if (previous.get("state") or {}).get("outcome_unknown") and not unknown and not reconciled:
        return {"changed": False, "record": previous, "record_key": f"{int(identity_id)}:cave_treasure"}
    if unknown:
        state["outcome_unknown"] = True
        state["outcome_unknown_day"] = get_day_key(now)
    if not state:
        return {"changed": False, "record": {}, "record_key": ""}
    state["owner_account_id"] = get_identity_account(identity_id) or int(identity_id)
    return (prepare_miniapp_state if prepare else record_miniapp_state)(
        identity_id,
        "cave_treasure",
        state,
        source="cave_treasure_miniapp",
        source_id=_cave_treasure_state_source_id(result, result_msg_id=result_msg_id),
        now=now,
        outputs=_CAVE_TREASURE_STATE_OUTPUTS,
        replaces_commands=(".洞府",),
    )


def _cave_treasure_unknown_hold(identity_id, now):
    # An account-shared mutation is not reconciled by a new day or another role.
    account_id = get_identity_account(identity_id) or int(identity_id)
    for key, record in get_miniapp_state_records().items():
        if not str(key).endswith(":cave_treasure") or not isinstance(record, dict):
            continue
        record_state = record.get("state") if isinstance(record.get("state"), dict) else {}
        if not record_state.get("outcome_unknown"):
            continue
        record_identity = _parse_int(str(key).split(":", 1)[0], 0)
        record_account = _parse_int(record_state.get("owner_account_id"), 0) or get_identity_account(record_identity) or record_identity
        if record_identity == int(identity_id) or record_account == account_id:
            return True
    return False


@dataclass(frozen=True)
class _CaveSmallWorldOperation:
    owner: MiniAppIdentityOwner
    snapshot: dict
    record: dict

    @classmethod
    def capture(cls, identity_id):
        owner = MiniAppIdentityOwner.capture(identity_id)
        if owner is None:
            return None
        return cls(
            owner,
            {key: deepcopy(value) for key, value in owner.identity.items()
             if (key.startswith("small_world_") or key == "next_small_world_time")
             and key != "small_world_last_public_request_at"},
            deepcopy(get_miniapp_state_records().get(f"{identity_id}:cave_small_world") or {}),
        )

    def matches(self, *keys):
        return self.owner.is_current() and all(
            self.owner.identity.get(key) == self.snapshot.get(key) for key in keys
        )

    def record_is_current(self):
        return self.owner.is_current() and self.record == (
            get_miniapp_state_records().get(f"{self.owner.identity_id}:cave_small_world") or {}
        )

    def panel_is_current(self, now):
        return self.matches(
            "small_world_last_panel_at", "small_world_panel_snapshot", "small_world_faith_value",
            "small_world_incense_stock", "small_world_pending_incense",
        ) and self.record_is_current() and max(
            float(self.snapshot.get("small_world_last_panel_at") or 0),
            float(self.record.get("updated_at") or 0),
        ) <= now

    def is_current(self):
        return (
            self.matches(*self.snapshot)
            and self.record_is_current()
            and is_cave_public_identity_available(self.owner.identity_id)
        )


def _record_cave_small_world_state(identity_id, result, *, now, result_msg_id=0, update_snapshot=True):
    data = dict((result or {}).get("data") or {})
    overview = data.get("overview") if isinstance(data.get("overview"), dict) else {}
    small_world = overview.get("small_world") if isinstance(overview.get("small_world"), dict) else {}
    confirmed = data.get("action_confirmed") is True
    if not (small_world and update_snapshot) and not confirmed:
        return {"changed": False, "record": {}, "record_key": ""}
    previous = get_miniapp_state_records().get(f"{identity_id}:cave_small_world") or {}
    payload = dict(previous.get("state") or {})
    if small_world and update_snapshot:
        payload.update(small_world)
        payload["snapshot_updated_at"] = now
    payload["snapshot_current"] = bool(small_world and update_snapshot)
    if confirmed:
        payload.update(
            last_action=data.get("action") or "", last_action_at=now, last_action_confirmed=True,
            last_action_message=_cave_small_world_action_message(result),
        )
    return record_miniapp_state(
        identity_id,
        "cave_small_world",
        payload,
        source="cave_dwelling_miniapp",
        source_id=f"cave_small_world:{int(result_msg_id or 0)}:{stable_payload_digest(payload)}",
        now=now,
        outputs=("module_snapshot",),
        replaces_commands=(".小世界",),
        persist=False,
    )


def _cave_small_world_panel_snapshot(small_world, now):
    small_world = small_world if isinstance(small_world, dict) else {}
    missing = small_world.get("prayer_missing_resources") if isinstance(small_world.get("prayer_missing_resources"), list) else []
    manifest_cost = "、".join(
        f"{item.get('name') or '资源'}缺{int(item.get('missing', 0) or 0)}"
        for item in missing
        if isinstance(item, dict) and int(item.get("missing", 0) or 0) > 0
    )
    return {
        "temple_level": int(small_world.get("temple_level", 0) or 0),
        "temple_name": str(small_world.get("temple_name") or ""),
        "population": int(small_world.get("population", 0) or 0),
        "capacity": int(small_world.get("population_cap", 0) or 0),
        "faith": int(small_world.get("faith", 0) or 0),
        "faith_max": int(small_world.get("faith_cap", 100) or 100),
        "stability": int(small_world.get("stability", 0) or 0),
        "stability_max": int(small_world.get("stability_cap", 100) or 100),
        "pending_incense": float(small_world.get("pending_incense", 0) or 0),
        "stock": int(small_world.get("incense_stock", 0) or 0),
        "hourly_output": float(small_world.get("hourly_incense", 0) or 0),
        "barrier_status": "已开启" if small_world.get("barrier_active") else "未开启",
        "spiritual_strength": 0,
        "has_prayer": bool(small_world.get("has_prayer")),
        "prayer_name": str(small_world.get("prayer_title") or ""),
        "manifest_cost": manifest_cost,
        "has_wait": int(small_world.get("prayer_remaining_seconds", 0) or 0) > 0,
        "wait_sec": int(small_world.get("prayer_remaining_seconds", 0) or 0),
        "wait_text": "",
        "updated_at": float(now),
    }


def _cave_small_world_silence_threshold():
    if not state.get("small_world_high_stock_silence_enabled", False):
        return 0
    try:
        configured = int(state.get("small_world_barrier_min_stock", 130000) or 130000)
    except (TypeError, ValueError):
        configured = 130000
    return max(100_000, configured)


def _cave_small_world_high_stock_silence(small_world):
    threshold = _cave_small_world_silence_threshold()
    try:
        stock = int((small_world or {}).get("incense_stock", 0) or 0)
    except (TypeError, ValueError):
        stock = 0
    if threshold <= 0 or stock < threshold:
        return None
    return {
        "silent": True,
        "suppress_refresh": True,
        "reason": f"高香火静默：库存 {stock} 已达阈值 {threshold}，跳过刷新/维护",
    }


def _apply_cave_small_world_overview(small_world, now):
    snapshot = _cave_small_world_panel_snapshot(small_world, now)
    state["small_world_last_panel_at"] = float(now)
    state["small_world_faith_value"] = int(snapshot.get("faith", 0) or 0)
    state["small_world_pending_incense"] = float(snapshot.get("pending_incense", 0) or 0)
    state["small_world_incense_stock"] = int(snapshot.get("stock", 0) or 0)
    state["small_world_panel_snapshot"] = snapshot
    return snapshot


def _cave_small_world_harvest_due(now):
    if not state.get("small_world_harvest_enabled"):
        return False
    return float(state.get("small_world_next_public_harvest_at", 0) or 0) <= float(now or time.time())


def _cave_small_world_prayer_due_at(small_world, now):
    small_world = small_world if isinstance(small_world, dict) else {}
    try:
        remaining = int(small_world.get("prayer_remaining_seconds", 0) or 0)
    except (TypeError, ValueError):
        remaining = 0
    if remaining > 0:
        return float(now + remaining + CD_BUFFER_SEC)
    return float(now) if small_world.get("has_prayer") else 0.0


def _cave_small_world_next_check_at(small_world, now, *, default_delay=CAVE_SMALL_WORLD_CYCLE_SEC):
    prayer_due_at = _cave_small_world_prayer_due_at(small_world, now)
    if prayer_due_at > 0:
        return prayer_due_at
    return float(now + default_delay)


def _preserve_small_world_timer_after_harvest(existing_next_time, small_world, now):
    """A harvest-only MiniApp pass must not postpone the prayer state machine."""
    try:
        existing_next_time = float(existing_next_time or 0)
    except (TypeError, ValueError):
        existing_next_time = 0.0
    if existing_next_time > 0:
        return existing_next_time
    return _cave_small_world_prayer_due_at(small_world, now)


def _plan_cave_public_small_world_action(overview, *, now=None):
    now = float(now or time.time())
    small_world = overview.get("small_world") if isinstance(overview, dict) and isinstance(overview.get("small_world"), dict) else {}
    if not small_world or not small_world.get("available") or not small_world.get("has_world"):
        return {"reason": "小世界尚不可用", "suppress_refresh": True}

    harvest_due = _cave_small_world_harvest_due(now)
    can_harvest = bool(small_world.get("can_harvest"))
    harvest_checked = bool(harvest_due and not can_harvest)

    if small_world.get("has_prayer"):
        if state.get("small_world_manifest_enabled") and small_world.get("can_manifest") and small_world.get("prayer_resources_ready"):
            return {"action": "manifest", "reason": f"处理祈愿 {small_world.get('prayer_title') or '凡人祈愿'}"}
        if harvest_due and can_harvest:
            return {"action": "collect", "harvest_due": True, "reason": "8 小时收割到期，祈愿暂不可处理"}
        if not state.get("small_world_manifest_enabled"):
            return {"harvest_due": harvest_due, "harvest_checked": harvest_checked, "reason": "检测到祈愿，但自动显灵未开启"}
        missing = small_world.get("prayer_missing_resources") or []
        missing_text = "、".join(
            f"{item.get('name') or '资源'}缺{int(item.get('missing', 0) or 0)}"
            for item in missing
            if isinstance(item, dict)
        )
        return {
            "blocked": "resource",
            "harvest_due": harvest_due,
            "harvest_checked": harvest_checked,
            "reason": missing_text or "显灵资源不足或当前不可显灵",
        }

    silence_plan = _cave_small_world_high_stock_silence(small_world)
    if silence_plan:
        silence_plan.update({"harvest_due": harvest_due, "harvest_checked": harvest_due})
        return silence_plan

    if harvest_due and can_harvest:
        return {"action": "collect", "harvest_due": True, "reason": "MiniApp 8 小时收割到期"}

    if state.get("small_world_preach_enabled") and int(small_world.get("edict_remaining_seconds", 0) or 0) <= 0:
        faith = int(small_world.get("faith", 0) or 0)
        faith_cap = int(small_world.get("faith_cap", 100) or 100)
        if faith > 0 and faith_cap > 0 and faith / faith_cap <= SMALL_WORLD_PREACH_FAITH_RATIO_TRIGGER:
            return {
                "action": "miracle_sermon",
                "harvest_due": harvest_due,
                "harvest_checked": harvest_checked,
                "reason": f"信仰 {faith}/{faith_cap}，执行布道",
            }

    if state.get("small_world_refine_enabled"):
        stock = int(small_world.get("incense_stock", 0) or 0)
        amount = _calc_refine_amount(stock)
        if amount >= 10:
            return {
                "action": "refine_shenshi",
                "payload": {"amount": amount},
                "harvest_due": harvest_due,
                "harvest_checked": harvest_checked,
                "reason": f"淬炼神识 {amount} 香火",
            }

    return {
        "harvest_due": harvest_due,
        "harvest_checked": harvest_checked,
        "reason": "8 小时收割已检查，当前无可收香火" if harvest_checked else "当前无已启用且可执行的小世界动作",
    }


def _plan_cave_public_small_world_harvest(overview, *, now=None):
    now = float(now or time.time())
    small_world = overview.get("small_world") if isinstance(overview, dict) and isinstance(overview.get("small_world"), dict) else {}
    if not small_world or not small_world.get("available") or not small_world.get("has_world"):
        return {"reason": "小世界尚不可用", "suppress_refresh": True}
    if not state.get("small_world_harvest_enabled"):
        return {"reason": "自动收割香火未开启"}
    if not _cave_small_world_harvest_due(now):
        return {"reason": "MiniApp 收割尚未到 8 小时周期"}
    if small_world.get("can_harvest"):
        return {"action": "collect", "harvest_due": True, "reason": "MiniApp 8 小时收割到期"}
    return {
        "harvest_due": True,
        "harvest_checked": True,
        "reason": "8 小时收割已检查，当前无可收香火",
    }


def _cave_small_world_action_message(result):
    data = dict(result.get("data") or {})
    action_result = data.get("action_result") if isinstance(data.get("action_result"), dict) else {}
    return str(action_result.get("rawMessage") or action_result.get("message") or result.get("error") or "").strip()


def _record_cave_deep_retreat_state(identity_id, action, result, sync_result, *, now, result_msg_id=0):
    data = dict((result or {}).get("data") or {})
    previous = get_miniapp_state_records().get(f"{identity_id}:cave_deep_retreat") or {}
    previous_state = previous.get("state") if isinstance(previous.get("state"), dict) else {}
    verified = not bool(cave_action_player_error(data, identity_id))
    resolved = verified and bool((sync_result or {}).get("handled"))
    new_unknown = bool((result or {}).get("outcome_unknown"))
    unresolved = new_unknown or (bool(previous_state.get("outcome_unknown")) and not resolved)
    payload = {
        "action": str(action or ""),
        "ok": bool((result or {}).get("ok")),
        "status": str((result or {}).get("status") or ""),
        "identity_verified": verified,
        "parser_version": 2,
        "action_dispatched": (result or {}).get("action_dispatched"),
        "outcome_unknown": unresolved,
        "sync": dict(sync_result or {}),
    }
    # Retain only typed gameplay fields for diagnosing rejected snapshots.
    # Never persist the raw dwelling response, authentication or inventory.
    if verified:
        snapshot = extract_cave_deep_seclusion_state(data)
        payload["snapshot"] = {key: value for key, value in snapshot.items() if key != "message"}
    if unresolved:
        if previous_state.get("outcome_unknown"):
            payload["unknown_action"] = str(previous_state.get("unknown_action") or previous_state.get("action") or action)
            payload["unknown_since"] = previous_state.get("unknown_since") or previous.get("updated_at") or now
        else:
            payload.update(unknown_action=str(action), unknown_since=now)
    message = extract_cave_deep_seclusion_action_message(data)
    if message:
        payload["message_digest"] = stable_payload_digest(message)
        payload["message_kind"] = (sync_result or {}).get("message_kind", "")
    return record_miniapp_state(
        identity_id,
        "cave_deep_retreat",
        payload,
        source="cave_dwelling_miniapp",
        source_id=f"cave_deep_retreat:{str(action or '')}:{int(result_msg_id or 0)}:{stable_payload_digest(payload)}",
        now=now,
        outputs=("module_snapshot", "deep_retreat_state"),
        replaces_commands=(".深度闭关", ".查看闭关"),
    )


def _capture_store(now):
    day_key = get_day_key(now)
    path = CAVE_TREASURE_MINIAPP_CAPTURE_DIR / f"cave_treasure-{day_key}.jsonl"
    return MiniAppCaptureStore(path, keep_memory=False)


def _record_cave_treasure_business_capture(capture_sink, result, *, source, now):
    result = dict(result or {})
    receipts = _cave_treasure_receipts(result)
    settled_count = len(receipts)
    if settled_count <= 0:
        return {}
    found_main = sum(
        1
        for receipt in receipts
        if receipt["result"].get("foundMain") is True or receipt["result"].get("found_main") is True
    )
    rewards, gains = _cave_treasure_materials(result)
    return append_business_capture(
        capture_sink,
        adapter_key="cave_treasure",
        detail={
            "settled_count": settled_count,
            "found_main": found_main,
            "gains": gains,
            "items": rewards,
            "material_errors": sorted({receipt["material_error"] for receipt in receipts if receipt["material_error"]}),
        },
        source=source,
        created_at=now,
    )


def _tower_capture_store(now):
    day_key = get_day_key(now)
    path = CAVE_TREASURE_MINIAPP_CAPTURE_DIR / f"tower-{day_key}.jsonl"
    return MiniAppCaptureStore(path, keep_memory=False)


_WILD_TRAINING_MODE_MAP = {
    "谨慎": "cautious",
    "均衡": "balanced",
    "深入": "deep",
}


def _server_epoch_seconds(value):
    try:
        timestamp = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not math.isfinite(timestamp):
        return 0.0
    if timestamp > 10_000_000_000:
        timestamp /= 1000.0
    return max(0.0, timestamp)


def _wild_training_server_next_time(wild, *, now):
    wild = wild if isinstance(wild, dict) else {}
    remaining = max(0, _parse_int(wild.get("remaining_seconds"), 0))
    if remaining > 0:
        return float(now) + remaining
    ready_at = _server_epoch_seconds(wild.get("ready_at"))
    if ready_at > float(now):
        return ready_at
    if _parse_int(wild.get("daily_remaining"), 0) <= 0:
        reset_at = _server_epoch_seconds(wild.get("reset_at"))
        if reset_at > float(now):
            return reset_at
    return 0.0


def _wild_training_post_action_next_time(wild, action_result, *, now):
    next_time = _wild_training_server_next_time(wild, now=now)
    if next_time > float(now):
        return next_time
    wild = wild if isinstance(wild, dict) else {}
    action_result = action_result if isinstance(action_result, dict) else {}
    daily_limit = _parse_int(wild.get("daily_limit"), 0) or _parse_int(action_result.get("dailyLimit"), 0)
    daily_count = _parse_int(wild.get("daily_count"), -1)
    if daily_count < 0:
        daily_count = _parse_int(action_result.get("dailyCount"), 0)
    daily_remaining = _parse_int(wild.get("daily_remaining"), max(0, daily_limit - daily_count))
    if daily_remaining > 0 and wild.get("available") is True:
        return float(now) + WILD_TRAINING_NO_COOLDOWN_FOLLOWUP_SEC
    return float(now) + 30 * 60


def _wild_training_action_summary(action_result):
    action_result = action_result if isinstance(action_result, dict) else {}
    title = str(action_result.get("title") or "野外历练").strip()
    cultivation_delta = _parse_int(action_result.get("cultivationDelta"), 0)
    rewards = {}
    loot = action_result.get("loot") if isinstance(action_result.get("loot"), list) else []
    for item in loot:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("itemId") or "").strip()
        amount = _parse_int(item.get("quantity", item.get("qty", item.get("count", 1))), 1)
        if name and amount > 0:
            rewards[name] = rewards.get(name, 0) + amount
    gains = {}
    for key, label in (("tianjiGain", "天机"), ("contributionGain", "贡献")):
        amount = _parse_int(action_result.get(key), 0)
        if amount:
            gains[label] = amount
    parts = []
    if cultivation_delta:
        parts.append(f"修为{cultivation_delta:+d}")
    for name, amount in sorted(gains.items()):
        if name == "修为" and cultivation_delta:
            continue
        if int(amount or 0):
            parts.append(f"{name}+{int(amount)}")
    for name, amount in sorted(rewards.items()):
        if int(amount or 0) > 0:
            parts.append(f"{name}x{int(amount)}")
    if action_result.get("fateProtected"):
        parts.append("改命脱险")
    message = str(action_result.get("message") or action_result.get("rawMessage") or "").strip()
    return title, "｜".join(parts) or message or "状态已更新", rewards, gains


def _record_cave_wild_training_state(identity_id, *, strategy, mode, wild, action_result, phase, now, action_dispatched=False):
    payload = {
        "phase": str(phase or ""),
        "strategy": str(strategy or ""),
        "mode": str(mode or ""),
        "wild": dict(wild or {}),
        "snapshot_current": bool(wild),
        "action_dispatched": bool(action_dispatched),
        "result": {
            key: action_result.get(key)
            for key in (
                "ok", "completed", "type", "outcome", "title", "message", "rawMessage",
                "cultivationDelta", "successRate", "fateProtected", "dailyCount", "dailyLimit",
            )
            if key in (action_result or {})
        },
    }
    return record_miniapp_state(
        identity_id,
        "wild_training",
        payload,
        source="cave_dwelling_journey",
        source_id=f"wild_training:{int(identity_id)}:{stable_payload_digest(payload)}",
        now=now,
        outputs=("module_snapshot", "daily_counter", "inventory_delta"),
        replaces_commands=(".野外历练",),
    )


async def run_cave_public_wild_training(identity_id, public_entry_url, strategy, *, now=None, operation_check=None):
    """Read the authoritative journey state and execute at most one wild-training action."""
    from .wild_training import WildTrainingMiniAppOperation, wild_training_http_route_is_ready

    identity_id = _identity_id(identity_id)
    now = float(now or time.time())
    started_at = time.monotonic()
    strategy = str(strategy or "").strip()
    mode = _WILD_TRAINING_MODE_MAP.get(strategy, "")
    operation = WildTrainingMiniAppOperation.capture(identity_id)
    if identity_id <= 0 or operation is None:
        return {"ok": False, "message": "身份不存在", "extra": {}}
    if not mode:
        return {"ok": False, "message": "野外历练策略无效", "extra": {}}
    if not is_cave_public_identity_available(identity_id):
        return {"ok": False, "message": "身份已停用", "extra": {}}
    with use_identity(identity_id):
        if not state.get("wild_training_enabled"):
            return {"ok": False, "message": "野外历练模块已关闭", "extra": {}}
    if not _public_entry_allowed():
        return {"ok": False, "message": "全局暂停来源不允许洞府公共入口 MiniApp HTTP", "extra": {}}
    token, webview_url, error = _parse_public_cave_entry_url(public_entry_url)
    if error:
        return {"ok": False, "message": error, "extra": {}}
    observation = _CAVE_PUBLIC_ENTRY_OBSERVATION.get()
    record_before = deepcopy(get_miniapp_state_records().get(f"{identity_id}:wild_training") or {})
    cancelled = {"ok": False, "message": "洞府野外操作已取消或身份已变更", "extra": {"phase": "cancelled", "acted": False}}

    def record_is_current():
        return record_before == (get_miniapp_state_records().get(f"{identity_id}:wild_training") or {})

    def can_continue():
        return (
            operation.is_current() and record_is_current()
            and (operation_check is None or operation_check() is True)
            and (observation is None or observation.permits(identity_id, token))
        )

    def completed_now():
        return now + max(0.0, time.monotonic() - started_at)

    lock = _public_entry_lock(identity_id)
    if lock.locked():
        return {"ok": False, "message": "洞府公共入口操作执行中", "extra": {}}
    async with lock:
        if not can_continue():
            return cancelled
        try:
            session = await _load_cave_public_identity_session(
                identity_id,
                token,
                webview_url,
                now=now,
                capture_source=f"cave_public_wild_training_start:{identity_id}",
                include_details=True,
                operation_check=can_continue,
            )
        except MiniAppFlowCancelled:
            raise MiniAppFlowCancelled(cancelled) from None
        if not can_continue():
            return cancelled
        if not session.get("ok"):
            return {
                "ok": False,
                "message": f"洞府野外历练身份读取失败：{session.get('error') or 'unknown'}",
                "extra": _miniapp_result_extra({"phase": "session_failed", "acted": False}, session),
            }
        session_data = dict((session.get("result") or {}).get("data") or {})
        before_raw = session_data.get("raw") or {}
        player_error = cave_action_player_error({"account": {"playerId": session.get("player_id")}}, identity_id)
        player_error = player_error or cave_action_player_error(before_raw, identity_id)
        if player_error:
            return {"ok": False, "message": f"洞府身份校验失败：{player_error}", "extra": {"phase": "player_mismatch", "acted": False}}
        before_overview = parse_cave_dwelling_overview(before_raw)
        before_journey = before_overview.get("journey") if isinstance(before_overview.get("journey"), dict) else {}
        before_wild = before_journey.get("wild_experience") if isinstance(before_journey.get("wild_experience"), dict) else {}
        observed_at = completed_now()
        server_next_time = _wild_training_server_next_time(before_wild, now=observed_at)
        available = bool(before_wild.get("available"))
        daily_remaining = _parse_int(before_wild.get("daily_remaining"), 0)
        daily_count = _parse_int(before_wild.get("daily_count"), -1)
        daily_limit = _parse_int(before_wild.get("daily_limit"), 0)
        if not before_wild or daily_count < 0 or daily_remaining < 0 or daily_limit <= 0 or daily_count + daily_remaining > daily_limit:
            return {"ok": False, "message": "洞府游历页未返回野外历练状态", "extra": {"phase": "state_missing"}}
        if not available or daily_remaining <= 0 or server_next_time > observed_at:
            next_time = server_next_time or (observed_at + 30 * 60)
            _record_cave_wild_training_state(
                identity_id,
                strategy=strategy,
                mode=mode,
                wild=before_wild,
                action_result={},
                phase="cooldown",
                now=observed_at,
            )
            return {
                "ok": True,
                "message": "MiniApp 野外历练尚未到期",
                "extra": {
                    "acted": False,
                    "phase": "cooldown",
                    "wild": before_wild,
                    "next_time": next_time,
                    "strategy": strategy,
                    "mode": mode,
                },
            }

        tianxing_basis = {}

        def can_dispatch():
            if not can_continue():
                return False
            with use_identity(identity_id):
                if not wild_training_http_route_is_ready(strategy, completed_now()):
                    return False
                tianxing_basis.update({
                    key: deepcopy(state.get(key))
                    for key in ("tianxing_observation", "tianxing_timeline_state")
                })
            return True

        if not can_dispatch():
            return {"ok": False, "message": "天星探索前置已变化，本轮未出手", "extra": {"phase": "route_changed", "acted": False}}
        cancelled_flow = None
        try:
            result = await run_cave_journey_action_production_flow(
                identity_id,
                token=token,
                webview_url=webview_url,
                action="wild_experience",
                mode=mode,
                player_id=session.get("player_id"),
                init_data=session.get("init_data") or "",
                capture_sink=_capture_store(now),
                capture_source=f"cave_public_wild_training:{identity_id}",
                operation_check=can_dispatch,
            )
        except MiniAppFlowCancelled as exc:
            cancelled_flow = exc
            result = exc.result if isinstance(exc.result, dict) else {}
        dispatched = result.get("action_dispatched") is True
        if not operation.owner.is_current() or (not dispatched and not can_continue()):
            if cancelled_flow is not None:
                raise MiniAppFlowCancelled() from None
            return cancelled
        if not dispatched:
            response = {
                "ok": False, "message": result.get("error") or "MiniApp 野外未出手",
                "extra": _miniapp_result_extra({
                    "phase": "cancelled" if result.get("status") == "cancelled" else "action_not_sent", "acted": False,
                }, result),
            }
            if cancelled_flow is not None:
                raise MiniAppFlowCancelled(response) from None
            return response
        observed_at = completed_now()
        tianxing_current = all(operation.owner.identity.get(key) == value for key, value in tianxing_basis.items())
        raw = result.get("data") if isinstance(result.get("data"), dict) else {}
        if isinstance(raw.get("data"), dict):
            raw = raw["data"]
        account = raw.get("account") if isinstance(raw.get("account"), dict) else {}
        http_rejected = not result.get("ok") and any(
            event.get("status_code") in {400, 401, 403, 404, 409, 422, 429}
            for event in result.get("events") or [] if isinstance(event, dict)
        )
        action_player_error = cave_action_player_error(raw, identity_id)
        if result.get("status") == "identity_unverified":
            action_player_error = str(result.get("error") or action_player_error)
        if action_player_error:
            message = (result.get("error") or "MiniApp 野外请求被拒绝") if http_rejected else f"洞府身份校验失败：{action_player_error}"
            response = {
                "ok": False, "message": message,
                "extra": _miniapp_result_extra({
                    "phase": "blocked" if http_rejected else "action_unknown", "acted": True,
                    "completed": False, "outcome_unknown": not http_rejected,
                    "tianxing_result_current": tianxing_current,
                }, result),
            }
            if cancelled_flow is not None:
                raise MiniAppFlowCancelled(response) from None
            return response
        after_journey = _parse_cave_journey_overview(account.get("journey"))
        after_wild = after_journey.get("wild_experience") if isinstance(after_journey.get("wild_experience"), dict) else {}
        action_result = raw.get("actionResult") if isinstance(raw.get("actionResult"), dict) else {}
        action_error = str(action_result.get("error") or "").strip()
        completed = (
            bool(result.get("ok"))
            and not action_player_error
            and action_result.get("ok") is True
            and action_result.get("completed", True) is True
        )
        server_next_time = _wild_training_server_next_time(after_wild, now=observed_at)
        next_time = _wild_training_post_action_next_time(after_wild, action_result, now=observed_at)
        title, summary, rewards, gains = _wild_training_action_summary(action_result)
        rejected = action_result.get("ok") is False or action_result.get("completed") is False or http_rejected
        phase = "completed" if completed else ("blocked" if rejected else "action_unknown")
        snapshot_current = record_is_current()
        if snapshot_current:
            _record_cave_wild_training_state(
                identity_id, strategy=strategy, mode=mode, wild=after_wild, action_result=action_result,
                phase=phase, now=observed_at, action_dispatched=True,
            )
        if completed and rewards:
            record_inventory_delta(
                identity_id,
                source="wild_training_miniapp",
                source_id=f"wild_training:{identity_id}:{int(now * 1000)}:{stable_payload_digest(action_result)}",
                items=rewards,
                now=observed_at,
                source_summary={"strategy": strategy, "title": title, "gains": gains},
            )
        response = {
            "ok": completed,
            "message": f"{title}｜{summary}" if completed else (action_player_error or action_error or result.get("error") or summary or "野外历练未完成"),
            "extra": _miniapp_result_extra({
                "acted": True,
                "completed": completed,
                "phase": phase,
                "wild": after_wild,
                "before_wild": before_wild,
                "action_result": action_result,
                "next_time": next_time,
                "strategy": strategy,
                "mode": mode,
                "transport_ok": bool(result.get("ok")),
                "server_cooldown": server_next_time > observed_at,
                "outcome_unknown": phase == "action_unknown",
                "tianxing_result_current": tianxing_current,
                "schedule_current": snapshot_current,
            }, result),
        }
        if cancelled_flow is not None:
            raise MiniAppFlowCancelled(response) from None
        return response


async def run_cave_public_small_world_sync(identity_id, public_entry_url, *, now=None, harvest_only=False):
    identity_id = _identity_id(identity_id)
    now = float(now or time.time())
    started_at = time.monotonic()
    operation = _CaveSmallWorldOperation.capture(identity_id)
    if identity_id <= 0 or operation is None:
        return {"ok": False, "message": "身份不存在", "extra": {}}
    if not is_cave_public_identity_available(identity_id):
        return {"ok": False, "message": "身份已停用", "extra": {}}
    if not _public_entry_allowed():
        return {"ok": False, "message": "全局暂停来源不允许洞府公共入口 MiniApp HTTP", "extra": {}}
    token, webview_url, error = _parse_public_cave_entry_url(public_entry_url)
    if error:
        return {"ok": False, "message": error, "extra": {}}
    observation = _CAVE_PUBLIC_ENTRY_OBSERVATION.get()
    cancelled = {"ok": False, "message": "洞府小世界操作已取消或身份已变更", "extra": {"status": "cancelled"}}

    def can_continue():
        return (
            operation.is_current()
            and _public_entry_allowed()
            and (observation is None or observation.permits(identity_id, token))
        )

    lock = _public_entry_lock(identity_id)
    if lock.locked():
        return {"ok": False, "message": "洞府公共入口操作执行中", "extra": {"status": "busy"}}
    if not can_continue():
        return cancelled
    with use_identity(identity_id):
        if str(state.get("small_world_phase") or "idle") not in {"idle", "refresh_wait", "calibration_wait"}:
            return {"ok": False, "message": "小世界命令链路仍在等待回包", "extra": {"status": "busy"}}
        next_time = float(state.get("next_small_world_time", 0) or 0)
        existing_next_time = next_time
        next_harvest_at = float(state.get("small_world_next_public_harvest_at", 0) or 0)
        harvest_due = _cave_small_world_harvest_due(now)
        if harvest_only and not state.get("small_world_harvest_enabled"):
            return {"ok": True, "message": "自动收割香火未开启，已跳过请求", "extra": {"skipped": True}}
        if harvest_only and not harvest_due:
            return {
                "ok": True,
                "message": "MiniApp 收割尚未到 8 小时周期，已跳过请求",
                "extra": {"skipped": True, "next_time": next_harvest_at},
            }
        if not harvest_only and next_time > now and not harvest_due:
            effective_next_time = min(
                item
                for item in (next_time, next_harvest_at if state.get("small_world_harvest_enabled") else 0)
                if item > 0
            )
            return {
                "ok": True,
                "message": "洞府小世界尚未到检查时间，已跳过请求",
                "extra": {"skipped": True, "next_time": effective_next_time},
            }
        last_request_at = float(state.get("small_world_last_public_request_at", 0) or 0)
        if last_request_at > 0 and now < last_request_at + CAVE_SMALL_WORLD_MIN_REQUEST_SEC:
            next_time = last_request_at + CAVE_SMALL_WORLD_MIN_REQUEST_SEC
            state["next_small_world_time"] = max(float(state.get("next_small_world_time", 0) or 0), next_time)
            if harvest_due:
                state["small_world_next_public_harvest_at"] = max(
                    float(state.get("small_world_next_public_harvest_at", 0) or 0),
                    next_time,
                )
            save_state()
            return {
                "ok": True,
                "message": "洞府小世界请求仍在最小间隔内，已跳过请求",
                "extra": {"skipped": True, "next_time": next_time},
            }
    async with lock:
        if not can_continue():
            return cancelled
        with use_identity(identity_id):
            state["small_world_last_public_request_at"] = float(now)
            save_state()
        session = await _load_cave_public_identity_session(
            identity_id,
            token,
            webview_url,
            now=now,
            capture_source=f"cave_public_small_world_start:{identity_id}",
            include_details=True,
            operation_check=can_continue,
        )
        if not can_continue() or session.get("status") == "cancelled":
            return cancelled
        if not session.get("ok"):
            message = f"洞府小世界身份读取失败：{session.get('error') or 'unknown'}"
            return {"ok": False, "message": message, "extra": _miniapp_result_extra({}, session)}
        with use_identity(identity_id):
            session_data = dict((session.get("result") or {}).get("data") or {})
            cancelled_flow = None
            try:
                result = await run_cave_small_world_production_flow(
                    identity_id,
                    token=token,
                    webview_url=webview_url,
                    init_data=session.get("init_data") or "",
                    player_id=session.get("player_id"),
                    action_planner=(
                        (lambda overview: _plan_cave_public_small_world_harvest(overview, now=now))
                        if harvest_only
                        else (lambda overview: _plan_cave_public_small_world_action(overview, now=now))
                    ),
                    capture_sink=_capture_store(now),
                    capture_source=f"cave_public_small_world:{identity_id}",
                    initial_snapshot=session_data.get("raw") or {},
                    operation_check=can_continue,
                )
            except MiniAppFlowCancelled as exc:
                cancelled_flow = exc
                result = exc.result if isinstance(exc.result, dict) else {}
            now += max(0, int(time.monotonic() - started_at))
            data = dict(result.get("data") or {})
            confirmed = data.get("action_confirmed") is True
            dispatched = data.get("action_dispatched") is True
            update_schedule = can_continue() and cancelled_flow is None and result.get("status") != "cancelled"
            if not operation.owner.is_current() or (not update_schedule and not (confirmed or dispatched)):
                if cancelled_flow is not None:
                    raise MiniAppFlowCancelled() from None
                return cancelled
            overview = data.get("overview") if isinstance(data.get("overview"), dict) else {}
            small_world = overview.get("small_world") if isinstance(overview.get("small_world"), dict) else {}
            plan = data.get("plan") if isinstance(data.get("plan"), dict) else {}
            action = str(data.get("action") or plan.get("action") or "")
            snapshot_current = data.get("snapshot_current") is True
            panel_owned = operation.panel_is_current(now)
            update_snapshot = bool(
                small_world.get("available") and small_world.get("has_world")
                and snapshot_current and panel_owned
            )
            harvest_clock_owned = operation.matches("small_world_last_public_harvest_at", "small_world_next_public_harvest_at")
            snapshot = _apply_cave_small_world_overview(small_world, now) if update_snapshot else {}
            if not snapshot_current and (confirmed or dispatched) and panel_owned:
                # Keep the last resource counts, but do not reuse pre-mutation decisions.
                cached = dict(state.get("small_world_panel_snapshot") or {})
                if cached:
                    cached["updated_at"] = 0
                    state["small_world_panel_snapshot"] = cached
                state["small_world_last_panel_at"] = 0
            record = _record_cave_small_world_state(
                identity_id, result, now=now, update_snapshot=update_snapshot,
            ) if operation.record_is_current() and float(operation.record.get("updated_at") or 0) <= now else {}
            action_message = _cave_small_world_action_message(result)
            resource_blocked = not confirmed and (plan.get("blocked") == "resource" or ("不足" in action_message and action == "manifest"))
            action_label = {
                "manifest": "显灵", "miracle_sermon": "布道", "miracle_relief": "赈灾",
                "collect": "收割香火", "refine_shenshi": "神识淬炼",
            }.get(action, action)
            if confirmed and action == "collect":
                state["small_world_last_public_harvest_at"] = max(float(state.get("small_world_last_public_harvest_at") or 0), now)
                if harvest_clock_owned:
                    state["small_world_next_public_harvest_at"] = now + CAVE_SMALL_WORLD_HARVEST_INTERVAL_SEC
            if confirmed and action in {"miracle_sermon", "miracle_relief"} and operation.matches("small_world_god_cooldown_until"):
                state["small_world_god_cooldown_until"] = max(
                    float(state.get("small_world_god_cooldown_until") or 0), now + CAVE_SMALL_WORLD_GOD_COOLDOWN_SEC,
                )
            if not update_schedule:
                save_state()
                response = {
                    "ok": confirmed,
                    "message": (
                        f"洞府小世界已{action_label}：{action_message or '动作已确认'}；保留新的调度设置"
                        if confirmed else f"洞府小世界{action_label}结果未确认；保留新的调度设置"
                    ),
                    "extra": _miniapp_result_extra({
                        "record_key": record.get("record_key", ""), "action": action, "action_confirmed": confirmed,
                        "snapshot": snapshot, "operation_cancelled": True, "status": "acted" if confirmed else "cancelled",
                    }, result),
                }
                if cancelled_flow is not None:
                    raise MiniAppFlowCancelled(response) from None
                return response
            if action and not confirmed:
                result = {**result, "ok": False, "error": result.get("error") or "small_world_action_unconfirmed"}
            retry_after_sec = miniapp_retry_after_sec(result)
            wait_sec, _wait_text = _parse_wait_from_text(action_message)
            harvest_was_due = bool(plan.get("harvest_due")) or _cave_small_world_harvest_due(now)
            harvest_checked = bool(plan.get("harvest_checked") and snapshot_current)
            if not (action == "collect" and confirmed):
                if harvest_checked:
                    state["small_world_next_public_harvest_at"] = now + CAVE_SMALL_WORLD_HARVEST_INTERVAL_SEC
                elif harvest_was_due and (harvest_only or not result.get("ok") or not snapshot_current):
                    state["small_world_next_public_harvest_at"] = now + max(CAVE_SMALL_WORLD_HARVEST_RETRY_SEC, retry_after_sec)
            if not result.get("ok") and not confirmed:
                state["small_world_refresh_count"] = 0
                state["small_world_phase"] = "idle"
                state["next_small_world_time"] = now + max(CAVE_SMALL_WORLD_CYCLE_SEC, retry_after_sec, wait_sec + CD_BUFFER_SEC)
                if resource_blocked:
                    state["small_world_last_error"] = f"洞府显灵资源不足：{plan.get('reason') or action_message or '资源不足'}"
                    message = f"洞府小世界显灵资源不足，已退避 6 小时：{plan.get('reason') or action_message or '资源不足'}"
                else:
                    state["small_world_last_error"] = f"洞府小世界处理失败：{result.get('error') or result.get('status') or 'unknown'}"
                    message = f"洞府小世界处理失败，已退避 6 小时：{result.get('error') or result.get('status') or 'unknown'}"
            elif not snapshot_current and not confirmed:
                state["small_world_refresh_count"] = 0
                state["small_world_phase"] = "idle"
                state["next_small_world_time"] = now + CAVE_SMALL_WORLD_CYCLE_SEC
                state["small_world_last_error"] = "洞府小世界回包未包含完整面板"
                message = "洞府小世界面板不完整，保留上次数据，6 小时后再查"
            elif resource_blocked:
                state["small_world_refresh_count"] = 0
                state["small_world_phase"] = "idle"
                state["next_small_world_time"] = now + CAVE_SMALL_WORLD_RESOURCE_PAUSE_SEC
                state["small_world_last_error"] = f"洞府显灵资源不足：{plan.get('reason') or '资源不足'}"
                message = f"洞府小世界显灵资源不足，已退避 6 小时：{plan.get('reason') or '资源不足'}"
            elif confirmed:
                state["small_world_refresh_count"] = 0
                state["small_world_phase"] = "idle"
                state["next_small_world_time"] = _cave_small_world_next_check_at(small_world if snapshot_current else {}, now)
                if wait_sec > 0 and action == "manifest":
                    state["next_small_world_time"] = max(
                        _cave_small_world_prayer_due_at(small_world, now) if snapshot_current else 0,
                        now + wait_sec + CD_BUFFER_SEC,
                    )
                state["small_world_last_error"] = ""
                message = f"洞府小世界已{action_label}：{action_message or plan.get('reason') or '处理完成'}"
            else:
                can_refresh = bool(
                    not harvest_only
                    and
                    not small_world.get("has_prayer")
                    and state.get("small_world_manifest_enabled")
                    and state.get("small_world_refresh_enabled")
                    and not plan.get("suppress_refresh")
                )
                if can_refresh:
                    refresh_count = int(state.get("small_world_refresh_count", 0) or 0) + 1
                    if refresh_count >= CAVE_SMALL_WORLD_MAX_REFRESH_ATTEMPTS:
                        state["small_world_refresh_count"] = 0
                        state["small_world_phase"] = "idle"
                        state["next_small_world_time"] = now + CAVE_SMALL_WORLD_CYCLE_SEC
                        state["small_world_last_error"] = "洞府祈愿刷新 5 次未出现，已退避 6 小时"
                        refresh_note = "刷新 5 次未出现，已退避 6 小时"
                    else:
                        state["small_world_refresh_count"] = refresh_count
                        state["small_world_phase"] = "refresh_wait"
                        state["next_small_world_time"] = now + CAVE_SMALL_WORLD_REFRESH_SEC
                        state["small_world_last_error"] = ""
                        refresh_note = f"10 分钟后刷新 {refresh_count + 1}/{CAVE_SMALL_WORLD_MAX_REFRESH_ATTEMPTS}"
                else:
                    state["small_world_refresh_count"] = 0
                    state["small_world_phase"] = "idle"
                    state["next_small_world_time"] = _cave_small_world_next_check_at(small_world, now)
                    state["small_world_last_error"] = str(plan.get("reason") or "")
                    refresh_note = plan.get("reason") or "无需动作，6 小时后再查"
                faith = small_world.get("faith", 0)
                stability = small_world.get("stability", 0)
                prayer = small_world.get("prayer_title") or "无祈愿"
                message = f"洞府小世界已检查：信仰 {faith}｜稳定 {stability}｜{prayer}｜{refresh_note}"
            if harvest_only:
                state["next_small_world_time"] = _preserve_small_world_timer_after_harvest(
                    existing_next_time,
                    small_world,
                    now,
                )
            save_state()
        response = {
            "ok": bool(result.get("ok")) or confirmed or resource_blocked,
            "message": message,
            "extra": _miniapp_result_extra({
                "record_key": record.get("record_key", ""),
                "action": action,
                "action_confirmed": confirmed,
                "snapshot": snapshot,
            }, result),
        }
        try:
            await send_audit_log(
                f"🌏 {message}", scope="identity", send_as_id=identity_id,
                priority="high" if resource_blocked else ("normal" if action or not result.get("ok") else "low"),
                limit=300,
            )
        except asyncio.CancelledError:
            raise MiniAppFlowCancelled(response if operation.owner.is_current() else None) from None
        except Exception as exc:
            logging.getLogger(__name__).warning("Small-world notification failed (%s); result preserved", type(exc).__name__)
        return response if operation.owner.is_current() else cancelled


def _cave_treasure_cancelled_response():
    return {"ok": False, "message": "洞府寻宝操作已取消或身份已变更", "extra": {"status": "cancelled"}}


def _cave_treasure_unknown_response():
    return {
        "ok": False, "message": "洞府寻宝存在结果未知动作，等待核实，不自动重试",
        "extra": {"status": "result_unknown", "outcome_unknown": True,
                  "daily_exhausted": False, "skipped": "outcome_unknown_hold"},
    }


async def _audit_cave_treasure(owner, response, *, priority="normal"):
    if not owner.is_current():
        return
    try:
        await send_audit_log(
            f"🕳️ {response['message']}", scope="identity", send_as_id=owner.identity_id,
            priority=priority, limit=260,
        )
    except asyncio.CancelledError:
        raise MiniAppFlowCancelled(response if owner.is_current() else None) from None
    except Exception as exc:
        logging.getLogger(__name__).warning("Treasure notification failed (%s); result retained", type(exc).__name__)


def _commit_cave_treasure_result(projection, result, *, now, result_msg_id=0, public=False,
                                operation_cancelled=False, operation_record=None):
    identity_id = projection.owner.identity_id
    try:
        inventory_record = _record_cave_treasure_inventory_delta(
            identity_id, result, now=now, result_msg_id=result_msg_id, prepare=True,
            **({"operation_id": operation_record["operation_id"]} if operation_record else {}),
        )
        state_record = _record_cave_treasure_miniapp_state(
            identity_id, result, now=now, result_msg_id=result_msg_id, prepare=True,
            **({"operation_record": operation_record} if operation_record else {}),
        )
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        game_state = data.get("state") if isinstance(data.get("state"), dict) else {}
        status = str(result.get("status") or "")
        unknown = bool(result.get("outcome_unknown") or game_state.get("outcome_unknown") or status == "result_unknown")
        settled_count = len(_cave_treasure_receipts(result))
        rewards, gains = _cave_treasure_materials(result)
        prefix = "洞府寻宝公共入口：" if public else "洞府寻宝结果｜"
        response = {
            "ok": bool(result.get("ok")) and not unknown,
            "message": prefix + _format_cave_treasure_summary(result),
            "extra": _miniapp_result_extra({
                "inventory_record_key": inventory_record.get("record_key", ""),
                "state_record_key": state_record.get("record_key", ""),
                "status": status, "outcome_unknown": unknown,
                "games_used": _parse_int(game_state.get("games_used"), 0),
                "games_limit": _parse_int(game_state.get("games_limit"), 0),
                "settled_count": settled_count, "gains": gains, "rewards": rewards,
                "daily_exhausted": bool(result.get("ok")) and status == "daily_limit" and not unknown and treasure_quota_exhausted(game_state),
                "operation_cancelled": operation_cancelled,
            }, result),
        }
        response = projection.apply(inventory_record, state_record, response, now=now, operation_record=operation_record)
    except Exception as exc:
        logging.getLogger(__name__).warning("Treasure projection failed (%s); blocking replay", type(exc).__name__)
        response = projection.invalidate()
    return response


async def _run_owned_cave_treasure(owner, token, webview_url, *, now, capture_source, operation_check,
                                   result_msg_id=0, public=False):
    identity_id = owner.identity_id
    recovered = _recover_owned_cave_treasure(identity_id)
    resume = treasure_operations.resume_allowed(identity_id)
    if recovered is not None and not resume:
        return recovered
    if not operation_check():
        return _cave_treasure_cancelled_response()
    if _cave_treasure_unknown_hold(identity_id, now) and not resume:
        return _cave_treasure_unknown_response()
    projection = treasure_results.ResultProjection.capture(owner, **({"resume": True} if resume else {}))
    session = await _load_cave_public_identity_session(
        identity_id, token, webview_url, now=now,
        capture_source=f"{capture_source}:start", operation_check=operation_check,
    )
    if not operation_check() or not projection.is_current():
        return _cave_treasure_cancelled_response()
    if not session.get("ok"):
        extra = treasure_operations.held_response(session.get("error") or "original_round_unverified")["extra"] if resume else {}
        response = {"ok": False, "message": f"洞府寻宝身份读取失败：{session.get('error') or 'unknown'}",
                    "extra": _miniapp_result_extra(extra, session)}
        await _audit_cave_treasure(owner, response)
        return response if owner.is_current() else _cave_treasure_cancelled_response()
    try:
        player_id = _require_cave_action_player_id(session.get("player_id"), identity_id=identity_id)
    except ValueError as exc:
        return {"ok": False, "message": f"洞府寻宝身份校验失败：{exc}", "extra": {"status": "blocked"}}
    reason = treasure_operations.hold_reason(identity_id)
    if reason and not (resume and treasure_operations.resume_allowed(identity_id)):
        return treasure_operations.held_response(reason)
    writer = treasure_operations.CheckpointWriter(projection, player_id=player_id, operation_check=operation_check,
                                                 **({"resume": True} if resume else {}))
    capture_sink = _capture_store(now)
    cancelled_flow = None
    try:
        result = await run_cave_treasure_miniapp_production_flow(
            identity_id, token=token, webview_url=webview_url, init_data=session.get("init_data") or "",
            player_id=player_id, max_steps=CAVE_TREASURE_MANUAL_MAX_STEPS,
            capture_sink=capture_sink, capture_source=capture_source,
            operation_check=lambda: operation_check() and writer.is_current() and not writer.cancelled,
            checkpoint=writer,
            **({"resume_record": writer.resume_record} if resume else {}),
        )
    except MiniAppFlowCancelled as exc:
        cancelled_flow, result = exc, exc.result
    except BaseException:
        writer.closed = True
        raise
    if not owner.is_current():
        writer.closed = True
        if cancelled_flow is not None:
            raise MiniAppFlowCancelled() from None
        return _cave_treasure_cancelled_response()
    if not isinstance(result, dict):
        result = {"ok": False, "status": "result_unknown", "error": "treasure_result_missing", "outcome_unknown": True}
    if resume and not writer.sequence:
        writer.closed = True
        response = treasure_operations.held_response(result.get("error") or "original_round_unverified")
        response["extra"] = _miniapp_result_extra(response["extra"], result)
        if cancelled_flow is not None or writer.cancelled:
            raise MiniAppFlowCancelled(response) from None
        return response
    try:
        _record_cave_treasure_business_capture(capture_sink, result, source=capture_source, now=now)
    except Exception as exc:
        logging.getLogger(__name__).warning("Treasure capture failed (%s); retaining local result", type(exc).__name__)
    if not writer.finish(result):
        response = (treasure_operations.held_response("checkpoint_persistence_pending") if writer.sequence
                    else projection.invalidate())
        if cancelled_flow is not None or writer.cancelled:
            raise MiniAppFlowCancelled(response) from None
        return response
    operation_record = writer.current if writer.sequence else None
    if operation_record:
        result = {**result, **treasure_operations.projected_result(operation_record)}
    response = _commit_cave_treasure_result(
        projection, result, now=operation_record["updated_at"] if operation_record else now,
        result_msg_id=result_msg_id, public=public, operation_record=operation_record,
        operation_cancelled=cancelled_flow is not None or writer.cancelled or not operation_check(),
    )
    if cancelled_flow is not None or writer.cancelled:
        raise MiniAppFlowCancelled(response) from None
    if response["extra"].get("persistence_only"):
        return response
    priority = "normal" if response["extra"].get("settled_count", 0) > 0 or not response["ok"] else "low"
    await _audit_cave_treasure(owner, response, priority=priority)
    if not owner.is_current():
        return _cave_treasure_cancelled_response()
    response["extra"]["operation_cancelled"] = not operation_check()
    return response


async def run_cave_public_treasure(identity_id, public_entry_url, *, now=None):
    identity_id = _identity_id(identity_id)
    now = float(now or time.time())
    owner = MiniAppIdentityOwner.capture(identity_id)
    if identity_id <= 0 or owner is None:
        return {"ok": False, "message": "身份不存在", "extra": {}}
    recovered = recover_cave_treasure_result(identity_id)
    if recovered is not None and not treasure_operations.resume_allowed(identity_id):
        return recovered
    if not is_cave_public_identity_available(identity_id):
        return {"ok": False, "message": "身份已停用", "extra": {}}
    identity_error = _public_entry_account_identity_error(identity_id)
    if identity_error:
        return {"ok": False, "message": identity_error, "extra": {}}
    if not _public_entry_allowed():
        return {"ok": False, "message": "全局暂停来源不允许洞府公共入口 MiniApp HTTP", "extra": {}}
    token, webview_url, error = _parse_public_cave_entry_url(public_entry_url)
    if error:
        return {"ok": False, "message": error, "extra": {}}
    observation = _CAVE_PUBLIC_ENTRY_OBSERVATION.get()

    def can_continue():
        return (
            owner.is_current() and is_cave_public_identity_available(identity_id) and _public_entry_allowed()
            and not _public_entry_account_identity_error(identity_id)
            and (observation is None or observation.permits(identity_id, token))
        )

    lock = _public_entry_lock(identity_id)
    game_lock = _run_lock(identity_id)
    if lock.locked() or game_lock.locked():
        return {"ok": False, "message": "洞府公共入口操作执行中", "extra": {"status": "busy"}}
    async with lock, game_lock:
        return await _run_owned_cave_treasure(
            owner, token, webview_url, now=now, capture_source=f"cave_public_treasure:{identity_id}",
            operation_check=can_continue, public=True,
        )


async def run_cave_public_trial(identity_id, public_entry_url, *, now=None):
    identity_id = _identity_id(identity_id)
    now = float(now or time.time())
    owner = MiniAppIdentityOwner.capture(identity_id)
    if identity_id <= 0 or owner is None:
        return {"ok": False, "message": "身份不存在", "extra": {}}
    if not is_cave_public_identity_available(identity_id):
        return {"ok": False, "message": "身份已停用", "extra": {}}
    if not _public_entry_allowed():
        return {"ok": False, "message": "全局暂停来源不允许洞府公共入口 MiniApp HTTP", "extra": {}}
    token, webview_url, error = _parse_public_cave_entry_url(public_entry_url)
    if error:
        return {"ok": False, "message": error, "extra": {}}
    observation = _CAVE_PUBLIC_ENTRY_OBSERVATION.get()

    def can_continue():
        return (
            owner.is_current() and is_cave_public_identity_available(identity_id)
            and _public_entry_allowed()
            and (observation is None or observation.permits(identity_id, token))
        )

    cancelled = {"ok": False, "message": "洞府天机试炼操作已取消或身份已变更", "extra": {"status": "cancelled"}}
    lock = _public_entry_lock(identity_id)
    game_lock = _trial_run_lock(identity_id)
    if lock.locked() or game_lock.locked():
        return {"ok": False, "message": "洞府公共入口操作执行中", "extra": {"status": "busy"}}
    async with lock, game_lock:
        if not can_continue():
            return cancelled
        recovered = trial_operations.recover_local(identity_id)
        if recovered is not None:
            return _trial_recovery_response(recovered)
        session = await _load_cave_public_identity_session(
            identity_id,
            token,
            webview_url,
            now=now,
            capture_source=f"cave_public_trial_start:{identity_id}",
            include_details=True,
            operation_check=can_continue,
        )
        if not can_continue():
            return cancelled
        if not session.get("ok"):
            message = f"洞府天机试炼入口读取失败：{session.get('error') or 'unknown'}"
            await send_audit_log(f"🧪 {message}", scope="identity", send_as_id=identity_id, priority="normal", limit=240)
            return {"ok": False, "message": message, "extra": _miniapp_result_extra({}, session)}
        dwelling_init_data = session.get("init_data") or ""
        selected_player_id = session.get("player_id")
        cave_result = dict(session.get("result") or {})
        cave_data = dict(cave_result.get("data") or {})
        raw = cave_data.get("raw") if isinstance(cave_data.get("raw"), dict) else {}
        if not cave_result.get("ok"):
            message = f"洞府天机试炼入口读取失败：{cave_result.get('error') or cave_result.get('status') or 'unknown'}"
            await send_audit_log(f"🧪 {message}", scope="identity", send_as_id=identity_id, priority="normal", limit=220)
            return {"ok": False, "message": message, "extra": _miniapp_result_extra({}, cave_result)}
        external_app = _find_trial_external_app_in_cave_payload(raw)
        if not external_app or not external_app.get("available"):
            message = "洞府天机试炼入口读取完成，但外府试炼入口不可用"
            await send_audit_log(f"🧪 {message}", scope="identity", send_as_id=identity_id, priority="normal", limit=220)
            return {"ok": False, "message": message, "extra": {}}
        launch = {}
        if external_app.get("action"):
            external_result = await run_cave_external_action_production_flow(
                identity_id,
                token=token,
                webview_url=webview_url,
                action=external_app["action"],
                player_id=selected_player_id,
                init_data=dwelling_init_data,
                capture_sink=_capture_store(now),
                capture_source=f"cave_public_trial_external:{identity_id}",
                operation_check=can_continue,
            )
            if not can_continue():
                return cancelled
            if not external_result.get("ok"):
                message = f"洞府天机试炼动态入口获取失败：{external_result.get('error') or external_result.get('status') or 'unknown'}"
                await send_audit_log(f"🧪 {message}", scope="identity", send_as_id=identity_id, priority="normal", limit=240)
                return {"ok": False, "message": message, "extra": _miniapp_result_extra({}, external_result)}
            launch = _find_trial_launch_in_cave_payload(external_result.get("data") or {})
        elif external_app.get("url"):
            launch = _find_trial_launch_in_cave_payload(external_app)
        if not launch:
            message = "洞府天机试炼入口已请求，但未返回可用试炼 URL"
            await send_audit_log(f"🧪 {message}", scope="identity", send_as_id=identity_id, priority="normal", limit=220)
            return {"ok": False, "message": message, "extra": {}}
        capture_sink = _trial_miniapp_capture_store(now)
        capture_source = f"cave_public_trial:{identity_id}"
        cancelled_flow = None
        writer = trial_operations.CheckpointWriter(owner, player_id=selected_player_id, operation_check=can_continue)
        try:
            result = await run_trial_miniapp_production_flow(
                identity_id,
                token=launch.get("token"),
                webview_url=launch.get("webview_url"),
                init_data=dwelling_init_data,
                player_id=selected_player_id,
                max_rounds=99,
                capture_sink=capture_sink,
                capture_source=capture_source,
                operation_check=lambda: can_continue() and writer.is_current(),
                checkpoint=writer,
            )
        except MiniAppFlowCancelled as exc:
            cancelled_flow = exc
            result = exc.result if isinstance(exc.result, dict) else {}
        result = dict(result or {})
        if not owner.is_current():
            if cancelled_flow is not None:
                raise MiniAppFlowCancelled() from None
            return cancelled
        result = trial_operations.finish_result(writer, result)
        if _trial_has_settlements(result):
            _record_trial_business_capture(capture_sink, result, source=capture_source, now=now)
        summary = _format_trial_summary(result)
        message = f"洞府天机试炼公共入口：{summary}"
        completed_ok = _trial_result_completed_ok(result)
        rewards, gains = _trial_batch_materials(result)
        settled_count = _parse_int(
            result.get("settled_count") or (result.get("data") or {}).get("settled_count"),
            0,
        )
        response = {
            "ok": completed_ok,
            "message": message,
            "extra": _miniapp_result_extra({
                "trial_title": launch.get("title", ""),
                "status": result.get("status") or "unknown",
                "error": result.get("error") or "",
                "events": list(result.get("events") or ()),
                "settled_count": settled_count,
                "gains": gains,
                "rewards": rewards,
                "operation_cancelled": cancelled_flow is not None or not can_continue(),
            }, result),
        }
        if cancelled_flow is not None:
            raise MiniAppFlowCancelled(response) from None
        try:
            await send_audit_log(f"🧪 {message}", scope="identity", send_as_id=identity_id, priority="low" if completed_ok else "normal", limit=260)
        except asyncio.CancelledError:
            raise MiniAppFlowCancelled(response if owner.is_current() else None) from None
        except Exception as exc:
            logging.getLogger(__name__).warning("Trial public result report failed (%s); settlement retained", type(exc).__name__)
        if not owner.is_current():
            return cancelled
        response["extra"]["operation_cancelled"] = not can_continue()
        return response


class _CaveFateCardsOperation:
    """Own one fate chain; completion ownership is independent of admission."""

    def __init__(self, owner, *, now, operation_check):
        self.owner = owner
        self.now = now
        self.operation_check = operation_check
        self.observation = _CAVE_PUBLIC_ENTRY_OBSERVATION.get()
        self.token = ""
        self.controls = self.control_values()
        self.deep = _CaveDeepRetreatOperation.capture(owner.identity_id)
        self.record_key = f"{owner.identity_id}:fate_cards"
        self.base_record = deepcopy(get_miniapp_state_records().get(self.record_key) or {})
        self.record = deepcopy(self.base_record)
        previous = self.base_record.get("state") or {}
        self.pending = deepcopy(previous.get("pending") or {})
        self.unconfirmed_prerequisite = deepcopy(previous.get("unconfirmed_prerequisite") or {})
        if not self.pending and str(previous.get("status") or "").endswith("_unknown"):
            self.pending = {"action": "legacy", "challenge_date": previous.get("challenge_date")}
        self.receipts = deepcopy(previous.get("receipts") or {})
        self.last_trace_balance = previous.get("trace_balance") if type(previous.get("trace_balance")) is int else None
        self.fate_state = {}
        self.reward = {}
        self.meditation = {}
        self.deep_summary = {}
        self.budget = MiniAppRequestBudget({"max_requests_per_run": 16, "max_attempts_per_request": 1})

    @staticmethod
    def control_values():
        config = get_miniapp_auto_config() or {}
        return tuple(config.get(key) for key in (
            "cave_public_fate_cards_enabled", "cave_public_fate_cards_choice_key",
            "cave_public_deep_status_enabled",
        ))

    def owns_result(self):
        return self.owner.is_current() and self.record == (get_miniapp_state_records().get(self.record_key) or {})

    def can_dispatch(self):
        try:
            return (
                self.owns_result() and self.deep is not None and self.deep.can_dispatch()
                and self.controls == self.control_values()
                and (self.operation_check is None or self.operation_check() is True)
                and (self.observation is None or self.observation.permits(self.owner.identity_id, self.token))
            )
        except MiniAppRequestAborted:
            return False

    async def read(self, function, *args, **kwargs):
        require_miniapp_operation(self.can_dispatch)
        result = await function(*args, **kwargs, operation_check=self.can_dispatch)
        require_miniapp_operation(self.can_dispatch)
        return result

    def record_state(self, identity_id, fate_state, *, now, status, **kwargs):
        if identity_id != self.owner.identity_id or not self.owns_result():
            raise MiniAppRequestAborted("fate_owner_changed")
        self.fate_state = dict(fate_state or self.fate_state)
        if self.fate_state.get("trace_balance_known"):
            self.last_trace_balance = self.fate_state.get("trace_balance")
        else:
            self.fate_state["trace_balance"] = self.last_trace_balance
        if str(status).endswith("_unknown") and not self.pending:
            status = str(status).removesuffix("_unknown") + "_unconfirmed"
        result = _record_fate_cards_state(
            identity_id, self.fate_state, now=now, status=status,
            reward=self.reward, meditation=self.meditation,
            deep_retreat=kwargs.get("deep_retreat") or self.deep_summary,
            base_record=self.base_record, pending=self.pending,
            receipts=self.receipts, owner_account_id=self.owner.account_id,
            unconfirmed_prerequisite=self.unconfirmed_prerequisite,
        )
        self.record = deepcopy(get_miniapp_state_records().get(self.record_key) or {})
        return result

    def add_reward(self, action, fate_state, action_result, *, expected=""):
        data = action_result.get("data") or {}
        direct = _fate_cards_state_from_result(action_result)
        if (action_result.get("ok") is not True
                or not _fate_cards_action_confirmed(action, direct, expected=expected)
                or _fate_cards_transition_error(direct, fate_state)):
            return
        raw = data.get("raw") or {}
        if raw.get({"draw": "alreadyDrawn", "settle": "alreadySettled"}.get(action, "")) is True:
            return
        receipt = stable_payload_digest({
            "day": fate_state.get("challenge_date"), "record": fate_state.get("record_key"),
            "action": action,
        })
        if self.receipts.get(action) == receipt:
            return
        self.receipts[action] = receipt
        for name, amount in (data.get("reward") or {}).items():
            if type(amount) is int and amount > 0:
                self.reward[str(name)] = self.reward.get(str(name), 0) + amount

    def accept_state(self, fate_state):
        previous = self.base_record.get("state") or {}
        prior_snapshot = previous.get("snapshot") or {}
        basis = self.fate_state
        if not basis and prior_snapshot.get("state_verified") is True and prior_snapshot.get("challenge_date") == fate_state.get("challenge_date"):
            basis = prior_snapshot
        error = _fate_cards_transition_error(basis, fate_state)
        if not error and previous.get("challenge_date", "") > fate_state.get("challenge_date", ""):
            error = "fate_day_regressed"
        if error:
            raise MiniAppRequestAborted(error)
        if previous.get("owner_account_id") not in (None, self.owner.account_id):
            raise MiniAppRequestAborted("fate_record_owner_unverified")
        if self.pending:
            action = self.pending.get("action")
            basis = self.pending.get("before") or {}
            if not self.pending.get("conflict") and basis.get("state_verified") is True and action in {"draw", "interpret", "choose", "settle"} and _fate_cards_action_confirmed(
                action, fate_state, previous=basis, expected=self.pending.get("expected", ""),
            ):
                self.pending = {}
            elif (not self.pending.get("conflict") and previous.get("owner_account_id") == self.owner.account_id
                  and _fate_cards_prerequisite_superseded(self.pending, fate_state)):
                # The same quest no longer needs this prerequisite. This does
                # not confirm the missing action receipt or manufacture gains.
                self.unconfirmed_prerequisite = {
                    "action": action, "challenge_date": basis.get("challenge_date"),
                    "record_key": basis.get("record_key"), "outcome_unknown": True,
                    "reason": "quest_no_longer_needs_prerequisite",
                }
                self.pending = {}
            else:
                raise MiniAppRequestAborted("fate_previous_outcome_unknown")
        if not self.fate_state and previous.get("challenge_date") != fate_state.get("challenge_date"):
            self.receipts = {}
        self.fate_state = deepcopy(fate_state)
        if fate_state.get("trace_balance_known"):
            self.last_trace_balance = fate_state.get("trace_balance")

    async def fate_action(self, identity_id, action, **kwargs):
        require_miniapp_operation(self.can_dispatch)
        before = deepcopy(self.fate_state)
        if before.get("state_verified") is not True or self.pending:
            raise MiniAppRequestAborted("fate_action_unverified")
        expected = kwargs.get("expected") or (kwargs.get("payload") or {}).get("questionKey", "")
        kwargs["expected"] = expected
        cancelled = None
        try:
            result = await _run_fate_cards_action_and_reconcile(
                identity_id, action, **kwargs, previous=before,
                operation_check=self.can_dispatch, request_budget=self.budget,
            )
        except MiniAppFlowCancelled as exc:
            cancelled = exc
            result = exc.result if isinstance(exc.result, dict) else {"outcome_unknown": True}
        if self.owns_result():
            observed = result.get("state") or {}
            confirmed = bool(result.get("ok") and _fate_cards_action_confirmed(
                action, observed, previous=before, expected=expected,
            ))
            if confirmed:
                self.fate_state = observed
                self.pending = {}
                self.add_reward(action, observed, result.get("action_result") or {}, expected=expected)
            elif result.get("outcome_unknown") or "outcome_unknown" not in result and result.get("action_dispatched") is not False:
                self.pending = {"action": action, "before": before, "expected": expected}
                if result.get("conflict"):
                    self.pending["conflict"] = result["conflict"]
            status = "settled" if confirmed and action == "settle" else action if confirmed else f"{action}_unknown" if self.pending else f"{action}_rejected"
            self.record_state(identity_id, self.fate_state, now=self.now, status=status)
            result = {**result, "ok": confirmed}
        else:
            result = {"ok": False, "status": "cancelled", "state": {}}
        if cancelled is not None:
            raise MiniAppFlowCancelled(result) from None
        if not self.owns_result() or result.get("ok") and action != "settle" and result.get("can_continue") is not True:
            raise MiniAppRequestAborted("fate_chain_stopped_after_completion")
        return result

    async def settle_meditation(self, identity_id, *, session, **kwargs):
        require_miniapp_operation(self.can_dispatch)
        raw = (((session.get("result") or {}).get("data") or {}).get("raw") or {})
        root = raw.get("data") if isinstance(raw.get("data"), dict) else raw
        meditation = (root.get("dwelling") or {}).get("meditation") or {}
        if cave_action_player_error(root, identity_id) or meditation.get("canSettle") is not True or self.pending:
            raise MiniAppRequestAborted("fate_meditation_permission_unverified")
        cancelled = None
        try:
            result = await run_cave_meditation_settle_production_flow(
                identity_id, **kwargs, operation_check=self.can_dispatch,
            )
        except MiniAppFlowCancelled as exc:
            cancelled = exc
            result = exc.result if isinstance(exc.result, dict) else {"outcome_unknown": True}
        confirmed = False
        if self.owns_result():
            raw = result.get("data") or {}
            root = raw.get("data") if isinstance(raw.get("data"), dict) else raw
            action = root.get("actionResult") or {}
            confirmed = bool(result.get("ok") and not cave_action_player_error(root, identity_id)
                             and action.get("ok") is True and type(action.get("cultivationGain")) is int
                             and action["cultivationGain"] >= 0)
            if confirmed:
                rewards, gains = _collect_materials(action)
                self.meditation = {"ok": True, "gains": gains, "rewards": rewards}
            elif (result.get("outcome_unknown") or "outcome_unknown" not in result and result.get("action_dispatched") is not False
                  or result.get("ok") and action.get("ok") is not False):
                self.pending = {"action": "meditation", "before": self.fate_state,
                                "last_update_ms": meditation.get("lastUpdateMs")}
            self.record_state(identity_id, self.fate_state, now=self.now,
                              status="meditation_settled" if confirmed else "meditation_unknown" if self.pending else "meditation_rejected")
        if cancelled is not None:
            raise MiniAppFlowCancelled(result) from None
        if not confirmed:
            raise MiniAppRequestAborted("fate_meditation_not_confirmed")
        return result

    async def deep_action(self, identity_id, **kwargs):
        require_miniapp_operation(self.can_dispatch)
        cancelled = None
        try:
            result = await _run_cave_public_deep_action_locked(
                identity_id, **kwargs, operation=self.deep, operation_check=self.can_dispatch,
            )
        except MiniAppFlowCancelled as exc:
            cancelled = exc
            result = exc.result if isinstance(exc.result, dict) else {"outcome_unknown": True}
        if self.owns_result() and result.get("status") != "cancelled":
            self.deep = _CaveDeepRetreatOperation.capture(identity_id)
            self.deep_summary = {"action": kwargs["action"], "ok": bool(result.get("ok")), "sent": bool(result.get("sent"))}
            if result.get("outcome_unknown"):
                self.pending = {"action": f"deep_{kwargs['action']}", "before": self.fate_state}
            self.record_state(identity_id, self.fate_state, now=self.now,
                              status=f"deep_{kwargs['action']}" if result.get("ok") else f"deep_{kwargs['action']}_unknown")
        if cancelled is not None:
            raise MiniAppFlowCancelled(result) from None
        if result.get("status") == "cancelled":
            raise MiniAppRequestAborted("fate_deep_operation_changed")
        return result


async def run_cave_public_fate_cards(identity_id, public_entry_url, *, choice_key="accept", now=None, operation_check=None):
    identity_id = _identity_id(identity_id)
    owner = MiniAppIdentityOwner.capture(identity_id)
    if owner is None:
        return {"ok": False, "message": "身份不存在", "extra": {}}
    operation = _CaveFateCardsOperation(owner, now=float(now or time.time()), operation_check=operation_check)
    try:
        return await _run_cave_public_fate_cards_owned(
            identity_id, public_entry_url, choice_key=choice_key, now=operation.now, operation=operation,
        )
    except MiniAppFlowCancelled:
        saved = (operation.record.get("state") or {}) if operation.owns_result() and operation.fate_state else {}
        settled = saved.get("status") == "settled"
        raise MiniAppFlowCancelled({
            "ok": settled, "message": "天机命脉已结算，后续操作已取消" if settled else "天机命脉操作已取消",
            "extra": {"status": "cancelled", "daily_exhausted": settled,
                      "gains": saved.get("gains") or {}, "outcome_unknown": bool(operation.pending)},
        }) from None
    except MiniAppRequestAborted as exc:
        return {"ok": False, "message": f"天机命脉链路已停止：{exc}",
                "extra": {"status": "cancelled" if not operation.can_dispatch() else "unverified",
                          "outcome_unknown": bool(operation.pending)}}


async def _run_cave_public_fate_cards_owned(
    identity_id, public_entry_url, *, choice_key, now, operation,
):
    """Drive one selected-role chain; the operation owns all result adoption."""
    try:
        choice_key = normalize_fate_cards_choice_key(choice_key, automation=True)
    except ValueError as exc:
        return {"ok": False, "message": str(exc), "extra": {}}
    token, webview_url, error = _parse_public_cave_entry_url(public_entry_url)
    if error:
        return {"ok": False, "message": error, "extra": {}}
    operation.token = token
    require_miniapp_operation(operation.can_dispatch)
    lock = _public_entry_lock(identity_id)
    if lock.locked():
        return {"ok": False, "message": "洞府公共入口操作执行中", "extra": {}}

    async with lock:
        source = f"cave_public_fate_cards:{identity_id}"
        capture = _capture_store(now)
        session = {}
        launch = {}
        fate_init_data = ""

        def record_gains():
            return dict((operation.record.get("state") or {}).get("gains") or {})

        def failed(message, result=None):
            return {"ok": False, "message": message, "extra": _miniapp_result_extra({
                "gains": record_gains(), "outcome_unknown": bool(operation.pending),
            }, result or {})}

        def waiting(status, message, *, deep=None):
            deep_summary = dict(operation.deep_summary)
            if deep:
                deep_summary.update({key: deep.get(key) for key in ("active", "remaining_seconds", "end_ms")})
            record = operation.record_state(
                identity_id, operation.fate_state, now=now, status=status, deep_retreat=deep_summary,
            )
            retry = _cave_public_deep_retry_after(deep, now=now) if deep else _fate_cards_retry_after_sec(operation.fate_state)
            console_log(message, scope="identity", send_as_id=identity_id, limit=240)
            return {"ok": True, "message": message, "extra": {
                "retry_after_sec": retry, "record_key": record.get("record_key", ""),
                "deep_retreat": deep or {}, "gains": record_gains(),
            }}

        def terminal():
            status = (operation.fate_state.get("quest") or {}).get("status")
            if status not in {"settled", "expired"}:
                return None
            record = operation.record_state(identity_id, operation.fate_state, now=now, status=status)
            return {"ok": True, "message": "天机命脉今日已结算" if status == "settled" else "天机命脉今日已过期",
                    "extra": {"terminal_skip": True, "daily_exhausted": True, "record_key": record.get("record_key", "")}}

        async def load_dwelling(suffix):
            nonlocal session
            loaded = await operation.read(
                _load_cave_public_identity_session, identity_id, token, webview_url,
                now=now, capture_source=f"{source}:{suffix}", include_details=True,
            )
            if loaded.get("ok"):
                raw = ((loaded.get("result") or {}).get("data") or {}).get("raw") or {}
                error = cave_action_player_error({"account": {"playerId": loaded.get("player_id")}}, identity_id)
                error = error or cave_action_player_error(raw, identity_id)
                if error:
                    return {"ok": False, "error": error, "status": "identity_unverified"}
                session = loaded
            return loaded

        def dwelling_data():
            return (session.get("result") or {}).get("data") or {}

        async def read_fate(suffix):
            result = await operation.read(
                run_fate_cards_start_probe_production, identity_id,
                token=launch.get("token"), webview_url=launch.get("webview_url"),
                init_data=fate_init_data, capture_sink=capture,
                capture_source=f"{source}:{suffix}", request_budget=operation.budget,
            )
            if not result.get("ok"):
                raise MiniAppRequestAborted("fate_read_failed")
            operation.accept_state(_fate_cards_state_from_result(result))

        async def mutate_fate(action, payload=None, expected=""):
            return await operation.fate_action(
                identity_id, action, token=launch.get("token"),
                webview_url=launch.get("webview_url"), init_data=fate_init_data,
                payload=payload or {}, expected=expected,
                capture_sink=capture, capture_source=source,
            )

        loaded = await load_dwelling("start")
        if not loaded.get("ok"):
            return failed("洞府天机命脉身份读取失败", loaded)
        external = find_fate_cards_external_app(dwelling_data().get("raw") or {})
        if not external or not external.get("available"):
            return {"ok": True, "message": "洞府公共入口未开放天机命脉", "extra": {"terminal_skip": True}}
        if external.get("action"):
            result = await operation.read(
                run_cave_external_action_production_flow, identity_id,
                token=token, webview_url=webview_url, action=external["action"],
                player_id=session.get("player_id"), init_data=session.get("init_data"),
                capture_sink=capture, capture_source=f"{source}:external",
            )
            if not result.get("ok"):
                return failed("洞府天机命脉动态入口获取失败", result)
            launch = _find_fate_cards_launch_in_cave_payload(result.get("data") or {})
        elif external.get("url"):
            launch = _find_fate_cards_launch_in_cave_payload(external)
        if not launch:
            return failed("洞府天机命脉入口未返回可用 URL")
        try:
            fate_init_data = await operation.read(
                request_fate_cards_miniapp_init_data, identity_id,
                token=launch.get("token"), webview_url=launch.get("webview_url"),
            )
        except MiniAppRequestAborted:
            raise
        except Exception as exc:
            return failed(f"天机命脉 WebView 会话失败：{type(exc).__name__}")
        await read_fate("fate_start")
        if done := terminal():
            return done

        for action in ("draw", "interpret", "choose"):
            fate = operation.fate_state
            if (action == "draw" and fate.get("has_drawn")
                    or action == "interpret" and fate.get("has_ai_reading")
                    or action == "choose" and fate.get("choice_key")):
                continue
            payload = {}
            expected = ""
            if action == "draw":
                expected = fate.get("default_question_key") or FATE_CARDS_FRONTEND_DEFAULT_QUESTION_KEY
                available = {item.get("key") for item in fate.get("questions") or ()}
                if expected not in available:
                    return failed("天机命脉页面未提供前端默认修行主题")
                if choice_key not in {item.get("key") for item in fate.get("choices") or ()}:
                    return failed(f"天机命脉页面未提供配置命择 {choice_key}")
                payload = {"questionKey": expected}
            elif action == "choose":
                expected = choice_key
                if expected not in {item.get("key") for item in fate.get("choices") or ()}:
                    return failed(f"天机命脉页面未提供配置命择 {choice_key}")
                payload = {"choiceKey": expected}
            result = await mutate_fate(action, payload, expected)
            if not result.get("ok"):
                return failed(f"天机命脉 {action} 未确认", result)

        performed = set()
        for _step in range(6):
            if done := terminal():
                return done
            quest = operation.fate_state.get("quest") or {}
            if quest.get("can_settle") or operation.fate_state.get("choice_key") != "accept":
                break
            require_miniapp_operation(operation.can_dispatch)
            overview = dwelling_data().get("overview") or {}
            meditation = overview.get("meditation") or {}
            if meditation.get("can_settle") and "meditation" not in performed:
                performed.add("meditation")
                await operation.settle_meditation(
                    identity_id, session=session, token=token, webview_url=webview_url,
                    player_id=session.get("player_id"), init_data=session.get("init_data"),
                    capture_sink=capture, capture_source=f"{source}:meditation",
                )
                refreshed = await load_dwelling("after_meditation_dwelling")
                if not refreshed.get("ok"):
                    return failed("天机命脉静室结算后洞府回读失败", refreshed)
                await read_fate("after_meditation")
                continue
            deep = overview.get("deep_seclusion") or {}
            action = ""
            if deep.get("active"):
                if (deep.get("can_settle") or deep.get("completed")) and "settle" not in performed:
                    action = "settle"
                else:
                    return waiting("waiting_deep_retreat", "天机命脉已承命，深度闭关进行中，完成后回洞府继续结算", deep=deep)
            elif deep.get("can_force_exit") and "force" not in performed:
                action = "force"
            elif deep.get("can_start") and "start" not in performed:
                action = "start"
            if not action:
                return waiting("waiting_meditation", "天机命脉已承命，静室暂无可结算修为，且深度闭关暂不可接续")
            performed.add(action)
            result = await operation.deep_action(
                identity_id, token=token, webview_url=webview_url, action=action,
                session=session, init_data=session.get("init_data"), now=now,
                capture_sink=capture, capture_source=source,
            )
            if not result.get("ok") or action in {"start", "force"} and not result.get("sent"):
                return failed(f"天机命脉深度闭关 {action} 未确认", result)
            refreshed = await load_dwelling(f"after_deep_{action}_dwelling")
            if not refreshed.get("ok"):
                return failed(f"天机命脉深度闭关 {action} 后洞府回读失败", refreshed)
            if action == "start":
                deep = (dwelling_data().get("overview") or {}).get("deep_seclusion") or {}
                return waiting("waiting_deep_retreat", "天机命脉已承命，已启动深度闭关，完成后回洞府继续结算", deep=deep)
            if action == "settle":
                await read_fate("after_deep_settle")

        quest = operation.fate_state.get("quest") or {}
        if not quest.get("can_settle"):
            return waiting("waiting_quest", "天机命脉任务进行中，等待服务端完成条件")
        result = await mutate_fate("settle")
        if not result.get("ok"):
            return failed("天机命脉领奖未确认", result)
        gains = record_gains()
        material = "、".join(f"{name}+{amount}" for name, amount in sorted(gains.items()) if _parse_int(amount, 0) > 0)
        return {"ok": True, "message": f"天机命脉完成：{material or '已结算，奖励数量未确认'}", "extra": {
            "daily_exhausted": True, "settled_count": 1, "gains": gains, "record_key": operation.record_key,
        }}


async def run_cave_public_tower(identity_id, public_entry_url, *, now=None, operation_check=None):
    """Run one identity's daily tower challenge through the dwelling entry."""
    identity_id = _identity_id(identity_id)
    now = float(now or time.time())
    owner = MiniAppIdentityOwner.capture(identity_id)
    if identity_id <= 0 or owner is None:
        return {"ok": False, "message": "身份不存在", "extra": {}}
    if not is_cave_public_identity_available(identity_id):
        return {"ok": False, "message": "身份已停用", "extra": {}}
    if not _public_entry_allowed():
        return {"ok": False, "message": "全局暂停来源不允许洞府公共入口 MiniApp HTTP", "extra": {}}
    cancelled = {"ok": False, "message": "洞府闯塔操作已取消或身份已变更", "extra": {"status": "cancelled"}}

    def can_continue():
        return (
            owner.is_current()
            and is_cave_public_identity_available(identity_id)
            and _public_entry_allowed()
            and (operation_check is None or operation_check() is True)
        )

    if not can_continue():
        return cancelled
    token, webview_url, error = _parse_public_cave_entry_url(public_entry_url)
    if error:
        return {"ok": False, "message": error, "extra": {}}
    lock = _public_entry_lock(identity_id)
    if lock.locked():
        return {"ok": False, "message": "洞府公共入口操作执行中", "extra": {}}
    async with lock:
        if not can_continue():
            return cancelled
        session = await _load_cave_public_identity_session(
            identity_id,
            token,
            webview_url,
            now=now,
            capture_source=f"cave_public_tower_start:{identity_id}",
            include_details=True,
            operation_check=can_continue,
        )
        if not can_continue():
            return cancelled
        if not session.get("ok"):
            message = f"洞府琉璃问心塔身份读取失败：{session.get('error') or 'unknown'}"
            await send_audit_log(f"🗼 {message}", scope="identity", send_as_id=identity_id, priority="normal", limit=240)
            return {"ok": False, "message": message, "extra": _miniapp_result_extra({}, session)}
        init_data = session.get("init_data") or ""
        selected_player_id = session.get("player_id")
        cave_result = dict(session.get("result") or {})
        cave_data = dict(cave_result.get("data") or {})
        raw = cave_data.get("raw") if isinstance(cave_data.get("raw"), dict) else {}
        external_app = _find_tower_external_app_in_cave_payload(raw)
        if not external_app or not external_app.get("available"):
            message = "洞府公共入口未开放琉璃问心塔"
            await send_audit_log(f"🗼 {message}", scope="identity", send_as_id=identity_id, priority="normal", limit=220)
            return {"ok": False, "message": message, "extra": {}}

        launch = {}
        if external_app.get("action"):
            external_result = await run_cave_external_action_production_flow(
                identity_id,
                token=token,
                webview_url=webview_url,
                action=external_app["action"],
                player_id=selected_player_id,
                init_data=init_data,
                capture_sink=_capture_store(now),
                capture_source=f"cave_public_tower_external:{identity_id}",
                operation_check=can_continue,
            )
            if not can_continue():
                return cancelled
            if not external_result.get("ok"):
                message = f"洞府琉璃问心塔动态入口获取失败：{external_result.get('error') or external_result.get('status') or 'unknown'}"
                await send_audit_log(f"🗼 {message}", scope="identity", send_as_id=identity_id, priority="normal", limit=240)
                return {"ok": False, "message": message, "extra": _miniapp_result_extra({}, external_result)}
            launch = _find_tower_launch_in_cave_payload(external_result.get("data") or {})
        elif external_app.get("url"):
            launch = _find_tower_launch_in_cave_payload(external_app)
        if not launch:
            message = "洞府琉璃问心塔入口未返回可用 URL"
            await send_audit_log(f"🗼 {message}", scope="identity", send_as_id=identity_id, priority="normal", limit=220)
            return {"ok": False, "message": message, "extra": {}}

        cancelled_flow = None
        try:
            result = await run_tower_miniapp_production_flow(
                identity_id,
                token=launch.get("token"),
                init_data=init_data,
                capture_sink=_tower_capture_store(now),
                capture_source=f"cave_public_tower:{identity_id}",
                operation_check=can_continue,
            )
        except MiniAppFlowCancelled as exc:
            cancelled_flow = exc
            result = exc.result if isinstance(exc.result, dict) else {}
            if not result.get("ok"):
                raise
        # A switch-off stops new actions, not a confirmed result for this owner.
        if not owner.is_current() or (not result.get("ok") and not can_continue()):
            if cancelled_flow is not None:
                raise MiniAppFlowCancelled() from None
            return cancelled
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        tower_state = data.get("state") if isinstance(data.get("state"), dict) else {}
        replay = data.get("replay") if isinstance(data.get("replay"), dict) else {}
        gains = dict(data.get("gains") or {})
        rewards = dict(data.get("rewards") or {})
        phase = "completed" if result.get("ok") else "blocked"
        record_miniapp_state(
            identity_id,
            "tower",
            {
                "phase": phase,
                "status": result.get("status") or "",
                "challenged": bool(data.get("challenged")),
                "dao_name": tower_state.get("dao_name") or "",
                "today_highest": tower_state.get("today_highest", 0),
                "record_highest": tower_state.get("record_highest", 0),
                "cleared_count": replay.get("cleared_count", 0),
                "end_floor": replay.get("end_floor", 0),
                "failed_floor": replay.get("failed_floor", 0),
                "gains": gains,
                "rewards": rewards,
                "error": result.get("error") or "",
            },
            source="cave_public_tower",
            source_id=f"tower:{identity_id}:{int(now)}",
            now=now,
            outputs=("daily_counter", "tower_progress", "rewards"),
            replaces_commands=(".闯塔", ".继续闯塔"),
        )
        if result.get("status") == "done_today":
            message = "洞府琉璃问心塔：今日已完成或已止步，未重铸道心"
        elif result.get("ok"):
            message = (
                f"洞府琉璃问心塔：通过 {replay.get('cleared_count', 0)} 层"
                f"｜止步 {replay.get('failed_floor') or '未止步'} 层"
                f"｜修为 {format_tower_delta(gains.get('修为', 0))}"
                f"｜塔印 {format_tower_delta(gains.get('塔印', 0))}"
            )
        else:
            message = f"洞府琉璃问心塔失败：{result.get('error') or result.get('status') or 'unknown'}"
        response = {
            "ok": bool(result.get("ok")),
            "message": message,
            "extra": _miniapp_result_extra({
                "status": result.get("status") or "",
                "state": tower_state,
                "replay": replay,
                "gains": gains,
                "rewards": rewards,
            }, result),
        }
        if cancelled_flow is not None:
            raise MiniAppFlowCancelled(response) from None
        try:
            await send_audit_log(
                f"🗼 {message}",
                scope="identity",
                send_as_id=identity_id,
                priority="low" if result.get("ok") else "normal",
                limit=280,
            )
        except asyncio.CancelledError:
            raise MiniAppFlowCancelled(response) from None
        except Exception as exc:
            logging.getLogger(__name__).warning("Tower notification failed (%s); result preserved", type(exc).__name__)
        return response


async def run_cave_public_fishing(identity_id, public_entry_url, *, now=None):
    """Run fishing for a selected dwelling identity without a channel group command."""
    identity_id = _identity_id(identity_id)
    now = float(now or time.time())
    operation = FishingMiniAppOperation.capture(identity_id)
    if identity_id <= 0 or operation is None:
        return {"ok": False, "message": "身份不存在", "extra": {}}
    if not is_cave_public_identity_available(identity_id):
        return {"ok": False, "message": "身份已停用", "extra": {}}
    if not _public_entry_allowed():
        return {"ok": False, "message": "全局暂停来源不允许洞府公共入口 MiniApp HTTP", "extra": {}}
    token, webview_url, error = _parse_public_cave_entry_url(public_entry_url)
    if error:
        return {"ok": False, "message": error, "extra": {}}
    observation = _CAVE_PUBLIC_ENTRY_OBSERVATION.get()

    def can_continue():
        return (
            operation.is_current() and _public_entry_allowed()
            and (observation is None or observation.permits(identity_id, token))
        )

    cancelled = {"ok": False, "message": "洞府钓鱼操作已取消或身份已变更", "extra": {"status": "cancelled"}}

    async def report(response, *, priority="normal", limit=260, daily=False):
        notice = FishingMiniAppOperation.capture(identity_id)
        if notice is None or not operation.owner.is_current():
            return cancelled
        try:
            await send_audit_log(f"🎣 {response['message']}", scope="identity", send_as_id=identity_id,
                                 priority=priority, limit=limit)
            if not operation.owner.is_current():
                return cancelled
            if daily and notice.is_current() and _public_entry_allowed() and (
                observation is None or observation.permits(identity_id, token)
            ):
                with use_identity(identity_id):
                    await _send_fishing_daily_completion_summary(time.time())
        except asyncio.CancelledError:
            raise MiniAppFlowCancelled(response if operation.owner.is_current() else None) from None
        except Exception as exc:
            logging.getLogger(__name__).warning("Fishing notification failed (%s); result preserved", type(exc).__name__)
        return response if operation.owner.is_current() else cancelled

    lock = _public_entry_lock(identity_id)
    game_lock = _fishing_send_lock(identity_id)
    if lock.locked() or game_lock.locked():
        return {"ok": False, "message": "洞府公共入口操作执行中", "extra": {"status": "busy"}}
    async with lock, game_lock:
        if not can_continue():
            return cancelled
        recovered = recover_fishing_result_pending(identity_id)
        if recovered is not None:
            return recovered
        try:
            session = await _load_cave_public_identity_session(
                identity_id, token, webview_url, now=now,
                capture_source=f"cave_public_fishing_start:{identity_id}", include_details=True,
                operation_check=can_continue,
            )
        except MiniAppFlowCancelled:
            raise MiniAppFlowCancelled(cancelled) from None
        if not can_continue():
            return cancelled
        if not session.get("ok"):
            message = f"洞府钓鱼身份读取失败：{session.get('error') or 'unknown'}"
            return await report({"ok": False, "message": message, "extra": _miniapp_result_extra({}, session)})

        dwelling_init_data = str(session.get("init_data") or "")
        selected_player_id = session.get("player_id")
        cave_result = dict(session.get("result") or {})
        cave_data = dict(cave_result.get("data") or {})
        raw = cave_data.get("raw") if isinstance(cave_data.get("raw"), dict) else {}
        external_app = _find_fishing_external_app_in_cave_payload(raw)
        if not external_app:
            with use_identity(identity_id):
                state["next_fishing_time"] = fishing_behavior.next_fishing_reset_timestamp(
                    now,
                    _fishing_reset_jitter_sec(identity_id),
                )
                state["fishing_last_result"] = "未开放灵溪垂钓，今日跳过"
                state["fishing_last_error"] = ""
                save_state()
            message = "该身份未开放灵溪垂钓，今日跳过"
            return await report({"ok": True, "message": message, "extra": {"skipped": "entry_missing"}}, priority="low", limit=220)
        if not external_app.get("available"):
            with use_identity(identity_id):
                state["next_fishing_time"] = fishing_behavior.next_fishing_reset_timestamp(
                    now,
                    _fishing_reset_jitter_sec(identity_id),
                )
                state["fishing_last_result"] = "未持有鱼竿，今日跳过"
                state["fishing_last_error"] = ""
                save_state()
            message = "未持有鱼竿，今日跳过灵溪垂钓"
            return await report({"ok": True, "message": message, "extra": {"skipped": "rod_missing"}}, priority="low", limit=220)

        launch = {}
        if external_app.get("action"):
            try:
                external_result = await run_cave_external_action_production_flow(
                    identity_id, token=token, webview_url=webview_url, action="fishing",
                    player_id=selected_player_id, init_data=dwelling_init_data,
                    capture_sink=_capture_store(now), capture_source=f"cave_public_fishing_external:{identity_id}",
                    operation_check=can_continue,
                )
            except MiniAppFlowCancelled:
                raise MiniAppFlowCancelled(cancelled) from None
            if not can_continue():
                return cancelled
            if not external_result.get("ok"):
                message = f"洞府钓鱼动态入口获取失败：{external_result.get('error') or external_result.get('status') or 'unknown'}"
                return await report({"ok": False, "message": message, "extra": _miniapp_result_extra({}, external_result)})
            launch = extract_fishing_miniapp_launch_from_dwelling_payload(external_result.get("data") or {})
        elif external_app.get("url"):
            launch = extract_fishing_miniapp_launch_from_dwelling_payload({
                "account": {"externalApps": {"groups": [{"apps": [external_app]}]}},
            })
        if not launch:
            message = "洞府钓鱼入口已请求，但未返回可用 URL"
            return await report({"ok": False, "message": message, "extra": {}})

        with use_identity(identity_id):
            max_rounds = _remaining_miniapp_chain_rounds(now)
        capture_sink = _fishing_miniapp_capture_store(now)
        capture_source = f"cave_public_fishing:{identity_id}"
        cancelled_flow = None
        writer = fishing_operations.CheckpointWriter(operation, operation_check=can_continue)
        try:
            result = await run_fishing_miniapp_production_flow(
                identity_id, token=launch.get("token"), webview_url=launch.get("webview_url"),
                init_data=dwelling_init_data, max_rounds=max_rounds,
                pond_choice=operation.pond_choice, bait_choice=operation.bait_choice,
                capture_sink=capture_sink, capture_source=capture_source,
                operation_check=lambda: can_continue() and writer.is_current(), checkpoint=writer,
            )
        except MiniAppFlowCancelled as exc:
            cancelled_flow = exc
            result = exc.result if isinstance(exc.result, dict) else {}
        result = dict(result or {})
        writer.finish(result)
        confirmed = fishing_miniapp_has_confirmed_outcome(result)
        if not operation.owner.is_current() or (not can_continue() and not confirmed):
            if cancelled_flow is not None:
                raise MiniAppFlowCancelled() from None
            return cancelled
        if cancelled_flow is not None and not confirmed:
            raise MiniAppFlowCancelled(result) from None
        if result.get("status") == "cancelled" and not confirmed:
            return cancelled
        update_schedule = can_continue() and cancelled_flow is None and result.get("status") != "cancelled"
        _record_fishing_business_capture(capture_sink, result, source=capture_source, now=now)
        try:
            with use_identity(identity_id):
                summary = _apply_fishing_miniapp_result(result, time.time(), update_schedule=update_schedule, public_entry=True)
        except FishingMiniAppCommitError as exc:
            response = _fishing_result_commit_response(exc.reason)
            if cancelled_flow is not None:
                raise MiniAppFlowCancelled(response) from None
            return response
        public_only_bait_missing = update_schedule and str(result.get("status") or "").strip() == "bait_missing"
        status = str(result.get("status") or "").strip()
        completed_ok = bool(result.get("ok")) or status in {"daily_limit", "no_rod"} or public_only_bait_missing
        message = f"洞府灵溪垂钓公共入口：{summary}"
        extra = {
            "fishing_title": launch.get("title") or external_app.get("title") or "灵溪垂钓",
            "player_id": selected_player_id, "daily_exhausted": status == "daily_limit",
            "operation_cancelled": not update_schedule,
        }
        if public_only_bait_missing or status == "no_rod":
            extra.update(skipped="bait_missing" if public_only_bait_missing else "rod_missing", terminal_skip=True)
        response = {
            "ok": completed_ok,
            "message": message,
            "extra": _miniapp_result_extra(extra, result),
        }
        if cancelled_flow is not None:
            raise MiniAppFlowCancelled(response) from None
        if fishing_operations.pending(operation.owner.identity):
            return fishing_operations.response(operation.owner.identity[fishing_operations.STATE_KEY])
        if not update_schedule:
            return response
        return await report(response, priority="low" if completed_ok and not result.get("error") else "normal",
                            limit=420, daily=completed_ok)


@dataclass(frozen=True)
class _CaveYuanyingOperation:
    owner: MiniAppIdentityOwner
    schedule: dict
    record: dict
    enabled: bool

    @classmethod
    def capture(cls, identity_id):
        owner = MiniAppIdentityOwner.capture(identity_id)
        if owner is None:
            return None
        spec = yuanying.YUANYING_SPEC
        return cls(
            owner,
            {key: deepcopy(owner.identity.get(key)) for key in (
                spec.phase_key, spec.next_time_key, spec.last_command_key,
                spec.probe_pending_key, spec.summary_sent_at_key, spec.last_summary_msg_id_key,
            )},
            deepcopy(get_miniapp_state_records().get(f"{identity_id}:cave_yuanying") or {}),
            bool(owner.identity.get(spec.enabled_key)),
        )

    def result_is_current(self):
        return (
            self.owner.is_current()
            and all(self.owner.identity.get(key) == value for key, value in self.schedule.items())
            and self.record == (get_miniapp_state_records().get(f"{self.owner.identity_id}:cave_yuanying") or {})
        )

    def can_dispatch(self):
        return (
            self.result_is_current() and self.enabled
            and bool(self.owner.identity.get("yuanying_enabled")) == self.enabled
            and is_cave_public_identity_available(self.owner.identity_id)
            and _public_entry_allowed()
        )

    def advanced(self):
        current = self.capture(self.owner.identity_id)
        return type(self)(self.owner, current.schedule, current.record, self.enabled)


def _record_cave_yuanying_state(owner, payload, now):
    recorded = record_miniapp_state(
        owner.identity_id, "cave_yuanying", {"account_id": owner.account_id, **payload},
        source="cave_public_yuanying", source_id=f"cave_yuanying:{stable_payload_digest(payload)}",
        now=now, outputs=["yuanying_phase", "next_yuanying_time"],
        replaces_commands=[yuanying.CMD_YUANYING_STATUS, yuanying.CMD_YUANYING], persist=False,
    )
    save_state()
    return recorded


def _defer_cave_yuanying(owner, now, *, unknown, retry_after_sec=0):
    with use_identity(owner.identity_id):
        yuanying.set_yuanying_phase("launching" if unknown else "idle")
        state["next_yuanying_time"] = max(
            float(state.get("next_yuanying_time", 0) or 0),
            now + max(CAVE_YUANYING_UNKNOWN_RECHECK_SEC, retry_after_sec),
        )
        state["yuanying_probe_pending"] = False


def _cave_yuanying_launch_unknown(result, sync_result):
    if sync_result.get("handled"):
        return False
    if result.get("outcome_unknown") is True:
        return True
    if result.get("action_dispatched") is False:
        return False
    if result.get("ok") is False and result.get("outcome_unknown") is False:
        return False
    for event in result.get("events") or ():
        status = event.get("status_code") if isinstance(event, dict) else None
        if isinstance(status, int) and 400 <= status < 500:
            return False
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    root = data.get("data") if isinstance(data.get("data"), dict) else data
    action = root.get("actionResult") if isinstance(root.get("actionResult"), dict) else {}
    return not (root.get("ok") is False or action.get("ok") is False)


async def _audit_cave_yuanying(message, identity_id, response):
    try:
        await send_audit_log(
            f"👶 {message}", scope="identity", send_as_id=identity_id, priority="normal", limit=320,
        )
    except asyncio.CancelledError:
        raise MiniAppFlowCancelled(response) from None
    except Exception as exc:
        logging.getLogger(__name__).warning("Cave YuanYing audit failed (%s)", type(exc).__name__)


async def run_cave_public_yuanying(identity_id, public_entry_url, *, now=None):
    """Read current status and launch at most once under one captured owner."""
    identity_id = _identity_id(identity_id)
    now = float(now or time.time())
    operation = _CaveYuanyingOperation.capture(identity_id)
    if identity_id <= 0 or operation is None:
        return {"ok": False, "message": "身份不存在", "extra": {}}
    if not is_cave_public_identity_available(identity_id):
        return {"ok": False, "message": "身份已停用", "extra": {}}
    if not _public_entry_allowed():
        return {"ok": False, "message": "全局暂停来源不允许洞府公共入口 MiniApp HTTP", "extra": {}}
    if not operation.enabled:
        return {"ok": False, "message": "元婴模块已关闭", "extra": {}}
    if float(operation.owner.identity.get("next_yuanying_time", 0) or 0) > now:
        return {"ok": False, "message": "元婴尚未到出窍窗口：等待中", "extra": {}}
    pending = yuanying.is_public_yuanying_unresolved(operation.record)
    prior = operation.record.get("state") if isinstance(operation.record, dict) else None
    prior = prior if isinstance(prior, dict) else {}
    if pending and (type(prior.get("account_id")) is not int or prior["account_id"] != operation.owner.account_id):
        return {"ok": False, "message": "洞府元婴未结记录的账号归属未确认", "extra": {"outcome_unknown": True}}
    token, webview_url, error = _parse_public_cave_entry_url(public_entry_url)
    if error:
        return {"ok": False, "message": error, "extra": {}}
    observation = _CAVE_PUBLIC_ENTRY_OBSERVATION.get()
    cancelled = {"ok": False, "message": "洞府元婴操作已取消或身份状态已变更", "extra": {"status": "cancelled"}}

    def can_continue():
        return operation.can_dispatch() and (observation is None or observation.permits(identity_id, token))

    lock = _public_entry_lock(identity_id)
    if lock.locked():
        return {"ok": False, "message": "洞府公共入口操作执行中", "extra": {}}
    async with lock:
        if not can_continue():
            return cancelled
        try:
            session = await _load_cave_public_identity_session(
                identity_id, token, webview_url, now=now,
                capture_source=f"cave_public_tianjige_start:{identity_id}", operation_check=can_continue,
            )
        except MiniAppFlowCancelled:
            raise MiniAppFlowCancelled(cancelled) from None
        if not can_continue():
            return cancelled
        if session.get("ok") is not True:
            message = f"洞府天机阁身份读取失败：{session.get('error') or 'unknown'}"
            response = {"ok": False, "message": message, "extra": _miniapp_result_extra({}, session)}
            await _audit_cave_yuanying(message, identity_id, response)
            return response
        player_error = _cave_tianjige_session_player_error(session, identity_id)
        if player_error:
            return {"ok": False, "message": player_error, "extra": {"status": "identity_unverified"}}
        init_data = session.get("init_data") or ""
        selected_player_id = session.get("player_id")
        try:
            status_result = await run_cave_tianjige_command_production_flow(
                identity_id, token=token, webview_url=webview_url, command=yuanying.CMD_YUANYING_STATUS,
                init_data=init_data, player_id=selected_player_id, capture_sink=_capture_store(now),
                capture_source=f"cave_public_tianjige_yuanying_status:{identity_id}", operation_check=can_continue,
            )
        except MiniAppFlowCancelled:
            raise MiniAppFlowCancelled(cancelled) from None
        if not can_continue():
            return cancelled
        status_data = status_result.get("data") if isinstance(status_result.get("data"), dict) else {}
        player_error = cave_action_player_error(status_data, identity_id) if status_result.get("ok") is True else ""
        status_ok = status_result.get("ok") is True and not player_error and _cave_tianjige_action_succeeded(status_data)
        status_sync = {"handled": False, "ready": False}
        if status_ok:
            status_sync = await sync_cave_tianjige_yuanying_result(
                identity_id, status_data, now=now, command=yuanying.CMD_YUANYING_STATUS,
            )
        if pending:
            reconciled = prior.get("account_id") == operation.owner.account_id and status_sync.get("kind") == "running"
            if not reconciled:
                _defer_cave_yuanying(
                    operation.owner, now, unknown=True, retry_after_sec=miniapp_retry_after_sec(status_result),
                )
            _record_cave_yuanying_state(operation.owner, {
                **prior, "status": "reconciled_running" if reconciled else "unknown",
                "outcome_unknown": not reconciled, "last_status_kind": status_sync.get("kind") or "",
            }, now)
            message = "洞府天机阁元婴状态已确认云游中" if reconciled else "洞府天机阁上次出窍结果仍待核实，仅查状态，不重复出窍"
            response = {"ok": bool(reconciled), "message": message, "extra": _miniapp_result_extra({
                "status_sync": status_sync, "launched": False, "outcome_unknown": not reconciled,
            }, status_result)}
            await _audit_cave_yuanying(message, identity_id, response)
            return response
        if not status_ok or not status_sync.get("handled"):
            reply_message = (
                extract_cave_tianjige_command_message(status_data)
                if status_result.get("ok") is True and not player_error else ""
            )
            reason = player_error or status_result.get("error") or reply_message or status_sync.get("reason") or status_result.get("status") or "unknown"
            message = f"洞府天机阁元婴状态未确认：{reason}"
            response = {"ok": False, "message": message, "extra": _miniapp_result_extra({"status_sync": status_sync}, status_result)}
            await _audit_cave_yuanying(message, identity_id, response)
            return response
        if not status_sync.get("ready"):
            message = f"洞府天机阁元婴状态：{status_sync.get('message') or '已同步，当前无需出窍'}"
            response = {"ok": True, "message": message, "extra": _miniapp_result_extra({"status_sync": status_sync, "launched": False}, status_result)}
            await _audit_cave_yuanying(message, identity_id, response)
            return response

        operation = operation.advanced()
        if not can_continue():
            return cancelled
        intent = {
            "status": "dispatching", "outcome_unknown": True,
            "player_id": selected_player_id, "started_at": now,
        }
        with use_identity(identity_id):
            yuanying.set_yuanying_phase("launching")
        _record_cave_yuanying_state(operation.owner, intent, now)
        operation = operation.advanced()
        cancelled_flow = None
        try:
            result = await run_cave_tianjige_command_production_flow(
                identity_id, token=token, webview_url=webview_url, command=yuanying.CMD_YUANYING,
                init_data=init_data, player_id=selected_player_id, capture_sink=_capture_store(now),
                capture_source=f"cave_public_tianjige_yuanying:{identity_id}", operation_check=can_continue,
            )
        except asyncio.CancelledError as exc:
            cancelled_flow = exc
            result = getattr(exc, "result", None)
        except Exception as exc:
            result = {"ok": False, "status": "failed", "error": type(exc).__name__, "outcome_unknown": True}
        result = result if isinstance(result, dict) else {"ok": False, "status": "cancelled", "outcome_unknown": True}
        if not operation.result_is_current():
            if cancelled_flow is not None:
                raise MiniAppFlowCancelled(cancelled) from None
            return cancelled
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        player_error = cave_action_player_error(data, identity_id) if result.get("ok") is True else ""
        if player_error:
            result = {**result, "ok": False, "data": {}, "error": player_error, "outcome_unknown": result.get("action_dispatched") is not False}
            data = {}
        sync_result = {"handled": False}
        if result.get("ok") is True and _cave_tianjige_action_succeeded(data):
            sync_result = await sync_cave_tianjige_yuanying_result(identity_id, data, now=now, command=yuanying.CMD_YUANYING)
        unknown = _cave_yuanying_launch_unknown(result, sync_result)
        if not sync_result.get("handled"):
            _defer_cave_yuanying(operation.owner, now, unknown=unknown, retry_after_sec=miniapp_retry_after_sec(result))
        final_status = "confirmed" if sync_result.get("handled") else "unknown" if unknown else "rejected"
        _record_cave_yuanying_state(operation.owner, {
            **intent, "status": final_status, "outcome_unknown": unknown,
            "action_dispatched": result.get("action_dispatched", True),
            "result_kind": sync_result.get("kind") or "", "error": result.get("error") or sync_result.get("reason") or "",
        }, now)
        if sync_result.get("handled"):
            message = f"洞府天机阁元婴出窍：{sync_result.get('message') or '已同步'}"
        elif unknown:
            message = "洞府天机阁元婴出窍结果未知，保留记录，仅允许后续查状态"
        else:
            message = f"洞府天机阁元婴出窍未执行：{result.get('error') or '请求被拒绝'}"
        response = {
            "ok": bool(sync_result.get("handled")), "message": message,
            "extra": _miniapp_result_extra({
                "status_sync": status_sync, "sync": sync_result,
                "launched": sync_result.get("kind") == "launched", "outcome_unknown": unknown,
                "action_dispatched": result.get("action_dispatched", True),
            }, status_result, result),
        }
        if cancelled_flow is not None:
            raise MiniAppFlowCancelled(response) from None
        await _audit_cave_yuanying(message, identity_id, response)
        return response


async def run_cave_public_tianti_status(identity_id, public_entry_url, *, now=None):
    """Use the same guarded status-only path as explicit Tianjige reads."""
    return await run_cave_public_tianjige_read_only(
        identity_id, public_entry_url, CMD_TIANTI_STATUS, now=now,
    )


def _sync_cave_tianjige_read_only_message(identity_id, command, message, *, now):
    """Apply supported Tianjige panels through status-only module bridges."""
    if command not in {CMD_TIANTI_STATUS, ".我的阴罗幡", ".我的侍妾"}:
        return {"supported": False, "handled": False, "summary": {}}

    with use_identity(identity_id):
        if command == CMD_TIANTI_STATUS:
            sync_result = tianti.sync_tianti_miniapp_status(message, now=now)
            progress = int(state.get("tianti_progress_current", 0) or 0)
            total = int(state.get("tianti_progress_total", 0) or 0)
            return {
                "supported": True,
                "handled": bool(sync_result.get("handled")),
                "reason": str(sync_result.get("reason") or ""),
                "summary": sync_result,
                "detail": f"进度 {progress}/{total}",
            }
        if command == ".我的侍妾":
            sync_result = concubine.sync_concubine_miniapp_status(message, now)
            summary = dict(sync_result.get("summary") or {})
            detail = ""
            if sync_result.get("handled"):
                detail = (
                    f"侍妾 {summary.get('name') or '未知'}"
                    f"｜情缘 {summary.get('affinity', 0)}"
                    f"｜位置 {summary.get('location') or '未知'}"
                )
            return {
                "supported": True,
                "handled": bool(sync_result.get("handled")),
                "reason": str(sync_result.get("reason") or ""),
                "summary": summary,
                "detail": detail,
            }
        sync_result = yinluo.sync_yinluo_miniapp_status(message, now)
    summary = dict(sync_result.get("summary") or {})
    return {
        "supported": True,
        "handled": bool(sync_result.get("handled")),
        "reason": str(sync_result.get("reason") or ""),
        "summary": summary,
        "detail": (
            f"煞气 {summary.get('sha_current', 0)}/{summary.get('sha_max', 0)}"
            f"｜精华槽 {len(summary.get('ready_slots') or [])}"
            f"｜炼化中 {len(summary.get('refining_slots') or [])}"
        ),
    }


def _unbridged_cave_tianjige_observation(command, message):
    """Keep an unowned panel observable without presenting it as synced state."""
    raw_message = str(message or "").strip()
    first_line = re.sub(r"\s+", " ", raw_message.splitlines()[0] if raw_message else "").strip()
    return {
        "command": str(command or "").strip(),
        "message_digest": stable_payload_digest(raw_message),
        "message_length": len(raw_message),
        "first_line": first_line[:120],
    }


async def _audit_cave_tianjige_read_only(identity_id, response, *, detail="", priority="normal"):
    try:
        await send_audit_log(
            f"📖 {response['message']}{f'｜{detail}' if detail else ''}",
            scope="identity", send_as_id=identity_id, priority=priority, limit=360,
        )
    except asyncio.CancelledError:
        raise MiniAppFlowCancelled(response) from None
    except Exception as exc:
        logging.getLogger(__name__).warning("Cave read-only audit failed (%s)", type(exc).__name__)


async def run_cave_public_tianjige_read_only(identity_id, public_entry_url, command, *, now=None):
    identity_id = _identity_id(identity_id)
    now = float(now or time.time())
    try:
        normalized_command = str(command or "").strip()
        if normalized_command not in CAVE_TIANJIGE_READ_ONLY_COMMANDS:
            return {"ok": False, "message": "洞府天机阁只读命令不在白名单", "extra": {}}
    except Exception:
        return {"ok": False, "message": "洞府天机阁只读命令无效", "extra": {}}
    owner = MiniAppIdentityOwner.capture(identity_id)
    if identity_id <= 0 or owner is None:
        return {"ok": False, "message": "身份不存在", "extra": {}}
    if not is_cave_public_identity_available(identity_id):
        return {"ok": False, "message": "身份已停用", "extra": {}}
    if not _public_entry_allowed():
        return {"ok": False, "message": "全局暂停来源不允许洞府公共入口 MiniApp HTTP", "extra": {}}
    token, webview_url, error = _parse_public_cave_entry_url(public_entry_url)
    if error:
        return {"ok": False, "message": error, "extra": {}}
    entry_observation = _CAVE_PUBLIC_ENTRY_OBSERVATION.get()
    prefix = {
        CMD_TIANTI_STATUS: "tianti_", ".我的阴罗幡": "yinluo_", ".我的侍妾": "concubine_",
    }.get(normalized_command)

    def business_snapshot():
        return {
            key: deepcopy(value) for key, value in owner.identity.items()
            if prefix and (key.startswith(prefix) or key.startswith(f"next_{prefix}"))
        }

    snapshot = business_snapshot()

    def module_block_reason():
        with use_identity(identity_id):
            if normalized_command == CMD_TIANTI_STATUS:
                return tianti.tianti_miniapp_status_block_reason(now)
            if normalized_command == ".我的侍妾":
                return concubine.concubine_miniapp_status_block_reason(now, processed_at=max(now, time.time()))
            if normalized_command == ".我的阴罗幡":
                return yinluo.yinluo_miniapp_status_block_reason(now)
        return ""

    def can_continue():
        return (
            owner.is_current()
            and is_cave_public_identity_available(identity_id)
            and _public_entry_allowed()
            and (entry_observation is None or entry_observation.permits(identity_id, token))
            and business_snapshot() == snapshot
            and not module_block_reason()
        )

    def cancelled(*results):
        return {
            "ok": False, "message": "洞府天机阁只读操作已取消或状态已变更",
            "extra": _miniapp_result_extra({"status": "cancelled"}, *results),
        }

    lock = _public_entry_lock(identity_id)
    if lock.locked():
        return {"ok": False, "message": "洞府公共入口操作执行中", "extra": {"status": "busy"}}
    reason = module_block_reason()
    if reason:
        return {
            "ok": False, "message": f"洞府天机阁只读状态暂不可同步：{reason}",
            "extra": {
                "status": "busy" if reason in {"active_pending", "active_phase"} else "blocked",
                "reason": reason,
            },
        }
    async with lock:
        if not can_continue():
            return cancelled()
        try:
            session = await _load_cave_public_identity_session(
                identity_id, token, webview_url, now=now,
                capture_source=f"cave_public_tianjige_read_only_start:{identity_id}",
                operation_check=can_continue,
            )
        except asyncio.CancelledError as exc:
            raise MiniAppFlowCancelled(cancelled(getattr(exc, "result", None))) from None
        if not can_continue():
            return cancelled(session)
        if session.get("ok") is not True:
            message = f"洞府天机阁只读身份读取失败：{session.get('error') or 'unknown'}"
            response = {"ok": False, "message": message, "extra": _miniapp_result_extra({}, session)}
            await _audit_cave_tianjige_read_only(identity_id, response)
            return response
        player_error = _cave_tianjige_session_player_error(session, identity_id)
        if player_error:
            return {
                "ok": False, "message": player_error,
                "extra": _miniapp_result_extra({"status": "identity_unverified"}, session),
            }
        try:
            result = await run_cave_tianjige_command_production_flow(
                identity_id, token=token, webview_url=webview_url, command=normalized_command,
                init_data=session.get("init_data") or "", player_id=session.get("player_id"),
                capture_sink=_capture_store(now), capture_source=f"cave_public_tianjige_read_only:{identity_id}",
                operation_check=can_continue,
            )
        except asyncio.CancelledError as exc:
            # A drained HTTP read can succeed without its panel being applied.
            raise MiniAppFlowCancelled(cancelled(session, getattr(exc, "result", None))) from None
        if not can_continue():
            return cancelled(session, result)
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        player_error = cave_action_player_error(data, identity_id) if result.get("ok") is True else ""
        if player_error:
            return {
                "ok": False, "message": player_error,
                "extra": _miniapp_result_extra({"status": "identity_unverified"}, session, result),
            }
        message = extract_cave_tianjige_command_message(data)
        if result.get("ok") is not True or not _cave_tianjige_action_succeeded(data) or not message:
            final_message = f"洞府天机阁只读未确认：{result.get('error') or result.get('status') or '无可识别回包'}"
            response = {
                "ok": False, "message": final_message,
                "extra": _miniapp_result_extra({"raw_message": message}, session, result),
            }
            await _audit_cave_tianjige_read_only(identity_id, response)
            return response
        if normalized_command == ".我的灵兽":
            observation = _unbridged_cave_tianjige_observation(normalized_command, message)
            final_message = "洞府天机阁灵兽面板已读取，但本地尚无对应 reducer；仅观察，不更新放养状态"
            response = {
                "ok": False,
                "message": final_message,
                "extra": _miniapp_result_extra({"command": normalized_command, "observation": observation}, session, result),
            }
            await _audit_cave_tianjige_read_only(
                identity_id, response, detail=f"首行={observation['first_line'] or '-'}｜摘要={observation['message_digest']}",
            )
            return response
        sync_result = _sync_cave_tianjige_read_only_message(
            identity_id,
            normalized_command,
            message,
            now=now,
        )
        if sync_result.get("supported"):
            if not sync_result.get("handled"):
                final_message = f"洞府天机阁只读回包未匹配现有解析器：{normalized_command}"
                first_line = re.sub(r"\s+", " ", message.splitlines()[0] if message else "").strip()[:120]
                try:
                    console_log(
                        f"📖 {final_message}｜reason={sync_result.get('reason') or 'unknown'}"
                        f"｜first_line={first_line or '-'}",
                        scope="identity",
                        send_as_id=identity_id,
                    )
                except Exception as exc:
                    logging.getLogger(__name__).warning("Cave read-only diagnostic failed (%s)", type(exc).__name__)
                response = {
                    "ok": False,
                    "message": final_message,
                    "extra": _miniapp_result_extra({
                        "command": normalized_command, "raw_message": message,
                        "sync": dict(sync_result.get("summary") or {}),
                        "reason": sync_result.get("reason") or "unparsed_panel",
                    }, session, result),
                }
                await _audit_cave_tianjige_read_only(identity_id, response, detail=str(sync_result.get("detail") or ""))
                return response
            summary = dict(sync_result.get("summary") or {})
            final_message = (
                "洞府天机阁天阶状态已同步（只读，不触发登阶）"
                if normalized_command == CMD_TIANTI_STATUS
                else f"洞府天机阁只读状态已同步：{normalized_command}"
            )
            detail = str(sync_result.get("detail") or "").strip()
            response = {
                "ok": True,
                "message": final_message,
                "extra": _miniapp_result_extra({"command": normalized_command, "sync": summary}, session, result),
            }
            await _audit_cave_tianjige_read_only(identity_id, response, detail=detail, priority="low")
            return response
        return {
            "ok": False, "message": f"洞府天机阁只读命令尚未接入状态同步：{normalized_command}",
            "extra": _miniapp_result_extra({"command": normalized_command, "raw_message": message}, session, result),
        }


async def run_cave_public_stargazer(identity_id, public_entry_url, *, now=None):
    identity_id = _identity_id(identity_id)
    now = float(now or time.time())
    operation = stargazer.StargazerMiniAppOperation.capture(identity_id)
    if identity_id <= 0 or operation is None:
        return {"ok": False, "message": "身份不存在", "extra": {}}
    if not is_cave_public_identity_available(identity_id):
        return {"ok": False, "message": "身份已停用", "extra": {}}
    with use_identity(identity_id):
        if not state.get("stargazer_enabled"):
            return {"ok": False, "message": "观星台模块已关闭", "extra": {}}
    if not _public_entry_allowed():
        return {"ok": False, "message": "全局暂停来源不允许洞府公共入口 MiniApp HTTP", "extra": {}}
    token, webview_url, error = _parse_public_cave_entry_url(public_entry_url)
    if error:
        return {"ok": False, "message": error, "extra": {}}
    observation = _CAVE_PUBLIC_ENTRY_OBSERVATION.get()

    def can_continue():
        return (
            operation.is_current()
            and _public_entry_allowed()
            and (observation is None or observation.permits(identity_id, token))
        )

    cancelled = {"ok": False, "message": "洞府观星台操作已取消或身份已变更", "extra": {"status": "cancelled"}}
    lock = _public_entry_lock(identity_id)
    game_lock = stargazer._stargazer_miniapp_run_lock(identity_id)
    if lock.locked() or game_lock.locked():
        return {"ok": False, "message": "洞府公共入口操作执行中", "extra": {"status": "busy"}}
    async with lock, game_lock:
        if not can_continue():
            return cancelled
        session = await _load_cave_public_identity_session(
            identity_id,
            token,
            webview_url,
            now=now,
            capture_source=f"cave_public_stargazer_start:{identity_id}",
            include_details=True,
            operation_check=can_continue,
        )
        if not can_continue():
            return cancelled
        if not session.get("ok"):
            return {
                "ok": False,
                "message": f"洞府观星台身份读取失败：{session.get('error') or 'unknown'}",
                "extra": _miniapp_result_extra({}, session),
            }
        init_data = session.get("init_data") or ""
        selected_player_id = session.get("player_id")
        cave_result = dict(session.get("result") or {})
        cave_data = dict(cave_result.get("data") or {})
        external_app = _find_stargazer_external_app_in_cave_payload(cave_data.get("raw") or {})
        if not external_app or not external_app.get("available"):
            with use_identity(identity_id):
                state["next_stargazer_panel_time"] = now + 6 * 3600
                state["stargazer_followup_due_at"] = 0
                state["stargazer_queued_action"] = ""
                state["stargazer_last_action"] = "public_entry_unavailable"
                save_state()
            return {
                "ok": True,
                "message": "该身份未开放观星台，已跳过并于 6 小时后复查",
                "extra": {"skipped": "entry_missing"},
            }
        launch = {}
        if external_app.get("action") and str(external_app.get("url") or "").strip() in {"", "#"}:
            external_result = await run_cave_external_action_production_flow(
                identity_id,
                token=token,
                webview_url=webview_url,
                action=external_app["action"],
                player_id=selected_player_id,
                init_data=init_data,
                capture_sink=_capture_store(now),
                capture_source=f"cave_public_stargazer_external:{identity_id}",
                operation_check=can_continue,
            )
            if not can_continue():
                return cancelled
            if not external_result.get("ok"):
                return {
                    "ok": False,
                    "message": f"洞府观星台动态入口获取失败：{external_result.get('error') or external_result.get('status') or 'unknown'}",
                    "extra": _miniapp_result_extra({}, external_result),
                }
            launch = _find_stargazer_launch_in_cave_payload(external_result.get("data") or {})
        elif external_app.get("url"):
            launch = _stargazer_launch_from_external_app(external_app)
        if not launch:
            return {"ok": False, "message": "洞府观星台入口未返回可用 URL", "extra": {}}
        star_choice = operation.star_choice
        capture_sink = stargazer._stargazer_miniapp_capture_store(now)
        capture_source = f"cave_public_stargazer:{identity_id}"
        cancelled_flow = None
        try:
            result = await run_stargazer_miniapp_production_flow(
                identity_id,
                token=launch.get("token"),
                webview_url=launch.get("webview_url"),
                star_choice=star_choice,
                init_data=init_data,
                player_id=selected_player_id,
                capture_sink=capture_sink,
                capture_source=capture_source,
                operation_check=can_continue,
            )
        except MiniAppFlowCancelled as exc:
            cancelled_flow = exc
            result = exc.result if isinstance(exc.result, dict) else {}
        result = dict(result or {})
        confirmed = stargazer.stargazer_miniapp_has_confirmed_outcome(result)
        if not operation.owner.is_current() or (not can_continue() and not confirmed):
            if cancelled_flow is not None:
                raise MiniAppFlowCancelled() from None
            return cancelled
        if cancelled_flow is not None and not confirmed:
            raise MiniAppFlowCancelled(result) from None
        result_data = result.get("data") if isinstance(result.get("data"), dict) else {}
        action_counts = result_data.get("action_counts") if isinstance(result_data.get("action_counts"), dict) else {}
        item_deltas = result_data.get("item_deltas") if isinstance(result_data.get("item_deltas"), dict) else {}
        collect_count = _parse_int(action_counts.get("collect"), 0)
        if collect_count > 0:
            append_business_capture(
                capture_sink,
                adapter_key="stargazer",
                detail={"collect_count": collect_count, "items": item_deltas},
                source=capture_source,
                created_at=now,
            )
        update_schedule = can_continue() and cancelled_flow is None and result.get("status") != "cancelled"
        try:
            with use_identity(identity_id):
                handled = await stargazer._finish_stargazer_miniapp_result(
                    result, now, star_choice=star_choice, update_schedule=update_schedule,
                )
        except MiniAppFlowCancelled as exc:
            cancelled_flow = exc
            handled = True
        if not operation.owner.is_current():
            if cancelled_flow is not None:
                raise MiniAppFlowCancelled() from None
            return cancelled
        response = {
            "ok": bool(handled and result.get("ok")),
            "message": f"洞府观星台：{result.get('status') or ('完成' if handled else '未处理')}",
            "extra": _miniapp_result_extra({
                "title": launch.get("title", ""),
                "status": result.get("status") or "",
                "operation_cancelled": not update_schedule,
                "action_counts": dict(result_data.get("action_counts") or {}),
                "rewards": dict(result_data.get("item_deltas") or {}),
            }, result),
        }
        if cancelled_flow is not None:
            raise MiniAppFlowCancelled(response) from None
        return response


async def run_cave_public_tree(
    identity_id,
    public_entry_url,
    *,
    now=None,
    day_key="",
    op_id="",
    score_profiles=None,
    operation_check=None,
    operation=None,
):
    identity_id = _identity_id(identity_id)
    now = float(now or time.time())
    eligible, reason = tree_runtime.check_tree_miniapp_eligibility(identity_id, enabled=True)
    if not eligible:
        return {"ok": False, "message": reason, "extra": {}}
    if not is_cave_public_identity_available(identity_id):
        return {"ok": False, "message": "身份已停用", "extra": {}}
    if not _public_entry_allowed():
        return {"ok": False, "message": "全局暂停来源不允许洞府公共入口 MiniApp HTTP", "extra": {}}
    token, webview_url, error = _parse_public_cave_entry_url(public_entry_url)
    if error:
        return {"ok": False, "message": error, "extra": {}}
    owns_operation = operation is None
    if owns_operation:
        recovered = tree_runtime.recover_tree_miniapp_local(identity_id)
        if recovered is not None:
            return {"ok": False, "message": "灵树结果已本地恢复", "extra": recovered}
    operation = operation or tree_runtime.TreeMiniAppOperation.daily(
        identity_id, now=now, day_key=day_key, op_id=op_id, score_profiles=score_profiles,
        operation_check=operation_check,
    )
    cancelled = {"ok": False, "message": "洞府灵树操作已取消或身份已变更", "extra": {"status": "cancelled"}}
    if operation is None or operation.owner.identity_id != identity_id:
        return cancelled
    observation = _CAVE_PUBLIC_ENTRY_OBSERVATION.get()

    def can_continue():
        try:
            require_miniapp_operation(operation_check)
        except MiniAppRequestAborted:
            return False
        return (operation.can_dispatch() and _public_entry_allowed()
                and (observation is None or observation.permits(identity_id, token)))

    def entry_failure(message, *sources):
        response = {"ok": False, "message": message, "extra": _miniapp_result_extra({}, *sources)}
        if owns_operation:
            operation.finish({
                "ok": False, "status": str(response["extra"].get("status") or "failed"),
                "error": message, "data": {}, "retry_after_sec": miniapp_retry_after_sec(response),
            }, now=time.time())
        return response

    lock = _public_entry_lock(identity_id)
    if lock.locked():
        return {"ok": False, "message": "洞府公共入口操作执行中", "extra": {}}
    async with lock, operation.execution() as acquired:
        if not acquired or not can_continue():
            return cancelled
        session = await _load_cave_public_identity_session(
            identity_id,
            token,
            webview_url,
            now=now,
            capture_source=f"cave_public_tree_start:{identity_id}",
            include_details=True,
            operation_check=can_continue,
        )
        if not can_continue():
            return cancelled
        if not session.get("ok"):
            return entry_failure(f"洞府灵树身份读取失败：{session.get('error') or 'unknown'}", session)
        cave_result = dict(session.get("result") or {})
        cave_data = dict(cave_result.get("data") or {})
        external_app = _find_tree_external_app_in_cave_payload(cave_data.get("raw") or {})
        if not external_app or not external_app.get("available"):
            return entry_failure("洞府外府未开放落云灵树入口")
        launch = {}
        if external_app.get("action") and str(external_app.get("url") or "").strip() in {"", "#"}:
            external_result = await run_cave_external_action_production_flow(
                identity_id,
                token=token,
                webview_url=webview_url,
                action=external_app["action"],
                player_id=session.get("player_id"),
                init_data=session.get("init_data") or "",
                capture_sink=_capture_store(now),
                capture_source=f"cave_public_tree_external:{identity_id}",
                operation_check=can_continue,
            )
            if not can_continue():
                return cancelled
            if not external_result.get("ok"):
                return entry_failure(
                    f"洞府落云灵树动态入口获取失败：{external_result.get('error') or external_result.get('status') or 'unknown'}",
                    external_result,
                )
            launch = _find_tree_launch_in_cave_payload(external_result.get("data") or {})
        elif external_app.get("url"):
            launch = _tree_launch_from_external_app(external_app)
        if not launch:
            return entry_failure("洞府落云灵树入口未返回可用 URL")
        interrupted = False
        try:
            result = await tree_runtime.run_tree_miniapp_daily_direct(
                identity_id,
                token=launch.get("token"),
                webview_url=launch.get("webview_url"),
                init_data=session.get("init_data") or "",
                day_key=day_key or get_day_key(now),
                op_id=op_id,
                score_profiles=score_profiles,
                now=now,
                operation=operation,
                operation_check=can_continue,
            )
        except MiniAppFlowCancelled as exc:
            interrupted = True
            result = exc.result if isinstance(exc.result, dict) else {}
        if not operation.owner.is_current():
            if interrupted:
                raise MiniAppFlowCancelled() from None
            return cancelled
        response = {
            "ok": bool(result.get("ok")),
            "message": f"洞府落云灵树：{result.get('status') or ('完成' if result.get('ok') else '未完成')}",
            "extra": _miniapp_result_extra({"title": launch.get("title", ""), "result": result}, result),
        }
        if interrupted:
            raise MiniAppFlowCancelled(response) from None
        return response


@dataclass(frozen=True)
class _CaveDeepRetreatOperation:
    owner: MiniAppIdentityOwner
    schedule: dict
    record: dict
    enabled: bool

    @classmethod
    def capture(cls, identity_id):
        owner = MiniAppIdentityOwner.capture(identity_id)
        if owner is None:
            return None
        spec = deep_retreat.DEEP_RETREAT_SPEC
        return cls(
            owner,
            {key: deepcopy(owner.identity.get(key)) for key in (
                spec.phase_key, spec.next_time_key, spec.last_command_key,
                spec.probe_pending_key, spec.summary_sent_at_key, spec.last_summary_msg_id_key,
            )},
            deepcopy(get_miniapp_state_records().get(f"{identity_id}:cave_deep_retreat") or {}),
            bool(owner.identity.get(spec.enabled_key)),
        )

    def result_is_current(self):
        return (
            self.owner.is_current()
            and all(self.owner.identity.get(key) == value for key, value in self.schedule.items())
            and self.record == (get_miniapp_state_records().get(f"{self.owner.identity_id}:cave_deep_retreat") or {})
        )

    def can_dispatch(self):
        return (
            self.result_is_current()
            and is_cave_public_identity_available(self.owner.identity_id)
            and bool(self.owner.identity.get("deep_retreat_enabled")) == self.enabled
            and _public_entry_allowed()
        )


async def _audit_cave_retreat(message, identity_id, response, *, priority="low"):
    try:
        await send_audit_log(
            f"🧘 {message}", scope="identity", send_as_id=identity_id, priority=priority, limit=260,
        )
    except asyncio.CancelledError:
        raise MiniAppFlowCancelled(response) from None
    except Exception as exc:
        logging.getLogger(__name__).warning("Cave retreat audit failed (%s)", type(exc).__name__)


async def run_cave_public_deep_retreat_action(identity_id, public_entry_url, action, *, now=None, operation_check=None):
    identity_id = _identity_id(identity_id)
    now = float(now or time.time())
    action = str(action or "").strip()
    if action not in {"status", "start", "settle", "force"}:
        return {"ok": False, "message": "洞府闭关动作仅允许 status/start/settle/force", "extra": {}}
    operation = _CaveDeepRetreatOperation.capture(identity_id)
    if identity_id <= 0 or operation is None:
        return {"ok": False, "message": "身份不存在", "extra": {}}
    if not is_cave_public_identity_available(identity_id):
        return {"ok": False, "message": "身份已停用", "extra": {}}
    if not _public_entry_allowed():
        return {"ok": False, "message": "全局暂停来源不允许洞府公共入口 MiniApp HTTP", "extra": {}}
    token, webview_url, error = _parse_public_cave_entry_url(public_entry_url)
    if error:
        return {"ok": False, "message": error, "extra": {}}
    observation = _CAVE_PUBLIC_ENTRY_OBSERVATION.get()
    cancelled = {"ok": False, "message": "洞府闭关操作已取消或身份状态已变更", "extra": {"status": "cancelled"}}

    def can_continue():
        return (
            operation.can_dispatch()
            and (operation_check is None or operation_check() is True)
            and (observation is None or observation.permits(identity_id, token))
        )

    lock = _public_entry_lock(identity_id)
    if lock.locked():
        return {"ok": False, "message": "洞府公共入口操作执行中", "extra": {}}
    async with lock:
        if not can_continue():
            return cancelled
        try:
            session = await _load_cave_public_identity_session(
                identity_id,
                token,
                webview_url,
                now=now,
                capture_source=f"cave_public_deep_retreat_start:{identity_id}",
                operation_check=can_continue,
            )
        except MiniAppFlowCancelled:
            raise MiniAppFlowCancelled(cancelled) from None
        if not can_continue():
            return cancelled
        if not session.get("ok"):
            message = f"洞府闭关身份读取失败：{session.get('error') or 'unknown'}"
            _record_cave_deep_retreat_state(
                identity_id,
                action,
                {"ok": False, "status": "session_failed", "error": session.get("error") or "unknown", "data": {}, "action_dispatched": False},
                {"handled": False, "reason": "session_failed", "phase": ""},
                now=now,
            )
            response = {"ok": False, "message": message, "extra": _miniapp_result_extra({}, session)}
            await _audit_cave_retreat(message, identity_id, response, priority="normal")
            return response
        cancelled_flow = None
        try:
            outcome = await _run_cave_public_deep_action_locked(
                identity_id, token=token, webview_url=webview_url, action=action,
                session=session, init_data=session.get("init_data") or "", now=now,
                capture_sink=_capture_store(now), capture_source=f"cave_public_deep_retreat:{identity_id}",
                operation=operation, operation_check=can_continue,
            )
        except MiniAppFlowCancelled as exc:
            cancelled_flow = exc
            outcome = exc.result if isinstance(exc.result, dict) else {"status": "cancelled"}
        if outcome.get("status") == "cancelled":
            if cancelled_flow is not None:
                raise MiniAppFlowCancelled(cancelled) from None
            return cancelled
        sync_result = outcome.get("sync") or {}
        skipped = outcome.get("status") == "preflight_skip"
        if skipped:
            if sync_result.get("phase") == "running":
                message = f"洞府闭关仍在进行，已跳过 {action}"
            else:
                message = f"洞府闭关未取得 {action} 许可或上次结果待核实，已跳过动作"
                if not sync_result.get("handled"):
                    message += "｜30 分钟后查状态"
        elif not outcome.get("ok"):
            message = f"洞府闭关 {action} 未确认：{outcome.get('error') or outcome.get('status') or 'unknown'}"
            if sync_result.get("phase") == "launching":
                message += "｜30 分钟后保守复查"
        else:
            message = f"洞府闭关 {action} 完成：已同步｜阶段 {sync_result.get('phase') or '-'}"
        response = {
            "ok": bool(outcome.get("ok")),
            "message": message,
            "extra": _miniapp_result_extra(
                {
                    "record_key": (outcome.get("record") or {}).get("record_key", ""), "sync": sync_result,
                    "status": outcome.get("status") or "", "acted": bool(outcome.get("sent")),
                    "action_skipped": skipped, "settle_skipped": skipped and action == "settle",
                    "outcome_unknown": bool(outcome.get("outcome_unknown")),
                    **({"retry_after_sec": outcome["retry_after_sec"]} if outcome.get("retry_after_sec") else {}),
                },
                outcome.get("result") or {},
            ),
        }
        if cancelled_flow is not None:
            raise MiniAppFlowCancelled(response) from None
        await _audit_cave_retreat(message, identity_id, response)
        return response


async def handle_cave_treasure_miniapp_entry(event, text, now, reply_to=None, matched_family=None, result_msg_id=0, require_identity_match=False):
    identity_id = _identity_id()
    owner = MiniAppIdentityOwner.capture(identity_id)
    authorization = _MANUAL_AUTH_UNTIL.get(identity_id)
    launch = extract_cave_treasure_miniapp_launch(event, message_text=text)
    if not launch:
        return False
    if owner is not None and (not require_identity_match or _entry_mentions_current_identity(text)):
        recovered = recover_cave_treasure_result(identity_id)
        if recovered is not None and not treasure_operations.resume_allowed(identity_id):
            if _MANUAL_AUTH_UNTIL.get(identity_id) == authorization:
                revoke_cave_treasure_miniapp_manual_run(identity_id)
            return True
    await capture_cave_public_entry_event(event, text)
    if (owner is None or not owner.is_current() or authorization is None
            or _MANUAL_AUTH_UNTIL.get(identity_id) != authorization or not _has_manual_auth(identity_id, now)):
        return False
    if require_identity_match and not _entry_mentions_current_identity(text):
        return False
    global_enabled = get_global_enabled()
    maintenance_miniapp_allowed = _miniapp_http_allowed_during_pause()
    identity_available = is_cave_public_identity_available(identity_id)
    if (not global_enabled and not maintenance_miniapp_allowed) or not identity_available:
        revoke_cave_treasure_miniapp_manual_run(identity_id)
        reason = "全局暂停" if not global_enabled else "身份已停用"
        await send_audit_log(f"🕳️ 洞府寻宝 MiniApp {reason}，已跳过 WebView/HTTP 接管。", scope="identity", limit=180)
        return True

    identity_error = _public_entry_account_identity_error(identity_id)
    if identity_error:
        revoke_cave_treasure_miniapp_manual_run(identity_id)
        await _audit_cave_treasure(owner, {"ok": False, "message": identity_error, "extra": {}})
        return True

    def can_continue():
        return (owner.is_current() and is_cave_public_identity_available(identity_id)
                and _public_entry_allowed() and not _public_entry_account_identity_error(identity_id))

    lock = _public_entry_lock(identity_id)
    game_lock = _run_lock(identity_id)
    if lock.locked() or game_lock.locked():
        await _audit_cave_treasure(owner, {"ok": False, "message": "洞府寻宝 MiniApp 已在执行，重复入口忽略。",
                                          "extra": {"status": "busy"}})
        return True

    async with lock, game_lock:
        if not can_continue():
            return True
        revoke_cave_treasure_miniapp_manual_run(identity_id)
        if _cave_treasure_unknown_hold(identity_id, now) and not treasure_operations.resume_allowed(identity_id):
            await _audit_cave_treasure(owner, _cave_treasure_unknown_response())
            return True
        await _audit_cave_treasure(owner, {
            "ok": False,
            "message": "洞府寻宝 MiniApp 接管入口，开始 WebView/HTTP 流程。"
                       + ("（天尊维护暂停中，仅执行 MiniApp HTTP）" if maintenance_miniapp_allowed else ""),
            "extra": {},
        }, priority="low")
        capture_source = f"cave_treasure_runtime:{identity_id}:{int(result_msg_id or getattr(event, 'id', 0) or 0)}"
        await _run_owned_cave_treasure(
            owner, launch.get("token"), launch.get("webview_url"), now=now,
            capture_source=capture_source, operation_check=can_continue,
            result_msg_id=int(result_msg_id or getattr(event, "id", 0) or 0),
        )
        return True


__all__ = [
    "CAVE_TREASURE_MANUAL_AUTH_TTL_SEC",
    "CAVE_TREASURE_MANUAL_MAX_STEPS",
    "authorize_cave_treasure_miniapp_manual_run",
    "extract_cave_deep_seclusion_action_message",
    "extract_cave_deep_seclusion_state",
    "extract_cave_tianjige_command_message",
    "handle_cave_treasure_miniapp_entry",
    "is_cave_public_entry_busy",
    "is_cave_treasure_busy",
    "recover_cave_treasure_result",
    "apply_cave_inventory_snapshot",
    "revoke_cave_treasure_miniapp_manual_run",
    "run_cave_public_deep_retreat_action",
    "run_cave_public_fate_cards",
    "run_cave_public_fishing",
    "run_cave_public_inventory",
    "run_cave_public_small_world_sync",
    "run_cave_public_stargazer",
    "run_cave_public_tianjige_read_only",
    "run_cave_public_tianti_status",
    "run_cave_public_tower",
    "run_cave_public_treasure",
    "run_cave_public_trial",
    "run_cave_public_wild_training",
    "run_cave_public_yuanying",
    "sync_cave_deep_seclusion_action_result",
    "sync_cave_tianjige_yuanying_result",
    "_find_trial_launch_in_cave_payload",
    "_find_tower_external_app_in_cave_payload",
    "_find_tower_launch_in_cave_payload",
    "_cave_treasure_inventory_items",
    "_record_cave_deep_retreat_state",
    "_record_cave_small_world_state",
    "_record_cave_treasure_miniapp_state",
]
