import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


CONFIG_SOURCE = Path(__file__).resolve().parents[1] / "model" / "config.py"
DUMMY_CONFIG = {
    "API_ID": "12345",
    "API_HASH": "0" * 32,
    "ADMIN_ID": "1",
    "LOG_GROUP_ID": "0",
    "LOG_SEND_MODE": "account",
    "TG_PROXY_TYPE": "",
    "CHAOGU_UI_PUBLIC_BASE_URL": "http://127.0.0.1:3030",
}


def run_config(tmp_path, *, dotenv=None, testing=False, base_url=True):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    source = model_dir / "config.py"
    shutil.copyfile(CONFIG_SOURCE, source)
    if dotenv is not None:
        (tmp_path / ".env").write_text(
            "\n".join(f"{key}={value}" for key, value in dotenv.items()),
            encoding="utf-8",
        )
    # This child imports the real config in an isolated project without any
    # inherited live credentials or production data-directory overrides.
    env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG") if key in os.environ}
    if testing:
        env.update(DUMMY_CONFIG, XIUXIAN_TESTING="1")
    if not base_url:
        env.pop("CHAOGU_UI_PUBLIC_BASE_URL", None)
    code = """
import json
import os
import runpy
import sys
from unittest.mock import patch

with patch('requests.Session', side_effect=AssertionError('unexpected network access')):
    config = runpy.run_path(sys.argv[1])
print(json.dumps({
    'data': config['DATA_DIR'],
    'state': config['STATE_DIR'],
    'session': config['SESSION_DIR'],
    'messages': config['MESSAGES_DIR'],
    'public_url': config['UI_PUBLIC_BASE_URL'],
    'dotenv_secret_loaded': 'AUDIT_DOTENV_SECRET' in os.environ,
}))
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(source)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    return json.loads(result.stdout)


def test_dotenv_data_paths_are_applied_before_directories_are_created(tmp_path):
    paths = {name: str(tmp_path / name.lower()) for name in (
        "XIUXIAN_DATA_DIR", "XIUXIAN_STATE_DIR", "XIUXIAN_SESSION_DIR", "XIUXIAN_MESSAGES_DIR",
    )}
    result = run_config(tmp_path, dotenv={**DUMMY_CONFIG, **paths})
    assert result["data"] == paths["XIUXIAN_DATA_DIR"]
    assert result["state"] == paths["XIUXIAN_STATE_DIR"]
    assert result["session"] == paths["XIUXIAN_SESSION_DIR"]
    assert result["messages"] == paths["XIUXIAN_MESSAGES_DIR"]
    assert not (tmp_path / "data").exists()


def test_testing_does_not_import_credentials_from_project_dotenv(tmp_path):
    result = run_config(tmp_path, dotenv={"AUDIT_DOTENV_SECRET": "fixture-only"}, testing=True)
    assert result["dotenv_secret_loaded"] is False


def test_testing_without_public_url_never_looks_up_external_ip(tmp_path):
    result = run_config(tmp_path, testing=True, base_url=False)
    assert result["public_url"] == "http://127.0.0.1:3030"
