import asyncio
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from model import persistence
from model import state as state_module


def _mutating_statements(statements):
    prefixes = ("INSERT", "UPDATE", "DELETE", "REPLACE")
    return [statement for statement in statements if statement.lstrip().upper().startswith(prefixes)]


class PersistenceDeltaLabTests(unittest.TestCase):
    def setUp(self):
        from model import runtime

        health_patcher = patch.object(runtime, "_bot_waiting_since", 0.0)
        health_patcher.start()
        self.addCleanup(health_patcher.stop)
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        self._db_conn_snapshot = persistence._db_conn
        self._db_initialized_snapshot = persistence._db_initialized
        self._schema_snapshot = persistence._schema_columns_ensured_key
        self._schema_version_snapshot = persistence._schema_columns_ensured_version
        self._snapshot_db_key = persistence._persistence_snapshot_db_key
        self._meta_snapshot = copy.deepcopy(persistence._persisted_meta_snapshot)
        self._identity_snapshots = copy.deepcopy(persistence._persisted_identity_snapshots)
        persistence._db_conn = None
        persistence._db_initialized = False
        persistence._schema_columns_ensured_key = None
        persistence._schema_columns_ensured_version = None
        persistence._clear_persistence_snapshots()
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))

    def tearDown(self):
        if persistence._db_conn is not None:
            persistence._db_conn.close()
        persistence._db_conn = self._db_conn_snapshot
        persistence._db_initialized = self._db_initialized_snapshot
        persistence._schema_columns_ensured_key = self._schema_snapshot
        persistence._schema_columns_ensured_version = self._schema_version_snapshot
        persistence._persistence_snapshot_db_key = self._snapshot_db_key
        persistence._persisted_meta_snapshot.clear()
        persistence._persisted_meta_snapshot.update(self._meta_snapshot)
        persistence._persisted_identity_snapshots.clear()
        persistence._persisted_identity_snapshots.update(self._identity_snapshots)
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    def _save_without_guard_backup(self):
        with patch.object(persistence, "_write_live_guard_backup"):
            return persistence.save_state()

    def test_heart_demon_chat_and_choice_anchors_survive_reload(self):
        from model.features import second_soul

        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            persistence, "DB_FILE", str(Path(tmpdir) / "state.db")
        ):
            for identity_id, chat_id in ((990111, -1001), (990112, -1002)):
                state_module.ensure_identity_registered(identity_id)
                with state_module.use_identity(identity_id):
                    state_module.state["second_soul_enabled"] = True
                    state_module.state["second_soul_phase"] = "heart_demon_pending"
                    state_module.state["second_soul_heart_demon_msg_id"] = 123
                    state_module.state["second_soul_heart_demon_chat_id"] = chat_id
                    state_module.state["second_soul_heart_demon_choice_msg_id"] = 124
            self.assertTrue(self._save_without_guard_backup())
            for identity_id in (990111, 990112):
                persistence._load_identity_from_db(identity_id)
            self.assertEqual((990112, [990112]), second_soul._match_heart_demon_identity(
                SimpleNamespace(id=123, chat_id=-1002, reply_to_msg_id=7310786),
            ))
            self.assertEqual((990111, [990111]), second_soul._match_heart_demon_identity(
                SimpleNamespace(id=125, chat_id=-1001, reply_to_msg_id=124),
            ))

    def test_checkin_teach_routes_and_completed_commands_survive_reload(self):
        from model.features import checkin

        expected = {
            "last_checkin_msg_id": 121,
            "last_checkin_chat_id": -1001,
            "last_sect_teach_msg_id": 123,
            "last_sect_teach_chat_id": -1002,
            "sect_teach_reply_to_msg_id": 123,
            "sect_teach_reply_chat_id": -1002,
            "checkin_teach_count": 1,
            "sect_teach_completed_message_keys": [[-1002, 123]],
            "checkin_cleanup_msg_ids": [[-1001, 121], [-1002, 123]],
        }
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            persistence, "DB_FILE", str(Path(tmpdir) / "state.db")
        ):
            state_module.ensure_identity_registered(990113)
            state_module.get_identity_state(990113).update(copy.deepcopy(expected))
            self.assertTrue(self._save_without_guard_backup())
            loaded = persistence._load_identity_from_db(990113)
            for key, value in expected.items():
                self.assertEqual(value, loaded[key], key)
            with state_module.use_identity(990113):
                self.assertFalse(checkin.remember_sect_teach_completion(123, chat_id=-1002))
                self.assertTrue(checkin.remember_sect_teach_completion(123, chat_id=-1003))
                self.assertEqual(2, state_module.state["checkin_teach_count"])

    def test_teach_terminal_reply_is_not_replayed_after_save_and_reload(self):
        from model.features import checkin, passive_inbox

        identity_id, now = 990114, 1_788_748_200.0
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            persistence, "DB_FILE", str(Path(tmpdir) / "state.db")
        ):
            state_module.ensure_identity_registered(identity_id)
            state_module.get_identity_state(identity_id).update({
                "sect_teach_enabled": True,
                "checkin_teach_day": checkin.get_checkin_day_key(now),
                "checkin_teach_count": 2,
                "sect_teach_completed_message_keys": [[-1002, 122], [-1002, 123]],
                "next_sect_teach_time": now,
                "sect_teach_reply_to_msg_id": 123,
                "sect_teach_reply_chat_id": -1002,
            })
            text = "传功玉简已记录！今日已传功 3/3 次。"

            async def cleanup():
                state_module.state["checkin_cleanup_msg_ids"] = []

            with (
                state_module.use_identity(identity_id),
                patch.object(checkin, "save_state", side_effect=self._save_without_guard_backup),
                patch.object(checkin, "cleanup_checkin_chain_messages", new=AsyncMock(side_effect=cleanup)) as cleanup_mock,
                patch.object(checkin, "_notify_sect_teach_completed", new=AsyncMock()) as notify,
                patch.object(checkin, "send_game_command", new=AsyncMock()) as sender,
            ):
                self.assertTrue(asyncio.run(passive_inbox._apply_checkin_passive(
                    text, now, "sect_teach", {"root_msg_id": 124, "chat_id": -1002},
                )))
                self.assertTrue(self._save_without_guard_backup())
                loaded = persistence._load_identity_from_db(identity_id)
                self.assertEqual(loaded["checkin_teach_count"], 3)
                self.assertEqual(loaded["next_sect_teach_time"], 0)
                self.assertEqual(loaded["checkin_cleanup_msg_ids"], [])
                self.assertIn([-1002, 124], loaded["sect_teach_completed_message_keys"])
                self.assertTrue(asyncio.run(checkin.handle_sect_teach_reply(
                    text, now + 1, SimpleNamespace(id=124, chat_id=-1002, raw_text=checkin.CMD_SECT_TEACH),
                    matched_family="sect_teach",
                )))
                self.assertFalse(asyncio.run(passive_inbox._apply_checkin_passive(
                    text, now + 2, "sect_teach", {"root_msg_id": 124, "chat_id": -1002},
                )))
                cleanup_mock.assert_awaited_once()
                notify.assert_awaited_once_with(send_as_id=identity_id)
                sender.assert_not_awaited()

    def test_same_identity_cross_chat_pending_survives_reload_and_exact_reply_cleanup(self):
        from model import runtime

        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            persistence, "DB_FILE", str(Path(tmpdir) / "state.db")
        ), patch.object(runtime, "_reply_chain_tracker", {}), patch.object(
            runtime, "_notify_game_command_sent_observers"
        ):
            identity_id = 990106
            state_module.ensure_identity_registered(identity_id)
            for chat_id in (-1001, -1002):
                runtime._finalize_game_command_sent(
                    runtime.CMD_CHECKIN, msg_id=7001, sent_at=runtime.time.time(),
                    send_as_id=identity_id, game_group_id=chat_id, append_sent_log=False,
                )
            self.assertTrue(self._save_without_guard_backup())
            runtime._reply_chain_tracker.clear()
            restored = persistence._load_identity_from_db(identity_id)
            self.assertEqual({(-1001, 7001), (-1002, 7001)}, set(restored["pending_tasks"]))
            self.assertEqual({(-1001, 7001), (-1002, 7001)}, set(restored["my_msg_ids"]))
            context = runtime.get_reply_context(reply_to_msg_id=7001, chat_id=-1001)
            self.assertEqual(identity_id, context["send_as_id"])
            runtime.clear_pending_by_reply(reply_context=context)
            self.assertEqual({(-1002, 7001)}, set(restored["pending_tasks"]))
            self.assertTrue(self._save_without_guard_backup())
            restored = persistence._load_identity_from_db(identity_id)
            self.assertEqual({(-1002, 7001)}, set(restored["pending_tasks"]))
            context = runtime.get_reply_context(reply_to_msg_id=7001, chat_id=-1002)
            self.assertEqual((identity_id, "checkin"), (context["send_as_id"], context["family"]))

    def test_duplicate_checkin_after_reload_cannot_requeue_teaching(self):
        from model.features import checkin

        now = 1788748200.0
        day_key = checkin.get_checkin_day_key(now)
        expected = {
            "checkin_enabled": True, "sect_teach_enabled": True,
            "last_checkin_done_day": day_key, "checkin_teach_day": day_key,
            "next_checkin_time": now + 86400, "next_sect_teach_time": now + 20,
            "last_checkin_msg_id": 123, "last_checkin_chat_id": -1001,
            "last_sect_teach_msg_id": 124, "last_sect_teach_chat_id": -1001,
            "sect_teach_reply_to_msg_id": 124, "sect_teach_reply_chat_id": -1001,
            "checkin_teach_count": 1,
        }
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            persistence, "DB_FILE", str(Path(tmpdir) / "state.db")
        ):
            identity_id = 990116
            state_module.ensure_identity_registered(identity_id).update(expected)
            self.assertTrue(self._save_without_guard_backup())
            restored = persistence._load_identity_from_db(identity_id)
            with state_module.use_identity(identity_id):
                self.assertFalse(checkin.apply_checkin_completion(now + 1, 123, chat_id=-1001))
            for key, value in expected.items():
                self.assertEqual(value, restored[key], key)

    def test_nanlong_routes_and_prompt_receipt_survive_delta_save_and_reload(self):
        from model.features import nanlong

        expected = {
            "nanlong_reply_to_msg_id": 123, "nanlong_reply_chat_id": -1001,
            "nanlong_prompt_at": 1700000000.0, "nanlong_last_prompt_key": "-1001:123",
            "nanlong_last_msg_id": 124, "nanlong_last_chat_id": -1001,
            "nanlong_last_sent_at": 1700000001.0,
        }
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            persistence, "DB_FILE", str(Path(tmpdir) / "state.db")
        ):
            identity_id = 990114
            identity = state_module.ensure_identity_registered(identity_id)
            identity.update(expected)
            self.assertTrue(self._save_without_guard_backup())
            identity["nanlong_last_chat_id"] = -1002
            expected["nanlong_last_chat_id"] = -1002
            self.assertTrue(self._save_without_guard_backup())
            loaded = persistence._load_identity_from_db(identity_id)
            for key, value in expected.items():
                self.assertEqual(value, loaded[key], key)
            with state_module.use_identity(identity_id):
                nanlong.clear_nanlong_state()
            self.assertTrue(self._save_without_guard_backup())
            loaded = persistence._load_identity_from_db(identity_id)
            self.assertEqual(0, loaded["nanlong_reply_chat_id"])
            self.assertEqual(0, loaded["nanlong_last_chat_id"])
            self.assertEqual(0, loaded["nanlong_last_sent_at"])
            self.assertEqual("-1001:123", loaded["nanlong_last_prompt_key"])
            self.assertEqual(expected["nanlong_prompt_at"], loaded["nanlong_prompt_at"])

    def test_nanlong_unresolved_send_checkpoint_survives_reload_without_retry(self):
        from model.features import nanlong

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch.object(persistence, "DB_FILE", str(Path(tmpdir) / "state.db")),
            patch.object(nanlong, "save_state", side_effect=self._save_without_guard_backup),
            patch.object(nanlong, "send_game_command", new=AsyncMock()) as sender,
        ):
            now = 1788748200.0
            identity_id = 990117
            state_module.ensure_identity_registered(identity_id)["nanlong_enabled"] = True
            with state_module.use_identity(identity_id):
                nanlong._set_nanlong_pending(123, now + 180, now, chat_id=-1001)
                self.assertTrue(nanlong._prepare_nanlong_send(nanlong.CMD_NANLONG_EXCHANGE_FABAO, now, protected=True))
            loaded = persistence._load_identity_from_db(identity_id)
            with state_module.use_identity(identity_id):
                self.assertTrue(nanlong._nanlong_send_is_unresolved())
                asyncio.run(nanlong.run_nanlong_scheduler(now + 60))
            sender.assert_not_awaited()
            self.assertEqual(0, loaded["nanlong_last_msg_id"])
            self.assertEqual(0, loaded["nanlong_reply_due_at"])
            self.assertEqual("exchange_pending", loaded["nanlong_protect_phase"])
            self.assertEqual(123, loaded["nanlong_reply_to_msg_id"])

    def test_nanlong_terminal_state_and_exact_detached_cleanup_survive_reload(self):
        from model.features import nanlong

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch.object(persistence, "DB_FILE", str(Path(tmpdir) / "state.db")),
            patch.object(nanlong, "save_state", side_effect=self._save_without_guard_backup),
            patch.object(nanlong, "send_audit_log", new=AsyncMock()),
            patch.object(nanlong, "send_game_command", new=AsyncMock()) as sender,
        ):
            now = 1788748200.0
            identity_id = 990118
            identity = state_module.ensure_identity_registered(identity_id)
            state_module.update_send_as_profile(identity_id, username="NanlongReload", enabled=True)
            identity.update(
                nanlong_enabled=True, nanlong_reply_to_msg_id=123, nanlong_reply_chat_id=-1001,
                nanlong_last_prompt_key="-1001:123", nanlong_last_msg_id=124,
                nanlong_last_chat_id=-1001, nanlong_last_sent_at=now - 1,
                nanlong_last_command=nanlong.CMD_NANLONG_EXCHANGE_FABAO,
            )
            identity["pending_tasks"] = {
                (chat_id, 124): {
                    "cmd": nanlong.CMD_NANLONG_EXCHANGE_FABAO, "chat_id": chat_id,
                    "sent_at": now - 1, "max_retry": 0, "send_caller_detached": True,
                }
                for chat_id in (-1001, -1002)
            }
            self.assertTrue(self._save_without_guard_backup())
            with state_module.use_identity(identity_id):
                self.assertTrue(asyncio.run(nanlong.handle_nanlong_result_broadcast(
                    "【天机异闻·南陇侯的交易】@NanlongReload 已完成交易。", now,
                    SimpleNamespace(id=125, chat_id=-1002),
                )))
            loaded = persistence._load_identity_from_db(identity_id)
            self.assertEqual(0, loaded["nanlong_last_msg_id"])
            self.assertEqual("-1001:123", loaded["nanlong_last_prompt_key"])
            self.assertNotIn((-1001, 124), loaded["pending_tasks"])
            self.assertTrue(loaded["pending_tasks"][(-1002, 124)]["send_caller_detached"])
            sender.assert_not_awaited()

    def test_nanlong_saved_protected_rejection_recalls_after_sqlite_reload(self):
        from model.features import nanlong

        identity_id, now = 99135001, 1788748200.0
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch.object(persistence, "DB_FILE", str(Path(tmpdir) / "state.db")),
            patch.object(nanlong, "save_state", side_effect=self._save_without_guard_backup),
            patch.object(nanlong.time, "time", return_value=now + 200),
            patch.object(nanlong, "iter_message_log_entries_between", return_value=[]),
            patch.object(nanlong, "send_audit_log", new=AsyncMock()),
            patch.object(nanlong, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(
                id=126, chat_id=-1001, sent_at=now + 200,
            ))) as sender,
        ):
            state_module.ensure_identity_registered(identity_id)["nanlong_enabled"] = True
            state_module.update_send_as_profile(identity_id, enabled=True, nanlong_choice="reject")
            with state_module.use_identity(identity_id):
                nanlong._set_nanlong_pending(123, now + 180, now, chat_id=-1001)
                nanlong._set_nanlong_waiting_for_exchange(
                    SimpleNamespace(id=125, chat_id=-1001, sent_at=now),
                    nanlong.CMD_NANLONG_REJECT, protected=True,
                )
            self.assertTrue(self._save_without_guard_backup())
            loaded = persistence._load_identity_from_db(identity_id)
            self.assertEqual("exchange_pending", loaded["nanlong_protect_phase"])
            with state_module.use_identity(identity_id):
                asyncio.run(nanlong.run_nanlong_scheduler(now + 200))
            loaded = persistence._load_identity_from_db(identity_id)
            self.assertEqual("recall_pending", loaded["nanlong_protect_phase"])
            self.assertEqual(126, loaded["nanlong_last_msg_id"])
            with state_module.use_identity(identity_id):
                self.assertTrue(asyncio.run(nanlong.handle_nanlong_reply(
                    "你已将道侣【墨彩环】从藏娇阁中召回。", now + 201,
                    SimpleNamespace(id=126, chat_id=-1001), matched_family="nanlong",
                )))
            loaded = persistence._load_identity_from_db(identity_id)
            self.assertEqual("", loaded["nanlong_protect_phase"])
            with state_module.use_identity(identity_id):
                asyncio.run(nanlong.run_nanlong_scheduler(now + 300))
            self.assertEqual([nanlong.CMD_CONCUBINE_RECALL], [call.args[0] for call in sender.await_args_list])

    def test_nanlong_disabled_results_resume_cleanup_after_sqlite_reload(self):
        from model import control
        from model.features import nanlong

        now = 1_700_000_000.0
        steps = (
            ("place", "你已将道侣【墨彩环】安置在洞府的藏娇阁中。", "exchange_pending"),
            ("exchange", "【天机异闻·南陇侯的交易】@NanlongReload 已完成交易。", "recall_pending"),
            ("recall", "你已将道侣【墨彩环】从藏娇阁中召回。", ""),
        )
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch.object(persistence, "DB_FILE", str(Path(tmpdir) / "state.db")),
            patch.object(nanlong, "save_state", side_effect=self._save_without_guard_backup),
            patch.object(control, "save_state", side_effect=self._save_without_guard_backup),
            patch.object(nanlong.time, "time", return_value=now + 400),
            patch.object(nanlong, "iter_message_log_entries_between", return_value=[]),
            patch.object(nanlong, "send_audit_log", new=AsyncMock()),
        ):
            for index, (step, text, next_phase) in enumerate(steps):
                with self.subTest(step=step):
                    identity_id = 990180 + index
                    identity = state_module.ensure_identity_registered(identity_id)
                    state_module.update_send_as_profile(identity_id, enabled=True, username="NanlongReload", nanlong_choice="exchange_fabao")
                    identity.update(nanlong_enabled=True, concubine_name="墨彩环")
                    with state_module.use_identity(identity_id):
                        nanlong._set_nanlong_pending(123, now + 180, now, chat_id=-1001)
                        receipt = SimpleNamespace(id=124, chat_id=-1001, sent_at=now, send_started_at=now)
                        if step == "place":
                            nanlong._set_nanlong_waiting_for_place(receipt)
                        elif step == "exchange":
                            nanlong._set_nanlong_waiting_for_exchange(receipt, nanlong.CMD_NANLONG_EXCHANGE_FABAO, protected=True)
                        else:
                            nanlong._set_nanlong_waiting_for_recall(receipt)
                        reply = SimpleNamespace(id=124, chat_id=-1001, raw_text=identity["nanlong_last_command"])
                        identity["nanlong_enabled"] = False
                        with patch.object(nanlong, "send_game_command", new=AsyncMock()) as sender:
                            self.assertTrue(asyncio.run(nanlong.handle_nanlong_reply(text, now + 1, reply, matched_family="nanlong")))
                        sender.assert_not_awaited()
                    restored = persistence._load_identity_from_db(identity_id)
                    self.assertFalse(restored["nanlong_enabled"])
                    self.assertEqual(next_phase, restored["nanlong_protect_phase"])
                    self.assertEqual(0, restored["nanlong_last_msg_id"])
                    ok, message = asyncio.run(control.set_module_enabled("南陇侯", True, send_as_id=identity_id))
                    self.assertTrue(ok, message)
                    restored = persistence._load_identity_from_db(identity_id)
                    with state_module.use_identity(identity_id), patch.object(
                        nanlong, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=125, chat_id=-1001, sent_at=now + 400)),
                    ) as sender:
                        asyncio.run(nanlong.run_nanlong_scheduler(now + 400))
                        asyncio.run(nanlong.run_nanlong_scheduler(now + 461))
                        if next_phase:
                            self.assertEqual([nanlong.CMD_CONCUBINE_RECALL], [call.args[0] for call in sender.await_args_list])
                            self.assertTrue(asyncio.run(nanlong.handle_nanlong_reply(
                                "你已将道侣【墨彩环】从藏娇阁中召回。", now + 462,
                                SimpleNamespace(id=125, chat_id=-1001, raw_text=nanlong.CMD_CONCUBINE_RECALL), matched_family="nanlong",
                            )))
                        else:
                            sender.assert_not_awaited()
                    completed = persistence._load_identity_from_db(identity_id)
                    self.assertEqual("", completed["nanlong_protect_phase"])
                    self.assertEqual(0, completed["nanlong_last_msg_id"])

    def test_nanlong_post_dispatch_disabled_receipt_is_durable(self):
        from model.features import nanlong

        now, identity_id = 1_700_000_000.0, 990183
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch.object(persistence, "DB_FILE", str(Path(tmpdir) / "state.db")),
            patch.object(nanlong, "save_state", side_effect=self._save_without_guard_backup),
            patch.object(nanlong.time, "time", return_value=now),
            patch.object(nanlong, "iter_message_log_entries_between", return_value=[]),
        ):
            identity = state_module.ensure_identity_registered(identity_id)
            state_module.update_send_as_profile(identity_id, enabled=True, nanlong_choice="exchange_fabao")
            identity.update(nanlong_enabled=True, concubine_name="南宫婉")

            async def send(*args, **kwargs):
                identity["nanlong_enabled"] = False
                self.assertTrue(self._save_without_guard_backup())
                return SimpleNamespace(id=124, chat_id=-1001, sent_at=now + 1, send_started_at=now)

            with state_module.use_identity(identity_id), patch.object(nanlong, "send_game_command", new=AsyncMock(side_effect=send)) as sender:
                nanlong._set_nanlong_pending(123, now + 180, now, chat_id=-1001)
                identity["nanlong_reply_due_at"] = now
                asyncio.run(nanlong.run_nanlong_scheduler(now))
            sender.assert_awaited_once()
            restored = persistence._load_identity_from_db(identity_id)
            self.assertFalse(restored["nanlong_enabled"])
            self.assertEqual(124, restored["nanlong_last_msg_id"])
            self.assertEqual(-1001, restored["nanlong_last_chat_id"])
            self.assertEqual(now, restored["nanlong_last_sent_at"])

    def test_nanlong_detached_receipt_is_adopted_once_after_sqlite_reload(self):
        from model import runtime
        from model.features import nanlong

        identity_id, now = 990119, 1788748200.0
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch.object(persistence, "DB_FILE", str(Path(tmpdir) / "state.db")),
            patch.object(nanlong, "save_state", side_effect=self._save_without_guard_backup),
            patch.object(nanlong.time, "time", return_value=now),
            patch.object(nanlong, "send_audit_log", new=AsyncMock()),
            patch.object(nanlong, "send_game_command", new=AsyncMock()) as sender,
            patch.object(runtime, "_reply_chain_tracker", {}),
            patch.object(runtime, "_append_sent_message_log"),
            patch.object(runtime, "_notify_game_command_sent_observers"),
        ):
            identity = state_module.ensure_identity_registered(identity_id)
            state_module.update_send_as_profile(identity_id, username="NanlongReload", enabled=True)
            state_module.set_identity_account(identity_id, 7101)
            identity.update(nanlong_enabled=True, concubine_name="南宫婉")
            with state_module.use_identity(identity_id):
                nanlong._set_nanlong_pending(123, now + 180, now, chat_id=-1001)
                self.assertTrue(nanlong._prepare_nanlong_send(nanlong.CMD_NANLONG_EXCHANGE_FABAO, now))
                intent = nanlong._nanlong_send_intent()
            runtime._finalize_game_send_receipt({
                "message": None, "detached": True, "send_as_id": identity_id,
                "command": nanlong.CMD_NANLONG_EXCHANGE_FABAO,
                "finalize_kwargs": {
                    "send_as_id": identity_id, "reply_to": 123, "track": False,
                    "game_group_id": -1001, "topic_id": 0, "send_intent": intent,
                    "send_started_at": now + 2,
                },
            }, msg_id=124, sent_at=now + 10)
            self.assertTrue(self._save_without_guard_backup())
            restored = persistence._load_identity_from_db(identity_id)
            self.assertEqual(now, restored["nanlong_last_sent_at"])
            self.assertEqual(0, restored["nanlong_last_msg_id"])
            pending = restored["pending_tasks"][(-1001, 124)]
            self.assertEqual(intent["op_id"], pending["op_id"])
            self.assertTrue(pending["send_caller_detached"])
            self.assertEqual(0, pending["max_retry"])
            self.assertEqual(now + 2, pending["send_started_at"])
            runtime._reply_chain_tracker.clear()
            text = "【天机异闻·南陇侯的交易】@NanlongReload 已完成交易。"
            reply = SimpleNamespace(id=124, chat_id=-1001)
            with state_module.use_identity(identity_id):
                self.assertTrue(asyncio.run(nanlong.handle_nanlong_reply(text, now + 11, reply, matched_family="nanlong")))
            restored = persistence._load_identity_from_db(identity_id)
            self.assertEqual(0, restored["nanlong_last_msg_id"])
            self.assertEqual("-1001:123", restored["nanlong_last_prompt_key"])
            self.assertNotIn((-1001, 124), restored["pending_tasks"])
            with state_module.use_identity(identity_id):
                self.assertFalse(asyncio.run(nanlong.handle_nanlong_reply(text, now + 12, reply, matched_family="nanlong")))
                asyncio.run(nanlong.run_nanlong_scheduler(now + 120))
            sender.assert_not_awaited()

    def test_nanlong_orphan_receipt_recovery_survives_native_toggle_and_sqlite_reload(self):
        from model import control, runtime
        from model.features import nanlong

        now = 1_700_000_000.0
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch.object(persistence, "DB_FILE", str(Path(tmpdir) / "state.db")),
            patch.object(nanlong, "save_state", side_effect=self._save_without_guard_backup),
            patch.object(control, "save_state", side_effect=self._save_without_guard_backup),
            patch.object(nanlong, "iter_message_log_entries_between", return_value=[]),
            patch.object(nanlong, "send_audit_log", new=AsyncMock()),
            patch.object(runtime, "_reply_chain_tracker", {}),
            patch.object(runtime, "_notify_game_command_sent_observers"),
        ):
            for index, command in enumerate((nanlong.CMD_CONCUBINE_PLACE, nanlong.CMD_CONCUBINE_RECALL)):
                with self.subTest(command=command):
                    identity_id = 990184 + index
                    identity = state_module.ensure_identity_registered(identity_id)
                    state_module.update_send_as_profile(identity_id, enabled=True, nanlong_choice="exchange_fabao")
                    state_module.set_identity_account(identity_id, 7101)
                    identity.update(nanlong_enabled=True, concubine_name="墨彩环")
                    with state_module.use_identity(identity_id), patch.object(nanlong.time, "time", return_value=now):
                        nanlong._set_nanlong_pending(123, now + 180, now - 1, chat_id=-1001)
                        identity["nanlong_last_chat_id"] = -1001
                        self.assertTrue(nanlong._prepare_nanlong_send(command, now))
                        receipt = runtime._finalize_game_command_sent(
                            command, msg_id=124, sent_at=now, send_started_at=now, send_as_id=identity_id,
                            track=True, max_retry=0, append_sent_log=False, game_group_id=-1001, topic_id=0,
                            send_intent=nanlong._nanlong_send_intent(),
                        )
                        if command == nanlong.CMD_CONCUBINE_PLACE:
                            nanlong._set_nanlong_waiting_for_place(receipt)
                        else:
                            nanlong._set_nanlong_waiting_for_recall(receipt)
                    identity.update(nanlong_last_msg_id=0, nanlong_last_command="", nanlong_protect_phase="")
                    identity["pending_tasks"][(-1002, 124)] = {"cmd": ".状态", "chat_id": -1002, "sent_at": now}
                    self.assertTrue(asyncio.run(control.set_module_enabled("南陇侯", False, send_as_id=identity_id))[0])
                    loaded = persistence._load_identity_from_db(identity_id)
                    self.assertFalse(loaded["nanlong_enabled"])
                    self.assertEqual(0, loaded["nanlong_last_msg_id"])
                    self.assertTrue(asyncio.run(control.set_module_enabled("南陇侯", True, send_as_id=identity_id))[0])
                    persistence._load_identity_from_db(identity_id)
                    with state_module.use_identity(identity_id), patch.object(nanlong, "send_game_command", new=AsyncMock()) as sender:
                        asyncio.run(nanlong.run_nanlong_scheduler(now + 400))
                    sender.assert_not_awaited()
                    restored = persistence._load_identity_from_db(identity_id)
                    self.assertEqual(124, restored["nanlong_last_msg_id"])
                    self.assertEqual(command, restored["nanlong_last_command"])
                    self.assertEqual(now, restored["nanlong_last_sent_at"])
                    result = ("你已将道侣【墨彩环】安置在洞府的藏娇阁中。" if command == nanlong.CMD_CONCUBINE_PLACE
                              else "你已将道侣【墨彩环】从藏娇阁中召回。")
                    with (
                        state_module.use_identity(identity_id),
                        patch.object(nanlong.time, "time", return_value=now + 401),
                        patch.object(nanlong, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(
                            id=125, chat_id=-1001, sent_at=now + 401,
                        ))) as sender,
                    ):
                        self.assertTrue(asyncio.run(nanlong.handle_nanlong_reply(
                            result, now + 401, SimpleNamespace(id=124, chat_id=-1001, raw_text=command), matched_family="nanlong",
                        )))
                        if command == nanlong.CMD_CONCUBINE_PLACE:
                            self.assertEqual([nanlong.CMD_CONCUBINE_RECALL], [call.args[0] for call in sender.await_args_list])
                            persistence._load_identity_from_db(identity_id)
                            self.assertTrue(asyncio.run(nanlong.handle_nanlong_reply(
                                "你已将道侣【墨彩环】从藏娇阁中召回。", now + 402,
                                SimpleNamespace(id=125, chat_id=-1001, raw_text=nanlong.CMD_CONCUBINE_RECALL), matched_family="nanlong",
                            )))
                        else:
                            sender.assert_not_awaited()
                    completed = persistence._load_identity_from_db(identity_id)
                    self.assertEqual(0, completed["nanlong_place_msg_id"])
                    self.assertEqual(0, completed["nanlong_recall_msg_id"])
                    self.assertNotIn((-1001, 124), completed["pending_tasks"])
                    self.assertIn((-1002, 124), completed["pending_tasks"])

    def test_pending_route_and_recovery_only_edits_are_not_lost_by_delta_save(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            persistence, "DB_FILE", str(Path(tmpdir) / "state.db")
        ):
            identity_id = 990107
            state_module.ensure_identity_registered(identity_id)
            pending = {"cmd": ".test", "sent_at": 100, "timeout": 10, "chat_id": -1001}
            state_module.get_identity_state(identity_id)["pending_tasks"][(-1001, 42)] = pending
            self.assertTrue(self._save_without_guard_backup())
            for field, value in (("topic_id", 77), ("send_caller_detached", True), ("send_started_at", 99), ("reply_recovery_retry_at", 10700), ("reply_recovery_applied", {"message:43:hash": False})):
                with self.subTest(field=field):
                    pending[field] = value
                    with patch.object(persistence, "upsert_identity_to_db", wraps=persistence.upsert_identity_to_db) as upsert:
                        self.assertTrue(self._save_without_guard_backup())
                    upsert.assert_called_once_with(identity_id)
            restored = persistence._load_identity_from_db(identity_id)["pending_tasks"][(-1001, 42)]
            self.assertEqual(77, restored["topic_id"])
            self.assertTrue(restored["send_caller_detached"])
            self.assertEqual(99, restored["send_started_at"])
            self.assertEqual(10700, restored["reply_recovery_retry_at"])
            self.assertEqual({"message:43:hash": False}, restored["reply_recovery_applied"])

    def test_detached_untracked_receipt_survives_reload_until_exact_reply(self):
        from model import runtime

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch.object(persistence, "DB_FILE", str(Path(tmpdir) / "state.db")),
            patch.object(runtime, "_reply_chain_tracker", {}),
            patch.object(runtime, "_notify_game_command_sent_observers"),
        ):
            identity_id = 990115
            state_module.ensure_identity_registered(identity_id)
            receipt = {
                "command": runtime.CMD_CHECKIN,
                "send_as_id": identity_id,
                "detached": True,
                "message": None,
                "finalize_kwargs": {
                    "send_as_id": identity_id, "track": False,
                    "game_group_id": -1001, "max_retry": 1,
                },
            }
            runtime._finalize_game_send_receipt(
                receipt, msg_id=42, sent_at=runtime.time.time(), append_sent_log=False,
            )
            self.assertTrue(self._save_without_guard_backup())
            runtime._reply_chain_tracker.clear()
            restored = persistence._load_identity_from_db(identity_id)
            pending = restored["pending_tasks"][(-1001, 42)]
            self.assertTrue(pending["send_caller_detached"])
            self.assertEqual(0, pending["max_retry"])
            self.assertEqual([], runtime.clear_pending_tasks_by_commands(
                {runtime.CMD_CHECKIN}, send_as_id=identity_id,
            ))
            context = runtime.get_reply_context(reply_to_msg_id=42, chat_id=-1001)
            runtime.clear_pending_by_reply(reply_context=context)
            self.assertEqual({}, restored["pending_tasks"])
            self.assertTrue(self._save_without_guard_backup())
            self.assertEqual({}, persistence._load_identity_from_db(identity_id)["pending_tasks"])

    def test_duplicate_legacy_and_scoped_reference_cannot_silently_replace_a_row(self):
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch.object(persistence, "DB_FILE", str(Path(tmpdir) / "state.db")),
            patch.object(persistence, "_last_save_failed_at", 0.0),
            patch.object(persistence, "_last_save_error", ""),
        ):
            identity_id = 990108
            state_module.ensure_identity_registered(identity_id)
            identity = state_module.get_identity_state(identity_id)
            item = {"cmd": ".original", "sent_at": 100, "timeout": 10, "chat_id": -1001}
            identity["pending_tasks"] = {(-1001, 42): item}
            self.assertTrue(self._save_without_guard_backup())
            identity["pending_tasks"][42] = {**item, "cmd": ".conflicting"}
            self.assertFalse(self._save_without_guard_backup())
            rows = persistence.get_db_conn().execute(
                "SELECT chat_id, msg_id, cmd FROM pending_tasks WHERE send_as_id = ?", (identity_id,),
            ).fetchall()
            self.assertEqual([(-1001, 42, ".original")], [tuple(row) for row in rows])

    def test_repeated_no_change_save_has_no_mutating_sql(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            persistence, "DB_FILE", str(Path(tmpdir) / "state.db")
        ):
            state_module.ensure_identity_registered(990101)
            state_module.ensure_identity_registered(990102)
            self.assertTrue(self._save_without_guard_backup())

            statements = []
            conn = persistence.get_db_conn()
            conn.set_trace_callback(statements.append)
            self.assertTrue(self._save_without_guard_backup())
            conn.set_trace_callback(None)

        self.assertEqual([], _mutating_statements(statements))

    def test_load_initializes_snapshot_for_no_change_save(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            persistence, "DB_FILE", str(Path(tmpdir) / "state.db")
        ):
            state_module.ensure_identity_registered(990105)
            self.assertTrue(self._save_without_guard_backup())
            persistence.get_db_conn().close()
            persistence._db_conn = None
            persistence._db_initialized = False
            persistence._schema_columns_ensured_key = None
            persistence._schema_columns_ensured_version = None
            persistence._clear_persistence_snapshots()
            state_module._meta_state.clear()
            state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
            self.assertTrue(persistence.load_state())

            statements = []
            conn = persistence.get_db_conn()
            conn.set_trace_callback(statements.append)
            self.assertTrue(self._save_without_guard_backup())
            conn.set_trace_callback(None)

        self.assertEqual([], _mutating_statements(statements))

    def test_identity_change_writes_only_changed_identity(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            persistence, "DB_FILE", str(Path(tmpdir) / "state.db")
        ):
            state_module.ensure_identity_registered(990111)
            state_module.ensure_identity_registered(990112)
            self.assertTrue(self._save_without_guard_backup())
            with state_module.use_identity(990111):
                state_module.state["tower_retry_count"] = 2

            original = persistence.upsert_identity_to_db
            calls = []

            def record(identity_id):
                calls.append(int(identity_id))
                return original(identity_id)

            with patch.object(persistence, "upsert_identity_to_db", side_effect=record):
                self.assertTrue(self._save_without_guard_backup())

            row = persistence.get_db_conn().execute(
                "SELECT tower_retry_count FROM identity_runtime_state WHERE send_as_id = ?",
                (990111,),
            ).fetchone()

        self.assertEqual([990111], calls)
        self.assertEqual(2, int(row["tower_retry_count"]))

    def test_current_runtime_column_change_is_included_in_delta_snapshot(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            persistence, "DB_FILE", str(Path(tmpdir) / "state.db")
        ):
            state_module.ensure_identity_registered(990113)
            self.assertTrue(self._save_without_guard_backup())
            with state_module.use_identity(990113):
                state_module.state["small_world_next_public_harvest_at"] = 1_800_000_321.0

            with patch.object(
                persistence,
                "upsert_identity_to_db",
                wraps=persistence.upsert_identity_to_db,
            ) as upsert_mock:
                self.assertTrue(self._save_without_guard_backup())

            row = persistence.get_db_conn().execute(
                "SELECT small_world_next_public_harvest_at FROM identity_runtime_state WHERE send_as_id = ?",
                (990113,),
            ).fetchone()

        upsert_mock.assert_called_once_with(990113)
        self.assertEqual(1_800_000_321.0, float(row["small_world_next_public_harvest_at"]))

    def test_meta_only_change_does_not_rewrite_identities(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            persistence, "DB_FILE", str(Path(tmpdir) / "state.db")
        ):
            state_module.ensure_identity_registered(990121)
            self.assertTrue(self._save_without_guard_backup())
            state_module.set_global_recovery_hold_until(1_800_000_000.0)

            with patch.object(persistence, "upsert_identity_to_db", wraps=persistence.upsert_identity_to_db) as upsert_mock:
                self.assertTrue(self._save_without_guard_backup())

            value = persistence.get_db_conn().execute(
                "SELECT value FROM meta WHERE key = ?",
                ("global_recovery_hold_until",),
            ).fetchone()["value"]

        upsert_mock.assert_not_called()
        self.assertEqual("1800000000.0", value)

    def test_failed_identity_write_keeps_old_snapshot_for_retry(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            persistence, "DB_FILE", str(Path(tmpdir) / "state.db")
        ):
            state_module.ensure_identity_registered(990131)
            self.assertTrue(self._save_without_guard_backup())
            with state_module.use_identity(990131):
                state_module.state["tower_retry_count"] = 3

            with patch.object(persistence, "upsert_identity_to_db", side_effect=RuntimeError("injected write failure")), \
                    patch.object(persistence, "_write_live_guard_backup"):
                self.assertFalse(persistence.save_state())

            original = persistence.upsert_identity_to_db
            calls = []

            def record(identity_id):
                calls.append(int(identity_id))
                return original(identity_id)

            with patch.object(persistence, "upsert_identity_to_db", side_effect=record):
                self.assertTrue(self._save_without_guard_backup())

        self.assertEqual([990131], calls)

    def test_reopened_connection_falls_back_to_full_identity_save(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            persistence, "DB_FILE", str(Path(tmpdir) / "state.db")
        ):
            state_module.ensure_identity_registered(990141)
            state_module.ensure_identity_registered(990142)
            self.assertTrue(self._save_without_guard_backup())
            persistence.get_db_conn().close()
            persistence._db_conn = None
            persistence._db_initialized = False

            original = persistence.upsert_identity_to_db
            calls = []

            def record(identity_id):
                calls.append(int(identity_id))
                return original(identity_id)

            with patch.object(persistence, "upsert_identity_to_db", side_effect=record):
                self.assertTrue(self._save_without_guard_backup())

        self.assertEqual([990141, 990142], calls)

    def test_identity_deletion_removes_rows_and_snapshot(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            persistence, "DB_FILE", str(Path(tmpdir) / "state.db")
        ):
            state_module.ensure_identity_registered(990151)
            state_module.ensure_identity_registered(990152)
            self.assertTrue(self._save_without_guard_backup())
            state_module.remove_identity(990151)
            self.assertTrue(self._save_without_guard_backup())
            conn = persistence.get_db_conn()
            table_counts = {
                table_name: conn.execute(
                    f"SELECT COUNT(*) FROM {table_name} WHERE send_as_id = ?",
                    (990151,),
                ).fetchone()[0]
                for table_name in (
                    "identities",
                    "identity_module_state",
                    "identity_timers",
                    "identity_runtime_state",
                )
            }

        self.assertEqual({name: 0 for name in table_counts}, table_counts)
        self.assertNotIn(990151, persistence._persisted_identity_snapshots)

    def test_empty_child_state_deletes_persisted_pending_and_message_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            persistence, "DB_FILE", str(Path(tmpdir) / "state.db")
        ):
            state_module.ensure_identity_registered(990161)
            with state_module.use_identity(990161):
                state_module.state["pending_tasks"] = {
                    501: {"cmd": ".测试", "sent_at": 100.0, "timeout": 120.0},
                }
                state_module.state["my_msg_ids"] = {501: 100.0}
            self.assertTrue(self._save_without_guard_backup())
            with state_module.use_identity(990161):
                state_module.state["pending_tasks"] = {}
                state_module.state["my_msg_ids"] = {}
            self.assertTrue(self._save_without_guard_backup())
            conn = persistence.get_db_conn()
            pending_count = conn.execute(
                "SELECT COUNT(*) FROM pending_tasks WHERE send_as_id = ?",
                (990161,),
            ).fetchone()[0]
            message_count = conn.execute(
                "SELECT COUNT(*) FROM message_index WHERE send_as_id = ?",
                (990161,),
            ).fetchone()[0]

        self.assertEqual(0, pending_count)
        self.assertEqual(0, message_count)

    def test_meta_and_identity_changes_commit_together(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            persistence, "DB_FILE", str(Path(tmpdir) / "state.db")
        ):
            state_module.ensure_identity_registered(990171)
            self.assertTrue(self._save_without_guard_backup())
            state_module.set_global_recovery_throttle_until(1_800_000_100.0)
            with state_module.use_identity(990171):
                state_module.state["tower_retry_count"] = 4
            self.assertTrue(self._save_without_guard_backup())
            conn = persistence.get_db_conn()
            meta_value = conn.execute(
                "SELECT value FROM meta WHERE key = ?",
                ("global_recovery_throttle_until",),
            ).fetchone()["value"]
            identity_value = conn.execute(
                "SELECT tower_retry_count FROM identity_runtime_state WHERE send_as_id = ?",
                (990171,),
            ).fetchone()["tower_retry_count"]

        self.assertEqual("1800000100.0", meta_value)
        self.assertEqual(4, int(identity_value))

    def test_snapshot_refresh_failure_rewrites_committed_change_on_next_save(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            persistence, "DB_FILE", str(Path(tmpdir) / "state.db")
        ):
            state_module.ensure_identity_registered(990181)
            self.assertTrue(self._save_without_guard_backup())
            with state_module.use_identity(990181):
                state_module.state["tower_retry_count"] = 5

            with patch.object(persistence, "_record_persistence_snapshots", side_effect=RuntimeError("snapshot refresh failed")), \
                    patch.object(persistence, "_write_live_guard_backup"):
                self.assertFalse(persistence.save_state())

            original = persistence.upsert_identity_to_db
            calls = []

            def record(identity_id):
                calls.append(int(identity_id))
                return original(identity_id)

            with patch.object(persistence, "upsert_identity_to_db", side_effect=record):
                self.assertTrue(self._save_without_guard_backup())

        self.assertEqual([990181], calls)

    def test_delta_save_reuses_same_snapshot_for_shadow_telemetry(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            persistence, "DB_FILE", str(Path(tmpdir) / "state.db")
        ):
            state_module.ensure_identity_registered(990191)
            self.assertTrue(self._save_without_guard_backup())
            sample = {"changed_identity_ids": (), "changed_meta_keys": ()}
            with (
                patch.object(persistence.persistence_shadow, "is_enabled", return_value=True),
                patch.object(persistence.persistence_shadow, "safe_capture", return_value=sample) as capture_mock,
                patch.object(persistence.persistence_shadow, "safe_commit") as commit_mock,
                patch.object(persistence, "_write_live_guard_backup"),
            ):
                self.assertTrue(persistence.save_state())

        capture_kwargs = capture_mock.call_args.kwargs
        self.assertEqual(persistence._persisted_meta_snapshot, capture_kwargs["meta_snapshot"])
        self.assertEqual(persistence._persisted_identity_snapshots, capture_kwargs["identity_snapshots"])
        commit_mock.assert_called_once_with(sample)

    def test_schema_version_change_repairs_missing_current_runtime_column(self):
        column = "small_world_next_public_harvest_at"
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            persistence, "DB_FILE", str(Path(tmpdir) / "state.db")
        ):
            state_module.ensure_identity_registered(990201)
            self.assertTrue(self._save_without_guard_backup())
            conn = persistence.get_db_conn()
            conn.execute(f"ALTER TABLE identity_runtime_state DROP COLUMN {column}")
            conn.commit()

            persistence._ensure_schema_columns_ready(conn, verify=True)
            columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(identity_runtime_state)").fetchall()
            }

        self.assertIn(column, columns)


if __name__ == "__main__":
    unittest.main()
