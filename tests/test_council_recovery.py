import copy
import json
from pathlib import Path
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model.features.council_recovery import completed_manual_council
from model import app_weekly_adventures as app


@pytest.fixture
def live():
    return json.loads((Path(__file__).parent / "fixtures/council_manual_completion_20261114.json").read_text())


def test_manual_council_actual_party_has_complete_owned_end_chain(live):
    before = copy.deepcopy(live)
    proof = completed_manual_council(live["run"], live["rows"], live["bot_ids"])
    assert proof["start_command_id"] == 13191022
    assert proof["terminal_message_id"] == 13191160
    assert proof["actual_identity_ids"] == [301299112, 8659059191, 7538826434, 5170745913]
    assert not proof["reward_accounted"]
    assert live == before


@pytest.mark.parametrize("change", ["wrong_chat", "wrong_topic", "forward", "wrong_bot", "wrong_start_actor", "wrong_start_reply", "wrong_final_actor", "wrong_final_reply", "wrong_count", "missing_round", "foreign_start", "late", "bound"])
def test_manual_council_incomplete_foreign_or_conflicting_chain_never_releases(live, change):
    rows = live["rows"]
    start = next(r for r in rows if r["message_id"] == 13191024)
    end = next(r for r in rows if r["message_id"] == 13191160)
    if change in {"wrong_chat", "wrong_topic", "forward", "wrong_bot", "late"}:
        key, value = {"wrong_chat": ("chat_id", -1), "wrong_topic": ("topic_id", 99),
                      "forward": ("forwarded", True), "wrong_bot": ("sender_id", 1),
                      "late": ("server_event_at", live["run"]["started_at"] + 7200)}[change]
        end[key] = value
    elif change == "wrong_start_actor":
        next(r for r in rows if r["message_id"] == 13191022)["sender_id"] = 1
    elif change == "wrong_start_reply":
        start["reply_to_msg_id"] = 42
    elif change == "wrong_final_actor":
        next(r for r in rows if r["message_id"] == 13191156)["sender_id"] = 1
    elif change == "wrong_final_reply":
        end["reply_to_msg_id"] = live["run"]["topic_id"]
    elif change == "wrong_count":
        end["text"] = end["text"].replace("4 位长老", "5 位长老")
    elif change == "missing_round":
        rows[:] = [r for r in rows if r["message_id"] != 13191093]
    elif change == "foreign_start":
        foreign = copy.deepcopy(start)
        foreign.update(message_id=13191060, server_event_at=start["server_event_at"] + 100)
        rows.append(foreign)
    else:
        live["run"]["bound"] = True
    assert completed_manual_council(live["run"], rows, live["bot_ids"]) is None


@pytest.mark.parametrize("failure", [None, "save", "changed", "replaced"])
def test_startup_reconciliation_is_durable_idempotent_and_never_sends(live, monkeypatch, failure):
    run = live["run"]
    run["foreign_busy"] = True  # The original held run had observed a later, unrelated room.
    records = {"runs": {"qipai": run}, "quotas": {"qipai": {"301299112": {"used": 1}}}}
    before = copy.deepcopy(records)
    save = Mock(side_effect=RuntimeError("disk unavailable") if failure == "save" else None)
    send = Mock(side_effect=AssertionError("recovery must never send"))
    ctx = SimpleNamespace(records=lambda: records, bot_ids=lambda: live["bot_ids"], save=save, send=send)
    monkeypatch.setattr(app, "_context", lambda: ctx)

    def scan(*args, **kwargs):
        if failure == "changed":
            run["result"] = "new passive observation"
        elif failure == "replaced":
            records["runs"]["qipai"] = copy.deepcopy(run)
        return iter(live["rows"])

    monkeypatch.setattr(app, "iter_message_log_rows", scan)
    # Replay still uses the real reducer, which must ignore the reconciled run.
    ctx.accept_run = lambda r: True
    ctx.topic = lambda e: e.topic_id
    ctx.event_time = lambda e: e.date.timestamp()
    ctx.notice = send

    async def recover():
        if failure == "save":
            with pytest.raises(RuntimeError, match="disk unavailable"):
                await app._recover_terminal_receipts(run["started_at"] + 86400)
            assert records == before
        elif failure:
            # A concurrent update is left for its owner, including normal replay.
            from model.features import weekly_adventures
            monkeypatch.setattr(weekly_adventures, "game_event", AsyncMock())
            await app._recover_terminal_receipts(run["started_at"] + 86400)
            save.assert_not_called()
            assert records["runs"]["qipai"]["phase"] == "held"
        else:
            await app._recover_terminal_receipts(run["started_at"] + 86400)
            assert run["phase"] == "reconciled" and not run["requires_reconciliation"]
            assert run["completion_evidence"]["terminal_message_id"] == 13191160
            assert records["quotas"] == before["quotas"]
            assert records["reconciliations"][0]["original_run"] == before["runs"]["qipai"]
            saved = save.call_count
            assert saved > 0
            await app._recover_terminal_receipts(run["started_at"] + 86401)
            assert save.call_count == saved
            assert len(records["reconciliations"]) == 1
            assert run["next_at"] >= run["started_at"] + 86400 + 300
        send.assert_not_called()

    asyncio.run(recover())
