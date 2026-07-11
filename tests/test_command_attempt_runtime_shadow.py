import asyncio
import copy
import functools
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from model import runtime
from model import state as state_module
from model.command_attempt import TransportState, list_open_attempts, list_transitions
from model.command_attempt import runtime_shadow


@pytest.fixture
def shadow_identity():
    snapshot = copy.deepcopy(state_module._meta_state)
    identity_id = 9002001
    state_module.ensure_identity_registered(identity_id)
    state_module.set_identity_account(identity_id, identity_id)
    yield identity_id
    state_module._meta_state.clear()
    state_module._meta_state.update(snapshot)


def _attempts(identity_id, command=None):
    attempts = list_open_attempts(send_as_id=identity_id, limit=100)
    if command is None:
        return attempts
    return [item for item in attempts if item.command == command]


def async_test(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return asyncio.run(func(*args, **kwargs))

    return wrapper


@async_test
async def test_flag_off_preserves_legacy_result_without_attempt_rows(shadow_identity):
    result = SimpleNamespace(id=77)
    with (
        patch.dict("os.environ", {"XIUXIAN_ATTEMPT_SHADOW_WRITE": "0"}),
        patch.object(runtime, "_send_game_command_impl", new=AsyncMock(return_value=result)) as legacy,
    ):
        actual = await runtime.send_game_command(
            ".测试影子关闭",
            send_as_id=shadow_identity,
            source_module="shadow_test",
            op_id="legacy:must-not-change",
        )

    assert actual is result
    legacy.assert_awaited_once()
    assert _attempts(shadow_identity, ".测试影子关闭") == []


@async_test
async def test_pre_send_block_records_definitely_unsent_without_changing_result(shadow_identity):
    async def blocked_impl(command, **kwargs):
        runtime._record_game_send_block(
            kwargs["send_as_id"],
            command,
            "global_disabled",
            "全局暂停",
        )
        return None

    with (
        patch.dict("os.environ", {"XIUXIAN_ATTEMPT_SHADOW_WRITE": "1"}),
        patch.object(runtime, "_send_game_command_impl", new=blocked_impl),
    ):
        actual = await runtime.send_game_command(
            ".测试影子拦截",
            send_as_id=shadow_identity,
            source_module="shadow_test",
        )

    assert actual is None
    attempts = _attempts(shadow_identity, ".测试影子拦截")
    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt.transport is TransportState.BLOCKED
    assert attempt.block_code == "global_disabled"
    assert attempt.definitely_unsent is True
    assert [item.to_state for item in list_transitions(attempt.op_id) if item.axis == "transport"] == [
        "created",
        "blocked",
    ]


@async_test
async def test_queued_send_timeout_records_unknown(shadow_identity):
    async def timeout_impl(command, **kwargs):
        runtime_shadow.note_queued()
        runtime._record_game_send_block(
            kwargs["send_as_id"],
            command,
            "send_timeout",
            ">60s",
        )
        return None

    with (
        patch.dict("os.environ", {"XIUXIAN_ATTEMPT_SHADOW_WRITE": "1"}),
        patch.object(runtime, "_send_game_command_impl", new=timeout_impl),
    ):
        actual = await runtime.send_game_command(
            ".测试影子超时",
            send_as_id=shadow_identity,
            source_module="shadow_test",
        )

    assert actual is None
    attempt = _attempts(shadow_identity, ".测试影子超时")[0]
    assert attempt.transport is TransportState.SEND_UNKNOWN
    assert attempt.block_code == "send_timeout"
    assert attempt.definitely_unsent is False


@async_test
async def test_success_records_sent_but_keeps_legacy_op_id_metadata_only(shadow_identity):
    result = SimpleNamespace(id=88001)

    async def sent_impl(*args, **kwargs):
        runtime_shadow.note_queued()
        runtime_shadow.note_sent(result.id, sent_at=1_700_000_000.0)
        return result

    with (
        patch.dict("os.environ", {"XIUXIAN_ATTEMPT_SHADOW_WRITE": "1"}),
        patch.object(runtime, "_send_game_command_impl", new=sent_impl),
    ):
        actual = await runtime.send_game_command(
            ".测试影子成功",
            send_as_id=shadow_identity,
            source_module="shadow_test",
            op_id="legacy:unchanged",
            chain_id="legacy-chain",
        )

    assert actual is result
    attempt = _attempts(shadow_identity, ".测试影子成功")[0]
    assert attempt.transport is TransportState.SENT
    assert attempt.root_msg_id == result.id
    assert attempt.op_id != "legacy:unchanged"
    assert attempt.meta["legacy_op_id"] == "legacy:unchanged"
    assert attempt.chain_id == "legacy-chain"


@async_test
async def test_legacy_fallback_identity_is_not_shadowed(shadow_identity):
    result = SimpleNamespace(id=91)
    with (
        patch.dict("os.environ", {"XIUXIAN_ATTEMPT_SHADOW_WRITE": "1"}),
        patch.object(runtime, "get_current_identity_id", return_value=shadow_identity),
        patch.object(runtime, "_send_game_command_impl", new=AsyncMock(return_value=result)),
    ):
        actual = await runtime.send_game_command(".测试旧回退")

    assert actual is result
    assert _attempts(shadow_identity, ".测试旧回退") == []


@async_test
async def test_concurrent_shadow_scopes_do_not_cross(shadow_identity):
    second_id = shadow_identity + 1
    state_module.ensure_identity_registered(second_id)
    state_module.set_identity_account(second_id, second_id)
    release = asyncio.Event()
    entered = 0

    async def sent_impl(*args, **kwargs):
        nonlocal entered
        entered += 1
        if entered == 2:
            release.set()
        await release.wait()
        runtime_shadow.note_queued()
        msg_id = int(kwargs["send_as_id"]) + 100
        runtime_shadow.note_sent(msg_id, sent_at=1_700_000_000.0)
        return SimpleNamespace(id=msg_id)

    with (
        patch.dict("os.environ", {"XIUXIAN_ATTEMPT_SHADOW_WRITE": "1"}),
        patch.object(runtime, "_send_game_command_impl", new=sent_impl),
    ):
        first, second = await asyncio.gather(
            runtime.send_game_command(".并发一", send_as_id=shadow_identity, source_module="shadow_test"),
            runtime.send_game_command(".并发二", send_as_id=second_id, source_module="shadow_test"),
        )

    assert first.id == shadow_identity + 100
    assert second.id == second_id + 100
    assert _attempts(shadow_identity, ".并发一")[0].root_msg_id == first.id
    assert _attempts(second_id, ".并发二")[0].root_msg_id == second.id


@async_test
async def test_shadow_write_failure_does_not_change_legacy_result(shadow_identity, caplog):
    result = SimpleNamespace(id=92)
    with (
        patch.dict("os.environ", {"XIUXIAN_ATTEMPT_SHADOW_WRITE": "1"}),
        patch.object(runtime_shadow, "create_attempt", side_effect=RuntimeError("ledger unavailable")),
        patch.object(runtime, "_send_game_command_impl", new=AsyncMock(return_value=result)),
    ):
        actual = await runtime.send_game_command(".影子失败", send_as_id=shadow_identity)

    assert actual is result
    assert "ledger unavailable" in caplog.text
