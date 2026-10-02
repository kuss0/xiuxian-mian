"""Offline acceptance of the supplied broker with a fake browser worker."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import threading
from concurrent.futures import Future
from http.server import ThreadingHTTPServer

import pytest
import requests

from model.features import world_boss_turnstile as ts


ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor"


@pytest.mark.parametrize("name", ["world_boss_turnstile_containers"])
def test_source_manifest(name):
    folder = VENDOR / name
    manifest = json.loads((folder / "PACKAGE_MANIFEST.json").read_text())
    for item in manifest["files"]:
        content = (folder / item["path"]).read_bytes()
        assert len(content) == item["bytes"], item["path"]
        assert hashlib.sha256(content).hexdigest() == item["sha256"], item["path"]


@pytest.fixture
def broker():
    path = VENDOR / "world_boss_turnstile_containers/scripts/world_boss_turnstile_broker.py"
    spec = importlib.util.spec_from_file_location("preprod_broker", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_worker_recovery(broker):
    class Worker:
        ready = True
        request_count = 2
        _last_diagnostic = {"stage": "timeout"}
        resets = 0

        def reset(self):
            self.resets += 1

    pool = broker.BrokerWorkerPool.__new__(broker.BrokerWorkerPool)
    pool._states = [{"worker": 0, "ready": True, "error": "turnstile_timeout"}]
    worker = Worker()
    pool._reset_worker(0, worker)
    assert worker.resets == 1
    assert pool._states[0]["ready"]
    assert "error" not in pool._states[0]


def test_actual_broker_handler_with_fake_browser(broker):
    class Pool:
        jobs = []

        def health(self):
            return {"ok": True, "workersReady": 1}

        def submit(self, payload):
            self.jobs.append(payload)
            future = Future()
            future.set_result({"turnstileToken": "offline-proof", "turnstileIdempotencyKey": "offline-key", "expiresAt": 9999999999})
            return future

    server = ThreadingHTTPServer(("127.0.0.1", 0), broker.BrokerHandler)
    server.pool = Pool()
    server.broker_secret = "offline-only"
    server.timeout_seconds = 60
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        with requests.Session() as session:
            session.trust_env = False
            assert session.get(url + "/healthz", timeout=2).json()["ok"]
            assert session.post(url + "/v1/turnstile/solve", json={}, timeout=2).status_code == 403
            assert not server.pool.jobs
        client = ts.TurnstileBrokerClient({"WORLD_BOSS_TURNSTILE_ENABLED": "true", "WORLD_BOSS_TURNSTILE_BROKER_URL": url, "TURNSTILE_BROKER_SECRET": "offline-only"})
        result = client.solve(
            identity_id=77,
            account_id=88,
            entry_token="qyz_offline",
            init_data="offline-init",
            challenge_id="offline-challenge",
        )
        assert result["turnstile_token"] == "offline-proof"
        assert len(server.pool.jobs) == 1
        assert server.pool.jobs[0]["pageUrl"] == ts.PAGE_URL
        assert server.pool.jobs[0]["startParam"] == ""
        assert server.pool.jobs[0]["initData"] == ""
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
