"""A Qingxi run awaiting review owns the dwelling scene, not group text activities.

On 2026-11-17/18 two unreadable browser replies held both Qingxi accounts for
more than a day, and that hold stopped the whole five-account weekly roster.
"""
import pytest

from model import activity_occupancy, app_hunt, app_replica, app_weekly_adventures, app_xuangu, app_zhengmo
from model import avatar_soul, config as model_config
from model.features import changfeng_trial, paired_activities, qingxi_quests

IDENTITY = 99
REVIEW = [{"gameplay_uncertain": True}, {"pending": {"operationId": "old"}}, {"gather": {"gatherId": "old"}}]
BLOCKED = "青溪坊任务执行中或结果待核对"


def occupy(monkeypatch, record):
    state = {"qingxi_quests": {str(IDENTITY): record}}
    monkeypatch.setattr(activity_occupancy, "get_expedition_run_state", lambda: state)
    monkeypatch.setattr(activity_occupancy, "get_hunt_run_state", lambda: {})
    monkeypatch.setattr(activity_occupancy, "get_replica_run_state", lambda: {})
    return state


def text_activity_reasons(monkeypatch, state):
    """Ask every group text activity whether the identity may take part."""
    client, profile = object(), {"username": "member", "realm": "结丹初期"}
    for module in (app_hunt, app_weekly_adventures, app_zhengmo, paired_activities):
        monkeypatch.setattr(module, "get_registered_client", lambda _account: client)
    monkeypatch.setattr(model_config, "get_registered_client", lambda _account: client)
    for module in (app_hunt, app_xuangu, app_weekly_adventures, app_zhengmo, paired_activities, app_replica):
        for name, value in (("get_identity_ids", lambda: [IDENTITY]), ("get_identity_account", lambda _i: IDENTITY),
                            ("get_identity_enabled", lambda _i: True), ("get_send_as_profile", lambda _i: profile),
                            ("get_global_enabled", lambda: True)):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, value)
    monkeypatch.setattr(app_zhengmo, "get_avatar_owner_id", lambda _i: 0)
    monkeypatch.setattr(app_weekly_adventures, "_team_config", lambda name="": {"participant_identity_ids": [IDENTITY]})
    monkeypatch.setattr(paired_activities, "get_expedition_run_state", lambda: state)
    monkeypatch.setattr(app_replica, "is_dungeon_quiet_active", lambda _now: False)
    return {
        "hunt": app_hunt._members([IDENTITY])[1],
        "xuangu": app_xuangu._preflight_error(IDENTITY),
        "weekly": "" if app_weekly_adventures._available(IDENTITY) else "unavailable",
        "zhengmo": app_zhengmo._members([IDENTITY])[1],
        "paired": "" if paired_activities.available(IDENTITY) else "unavailable",
        # The replica check continues into unrelated account checks afterwards.
        "replica": BLOCKED if app_replica._get_replica_identity_block_reason(
            IDENTITY, now=1_800_000_000.0) == BLOCKED else "",
    }


@pytest.mark.parametrize("review", REVIEW)
def test_review_hold_is_scoped_by_the_caller(monkeypatch, review):
    occupy(monkeypatch, {"phase": "held", **review})
    assert activity_occupancy.get_activity_block_reason(IDENTITY) == BLOCKED
    assert not activity_occupancy.get_activity_block_reason(IDENTITY, include_qingxi_review=False)
    assert not activity_occupancy.get_activity_block_reason(IDENTITY, include_qingxi=False)


@pytest.mark.parametrize("phase", ["loading", "running"])
def test_live_run_still_owns_the_account_everywhere(monkeypatch, phase):
    occupy(monkeypatch, {"phase": phase, "gameplay_uncertain": True})
    assert activity_occupancy.get_activity_block_reason(IDENTITY, include_qingxi_review=False) == BLOCKED
    assert not activity_occupancy.get_activity_block_reason(IDENTITY, include_qingxi=False)


@pytest.mark.parametrize("review", REVIEW)
def test_group_text_activities_ignore_a_run_awaiting_review(monkeypatch, review):
    state = occupy(monkeypatch, {"phase": "held", **review})
    assert text_activity_reasons(monkeypatch, state) == dict.fromkeys(
        ("hunt", "xuangu", "weekly", "zhengmo", "paired", "replica"), "")


def test_group_text_activities_wait_for_a_live_run(monkeypatch):
    state = occupy(monkeypatch, {"phase": "running", "gameplay_uncertain": True})
    reasons = text_activity_reasons(monkeypatch, state)
    assert all(reasons.values()), reasons
    assert all(BLOCKED in reasons[key] for key in ("hunt", "xuangu", "zhengmo", "replica"))


@pytest.mark.parametrize("review", REVIEW)
def test_dwelling_scene_activities_keep_the_review_hold(monkeypatch, review):
    occupy(monkeypatch, {"phase": "held", **review})
    # Changfeng shares the 3D scene and its flight state with Qingxi quests.
    monkeypatch.setattr(changfeng_trial, "get_expedition_config", lambda: {"changfeng": {
        "enabled": True, "participant_identity_ids": [IDENTITY]}})
    monkeypatch.setattr(changfeng_trial.MiniAppIdentityOwner, "capture", classmethod(lambda cls, _i: object()))
    monkeypatch.setattr(changfeng_trial, "is_cave_public_identity_available", lambda _i: True)
    monkeypatch.setattr(changfeng_trial, "get_global_enabled", lambda: True)
    import asyncio
    assert asyncio.run(changfeng_trial.run_changfeng_once(IDENTITY)) == (False, "身份正在参加其他活动")
    # A new quest run and a soul switch must also wait for the review.
    assert activity_occupancy.get_activity_block_reason(IDENTITY) == BLOCKED
    monkeypatch.setattr(avatar_soul, "get_identity_ids", lambda: [IDENTITY])
    monkeypatch.setattr(avatar_soul, "get_avatar_owner_id", lambda _i: 0)
    monkeypatch.setattr(avatar_soul, "recovery_busy", lambda _i: False)
    assert avatar_soul.switch_block(IDENTITY) == BLOCKED
    assert qingxi_quests.get_activity_block_reason is activity_occupancy.get_activity_block_reason
