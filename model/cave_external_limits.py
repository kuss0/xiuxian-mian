"""Persist account-wide spacing for the dwelling external-launch endpoint."""

import math

from .persistence import save_state
from .state import get_identity_account, get_miniapp_state_records, set_miniapp_state_records


# Native 429 samples show both 10-minute and hourly quotas shared by identities
# on one Telegram account. Cap at 6/10min and 36/hour, surviving restarts.
INTERVAL_SEC = 100.0
KEY = "cave_external_account_next_at"


def next_request_at(identity_id):
    account = get_identity_account(identity_id)
    meta = get_miniapp_state_records().get("_meta", {})
    deadlines = meta.get(KEY, {}) if isinstance(meta, dict) else {}
    value = deadlines.get(str(account), 0) if isinstance(deadlines, dict) else 0
    return float(value) if type(value) in {int, float} and math.isfinite(value) and value > 0 else 0.0


def reserve_request(identity_id, now):
    """Return a delay without dispatching or waiting inside a worker."""
    account = get_identity_account(identity_id)
    if account <= 0:
        return 0.0
    wait = next_request_at(identity_id) - now
    if wait > 0:
        return wait
    records = dict(get_miniapp_state_records())
    meta = dict(records.get("_meta") or {})
    deadlines = dict(meta.get(KEY) or {})
    deadlines = {key: value for key, value in deadlines.items()
                 if type(value) in {int, float} and math.isfinite(value) and value > now}
    deadlines[str(account)] = now + INTERVAL_SEC
    records["_meta"] = {**meta, KEY: deadlines}
    set_miniapp_state_records(records)
    return 0.0 if save_state() is True else INTERVAL_SEC
