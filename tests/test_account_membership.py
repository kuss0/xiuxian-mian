import unittest

from telethon import errors, types

from model.account_membership import (
    TargetGroupMembership,
    TargetGroupMembershipProbe,
    build_account_membership_gate,
    classify_membership_error,
    merge_account_membership_probe,
    probe_target_group_membership,
    resolve_account_identity_ids,
)


class _MembershipClient:
    def __init__(self, peer, result=None, error=None):
        self.peer = peer
        self.result = result
        self.error = error
        self.requests = []

    async def get_input_entity(self, _target):
        if isinstance(self.peer, BaseException):
            raise self.peer
        return self.peer

    async def __call__(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.result


class AccountMembershipClassificationTests(unittest.TestCase):
    def test_only_definitive_rpc_errors_block(self):
        for exc in (
            errors.UserNotParticipantError(request=None),
            errors.ChannelPrivateError(request=None),
            errors.ChannelInvalidError(request=None),
        ):
            probe = classify_membership_error(exc)
            self.assertEqual(TargetGroupMembership.NOT_MEMBER, probe.status)

    def test_transient_and_cache_errors_remain_unknown(self):
        for exc in (
            errors.RpcCallFailError(request=None),
            errors.FloodWaitError(request=None, capture=30),
            ValueError("Could not find the input entity"),
        ):
            probe = classify_membership_error(exc)
            self.assertEqual(TargetGroupMembership.UNKNOWN, probe.status)

    def test_mapping_includes_personal_and_channel_identities(self):
        identity_ids = resolve_account_identity_ids(
            301299112,
            {
                "8659059191": "301299112",
                7063348270: 301299112,
                9000000001: 999,
            },
            [301299112, 8659059191, 7063348270, 9000000001],
        )
        self.assertEqual((301299112, 7063348270, 8659059191), identity_ids)

    def test_unknown_probe_never_blocks_group_commands(self):
        gate = build_account_membership_gate(
            301299112,
            TargetGroupMembershipProbe(TargetGroupMembership.UNKNOWN, "telegram internal"),
            {8659059191: 301299112},
            [301299112, 8659059191],
        )
        self.assertFalse(gate.block_group_commands)
        self.assertEqual((301299112, 8659059191), gate.identity_ids)

    def test_not_member_probe_blocks_only_the_projected_account(self):
        gate = build_account_membership_gate(
            301299112,
            TargetGroupMembershipProbe(TargetGroupMembership.NOT_MEMBER, "not participant"),
            {8659059191: 301299112, 9000000001: 999},
            [301299112, 8659059191, 9000000001],
        )
        self.assertTrue(gate.block_group_commands)
        self.assertEqual((301299112, 8659059191), gate.identity_ids)

    def test_transient_probe_preserves_known_negative_for_same_group(self):
        previous = {
            "account_id": 301299112,
            "game_group_id": -1002083016447,
            "status": "not_member",
            "reason": "USER_NOT_PARTICIPANT",
            "last_definitive_at": 100.0,
        }
        record = merge_account_membership_probe(
            previous,
            TargetGroupMembershipProbe(
                TargetGroupMembership.UNKNOWN,
                "telegram internal",
                "RPCCALLFAILERROR",
            ),
            account_id=301299112,
            identity_ids=[301299112, 8659059191],
            game_group_id=-1002083016447,
            now=200.0,
        )
        self.assertEqual("not_member", record["status"])
        self.assertEqual("unknown", record["probe_status"])
        self.assertEqual("USER_NOT_PARTICIPANT", record["reason"])
        self.assertEqual("telegram internal", record["last_error"])
        self.assertEqual(100.0, record["last_definitive_at"])

    def test_group_change_does_not_carry_old_negative_into_unknown(self):
        record = merge_account_membership_probe(
            {
                "account_id": 301299112,
                "game_group_id": -1001,
                "status": "not_member",
                "reason": "old group",
            },
            TargetGroupMembershipProbe(TargetGroupMembership.UNKNOWN, "cache miss"),
            account_id=301299112,
            identity_ids=[301299112],
            game_group_id=-1002,
            now=200.0,
        )
        self.assertEqual("unknown", record["status"])
        self.assertEqual("cache miss", record["last_error"])


class AccountMembershipProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_channel_success_is_member(self):
        client = _MembershipClient(types.InputPeerChannel(123, 456), result=object())
        probe = await probe_target_group_membership(client, -100123)
        self.assertEqual(TargetGroupMembership.MEMBER, probe.status)
        self.assertEqual(1, len(client.requests))

    async def test_channel_definitive_negative_is_not_member(self):
        client = _MembershipClient(
            types.InputPeerChannel(123, 456),
            error=errors.UserNotParticipantError(request=None),
        )
        probe = await probe_target_group_membership(client, -100123)
        self.assertEqual(TargetGroupMembership.NOT_MEMBER, probe.status)

    async def test_channel_transient_failure_is_unknown(self):
        client = _MembershipClient(
            types.InputPeerChannel(123, 456),
            error=errors.RpcCallFailError(request=None),
        )
        probe = await probe_target_group_membership(client, -100123)
        self.assertEqual(TargetGroupMembership.UNKNOWN, probe.status)

    async def test_legacy_chat_is_not_applicable_without_rpc(self):
        client = _MembershipClient(types.InputPeerChat(123))
        probe = await probe_target_group_membership(client, -123)
        self.assertEqual(TargetGroupMembership.NOT_APPLICABLE, probe.status)
        self.assertEqual([], client.requests)

    async def test_entity_resolution_failure_is_unknown(self):
        client = _MembershipClient(ValueError("entity cache miss"))
        probe = await probe_target_group_membership(client, -100123)
        self.assertEqual(TargetGroupMembership.UNKNOWN, probe.status)


if __name__ == "__main__":
    unittest.main()
