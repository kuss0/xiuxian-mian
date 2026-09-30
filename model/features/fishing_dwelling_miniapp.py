"""One native fishing round, using the shared HTTP budget and durable journal."""

from copy import deepcopy
import time

from ..webapp_core import (
    MiniAppAdapter, MiniAppRequestBudget, MiniAppRequestPolicy,
    build_miniapp_http_request, execute_miniapp_http_request, sanitize_webapp_secret_text,
)
from . import fishing_dwelling_protocol as protocol


API_PATH = "/api/miniapp/xianxia-dwelling/fishing/"
ENDPOINTS = ("context", "state", "cast", "hook", "checkpoint", "fight")


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
                            capture_sink=None, capture_source="", request_budget=None, basis_provider=None, bait_choice=""):
    """No blind retries, purchases, next cast or legacy fallback.

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
        return {"ok": settled and status == "settled", "status": status,
                "error": sanitize_webapp_secret_text(str(error)), "retry_after_sec": retry_after,
                "data": {"settled_count": 1 if settled else 0,
                         "catches": deepcopy(record["catches"]) if settled else {},
                         "context": deepcopy(context)},
                "outcome_unknown": bool(record and record["pending_action"]), "events": events}

    def check():
        journal._check()
        return operation_check() is True

    def wait(delay):
        if not check():
            raise protocol.ProtocolError("native_operation_cancelled")
        start = monotonic()
        sleeper(delay)
        if not check():
            raise protocol.ProtocolError("native_operation_cancelled")
        if monotonic() - start + 1e-9 < delay:
            raise protocol.ProtocolError("native_wait_incomplete")

    def request(action, payload, query=None, *, hook_clock=None):
        nonlocal context, retry_after
        expected = deepcopy(journal.record)

        def current():
            if not check() or expected != journal.record:
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
            if (query is not None and query.action != "state" and result.attempts == 0
                    and result.error_type in {"preparation", "request_budget", "operation_cancelled"}):
                journal.cancel_undispatched(query)
            raise _RequestFailed(result)
        data = result.data
        if query is not None:
            # Persist a confirmed response even if the UI toggled automation off
            # while HTTP was in flight. Owner replacement still rejects it.
            parsed = journal.accept(query, data)
        else:
            parsed = None
        if isinstance(data, dict) and data.get("context") is not None:
            context = protocol.parse_context(data)
        return data, parsed, start, received

    try:
        if not check():
            return finish("cancelled")
        if journal.record == {} or journal.record["phase"] == "accounted":
            initial, _, _, _ = request("context", {"siteId": site_id})
            if not check():
                return finish("cancelled")
            if bait_choice:
                selected = next((row for row in context["baits"] if bait_choice in (row["name"], row["itemId"])), None)
                if selected is None:
                    raise protocol.ProtocolError("fishing_bait_unavailable")
                bait_id = selected["itemId"]
            query, body = journal.start(context=initial, site_id=site_id, model_id=model_id, bait_id=bait_id,
                                       projection_basis=basis_provider() if basis_provider is not None else None)
            data, parsed, start, received = request("cast", body, query)
        else:
            if journal.record["phase"] == "settled" and journal.record["settlement_quota"] is not None:
                return finish("settled")
            settlement_read = journal.record["phase"] == "settled"
            query, body = journal.recovery()
            data, parsed, start, received = request("state", body, query)
        for _ in range(8):
            if journal.record["phase"] == "settled":
                if journal.record["settlement_quota"] is None:
                    if settlement_read or not check():
                        return finish("settlement_context_pending")
                    settlement_read = True
                    query, body = journal.recovery()
                    data, parsed, start, received = request("state", body, query)
                    continue
                return finish("settled")
            if journal.record["pending_action"]:
                return finish("operation_pending")
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
                for final, proof, details in protocol.timed_fight_steps(
                        challenge, is_current=check, monotonic=monotonic, sleeper=wait):
                    action = "fight" if final else "checkpoint"
                    query, body = journal.prepare(action, proof=proof, details=details)
                    data, parsed, start, received = request(action, body, query)
                    if journal.record["pending_action"] or parsed["phase"] != "fighting":
                        break
            elif phase == "settling":
                wait(2.5)
                query, body = journal.recovery()
                data, parsed, start, received = request("state", body, query)
            else:
                raise protocol.ProtocolError("native_unhandled_phase")
        return finish("wait_state")
    except _RequestFailed as exc:
        return finish("operation_pending" if journal.record and journal.record["pending_action"] else "request_failed",
                      exc.result.error)
    except Exception as exc:
        return finish("operation_pending" if journal.record and journal.record["pending_action"] else "blocked", exc)


async def run_native_fishing_production_flow(identity_id, *, player_id, token, init_data,
                                             site_id, model_id, bait_id, operation_check,
                                             transport=None, sleeper=None, monotonic=time.monotonic,
                                             capture_sink=None, capture_source="", bait_choice=""):
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
        )

    try:
        result = await run_miniapp_blocking_flow(worker, operation_check=operation_check, sleeper=sleeper)
    except MiniAppFlowCancelled as exc:
        cancelled = exc
        result = exc.result if isinstance(exc.result, dict) else {"ok": False, "status": "cancelled", "data": {}}
    except Exception as exc:
        result = {"ok": False, "status": "blocked", "error": sanitize_webapp_secret_text(str(exc)), "data": {}}
    try:
        record = store.read()
        if record and record["phase"] == "settled" and record["settlement_quota"] is not None:
            store.project(time.time(), update_schedule=cancelled is None and operation_check() is True)
            result["committed"] = True
        elif record and record["phase"] == "accounted":
            result["committed"] = True
    except Exception as exc:
        result.update(ok=False, status="persistence_pending", error=sanitize_webapp_secret_text(str(exc)), committed=False)
    finally:
        store.closed = True
    if cancelled is not None:
        raise MiniAppFlowCancelled(result) from None
    return result
