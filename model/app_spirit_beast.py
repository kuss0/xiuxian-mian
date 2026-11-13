"""Single-owner scheduler for the upstream spirit-beast state machine."""
import asyncio
import copy
import time

from .activity_occupancy import get_activity_block_reason
from .features import spirit_beast as beast
from .features import cave_treasure_miniapp as dwelling
from .features import cave_treasure_runtime as cave
from .features.changfeng_trial import _entry_url
from .features.miniapp_common import MiniAppIdentityOwner, build_pooled_miniapp_transport, run_miniapp_blocking_flow
from .features.spirit_beast_miniapp import build_spirit_beast_miniapp_adapter
from .persistence import mark_dirty, save_state
from .runtime import track_background_task
from .state import get_expedition_config, get_expedition_run_state, get_global_enabled, is_cave_public_identity_available
from .webapp_core import MiniAppRequestBudget, MiniAppRequestPolicy, build_miniapp_http_request, build_miniapp_launch_request, execute_miniapp_http_request, require_miniapp_operation

_TASKS = {}


def records():
    return get_expedition_run_state().setdefault("spirit_beast", {})


def save():
    mark_dirty()
    if save_state() is False:
        raise beast.SpiritBeastMiniappError("spirit_beast_state_save_failed")


async def run_protocol(call, record, now):
    """Persist before mutation. Once a result is unknown, no further mutation is allowed."""
    async def request(path, token, init_data, **payload):
        if record.get("pending"):
            raise beast.SpiritBeastMiniappError("spirit_beast_request_unconfirmed")
        mutation = path != beast.SPIRIT_BEAST_API_START
        if mutation:
            # Never persist token, initData or runToken.
            record["pending"] = {"path": path, "at": time.time(),
                                 "payload": {k: v for k, v in payload.items() if k != "runToken"}}
            save()
        response = await call(path, token, init_data, **payload)
        if not isinstance(response, dict) or type(response.get("ok")) is not bool:
            raise beast.SpiritBeastMiniappError("spirit_beast_response_missing")
        if mutation and response["ok"]:
            if path == beast.SPIRIT_BEAST_API_ACTION:
                valid = any(b.get("id") == payload.get("beastId") for b in response.get("beasts", []) if isinstance(b, dict))
            elif path == beast.SPIRIT_BEAST_API_ABYSS_ENTER:
                valid = isinstance(response.get("result"), dict) and bool(response["result"])
            elif path == beast.SPIRIT_BEAST_API_EXPEDITION_START:
                valid = isinstance(response.get("expedition"), dict) and bool(response["expedition"].get("runToken"))
            else:
                run = response.get("expedition")
                valid = (isinstance(run, dict) and bool(run.get("runToken")) and type(run.get("seq")) is int
                         and run["seq"] > payload.get("seq", -1) or bool(response.get("outcome"))
                         and not run and (bool(response.get("history")) or bool(response.get("reward"))))
            if not valid:
                raise beast.SpiritBeastMiniappError("spirit_beast_mutation_receipt_missing")
        if mutation:
            pending = record.pop("pending")
            try:
                save()
            except BaseException:
                record.update(pending=pending, phase="held")
                raise
        if not response["ok"]:
            raise beast.SpiritBeastMiniappError(response.get("error") or "spirit_beast_rejected", payload=response)
        return response

    state_token = beast._RECORD.set(record)
    call_token = beast._CALL.set(request)
    try:
        initial = await request(beast.SPIRIT_BEAST_API_START, "", "")
        if str((initial.get("player") or {}).get("sect") or "").strip() != "万灵宗":
            record.update(phase="sect_blocked", next_at=now + 86400)
            save()
            return False
        await beast._run_spirit_beast_flow("", "", initial, now)
        record.update(phase="idle", next_at=record.get("next_spirit_beast_time", now + 1800), last_error="")
        save()
        return True
    finally:
        beast._CALL.reset(call_token)
        beast._RECORD.reset(state_token)


async def run_once(identity_id):
    config = copy.deepcopy(get_expedition_config().get("spirit_beast", {}))
    owner = MiniAppIdentityOwner.capture(identity_id)
    if (owner is None or not config.get("enabled") or identity_id not in config.get("participant_identity_ids", [])
            or not get_global_enabled() or not is_cave_public_identity_available(identity_id)
            or get_activity_block_reason(identity_id)):
        return
    lock = cave._public_entry_lock(identity_id)
    if lock.locked():
        return
    record = records().setdefault(str(identity_id), {})
    if record.get("phase") in {"held", "running"} or record.get("pending"):
        return
    entry = _entry_url()

    def allowed():
        return (owner.is_current() and get_global_enabled() and is_cave_public_identity_available(identity_id)
                and config == get_expedition_config().get("spirit_beast", {}) and entry == _entry_url()
                and not get_activity_block_reason(identity_id) and records().get(str(identity_id)) is record)

    async with lock:
        now = time.time()
        record.update(phase="running", identity_id=identity_id, started_at=now)
        save()
        try:
            token, url, error = cave._parse_public_cave_entry_url(entry)
            if error:
                raise ValueError("洞府公共入口不可用")
            session = await cave._load_cave_public_identity_session(identity_id, token, url, now=now,
                capture_source=f"spirit_beast:{identity_id}", include_details=False, operation_check=allowed)
            if not session.get("ok"):
                raise ValueError("洞府身份会话不可用")
            raw = (((session.get("result") or {}).get("data") or {}).get("raw") or {})
            external = dwelling.find_cave_external_app(raw, keys=("spirit_beast",), title_terms=("驭灵行迹",))
            if not external or not dwelling._external_app_available(external):
                raise ValueError("驭灵行迹入口不可用")
            launch_url = external.get("url") or ""
            if external.get("action"):
                result = await dwelling.run_cave_external_action_production_flow(identity_id,
                    token=token, webview_url=url, action=external["action"], player_id=session["player_id"],
                    init_data=session["init_data"], operation_check=allowed)
                if not result.get("ok"):
                    raise ValueError("驭灵动态入口不可用")
                launch_url = find_launch_url(result.get("data") or {})
            adapter = build_spirit_beast_miniapp_adapter()
            launch = build_miniapp_launch_request(adapter, launch_url)
            if not launch.allowed or not launch.start_param:
                raise ValueError("驭灵入口校验失败")
            transport = build_pooled_miniapp_transport(adapter_key="spirit_beast", identity_id=identity_id, operation_check=allowed)
            budget = MiniAppRequestBudget(MiniAppRequestPolicy(min_interval_sec=1, max_requests_per_run=80, max_attempts_per_request=1))

            async def call(path, _token, _init_data, **payload):
                require_miniapp_operation(allowed)
                request = build_miniapp_http_request(adapter, path, {"token": launch.start_param, **payload}, init_data=session["init_data"])
                result = await run_miniapp_blocking_flow(lambda operation: execute_miniapp_http_request(
                    request, transport, request_budget=budget, operation_check=operation.check, sleeper=operation.sleep,
                    capture_sink=cave._capture_store(now), capture_source=f"spirit_beast:{identity_id}", step_key=path.rsplit("/", 1)[-1]), operation_check=allowed)
                require_miniapp_operation(allowed)
                if not isinstance(result.data, dict) or not (result.ok or result.data.get("ok") is False):
                    raise beast.SpiritBeastMiniappError("spirit_beast_request_unconfirmed")
                return result.data

            await run_protocol(call, record, now)
        except (Exception, asyncio.CancelledError) as exc:
            record.update(phase="held" if record.get("pending") else "idle", next_at=time.time() + 1800,
                          last_error=type(exc).__name__)
            save()
            if isinstance(exc, asyncio.CancelledError):
                raise


def find_launch_url(payload):
    adapter = build_spirit_beast_miniapp_adapter()
    for item in cave._iter_dicts(payload):
        url = str(item.get("url") or item.get("webviewUrl") or item.get("webview_url") or "")
        if url.startswith("/"):
            url = "https://asc.aiopenai.app" + url
        launch = build_miniapp_launch_request(adapter, url)
        if launch.allowed and launch.start_param:
            return url
    return ""


async def scheduler(now):
    config = get_expedition_config().get("spirit_beast", {})
    if not config.get("enabled") or not get_global_enabled() or _TASKS or not _entry_url():
        return
    for identity in config.get("participant_identity_ids", []):
        record = records().get(str(identity), {})
        if record.get("phase") in {"held", "running"} or record.get("pending") or float(record.get("next_at") or 0) > now:
            continue
        task = track_background_task(asyncio.create_task(run_once(identity)))
        _TASKS[identity] = task
        task.add_done_callback(lambda _task, key=identity: _TASKS.pop(key, None))
        return


def reset_on_startup():
    for record in records().values():
        if record.get("pending") or record.get("phase") == "running":
            record.update(phase="held", last_error="重启前流程未结束，保留记录待核对")
            mark_dirty()
