import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model.features import cave_treasure_runtime


class CaveTowerRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cave_treasure_runtime._PUBLIC_ENTRY_LOCKS.clear()

    def test_finds_pagoda_external_app_and_launch(self):
        payload = {
            "account": {
                "externalApps": {
                    "groups": [{"apps": [{
                        "key": "pagoda",
                        "title": "琉璃问心塔",
                        "available": True,
                        "action": "pagoda",
                    }]}],
                },
            },
        }
        app = cave_treasure_runtime._find_tower_external_app_in_cave_payload(payload)
        launch = cave_treasure_runtime._find_tower_launch_in_cave_payload({
            "url": "/miniapp/xianxia-pagoda?startapp=pagoda_SECRET999",
        })
        self.assertEqual("pagoda", app["action"])
        self.assertEqual("pagoda_SECRET999", launch["token"])

    async def test_public_tower_uses_selected_identity_and_dwelling_init_data(self):
        identity_id = 8659059191
        cave_start = {
            "ok": True,
            "data": {
                "overview": {"player_id": identity_id},
                "raw": {
                    "account": {
                        "externalApps": {
                            "groups": [{"apps": [{
                                "key": "pagoda",
                                "title": "琉璃问心塔",
                                "available": True,
                                "action": "pagoda",
                            }]}],
                        },
                    },
                },
            },
        }
        external_result = {
            "ok": True,
            "data": {"url": "/miniapp/xianxia-pagoda?startapp=pagoda_SECRET999"},
        }
        tower_result = {
            "ok": True,
            "status": "challenged",
            "data": {
                "state": {"today_highest": 8},
                "replay": {"cleared_count": 8, "end_floor": 8, "failed_floor": 9},
                "gains": {"修为": 1260, "塔印": 42},
                "rewards": {},
                "challenged": True,
            },
        }
        with patch.object(cave_treasure_runtime, "is_cave_public_identity_available", return_value=True), \
                patch.object(cave_treasure_runtime, "_public_entry_allowed", return_value=True), \
                patch.object(cave_treasure_runtime, "_load_cave_public_identity_session", new=AsyncMock(return_value={
                    "ok": True,
                    "init_data": "dwelling_init_data",
                    "player_id": identity_id,
                    "result": cave_start,
                })), \
                patch.object(cave_treasure_runtime, "run_cave_external_action_production_flow", new=AsyncMock(return_value=external_result)) as external_mock, \
                patch.object(cave_treasure_runtime, "run_tower_miniapp_production_flow", new=AsyncMock(return_value=tower_result)) as tower_mock, \
                patch.object(cave_treasure_runtime, "record_miniapp_state"), \
                patch.object(cave_treasure_runtime, "send_audit_log", new=AsyncMock()):
            result = await cave_treasure_runtime.run_cave_public_tower(
                identity_id,
                "https://t.me/fanrenxiuxian_bot?startapp=df_SECRET999",
                now=1_700_000_000.0,
            )

        self.assertTrue(result["ok"])
        self.assertEqual("pagoda", external_mock.await_args.kwargs["action"])
        self.assertEqual(identity_id, external_mock.await_args.kwargs["player_id"])
        self.assertEqual("dwelling_init_data", external_mock.await_args.kwargs["init_data"])
        self.assertEqual("pagoda_SECRET999", tower_mock.await_args.kwargs["token"])
        self.assertEqual("dwelling_init_data", tower_mock.await_args.kwargs["init_data"])
        self.assertEqual(8, result["extra"]["replay"]["cleared_count"])

