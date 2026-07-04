import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from model import ui
from model.features import passive_inbox


class _MemoryReader:
    def __init__(self, request_bytes):
        self._request_bytes = request_bytes

    async def readuntil(self, separator):
        return self._request_bytes

    async def readexactly(self, size):
        return b""


class _MemoryWriter:
    def __init__(self, peer):
        self.peer = peer
        self.body = bytearray()

    def get_extra_info(self, name):
        return self.peer if name == "peername" else None

    def write(self, data):
        self.body.extend(data)

    async def drain(self):
        return None

    def close(self):
        return None

    async def wait_closed(self):
        return None


def test_passive_stats_save_does_not_build_contract_gap_summary():
    original_stats = passive_inbox._passive_stats
    passive_inbox._passive_stats = {
        "total": 3,
        "changed": 1,
        "skipped": 2,
        "modules": {"天星": 1},
        "skip_reasons": {"external": 2},
        "recent": [{"module": "天星", "reason": "handled"}],
    }
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            stats_path = str(Path(tmpdir) / "passive_inbox_stats.json")
            with patch.object(passive_inbox, "STATE_DIR", tmpdir), \
                 patch.object(passive_inbox, "PASSIVE_INBOX_STATS_FILE", stats_path), \
                 patch.object(passive_inbox, "_build_contract_gap_summary", side_effect=AssertionError("hot path scan")):
                passive_inbox._save_passive_stats()

            payload = json.loads(Path(stats_path).read_text(encoding="utf-8"))

        assert payload["total"] == 3
        assert payload["modules"] == {"天星": 1}
        assert "contract_gap_summary" not in payload
        assert "attention_total" not in payload
    finally:
        passive_inbox._passive_stats = original_stats


def test_setup_mode_fake_session_is_loopback_only():
    request = b"GET / HTTP/1.1\r\nHost: example\r\n\r\n"

    async def run_for_peer(peer):
        writer = _MemoryWriter(peer)
        with patch.object(ui, "get_accounts", return_value=[]), \
             patch.object(ui, "get_identity_ids", return_value=[]), \
             patch.object(ui, "_get_authenticated_session", return_value=(None, "")), \
             patch.object(ui, "_render_login_page", return_value="LOGIN"), \
             patch.object(ui, "render_ui_page", return_value="SETUP"):
            await ui.handle_ui_http(_MemoryReader(request), writer)
        return bytes(writer.body).decode("utf-8", errors="ignore")

    remote_response = asyncio.run(run_for_peer(("203.0.113.7", 49152)))
    local_response = asyncio.run(run_for_peer(("127.0.0.1", 49152)))

    assert "LOGIN" in remote_response
    assert "SETUP" not in remote_response
    assert "SETUP" in local_response
