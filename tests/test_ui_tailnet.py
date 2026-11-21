import asyncio
import stat
from functools import partial

import pytest

from model import ui
from model.ui_tailnet import start_tailnet_listener, tailnet_origin


ORIGIN = "https://panel.example.net"
HOST = "panel.example.net"


def test_private_http_transport_does_not_trust_headers_on_tcp(tmp_path, monkeypatch):
    calls = []

    def record(payload, _session):
        calls.append(payload)
        return True, "ok"

    monkeypatch.setattr(ui, "get_accounts", lambda: [1])
    monkeypatch.setattr(ui, "get_ui_snapshot", lambda **_: {"private": True})
    monkeypatch.setitem(ui._UI_JSON_ACTION_ROUTES, "/api/test-private-write", {
        "handler": record,
        "include_snapshot": False,
    })
    tmp_path.chmod(0o750)
    path = tmp_path / "control.sock"

    async def run():
        tcp = await asyncio.start_server(ui.handle_ui_http, "127.0.0.1", 0)
        unix = await start_tailnet_listener(path, partial(ui.handle_ui_http, private_origin=ORIGIN), limit=65537)
        assert stat.S_IMODE(path.stat().st_mode) == 0o660
        port = tcp.sockets[0].getsockname()[1]

        async def send(request, *, private):
            if private:
                reader, writer = await asyncio.open_unix_connection(path)
            else:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(request.encode())
            await writer.drain()
            data = await reader.read()
            writer.close()
            await writer.wait_closed()
            return data

        get = f"GET /api/state HTTP/1.1\r\nHost: {HOST}\r\nX-Xiuxian-Tailnet-Client: 100.65.0.2\r\n\r\n"
        post = (f"POST /api/test-private-write HTTP/1.1\r\nHost: {HOST}\r\n"
                "Content-Type: application/json\r\nContent-Length: 2\r\n")
        try:
            assert b"401 Unauthorized" in await send(get, private=False)
            assert b'"private":true' in await send(get, private=True)
            assert b"403 Forbidden" in await send(get.replace(HOST, "other.example.net"), private=True)
            assert b"403 Forbidden" in await send(get.replace("100.65.0.2", "203.0.113.9"), private=True)
            assert b"403 Forbidden" in await send(post + "\r\n{}", private=True)
            assert b"403 Forbidden" in await send(post + "Origin: https://other.example.net\r\n\r\n{}", private=True)
            assert not calls
            same_origin = post + f"Origin: {ORIGIN}\r\n\r\n{{}}"
            assert b"200 OK" in await send(same_origin, private=True)
            assert b"401 Unauthorized" in await send(same_origin, private=False)
            assert calls == [{}]
        finally:
            for server in (tcp, unix):
                server.close()
            for server in (tcp, unix):
                await server.wait_closed()

    asyncio.run(run())


def test_tailnet_rejects_public_socket_directory_and_non_https_origin(tmp_path):
    tmp_path.chmod(0o777)
    with pytest.raises(PermissionError):
        asyncio.run(start_tailnet_listener(tmp_path / "unsafe.sock", None, limit=65537))
    with pytest.raises(ValueError):
        tailnet_origin("http://panel.example.net")
