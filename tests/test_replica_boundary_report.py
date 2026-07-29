import unittest
from pathlib import Path

from tools import replica_boundary_report


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ReplicaBoundaryReportTests(unittest.TestCase):
    def test_live_replica_report_keeps_expected_anchors_and_runtime_surface(self):
        report = replica_boundary_report.build_report(PROJECT_ROOT)

        self.assertGreater(report["line_count"], 14000)
        self.assertGreater(report["function_count"], 600)
        self.assertEqual(
            [segment for segment, _anchor in replica_boundary_report.SEGMENT_ANCHORS],
            [row["segment"] for row in report["segments"]],
        )
        runtime_names = {row["name"] for row in report["runtime_import_surface"]}
        self.assertIn("_handle_replica_group_command", runtime_names)
        self.assertIn("_handle_replica_progress_event", runtime_names)
        self.assertIn("handle_replica_button_callback", runtime_names)
        self.assertTrue(report["boundary_edges"])


if __name__ == "__main__":
    unittest.main()
