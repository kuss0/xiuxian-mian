from unittest.mock import patch

import pytest

from tools import safety_watchdog as watchdog


def config():
    return watchdog.build_config(watchdog.parse_args(["--project-root", "/tmp/watchdog-test", "--dry-run"]))


def event(at, number, **extra):
    return {
        "_epoch": at, "event_type": "sent", "sender_id": number % 3 + 1,
        "message_id": number + 100, "text": f".command{number} private-argument",
        "family": "concubine_status", "source_module": "concubine_status", **extra,
    }


@pytest.mark.parametrize("seconds,limit", [(120, 8), (300, 18), (900, 38)])
def test_details_use_same_window_and_exemptions_as_counter(seconds, limit):
    cfg, now = config(), 10000
    events = [event(now - seconds + n + 1, n) for n in range(limit)]
    events += [event(now - seconds, 1000), event(now - seconds - 1, 1001)]
    events += [event(now, 1002, text=".加入副本")]
    events += [event(now, 1003, event_type="message"), event(now, 1004, event_type="edit")]
    reason = f"send burst: {limit}+ sends in {seconds}s"
    details = "\n".join(watchdog.send_burst_details(events, now, cfg, reason))
    sent = [row for row in events if row["event_type"] == "sent"]
    assert watchdog.count_non_burst_exempt_since(sent, now, seconds) == limit + 1
    assert f"窗口 {seconds}s" in details
    assert f"计数 {limit + 1}｜豁免 1｜身份 3" in details
    assert "private-argument" not in details
    assert f"最近 5/{limit + 1} 条" in details
    assert len([line for line in details.splitlines() if line.startswith("- ")]) == 5
    assert "msg=1101" not in details
    assert "msg=1103" not in details


def test_marked_heart_choice_is_exempt_in_evidence_too():
    now, cfg = 10000, config()
    events = [event(now - 100 + n * 14, n) for n in range(8)]
    chain = "concubine_heart_choice:42:123:round1"
    events.append(event(now, 20, sender_id=42, text=".稳", family="concubine_heart",
                        source_module="共历心劫", priority="chain", reply_to_msg_id=123,
                        chain_id=chain, op_id=f"{chain}:try0:.稳"))
    reason = watchdog.find_send_breach(events, now, cfg)
    assert reason == "send burst: 8+ sends in 120s"
    details = "\n".join(watchdog.send_burst_details(events, now, cfg, reason))
    assert "计数 8｜豁免 1｜身份 3" in details
    assert "msg=120" not in details
    assert not watchdog.should_fuse_breach(reason, watchdog.BreachConfirmationState(), now)


def test_current_heart_uuid_markers_do_not_silently_gain_exemption():
    now, cfg = 10000, config()
    events = [event(now - 116 + n * 16, n) for n in range(7)]
    events.append(event(now, 10, text=".稳", family="concubine_heart",
                        source_module="concubine_heart", priority="chain", reply_to_msg_id=123,
                        chain_id="a" * 32, op_id="b" * 32))
    reason = watchdog.find_send_breach(events, now, cfg)
    assert reason == "send burst: 8+ sends in 120s"
    assert "计数 8｜豁免 0" in "\n".join(watchdog.send_burst_details(events, now, cfg, reason))


def test_check_once_captures_reset_filtered_snapshot_without_rereading():
    cfg, now = config(), 10000
    rows = [event(now - 100 + n * 14, n) for n in range(8)]
    rows.insert(0, event(now - 110, 9000))
    details = ["stale previous warning"]
    with patch.object(watchdog.time, "time", return_value=now), \
         patch.object(watchdog, "find_legacy_xiuxian_processes", return_value=[]), \
         patch.object(watchdog, "read_recent_log_lines", return_value=rows) as read, \
         patch.object(watchdog, "get_reset_after_epoch", return_value=now - 105):
        reason = watchdog.check_once(cfg, warning_details=details)
    read.assert_called_once()
    assert reason == "send burst: 8+ sends in 120s"
    assert "计数 8｜豁免 0" in "\n".join(details)
    assert "stale previous warning" not in details
    assert "msg=9100" not in "\n".join(details)


def test_non_burst_check_clears_previous_details():
    details = ["stale previous warning"]
    with patch.object(watchdog, "find_legacy_xiuxian_processes",
                      return_value=[{"pid": 123, "cmdline": "legacy"}]):
        assert watchdog.check_once(config(), warning_details=details).startswith("legacy xiuxian process:")
    assert details == []
    assert watchdog.send_burst_details([], 1000, config(), "global lock breach: gap 1s") == []


def test_message_is_bounded_escaped_and_omits_arguments():
    cfg, now = config(), 10000
    events = [event(now - n, n, sender_id="<" * 200, source_module="<&>\n" * 200,
                    text=".<&> secret-token", message_id="<" * 200) for n in range(100)]
    details = watchdog.send_burst_details(events, now, cfg, "send burst: 8+ sends in 120s")
    message = watchdog.format_warning_message("send burst: 8+ sends in 120s", env={}, details=details)
    assert "secret-token" not in message
    assert "<" not in message
    assert "&lt;" in message
    assert len(message) < 4096


def test_warning_dry_run_flushes_but_never_notifies_or_fuses():
    with patch("builtins.print") as output, \
         patch.object(watchdog, "send_log_via_bot") as notify, \
         patch.object(watchdog, "disable_global_switch") as disable, \
         patch.object(watchdog, "write_fuse_marker") as fuse:
        watchdog.perform_warning(config(), {}, "send burst: 8+ sends in 120s", details=["test evidence"])
    output.assert_called_once()
    assert output.call_args.kwargs == {"flush": True}
    assert "test evidence" in output.call_args.args[0]
    assert "action: warn dry-run" in output.call_args.args[0]
    notify.assert_not_called()
    disable.assert_not_called()
    fuse.assert_not_called()


def test_main_once_passes_the_check_snapshot_to_warning():
    def check(_cfg, *, warning_details):
        warning_details.extend(["original sample"])
        return "send burst: 8+ sends in 120s"

    with patch.object(watchdog, "load_dotenv", return_value={}), \
         patch.object(watchdog, "check_once", side_effect=check), \
         patch.object(watchdog, "find_journal_breach", return_value=""), \
         patch.object(watchdog, "perform_warning") as warn, \
         patch.object(watchdog, "perform_fuse") as fuse:
        assert watchdog.main(["--once", "--dry-run"]) == 0
    assert warn.call_args.kwargs == {"details": ["original sample"]}
    fuse.assert_not_called()


def test_warning_loop_keeps_five_minute_throttle_and_current_evidence():
    samples = iter(["first", "throttled", "third"])

    def check(_cfg, *, warning_details):
        warning_details.append(next(samples))
        return "send burst: 8+ sends in 120s"

    with patch.object(watchdog, "load_dotenv", return_value={}), \
         patch.object(watchdog, "check_once", side_effect=check), \
         patch.object(watchdog.time, "time", side_effect=[10000, 10015, 10300]), \
         patch.object(watchdog.time, "sleep", side_effect=[None, None, KeyboardInterrupt]), \
         patch.object(watchdog, "perform_warning") as warn, \
         patch.object(watchdog, "perform_fuse") as fuse:
        with pytest.raises(KeyboardInterrupt):
            watchdog.main(["--dry-run"])
    assert [call.kwargs["details"] for call in warn.call_args_list] == [["first"], ["third"]]
    fuse.assert_not_called()


def test_live_warning_notifies_once_without_disabling_service():
    cfg = config()
    cfg.dry_run = False
    with patch("builtins.print") as output, \
         patch.object(watchdog, "send_log_via_bot", return_value="ok") as notify, \
         patch.object(watchdog, "perform_fuse") as fuse, \
         patch.object(watchdog, "disable_global_switch") as disable:
        watchdog.perform_warning(cfg, {}, "send burst: 8+ sends in 120s", details=["sample <tag>"])
    notify.assert_called_once()
    assert "sample &lt;tag&gt;" in notify.call_args.args[1]
    assert all(call.kwargs == {"flush": True} for call in output.call_args_list)
    fuse.assert_not_called()
    disable.assert_not_called()
