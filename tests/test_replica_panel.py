import unittest

from model.features.replica_panel import (
    ReplicaCdKindSnapshot,
    ReplicaCdOverviewSnapshot,
    ReplicaPanelKindSnapshot,
    ReplicaPanelSnapshot,
    render_log_group_replica_cd_overview,
    render_log_group_replica_panel,
)


class ReplicaPanelRendererTests(unittest.TestCase):
    def test_summary_renderer_uses_ticket_and_requirement_states(self):
        snapshot = ReplicaPanelSnapshot(
            mode="summary",
            summary_rows=(
                ReplicaPanelKindSnapshot("virtual_hall", "虚天殿", "虚", True, ready=2),
                ReplicaPanelKindSnapshot("cangkun", "苍坤洞府", "苍", True, blocked=1),
                ReplicaPanelKindSnapshot("luoyun", "落云秘圃", "落", False),
            ),
        )

        text = render_log_group_replica_panel(snapshot)

        self.assertIn("虚天殿：可开 2", text)
        self.assertIn("苍坤洞府：不可开 1", text)
        self.assertIn("落云秘圃：无资格", text)
        self.assertTrue(text.endswith("操作：先点查询按钮看单本；可开则可点开本按钮"))

    def test_detail_renderer_keeps_read_only_snapshot_order(self):
        snapshot = ReplicaPanelSnapshot(
            mode="detail",
            room_line="房间：苍坤洞府 12｜待加入/进入",
            opener_line="苍坤洞府可开：2",
            preview_lines=("苍坤多队预览：可组 2 队",),
        )

        self.assertEqual(
            "房间：苍坤洞府 12｜待加入/进入\n"
            "苍坤洞府可开：2\n"
            "苍坤多队预览：可组 2 队\n"
            "操作：点按钮",
            render_log_group_replica_panel(snapshot),
        )

    def test_cd_renderer_limits_details_without_losing_totals(self):
        snapshot = ReplicaCdOverviewSnapshot(
            kinds=(
                ReplicaCdKindSnapshot("virtual_hall", "虚", ready=1, busy=2),
                ReplicaCdKindSnapshot("cangkun", "苍", ready=0, limited=1),
            ),
            detail_rows=(("@one", "虚30分钟"), ("@two", "虚1小时")),
        )

        text = render_log_group_replica_cd_overview(snapshot, max_rows=1)

        self.assertIn("可开：虚1｜苍0", text)
        self.assertIn("冷却/占用：虚2", text)
        self.assertIn("条件受限：苍1", text)
        self.assertIn("- @one｜虚30分钟", text)
        self.assertIn("- 另 1 个略", text)
        self.assertNotIn("@two", text)


if __name__ == "__main__":
    unittest.main()
