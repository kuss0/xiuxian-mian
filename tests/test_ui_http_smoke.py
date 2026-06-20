import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_ui_http_smoke_tool_passes():
    proc = subprocess.run(
        [sys.executable, "tools/ui_http_smoke.py", "--json"],
        cwd=PROJECT_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=False,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True
    check_names = {item["name"] for item in payload["checks"]}
    assert "unauthenticated state is rejected" in check_names
    assert "login token exchange returns session cookie" in check_names
    assert "authenticated state exposes expected snapshot" in check_names
