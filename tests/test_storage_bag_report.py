import json
import sqlite3
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from tools import storage_bag_report


def _write_report_db(path):
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            """
            CREATE TABLE identities (
                send_as_id INTEGER PRIMARY KEY,
                username TEXT,
                label TEXT,
                daohao TEXT,
                enabled INTEGER
            )
            """
        )
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.executemany(
            "INSERT INTO identities(send_as_id, username, label, daohao, enabled) VALUES (?, ?, ?, ?, ?)",
            [
                (3101, "boxboxji", "盒子", "守一子", 1),
                (8659059191, "WalterWA2000", "wa2000", "清源子", 1),
            ],
        )
        conn.execute(
            "INSERT INTO meta(key, value) VALUES ('storage_bag_records', ?)",
            (
                json.dumps(
                    {
                        "3101": {
                            "owner": "boxboxji",
                            "owner_username": "boxboxji",
                            "label": "盒子",
                            "items": {"阴凝之晶": 5, "玄铁剑": 1},
                            "updated_at_text": "2026-06-11 08:00:00 UTC+8",
                            "source": "storage_bag_api_cultivator",
                        },
                        "8659059191": {
                            "owner": "WalterWA2000",
                            "owner_username": "WalterWA2000",
                            "label": "wa2000",
                            "items": {"阴凝之晶": 999},
                            "updated_at_text": "2026-06-11 08:00:00 UTC+8",
                            "source": "storage_bag_api_cultivator",
                        },
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_cached_storage_bag_report_includes_wa_by_default(tmp_path):
    db_file = tmp_path / "state.db"
    _write_report_db(db_file)
    identities = storage_bag_report.load_identities(db_file)

    snapshots, stats = storage_bag_report.read_cached_snapshots(
        db_file,
        identities,
        (),
    )
    text = storage_bag_report.build_report(
        snapshots,
        stats,
        identities,
        (),
        messages_dir=tmp_path,
        item_label="物资",
        source_label="API/本地缓存",
    )

    assert "【储物袋物资盘点】" in text
    assert "数据源：API/本地缓存" in text
    assert "阴凝之晶：1,004" in text
    assert "玄铁剑：1" in text
    assert "999" in text
    assert "wa2000" in text.casefold()


def test_storage_bag_report_auto_prefers_cache_over_old_logs(tmp_path, capsys):
    db_file = tmp_path / "state.db"
    messages_dir = tmp_path / "messages"
    messages_dir.mkdir()
    _write_report_db(db_file)
    (messages_dir / "2026-06-10.log").write_text(
        json.dumps(
            {
                "ts": "2026-06-10 08:00:00 UTC+8",
                "message_id": 1,
                "text": "@boxboxji 的储物袋\n材料:\n- 阴凝之晶 x1",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    exit_code = storage_bag_report.main(
        [
            "--db-file",
            str(db_file),
            "--messages-dir",
            str(messages_dir),
            "--chunk-limit",
            "20000",
        ]
    )

    captured = capsys.readouterr().out
    assert exit_code == 0
    assert "数据源：API/本地缓存" in captured
    assert "阴凝之晶：1,004" in captured
    assert "阴凝之晶：1｜" not in captured
