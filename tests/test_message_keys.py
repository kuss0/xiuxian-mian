from types import SimpleNamespace

import pytest

from model.message_keys import find_message_key, get_message_record, message_key, message_key_parts, pop_message_record


def test_same_id_requires_a_chat_when_two_records_exist():
    records = {(-1001, 42): {"cmd": ".one"}, (-1002, 42): {"cmd": ".two"}}
    assert find_message_key(records, 42) is None
    assert get_message_record(records, 42) is None
    assert pop_message_record(records, 42) is None
    assert len(records) == 2
    assert get_message_record(records, 42, chat_id=-1002) == {"cmd": ".two"}
    assert pop_message_record(records, (-1001, 42)) == {"cmd": ".one"}
    assert list(records) == [(-1002, 42)]


def test_legacy_keys_use_only_recorded_provenance():
    records = {42: {"chat_id": -1001}, 43: 123.0}
    assert message_key_parts(42, records[42]) == (-1001, 42)
    assert find_message_key(records, 42, chat_id=-1001) == 42
    assert find_message_key(records, 42, chat_id=-1002) is None
    assert find_message_key(records, 43, chat_id=-1001) is None
    assert find_message_key(records, 43, chat_id=0) == 43


def test_message_receipts_preserve_chat_and_reject_conflicts():
    receipt = SimpleNamespace(id=42, chat_id=-1001)
    assert message_key(receipt) == (-1001, 42)
    with pytest.raises(ValueError, match="conflicting"):
        message_key(receipt, -1002)
    with pytest.raises(ValueError, match="conflicting"):
        message_key_parts((-1001, 42), {"chat_id": -1002})


def test_invalid_or_duplicate_legacy_references_are_not_selected():
    records = {42: {"chat_id": -1001}, (-1001, 42): {"chat_id": -1001}}
    assert find_message_key(records, 42, chat_id=-1001) is None
    assert find_message_key(records, 0) is None
    assert find_message_key(records, "not-an-id") is None
