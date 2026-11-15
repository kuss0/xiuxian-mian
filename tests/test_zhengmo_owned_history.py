import json
from pathlib import Path

import pytest

from model.features.zhengmo import parse_result, parse_status


CASES = json.loads((Path(__file__).parent / "fixtures/zhengmo_owned_history_20261115.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=lambda row: f"{row['day']}:{row['identity_id']}")
def test_owned_history_keeps_faction_intelligence_and_real_outcomes_separate(case):
    status = parse_status(case["panel_text"])
    assert status["faction"] == "魔道" and not status["done"]
    assert case["command"] == ".出征 " + status["recommendation"]
    result = parse_result(case["text"])
    assert result["days"] == status["days"] + 1
    assert result["personal_points"] == (6 if "反被克制" in case["text"] else 20)
