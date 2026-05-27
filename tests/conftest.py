import atexit
import os
import shutil
import tempfile
from pathlib import Path


_TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="xiuxian-pytest-"))

os.environ["XIUXIAN_TESTING"] = "1"
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "00000000000000000000000000000000")
os.environ.setdefault("TG_PROXY_TYPE", "")
os.environ.setdefault("TG_PROXY_HOST", "127.0.0.1:7890")
os.environ.setdefault("LOG_GROUP_ID", "0")
os.environ.setdefault("LOG_SEND_MODE", "account")
os.environ.setdefault("ADMIN_ID", "1")
os.environ.setdefault("CHAOGU_UI_HOST", "127.0.0.1")
os.environ.setdefault("CHAOGU_UI_PORT", "3030")

if os.environ.get("XIUXIAN_ALLOW_LIVE_TEST_DB") != "1":
    os.environ["XIUXIAN_DATA_DIR"] = str(_TEST_DATA_DIR)
    os.environ["XIUXIAN_SESSION_DIR"] = str(_TEST_DATA_DIR / "session")
    os.environ["XIUXIAN_STATE_DIR"] = str(_TEST_DATA_DIR / "state")
    os.environ["XIUXIAN_MESSAGES_DIR"] = str(_TEST_DATA_DIR / "messages")
    os.environ["XIUXIAN_DB_FILE"] = str(_TEST_DATA_DIR / "state" / "chaogu_state.db")

for key in ("XIUXIAN_SESSION_DIR", "XIUXIAN_STATE_DIR", "XIUXIAN_MESSAGES_DIR"):
    Path(os.environ[key]).mkdir(parents=True, exist_ok=True)


@atexit.register
def _cleanup_test_data_dir():
    if os.environ.get("XIUXIAN_ALLOW_LIVE_TEST_DB") == "1":
        return
    shutil.rmtree(_TEST_DATA_DIR, ignore_errors=True)
