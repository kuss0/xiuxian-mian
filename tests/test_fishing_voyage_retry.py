import asyncio
from unittest.mock import AsyncMock

import pytest

from model.features import cave_treasure_runtime as cave
from model.features import fishing_dwelling_runtime as native
import test_fishing_dwelling_runtime as runtime_tests
from test_fishing_dwelling_runtime import configure, enable_voyage_handoff


fishing_env = runtime_tests.fishing_env


def test_sailing_retry_does_not_miss_the_confirmed_return_window(fishing_env, monkeypatch):
    h = fishing_env
    configure(h)
    enable_voyage_handoff(h)
    returned_at = h.now + 60
    h.identity.update(concubine_voyage_settled_at=0, concubine_voyage_status="sailing",
                      concubine_voyage_return_at=returned_at)
    worker = AsyncMock(return_value={"ok": False, "status": "blocked",
                                     "error": "fishing_companion_sailing"})
    monkeypatch.setattr(native, "run_native_fishing_production_flow", worker)
    result = asyncio.run(cave.run_cave_public_fishing(h.identity_id, h.url))
    assert not result["ok"] and not result["extra"]["terminal_skip"]
    assert h.now < h.identity["next_fishing_time"] < returned_at + native.VOYAGE_HANDOFF_SEC
    h.identity.update(concubine_voyage_settled_at=returned_at, concubine_voyage_status="idle")
    assert native.voyage_launch_wait_reason(h.identity_id, returned_at)
    h.daily.assert_not_awaited()


@pytest.mark.parametrize("return_at", [None, 0, True, "later", float("nan"), float("inf"), -1])
def test_unknown_sailing_clock_uses_bounded_recheck(fishing_env, monkeypatch, return_at):
    h = fishing_env
    configure(h)
    h.identity.update(concubine_voyage_status="sailing", concubine_voyage_return_at=return_at)
    # Test retry calculation independently of SQLite's rejection of NaN/NULL.
    monkeypatch.setattr(native.persistence, "save_state", lambda: True)
    monkeypatch.setattr(native, "run_native_fishing_production_flow", AsyncMock(return_value={
        "ok": False, "status": "blocked", "error": "fishing_companion_sailing"}))
    result = asyncio.run(cave.run_cave_public_fishing(h.identity_id, h.url))
    assert not result["ok"]
    assert h.identity["next_fishing_time"] == h.now + native.SAILING_RECHECK_SEC
    assert h.identity["concubine_voyage_status"] == "sailing"
    h.daily.assert_not_awaited()


def test_long_voyage_waits_for_known_return_instead_of_polling(fishing_env, monkeypatch):
    h = fishing_env
    configure(h)
    return_at = h.now + 6 * 3600
    h.identity.update(concubine_voyage_status="sailing", concubine_voyage_return_at=return_at)
    monkeypatch.setattr(native, "run_native_fishing_production_flow", AsyncMock(return_value={
        "ok": False, "status": "blocked", "error": "fishing_companion_sailing"}))
    asyncio.run(cave.run_cave_public_fishing(h.identity_id, h.url))
    assert return_at <= h.identity["next_fishing_time"] < return_at + native.VOYAGE_HANDOFF_SEC


def test_sailing_does_not_shorten_server_retry_after(fishing_env, monkeypatch):
    h = fishing_env
    configure(h)
    h.identity.update(concubine_voyage_status="sailing", concubine_voyage_return_at=h.now + 60)
    monkeypatch.setattr(native, "run_native_fishing_production_flow", AsyncMock(return_value={
        "ok": False, "status": "blocked", "error": "fishing_companion_sailing", "retry_after_sec": 3600}))
    asyncio.run(cave.run_cave_public_fishing(h.identity_id, h.url))
    assert h.identity["next_fishing_time"] == h.now + 3600


def test_stale_return_clock_without_sailing_does_not_delay_fishing(fishing_env, monkeypatch):
    h = fishing_env
    configure(h)
    h.identity.update(concubine_voyage_status="idle", concubine_voyage_return_at=h.now + 6 * 3600)
    monkeypatch.setattr(native, "run_native_fishing_production_flow", AsyncMock(return_value={
        "ok": False, "status": "blocked", "error": "fishing_companion_sailing"}))
    asyncio.run(cave.run_cave_public_fishing(h.identity_id, h.url))
    assert h.identity["next_fishing_time"] == h.now + native.SAILING_RECHECK_SEC


def test_sailing_schedule_failure_preserves_prior_timer(fishing_env, monkeypatch):
    h = fishing_env
    configure(h)
    before = h.identity["next_fishing_time"]
    monkeypatch.setattr(native, "run_native_fishing_production_flow", AsyncMock(return_value={
        "ok": False, "status": "blocked", "error": "fishing_companion_sailing"}))
    monkeypatch.setattr(native.persistence, "save_state", lambda: False)
    result = asyncio.run(cave.run_cave_public_fishing(h.identity_id, h.url))
    assert not result["ok"]
    assert h.identity["next_fishing_time"] == before
    h.daily.assert_not_awaited()
