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
