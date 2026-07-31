import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

from model.features import world_boss_miniapp_runtime


class WorldBossMiniAppRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_realtime_feed_decodes_state_and_answers_ping(self):
        sent = []
        received_url = []

        class FakeWebsocket:
            def __init__(self):
                self.messages = asyncio.Queue()
                self.messages.put_nowait('{"type":"ping"}')
                self.messages.put_nowait(
                    '{"type":"state","data":{"boss":{"eventStatus":"active","roomStatus":"battle","phase":2}}}'
                )

            async def recv(self):
                return await self.messages.get()

            async def send(self, message):
                sent.append(message)

        websocket = FakeWebsocket()

        class FakeConnection:
            async def __aenter__(self):
                return websocket

            async def __aexit__(self, _exc_type, _exc, _tb):
                return False

        def connector(url, **_kwargs):
            received_url.append(url)
            return FakeConnection()

        def transport(request):
            self.assertEqual("ws_ticket", request["safe_summary"]["endpoint"])
            return 200, {"ok": True, "ticket": "ws-secret"}

        feed = world_boss_miniapp_runtime._WorldBossRealtimeFeed(
            identity_id=11,
            token="qyz_SESSION",
            init_data="query_id=11&hash=secret",
            transport=transport,
            connector=connector,
        )
        self.assertTrue(await feed.start())
        self.assertTrue(await asyncio.to_thread(feed.wait_for_update, 1.0))

        self.assertEqual("battle", feed.latest_boss()["roomStatus"])
        self.assertEqual(['{"type":"pong"}'], sent)
        self.assertEqual(1, len(received_url))
        self.assertIn("/ws/miniapp/xianxia-world-boss/state?ticket=ws-secret", received_url[0])
        await feed.close()

    async def test_realtime_feed_reconnects_after_connection_failure(self):
        connect_attempts = 0
        ticket_attempts = 0

        class FakeWebsocket:
            def __init__(self):
                self.messages = asyncio.Queue()
                self.messages.put_nowait(
                    '{"type":"state","data":{"boss":{"eventStatus":"active","roomStatus":"battle"}}}'
                )

            async def recv(self):
                return await self.messages.get()

            async def send(self, _message):
                return None

        class FakeConnection:
            def __init__(self, fail):
                self.fail = fail

            async def __aenter__(self):
                if self.fail:
                    raise OSError("temporary websocket failure")
                return FakeWebsocket()

            async def __aexit__(self, _exc_type, _exc, _tb):
                return False

        def connector(_url, **_kwargs):
            nonlocal connect_attempts
            connect_attempts += 1
            return FakeConnection(connect_attempts == 1)

        def transport(_request):
            nonlocal ticket_attempts
            ticket_attempts += 1
            return 200, {"ok": True, "ticket": f"ws-secret-{ticket_attempts}"}

        feed = world_boss_miniapp_runtime._WorldBossRealtimeFeed(
            identity_id=11,
            token="qyz_SESSION",
            init_data="query_id=11&hash=secret",
            transport=transport,
            connector=connector,
        )
        with patch.object(world_boss_miniapp_runtime, "WORLD_BOSS_MINIAPP_WS_RECONNECT_SEC", 0.01):
            self.assertTrue(await feed.start())
            for _attempt in range(100):
                if feed.latest_boss().get("roomStatus") == "battle":
                    break
                await asyncio.sleep(0.01)

        self.assertGreaterEqual(connect_attempts, 2)
        self.assertGreaterEqual(ticket_attempts, 2)
        self.assertEqual("battle", feed.latest_boss()["roomStatus"])
        await feed.close()

    async def test_realtime_feed_deduplicates_equivalent_state_signals(self):
        feed = world_boss_miniapp_runtime._WorldBossRealtimeFeed(
            identity_id=11,
            token="qyz_SESSION",
            init_data="query_id=11&hash=secret",
            transport=lambda _request: None,
            connector=object(),
        )
        boss = {"eventStatus": "active", "roomStatus": "battle", "phase": 2, "hpPercent": 90}
        feed._publish(boss)
        self.assertTrue(feed.wait_for_update(0))

        feed._publish({**boss, "hpPercent": 80})
        self.assertFalse(feed.wait_for_update(0))
        self.assertEqual(80, feed.latest_boss()["hpPercent"])

        feed._publish({**boss, "phase": 3})
        self.assertTrue(feed.wait_for_update(0))

    async def test_realtime_feed_summary_redacts_ticket_errors(self):
        feed = world_boss_miniapp_runtime._WorldBossRealtimeFeed(
            identity_id=11,
            token="qyz_SESSION",
            init_data="query_id=11&hash=secret",
            transport=lambda _request: None,
            connector=object(),
        )
        feed.last_error = "failed wss://asc.aiopenai.app/ws/state?ticket=ws-secret"

        summary = feed.safe_summary()

        self.assertNotIn("ws-secret", str(summary))
        self.assertIn("ticket=<redacted>", summary["last_error"])

    async def test_runtime_passes_optional_realtime_callbacks_to_battle(self):
        event = SimpleNamespace(
            buttons=[[SimpleNamespace(text="进入战场", url="https://t.me/hantianzun22_bot?startapp=qyz_SECRET123")]],
        )
        callback_types = []

        async def init_data_provider(identity_id, _launch):
            return f"query_id={identity_id}&hash=secret"

        def fake_join(**kwargs):
            return SimpleNamespace(
                joined=True,
                identity_id=kwargs["identity_id"],
                session_token="qyz_SESSION",
                safe_summary=lambda: {"joined": True, "status": "joined"},
            )

        class FakeFeed:
            def __init__(self, **_kwargs):
                pass

            async def start(self):
                return True

            async def close(self):
                return None

            def wait_for_update(self, _timeout):
                return False

            def latest_boss(self):
                return {}

            def safe_summary(self):
                return {"available": True, "connected": True, "reconnect_count": 0, "has_state": False, "last_error": ""}

        def fake_battle(_receipt, **kwargs):
            callback_types.append((callable(kwargs.get("realtime_waiter")), callable(kwargs.get("realtime_state_provider"))))
            return {"ok": True, "status": "settled", "data": {"result": {"score": 100}}, "error": ""}

        with (
            patch.object(world_boss_miniapp_runtime, "_websocket_connect", object()),
            patch.object(world_boss_miniapp_runtime, "_WorldBossRealtimeFeed", FakeFeed),
            patch.object(world_boss_miniapp_runtime, "join_world_boss_miniapp_lab", side_effect=fake_join),
            patch.object(world_boss_miniapp_runtime, "run_world_boss_joined_battle_lab_flow", side_effect=fake_battle),
            patch.object(world_boss_miniapp_runtime, "get_identity_account", return_value=100),
        ):
            result = await world_boss_miniapp_runtime.run_world_boss_miniapp_event(
                [11],
                event,
                init_data_provider=init_data_provider,
                transport=lambda _request: None,
            )

        self.assertTrue(result["ok"])
        self.assertEqual([(True, True)], callback_types)

    def test_extract_rotating_bot_qyz_entry(self):
        event = SimpleNamespace(
            buttons=[[SimpleNamespace(text="进入战场", url="https://t.me/hantianzun22_bot?startapp=qyz_SECRET123")]],
        )

        launch = world_boss_miniapp_runtime.extract_world_boss_miniapp_launch(event)

        self.assertEqual("hantianzun22_bot", launch["bot_username"])
        self.assertEqual("qyz_SECRET123", launch["token"])
        self.assertNotIn("qyz_SECRET123", str(launch["safe_summary"]))

    async def test_all_accounts_join_before_first_battle(self):
        calls = []
        progress = []
        event = SimpleNamespace(
            buttons=[[SimpleNamespace(text="进入战场", url="https://t.me/hantianzun22_bot?startapp=qyz_SECRET123")]],
        )

        async def init_data_provider(identity_id, _launch):
            return f"query_id={identity_id}&hash=secret"

        def fake_join(**kwargs):
            calls.append(("join", kwargs["identity_id"]))
            return SimpleNamespace(
                joined=True,
                safe_summary=lambda: {"joined": True, "status": "joined"},
            )

        def fake_battle(receipt, **kwargs):
            identity_id = int(kwargs["capture_source"].rsplit(":", 1)[-1])
            calls.append(("battle", identity_id))
            return {
                "ok": True,
                "status": "settled",
                "data": {"result": {"score": 900, "realtime_hit_count": 2, "realtime_damage_yi": 300}},
                "error": "",
            }

        with (
            patch.object(world_boss_miniapp_runtime, "join_world_boss_miniapp_lab", side_effect=fake_join),
            patch.object(world_boss_miniapp_runtime, "run_world_boss_joined_battle_lab_flow", side_effect=fake_battle),
            patch.object(world_boss_miniapp_runtime, "get_identity_account", side_effect=lambda identity_id: identity_id + 100),
        ):
            result = await world_boss_miniapp_runtime.run_world_boss_miniapp_event(
                [11, 22, 33, 44],
                event,
                account_gap_sec=3,
                init_data_provider=init_data_provider,
                transport=lambda _request: None,
                progress_callback=lambda item: progress.append(item),
            )

        self.assertTrue(result["ok"])
        self.assertCountEqual(
            [("join", 11), ("join", 22), ("join", 33), ("join", 44)],
            calls[:4],
        )
        self.assertTrue(all(kind == "join" for kind, _identity_id in calls[:4]))
        self.assertEqual(4, result["joined_count"])
        self.assertTrue(all(item["ok"] for item in result["results"] if item["phase"] == "join"))
        self.assertEqual(4, sum(item["phase"] == "join" for item in progress))
        self.assertEqual(4, sum(item["phase"] == "battle" for item in progress))
        self.assertTrue(all(
            item.get("summary", {}).get("realtime_hit_count") == 2
            for item in progress
            if item["phase"] == "battle"
        ))

    async def test_token_registration_delay_retries_only_confirmed_missing_token(self):
        event = SimpleNamespace(
            buttons=[[SimpleNamespace(text="进入战场", url="https://t.me/hantianzun22_bot?startapp=qyz_SECRET123")]],
        )
        attempts = 0

        async def init_data_provider(identity_id, _launch):
            return f"query_id={identity_id}&hash=secret"

        def fake_join(**kwargs):
            nonlocal attempts
            attempts += 1
            joined = attempts == 3
            return SimpleNamespace(
                joined=joined,
                status="joined" if joined else "boss_token_missing",
                identity_id=kwargs["identity_id"],
                session_token="qyz_SESSION" if joined else "",
                safe_summary=lambda: {
                    "joined": joined,
                    "status": "joined" if joined else "boss_token_missing",
                },
            )

        def fake_battle(_receipt, **_kwargs):
            return {"ok": True, "status": "settled", "data": {"result": {"score": 100}}, "error": ""}

        with (
            patch.object(world_boss_miniapp_runtime, "join_world_boss_miniapp_lab", side_effect=fake_join),
            patch.object(world_boss_miniapp_runtime, "run_world_boss_joined_battle_lab_flow", side_effect=fake_battle),
            patch.object(world_boss_miniapp_runtime, "get_identity_account", return_value=100),
            patch.object(world_boss_miniapp_runtime.asyncio, "sleep", new=AsyncMock()) as sleep_mock,
        ):
            result = await world_boss_miniapp_runtime.run_world_boss_miniapp_event(
                [11],
                event,
                opened_at=time.time(),
                init_data_provider=init_data_provider,
                transport=lambda _request: None,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(3, attempts)
        self.assertEqual([call(1.0), call(2.0)], sleep_mock.await_args_list)

    async def test_non_token_join_failure_is_not_retried(self):
        event = SimpleNamespace(
            buttons=[[SimpleNamespace(text="进入战场", url="https://t.me/hantianzun22_bot?startapp=qyz_SECRET123")]],
        )
        attempts = 0

        async def init_data_provider(identity_id, _launch):
            return f"query_id={identity_id}&hash=secret"

        def fake_join(**_kwargs):
            nonlocal attempts
            attempts += 1
            return SimpleNamespace(
                joined=False,
                status="boss_event_closed",
                safe_summary=lambda: {"joined": False, "status": "boss_event_closed"},
            )

        with (
            patch.object(world_boss_miniapp_runtime, "join_world_boss_miniapp_lab", side_effect=fake_join),
            patch.object(world_boss_miniapp_runtime, "get_identity_account", return_value=100),
            patch.object(world_boss_miniapp_runtime.asyncio, "sleep", new=AsyncMock()) as sleep_mock,
        ):
            result = await world_boss_miniapp_runtime.run_world_boss_miniapp_event(
                [11],
                event,
                opened_at=time.time(),
                init_data_provider=init_data_provider,
                transport=lambda _request: None,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(1, attempts)
        sleep_mock.assert_not_awaited()

    async def test_finish_reserve_and_per_identity_extra_skip_are_combined(self):
        event = SimpleNamespace(
            buttons=[[SimpleNamespace(text="进入战场", url="https://t.me/hantianzun22_bot?startapp=qyz_SECRET123")]],
        )
        received = {}

        async def init_data_provider(identity_id, _launch):
            return f"query_id={identity_id}&hash=secret"

        def fake_join(**kwargs):
            return SimpleNamespace(
                joined=True,
                session_token="qyz_SESSION",
                safe_summary=lambda: {"joined": True, "status": "joined"},
            )

        def fake_battle(_receipt, **kwargs):
            identity_id = int(kwargs["capture_source"].rsplit(":", 1)[-1])
            received[identity_id] = kwargs.get("window_skip_count")
            return {
                "ok": True,
                "status": "settled",
                "data": {"result": {"score": 100, "realtime_hit_count": 1}},
                "error": "",
            }

        with (
            patch.object(world_boss_miniapp_runtime, "join_world_boss_miniapp_lab", side_effect=fake_join),
            patch.object(world_boss_miniapp_runtime, "run_world_boss_joined_battle_lab_flow", side_effect=fake_battle),
            patch.object(world_boss_miniapp_runtime, "get_identity_account", return_value=100),
        ):
            result = await world_boss_miniapp_runtime.run_world_boss_miniapp_event(
                [11, 22],
                event,
                init_data_provider=init_data_provider,
                transport=lambda _request: None,
                window_skip_by_identity={"22": 2},
            )

        self.assertTrue(result["ok"])
        self.assertEqual({11: 2, 22: 4}, received)
        summaries = {
            item["identity_id"]: item["summary"]
            for item in result["results"]
            if item["phase"] == "battle"
        }
        self.assertEqual(2, summaries[11]["finish_reserve_window_count"])
        self.assertEqual(0, summaries[11]["identity_extra_window_skip_count"])
        self.assertEqual(2, summaries[22]["finish_reserve_window_count"])
        self.assertEqual(2, summaries[22]["identity_extra_window_skip_count"])

    async def test_parallel_battles_share_one_terminal_stop_event(self):
        event = SimpleNamespace(
            buttons=[[SimpleNamespace(text="进入战场", url="https://t.me/hantianzun22_bot?startapp=qyz_SECRET123")]],
        )
        stop_events = []

        async def init_data_provider(identity_id, _launch):
            return f"query_id={identity_id}&hash=secret"

        def fake_join(**kwargs):
            return SimpleNamespace(
                joined=True,
                identity_id=kwargs["identity_id"],
                session_token="qyz_SESSION",
                safe_summary=lambda: {"joined": True, "status": "joined"},
            )

        def fake_battle(_receipt, **kwargs):
            stop_events.append(kwargs.get("stop_event"))
            return {
                "ok": True,
                "status": "settled",
                "data": {"result": {"score": 100, "realtime_hit_count": 1}},
                "error": "",
            }

        with (
            patch.object(world_boss_miniapp_runtime, "join_world_boss_miniapp_lab", side_effect=fake_join),
            patch.object(world_boss_miniapp_runtime, "run_world_boss_joined_battle_lab_flow", side_effect=fake_battle),
            patch.object(world_boss_miniapp_runtime, "get_identity_account", return_value=100),
        ):
            result = await world_boss_miniapp_runtime.run_world_boss_miniapp_event(
                [11, 22, 33, 44],
                event,
                init_data_provider=init_data_provider,
                transport=lambda _request: None,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(4, len(stop_events))
        self.assertIsNotNone(stop_events[0])
        self.assertTrue(all(item is stop_events[0] for item in stop_events))

    async def test_battle_priority_micro_stagger_preserves_candidate_order(self):
        event = SimpleNamespace(
            buttons=[[SimpleNamespace(text="进入战场", url="https://t.me/hantianzun22_bot?startapp=qyz_SECRET123")]],
        )
        battle_order = []

        async def init_data_provider(identity_id, _launch):
            return f"query_id={identity_id}&hash=secret"

        def fake_join(**kwargs):
            return SimpleNamespace(
                joined=True,
                identity_id=kwargs["identity_id"],
                session_token="qyz_SESSION",
                safe_summary=lambda: {"joined": True, "status": "joined"},
            )

        def fake_battle(_receipt, **kwargs):
            identity_id = int(kwargs["capture_source"].rsplit(":", 1)[-1])
            battle_order.append(identity_id)
            return {
                "ok": True,
                "status": "settled",
                "data": {"result": {"score": 100, "realtime_hit_count": 1}},
                "error": "",
            }

        async def inline_to_thread(function, *args, **kwargs):
            return function(*args, **kwargs)

        sleep_delays = []

        async def record_sleep(delay):
            sleep_delays.append(delay)

        with (
            patch.object(world_boss_miniapp_runtime, "join_world_boss_miniapp_lab", side_effect=fake_join),
            patch.object(world_boss_miniapp_runtime, "run_world_boss_joined_battle_lab_flow", side_effect=fake_battle),
            patch.object(world_boss_miniapp_runtime, "get_identity_account", side_effect=lambda identity_id: identity_id + 100),
            patch.object(asyncio, "to_thread", new=inline_to_thread),
            patch.object(asyncio, "sleep", new=record_sleep),
        ):
            result = await world_boss_miniapp_runtime.run_world_boss_miniapp_event(
                [11, 22, 33, 44],
                event,
                init_data_provider=init_data_provider,
                transport=lambda _request: None,
                battle_priority_gap_sec=0.25,
            )

        self.assertTrue(result["ok"])
        self.assertEqual([11, 22, 33, 44], battle_order)
        self.assertEqual([0.25, 0.5, 0.75], sleep_delays)
        summaries = {
            item["identity_id"]: item["summary"]
            for item in result["results"]
            if item["phase"] == "battle"
        }
        self.assertEqual(0, summaries[11]["launch_delay_ms"])
        self.assertEqual(250, summaries[22]["launch_delay_ms"])
        self.assertEqual(500, summaries[33]["launch_delay_ms"])
        self.assertEqual(750, summaries[44]["launch_delay_ms"])

    async def test_zero_contribution_marks_event_partial(self):
        event = SimpleNamespace(
            buttons=[[SimpleNamespace(text="进入战场", url="https://t.me/hantianzun22_bot?startapp=qyz_SECRET123")]],
        )

        async def init_data_provider(identity_id, _launch):
            return f"query_id={identity_id}&hash=secret"

        def fake_join(**_kwargs):
            return SimpleNamespace(joined=True, safe_summary=lambda: {"joined": True, "status": "joined"})

        def fake_battle(_receipt, **_kwargs):
            return {
                "ok": False,
                "status": "settled_zero_contribution",
                "data": {"result": {"score": 0, "realtime_hit_count": 0, "realtime_damage_yi": 0}},
                "error": "world boss settled without effective contribution",
            }

        with (
            patch.object(world_boss_miniapp_runtime, "join_world_boss_miniapp_lab", side_effect=fake_join),
            patch.object(world_boss_miniapp_runtime, "run_world_boss_joined_battle_lab_flow", side_effect=fake_battle),
            patch.object(world_boss_miniapp_runtime, "get_identity_account", return_value=100),
        ):
            result = await world_boss_miniapp_runtime.run_world_boss_miniapp_event(
                [11],
                event,
                init_data_provider=init_data_provider,
                transport=lambda _request: None,
            )

        self.assertFalse(result["ok"])
        self.assertEqual("partial", result["status"])
        self.assertEqual("settled_zero_contribution", result["results"][-1]["status"])

    async def test_default_transport_reuses_one_session_per_identity(self):
        event = SimpleNamespace(
            buttons=[[SimpleNamespace(text="进入战场", url="https://t.me/hantianzun22_bot?startapp=qyz_SECRET123")]],
        )
        join_transports = {}
        sessions = []

        class FakeSession:
            def __init__(self):
                self.closed = False

            def request(self, *_args, **_kwargs):
                return None

            def close(self):
                self.closed = True

        def session_factory():
            session = FakeSession()
            sessions.append(session)
            return session

        async def init_data_provider(identity_id, _launch):
            return f"query_id={identity_id}&hash=secret"

        def fake_join(**kwargs):
            join_transports[kwargs["identity_id"]] = kwargs["transport"]
            return SimpleNamespace(
                joined=True,
                session_token="qyz_SESSION",
                safe_summary=lambda: {"joined": True, "status": "joined"},
            )

        def fake_battle(_receipt, **kwargs):
            identity_id = int(kwargs["capture_source"].rsplit(":", 1)[-1])
            self.assertIs(join_transports[identity_id], kwargs["transport"])
            return {"ok": True, "status": "settled", "data": {"result": {"score": 100}}, "error": ""}

        with (
            patch.object(world_boss_miniapp_runtime.requests, "Session", side_effect=session_factory),
            patch.object(world_boss_miniapp_runtime, "join_world_boss_miniapp_lab", side_effect=fake_join),
            patch.object(world_boss_miniapp_runtime, "run_world_boss_joined_battle_lab_flow", side_effect=fake_battle),
            patch.object(world_boss_miniapp_runtime, "get_identity_account", return_value=100),
        ):
            result = await world_boss_miniapp_runtime.run_world_boss_miniapp_event(
                [11, 22],
                event,
                init_data_provider=init_data_provider,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(2, len(sessions))
        self.assertTrue(all(session.closed for session in sessions))


if __name__ == "__main__":
    unittest.main()
