import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
import requests

from model.features import world_boss_miniapp as wb
from model.features import world_boss_turnstile as ts
from model.webapp_core import safe_miniapp_event_detail, sanitize_webapp_secret_text


def env(**overrides):
    return {"WORLD_BOSS_TURNSTILE_ENABLED": "true", "TURNSTILE_BROKER_SECRET": "test-secret", **overrides}


def solve(client):
    return client.solve(
        identity_id=77,
        account_id=88,
        entry_token="qyz_test_entry",
        init_data="test-init",
        challenge_id="challenge",
    )


def response(**overrides):
    return {"ok": True, "turnstileToken": "test-proof", "turnstileIdempotencyKey": "test-key",
            "expiresAt": time.time() + 240, **overrides}


class Session:
    calls = []
    body = response()
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs, self.trust_env))
        return SimpleNamespace(status_code=self.status, json=lambda: self.body)


@pytest.fixture(autouse=True)
def reset():
    Session.calls = []
    Session.body = response()
    Session.status = 200


def test_default_and_identity_filter(monkeypatch):
    assert not ts.TurnstileBrokerClient({}).enabled_for(77)
    client = ts.TurnstileBrokerClient(env(WORLD_BOSS_TURNSTILE_ONLY_IDENTITY="77"))
    assert client.enabled_for(77)
    assert not client.enabled_for(78)
    monkeypatch.setenv("WORLD_BOSS_TURNSTILE_ENABLED", "false")
    assert ts.turnstile_provider_for(77, 88, enabled=False) is None


def test_wire_contract_and_no_proxy_redirect():
    assert solve(ts.TurnstileBrokerClient(env(), Session))["turnstile_token"] == "test-proof"
    url, args, trust_env = Session.calls[0]
    assert url == "http://127.0.0.1:8193/v1/turnstile/solve"
    assert not trust_env and not args["allow_redirects"]
    assert args["headers"]["X-Turnstile-Broker-Key"] == "test-secret"
    assert args["json"]["startParam"] == ""
    assert args["json"]["initData"] == ""
    assert "qyz_test_entry" not in json.dumps(args["json"])
    assert "test-init" not in json.dumps(args["json"])
    assert args["json"]["accountKey"] not in {"77", "88"}
    assert args["json"]["challengeId"] == "challenge"


@pytest.mark.parametrize("url", ["https://evil.test", "http://127.0.0.1/extra", "http://u:p@localhost", "http://localhost?redirect=x", "http://[bad", "http://localhost:bad", "http://localhost:0"])
def test_remote_or_ambiguous_broker_rejected(url):
    with pytest.raises(ts.TurnstileBrokerError):
        solve(ts.TurnstileBrokerClient(env(WORLD_BOSS_TURNSTILE_BROKER_URL=url), Session))
    assert not Session.calls


@pytest.mark.parametrize("overrides", [{"TURNSTILE_BROKER_SECRET": ""}, {"WORLD_BOSS_TURNSTILE_TIMEOUT_SECONDS": "nan"}, {"WORLD_BOSS_TURNSTILE_ENABLED": "false"}])
def test_bad_config_fails_before_http(overrides):
    with pytest.raises(ts.TurnstileBrokerError):
        solve(ts.TurnstileBrokerClient(env(**overrides), Session))
    assert not Session.calls


@pytest.mark.parametrize("body", [[], response(ok=False), response(turnstileToken=""), response(expiresAt=0), response(expiresAt=float("nan")), response(expiresAt=time.time() + 2)])
def test_invalid_response_no_retry(body):
    Session.body = body
    with pytest.raises(ts.TurnstileBrokerError):
        solve(ts.TurnstileBrokerClient(env(), Session))
    assert len(Session.calls) == 1


@pytest.mark.parametrize("status", [302, 401, 429, 500, 504])
def test_http_failure_no_retry_or_secret_echo(status):
    Session.status = status
    Session.body = {"error": "test-secret test-proof test-init"}
    with pytest.raises(ts.TurnstileBrokerError) as exc:
        solve(ts.TurnstileBrokerClient(env(), Session))
    assert "test-" not in str(exc.value)
    assert len(Session.calls) == 1


def test_loopback_http_serialization():
    class Handler(BaseHTTPRequestHandler):
        active = 0
        maximum = 0
        count = 0

        def log_message(self, *args):
            pass

        def do_POST(self):
            cls = type(self)
            cls.active += 1
            cls.maximum = max(cls.maximum, cls.active)
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert data["initData"] == ""
            assert data["startParam"] == ""
            assert self.headers["X-Turnstile-Broker-Key"] == "test-secret"
            time.sleep(0.02)
            body = json.dumps(response()).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            cls.active -= 1
            cls.count += 1

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = env(WORLD_BOSS_TURNSTILE_BROKER_URL=f"http://127.0.0.1:{server.server_port}")
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda _: solve(ts.TurnstileBrokerClient(config)), range(3)))
        assert len(results) == Handler.count == 3
        assert Handler.maximum == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def run_flow(provider, begin_outcomes=None, stop=None, solve_elapsed=0):
    calls = []
    now = [100.0]
    begin_outcomes = list(begin_outcomes or [
        (400, {"ok": False, "error": "turnstile_required"}),
        (200, {"ok": True, "startsInMs": 0}),
    ])

    def transport(req):
        endpoint = req["safe_summary"]["endpoint"]
        calls.append((endpoint, req))
        if endpoint == "start":
            return 200, {"ok": True, "boss": {"roomStatus": "battle", "actionsRemaining": 1},
                         "challenge": {"mode": "qyz_focus_burst_v2", "challengeId": "challenge", "durationMs": 2000,
                                       "windows": [{"id": "w1", "centerMs": 1200, "hitMs": 620, "perfectMs": 210}]}}
        if endpoint == "begin":
            outcome = begin_outcomes.pop(0)
            if outcome == "timeout":
                raise requests.Timeout("test timeout")
            return outcome
        if endpoint == "hit":
            return 200, {"ok": True, "hit": {"attemptConsumed": True, "perfect": True}, "boss": {"actionsRemaining": 0}}
        return 200, {"ok": True, "result": {"score": 1}}

    def sleep(seconds):
        now[0] += seconds

    def timed_provider(**kwargs):
        solved = provider(**kwargs)
        now[0] += float(solve_elapsed or 0)
        return solved

    result = wb.run_world_boss_joined_battle_lab_flow(
        wb.WorldBossJoinReceipt(True, "joined", player_id="77"), token="session-current",
        entry_token="qyz_entry", init_data="test-init", transport=transport, sleeper=sleep,
        clock=lambda: now[0], turnstile_provider=timed_provider, stop_event=stop,
    )
    return result, calls


def test_provider_only_receives_challenge_and_begin_uses_session():
    received = []

    def provider(**args):
        received.append(args)
        return {"turnstile_token": "test-proof", "turnstile_idempotency_key": "broker-key-must-be-ignored"}

    result, calls = run_flow(provider)
    assert result["ok"]
    assert len(received) == 1
    assert received[0]["challenge_id"] == "challenge"
    assert received[0]["timeout_seconds"] > 0
    assert "entry_token" not in received[0]
    assert "init_data" not in received[0]
    begin_payloads = [req["payload"] for step, req in calls if step == "begin"]
    assert len(begin_payloads) == 2
    assert begin_payloads[0]["token"] == "session-current"
    assert "turnstileToken" not in begin_payloads[0]
    assert begin_payloads[1]["turnstileToken"] == "test-proof"
    assert begin_payloads[1]["turnstileIdempotencyKey"] != "broker-key-must-be-ignored"
    uuid.UUID(begin_payloads[1]["turnstileIdempotencyKey"])
    assert "test-proof" not in json.dumps(result)


@pytest.mark.parametrize("status", [400, 429, 500, "timeout"])
def test_begin_failure_never_replays(status):
    solved = []

    def provider(**args):
        solved.append(1)
        return {"turnstile_token": "test-proof"}

    outcome = status if status == "timeout" else (status, {"ok": False, "error": "ordinary_begin_failure"})
    result, calls = run_flow(provider, [outcome])
    assert not result["ok"]
    assert not solved
    assert [step for step, _ in calls] == ["start", "begin"]


@pytest.mark.parametrize("outcome", [(500, {"ok": False, "error": "server_error"}), "timeout"])
def test_verified_begin_ambiguous_result_never_replays(outcome):
    solved = []

    def provider(**args):
        solved.append(args)
        return {"turnstile_token": "test-proof"}

    result, calls = run_flow(provider, [
        (400, {"ok": False, "error": "turnstile_required"}),
        outcome,
    ])
    assert result["status"] == "boss_begin_result_unknown"
    assert len(solved) == 1
    assert [step for step, _ in calls] == ["start", "begin", "begin"]


def test_verified_begin_explicit_rejection_never_replays():
    result, calls = run_flow(
        lambda **_args: {"turnstile_token": "test-proof"},
        [
            (400, {"ok": False, "error": "turnstile_required"}),
            (400, {"ok": False, "error": "turnstile_failed"}),
        ],
    )
    assert result["status"] == "turnstile_failed"
    assert [step for step, _ in calls] == ["start", "begin", "begin"]


@pytest.mark.parametrize("outcome", [
    (500, {"ok": False, "error": "turnstile_failed"}),
    (429, {"ok": False, "error": "turnstile_required"}),
    (400, {"ok": False, "error": "unexpected_turnstile_failed"}),
    (400, {"ok": False, "message": "turnstile_failed"}),
])
def test_only_explicit_app_error_can_start_verification(outcome):
    solved = []
    result, calls = run_flow(lambda **kwargs: solved.append(kwargs), [outcome])
    assert not result["ok"]
    assert not solved
    assert [step for step, _ in calls] == ["start", "begin"]


@pytest.mark.parametrize("solved", [None, [], "not-a-token-record", {"turnstile_token": 123}])
def test_invalid_provider_record_does_not_submit_begin(solved):
    result, calls = run_flow(lambda **kwargs: solved)
    assert result["status"] == "turnstile_broker_failed"
    assert [step for step, _ in calls] == ["start", "begin"]


def test_broker_handoff_rechecks_battle_deadline_before_verified_begin():
    result, calls = run_flow(
        lambda **_args: {"turnstile_token": "test-proof"},
        solve_elapsed=70,
    )
    assert result["status"] == "boss_battle_deadline"
    assert [step for step, _ in calls] == ["start", "begin"]


def test_broker_failure_prevents_begin():
    def provider(**args):
        raise ts.TurnstileBrokerError("turnstile_token_expired")

    result, calls = run_flow(provider)
    assert result["status"] == "turnstile_broker_failed"
    assert [step for step, _ in calls] == ["start", "begin"]


def test_stop_during_solve_prevents_begin():
    stop = threading.Event()

    def provider(**args):
        stop.set()
        return {"turnstile_token": "test-proof"}

    result, calls = run_flow(provider, stop=stop)
    assert result["status"] == "cancelled"
    assert [step for step, _ in calls] == ["start", "begin"]


def test_secret_capture_redaction():
    data = {"turnstileToken": "proof-private", "turnstileIdempotencyKey": "key-private", "X-Turnstile-Broker-Key": "broker-private"}
    safe = json.dumps(safe_miniapp_event_detail(data))
    for value in data.values():
        assert value not in safe
    assert "key-private" not in sanitize_webapp_secret_text("turnstileIdempotencyKey=key-private")
    assert "broker-private" not in sanitize_webapp_secret_text("X-Turnstile-Broker-Key: broker-private")
