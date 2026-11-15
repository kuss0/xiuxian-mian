import asyncio
from unittest.mock import AsyncMock

import pytest

from model import runtime


@pytest.mark.parametrize("module", ["组队周常", "玄骨遗府", "出海猎妖"])
@pytest.mark.parametrize("jitter", ["min", "max"])
def test_party_actions_obey_global_floor_across_accounts_and_fit_45_second_round(monkeypatch, module, jitter):
    async def run():
        clock = [100.0]
        async def sleep(seconds):
            clock[0] += seconds
        monkeypatch.setattr(runtime.time, "monotonic", lambda: clock[0])
        monkeypatch.setattr(runtime.asyncio, "sleep", sleep)
        monkeypatch.setattr(runtime.random, "uniform", lambda low, high: low if jitter == "min" else high)
        monkeypatch.setattr(runtime, "_GAME_SEND_LOCK", asyncio.Lock())
        monkeypatch.setattr(runtime, "_GAME_SEND_QUEUE_ITEMS", {})
        monkeypatch.setattr(runtime, "_GAME_LAST_SEND_AT", 100.)  # A preceding command from another module.
        monkeypatch.setattr(runtime, "_MODULE_LAST_SEND_AT", {})
        monkeypatch.setattr(runtime, "_IDENTITY_LAST_SEND_AT", {})
        monkeypatch.setattr(runtime, "_wait_for_game_send_tasks", AsyncMock(return_value=True))
        monkeypatch.setattr(runtime, "_global_recovery_throttle_active", lambda _: False)
        monkeypatch.setattr(runtime, "_has_ordered_recovery_queue_items", lambda: False)
        sent = []
        for identity in range(1, 6):
            async with runtime._send_slot(runtime.SEND_PRIORITY_URGENT_REACTIVE, command=".准", send_as_id=identity,
                                          intent={"source_module": module}):
                sent.append(clock[0])
        assert all(6 <= b - a <= 8 for a, b in zip([100.] + sent, sent))
        assert sent[-1] - 100 < 42  # 45s panel, retaining the existing 3s deadline margin.
    asyncio.run(run())
