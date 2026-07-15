from model.game_bot_registry import GameBotCandidateRegistry


def _registry(**overrides):
    options = {
        "ttl_sec": 300,
        "min_replies": 3,
        "min_players": 2,
        "min_commands": 2,
    }
    options.update(overrides)
    return GameBotCandidateRegistry(**options)


def test_duplicate_reply_edits_count_once():
    registry = _registry()

    first = registry.observe(
        9001,
        now=100,
        family="wild_training",
        player_id=101,
        command_label="野外历练",
        reply_to_msg_id=501,
    )
    second = registry.observe(
        9001,
        now=101,
        family="wild_training",
        player_id=101,
        command_label="野外历练",
        reply_to_msg_id=501,
    )

    assert first.should_notify is True
    assert second.should_notify is False
    assert registry.candidates[9001]["count"] == 1


def test_diverse_anchored_evidence_reaches_threshold():
    registry = _registry()

    registry.observe(
        9002,
        now=100,
        family="wild_training",
        player_id=101,
        command_label="野外历练",
        reply_to_msg_id=501,
    )
    registry.observe(
        9002,
        now=101,
        family="tower",
        player_id=102,
        command_label="闯塔",
        reply_to_msg_id=502,
    )
    decision = registry.observe(
        9002,
        now=102,
        family="wild_training",
        player_id=101,
        command_label="野外历练",
        reply_to_msg_id=503,
    )

    assert decision.ready_to_learn is True
    assert registry.evidence(9002, username="hantianzun31_bot") == {
        "username": "hantianzun31_bot",
        "reply_count": 3,
        "player_count": 2,
        "commands": ["野外历练", "闯塔"],
    }


def test_ttl_reset_drops_stale_evidence():
    registry = _registry(ttl_sec=10, min_replies=2, min_players=1, min_commands=1)
    registry.observe(
        9003,
        now=100,
        player_id=101,
        command_label="野外历练",
        reply_to_msg_id=501,
    )

    decision = registry.observe(
        9003,
        now=111,
        player_id=101,
        command_label="野外历练",
        reply_to_msg_id=502,
    )

    assert decision.should_notify is True
    assert decision.ready_to_learn is False
    assert registry.candidates[9003]["count"] == 1


def test_mark_decided_prevents_repeat_decision():
    registry = _registry(min_replies=1, min_players=1, min_commands=1)
    decision = registry.observe(
        9004,
        now=100,
        player_id=101,
        command_label="野外历练",
        reply_to_msg_id=501,
    )
    assert decision.ready_to_learn is True
    assert registry.mark_decided(9004, learned=True) is True

    repeated = registry.observe(
        9004,
        now=101,
        player_id=102,
        command_label="闯塔",
        reply_to_msg_id=502,
    )
    assert repeated.already_decided is True
    assert repeated.ready_to_learn is False

