from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

import pytest
import requests

from model import webapp_core
from model.features import miniapp_common, tiandao_miniapp, world_boss_miniapp_runtime


@pytest.fixture
def local_api(monkeypatch):
    records = []
    reply = {"status": 200, "redirect_host": "localhost"}
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setattr(tiandao_miniapp, "TG_REQUESTS_PROXIES", {})
    monkeypatch.setattr(world_boss_miniapp_runtime, "TG_REQUESTS_PROXIES", {})
    pool = miniapp_common._MiniAppSessionPool()
    monkeypatch.setattr(miniapp_common, "_MINIAPP_SESSION_POOL", pool)

    class Handler(BaseHTTPRequestHandler):
        def handle_request(self):
            raw_body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            records.append((self.command, self.path, raw_body))
            redirecting = self.path == "/start" and reply["status"] != 200
            self.send_response(reply["status"] if redirecting else 200)
            if redirecting:
                self.send_header(
                    "Location",
                    f"http://{reply['redirect_host']}:{self.server.server_port}/redirected",
                )
            body = b'{"ok": true, "value": 42}'
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_POST = handle_request
        do_GET = handle_request

        def log_message(self, _format, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", reply, records
    finally:
        server.shutdown()
        worker.join(2)
        server.server_close()
        pool.close()
        assert not worker.is_alive()


def _run_request(kind, base_url, monkeypatch):
    payload = {"token": "private-test-token", "initData": "hash=test-secret"}
    if kind == "tiandao":
        monkeypatch.setattr(tiandao_miniapp, "TIANDAO_MINIAPP_VERIFY_URL", base_url + "/miniapp/verify")
        return tiandao_miniapp._post_miniapp_json("/start", payload)

    with ExitStack() as stack:
        if kind in {"session", "boss"}:
            session = stack.enter_context(requests.Session())
            session.trust_env = False
        if kind == "pooled":
            transport = miniapp_common.build_pooled_miniapp_transport(
                adapter_key="test", identity_id=99, proxies={},
            )
        elif kind == "boss":
            def transport(request):
                return world_boss_miniapp_runtime._requests_transport(request, session=session)
        else:
            transport = miniapp_common.build_miniapp_transport(
                proxies={}, session=session if kind == "session" else None,
            )
        adapter = webapp_core.MiniAppAdapter(
            game_key="test", label="test", api_base_url=base_url,
            allowed_api_hosts=("127.0.0.1",), allowed_api_paths=("/start",),
            endpoints={"start": "/start"},
        )
        request = webapp_core.build_miniapp_http_request(adapter, "start", payload)
        return webapp_core.execute_miniapp_http_request(request, transport)


@pytest.mark.parametrize("kind", ["direct", "session", "pooled", "boss", "tiandao"])
@pytest.mark.parametrize("status", [302, 307, 308])
@pytest.mark.parametrize("redirect_host", ["localhost", "127.0.0.1"])
def test_api_redirect_cannot_replay_post_or_forward_credentials(local_api, monkeypatch, kind, status, redirect_host):
    base_url, reply, records = local_api
    reply.update(status=status, redirect_host=redirect_host)

    if kind == "tiandao":
        with pytest.raises(tiandao_miniapp.TiandaoMiniappError, match=f"HTTP {status}"):
            _run_request(kind, base_url, monkeypatch)
    else:
        result = _run_request(kind, base_url, monkeypatch)
        assert not result.ok
        assert result.status_code == status
        assert result.attempts == 1
    assert len(records) == 1
    assert records[0][:2] == ("POST", "/start")
    assert json.loads(records[0][2])["token"] == "private-test-token"


@pytest.mark.parametrize("kind", ["direct", "session", "pooled", "boss", "tiandao"])
def test_normal_json_success_still_uses_one_post(local_api, monkeypatch, kind):
    base_url, _reply, records = local_api
    result = _run_request(kind, base_url, monkeypatch)
    if kind == "tiandao":
        assert result == {"ok": True, "value": 42}
    else:
        assert result.ok
        assert result.data == {"ok": True, "value": 42}
        assert result.attempts == 1
    assert len(records) == 1
