import asyncio
import copy
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from model import app, runtime, ui
from model import state as state_module


@pytest.mark.parametrize("loaded,with_timers,recovery", [
    (False, False, "首次初始化"),
    (True, False, "无待恢复任务"),
    (True, True, "成功"),
])
@pytest.mark.parametrize("issue", ["none", "closed_module", "account_failure", "banned"])
@pytest.mark.parametrize("logged_in", [True, False])
def test_bootstrap_compact_notice_preserves_diagnostics(loaded, with_timers, recovery, issue, logged_in):
    asyncio.run(_check_bootstrap_notice(loaded, with_timers, recovery, issue, logged_in))


async def _check_bootstrap_notice(loaded, with_timers, recovery, issue, logged_in):
    snapshot = copy.deepcopy(state_module._meta_state)
    identities = list(range(990701, 990725))
    audits = []
    try:
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
        state_module.set_global_enabled(True)
        state_module.state["my_user_id"] = identities[0] if logged_in else 0
        for identity_id in identities:
            state_module.ensure_identity_registered(identity_id)
            state_module.update_send_as_profile(identity_id, username=f"fixture{identity_id}")
        if with_timers:
            state_module.get_identity_state(identities[0])["next_pet_time"] = 1700000000.0
        client = SimpleNamespace(connect=AsyncMock(), is_connected=Mock(return_value=False))
        issue_lines = {
            "none": [], "closed_module": [],
            "account_failure": ["账号启动失败：fixture"],
            "banned": ["账号已被封禁，请手动处理"],
        }[issue]
        with ExitStack() as stack:
            for name in ("resume_audit_summary", "start_ui_server",
                         "run_account_target_membership_probe_scheduler", "discover_cave_public_entry_from_history"):
                stack.enter_context(patch.object(app, name, new=AsyncMock()))
            for name, value in {
                "load_state": loaded, "has_persisted_identity_rows": False,
                "get_accounts": {}, "get_all_clients": {}, "get_identity_account": 0,
                "clear_expired_dungeon_quiet": None, "_cleanup_replica_run_state": None,
                "restore_guanxing_round_runtime": ({}, False),
                "run_startup_account_integrity_check": {"audit_lines": issue_lines},
                "scan_startup_timeout_tasks": {"closed_count": int(issue == "closed_module"), "alerts": []},
                "initialize_identity_runtime": None, "clear_transient_send_failures_for_global_recovery": 0,
                "spread_overdue_runtime_timers": 0, "recover_divination_startup_timeouts": None,
                "save_state": True,
            }.items():
                stack.enter_context(patch.object(app, name, return_value=value))
            stack.enter_context(patch.object(app, "client", client))
            stack.enter_context(patch.object(app, "_fire_and_forget", side_effect=lambda coro: audits.append(coro)))
            audit = stack.enter_context(patch.object(app, "send_audit_log", new=AsyncMock()))
            console = stack.enter_context(patch.object(app, "console_log"))
            await app.bootstrap()
            for coro in audits:
                await coro
            audits.clear()

        audit.assert_awaited_once()
        message = audit.await_args.args[0]
        assert f"身份：24｜恢复：{recovery}" in message
        assert "fixture990701" not in message
        assert "身份列表" not in message
        assert "SQLite" not in message
        assert ("未登录（等待 UI 登录）" in message) == (not logged_in)
        assert all(line in message for line in issue_lines)
        closed = loaded and issue == "closed_module"
        assert ("自动关闭 1 个模块" in message) == closed
        priority = runtime._resolve_audit_priority(message, audit.await_args.kwargs.get("priority"))
        expected = "high" if issue == "banned" else "medium" if closed or issue == "account_failure" else "low"
        assert priority == expected
        if expected == "low":
            assert len(message.splitlines()) == 2
            assert len(message.encode("utf-16-le")) // 2 < 100
        roster = next(call.args[0] for call in console.call_args_list if "启动身份列表" in call.args[0])
        assert all(str(identity_id) in roster for identity_id in identities)
    finally:
        for coro in audits:
            coro.close()
        state_module._meta_state.clear()
        state_module._meta_state.update(snapshot)


def test_ui_bind_is_local_and_server_start_remains_idempotent(monkeypatch):
    server = SimpleNamespace(sockets=[SimpleNamespace(getsockname=lambda: ("127.0.0.1", 3030))])
    start = AsyncMock(return_value=server)
    audit = AsyncMock()
    console = Mock()
    monkeypatch.setattr(ui, "_ui_server", None)
    monkeypatch.setattr(ui, "_ui_stopping", False)
    monkeypatch.setattr(ui.asyncio, "start_server", start)
    monkeypatch.setattr(ui, "send_audit_log", audit)
    monkeypatch.setattr(ui, "console_log", console)
    async def start_twice():
        assert await ui.start_ui_server() is server
        assert await ui.start_ui_server() is server

    asyncio.run(start_twice())
    start.assert_awaited_once_with(ui.handle_ui_http, ui.UI_HOST, ui.UI_PORT, limit=ui.UI_HTTP_MAX_HEADER_BYTES + 1)
    audit.assert_not_awaited()
    console.assert_called_once()
    assert "127.0.0.1" in console.call_args.args[0]


def test_ui_bind_failure_does_not_report_success(monkeypatch):
    console = Mock()
    monkeypatch.setattr(ui, "_ui_server", None)
    monkeypatch.setattr(ui, "_ui_stopping", False)
    monkeypatch.setattr(ui.asyncio, "start_server", AsyncMock(side_effect=OSError("address in use")))
    monkeypatch.setattr(ui, "console_log", console)
    with pytest.raises(OSError, match="address in use"):
        asyncio.run(ui.start_ui_server())
    assert ui._ui_server is None
    console.assert_not_called()
