import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

from model import state as state_module
from model.features import wanxin
from test_wanxin_lifecycle import HELPER, NOW, OWNER, env  # noqa: F401


def test_every_observed_strip_failure_is_a_failed_attempt():
    samples = json.loads((Path(__file__).parent / "fixtures/wanxin_strip_failures_20261121.json").read_text())
    assert len(samples) == 3
    for sample in samples:
        parsed = wanxin.parse_wanxin_text(sample["text"], now=NOW, family="wanxin_assist_strip")
        assert parsed["type"] == "assist_strip_failed"
        assert parsed["sha_cost"] == 120
        assert parsed["values"]["soul_seal"] == 4


def test_failed_accepted_contract_survives_restart_and_does_not_resend(env):  # noqa: F811
    observed = env.owner["wanxin_observation"]
    observed["commission"].update(
        id=1555, accepted=True, published_at=NOW - 1000, accepted_at=NOW - 900,
        helper_username="helper", owner_username="owner", strip_failed_at=NOW - 100,
        failed_strip_retry_at=NOW - 1000 + wanxin.WANXIN_COMMISSION_TTL_SEC + wanxin.CD_BUFFER_SEC,
    )
    observed["auto_config"].update(visit_enabled=False, protect_enabled=False, deduce_enabled=False, publish_enabled=True)
    env.owner["wanxin_observation"] = wanxin.normalize_wanxin_observation(json.loads(json.dumps(observed)))
    sender = AsyncMock()
    env.monkeypatch.setattr(wanxin, "_assist_identity_ready", lambda _observed: True)
    env.monkeypatch.setattr(wanxin, "send_game_command", sender)
    env.monkeypatch.setattr(wanxin, "send_owned_yinluo_command", sender)

    async def run():
        with state_module.use_identity(OWNER):
            for after in (0, 60, 8 * 3600, 20 * 3600):
                await wanxin.run_wanxin_scheduler(NOW + after)
            assert not await wanxin._send_assist_action(
                env.owner["wanxin_observation"], wanxin.WANXIN_ACTION_STRIP, NOW + 8 * 3600,
            )

    asyncio.run(run())
    sender.assert_not_awaited()
    commission = env.owner["wanxin_observation"]["commission"]
    assert commission["id"] == 1555 and commission["accepted"]
    assert commission["strip_failed_at"] == NOW - 100
    with state_module.use_identity(OWNER):
        assert wanxin._owner_needs_commission(env.owner["wanxin_observation"], commission["failed_strip_retry_at"])
    assert commission["id"] == 1555  # A deadline itself never closes the contract.


