"""Read-only policy for an unanswered, cost-free daily income command.

Never manufacture a completion or release a debit reservation. A later native
balance can cover an old income without proving whether that command succeeded.
"""

import math


def stale_daily_income(operation, now):
    return bool(
        operation.get("phase") == "sent" and operation.get("command") == ".每日献祭"
        and operation.get("source_module") == "阴罗宗" and not operation.get("beneficiary")
        and operation.get("budget") == {"costs": {}, "exclusive": []}
        and type(operation.get("msg_id")) is int and operation["msg_id"] > 0
        and type(operation.get("sent_at")) in {int, float} and operation["sent_at"] > 0
        and math.isfinite(operation["sent_at"]) and type(now) in {int, float} and math.isfinite(now)
        and now - operation["sent_at"] >= 86400
    )


def income_covered_by_native_panel(value, operation):
    baseline = value.get("book", {}).get("ledgers", {}).get("sha", {}).get("baseline")
    if not isinstance(baseline, dict) or baseline.get("conflicted") is not False:
        return False
    point = baseline.get("point", {})
    at, evidence = point.get("at"), point.get("evidence", {})
    if (type(at) not in {int, float} or not stale_daily_income(operation, at)
            or evidence.get("source") != "telegram" or evidence.get("chat_id") != operation.get("chat_id")
            or type(evidence.get("msg_id")) is not int or evidence["msg_id"] <= operation["msg_id"]
            or value.get("hold") or value.get("book", {}).get("gap")):
        return False
    # An ambiguous native reply needs its existing reconciliation path, even
    # when the latest balance looks plausible. This policy only covers silence.
    return not any(
        receipt.get("start", {}).get("evidence", {}).get("chat_id") == operation["chat_id"]
        and receipt.get("start", {}).get("evidence", {}).get("msg_id") == operation["msg_id"]
        for receipt in value["book"].get("receipts", [])
    )


def blocking_operations(value):
    return [operation for operation in value["operations"]
            if operation["phase"] in {"prepared", "sent", "unknown"}
            and not income_covered_by_native_panel(value, operation)]
