from unittest.mock import Mock

import pytest

from tools import lab_send_crash_probe


@pytest.mark.parametrize("kwargs", [
    {"command_name": "unsupported"}, {"crash_point": "unsupported"},
    {"tracked": "false"}, {"tracked": 0}, {"advance_seconds": True},
    {"advance_seconds": "86400"}, {"advance_seconds": -1},
    {"advance_seconds": float("nan")}, {"advance_seconds": float("inf")},
    {"advance_seconds": 32 * 86400},
])
def test_bad_crash_probe_options_cannot_start_a_process(monkeypatch, kwargs):
    start = Mock(side_effect=AssertionError("must validate before creating processes"))
    monkeypatch.setattr(lab_send_crash_probe.multiprocessing, "get_context", start)
    with pytest.raises(ValueError):
        lab_send_crash_probe.probe(**kwargs)
    start.assert_not_called()
