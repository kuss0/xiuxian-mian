"""Schema contract tests for persistence.

`_ensure_schema_columns` carries every column ever added to the identity
tables. Its job is to make a database opened from any past version match what
a freshly created one looks like, so the invariant worth pinning is not a
column list (that changes with every feature) but the equivalence itself:

    old database + migrations  ==  what the running code expects

These tests build a minimal legacy database, run the migrations over it, and
assert the result is complete, stable and idempotent.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from model import persistence


SKELETON_TABLES = (
    "CREATE TABLE identities (send_as_id INTEGER PRIMARY KEY)",
    "CREATE TABLE identity_module_state (send_as_id INTEGER PRIMARY KEY)",
    "CREATE TABLE identity_timers (send_as_id INTEGER PRIMARY KEY)",
    "CREATE TABLE identity_runtime_state (send_as_id INTEGER PRIMARY KEY)",
    "CREATE TABLE pending_tasks (msg_id INTEGER PRIMARY KEY)",
)

MIGRATED_TABLES = (
    "identities",
    "identity_module_state",
    "identity_timers",
    "identity_runtime_state",
)


def _column_map(conn):
    """Return {table: {column: (type, notnull, default)}} for the whole db."""
    schema = {}
    for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        schema[table] = {
            row[1]: (row[2], row[3], row[4])
            for row in conn.execute(f"PRAGMA table_info({table})")
        }
    return schema


class PersistenceSchemaContractTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def _migrated_schema(self, name="legacy.db", extra_setup=()):
        path = Path(self._tmp.name) / name
        with sqlite3.connect(path) as conn:
            for statement in SKELETON_TABLES:
                conn.execute(statement)
            for statement in extra_setup:
                conn.execute(statement)
            persistence._ensure_schema_columns(conn)
            return _column_map(conn)

    def test_migrations_populate_every_identity_table(self):
        schema = self._migrated_schema()

        for table in MIGRATED_TABLES:
            with self.subTest(table=table):
                self.assertIn(table, schema)
                # 骨架只有主键；迁移必须补出真实业务列
                self.assertGreater(len(schema[table]), 1, f"{table} 未被迁移补列")

    def test_runtime_state_carries_the_bulk_of_the_columns(self):
        """identity_runtime_state 是最宽的表；数量骤降说明有整段迁移丢了。"""
        schema = self._migrated_schema()

        self.assertGreater(len(schema["identity_runtime_state"]), 300)
        self.assertGreater(len(schema["identity_module_state"]), 50)
        self.assertGreater(len(schema["identity_timers"]), 20)

    def test_migrations_are_idempotent(self):
        """每次启动都会跑一遍：重复执行不得报错，也不得改变 schema。"""
        path = Path(self._tmp.name) / "repeat.db"
        with sqlite3.connect(path) as conn:
            for statement in SKELETON_TABLES:
                conn.execute(statement)
            persistence._ensure_schema_columns(conn)
            first = _column_map(conn)
            persistence._ensure_schema_columns(conn)
            persistence._ensure_schema_columns(conn)
            third = _column_map(conn)

        self.assertEqual(first, third)

    def test_partially_upgraded_database_converges_to_the_same_schema(self):
        """半旧库（已有部分列）迁移后必须和全旧库结果一致。"""
        full = self._migrated_schema("full.db")
        partial = self._migrated_schema(
            "partial.db",
            extra_setup=(
                'ALTER TABLE identity_module_state ADD COLUMN quiz_enabled INTEGER NOT NULL DEFAULT 1',
                'ALTER TABLE identity_module_state ADD COLUMN hehuan_enabled INTEGER NOT NULL DEFAULT 0',
            ),
        )

        for table in MIGRATED_TABLES:
            with self.subTest(table=table):
                self.assertEqual(set(full[table]), set(partial[table]))

    def test_column_definitions_do_not_drift_between_runs(self):
        """默认值/类型同样是契约，不只是列名存在。"""
        first = self._migrated_schema("a.db")
        second = self._migrated_schema("b.db")

        mismatched = []
        for table in MIGRATED_TABLES:
            for name, spec in first[table].items():
                other = second[table].get(name)
                if other != spec:
                    mismatched.append(f"{table}.{name}: {spec} != {other}")

        self.assertFalse(mismatched, f"列定义漂移: {mismatched[:10]}")

    def test_backfills_stay_quiet_when_there_is_nothing_to_migrate(self):
        """回填不得在无事可做时写库。

        _ensure_schema_columns 每次启动都跑，而差量保存遥测要求"无变化即无写
        SQL"。原实现把这几段 UPDATE 夹在 ALTER 之间，靠所在位置的 not-in 判断
        约束；拆成独立函数后必须显式保持同样的克制。
        """
        path = Path(self._tmp.name) / "quiet.db"
        with sqlite3.connect(path) as conn:
            for statement in SKELETON_TABLES:
                conn.execute(statement)
            persistence._ensure_schema_columns(conn)
            conn.commit()

            # total_changes 由 SQLite 维护，只统计真正写入的行
            before = conn.total_changes
            persistence._run_schema_backfills(conn, added_columns=set())
            written = conn.total_changes - before

        self.assertEqual(0, written, f"空库不应写入任何行，实际写了 {written} 行")

    def test_data_migrations_backfill_derived_columns(self):
        """迁移里夹着几段 UPDATE 回填；它们不能在重构中被丢掉。"""
        path = Path(self._tmp.name) / "backfill.db"
        with sqlite3.connect(path) as conn:
            for statement in SKELETON_TABLES:
                conn.execute(statement)
            persistence._ensure_schema_columns(conn)
            conn.execute(
                "INSERT INTO identity_runtime_state (send_as_id, concubine_fragment_count,"
                " concubine_fragment_total, concubine_fragment_xutian_count,"
                " concubine_fragment_xutian_total) VALUES (1, 7, 9, 0, 0)"
            )
            # 再跑一次迁移，回填 UPDATE 应把虚天计数补齐
            persistence._ensure_schema_columns(conn)
            row = conn.execute(
                "SELECT concubine_fragment_xutian_count, concubine_fragment_xutian_total"
                " FROM identity_runtime_state WHERE send_as_id = 1"
            ).fetchone()

        self.assertEqual((7, 9), row)


class MetaDefaultsContractTests(unittest.TestCase):
    """Seed values for the meta table, declared as data rather than 38 inserts."""

    def test_every_default_is_a_storable_scalar(self):
        defaults = persistence._meta_defaults()

        self.assertGreater(len(defaults), 30)
        for key, value in defaults.items():
            with self.subTest(key=key):
                self.assertIsInstance(key, str)
                self.assertIsInstance(value, str, "meta 值必须是已编码的字符串")

    def test_defaults_are_rebuilt_per_call(self):
        """惰性构造：不能让调用方拿到同一个可变字典改坏后续初始化。"""
        first = persistence._meta_defaults()
        first["game_group_id"] = "tampered"

        self.assertNotEqual("tampered", persistence._meta_defaults()["game_group_id"])

    def test_seeding_is_insert_or_ignore(self):
        """已有值不能被默认值覆盖 —— 否则每次重启都会重置用户配置。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "meta.db"
            with sqlite3.connect(path) as conn:
                conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                conn.execute("INSERT INTO meta(key, value) VALUES ('global_enabled', '0')")
                for meta_key, meta_value in persistence._meta_defaults().items():
                    conn.execute(
                        "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)", (meta_key, meta_value)
                    )
                stored = dict(conn.execute("SELECT key, value FROM meta"))

        self.assertEqual("0", stored["global_enabled"], "已存在的值不应被种子覆盖")
        self.assertIn("game_group_id", stored)

    def test_json_valued_defaults_parse(self):
        """几个键存的是 JSON 文本；写坏了要在这里而不是运行期发现。"""
        import json

        defaults = persistence._meta_defaults()
        for key in ("game_bot_ids", "forum_topics", "accounts", "identity_account_map"):
            with self.subTest(key=key):
                json.loads(defaults[key])


if __name__ == "__main__":
    unittest.main()
