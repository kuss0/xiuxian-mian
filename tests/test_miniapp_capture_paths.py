from pathlib import Path

from model.config import STATE_DIR
from model.features import cave_treasure_runtime, fishing_runtime, stargazer, tree_runtime, trial_runtime


def test_runtime_capture_dirs_follow_configured_state_dir():
    expected = Path(STATE_DIR) / "miniapp_capture"
    assert cave_treasure_runtime.CAVE_TREASURE_MINIAPP_CAPTURE_DIR == expected
    assert fishing_runtime.FISHING_MINIAPP_CAPTURE_DIR == expected
    assert stargazer.STARGAZER_MINIAPP_CAPTURE_DIR == expected
    assert tree_runtime.TREE_MINIAPP_CAPTURE_DIR == expected
    assert trial_runtime.TRIAL_MINIAPP_CAPTURE_DIR == expected


def test_capture_store_uses_isolated_test_state_dir():
    store = trial_runtime._trial_miniapp_capture_store(1_700_000_000.0)
    assert store.path == Path(STATE_DIR) / "miniapp_capture" / "trial-2023-11-15.jsonl"
