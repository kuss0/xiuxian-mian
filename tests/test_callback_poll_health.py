from unittest.mock import patch

import pytest

from tools import health_observer as observer


def fail(count, *, pid="123", detail="HTTP 502: temporary"):
    prefix = f"Oct 07 09:14:53 pve python[{pid}]: " if pid else ""
    return f"{prefix}log bot callback poll failed: {detail} | failures={count} retry=60s"


def recovered(*, pid="123"):
    prefix = f"Oct 07 09:16:08 pve python[{pid}]: " if pid else ""
    return f"{prefix}log bot callback poll recovered after 6 failures"


def scan(lines):
    with patch.object(observer, "run_command", return_value=(0, "\n".join(lines), "")):
        return observer.read_journal_matches("xiuxian.service", 600, 12)


@pytest.mark.parametrize("count", [5, 6, 99])
@pytest.mark.parametrize("pid", ["123", ""])
def test_sustained_poll_failure_warns_without_hard_error_or_raw_error(count, pid):
    result = scan([fail(count, pid=pid, detail="HTTP 502: private-token-example")])
    assert result["hard_count"] == 0
    assert result["warn_count"] == 1
    assert "log bot callback polling" in result["warn"][0]
    assert str(count) in result["warn"][0]
    assert "private-token-example" not in str(result)
    assert observer.classify_snapshot({}, [result])[0] == "warn"


@pytest.mark.parametrize("count", [1, 2, 3, 4])
def test_short_poll_failure_stays_transient(count):
    result = scan([fail(count)] * 6)
    assert result["warn_count"] == 0 and result["hard_count"] == 0


def test_repeated_observation_is_not_counted_as_more_failures():
    result = scan([fail(5)] * 8)
    assert result["warn_count"] == 1


@pytest.mark.parametrize("pid", ["123", ""])
def test_recovery_clears_warning_for_its_own_poller(pid):
    result = scan([fail(5, pid=pid), fail(6, pid=pid), recovered(pid=pid)])
    assert result["warn_count"] == 0 and result["hard_count"] == 0


def test_recovery_does_not_clear_other_process_evidence():
    result = scan([fail(6, pid="123"), recovered(pid="456")])
    assert result["warn_count"] == 1


def test_new_failure_streak_after_recovery_is_observed():
    assert scan([fail(6), recovered(), fail(5)])["warn_count"] == 1
    assert scan([fail(6), recovered(), fail(1)])["warn_count"] == 0


def test_invalid_zero_counter_does_not_clear_observed_failure():
    assert scan([fail(6), fail(0)])["warn_count"] == 1
    assert scan([fail(6), recovered().replace("after 6", "after 0")])["warn_count"] == 1


@pytest.mark.parametrize("line", [
    "log bot callback poll failed: HTTP 502",  # Old logs lack streak evidence.
    "log bot callback poll failed: HTTP 502 | failures=-5 retry=60s",
    "log bot callback poll failed: HTTP 502 | failures=5.0 retry=60s",
    "log bot callback poll failed: HTTP 502 | failures=5 retry=60s trailing text",
    "[2026-10-07 09:14:53] [player] " + fail(6, pid=""),
    "Oct 07 09:14:53 pve python[123]: [player] " + fail(6, pid=""),
    "quoted " + fail(6),
])
def test_untrusted_or_incomplete_copy_does_not_create_poll_alert(line):
    assert scan([line])["warn_count"] == 0


def test_embedded_recovery_cannot_clear_actual_failure():
    result = scan([fail(6), "Oct 07 09:16:08 pve python[123]: [player] " + recovered(pid="")])
    assert result["warn_count"] == 1


def test_other_errors_survive_callback_recovery():
    error = "Oct 07 09:16:10 pve python[123]: ERROR state write failed"
    result = scan([fail(6), recovered(), error])
    assert result["hard"] == [error] and result["hard_count"] == 1
    assert observer.classify_snapshot({}, [result])[0] == "error"


def test_observation_does_not_change_journal_limit_or_service_start_filter():
    with patch.object(observer, "run_command", return_value=(0, fail(5), "")) as run:
        observer.read_journal_matches("xiuxian.service", 600, 1,
                                      service_start_epoch=1791330000, max_lines=321)
    run.assert_called_once()
    assert "--lines=321" in run.call_args.args[0]
