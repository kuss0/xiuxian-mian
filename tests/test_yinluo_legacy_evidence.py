import asyncio
import copy
import json
from unittest.mock import AsyncMock

import pytest

from model import cultivation_accounting as cultivation
from model import persistence
from model import state as state_module
from model import yinluo_accounting as accounting
from model.features import yinluo
from test_yinluo_accounting_runtime import (  # noqa: F401
    ACCOUNT, CHAT, CONVERT, IDENTITY, bind, env, event, observe,
    panel, prepare, receipts as receipts_fixture, runtime,
)
from test_yinluo_completion_lifecycle import CONSUME, READ, reload_state


receipts = receipts_fixture
FIELDS = ["auto_collect_pending", "auto_refine_pending", "auto_soothe_pending"]
INVALID = "legacy_pending_invalid"


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("payload", [None, [], False, "legacy", {"slot": 0}, {"slots": 1, "sent_at": "bad"}])
def test_pending_normalization_preserves_untrusted_legacy_payload(field, payload):
    before = copy.deepcopy(payload)
    normalized = yinluo.normalize_yinluo_observation({field: payload})
    assert normalized[field] == before
    assert payload == before
    if not isinstance(payload, dict):
        assert normalized[INVALID] is True
    assert yinluo.normalize_yinluo_observation(normalized) == normalized


@pytest.mark.parametrize("field", FIELDS)
def test_normalization_does_not_drop_transport_provenance_or_alias_raw_evidence(field):
    pending = {
        "slot": 3, "slots": [3], "target": "test_soul", "sent_at": 110.5,
        "chat_id": CHAT, "msg_id": 100, "account_id": ACCOUNT,
        "pre_soul_stocks": {"test_soul": 1},
    }
    before = copy.deepcopy(pending)
    normalized = yinluo.normalize_yinluo_observation({field: pending})
    assert normalized[field] == before
    normalized[field]["pre_soul_stocks"]["test_soul"] = 9
    assert pending == before


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("payload", [None, [], ["unknown"], {"slot": 0}])
def test_config_save_cannot_turn_unproved_legacy_work_into_empty_admission(receipts, field, payload):
    receipts[accounting.STATE_KEY] = {}
    receipts["yinluo_observation"][field] = copy.deepcopy(payload)
    assert accounting.admission_reason(IDENTITY, CONSUME) == "legacy_pending"
    with state_module.use_identity(IDENTITY):
        ok, reason, _ui = yinluo.set_yinluo_auto_config({"convert_amount": 20000})
    assert ok, reason
    assert receipts["yinluo_observation"][field] == payload
    restored = reload_state()
    assert restored["yinluo_observation"][field] == payload
    assert accounting.admission_reason(IDENTITY, CONSUME) == "legacy_pending"
    assert cultivation.cultivation_balance(IDENTITY)["value"] is None


@pytest.mark.parametrize("field", FIELDS)
def test_existing_book_cannot_ignore_an_untracked_legacy_reservation(receipts, monkeypatch, field):
    receipts["yinluo_observation"][field] = {"slot": 3, "slots": [3], "target": "test_soul", "sent_at": 110.5}
    sender = AsyncMock(side_effect=AssertionError("Untracked legacy work must block spending"))
    monkeypatch.setattr(yinluo, "send_game_command", sender)
    ok, reason, _plan = asyncio.run(yinluo.execute_yinluo_manual_action("convert", "10000", send_as_id=IDENTITY, now=200))
    assert not ok and reason
    assert accounting.admission_reason(IDENTITY, CONSUME) == "legacy_pending"
    assert accounting.resource_balance(IDENTITY, "sha")["status"] == "legacy_pending"
    assert cultivation.cultivation_balance(IDENTITY)["status"] == "legacy_pending"
    sender.assert_not_awaited()


@pytest.mark.parametrize("field", FIELDS)
def test_scheduler_ticks_and_reload_cannot_erase_a_transient_legacy_hold(receipts, monkeypatch, field):
    receipts[accounting.STATE_KEY] = {}
    receipts["yinluo_observation"][field] = ["unresolved"]
    sender = AsyncMock(side_effect=AssertionError("Normalization must not release a legacy hold"))
    monkeypatch.setattr(yinluo, "send_game_command", sender)
    with state_module.use_identity(IDENTITY):
        asyncio.run(yinluo.run_yinluo_scheduler(800))
    restored = reload_state()
    with state_module.use_identity(IDENTITY):
        asyncio.run(yinluo.run_yinluo_scheduler(5000))
    assert restored["yinluo_observation"][field] == ["unresolved"]
    assert accounting.admission_reason(IDENTITY, CONSUME) == "legacy_pending"
    sender.assert_not_awaited()


@pytest.mark.parametrize("raw", [None, [], False, "damaged"])
def test_corrupt_observation_root_remains_quarantined_after_a_config_save(receipts, raw):
    receipts[accounting.STATE_KEY] = {}
    receipts["yinluo_observation"] = raw
    assert accounting.admission_reason(IDENTITY, CONSUME) == "legacy_pending"
    with state_module.use_identity(IDENTITY):
        ok, reason, _ui = yinluo.set_yinluo_auto_config({"convert_amount": 20000})
    assert ok, reason
    restored = reload_state()
    assert restored["yinluo_observation"][INVALID] is True
    assert accounting.admission_reason(IDENTITY, CONSUME) == "legacy_pending"


@pytest.mark.parametrize("marker", [True, None, 0, "false"])
def test_a_retained_or_malformed_quarantine_marker_cannot_be_normalized_away(receipts, marker):
    receipts["yinluo_observation"][INVALID] = marker
    normalized = yinluo.normalize_yinluo_observation(receipts["yinluo_observation"])
    assert normalized[INVALID] is True
    receipts["yinluo_observation"] = normalized
    assert accounting.admission_reason(IDENTITY, CONSUME) == "legacy_pending"
    assert not accounting.admission_reason(IDENTITY, READ)


@pytest.mark.parametrize("legacy_action", ["\u8840\u6d17\u5c71\u6797", "\u53ec\u5524\u9b54\u5f71", "\u5316\u529f\u4e3a\u715e"])
def test_new_panel_does_not_erase_an_unowned_old_pending_summary(receipts, legacy_action):
    receipts[accounting.STATE_KEY] = {}
    old = {
        "last_action": legacy_action, "last_result": "pending", "last_observed_at": 110.0,
        "last_summary": "original unknown operation",
    }
    receipts["yinluo_observation"].update(old)
    assert observe(panel(msg_id=301, start=200, end=201))
    assert receipts[accounting.STATE_KEY]["hold"] == "legacy_pending"
    assert {key: receipts["yinluo_observation"][key] for key in old} == old
    restored = reload_state()
    assert {key: restored["yinluo_observation"][key] for key in old} == old
    assert accounting.admission_reason(IDENTITY, CONSUME) == "legacy_pending"


def test_empty_initial_observation_and_new_owned_pending_are_not_legacy(receipts):
    normalized = yinluo.normalize_yinluo_observation()
    assert normalized.get(INVALID, False) is False
    assert yinluo.normalize_yinluo_observation({}).get(INVALID, False) is False
    record = prepare(CONSUME)
    bind(record)
    received = event(CONSUME, "\u4f60\u5f00\u59cb\u8fd0\u8f6c\u9b54\u529f\uff0c\u5c06\u4fee\u4e3a\u8f6c\u5316\u4e3a\u715e\u6c14\u3002")
    assert observe(received)
    assert not accounting.read_accounting(IDENTITY)[0]["hold"]
    assert observe(event(CONSUME, CONVERT, msg_id=111, end=121))
    assert accounting.current_operation(IDENTITY, record["op_id"])["phase"] == "complete"
    assert not accounting.read_accounting(IDENTITY)[0]["hold"]


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("payload", [None, [], False, "legacy", {"slot": 0}, {"slots": 1, "sent_at": "bad"}])
def test_status_reads_are_nonmutating_and_safe_with_legacy_evidence(receipts, field, payload):
    receipts["yinluo_observation"][field] = copy.deepcopy(payload)
    before = copy.deepcopy(receipts)
    with state_module.use_identity(IDENTITY):
        ui = yinluo.get_yinluo_ui_state(now=200)
        status = yinluo.get_yinluo_status_text()
        recovery = yinluo.get_yinluo_sha_recovery_status(IDENTITY)
    assert ui["observed"]["sha_status"] == "legacy_pending"
    assert ui["observed"]["sha_known"] is False
    assert ui["observed"]["legacy_pending_invalid"] is (not isinstance(payload, dict))
    assert status
    assert recovery["sha_current"] is None
    assert receipts == before


@pytest.mark.parametrize("hold", ["capacity", "receipt_conflict"])
def test_legacy_evidence_cannot_replace_a_stronger_existing_hold(receipts, hold):
    receipts[accounting.STATE_KEY]["hold"] = hold
    receipts["yinluo_observation"]["auto_refine_pending"] = {"slot": 1}
    before = copy.deepcopy(receipts)
    assert accounting.read_accounting(IDENTITY)[0]["hold"] == hold
    assert accounting.admission_reason(IDENTITY, CONSUME) == hold
    assert receipts == before


@pytest.mark.parametrize("existing_book", [False, True])
@pytest.mark.parametrize("field", [*FIELDS, INVALID])
def test_unavailable_scheduler_retains_legacy_evidence_after_reload(receipts, monkeypatch, existing_book, field):
    if not existing_book:
        receipts[accounting.STATE_KEY] = {}
    receipts["yinluo_observation"][field] = True if field == INVALID else {"slot": 1, "sent_at": 110.5}
    before = copy.deepcopy(receipts["yinluo_observation"])
    assert accounting.admission_reason(IDENTITY, CONSUME) == "legacy_pending"
    sender = AsyncMock(side_effect=AssertionError("Unavailable modules must not send"))
    monkeypatch.setattr(yinluo, "send_game_command", sender)
    monkeypatch.setattr(yinluo, "is_module_available", lambda _module: False)
    with state_module.use_identity(IDENTITY):
        asyncio.run(yinluo.run_yinluo_scheduler(800))
    assert receipts["yinluo_enabled"] is False
    assert receipts["yinluo_observation"] == before
    restored = reload_state()
    assert restored["yinluo_observation"] == before
    assert accounting.admission_reason(IDENTITY, CONSUME) == "legacy_pending"
    assert cultivation.cultivation_balance(IDENTITY)["value"] is None
    sender.assert_not_awaited()


@pytest.mark.parametrize("existing_book", [False, True])
@pytest.mark.parametrize("raw", [None, [], False, "damaged"])
def test_direct_save_preserves_corrupt_root_quarantine(receipts, existing_book, raw):
    if not existing_book:
        receipts[accounting.STATE_KEY] = {}
    receipts["yinluo_observation"] = raw
    assert accounting.admission_reason(IDENTITY, CONSUME) == "legacy_pending"
    assert persistence.save_state()
    restored = reload_state()
    assert restored["yinluo_observation"].get(INVALID) is True
    assert accounting.admission_reason(IDENTITY, CONSUME) == "legacy_pending"
    assert cultivation.cultivation_balance(IDENTITY)["value"] is None


@pytest.mark.parametrize("existing_book", [False, True])
@pytest.mark.parametrize("encoded", ["null", "[]", "false", "0", '""', '"damaged"', "{bad"])
def test_established_roster_loads_corrupt_root_without_repairing_storage(receipts, existing_book, encoded):
    if not existing_book:
        receipts[accounting.STATE_KEY] = {}
    state_module._meta_state["identity_membership_initialized"] = True
    assert persistence.save_state()
    connection = persistence.get_db_conn()
    connection.execute(
        "UPDATE identity_runtime_state SET yinluo_observation=? WHERE send_as_id=?", (encoded, IDENTITY),
    )
    connection.commit()
    restored = reload_state()
    assert restored["yinluo_observation"].get(INVALID) is True
    assert accounting.admission_reason(IDENTITY, CONSUME) == "legacy_pending"
    assert cultivation.cultivation_balance(IDENTITY)["value"] is None
    assert connection.execute(
        "SELECT yinluo_observation FROM identity_runtime_state WHERE send_as_id=?", (IDENTITY,),
    ).fetchone()[0] == encoded
    with state_module.use_identity(IDENTITY):
        ok, reason, _ui = yinluo.set_yinluo_auto_config({"convert_amount": 20000})
    assert ok, reason
    assert reload_state()["yinluo_observation"][INVALID] is True
    assert accounting.admission_reason(IDENTITY, CONSUME) == "legacy_pending"


def test_roster_initialization_writeback_retains_corrupt_root_quarantine(receipts):
    receipts[accounting.STATE_KEY] = {}
    state_module._meta_state["identity_membership_initialized"] = False
    assert persistence.save_state()
    connection = persistence.get_db_conn()
    connection.execute(
        "UPDATE identity_runtime_state SET yinluo_observation=? WHERE send_as_id=?", ("{bad", IDENTITY),
    )
    connection.commit()
    assert reload_state()["yinluo_observation"] == {INVALID: True}
    encoded = connection.execute(
        "SELECT yinluo_observation FROM identity_runtime_state WHERE send_as_id=?", (IDENTITY,),
    ).fetchone()[0]
    assert json.loads(encoded) == {INVALID: True}
    assert accounting.admission_reason(IDENTITY, CONSUME) == "legacy_pending"


def test_empty_legacy_database_observation_is_not_quarantined(receipts):
    receipts[accounting.STATE_KEY] = {}
    receipts["yinluo_observation"] = {}
    assert persistence.save_state()
    assert reload_state()["yinluo_observation"] == {}
    assert not accounting.read_accounting(IDENTITY)[0]["hold"]
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 500000
