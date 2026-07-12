import asyncio
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
            return {"ok": True, "status": "settled", "data": {"reward": "玄晶"}, "error": ""}

        with (
            patch.object(world_boss_miniapp_runtime, "join_world_boss_miniapp_lab", side_effect=fake_join),
            patch.object(world_boss_miniapp_runtime, "run_world_boss_joined_battle_lab_flow", side_effect=fake_battle),
            patch.object(world_boss_miniapp_runtime, "get_identity_account", side_effect=lambda identity_id: identity_id + 100),
            patch.object(asyncio, "sleep", return_value=None),
        ):
            result = await world_boss_miniapp_runtime.run_world_boss_miniapp_event(
                [11, 22, 33, 44],
                event,
                init_data_provider=init_data_provider,
                transport=lambda _request: None,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(
            [("join", 11), ("join", 22), ("join", 33), ("join", 44)],
            calls[:4],
        )
        self.assertEqual(4, result["joined_count"])
        self.assertTrue(all(item["ok"] for item in result["results"] if item["phase"] == "join"))


if __name__ == "__main__":
    unittest.main()
