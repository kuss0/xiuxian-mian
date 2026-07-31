import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from model import app


class AppPhasefulRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_phaseful_broadcast_result_short_circuits_owner_routing(self):
        async def phaseful_handler(*args, **kwargs):
            return True

        original_handlers = set(app._PHASEFUL_SUMMARY_REPLY_CONTEXT_HANDLERS)
        app._PHASEFUL_SUMMARY_REPLY_CONTEXT_HANDLERS.add(phaseful_handler)
        try:
            with patch.object(app, "_claim_runtime_event", return_value=True):
                handled = await app._dispatch_broadcast_handlers(
                    SimpleNamespace(id=1),
                    "【元婴闭关结算】",
                    1.0,
                    (("yuanying_summary", phaseful_handler),),
                    reply_to=SimpleNamespace(id=2, raw_text=".天机代卜"),
                    reply_context={"family": "concubine_tianji", "send_as_id": 3},
                )
        finally:
            app._PHASEFUL_SUMMARY_REPLY_CONTEXT_HANDLERS.clear()
            app._PHASEFUL_SUMMARY_REPLY_CONTEXT_HANDLERS.update(original_handlers)

        self.assertTrue(handled)

    async def test_non_phaseful_broadcast_result_does_not_short_circuit_owner_routing(self):
        handler = AsyncMock(return_value=True)
        with patch.object(app, "_claim_runtime_event", return_value=True):
            handled = await app._dispatch_broadcast_handlers(
                SimpleNamespace(id=1),
                "普通通告",
                1.0,
                (("other", handler),),
            )

        self.assertFalse(handled)
        handler.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
