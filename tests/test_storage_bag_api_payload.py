from copy import deepcopy

import pytest

from model import storage_bag_api_payload as payload


@pytest.mark.parametrize("raw,expected", [
    (None, ({}, False)), ("{}", ({}, False)), ([], ({}, True)), ({}, ({}, True)),
    ([{"item_id": "item_fishing_bait_plain", "quantity": "1,200"},
      {"name": "【凡饵】", "count": 3}, None], ({"凡饵": 1203}, True)),
    ({"materials": {"item_fishing_bait_spirit_rice": {"num": 2},
                    "item_fishing_bait_demon_blood": 4}}, ({"灵米饵": 2, "妖血饵": 4}, True)),
    ({"items": {"丹药": "5", "无效": "bad", "欠缺": -1}}, ({"丹药": 5}, True)),
    ({"owner": "100", "msg_id": 123, "灵石": "2,000"}, ({"灵石": 2000}, True)),
])
def test_inventory_formats_and_empty_distinction(raw, expected):
    before = deepcopy(raw)
    assert payload.storage_bag_api_extract_items(raw) == expected
    assert raw == before


@pytest.mark.parametrize("key", ["items", "current", "materials", "inventory", "storage", "bag", "snapshots"])
def test_list_containers_and_custom_name_map(key):
    assert payload.storage_bag_api_extract_items(
        {key: [{"id": "item_fishing_bait_plain", "amount": "3"}]},
        {"item_fishing_bait_plain": "custom bait"},
    ) == ({"custom bait": 3}, True)


@pytest.mark.parametrize("raw,expected", [
    (" {\"a\": 1} ", {"a": 1}), (" [1] ", [1]), ("{bad}", "{bad}"),
    ("", ""), ("false", "false"), (None, None),
])
def test_json_parser_preserves_non_containers_and_malformed_text(raw, expected):
    assert payload.parse_json_maybe(raw) == expected


def test_owner_precedence_and_nested_json_do_not_mutate_input():
    raw = {"user": '{"telegram_id": -1001, "username": "nested"}',
           "profile": {"username": "profile"}, "username": "root",
           "identity_id": "bad", "cave": '{"level": 2}'}
    before = deepcopy(raw)
    assert payload.storage_bag_api_extract_owner_fields(raw) == (-1001, "root")
    assert payload.flatten_api_row(raw)["dongfu"] == {"level": 2}
    assert raw == before
    assert payload.storage_bag_api_extract_owner_fields(None) == (0, "")


@pytest.mark.parametrize("raw,normalize,expected", [
    (" @new_name1 ", False, "new_name1"), ("name-12345", False, "name-12345"),
    ("name-12345", True, "name"), ("name-123", True, "name-123"), (None, True, ""),
])
def test_candidate_normalization(raw, normalize, expected):
    assert payload.storage_bag_api_candidate_from_value(raw, normalize_suffix=normalize) == expected


def test_ui_and_runtime_share_pure_parsers_but_keep_write_boundaries():
    from model import storage_bag_api_runtime as runtime, ui

    for name in ("candidate_from_value", "normalize_item_name", "item_count", "add_item",
                 "resolve_item_name", "extract_items", "extract_owner_fields"):
        symbol = "storage_bag_api_" + name
        assert getattr(runtime, symbol) is getattr(payload, symbol)
        assert getattr(ui, "_" + symbol) is getattr(payload, symbol)
    assert runtime._parse_json_maybe is ui._tianjige_parse_json_maybe is payload.parse_json_maybe
    assert runtime._flatten_api_row is ui._tianjige_flatten_api_row is payload.flatten_api_row
    assert runtime.DEFAULT_STORAGE_BAG_ITEM_NAME_MAP is ui._STORAGE_BAG_API_DEFAULT_ITEM_NAME_MAP
    assert runtime.storage_bag_api_apply_payload is not ui._storage_bag_api_apply_payload
