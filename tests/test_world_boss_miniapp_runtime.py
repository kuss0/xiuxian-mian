import unittest
from types import SimpleNamespace
from unittest.mock import patch

from model.features import world_boss_miniapp_runtime


class WorldBossMiniAppRuntimeTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_per_identity_window_skip_is_forwarded_only_to_selected_identity(self):
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
        self.assertEqual({11: 0, 22: 2}, received)

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
