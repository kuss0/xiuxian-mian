import unittest

from tools import health_observer


class HealthObserverTests(unittest.TestCase):
    def test_parse_systemctl_show_groups_multiple_services(self):
        output = (
            "Id=xiuxian.service\n"
            "ActiveState=active\n"
            "SubState=running\n"
            "MainPID=123\n"
            "\n"
            "Id=xiuxian-safety-watchdog.service\n"
            "ActiveState=active\n"
            "SubState=running\n"
            "MainPID=456\n"
        )

        parsed = health_observer.parse_systemctl_show(output)

        self.assertEqual("running", parsed["xiuxian.service"]["SubState"])
        self.assertEqual("456", parsed["xiuxian-safety-watchdog.service"]["MainPID"])

    def test_classify_snapshot_separates_warning_and_error(self):
        services = {
            "xiuxian.service": {"ActiveState": "active", "SubState": "running"},
            "xiuxian-safety-watchdog.service": {"ActiveState": "active", "SubState": "running"},
        }

        status, reasons = health_observer.classify_snapshot(
            services,
            [{"hard_count": 0, "warn_count": 2}],
        )

        self.assertEqual("warn", status)
        self.assertIn("journal warn matches: 2", reasons)

        status, reasons = health_observer.classify_snapshot(
            {"xiuxian.service": {"ActiveState": "inactive", "SubState": "dead"}},
            [{"hard_count": 0, "warn_count": 0}],
        )

        self.assertEqual("error", status)
        self.assertIn("xiuxian.service not running: inactive/dead", reasons)

    def test_warn_line_ignores_no_resend_context(self):
        self.assertFalse(health_observer.is_warn_journal_line("启动校验：查询灵树状态（无补发）。"))
        self.assertFalse(health_observer.is_warn_journal_line("状态确认，不补发。"))
        self.assertTrue(health_observer.is_warn_journal_line("野外历练回复超时，准备补发一次。"))

    def test_warn_line_ignores_quiz_business_timeout(self):
        self.assertFalse(
            health_observer.is_warn_journal_line(
                "🦴 <code>@vvlvdfr</code>｜题库内超时未作答｜题库匹配 B.鼎外乾蓝冰焰"
            )
        )


if __name__ == "__main__":
    unittest.main()
