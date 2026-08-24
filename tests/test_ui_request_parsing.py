"""Contract tests for the request-parsing stage of the UI HTTP handler.

`_read_ui_request` was extracted out of `handle_ui_http`. Its contract is that
every early exit answers the client itself and returns None, and that the
caller can still name the request in its access log even when parsing bailed
out after the request line was understood.
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from model import ui


class _FakeReader:
    """Minimal StreamReader stand-in driving readuntil/readexactly."""

    def __init__(self, head=b"", body=b"", *, head_timeout=False, body_timeout=False, head_incomplete=False):
        self._head = head
        self._body = body
        self._head_timeout = head_timeout
        self._body_timeout = body_timeout
        self._head_incomplete = head_incomplete

    async def readuntil(self, _sep):
        if self._head_timeout:
            raise asyncio.TimeoutError
        if self._head_incomplete:
            raise asyncio.IncompleteReadError(self._head, None)
        return self._head

    async def readexactly(self, count):
        if self._body_timeout:
            raise asyncio.TimeoutError
        if len(self._body) < count:
            raise asyncio.IncompleteReadError(self._body, count)
        return self._body[:count]


class _FakeWriter:
    def __init__(self):
        self.closed = False

    def get_extra_info(self, _name):
        return ("127.0.0.1", 12345)

    def write(self, _data):
        return None

    def close(self):
        self.closed = True

    async def wait_closed(self):
        return None

    async def drain(self):
        return None


def _request_bytes(method="GET", target="/api/state", extra_headers=(), body=b""):
    lines = [f"{method} {target} HTTP/1.1", "Host: 127.0.0.1"]
    lines.extend(extra_headers)
    if body:
        lines.append(f"Content-Length: {len(body)}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8")


class UiRequestParsingTests(unittest.IsolatedAsyncioTestCase):
    async def test_parses_method_path_query_and_headers(self):
        reader = _FakeReader(head=_request_bytes(target="/api/state?send_as_id=7&flag="))
        writer = _FakeWriter()

        request = await ui._read_ui_request(reader, writer)

        self.assertIsNotNone(request)
        self.assertEqual("GET", request["method"])
        self.assertEqual("/api/state", request["path"])
        self.assertEqual(["7"], request["query"].get("send_as_id"))
        # keep_blank_values=False：空值参数不应出现
        self.assertNotIn("flag", request["query"])
        self.assertEqual("127.0.0.1", request["headers"].get("host"))

    async def test_lowercases_header_names_and_uppercases_method(self):
        reader = _FakeReader(head=_request_bytes(method="post", extra_headers=["X-Odd-Case: Kept"]))
        writer = _FakeWriter()

        request = await ui._read_ui_request(reader, writer)

        self.assertEqual("POST", request["method"])
        self.assertEqual("Kept", request["headers"].get("x-odd-case"))

    async def test_header_timeout_answers_and_returns_none(self):
        reader = _FakeReader(head_timeout=True)
        writer = _FakeWriter()

        with patch.object(ui, "_write_request_timeout") as timeout_mock:
            request = await ui._read_ui_request(reader, writer)

        self.assertIsNone(request)
        timeout_mock.assert_called_once()

    async def test_oversized_header_answers_and_returns_none(self):
        oversized = b"GET / HTTP/1.1\r\n" + b"X: " + b"y" * (ui.UI_HTTP_MAX_HEADER_BYTES + 16) + b"\r\n\r\n"
        reader = _FakeReader(head=oversized)
        writer = _FakeWriter()

        with patch.object(ui, "_write_payload_too_large") as too_large_mock:
            request = await ui._read_ui_request(reader, writer)

        self.assertIsNone(request)
        too_large_mock.assert_called_once()

    async def test_malformed_request_line_closes_without_response(self):
        reader = _FakeReader(head=b"GARBAGE\r\n\r\n")
        writer = _FakeWriter()

        request = await ui._read_ui_request(reader, writer)

        self.assertIsNone(request)
        self.assertTrue(writer.closed)

    async def test_bad_content_length_answers_and_returns_none(self):
        reader = _FakeReader(head=_request_bytes(extra_headers=["Content-Length: not-a-number"]))
        writer = _FakeWriter()

        with patch.object(ui, "_write_json_bad_request") as bad_request_mock:
            request = await ui._read_ui_request(reader, writer)

        self.assertIsNone(request)
        bad_request_mock.assert_called_once()

    async def test_trace_names_the_request_even_when_body_read_fails(self):
        """请求行已解析成功后失败时，调用方的访问日志仍要能说出是哪个请求。"""
        head = _request_bytes(method="POST", target="/api/toggle", extra_headers=["Content-Length: 32"])
        reader = _FakeReader(head=head, body_timeout=True)
        writer = _FakeWriter()
        trace = {}

        with patch.object(ui, "_write_request_timeout"):
            request = await ui._read_ui_request(reader, writer, trace)

        self.assertIsNone(request)
        self.assertEqual("POST", trace.get("method"))
        self.assertEqual("/api/toggle", trace.get("path"))

    async def test_trace_is_optional(self):
        reader = _FakeReader(head=_request_bytes())
        writer = _FakeWriter()

        request = await ui._read_ui_request(reader, writer)

        self.assertEqual("/api/state", request["path"])


class UiDocumentRouteTests(unittest.TestCase):
    """favicon / static assets / app page — the routes that are not JSON APIs."""

    def _serve(self, **kwargs):
        params = {
            "method": "GET",
            "path": "/",
            "query": {},
            "session": {"session_token": "s"},
            "session_cookie_header": "",
            "auth_headers": [],
        }
        params.update(kwargs)
        return ui._serve_ui_document_route(_FakeWriter(), **params)

    def test_returns_false_for_api_paths(self):
        """不认识的路径必须交还给 API 路由，否则会静默吞掉请求。"""
        for path in ("/api/state", "/api/toggle", "/unknown"):
            with self.subTest(path=path):
                self.assertFalse(self._serve(path=path))

    def test_static_assets_do_not_require_a_session(self):
        """静态资源不含账号数据，匿名可读；这条边界不能在重构中收紧或放宽。"""
        for path, loader in (("/static/app.js", "_load_ui_static_asset"), ("/static/css/ui_skin.css", "_load_ui_static_asset")):
            with self.subTest(path=path):
                with patch.object(ui, loader, return_value=(b"body", "application/javascript")) as load_mock, \
                        patch.object(ui, "_write_response") as write_mock:
                    handled = self._serve(path=path, session=None)
                self.assertTrue(handled)
                load_mock.assert_called_once()
                self.assertIn("200", write_mock.call_args.args[1])

    def test_static_asset_strips_only_its_own_prefix(self):
        with patch.object(ui, "_load_ui_static_asset", return_value=(b"x", "text/css")) as load_mock, \
                patch.object(ui, "_write_response"):
            self._serve(path="/static/css/app.css")
        self.assertEqual("css/app.css", load_mock.call_args.args[0])

    def test_missing_static_asset_is_404(self):
        with patch.object(ui, "_load_ui_static_asset", return_value=(None, "")), \
                patch.object(ui, "_write_response") as write_mock:
            self._serve(path="/static/missing.js")
        self.assertIn("404", write_mock.call_args.args[1])

    def test_non_get_on_document_routes_is_rejected(self):
        for path in ("/favicon.png", "/static/app.js", "/static/css/ui_skin.css", "/", "/new"):
            with self.subTest(path=path):
                with patch.object(ui, "_write_method_not_allowed") as not_allowed:
                    handled = self._serve(path=path, method="POST")
                self.assertTrue(handled)
                not_allowed.assert_called_once()

    def test_app_page_falls_back_to_login_when_anonymous(self):
        with patch.object(ui, "_render_login_page", return_value="LOGIN") as login_mock, \
                patch.object(ui, "_write_response") as write_mock:
            handled = self._serve(path="/", session=None, session_cookie_header="sid=x")
        self.assertTrue(handled)
        # 携带过期 cookie 时要给出提示文案，纯匿名则不提示
        self.assertIn("登录已失效", login_mock.call_args.args[0])
        self.assertIn("200", write_mock.call_args.args[1])

    def test_app_page_renders_for_authenticated_session(self):
        with patch.object(ui, "render_ui_page", return_value="PAGE") as render_mock, \
                patch.object(ui, "_write_response"):
            self._serve(path="/new", query={"send_as_id": ["990001"]}, session={"session_token": "tok"})
        self.assertEqual("990001", render_mock.call_args.kwargs["selected_send_as_id"])
        self.assertEqual("tok", render_mock.call_args.kwargs["session_token"])


class UiJsonActionRouteTests(unittest.IsolatedAsyncioTestCase):
    """Table-driven JSON action routes.

    The value of the table is that authentication and method checks exist once
    instead of being retyped per route, so these tests pin that invariant
    rather than each individual handler.
    """

    async def _dispatch(self, **kwargs):
        params = {
            "method": "POST",
            "path": "/api/replica-config",
            "payload": {},
            "session": {"session_token": "s", "sender_id": 42},
            "auth_headers": [],
        }
        params.update(kwargs)
        return await ui._serve_ui_json_action_route(_FakeWriter(), **params)

    async def test_returns_false_for_unlisted_paths(self):
        self.assertFalse(await self._dispatch(path="/api/state"))

    async def test_every_table_route_rejects_anonymous_access(self):
        """表里任何一条都不能绕过鉴权——这正是表驱动要保证的不变式。"""
        for path in ui._UI_JSON_ACTION_ROUTES:
            with self.subTest(path=path):
                with patch.object(ui, "_write_json_unauthorized") as unauth:
                    handled = await self._dispatch(path=path, session=None)
                self.assertTrue(handled)
                unauth.assert_called_once()

    async def test_every_table_route_rejects_wrong_method(self):
        for path in ui._UI_JSON_ACTION_ROUTES:
            with self.subTest(path=path):
                with patch.object(ui, "_write_method_not_allowed") as not_allowed:
                    handled = await self._dispatch(path=path, method="GET")
                self.assertTrue(handled)
                not_allowed.assert_called_once()

    async def test_routes_needing_send_as_id_reject_missing_value(self):
        guarded = [p for p, r in ui._UI_JSON_ACTION_ROUTES.items() if r.get("needs_send_as_id")]
        self.assertTrue(guarded, "至少应有一条路由声明 needs_send_as_id")
        for path in guarded:
            for missing in ({}, {"send_as_id": ""}, {"send_as_id": None}):
                with self.subTest(path=path, payload=missing):
                    with patch.object(ui, "_write_json_bad_request") as bad_request:
                        handled = await self._dispatch(path=path, payload=dict(missing))
                    self.assertTrue(handled)
                    bad_request.assert_called_once()

    async def test_sync_handler_result_is_written(self):
        with patch.object(ui, "ui_set_replica_config", return_value=(True, "saved")) as handler, \
                patch.object(ui, "_write_json_result") as write_mock:
            handled = await self._dispatch(path="/api/replica-config", payload={"a": 1})

        self.assertTrue(handled)
        handler.assert_called_once_with({"a": 1})
        self.assertEqual((True, "saved"), write_mock.call_args.args[1:3])
        self.assertIsNone(write_mock.call_args.kwargs["extra"])

    async def test_async_handler_is_awaited(self):
        with patch.object(ui, "ui_sync_tianti_status", new=AsyncMock(return_value=(True, "ok"))) as handler, \
                patch.object(ui, "_write_json_result") as write_mock:
            await self._dispatch(path="/api/tianti-sync", payload={"send_as_id": 7})

        handler.assert_awaited_once_with(7)
        self.assertEqual((True, "ok"), write_mock.call_args.args[1:3])

    async def test_triple_result_is_wrapped_under_extra_key(self):
        with patch.object(ui, "ui_start_storage_bag_transfer", new=AsyncMock(return_value=(True, "ok", {"n": 1}))), \
                patch.object(ui, "_write_json_result") as write_mock:
            await self._dispatch(path="/api/storage-bag-transfer-start", payload={})

        self.assertEqual({"transfer": {"n": 1}}, write_mock.call_args.kwargs["extra"])

    async def test_falsy_extra_is_omitted(self):
        """原实现用 `if transfer else None`，空结果不应产出 extra 键。"""
        with patch.object(ui, "ui_cancel_storage_bag_transfer", new=AsyncMock(return_value=(False, "nope", None))), \
                patch.object(ui, "_write_json_result") as write_mock:
            await self._dispatch(path="/api/storage-bag-transfer-cancel", payload={})

        self.assertIsNone(write_mock.call_args.kwargs["extra"])

    async def test_actor_id_comes_from_session(self):
        with patch.object(ui, "ui_delete_identity", new=AsyncMock(return_value=(True, "gone"))) as handler, \
                patch.object(ui, "_write_json_result"):
            await self._dispatch(
                path="/api/identity-delete",
                payload={"send_as_id": 990001},
                session={"session_token": "s", "sender_id": 4242},
            )

        handler.assert_awaited_once_with(990001, actor_id=4242)


class UiLoginExchangeTests(unittest.TestCase):
    """The one route reachable without a session; its failure modes matter."""

    def test_rejects_non_post(self):
        writer = _FakeWriter()
        with patch.object(ui, "_write_method_not_allowed") as not_allowed:
            ui._handle_ui_login_exchange(writer, "GET", {"token": "x"}, 1000.0)
        not_allowed.assert_called_once()

    def test_missing_token_is_bad_request(self):
        writer = _FakeWriter()
        with patch.object(ui, "_write_response") as write_mock:
            ui._handle_ui_login_exchange(writer, "POST", {"token": "   "}, 1000.0)
        self.assertIn("400", write_mock.call_args.args[1])

    def test_invalid_token_clears_cookie_with_401(self):
        writer = _FakeWriter()
        with patch.object(ui, "redeem_ui_login_token", return_value=""), \
                patch.object(ui, "_write_response") as write_mock:
            ui._handle_ui_login_exchange(writer, "POST", {"token": "bad"}, 1000.0)
        self.assertIn("401", write_mock.call_args.args[1])
        self.assertTrue(any("Set-Cookie" in h for h in write_mock.call_args.kwargs["extra_headers"]))

    def test_valid_token_sets_session_cookie(self):
        writer = _FakeWriter()
        with patch.object(ui, "redeem_ui_login_token", return_value="sess-1"), \
                patch.object(ui, "_write_response") as write_mock:
            ui._handle_ui_login_exchange(writer, "POST", {"token": "good"}, 1000.0)
        self.assertIn("200", write_mock.call_args.args[1])
        self.assertTrue(any("Set-Cookie" in h for h in write_mock.call_args.kwargs["extra_headers"]))


if __name__ == "__main__":
    unittest.main()
