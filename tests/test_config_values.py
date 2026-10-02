import pytest

from model.config_values import coerce_bool


@pytest.mark.parametrize("value", [True, 1, -2, 0.5, float("nan"), float("inf"),
                                  "1", " TRUE ", "Yes", "y", "ON", "open", "enable", "enabled",
                                  "开", "开启", "启用"])
def test_explicit_true_ignores_default(value):
    assert coerce_bool(value, default=False) is True


@pytest.mark.parametrize("value", [False, 0, 0.0, "", "  ", "0", " FALSE ", "No", "n", "OFF",
                                  "close", "disable", "disabled", "关", "关闭", "禁用"])
def test_explicit_false_ignores_default(value):
    assert coerce_bool(value, default=True) is False


@pytest.mark.parametrize("value", [None, "unknown", "2", [], {}, object()])
@pytest.mark.parametrize("default", [False, True, "", "enabled"])
def test_unknown_uses_default_truthiness(value, default):
    assert coerce_bool(value, default) is bool(default)


def test_existing_entry_points_share_one_implementation():
    from model import control, state, ui

    assert control._coerce_control_bool is coerce_bool
    assert state._coerce_meta_bool is coerce_bool
    assert ui._coerce_ui_bool is coerce_bool
