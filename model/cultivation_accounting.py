"""Identity-owned cultivation accounting; no sends, refreshes or retry control."""

from . import resource_accounting
from .persistence import mark_dirty
from .profile_observation import field_clocks
from .state import get_identity_account, get_identity_state, get_send_as_profile, has_identity, update_send_as_profile


STATE_KEY = "xiuwei_accounting"


def _read(identity_id):
    if not has_identity(identity_id):
        return None, "missing_identity"
    account_id = get_identity_account(identity_id)
    if account_id <= 0:
        return None, "missing_account"
    stored = get_identity_state(identity_id).get(STATE_KEY, {})
    if stored == {}:
        stored = {
            "account_id": account_id, "ledger": {},
            "profile_value": get_send_as_profile(identity_id).get("xiuwei_current", 0),
        }
    if (
        not isinstance(stored, dict) or stored.keys() != {"account_id", "ledger", "profile_value"}
        or type(stored["account_id"]) is not int or stored["account_id"] <= 0
        or type(stored["profile_value"]) is not int or stored["profile_value"] < 0
    ):
        return None, "corrupt"
    if stored["account_id"] != account_id:
        return None, "account_changed"
    ledger = resource_accounting.read_ledger(stored["ledger"])
    if ledger is None:
        return None, "corrupt"
    return {**stored, "ledger": ledger}, ""


def _matches_profile_clock(identity_id, ledger):
    clocks = field_clocks(identity_id)
    baseline = ledger["baseline"]
    return bool(
        clocks is not None and baseline is not None
        and clocks.get("xiuwei") == baseline["point"]["at"]
        and clocks["_evidence"].get("xiuwei") == baseline["point"]["evidence"]
    )


def stage_profile_snapshot(identity_id, value, observed_at, evidence):
    stored, _reason = _read(identity_id)
    if stored is None:
        return None
    return resource_accounting.record_snapshot(stored["ledger"], value, {"at": observed_at, "evidence": evidence})


def commit_profile_snapshot(identity_id, ledger, *, profile_applied=False):
    stored, _reason = _read(identity_id)
    if stored is None or resource_accounting.read_ledger(ledger) is None:
        return False
    stored["ledger"] = ledger
    if profile_applied:
        stored["profile_value"] = get_send_as_profile(identity_id).get("xiuwei_current", 0)
    if stored == get_identity_state(identity_id).get(STATE_KEY, {}):
        return False
    get_identity_state(identity_id)[STATE_KEY] = stored
    mark_dirty()
    return True


def apply_cultivation_delta(identity_id, key, amount, start, end, *, revision=None):
    """Verified facts only; None retains an unresolved amount instead of zero."""
    stored, _reason = _read(identity_id)
    if stored is None:
        return False
    if amount is None:
        ledger = resource_accounting.record_unresolved_delta(stored["ledger"], key, start, revision or end)
    else:
        ledger = resource_accounting.record_delta(stored["ledger"], key, amount, start, end, revision=revision)
    if ledger is None or ledger == stored["ledger"]:
        return False
    balance = resource_accounting.evaluate(ledger)
    if (
        balance["status"] == "ready" and _matches_profile_clock(identity_id, ledger)
        and get_send_as_profile(identity_id).get("xiuwei_current") == stored["profile_value"]
    ):
        update_send_as_profile(identity_id, xiuwei_current=balance["value"])
        stored["profile_value"] = balance["value"]
    stored["ledger"] = ledger
    get_identity_state(identity_id)[STATE_KEY] = stored
    mark_dirty()
    return True


def has_cultivation_result(identity_id, key, start, end):
    stored, _reason = _read(identity_id)
    return bool(stored is not None and resource_accounting.matching_entries(stored["ledger"], key, start, end))


def cultivation_balance(identity_id):
    stored, reason = _read(identity_id)
    if stored is None:
        return {"status": reason, "value": None}
    balance = resource_accounting.evaluate(stored["ledger"])
    if balance["status"] != "ready":
        return balance
    if (
        not _matches_profile_clock(identity_id, stored["ledger"])
        or get_send_as_profile(identity_id).get("xiuwei_current") != stored["profile_value"]
        or stored["profile_value"] != balance["value"]
    ):
        return {"status": "unverified_profile", "value": None}
    return balance
