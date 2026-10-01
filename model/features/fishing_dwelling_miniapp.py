"""One native fishing round, using the shared HTTP budget and durable journal."""

from copy import deepcopy
import time

from ..webapp_core import (
    MiniAppAdapter, MiniAppRequestBudget, MiniAppRequestPolicy,
    build_miniapp_http_request, execute_miniapp_http_request, sanitize_webapp_secret_text,
)
from . import fishing_dwelling_protocol as protocol
from .fishing_dwelling_journal import settlement_complete


API_PATH = "/api/miniapp/xianxia-dwelling/fishing/"
ENDPOINTS = ("context", "state", "cast", "hook", "checkpoint", "fight", "buy-bait", "chum")


def build_adapter():
    return MiniAppAdapter(
        game_key="fishing", label="Dwelling fishing", api_base_url="https://asc.aiopenai.app",
        allowed_api_hosts=("asc.aiopenai.app",), allowed_api_paths=(API_PATH,),
        endpoints={name: API_PATH + name for name in ENDPOINTS},
        request_policy=MiniAppRequestPolicy(min_interval_sec=.7, max_requests_per_run=96, max_attempts_per_request=1),
    )


class _RequestFailed(Exception):
    def __init__(self, result):
        self.result = result


def run_native_fishing_flow(*, journal, token, init_data, site_id, model_id, bait_id,
                            transport, operation_check, monotonic=time.monotonic, sleeper=time.sleep,
                            capture_sink=None, capture_source="", request_budget=None, basis_provider=None, bait_choice="",
                            supply_journal=None, supply_settings=None, recovery_only=False, accept_recovery=None):
    """One supply or cast, without blind retries, next cast or legacy fallback.

    The production caller must hold its public-entry/fishing locks, supply a
    verified selected player and drain this blocking worker on cancellation.
    Only a durable receipt is returned; inventory projection belongs to the caller.
    """
    adapter = build_adapter()
    budget = request_budget or MiniAppRequestBudget(adapter.request_policy, clock=monotonic, sleeper=sleeper)
    events, context = [], None
    retry_after = 0
    settlement_read = False

    def finish(status, error=""):
        record = journal.record
        settled = bool(record and record["phase"] == "settled")
        supply = supply_journal.record if supply_journal is not None else {}
        return {"ok": settled and status == "settled", "status": status,
                "error": sanitize_webapp_secret_text(str(error)), "retry_after_sec": retry_after,
                "data": {"settled_count": 1 if settled else 0,
                         "catches": deepcopy(record["catches"]) if settled else {},
                         "rewards": deepcopy((record.get("settlement_resources") or {}).get("rewards", {})) if settled else {},
                         "context": deepcopy(context), "supply_action": supply.get("action", "")},
                "outcome_unknown": bool(record and record["pending_action"] or supply.get("phase") == "pending"), "events": events}

    def check():
        journal._check()
        if supply_journal is not None:
            supply_journal._check()
        return operation_check() is True

    def wait(delay):
        start = monotonic()
        if not check():
            raise protocol.ProtocolError("native_operation_cancelled")
        # Main-loop receipt checks consume real time; do not add that time to
        # the already-computed bite wait and miss the server's short window.
        sleeper(max(0.0, delay - (monotonic() - start)))
        if not check():
            raise protocol.ProtocolError("native_operation_cancelled")
        if monotonic() - start + 1e-9 < delay:
            raise protocol.ProtocolError("native_wait_incomplete")

    def request(action, payload, query=None, *, hook_clock=None, supply_expected=None):
        nonlocal context, retry_after
        expected = deepcopy(journal.record)
        before_read = basis_provider() if action == "state" and accept_recovery is not None else None

        def current():
            if (not check() or expected != journal.record
                    or supply_expected is not None and supply_expected != supply_journal.record):
                return False
            return hook_clock is None or protocol.next_session_action(
                expected["remote"], hook_clock, monotonic())[0] == "hook"

        request = build_miniapp_http_request(
            adapter, action, {**payload, "token": token, "playerId": journal.owner[2]}, init_data=init_data,
        )
        start = monotonic()

        def timed_transport(request):
            nonlocal start
            # Queue/rate-limit waits are not network RTT.
            start = monotonic()
            return transport(request)

        result = execute_miniapp_http_request(
            request, timed_transport, retry_safe=False, sleeper=wait, request_budget=budget,
            operation_check=current, capture_sink=capture_sink, capture_source=capture_source, step_key=action,
        )
        received = monotonic()
        events.append({"action": action, "ok": result.ok, "status_code": result.status_code,
                       "error_type": result.error_type, "attempts": result.attempts})
        retry_after = max(retry_after, float(result.retry_after_sec or 0))
        if not result.ok:
            if (supply_expected is not None and result.attempts == 0
                    and result.error_type in {"preparation", "request_budget", "operation_cancelled"}):
                supply_journal.cancel_undispatched(supply_expected)
            if (query is not None and query.action != "state" and result.attempts == 0
                    and result.error_type in {"preparation", "request_budget", "operation_cancelled"}):
                journal.cancel_undispatched(query)
            raise _RequestFailed(result)
        data = result.data
        if supply_expected is not None:
            supply_journal.accept(supply_expected, data)
        if query is not None:
            # Persist a confirmed response even if the UI toggled automation off
            # while HTTP was in flight. Owner replacement still rejects it.
            parsed = (accept_recovery(query, data, before_read)
                      if action == "state" and accept_recovery is not None else journal.accept(query, data))
        else:
            parsed = None
        if isinstance(data, dict) and data.get("context") is not None:
            context = protocol.parse_context(data)
        return data, parsed, start, received

    try:
        if not check():
            return finish("cancelled")
        if supply_journal is not None and supply_journal.record:
            phase = supply_journal.record["phase"]
            if phase != "accounted":
                return finish("supplied" if phase == "confirmed" else "supply_pending")
        if journal.record == {} or journal.record["phase"] == "accounted":
            if recovery_only:
                return finish("recovery_not_needed")
            initial, _, _, _ = request("context", {"siteId": site_id})
            if not check():
                return finish("cancelled")
            if bait_choice:
                selected = next((row for row in context["baits"] if bait_choice in (row["name"], row["itemId"])), None)
                if selected is None:
                    raise protocol.ProtocolError("fishing_bait_unavailable")
                bait_id = selected["itemId"]
            if supply_settings is not None:
                if supply_journal is None:
                    raise protocol.ProtocolError("native_supply_store_missing")
                action, expected_supply, body = supply_journal.prepare(
                    context=initial, site_id=site_id, model_id=model_id,
                    basis=basis_provider()["inventory"] if basis_provider is not None else "", **supply_settings,
                )
                if action:
                    request(action, body, supply_expected=expected_supply)
                    return finish("supplied")
            query, body = journal.start(context=initial, site_id=site_id, model_id=model_id, bait_id=bait_id,
                                       projection_basis=basis_provider() if basis_provider is not None else None)
            data, parsed, start, received = request("cast", body, query)
        else:
            if not recovery_only and journal.record["phase"] == "settled" and settlement_complete(journal.record):
                return finish("settled")
            settlement_read = journal.record["phase"] == "settled"
            query, body = journal.recovery()
            data, parsed, start, received = request("state", body, query)
        for _ in range(8):
            if journal.record["phase"] == "settled":
                if not settlement_complete(journal.record):
                    if settlement_read or not check():
                        return finish("settlement_context_pending")
                    settlement_read = True
                    query, body = journal.recovery()
                    data, parsed, start, received = request("state", body, query)
                    continue
                return finish("settled")
            if journal.record["pending_action"]:
                return finish("operation_pending")
            if recovery_only:
                return finish("recovery_wait")
            if not check():
                return finish("cancelled")
            phase = parsed["phase"]
            if phase in ("casting", "waiting", "bite"):
                clock = protocol.ServerClock.capture(parsed["serverNow"], start, received)
                action, delay = protocol.next_session_action(parsed, clock, monotonic())
                if action == "state" or delay > 75:
                    return finish("wait_state" if action == "state" else "wait_bite")
                if delay > 0:
                    wait(delay)
                query, body = journal.prepare("hook", clock=clock, monotonic_now=monotonic())
                data, parsed, start, received = request("hook", body, query, hook_clock=clock)
            elif phase == "fighting":
                challenge = deepcopy(parsed["fight"])
                checkpoint_not_before = None
                for final, proof, details in protocol.timed_fight_steps(
                        challenge, is_current=check, monotonic=monotonic, sleeper=wait):
                    action = "fight" if final else "checkpoint"
                    if not final and checkpoint_not_before is not None:
                        delay = checkpoint_not_before - monotonic()
                        if delay > 0:
                            wait(delay)
                    query, body = journal.prepare(action, proof=proof, details=details)
                    data, parsed, start, received = request(action, body, query)
                    if journal.record["pending_action"] or parsed["phase"] != "fighting":
                        break
                    if not final:
                        # Keep response jitter from compressing consecutive
                        # checkpoints below the challenge's declared interval.
                        checkpoint_not_before = received + challenge.get("checkpointIntervalMs", 2500) / 1000
            elif phase == "settling":
                wait(2.5)
                query, body = journal.recovery()
                data, parsed, start, received = request("state", body, query)
            else:
                raise protocol.ProtocolError("native_unhandled_phase")
        return finish("wait_state")
    except _RequestFailed as exc:
        return finish("supply_pending" if supply_journal is not None and supply_journal.record.get("phase") == "pending"
                      else "operation_pending" if journal.record and journal.record["pending_action"] else "request_failed",
                      exc.result.error)
    except Exception as exc:
        return finish("supply_pending" if supply_journal is not None and supply_journal.record.get("phase") == "pending"
                      else "operation_pending" if journal.record and journal.record["pending_action"] else "blocked", exc)


async def run_native_fishing_production_flow(identity_id, *, player_id, token, init_data,
                                             site_id, model_id, bait_id, operation_check,
                                             transport=None, sleeper=None, monotonic=time.monotonic,
                                             capture_sink=None, capture_source="", bait_choice="", supply_settings=None,
                                             update_schedule=True, recovery_only=False):
    """Caller supplies verified entry ownership and holds the fishing/public locks."""
    from .fishing_dwelling_store import NativeFishingStore
    from .miniapp_common import MiniAppFlowCancelled, build_pooled_miniapp_transport, run_miniapp_blocking_flow

    store = NativeFishingStore(identity_id, player_id)
    cancelled = None

    def worker(operation):
        return run_native_fishing_flow(
            journal=store.journal(), token=token, init_data=init_data, site_id=site_id,
            model_id=model_id, bait_id=bait_id, basis_provider=store.basis,
            transport=transport or build_pooled_miniapp_transport(
                adapter_key="fishing", identity_id=identity_id, operation_check=operation.check),
            operation_check=operation.check, monotonic=monotonic, sleeper=operation.sleep,
            capture_sink=capture_sink, capture_source=capture_source,
            bait_choice=bait_choice,
            supply_journal=store.supply_journal(), supply_settings=supply_settings,
            recovery_only=recovery_only,
            accept_recovery=store.accept_recovery,
        )

    try:
        result = await run_miniapp_blocking_flow(worker, operation_check=operation_check, sleeper=sleeper)
    except MiniAppFlowCancelled as exc:
        cancelled = exc
        result = exc.result if isinstance(exc.result, dict) else {"ok": False, "status": "cancelled", "data": {}}
    except Exception as exc:
        result = {"ok": False, "status": "blocked", "error": sanitize_webapp_secret_text(str(exc)), "data": {}}
    try:
        supply_record = store.read_supply()
        if supply_record and supply_record["phase"] == "confirmed":
            store.project_supply()
            result["supply_committed"] = True
        record = store.read()
        if record and record["phase"] == "settled" and settlement_complete(record):
            store.project(time.time(), update_schedule=update_schedule and cancelled is None and operation_check() is True)
            result["committed"] = True
    except Exception as exc:
        result.update(ok=False, status="persistence_pending", error=sanitize_webapp_secret_text(str(exc)), committed=False)
    finally:
        store.closed = True
    if cancelled is not None:
        raise MiniAppFlowCancelled(result) from None
    return result
