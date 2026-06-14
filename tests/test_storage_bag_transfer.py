import asyncio
import atexit
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
CREATED_ENV = False

if not ENV_PATH.exists():
    ENV_PATH.write_text(
        "\n".join(
            [
                "API_ID=12345",
                "API_HASH=00000000000000000000000000000000",
                "TG_PROXY_TYPE=",
                "TG_PROXY_HOST=127.0.0.1:7890",
                "LOG_GROUP_ID=0",
                "LOG_SEND_MODE=account",
                "ADMIN_ID=1",
                "CHAOGU_UI_HOST=127.0.0.1",
                "CHAOGU_UI_PORT=3030",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    CREATED_ENV = True

if CREATED_ENV:
    atexit.register(lambda: ENV_PATH.exists() and ENV_PATH.unlink())

sys.path.insert(0, str(PROJECT_ROOT))

from model import app
from model import state as state_module
from model import runtime
from model import ui
from model.features import storage_bag, workflow_log
from model.features.storage_bag import (
    apply_storage_bag_item_deltas,
    cancel_storage_bag_transfer_task,
    handle_storage_bag_transfer_reply,
    parse_storage_bag_item_counts,
    run_storage_bag_transfer_scheduler,
    start_storage_bag_transfer_task,
)


def _read_workflow_events(tmpdir):
    events = []
    for path in Path(tmpdir).glob("**/*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
    return events


class StorageBagTransferTests(unittest.TestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        storage_bag._clear_storage_bag_transfer_state()
        storage_bag._clear_storage_bag_transfer_batch_state()
        self.source_id = 1001
        self.target_id = 1002
        state_module.ensure_identity_registered(self.source_id)
        state_module.ensure_identity_registered(self.target_id)
        state_module.set_send_as_profile(self.source_id, label="来源号", username="source")
        state_module.set_send_as_profile(self.target_id, label="目标号", username="target")
        state_module.set_storage_bag_records({
            str(self.source_id): {
                "updated_at": 1000,
                "items": {"妖丹": 9, "木髓": 4, "绑定物": 1},
            },
            str(self.target_id): {
                "updated_at": 1000,
                "items": {"灵石": 100, "标记物": 1},
            },
        })
        state_module.set_storage_bag_item_rules({
            "妖丹": {"method": "basic", "tags": ["材料"]},
            "木髓": {"method": "gift", "tags": ["材料"]},
            "绑定物": {"method": "blocked", "tags": ["特殊"]},
        })

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        storage_bag._clear_storage_bag_transfer_state()
        storage_bag._clear_storage_bag_transfer_batch_state()

    def test_snapshot_includes_rules_and_transfer_identities(self):
        snapshot = ui.get_storage_bag_snapshot()

        self.assertIn("item_rules", snapshot)
        self.assertEqual("买卖", snapshot["item_rules"]["妖丹"]["method_label"])
        self.assertEqual("赠送", snapshot["item_rules"]["木髓"]["method_label"])
        self.assertEqual("basic", snapshot["item_rules"]["灵石"]["method"])
        self.assertFalse(snapshot["item_rules"]["绑定物"]["transfer_selectable"])
        self.assertEqual({self.source_id, self.target_id}, {row["identity_id"] for row in snapshot["transfer_identities"]})

    def test_snapshot_infers_categories_for_uncategorized_items(self):
        state_module.set_storage_bag_item_rules({})
        state_module.set_storage_bag_records({
            str(self.source_id): {
                "updated_at": 1000,
                "items": {
                    "灵石": 1,
                    "增元丹丹方": 1,
                    "玄铁剑图纸": 1,
                    "紫铖兜图谱": 1,
                    "玄天斩灵剑": 1,
                    "乱星海炼体第一人": 1,
                    "真仙试锋": 1,
                    "紫灵的轻吻": 1,
                    "稳控全场": 1,
                    "黄芽丹": 1,
                    "凝血草": 1,
                    "凝血草种子": 1,
                    "一阶妖丹": 1,
                    "法则碎片·水": 1,
                    "元磁山核·甲": 1,
                    "青竹蜂云剑（金雷竹·庚金相）": 1,
                    "虚天残图": 1,
                    "神行符": 1,
                    "第二元神残篇": 1,
                    "尘封的储物袋": 1,
                },
            },
            str(self.target_id): {
                "updated_at": 1000,
                "items": {},
            },
        })

        snapshot = ui.get_storage_bag_snapshot()

        expected_tags = {
            "灵石": "货币",
            "增元丹丹方": "丹方图纸图谱",
            "玄铁剑图纸": "丹方图纸图谱",
            "紫铖兜图谱": "丹方图纸图谱",
            "玄天斩灵剑": "装备武器防具",
            "乱星海炼体第一人": "称号",
            "真仙试锋": "称号",
            "紫灵的轻吻": "称号",
            "稳控全场": "特殊",
            "黄芽丹": "丹药",
            "凝血草": "灵草",
            "凝血草种子": "种子",
            "一阶妖丹": "材料",
            "法则碎片·水": "法则",
            "元磁山核·甲": "材料",
            "青竹蜂云剑（金雷竹·庚金相）": "装备武器防具",
            "虚天残图": "副本",
            "神行符": "符箓",
            "第二元神残篇": "特殊",
            "尘封的储物袋": "材料",
        }
        for item_name, expected_tag in expected_tags.items():
            self.assertEqual(expected_tag, snapshot["item_rules"][item_name]["tags"][0], item_name)

    def test_basic_transfer_preview_generates_listing_and_purchase_only(self):
        ok, message, preview = ui.ui_preview_storage_bag_transfer({
            "source_identity_id": self.source_id,
            "target_identity_id": self.target_id,
            "listing_item": "灵石",
            "items": [{"item_name": "妖丹", "quantity": 3}],
        })

        self.assertTrue(ok, message)
        self.assertEqual("已生成转移预览", message)
        self.assertEqual([
            {"identity_id": self.target_id, "command": ".上架 灵石 1 换 妖丹*3", "note": "目标身份上架换购物品"},
            {"identity_id": self.source_id, "command": ".购买 <挂单ID>", "note": "上架成功后来源身份购买挂单"},
        ], preview["commands"])
        self.assertIn("可手动开始执行", preview["summary"])

    def test_money_preset_preview_uses_compact_huangyadan_listing(self):
        state_module.set_storage_bag_records({
            str(self.source_id): {"updated_at": 1000, "items": {"灵石": 12000}},
            str(self.target_id): {"updated_at": 1000, "items": {"黄芽丹": 2}},
        })

        ok, message, preview = ui.ui_preview_storage_bag_transfer({
            "source_identity_id": self.source_id,
            "target_identity_id": self.target_id,
            "listing_item": "黄芽丹",
            "listing_count": 1,
            "listing_syntax": "compact",
            "items": [{"item_name": "灵石", "quantity": 5000}],
        })

        self.assertTrue(ok, message)
        self.assertEqual([
            {"identity_id": self.target_id, "command": ".上架 黄芽丹*1 换 灵石*5000", "note": "目标身份上架换购物品"},
            {"identity_id": self.source_id, "command": ".购买 <挂单ID>", "note": "上架成功后来源身份购买挂单"},
        ], preview["commands"])
        self.assertEqual(1, preview["listing_count"])
        self.assertEqual("compact", preview["listing_syntax"])

    def test_gift_transfer_preview_uses_target_marker_and_source_gift_reply(self):
        ok, message, preview = ui.ui_preview_storage_bag_transfer({
            "source_identity_id": self.source_id,
            "target_identity_id": self.target_id,
            "items": [{"item_name": "木髓", "quantity": 2}],
        })

        self.assertTrue(ok, message)
        self.assertEqual([
            {"identity_id": self.target_id, "command": "转移标记 <本次转移ID>", "note": "目标身份先发送一条可回复的标记消息"},
            {"identity_id": self.source_id, "command": ".赠送 木髓 2", "note": "来源身份回复目标身份标记消息发送"},
        ], preview["commands"])

    def test_batch_transfer_preview_defaults_to_all_non_protected_sources(self):
        other_id = 1003
        protected_id = 1004
        state_module.ensure_identity_registered(other_id)
        state_module.ensure_identity_registered(protected_id)
        state_module.set_send_as_profile(other_id, label="备用来源", username="other")
        state_module.set_send_as_profile(protected_id, label="WalterWA2000", username="wa2000")
        records = state_module.get_storage_bag_records()
        records[str(other_id)] = {"updated_at": 1000, "items": {"妖丹": 2, "木髓": 1}}
        records[str(protected_id)] = {"updated_at": 1000, "items": {"妖丹": 99}}
        state_module.set_storage_bag_records(records)

        ok, message, preview = ui.ui_preview_storage_bag_transfer({
            "batch": True,
            "target_identity_id": self.target_id,
            "listing_item": "灵石",
            "mode": "all",
            "items": [{"item_name": "妖丹", "quantity": 0}, {"item_name": "木髓", "quantity": 0}],
        })

        self.assertTrue(ok, message)
        self.assertEqual("已生成批量转移预览", message)
        self.assertEqual({self.source_id, other_id}, {task["source_identity_id"] for task in preview["tasks"]})
        self.assertNotIn(self.target_id, {task["source_identity_id"] for task in preview["tasks"]})
        self.assertNotIn(protected_id, {task["source_identity_id"] for task in preview["tasks"]})
        quantities = {
            (task["source_identity_id"], item["item_name"]): item["quantity"]
            for task in preview["tasks"]
            for item in task["items"]
        }
        self.assertEqual(9, quantities[(self.source_id, "妖丹")])
        self.assertEqual(4, quantities[(self.source_id, "木髓")])
        self.assertEqual(2, quantities[(other_id, "妖丹")])
        self.assertEqual(1, quantities[(other_id, "木髓")])

    def test_batch_transfer_preview_supports_subset_and_fixed_quantity(self):
        other_id = 1003
        state_module.ensure_identity_registered(other_id)
        state_module.set_send_as_profile(other_id, label="备用来源", username="other")
        records = state_module.get_storage_bag_records()
        records[str(other_id)] = {"updated_at": 1000, "items": {"妖丹": 2}}
        state_module.set_storage_bag_records(records)

        ok, message, preview = ui.ui_preview_storage_bag_transfer({
            "batch": True,
            "target_identity_id": self.target_id,
            "source_identity_ids": [other_id],
            "listing_item": "灵石",
            "mode": "fixed",
            "items": [{"item_name": "妖丹", "quantity": 3}],
        })

        self.assertTrue(ok, message)
        self.assertEqual([other_id], [task["source_identity_id"] for task in preview["tasks"]])
        self.assertEqual(2, preview["tasks"][0]["items"][0]["quantity"])

    def test_blocked_item_rejects_preview(self):
        ok, message, preview = ui.ui_preview_storage_bag_transfer({
            "source_identity_id": self.source_id,
            "target_identity_id": self.target_id,
            "items": [{"item_name": "绑定物", "quantity": 1}],
        })

        self.assertFalse(ok)
        self.assertEqual("绑定物 不可转移", message)
        self.assertIsNone(preview)

    def test_set_rule_persists_override_without_touching_command_send(self):
        with patch.object(ui, "send_game_command") as send_mock:
            ok, message = ui.ui_set_storage_bag_item_rule("妖丹", "gift", ["材料", "妖丹"], "测试")

        self.assertTrue(ok, message)
        self.assertEqual("gift", state_module.get_storage_bag_item_rules()["妖丹"]["method"])
        send_mock.assert_not_called()

    def test_apply_item_deltas_updates_sections_and_timestamp(self):
        with patch("model.features.storage_bag.time.time", return_value=2000.0):
            changed = apply_storage_bag_item_deltas(self.source_id, {"妖丹": 2, "木髓": -3})

        self.assertTrue(changed)
        record = state_module.get_storage_bag_records()[str(self.source_id)]
        self.assertEqual(11, record["items"]["妖丹"])
        self.assertEqual(1, record["items"]["木髓"])
        self.assertEqual(11, record["sections"]["材料"]["妖丹"])
        self.assertEqual(1, record["sections"]["材料"]["木髓"])
        self.assertEqual(2000.0, record["updated_at"])
        self.assertTrue(record["updated_at_text"])

    def test_apply_item_deltas_floors_at_zero(self):
        changed = apply_storage_bag_item_deltas(self.source_id, {"木髓": -99})

        self.assertTrue(changed)
        record = state_module.get_storage_bag_records()[str(self.source_id)]
        self.assertNotIn("木髓", record["items"])
        self.assertFalse(any("木髓" in section for section in (record.get("sections") or {}).values()))

    def test_parse_item_counts_from_real_reward_and_cost_text(self):
        self.assertEqual(
            {"灵石": 3000, "养魂木": 3},
            parse_storage_bag_item_counts("- 消耗：灵石x3000、养魂木x3", allow_plain=True),
        )
        self.assertEqual(
            {"金精矿": 15, "灵石": 110},
            parse_storage_bag_item_counts("你获得了：【金精矿】x15, 【灵石】x110。"),
        )
        self.assertEqual(
            {"灵石": 7},
            parse_storage_bag_item_counts("修为x200、贡献x5、灵石x7", allow_plain=True),
        )


class StorageBagTransferExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        storage_bag._clear_storage_bag_transfer_state()
        storage_bag._clear_storage_bag_transfer_batch_state()
        self.source_id = 1001
        self.target_id = 1002
        state_module.ensure_identity_registered(self.source_id)
        state_module.ensure_identity_registered(self.target_id)
        state_module.set_send_as_profile(self.source_id, label="来源号", username="source")
        state_module.set_send_as_profile(self.target_id, label="目标号", username="target")
        state_module.set_storage_bag_records({
            str(self.source_id): {"updated_at": 1000, "items": {"妖丹": 9, "木髓": 4, "灵石": 1000}, "sections": {"材料": {"妖丹": 9, "木髓": 4}, "法宝/丹药/杂物": {"灵石": 1000}}},
            str(self.target_id): {"updated_at": 1000, "items": {"灵石": 100, "标记物": 1}, "sections": {"法宝/丹药/杂物": {"灵石": 100, "标记物": 1}}},
        })

    def _inbox_summaries(self, inbox_mock):
        return [str(call.kwargs.get("summary") or "") for call in inbox_mock.call_args_list]

    async def asyncTearDown(self):
        await cancel_storage_bag_transfer_task()
        storage_bag._clear_storage_bag_transfer_state()
        storage_bag._clear_storage_bag_transfer_batch_state()
        ui._storage_bag_sync_state.update({"running": False, "pending_ids": [], "completed_ids": []})
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    async def test_suspected_game_bot_edit_routes_as_edit_event(self):
        event = SimpleNamespace(id=9452531, sender_id=888001, chat_id=-1001680975844)
        reply_to = SimpleNamespace(id=7001, raw_text=".卜筮问天")
        reply_context = {
            "send_as_id": self.target_id,
            "family": "divination",
            "reply_to_msg_id": 7001,
            "root_msg_id": 7001,
        }

        with patch.object(app, "_resolve_identity_sender_id", return_value=0), \
                patch.object(app, "_resolve_event_reply", new=AsyncMock(return_value=(reply_to, reply_context))), \
                patch.object(app, "_looks_like_game_bot_reply", return_value=True), \
                patch.object(app, "_handle_routed_reply_event", new=AsyncMock(return_value=True)) as routed_mock, \
                patch.object(app, "_note_game_bot_activity", new=AsyncMock()), \
                patch.object(app, "_record_suspected_game_bot", new=AsyncMock()):
            handled = await app._handle_suspected_game_bot_reply(event, "【神物现世】", 1000.0, edited=True)

        self.assertTrue(handled)
        self.assertEqual("edit", routed_mock.await_args.kwargs["event_kind"])

    async def test_routed_divination_final_edit_replays_after_start_notice_consumed(self):
        state_module.get_identity_state(self.target_id)["divination_enabled"] = True
        state_module.set_storage_bag_records({
            str(self.target_id): {"items": {"三级妖丹": 4, "养魂木": 1}, "sections": {}},
        })
        event = SimpleNamespace(id=9955440, sender_id=888001, chat_id=-1001680975844)
        reply_to = SimpleNamespace(id=9955438, raw_text=".卜筮问天")
        reply_context = {
            "send_as_id": self.target_id,
            "family": "divination",
            "reply_to_msg_id": 9955438,
            "root_msg_id": 9955438,
        }
        app._mark_runtime_message_consumed(event, "divination")
        final_text = (
            "【神物现世】！天机罗盘疯狂转动，最终指向一处被迷雾笼罩的上古神山！"
            "卦象显示，【昆吾通行令】的机缘已降临于你！\n\n"
            "天道示警：获取此等逆天之物，需献上祭品以获天道认可。\n"
            "你是否愿意消耗 【三级妖丹】x4、【养魂木】x1 来换取它？\n\n"
            "请在 5分钟 内回复本消息 .换取 来确认，超时则机缘消散。"
        )

        with patch("model.features.divination.send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=9966046))) as send_mock, \
                patch("model.features.divination.send_audit_log", new=AsyncMock()), \
                patch("model.features.divination.refresh_storage_bag_records_from_api", new=AsyncMock(return_value={"updated_identity_ids": [self.target_id], "updated_count": 1, "skipped_count": 0})), \
                patch("model.features.divination.save_state", return_value=True), \
                patch.object(app, "schedule_cleanup", new=AsyncMock()):
            handled = await app._handle_routed_reply_event(
                event,
                final_text,
                1000.0,
                reply_to,
                reply_context,
                event_kind="edit",
            )

        self.assertTrue(handled)
        self.assertEqual(".换取", send_mock.await_args.args[0])
        self.assertEqual(self.target_id, send_mock.await_args.kwargs["send_as_id"])
        self.assertEqual(9955440, send_mock.await_args.kwargs["reply_to"])

    async def test_unhandled_routed_reply_records_passive_evidence(self):
        event = SimpleNamespace(id=9966101, sender_id=888001, chat_id=-1001680975844)
        reply_to = SimpleNamespace(id=9966099, raw_text=".卜筮问天")
        reply_context = {
            "send_as_id": self.target_id,
            "family": "divination",
            "reply_to_msg_id": 9966099,
            "root_msg_id": 9966099,
        }
        text = "【问天异象】\n天机云纹变幻，结果稍后自显。"

        with patch.object(app, "record_unhandled_routed_reply", return_value=True) as contract_mock, \
                patch.object(app, "schedule_cleanup", new=AsyncMock()):
            handled = await app._handle_routed_reply_event(
                event,
                text,
                1000.0,
                reply_to,
                reply_context,
                event_kind="edit",
            )

        self.assertFalse(handled)
        contract_mock.assert_called_once_with(
            event,
            text,
            self.target_id,
            "divination",
            9966099,
            event_kind="edit",
            reply_to_sender_id=0,
        )

    async def test_resolve_event_reply_keeps_reply_sender_as_evidence_only(self):
        reply_to = SimpleNamespace(id=9966201, sender_id=8325841058, raw_text=".侍妾远航 冒险")
        event = SimpleNamespace(get_reply_message=AsyncMock(return_value=reply_to))
        base_context = {
            "send_as_id": None,
            "family": "concubine_voyage",
            "reply_to_msg_id": 9966201,
            "root_msg_id": 9966201,
            "matched_via": "reply_object",
        }

        with patch.object(app, "_get_event_reply_header_msg_id", return_value=9966201), \
                patch.object(app, "get_reply_context", return_value=dict(base_context)):
            resolved_reply, reply_context = await app._resolve_event_reply(event)

        self.assertIs(resolved_reply, reply_to)
        self.assertIsNone(reply_context["send_as_id"])
        self.assertEqual(8325841058, reply_context["reply_to_sender_id"])

    async def test_storage_bag_sync_uses_background_task_wrapper(self):
        def close_coro(coro):
            coro.close()

        with patch.object(ui, "_fire_and_forget", side_effect=close_coro) as fire_mock:
            ok, message = await ui.ui_start_storage_bag_sync([self.source_id, self.target_id])

        self.assertTrue(ok, message)
        self.assertEqual(f"已开始同步 2 个身份的储物袋", message)
        self.assertTrue(ui._storage_bag_sync_state["running"])
        self.assertEqual([self.source_id, self.target_id], ui._storage_bag_sync_state["pending_ids"])
        fire_mock.assert_called_once()

    async def test_storage_bag_sync_rejects_while_transfer_running(self):
        storage_bag._storage_bag_transfer_state.update({
            "running": True,
            "step": "waiting_listing_reply",
            "source_identity_id": self.source_id,
            "target_identity_id": self.target_id,
        })

        with patch.object(ui, "_fire_and_forget") as fire_mock:
            ok, message = await ui.ui_start_storage_bag_sync([self.source_id])

        self.assertFalse(ok)
        self.assertIn("转移正在进行中", message)
        self.assertFalse(ui._storage_bag_sync_state["running"])
        fire_mock.assert_not_called()

    async def test_storage_bag_transfer_rejects_while_sync_running(self):
        ui._storage_bag_sync_state.update({"running": True, "pending_ids": [self.source_id], "completed_ids": []})

        with patch("model.ui.start_storage_bag_transfer_task", new=AsyncMock()) as start_mock:
            ok, message, transfer = await ui.ui_start_storage_bag_transfer({
                "source_identity_id": self.source_id,
                "target_identity_id": self.target_id,
                "listing_item": "灵石",
                "items": [{"item_name": "妖丹", "quantity": 1}],
            })

        self.assertFalse(ok)
        self.assertIn("同步正在进行中", message)
        self.assertIsNone(transfer)
        start_mock.assert_not_called()

    async def test_storage_bag_batch_transfer_rejects_while_sync_running(self):
        ui._storage_bag_sync_state.update({"running": True, "pending_ids": [self.source_id], "completed_ids": []})

        with patch("model.ui.start_storage_bag_transfer_batch", new=AsyncMock()) as start_mock:
            ok, message, transfer = await ui.ui_start_storage_bag_transfer_batch({
                "target_identity_id": self.target_id,
                "listing_item": "灵石",
                "items": [{"item_name": "妖丹", "quantity": 1}],
            })

        self.assertFalse(ok)
        self.assertIn("同步正在进行中", message)
        self.assertIsNone(transfer)
        start_mock.assert_not_called()

    async def test_transfer_task_rejects_invalid_direct_payload_before_sending(self):
        invalid_cases = [
            (9999, self.target_id, [{"item_name": "妖丹", "quantity": 1, "method": "basic"}], "灵石", "来源身份无效"),
            (self.source_id, 9999, [{"item_name": "妖丹", "quantity": 1, "method": "basic"}], "灵石", "目标身份无效"),
            (self.source_id, self.source_id, [{"item_name": "妖丹", "quantity": 1, "method": "basic"}], "灵石", "来源和目标身份不能相同"),
            (self.source_id, self.target_id, [{"item_name": "妖丹", "quantity": -1, "method": "basic"}], "灵石", "妖丹 数量必须大于 0"),
            (self.source_id, self.target_id, [{"item_name": "绑定物", "quantity": 1, "method": "blocked"}], "灵石", "绑定物 不可转移"),
            (self.source_id, self.target_id, [{"item_name": "妖丹", "quantity": 1, "method": "bad"}], "灵石", "妖丹 转移方式无效"),
        ]

        with patch("model.features.storage_bag.send_game_command") as send_mock:
            for source_id, target_id, items, listing_item, expected_message in invalid_cases:
                ok, message, transfer = await start_storage_bag_transfer_task(source_id, target_id, items, listing_item)
                self.assertFalse(ok)
                self.assertEqual(expected_message, message)
                self.assertIsNone(transfer)

        send_mock.assert_not_called()
        self.assertFalse(storage_bag._storage_bag_transfer_state["running"])

    async def test_basic_transfer_executes_listing_then_buy_and_syncs_records(self):
        sent = []

        async def fake_send(command, **kwargs):
            sent.append((command, kwargs))
            return SimpleNamespace(id=100 + len(sent))

        with patch("model.features.storage_bag.send_game_command", side_effect=fake_send), \
                patch("model.features.storage_bag.send_audit_log"), \
                patch("model.features.passive_inbox.record_passive_inbox_event") as inbox_mock:
            ok, message, transfer = await start_storage_bag_transfer_task(
                self.source_id,
                self.target_id,
                [{"item_name": "妖丹", "quantity": 3, "method": "basic"}],
                "灵石",
            )
            self.assertTrue(ok, message)
            self.assertEqual(".上架 灵石 1 换 妖丹*3", sent[0][0])
            ignored_listing = await handle_storage_bag_transfer_reply(
                "上架成功！\n你已将 【灵石】x1 上架至万宝楼。\n捆绑总价: 妖丹*3\n挂单ID: 777",
                999.0,
                SimpleNamespace(id=999, raw_text=sent[0][0]),
                reply_context={"reply_to_msg_id": 999},
            )
            self.assertFalse(ignored_listing)
            self.assertEqual("waiting_listing_reply", storage_bag._storage_bag_transfer_state["step"])
            self.assertEqual(1, len(sent))
            handled_listing = await handle_storage_bag_transfer_reply(
                "上架成功！\n你已将 【灵石】x1 上架至万宝楼。\n捆绑总价: 妖丹*3\n挂单ID: 888",
                1000.0,
                SimpleNamespace(id=101, raw_text=sent[0][0]),
                reply_context={"reply_to_msg_id": 101},
            )
            self.assertTrue(handled_listing)
            self.assertEqual(".购买 888", sent[1][0])
            handled_buy = await handle_storage_bag_transfer_reply(
                "交易成功！你成功购得 【灵石】x1。",
                1001.0,
                SimpleNamespace(id=102, raw_text=sent[1][0]),
                reply_context={"reply_to_msg_id": 102},
            )

        self.assertTrue(handled_buy)
        summaries = self._inbox_summaries(inbox_mock)
        self.assertTrue(any("上架已发送" in summary and ".上架 灵石 1 换 妖丹*3" in summary for summary in summaries))
        self.assertTrue(any("准备购买" in summary and "挂单ID=888" in summary for summary in summaries))
        self.assertTrue(any("购买已发送" in summary and "挂单ID=888" in summary for summary in summaries))
        self.assertTrue(any("购买成功" in summary and "挂单ID=888" in summary for summary in summaries))
        self.assertTrue(any(
            call.kwargs.get("family") == "storage_bag_buy"
            and call.kwargs.get("decision") == "buy_success_inventory_synced"
            and call.kwargs.get("reply_to_msg_id") == 102
            and "本地核销=1项" in str(call.kwargs.get("summary") or "")
            for call in inbox_mock.call_args_list
        ))
        records = state_module.get_storage_bag_records()
        self.assertEqual(6, records[str(self.source_id)]["items"]["妖丹"])
        self.assertEqual(1, records[str(self.source_id)]["items"].get("灵石", 0) - 1000)
        self.assertEqual(3, records[str(self.target_id)]["items"]["妖丹"])
        self.assertEqual(99, records[str(self.target_id)]["items"]["灵石"])

    async def test_money_preset_transfer_executes_compact_listing_and_syncs_records(self):
        state_module.set_storage_bag_records({
            str(self.source_id): {"updated_at": 1000, "items": {"灵石": 12000}},
            str(self.target_id): {"updated_at": 1000, "items": {"黄芽丹": 2}},
        })
        sent = []

        async def fake_send(command, **kwargs):
            sent.append((command, kwargs))
            return SimpleNamespace(id=200 + len(sent))

        with patch("model.features.storage_bag.send_game_command", side_effect=fake_send), \
                patch("model.features.storage_bag.send_audit_log"), \
                patch("model.features.passive_inbox.record_passive_inbox_event"):
            ok, message, transfer = await start_storage_bag_transfer_task(
                self.source_id,
                self.target_id,
                [{"item_name": "灵石", "quantity": 5000, "method": "basic"}],
                "黄芽丹",
                listing_count=1,
                listing_syntax="compact",
            )
            self.assertTrue(ok, message)
            self.assertEqual(".上架 黄芽丹*1 换 灵石*5000", sent[0][0])

            handled_listing = await handle_storage_bag_transfer_reply(
                "上架成功！\n你已将 【黄芽丹】x1 上架至万宝楼。\n捆绑总价: 灵石*5000\n挂单ID: 1888",
                1000.0,
                SimpleNamespace(id=201, raw_text=sent[0][0]),
                reply_context={"reply_to_msg_id": 201},
            )
            self.assertTrue(handled_listing)
            self.assertEqual(".购买 1888", sent[1][0])

            handled_buy = await handle_storage_bag_transfer_reply(
                "交易成功！你成功购得 【黄芽丹】x1。",
                1001.0,
                SimpleNamespace(id=202, raw_text=sent[1][0]),
                reply_context={"reply_to_msg_id": 202},
            )

        self.assertTrue(handled_buy)
        records = state_module.get_storage_bag_records()
        self.assertEqual(7000, records[str(self.source_id)]["items"]["灵石"])
        self.assertEqual(1, records[str(self.source_id)]["items"]["黄芽丹"])
        self.assertEqual(5000, records[str(self.target_id)]["items"]["灵石"])
        self.assertEqual(1, records[str(self.target_id)]["items"]["黄芽丹"])

    async def test_single_transfer_start_queues_while_another_transfer_is_running(self):
        sent = []

        async def fake_send(command, **kwargs):
            msg_id = 650 + len(sent)
            sent.append((command, kwargs, msg_id))
            return SimpleNamespace(id=msg_id)

        with patch("model.features.storage_bag.send_game_command", side_effect=fake_send), \
                patch("model.features.storage_bag.send_audit_log"), \
                patch("model.features.passive_inbox.record_passive_inbox_event"):
            ok, message, _snapshot = await ui.ui_start_storage_bag_transfer({
                "source_identity_id": self.source_id,
                "target_identity_id": self.target_id,
                "listing_item": "灵石",
                "items": [{"item_name": "妖丹", "quantity": 3}],
            })
            self.assertTrue(ok, message)
            self.assertEqual(".上架 灵石 1 换 妖丹*3", sent[0][0])

            queued_ok, queued_message, queued_snapshot = await ui.ui_start_storage_bag_transfer({
                "source_identity_id": self.source_id,
                "target_identity_id": self.target_id,
                "listing_item": "灵石",
                "items": [{"item_name": "木髓", "quantity": 2}],
            })
            self.assertTrue(queued_ok, queued_message)
            self.assertIn("队列", queued_message)
            self.assertEqual(1, len(sent))
            self.assertTrue(queued_snapshot["batch"]["running"])
            self.assertEqual(1, len(queued_snapshot["batch"]["queue"]))
            self.assertIsNone(queued_snapshot["batch"]["active_task"])

            handled_listing = await handle_storage_bag_transfer_reply(
                "上架成功！\n"
                "你已将 【灵石】x1 上架至万宝楼。\n"
                "捆绑总价: 妖丹*3\n"
                "挂单ID: 902",
                1000.0,
                SimpleNamespace(id=sent[0][2], raw_text=sent[0][0]),
                reply_context={"reply_to_msg_id": sent[0][2]},
            )
            self.assertTrue(handled_listing)

            handled_buy = await handle_storage_bag_transfer_reply(
                "交易成功！你成功购得 【灵石】x1。",
                1001.0,
                SimpleNamespace(id=sent[1][2], raw_text=sent[1][0]),
                reply_context={"reply_to_msg_id": sent[1][2]},
            )
            self.assertTrue(handled_buy)

            await run_storage_bag_transfer_scheduler(1002.0)
            await asyncio.sleep(0.05)

            self.assertGreaterEqual(len(sent), 3)
            self.assertEqual(".上架 灵石 1 换 木髓*2", sent[2][0])
            self.assertEqual("running_task", storage_bag._storage_bag_transfer_batch_state["status"])
            self.assertEqual(self.source_id, storage_bag._storage_bag_transfer_batch_state["active_task"]["source_identity_id"])
            self.assertEqual([], storage_bag._storage_bag_transfer_batch_state["queue"])

    async def test_batch_transfer_advances_serially_after_each_success(self):
        other_id = 1003
        state_module.ensure_identity_registered(other_id)
        state_module.set_send_as_profile(other_id, label="备用来源", username="other")
        state_module.set_storage_bag_records({
            str(self.source_id): {"updated_at": 1000, "items": {"妖丹": 9, "木髓": 4, "灵石": 1000}, "sections": {"材料": {"妖丹": 9, "木髓": 4}, "法宝/丹药/杂物": {"灵石": 1000}}},
            str(self.target_id): {"updated_at": 1000, "items": {"灵石": 100, "标记物": 1}, "sections": {"法宝/丹药/杂物": {"灵石": 100, "标记物": 1}}},
            str(other_id): {"updated_at": 1000, "items": {"妖丹": 2, "灵石": 500}, "sections": {"材料": {"妖丹": 2}, "法宝/丹药/杂物": {"灵石": 500}}},
        })

        sent = []

        async def fake_send(command, **kwargs):
            msg_id = 700 + len(sent)
            sent.append((command, kwargs, msg_id))
            return SimpleNamespace(id=msg_id)

        with patch("model.features.storage_bag.send_game_command", side_effect=fake_send), \
                patch("model.features.storage_bag.send_audit_log"), \
                patch("model.features.passive_inbox.record_passive_inbox_event"):
            ok, message, snapshot = await ui.ui_start_storage_bag_transfer({
                "batch": True,
                "target_identity_id": self.target_id,
                "listing_item": "灵石",
                "mode": "all",
                "items": [{"item_name": "妖丹", "quantity": 0}],
            })

            self.assertTrue(ok, message)
            self.assertTrue(snapshot["batch"]["running"])
            self.assertEqual("running_task", snapshot["batch"]["status"])
            self.assertEqual(".上架 灵石 1 换 妖丹*9", sent[0][0])

            handled_listing = await handle_storage_bag_transfer_reply(
                "上架成功！\n"
                "你已将 【灵石】x1 上架至万宝楼。\n"
                "捆绑总价: 妖丹*9\n"
                "挂单ID: 901",
                1000.0,
                SimpleNamespace(id=sent[0][2], raw_text=sent[0][0]),
                reply_context={"reply_to_msg_id": sent[0][2]},
            )
            self.assertTrue(handled_listing)

            handled_buy = await handle_storage_bag_transfer_reply(
                "交易成功！你成功购得 【灵石】x1。",
                1001.0,
                SimpleNamespace(id=sent[1][2], raw_text=sent[1][0]),
                reply_context={"reply_to_msg_id": sent[1][2]},
            )
            self.assertTrue(handled_buy)

            await asyncio.sleep(0.05)

            self.assertGreaterEqual(len(sent), 3)
            self.assertEqual(".上架 灵石 1 换 妖丹*2", sent[2][0])
            self.assertEqual(other_id, storage_bag._storage_bag_transfer_state["source_identity_id"])
            self.assertEqual(1, len(storage_bag._storage_bag_transfer_batch_state["completed"]))
            self.assertEqual(other_id, storage_bag._storage_bag_transfer_batch_state["active_task"]["source_identity_id"])
            self.assertEqual([], storage_bag._storage_bag_transfer_batch_state["queue"])

    async def test_batch_transfer_stops_queue_on_failure_by_default(self):
        other_id = 1003
        state_module.ensure_identity_registered(other_id)
        state_module.set_send_as_profile(other_id, label="备用来源", username="other")
        state_module.set_storage_bag_records({
            str(self.source_id): {"updated_at": 1000, "items": {"妖丹": 9, "灵石": 1000}, "sections": {"材料": {"妖丹": 9}, "法宝/丹药/杂物": {"灵石": 1000}}},
            str(self.target_id): {"updated_at": 1000, "items": {"灵石": 100}, "sections": {"法宝/丹药/杂物": {"灵石": 100}}},
            str(other_id): {"updated_at": 1000, "items": {"妖丹": 2, "灵石": 500}, "sections": {"材料": {"妖丹": 2}, "法宝/丹药/杂物": {"灵石": 500}}},
        })

        sent = []

        async def fake_send(command, **kwargs):
            msg_id = 800 + len(sent)
            sent.append((command, kwargs, msg_id))
            return SimpleNamespace(id=msg_id)

        with patch("model.features.storage_bag.send_game_command", side_effect=fake_send), \
                patch("model.features.storage_bag.send_audit_log"), \
                patch("model.features.passive_inbox.record_passive_inbox_event"):
            ok, message, _snapshot = await ui.ui_start_storage_bag_transfer({
                "batch": True,
                "target_identity_id": self.target_id,
                "listing_item": "灵石",
                "items": [{"item_name": "妖丹", "quantity": 0}],
            })
            self.assertTrue(ok, message)

            handled = await handle_storage_bag_transfer_reply(
                "价格格式错误，请重新上架。",
                1000.0,
                SimpleNamespace(id=sent[0][2], raw_text=sent[0][0]),
                reply_context={"reply_to_msg_id": sent[0][2]},
            )
            self.assertTrue(handled)

            await asyncio.sleep(0.05)

            self.assertFalse(storage_bag._storage_bag_transfer_batch_state["running"])
            self.assertEqual("failed", storage_bag._storage_bag_transfer_batch_state["status"])
            self.assertEqual([], storage_bag._storage_bag_transfer_batch_state["queue"])
            self.assertEqual(1, len(storage_bag._storage_bag_transfer_batch_state["failed"]))
            self.assertEqual(1, len(sent))

    async def test_batch_transfer_immediate_send_failure_does_not_double_advance(self):
        other_id = 1003
        state_module.ensure_identity_registered(other_id)
        state_module.set_send_as_profile(other_id, label="备用来源", username="other")
        state_module.set_storage_bag_records({
            str(self.source_id): {"updated_at": 1000, "items": {"妖丹": 9, "灵石": 1000}, "sections": {"材料": {"妖丹": 9}, "法宝/丹药/杂物": {"灵石": 1000}}},
            str(self.target_id): {"updated_at": 1000, "items": {"灵石": 100}, "sections": {"法宝/丹药/杂物": {"灵石": 100}}},
            str(other_id): {"updated_at": 1000, "items": {"妖丹": 2, "灵石": 500}, "sections": {"材料": {"妖丹": 2}, "法宝/丹药/杂物": {"灵石": 500}}},
        })

        sent = []

        async def fake_send(command, **kwargs):
            sent.append((command, kwargs))
            return None

        with patch("model.features.storage_bag.send_game_command", side_effect=fake_send), \
                patch("model.features.storage_bag.send_audit_log"), \
                patch("model.features.passive_inbox.record_passive_inbox_event"):
            ok, message, _snapshot = await ui.ui_start_storage_bag_transfer({
                "batch": True,
                "target_identity_id": self.target_id,
                "listing_item": "灵石",
                "items": [{"item_name": "妖丹", "quantity": 0}],
            })

            await asyncio.sleep(0.05)

        self.assertFalse(ok)
        self.assertIn("上架命令发送失败", message)
        self.assertFalse(storage_bag._storage_bag_transfer_batch_state["running"])
        self.assertEqual("failed", storage_bag._storage_bag_transfer_batch_state["status"])
        self.assertEqual(1, len(storage_bag._storage_bag_transfer_batch_state["failed"]))
        self.assertEqual(1, len(sent))

    async def test_storage_transfer_workflow_log_tracks_stale_listing_and_inventory_sync(self):
        sent = []

        async def fake_send(command, **kwargs):
            sent.append((command, kwargs))
            return SimpleNamespace(id=100 + len(sent))

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("model.features.storage_bag.send_game_command", side_effect=fake_send), \
                    patch("model.features.storage_bag.send_audit_log"), \
                    patch.object(workflow_log, "WORKFLOW_LOG_DIR", tmpdir), \
                    patch("model.features.passive_inbox.record_passive_inbox_event"):
                ok, message, _transfer = await start_storage_bag_transfer_task(
                    self.source_id,
                    self.target_id,
                    [{"item_name": "妖丹", "quantity": 3, "method": "basic"}],
                    "灵石",
                )
                self.assertTrue(ok, message)
                op_id = storage_bag._storage_bag_transfer_state["op_id"]

                handled_listing = await handle_storage_bag_transfer_reply(
                    "上架成功！\n你已将 【灵石】x1 上架至万宝楼。\n捆绑总价: 妖丹*3\n挂单ID: 888",
                    1000.0,
                    SimpleNamespace(id=101, raw_text=sent[0][0]),
                    reply_context={"reply_to_msg_id": 101},
                )
                self.assertTrue(handled_listing)

                ignored_listing = await handle_storage_bag_transfer_reply(
                    "上架成功！\n你已将 【灵石】x1 上架至万宝楼。\n捆绑总价: 妖丹*3\n挂单ID: 777",
                    1001.0,
                    SimpleNamespace(id=999, raw_text=sent[0][0]),
                    matched_family="storage_bag_listing",
                    reply_context={"reply_to_msg_id": 999, "family": "storage_bag_listing"},
                )
                self.assertFalse(ignored_listing)

                handled_buy = await handle_storage_bag_transfer_reply(
                    "交易成功！你成功购得 【灵石】x1。",
                    1002.0,
                    SimpleNamespace(id=102, raw_text=sent[1][0]),
                    matched_family="storage_bag_buy",
                    reply_context={"reply_to_msg_id": 102, "family": "storage_bag_buy"},
                )
                self.assertTrue(handled_buy)

                events = _read_workflow_events(tmpdir)

        self.assertTrue(all(event.get("workflow") == "storage_bag_transfer" for event in events))
        self.assertTrue(any(event.get("op_id") == op_id and event.get("event") == "准备购买" for event in events))
        self.assertTrue(any(
            event.get("decision") == "stale_listing_reply_ignored"
            and "回执挂单ID=777" in event.get("detail", {}).get("detail", "")
            and "当前挂单ID=888" in event.get("detail", {}).get("detail", "")
            for event in events
        ))
        self.assertTrue(any(
            event.get("decision") == "buy_success_inventory_synced"
            and event.get("detail", {}).get("listing_id") == "888"
            and "本地核销=1项" in event.get("detail", {}).get("detail", "")
            for event in events
        ))

    async def test_storage_transfer_commands_route_without_pending_tasks(self):
        with state_module.use_identity(self.target_id) as identity_state:
            identity_state["my_msg_ids"][301] = 1000.0
        context = runtime.get_reply_context(SimpleNamespace(id=301, raw_text=".上架 灵石 1 换 妖丹*3"))

        self.assertEqual(self.target_id, context["send_as_id"])
        self.assertEqual("storage_bag_listing", context["family"])

    async def test_routed_waiting_listing_reply_preserves_pending_until_final_edit(self):
        sent = []

        async def fake_send(command, **kwargs):
            sent.append((command, kwargs))
            return SimpleNamespace(id=400 + len(sent))

        with patch("model.features.storage_bag.send_game_command", side_effect=fake_send):
            ok, message, transfer = await start_storage_bag_transfer_task(
                self.source_id,
                self.target_id,
                [{"item_name": "妖丹", "quantity": 3, "method": "basic"}],
                "灵石",
            )
        self.assertTrue(ok, message)
        listing_msg_id = int(storage_bag._storage_bag_transfer_state["listing_msg_id"])
        with state_module.use_identity(self.target_id) as identity_state:
            identity_state["pending_tasks"][listing_msg_id] = {
                "cmd": sent[0][0],
                "sent_at": 1000.0,
                "retry": 0,
                "timeout": 60,
                "reply_to_msg_id": 0,
                "priority": "normal",
                "max_retry": 1,
            }

        reply_to = SimpleNamespace(id=listing_msg_id, raw_text=sent[0][0])
        reply_context = {
            "send_as_id": self.target_id,
            "family": "storage_bag_listing",
            "reply_to_msg_id": listing_msg_id,
            "root_msg_id": listing_msg_id,
        }

        with patch.object(app, "schedule_cleanup", new=AsyncMock()):
            handled_waiting = await app._handle_routed_reply_event(
                SimpleNamespace(id=9452531, chat_id=-1001680975844),
                "正在思考，请稍等……",
                1001.0,
                reply_to,
                reply_context,
            )

        self.assertFalse(handled_waiting)
        with state_module.use_identity(self.target_id) as identity_state:
            self.assertIn(listing_msg_id, identity_state["pending_tasks"])
        self.assertEqual("waiting_listing_reply", storage_bag._storage_bag_transfer_state["step"])

        with patch("model.features.storage_bag.send_game_command", side_effect=fake_send), \
                patch("model.features.storage_bag.send_audit_log"), \
                patch.object(app, "schedule_cleanup", new=AsyncMock()):
            handled_final = await app._handle_routed_reply_event(
                SimpleNamespace(id=9452531, chat_id=-1001680975844),
                "上架成功！\n"
                "你已将 【灵石】x1 上架至万宝楼。\n"
                "捆绑总价: 妖丹*3\n"
                "挂单ID: 888",
                1002.0,
                reply_to,
                reply_context,
                event_kind="edit",
            )

        self.assertTrue(handled_final)
        self.assertEqual(".购买 888", sent[-1][0])
        with state_module.use_identity(self.target_id) as identity_state:
            self.assertNotIn(listing_msg_id, identity_state["pending_tasks"])
        self.assertEqual("waiting_buy_reply", storage_bag._storage_bag_transfer_state["step"])

    async def test_manual_listing_reply_can_resume_stuck_listing_command(self):
        sent = []

        async def fake_send(command, **kwargs):
            sent.append((command, kwargs))
            return SimpleNamespace(id=500 + len(sent))

        with patch("model.features.storage_bag.send_game_command", side_effect=fake_send):
            ok, message, transfer = await start_storage_bag_transfer_task(
                self.source_id,
                self.target_id,
                [{"item_name": "妖丹", "quantity": 3, "method": "basic"}],
                "灵石",
            )

        self.assertTrue(ok, message)
        original_listing_msg_id = int(storage_bag._storage_bag_transfer_state["listing_msg_id"])
        manual_listing_msg_id = original_listing_msg_id + 99
        manual_reply_to = SimpleNamespace(id=manual_listing_msg_id, raw_text=sent[0][0])
        manual_context = {
            "send_as_id": self.target_id,
            "family": "storage_bag_listing",
            "reply_to_msg_id": manual_listing_msg_id,
            "root_msg_id": manual_listing_msg_id,
        }

        with patch("model.features.passive_inbox.record_passive_inbox_event") as inbox_mock:
            with patch("model.features.storage_bag.send_game_command", side_effect=fake_send), patch("model.features.storage_bag.send_audit_log"):
                handled_manual = await handle_storage_bag_transfer_reply(
                    "上架成功！\n"
                    "你已将 【灵石】x1 上架至万宝楼。\n"
                    "每件售价: 【妖丹】x3\n"
                    "挂单ID: 889",
                    1000.0,
                    manual_reply_to,
                    matched_family="storage_bag_listing",
                    reply_context=manual_context,
                )

            self.assertTrue(handled_manual)
            self.assertEqual(".购买 889", sent[-1][0])
            self.assertEqual("waiting_buy_reply", storage_bag._storage_bag_transfer_state["step"])

            ignored_original = await handle_storage_bag_transfer_reply(
                "上架成功！\n"
                "你已将 【灵石】x1 上架至万宝楼。\n"
                "每件售价: 【妖丹】x3\n"
                "挂单ID: 888",
                1001.0,
                SimpleNamespace(id=original_listing_msg_id, raw_text=sent[0][0]),
                matched_family="storage_bag_listing",
                reply_context={
                    "send_as_id": self.target_id,
                    "family": "storage_bag_listing",
                    "reply_to_msg_id": original_listing_msg_id,
                    "root_msg_id": original_listing_msg_id,
                },
            )

        self.assertFalse(ignored_original)
        self.assertEqual(".购买 889", sent[-1][0])
        summaries = self._inbox_summaries(inbox_mock)
        self.assertTrue(any("采纳手动补发" in summary and "挂单ID=889" in summary for summary in summaries))
        self.assertTrue(any("忽略上架回执" in summary and "回执挂单ID=888" in summary and "当前挂单ID=889" in summary for summary in summaries))
        self.assertTrue(any(
            call.kwargs.get("decision") == "manual_listing_accepted"
            and call.kwargs.get("family") == "storage_bag_listing"
            and call.kwargs.get("reply_to_msg_id") == manual_listing_msg_id
            and "上架成功" in str(call.kwargs.get("matched_text") or "")
            for call in inbox_mock.call_args_list
        ))

    async def test_cancel_rejects_inflight_buy_send(self):
        storage_bag._storage_bag_transfer_state.update({
            "running": True,
            "step": "buying",
            "source_identity_id": self.source_id,
            "target_identity_id": self.target_id,
        })

        with patch("model.features.passive_inbox.record_passive_inbox_event") as inbox_mock:
            ok, message, snapshot = await cancel_storage_bag_transfer_task()

        self.assertFalse(ok)
        self.assertIn("不能安全取消", message)
        self.assertTrue(snapshot["running"])
        self.assertEqual("buying", snapshot["step"])
        summaries = self._inbox_summaries(inbox_mock)
        self.assertTrue(any("取消被拒绝" in summary and "step=buying" in summary for summary in summaries))

    async def test_transfer_event_recording_failure_is_non_fatal(self):
        sent = []

        async def fake_send(command, **kwargs):
            sent.append((command, kwargs))
            return SimpleNamespace(id=600 + len(sent))

        with patch("model.features.storage_bag.send_game_command", side_effect=fake_send), \
                patch("model.features.passive_inbox.record_passive_inbox_event", side_effect=RuntimeError("disk full")):
            ok, message, transfer = await start_storage_bag_transfer_task(
                self.source_id,
                self.target_id,
                [{"item_name": "妖丹", "quantity": 3, "method": "basic"}],
                "灵石",
            )

        self.assertTrue(ok, message)
        self.assertEqual("waiting_listing_reply", storage_bag._storage_bag_transfer_state["step"])
        self.assertEqual([".上架 灵石 1 换 妖丹*3"], [item[0] for item in sent])

    async def test_gift_transfer_sends_locator_and_gift_reply_then_syncs_tax(self):
        sent = []

        async def fake_send(command, **kwargs):
            sent.append((command, kwargs))
            return SimpleNamespace(id=200 + len(sent))

        with patch("model.features.storage_bag.send_game_command", side_effect=fake_send), patch("model.features.storage_bag.random.choice", return_value="稍等"), patch("model.features.storage_bag._delete_storage_bag_gift_locator", return_value=True), patch("model.features.storage_bag.send_audit_log"):
            ok, message, transfer = await start_storage_bag_transfer_task(
                self.source_id,
                self.target_id,
                [{"item_name": "木髓", "quantity": 2, "method": "gift"}],
                "",
            )
            self.assertTrue(ok, message)
            self.assertEqual("稍等", sent[0][0])
            self.assertEqual(".赠送 木髓 2", sent[1][0])
            self.assertEqual(201, sent[1][1]["reply_to"])
            handled = await handle_storage_bag_transfer_reply(
                "【赠送成功】\n"
                "道友 @WalterWA2000 向 @takaranoao_bot 赠送了 【木髓】x2。\n"
                "并额外支付了 50 灵石作为因果税 (基础税率 10%)。",
                1000.0,
                SimpleNamespace(id=202, raw_text=sent[1][0]),
                reply_context={"reply_to_msg_id": 202},
            )

        self.assertTrue(handled)
        records = state_module.get_storage_bag_records()
        self.assertEqual(2, records[str(self.source_id)]["items"]["木髓"])
        self.assertEqual(950, records[str(self.source_id)]["items"]["灵石"])
        self.assertEqual(2, records[str(self.target_id)]["items"]["木髓"])

    async def test_gift_transfer_rejects_success_reply_for_different_item(self):
        sent = []

        async def fake_send(command, **kwargs):
            sent.append((command, kwargs))
            return SimpleNamespace(id=200 + len(sent))

        with patch("model.features.storage_bag.send_game_command", side_effect=fake_send), \
                patch("model.features.storage_bag.random.choice", return_value="稍等"), \
                patch("model.features.storage_bag._delete_storage_bag_gift_locator", return_value=True), \
                patch("model.features.storage_bag.send_audit_log"):
            ok, message, transfer = await start_storage_bag_transfer_task(
                self.source_id,
                self.target_id,
                [{"item_name": "木髓", "quantity": 2, "method": "gift"}],
                "",
            )
            self.assertTrue(ok, message)
            handled = await handle_storage_bag_transfer_reply(
                "【赠送成功】\n"
                "道友 @WalterWA2000 向 @takaranoao_bot 赠送了 【空间之核】x1。\n"
                "并额外支付了 10 灵石作为因果税 (基础税率 10%)。",
                1000.0,
                SimpleNamespace(id=202, raw_text=sent[1][0]),
                reply_context={"reply_to_msg_id": 202},
            )

        self.assertTrue(handled)
        self.assertFalse(storage_bag._storage_bag_transfer_state["running"])
        self.assertEqual("failed", storage_bag._storage_bag_transfer_state["step"])
        self.assertIn("赠送结果不匹配", storage_bag._storage_bag_transfer_state["last_error"])
        records = state_module.get_storage_bag_records()
        self.assertEqual(4, records[str(self.source_id)]["items"]["木髓"])
        self.assertEqual(1000, records[str(self.source_id)]["items"]["灵石"])
        self.assertNotIn("空间之核", records[str(self.source_id)]["items"])
        self.assertNotIn("木髓", records[str(self.target_id)]["items"])
        self.assertNotIn("空间之核", records[str(self.target_id)]["items"])

    async def test_multi_gift_transfer_waits_for_scheduler_between_gifts(self):
        sent = []

        async def fake_send(command, **kwargs):
            sent.append((command, kwargs))
            return SimpleNamespace(id=200 + len(sent))

        with patch("model.features.storage_bag.send_game_command", side_effect=fake_send), \
                patch("model.features.storage_bag.random.choice", return_value="稍等"), \
                patch("model.features.storage_bag._delete_storage_bag_gift_locator", return_value=True), \
                patch("model.features.storage_bag.send_audit_log"), \
                patch("model.features.storage_bag.time.time", return_value=1000.0):
            ok, message, transfer = await start_storage_bag_transfer_task(
                self.source_id,
                self.target_id,
                [
                    {"item_name": "木髓", "quantity": 1, "method": "gift"},
                    {"item_name": "妖丹", "quantity": 2, "method": "gift"},
                ],
                "",
            )

        self.assertTrue(ok, message)
        self.assertEqual(["稍等", ".赠送 木髓 1"], [item[0] for item in sent])

        with patch("model.features.storage_bag.send_game_command", side_effect=fake_send), \
                patch("model.features.storage_bag.send_audit_log"), \
                patch("model.features.storage_bag.time.time", return_value=1005.0):
            handled = await handle_storage_bag_transfer_reply(
                "【赠送成功】\n"
                "道友 @WalterWA2000 向 @takaranoao_bot 赠送了 【木髓】x1。\n"
                "并额外支付了 10 灵石作为因果税 (基础税率 10%)。",
                1005.0,
                SimpleNamespace(id=202, raw_text=sent[1][0]),
                reply_context={"reply_to_msg_id": 202},
            )

        self.assertTrue(handled)
        self.assertEqual(["稍等", ".赠送 木髓 1"], [item[0] for item in sent])
        self.assertEqual("gift_waiting_interval", storage_bag._storage_bag_transfer_state["step"])
        self.assertEqual(1025.0, storage_bag._storage_bag_transfer_state["gift_next_due_at"])

        with patch("model.features.storage_bag.send_game_command", side_effect=fake_send), \
                patch("model.features.storage_bag.send_audit_log"):
            await run_storage_bag_transfer_scheduler(1024.0)

        self.assertEqual(["稍等", ".赠送 木髓 1"], [item[0] for item in sent])

        with patch("model.features.storage_bag.send_game_command", side_effect=fake_send), \
                patch("model.features.storage_bag.send_audit_log"):
            await run_storage_bag_transfer_scheduler(1025.0)

        self.assertEqual(["稍等", ".赠送 木髓 1", ".赠送 妖丹 2"], [item[0] for item in sent])
        self.assertEqual("waiting_gift_reply", storage_bag._storage_bag_transfer_state["step"])

    async def test_locator_delete_respects_auto_delete_switch(self):
        storage_bag._storage_bag_transfer_state["gift_locator_msg_id"] = 123
        storage_bag._storage_bag_transfer_state["target_identity_id"] = self.target_id

        client = SimpleNamespace()
        with patch("model.features.storage_bag.is_auto_delete_sent_messages_enabled", return_value=False), \
                patch("model.features.storage_bag._get_identity_client", return_value=client) as client_mock:
            ok = await storage_bag._delete_storage_bag_gift_locator()

        self.assertTrue(ok)
        client_mock.assert_not_called()
        self.assertFalse(storage_bag._storage_bag_transfer_state["gift_locator_deleted"])


if __name__ == "__main__":
    unittest.main()
