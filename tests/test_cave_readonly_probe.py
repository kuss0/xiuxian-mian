import runpy
import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def probe(monkeypatch):
    tools = Path(__file__).resolve().parents[1] / "tools"
    monkeypatch.syspath_prepend(str(tools))
    return runpy.run_path(str(tools / "cave_readonly_probe.py"))


def test_probe_has_only_scoped_reads(probe):
    assert set(probe["READS"]) == {".天机盘", ".我的阵法", ".我的灵兽", "fishing_context", "fishing_state", "inventory"}
    assert set(probe["COMMAND_READS"]) == {".天机盘", ".我的阵法", ".我的灵兽"}
    assert not {".装备", ".卸下法宝", ".斗法"}.intersection(probe["COMMAND_READS"])


def inventory_payload(active):
    return {"account": {"playerId": 7, "bagTreasure": {
        "items": [], "materials": [], "treasures": [{"name": "bound", "quantity": 6}], "active": active,
    }}}


@pytest.mark.parametrize("active", [[], [{"name": "equipped", "token": "secret", "quantity": 1}]])
def test_inventory_keeps_active_not_bound_inventory(probe, active):
    assert probe["inventory_loadout_report"](inventory_payload(active), 7) == {
        "player_id": 7, "active": [{"name": row["name"]} for row in active],
        "active_count": len(active), "complete": True,
    }


@pytest.mark.parametrize("field", ["items", "materials", "treasures", "active"])
def test_inventory_missing_list_cannot_prove_unequipped(probe, field):
    payload = inventory_payload([])
    del payload["account"]["bagTreasure"][field]
    with pytest.raises(ValueError, match="inventory_incomplete"):
        probe["inventory_loadout_report"](payload, 7)


@pytest.mark.parametrize("player", [None, "7", True, 8])
def test_inventory_rejects_wrong_player(probe, player):
    payload = inventory_payload([])
    payload["account"]["playerId"] = player
    with pytest.raises(ValueError, match="inventory_player_mismatch"):
        probe["inventory_loadout_report"](payload, 7)


@pytest.mark.parametrize("active", [[None], [{}], [{"name": ""}], [{"name": []}],
                                   [{"name": "x" * 161}], [{"name": "x"}] * 101])
def test_inventory_rejects_malformed_active(probe, active):
    with pytest.raises(ValueError, match="inventory_active_invalid"):
        probe["inventory_loadout_report"](inventory_payload(active), 7)


@pytest.mark.parametrize("failure", ["", "player", "http"])
def test_inventory_probe_uses_only_scoped_reads(probe, monkeypatch, tmp_path, failure):
    namespace = probe["probe"].__globals__
    session_dir = tmp_path / "data/session"
    session_dir.mkdir(parents=True)
    with sqlite3.connect(session_dir / "account_7.session") as db:
        db.execute("CREATE TABLE sessions (dc_id, server_address, port, auth_key)")
        db.execute("INSERT INTO sessions VALUES (2, '127.0.0.1', 443, ?)", (b"x" * 256,))
    entry = "https://t.me/fanrenxiuxian_bot?startapp=df_FIXTURE999"
    monkeypatch.setitem(namespace, "load_owner", lambda *_: (7, {}, {"cave_public_entry_urls": [entry]}))
    monkeypatch.setitem(namespace, "load_dotenv", lambda *_: {"API_ID": "1", "API_HASH": "fixture"})
    monkeypatch.setattr(namespace["time"], "sleep", lambda *_: None)
    lifecycle = []

    class Client:
        def __init__(self, *args, **kwargs):
            assert kwargs["receive_updates"] is False
            assert kwargs["request_retries"] == kwargs["connection_retries"] == 0

        async def connect(self):
            lifecycle.append("connect")

        async def disconnect(self):
            lifecycle.append("disconnect")

        async def get_me(self):
            return SimpleNamespace(id=7)

        async def get_input_entity(self, _):
            return 7

        async def __call__(self, _):
            return SimpleNamespace(url="https://asc.aiopenai.app/#tgWebAppData=fixture")

    calls = []

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, *, json, timeout, allow_redirects):
            endpoint = url.removeprefix(probe["BASE"])
            calls.append((endpoint, json))
            assert timeout == (8, 25) and allow_redirects is False and self.trust_env is False
            data = inventory_payload([]) if endpoint == "section" else {"account": {"playerId": 7}}
            data.update(ok=True, identity={"choices": [{"playerId": 7}]})
            if endpoint == "section" and failure == "player":
                data["account"]["playerId"] = 8

            def raise_for_status():
                if endpoint == "section" and failure == "http":
                    raise namespace["requests"].HTTPError("fixture")

            return SimpleNamespace(status_code=400 if failure == "http" and endpoint == "section" else 200,
                                   raise_for_status=raise_for_status, json=lambda: data)

    monkeypatch.setitem(namespace, "TelegramClient", Client)
    monkeypatch.setattr(namespace["requests"], "Session", Session)
    args = SimpleNamespace(project_root=tmp_path, identity=7, read="inventory")
    report = {"steps": []}
    if failure:
        with pytest.raises((ValueError, namespace["requests"].HTTPError)):
            asyncio.run(probe["probe"](args, report))
        assert "inventory" not in report
    else:
        asyncio.run(probe["probe"](args, report))
        assert report["status"] == "read_returned" and report["inventory"]["active_count"] == 0
    assert lifecycle == ["connect", "disconnect"]
    assert [endpoint for endpoint, _ in calls] == ["start", "start", "details", "section"]
    assert calls[-1][1] == {"token": "df_FIXTURE999", "initData": "fixture", "playerId": 7, "section": "inventory"}
    assert all("command" not in payload and "action" not in payload for _, payload in calls)


@pytest.mark.parametrize("url", [
    "http://t.me/fanrenxiuxian_bot?startapp=df_FIXTURE999",
    "https://t.me/unrelated_bot?startapp=df_FIXTURE999",
    "https://evil.invalid/fanrenxiuxian_bot?startapp=df_FIXTURE999",
    "https://t.me/fanrenxiuxian_bot?startapp=fish_FIXTURE999",
    "https://other@t.me/fanrenxiuxian_bot?startapp=df_FIXTURE999",
])
def test_probe_rejects_untrusted_entry(probe, url):
    with pytest.raises(ValueError):
        probe["entry_parts"](url)


def test_probe_accepts_current_official_bot_shape(probe):
    assert probe["entry_parts"]("https://t.me/hantianzun21_bot?startapp=df_FIXTURE999") == ("hantianzun21_bot", "df_FIXTURE999")


def test_fishing_report_retains_only_allowlisted_supply_fields(probe):
    cost = {"itemId": "stone", "name": "stone", "qty": 2, "owned": 80}
    bait = {"itemId": "bait", "name": "bait", "count": 0, "unlocked": True}
    chum = {"key": "chum", "name": "chum", "usedToday": 0, "remainingToday": 2, "affordable": True}
    report = probe["fishing_context_report"]({
        "serverNow": 1790000000000, "baits": [bait], "token": "secret",
        "shop": {"castActive": False, "activeChum": None, "token": "secret",
                 "baits": [{**bait, "cost": [{**cost, "token": "secret"}], "token": "secret"}],
                 "chums": [{**chum, "cost": [cost], "token": "secret"}]},
    })
    assert report == {"serverNow": 1790000000000, "baits": [bait], "shop": {
        "castActive": False, "activeChum": None,
        "baits": [{**bait, "cost": [cost]}], "chums": [{**chum, "cost": [cost]}],
    }}


@pytest.mark.parametrize("context", [None, {"baits": {}}, {"baits": [None]}, {"shop": None},
                                     {"shop": {"baits": [{"cost": {}}]}}, {"shop": {"activeChum": []}},
                                     {"shop": {"chums": [{}] * 101}}])
def test_fishing_report_rejects_malformed_supply(probe, context):
    with pytest.raises(ValueError):
        probe["fishing_context_report"](context)


def test_fishing_report_does_not_invent_missing_fields(probe):
    assert probe["fishing_context_report"]({"shop": {}}) == {"shop": {}}


def test_timing_report_keeps_only_numeric_nonsecret_timestamps(probe):
    assert probe["fishing_session_timing_report"]({
        "serverNow": 1790785381978, "startedAt": 1790784769356,
        "biteAt": 1790784784356, "expiresAt": 1790784788356,
        "sessionId": "secret", "token": "secret", "initData": "secret",
    }) == {"serverNow": 1790785381978, "startedAt": 1790784769356,
           "biteAt": 1790784784356, "expiresAt": 1790784788356}
    assert probe["fishing_session_timing_report"]({
        "serverNow": True, "startedAt": "secret", "biteAt": -1, "expiresAt": 10**15,
    }) == {}


def test_checkpoint_report_keeps_evidence_without_credentials(probe):
    report = probe["fishing_checkpoint_report"]({"fight": {
        "startedAt": 1790898000000, "token": "secret", "challengeId": "secret",
        "checkpoint": {"durationMs": 2500, "secret": "secret",
                       "events": [{"t": 20, "holding": True, "token": "secret"}],
                       "details": {"progress": 26.9, "holding": True, "samples": 125, "token": "secret"}},
    }})
    assert report == {"startedAt": 1790898000000, "checkpoint_present": True,
                      "checkpoint": {"durationMs": 2500, "events": [{"t": 20, "holding": True}],
                                     "details": {"progress": 26.9, "holding": True, "samples": 125}}}
    assert probe["fishing_checkpoint_report"]({}) is None
    assert probe["fishing_checkpoint_report"]({"fight": {}}) == {"checkpoint_present": False}
    assert probe["fishing_checkpoint_report"]({"fight": {"checkpoint": None}}) == {"checkpoint_present": True}


def test_checkpoint_report_omits_unbounded_or_nonnumeric_values(probe):
    report = probe["fishing_checkpoint_report"]({"fight": {
        "startedAt": "secret", "checkpointIntervalMs": True, "maxDurationMs": float("inf"),
        "checkpoint": {"durationMs": "secret", "events": [{}] * 1001,
                       "details": {"progress": float("nan"), "tension": {}, "holding": "secret"}},
    }})
    assert report == {"checkpoint_present": True, "checkpoint": {"details": {}}}


@pytest.mark.parametrize("change", ["none", "owner", "player", "cast", "site", "missing"])
def test_state_read_must_use_identity_owned_cast(probe, tmp_path, change):
    folder = tmp_path / "data" / "state"
    folder.mkdir(parents=True)
    record = {"identity_id": 1, "account_id": 2, "player_id": 3, "cast_id": "a" * 32, "site_id": "west-shore"}
    if change == "owner":
        record["account_id"] = 4
    elif change == "player":
        record["player_id"] = 4
    elif change == "cast":
        record["cast_id"] = "arbitrary"
    elif change == "site":
        record["site_id"] = "unknown"
    with sqlite3.connect(folder / "chaogu_state.db") as db:
        db.execute("CREATE TABLE identity_runtime_state (send_as_id INTEGER, fishing_native_operation TEXT)")
        if change != "missing":
            db.execute("INSERT INTO identity_runtime_state VALUES (1, ?)", (json.dumps(record),))
    if change == "none":
        assert probe["fishing_state_scope"](tmp_path, 1, 2, 3) == {
            "siteId": "west-shore", "castOperationId": "a" * 32, "refreshContext": True,
        }
    else:
        with pytest.raises(ValueError, match="native_state_scope_missing"):
            probe["fishing_state_scope"](tmp_path, 1, 2, 3)
