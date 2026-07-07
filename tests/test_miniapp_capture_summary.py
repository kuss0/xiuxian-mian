import json

from model.miniapp_capture_summary import format_miniapp_capture_summary, get_miniapp_capture_summary


def test_miniapp_capture_summary_groups_endpoint_and_keeps_redaction(tmp_path):
    capture_dir = tmp_path / "capture"
    capture_dir.mkdir()
    path = capture_dir / "fishing-2026-07-07.jsonl"
    path.write_text(
        json.dumps(
            {
                "adapter_key": "fishing",
                "step_key": "result",
                "endpoint": "result",
                "method": "POST",
                "url_path": "/api/miniapp/xianxia-fishing/result",
                "status_code": 200,
                "ok": True,
                "elapsed_ms": 123,
                "created_at": 1783354167.0,
                "source": "unit",
                "request": {
                    "summary": {
                        "payload_keys": ["initData", "token"],
                        "secret_keys": ["initData"],
                    },
                    "payload_shape": {
                        "type": "object",
                        "keys": ["initData", "token"],
                    },
                    "payload": {
                        "initData": {"present": True, "digest": "safe"},
                        "token": {"present": True, "digest": "safe"},
                    },
                },
                "response": {
                    "data_keys": ["ok", "result"],
                    "body_shape": {
                        "type": "object",
                        "keys": ["ok", "result"],
                        "children": {
                            "result": {
                                "type": "object",
                                "keys": ["fish", "expGain"],
                            }
                        },
                    },
                    "body": {
                        "ok": True,
                        "result": {
                            "fish": {"name": "银须灵鲢", "weight": 3.32},
                            "expGain": 4,
                        },
                    },
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    summary = get_miniapp_capture_summary("fishing", day="2026-07-07", capture_dir=capture_dir)
    text = json.dumps(summary, ensure_ascii=False)
    rendered = format_miniapp_capture_summary(summary)

    assert summary["endpoint_count"] == 1
    assert summary["ok_records"] == 1
    assert summary["endpoints"][0]["request_payload_keys"] == ["initData", "token"]
    assert summary["endpoints"][0]["response_keys"] == ["ok", "result"]
    assert "xianxia-fishing/result" in rendered
    assert "query_id=" not in text
    assert "hash=" not in text
    assert "fish_SECRET" not in text


def test_miniapp_capture_summary_sanitizes_adversarial_error_and_source(tmp_path):
    capture_dir = tmp_path / "capture"
    capture_dir.mkdir()
    path = capture_dir / "trial-2026-07-07.jsonl"
    path.write_text(
        json.dumps(
            {
                "adapter_key": "trial",
                "step_key": "finish",
                "endpoint": "finish",
                "method": "POST",
                "url_path": "/api/miniapp/xianxia-trial/finish",
                "status_code": 200,
                "ok": False,
                "elapsed_ms": 88,
                "created_at": 1783354167.0,
                "source": "manual trial_SECRET999 Authorization: Bearer SUPERSECRET",
                "error": "failed initData=query_id%3Dabc%26hash%3DVERY_SECRET next=df_SECRET777",
                "request": {
                    "summary": {
                        "payload_keys": ["initData", "token", "trialProof"],
                        "secret_keys": ["initData"],
                    },
                    "payload_shape": {"type": "object", "keys": ["initData", "token", "trialProof"]},
                },
                "response": {
                    "data_keys": ["ok", "error"],
                    "body_shape": {"type": "object", "keys": ["ok", "error"]},
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    summary = get_miniapp_capture_summary("trial", day="2026-07-07", capture_dir=capture_dir)
    text = json.dumps(summary, ensure_ascii=False)
    rendered = format_miniapp_capture_summary(summary)

    assert summary["endpoint_count"] == 1
    assert "<redacted>" in text
    assert "<redacted>" in rendered
    assert "trial_SECRET999" not in text
    assert "df_SECRET777" not in text
    assert "VERY_SECRET" not in text
    assert "SUPERSECRET" not in text


def test_miniapp_capture_summary_reports_latest_success_and_error_times(tmp_path):
    capture_dir = tmp_path / "capture"
    capture_dir.mkdir()
    path = capture_dir / "trial-2026-07-07.jsonl"
    rows = [
        {
            "adapter_key": "trial",
            "step_key": "finish",
            "endpoint": "finish",
            "method": "POST",
            "url_path": "/api/miniapp/xianxia-trial/finish",
            "status_code": 400,
            "ok": False,
            "elapsed_ms": 90,
            "created_at": 1783354167.0,
            "error": "trial_invalid_proof",
            "request": {"summary": {"payload_keys": ["initData", "token", "trialProof"]}},
            "response": {"data_keys": ["error", "ok"]},
        },
        {
            "adapter_key": "trial",
            "step_key": "finish",
            "endpoint": "finish",
            "method": "POST",
            "url_path": "/api/miniapp/xianxia-trial/finish",
            "status_code": 200,
            "ok": True,
            "elapsed_ms": 120,
            "created_at": 1783383778.0,
            "request": {"summary": {"payload_keys": ["initData", "token", "trialProof"]}},
            "response": {"data_keys": ["event_id", "ok", "result"]},
        },
    ]
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")

    summary = get_miniapp_capture_summary("trial", day="2026-07-07", capture_dir=capture_dir)
    endpoint = summary["endpoints"][0]
    rendered = format_miniapp_capture_summary(summary)

    assert endpoint["ok_count"] == 1
    assert endpoint["error_count"] == 1
    assert endpoint["latest_success_at"] == 1783383778.0
    assert endpoint["latest_error_at"] == 1783354167.0
    assert "latest_ok_at:" in rendered
    assert "latest_err_at:" in rendered
