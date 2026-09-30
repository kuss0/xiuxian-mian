import runpy
from pathlib import Path

import pytest


@pytest.fixture
def probe(monkeypatch):
    tools = Path(__file__).resolve().parents[1] / "tools"
    monkeypatch.syspath_prepend(str(tools))
    return runpy.run_path(str(tools / "cave_readonly_probe.py"))


def test_probe_has_only_scoped_reads(probe):
    assert set(probe["READS"]) == {".天机盘", ".我的阵法", ".我的灵兽", "fishing_context"}


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
