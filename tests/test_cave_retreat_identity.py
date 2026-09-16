import copy
import unittest
from unittest.mock import AsyncMock, Mock, patch

from model import state as state_module
from model.features import cave_treasure_miniapp as api
from model.features import cave_treasure_runtime as runtime
from model.features import deep_retreat


IDENTITY_ID = 1001
ACCOUNT_ID = 2001
PLAYER_ID = -1_000_000_001_001
ENTRY = "https://t.me/fanrenxiuxian_bot?startapp=df_TEST_ONLY"
START_MESSAGE = "你已进入深度闭关状态，神魂将自行吐纳 **8** 小时。"


def action_data(player_id=PLAYER_ID, message=START_MESSAGE):
    return {
        "ok": True,
        "account": {"playerId": player_id},
        "actionResult": {"ok": True, "rawMessage": message},
    }


class CaveRetreatIdentityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.saved_meta = copy.deepcopy(state_module._meta_state)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}
        state_module._meta_state["identity_account_map"] = {}
        state_module.set_miniapp_state_records({})
        for identity_id in (IDENTITY_ID, ACCOUNT_ID):
            state_module.ensure_identity_registered(identity_id)
            state_module.set_identity_account(identity_id, ACCOUNT_ID)
            state_module.get_identity_state(identity_id)["deep_retreat_enabled"] = True
        self.saved_locks = dict(runtime._PUBLIC_ENTRY_LOCKS)
        runtime._PUBLIC_ENTRY_LOCKS.clear()

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(self.saved_meta)
        runtime._PUBLIC_ENTRY_LOCKS.clear()
        runtime._PUBLIC_ENTRY_LOCKS.update(self.saved_locks)

    def session(self, action="start"):
        return {
            "ok": True,
            "init_data": "test_init_data",
            "player_id": PLAYER_ID,
            "result": {
                "ok": True,
                "data": {
                    "raw": {
                        "account": {"playerId": PLAYER_ID},
                        "dwelling": {"meditation": {"deepSeclusion": {
                            "active": action in {"force", "settle"}, "completed": action == "settle",
                            "canSettle": action == "settle", "canStart": action == "start", "canForceExit": action == "force",
                        }}},
                    },
                },
            },
        }

    def test_builders_require_an_explicit_valid_player(self):
        builders = (
            lambda **kwargs: api.build_cave_deep_seclusion_action_request("start", **kwargs),
            api.build_cave_meditation_settle_request,
        )
        for builder in builders:
            with self.assertRaises(TypeError):
                builder(token="test")
            for invalid in (None, True, False, 0, 1001.0, "", "1001.5", -1001, "--1001"):
                with self.subTest(builder=builder, invalid=invalid), self.assertRaises(ValueError):
                    builder(token="test", player_id=invalid)
            for valid in (IDENTITY_ID, PLAYER_ID, str(PLAYER_ID)):
                with self.subTest(builder=builder, valid=valid):
                    request = builder(token="test", player_id=valid, init_data="test_init_data")
                    self.assertEqual(int(valid), request["payload"]["playerId"])

    async def test_both_flows_reject_wrong_session_before_auth_or_http(self):
        flows = (
            (api.run_cave_deep_seclusion_action_production_flow, {"action": "start"}),
            (api.run_cave_meditation_settle_production_flow, {}),
        )
        for flow, args in flows:
            for player_id in (None, True, 0, ACCOUNT_ID, -1_000_000_002_001):
                transport = Mock()
                with self.subTest(flow=flow.__name__, player_id=player_id), patch.object(
                    api, "request_cave_treasure_miniapp_init_data", new=AsyncMock(),
                ) as auth:
                    result = await flow(
                        IDENTITY_ID, token="test", webview_url=ENTRY,
                        player_id=player_id, transport=transport, **args,
                    )
                self.assertFalse(result["ok"])
                self.assertEqual({}, result["data"])
                auth.assert_not_awaited()
                transport.assert_not_called()

    async def test_every_action_carries_selected_channel_to_actual_transport(self):
        for action in ("status", "start", "settle", "force", "meditation"):
            calls = []

            def transport(request):
                calls.append(request)
                selected = request["payload"].get("playerId", ACCOUNT_ID)
                return 200, action_data(selected)

            with self.subTest(action=action):
                flow = api.run_cave_deep_seclusion_action_production_flow
                args = {"action": action}
                if action == "meditation":
                    flow = api.run_cave_meditation_settle_production_flow
                    args = {}
                result = await flow(
                    IDENTITY_ID, token="test", webview_url=ENTRY, player_id=PLAYER_ID,
                    init_data="test_init_data", transport=transport, **args,
                )
                self.assertTrue(result["ok"], result)
                self.assertEqual(1, len(calls))
                self.assertEqual(PLAYER_ID, calls[0]["payload"]["playerId"])
                self.assertEqual(PLAYER_ID, result["data"]["account"]["playerId"])

    async def test_both_flows_discard_missing_or_mismatched_account_responses(self):
        responses = (
            {"ok": True, "identity": {"selectedPlayerId": PLAYER_ID}},
            action_data(ACCOUNT_ID),
            action_data(True),
            {**action_data(), "identity": {"selectedPlayerId": ACCOUNT_ID}},
            {"ok": True, "data": action_data(ACCOUNT_ID)},
        )
        flows = (
            (api.run_cave_deep_seclusion_action_production_flow, {"action": "settle"}),
            (api.run_cave_meditation_settle_production_flow, {}),
        )
        for flow, args in flows:
            for response in responses:
                transport = Mock(return_value=(200, response))
                with self.subTest(flow=flow.__name__, response=response):
                    result = await flow(
                        IDENTITY_ID, token="test", webview_url=ENTRY, player_id=PLAYER_ID,
                        init_data="test_init_data", transport=transport, **args,
                    )
                    self.assertFalse(result["ok"])
                    self.assertEqual("identity_unverified", result["status"])
                    self.assertEqual({}, result["data"])
                    transport.assert_called_once()

    async def test_public_channel_start_does_not_start_or_change_primary_role(self):
        now = 1_700_000_500.0
        primary = state_module.get_identity_state(ACCOUNT_ID)
        primary["deep_retreat_phase"] = "waiting_summary"
        primary["next_deep_retreat_time"] = now - 500
        primary_before = copy.deepcopy(primary)
        state_module.set_identity_enabled(IDENTITY_ID, False)
        state_module.set_channel_send_as_health({
            "status": "closed", "restore_identity_ids": [IDENTITY_ID],
        })
        started_players = []

        def transport(request):
            selected = request["payload"].get("playerId", ACCOUNT_ID)
            started_players.append(selected)
            return 200, action_data(selected)

        with (
            patch.object(runtime, "_public_entry_allowed", return_value=True),
            patch.object(runtime, "_load_cave_public_identity_session", new=AsyncMock(return_value=self.session())),
            patch.object(api, "_flow_transport", return_value=transport),
            patch.object(runtime, "_capture_store", return_value=None),
            patch.object(runtime, "send_audit_log", new=AsyncMock()),
            patch.object(deep_retreat, "send_audit_log", new=AsyncMock()),
            patch.object(deep_retreat, "save_state"),
            patch("model.features._phaseful.save_state"),
        ):
            result = await runtime.run_cave_public_deep_retreat_action(IDENTITY_ID, ENTRY, "start", now=now)

        self.assertTrue(result["ok"], result)
        self.assertEqual([PLAYER_ID], started_players)
        self.assertEqual(primary_before, state_module.get_identity_state(ACCOUNT_ID))
        target = state_module.get_identity_state(IDENTITY_ID)
        self.assertEqual("running", target["deep_retreat_phase"])
        self.assertEqual(now + 8 * 3600 + deep_retreat.CD_BUFFER_SEC, target["next_deep_retreat_time"])
        record = state_module.get_miniapp_state_records()[f"{IDENTITY_ID}:cave_deep_retreat"]["state"]
        self.assertTrue(record["identity_verified"])

    async def test_public_wrong_account_reply_never_becomes_success(self):
        before = copy.deepcopy(state_module._meta_state["identity_states"])
        wrong_result = {"ok": True, "status": "start", "action_dispatched": True, "data": action_data(ACCOUNT_ID)}
        with (
            patch.object(runtime, "_public_entry_allowed", return_value=True),
            patch.object(runtime, "_load_cave_public_identity_session", new=AsyncMock(return_value=self.session())),
            patch.object(runtime, "run_cave_deep_seclusion_action_production_flow", new=AsyncMock(return_value=wrong_result)),
            patch.object(runtime, "send_audit_log", new=AsyncMock()),
            patch.object(runtime, "save_state") as save,
        ):
            result = await runtime.run_cave_public_deep_retreat_action(IDENTITY_ID, ENTRY, "start", now=1_700_000_500)

        self.assertFalse(result["ok"])
        self.assertIn("cave_action_player_mismatch", result["message"])
        self.assertEqual(before, state_module._meta_state["identity_states"])
        save.assert_not_called()
        record = state_module.get_miniapp_state_records()[f"{IDENTITY_ID}:cave_deep_retreat"]["state"]
        self.assertFalse(record["ok"])
        self.assertFalse(record["identity_verified"])

    async def test_sync_does_not_trust_text_without_matching_account(self):
        before = copy.deepcopy(state_module._meta_state["identity_states"])
        for action in ("status", "start", "settle", "force"):
            for response in ({"actionResult": {"rawMessage": START_MESSAGE}}, action_data(ACCOUNT_ID)):
                with self.subTest(action=action, response=response):
                    result = await runtime.sync_cave_deep_seclusion_action_result(
                        IDENTITY_ID, action, response, now=1_700_000_500,
                    )
                    self.assertFalse(result["handled"])
                    self.assertEqual(before, state_module._meta_state["identity_states"])

    async def test_fate_deep_helper_passes_selected_player(self):
        for action in ("start", "status", "force", "settle"):
            flow = AsyncMock(return_value={"ok": True, "status": action, "action_dispatched": True, "data": action_data()})
            with (
                self.subTest(action=action),
                patch.object(runtime, "run_cave_deep_seclusion_action_production_flow", new=flow),
                patch.object(runtime, "sync_cave_deep_seclusion_action_result", new=AsyncMock(return_value={"handled": True})),
            ):
                result = await runtime._run_cave_public_deep_action_locked(
                    IDENTITY_ID, token="test", webview_url=ENTRY, action=action, session=self.session(action),
                    init_data="test_init_data", now=1_700_000_500,
                )
                self.assertTrue(result["ok"], result)
                self.assertTrue(result["sent"])
                self.assertEqual(PLAYER_ID, flow.await_args.kwargs["player_id"])

    async def test_missing_or_wrong_session_cannot_dispatch_or_reconcile(self):
        before = copy.deepcopy(state_module._meta_state["identity_states"])
        for player_id in (None, ACCOUNT_ID):
            session = {**self.session(), "player_id": player_id}
            flow = AsyncMock()
            with (
                self.subTest(player_id=player_id),
                patch.object(runtime, "_public_entry_allowed", return_value=True),
                patch.object(runtime, "_load_cave_public_identity_session", new=AsyncMock(return_value=session)),
                patch.object(runtime, "run_cave_deep_seclusion_action_production_flow", new=flow),
            ):
                result = await runtime.run_cave_public_deep_retreat_action(IDENTITY_ID, ENTRY, "settle", now=1_700_000_500)
                self.assertFalse(result["ok"])
                result = await runtime._run_cave_public_deep_action_locked(
                    IDENTITY_ID, token="test", webview_url=ENTRY, action="settle",
                    session=session, now=1_700_000_500,
                )
                self.assertFalse(result["ok"])
                self.assertFalse(result["sent"])
                flow.assert_not_awaited()
                self.assertEqual(before, state_module._meta_state["identity_states"])
