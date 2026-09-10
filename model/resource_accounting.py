"""Bounded business-resource facts, independent of sends and retry control.

Callers must verify the identity, command and source before admitting a fact.
An operation's interval is not its local delivery time. Overlapping intervals
do not establish whether a snapshot already contains the operation.
"""

import copy

from .profile_observation import observation_is_newer, timestamp, valid_evidence


MAX_ENTRIES = 256
MAX_SOURCES_PER_ENTRY = 16
MAX_BALANCE = 2 ** 63 - 1


def empty_ledger():
    return {"version": 1, "baseline": None, "entries": [], "overflow_at": 0.0}


def valid_point(point, *, telegram_only=False):
    if not isinstance(point, dict) or point.keys() != {"at", "evidence"}:
        return False
    at, evidence = point["at"], point["evidence"]
    if timestamp(at) <= 0 or not valid_evidence(evidence, at):
        return False
    return not telegram_only or evidence["source"] == "telegram"


def compare_points(left, right):
    """Return -1/0/1 only for proved order, or None for incomparable evidence."""
    if not valid_point(left) or not valid_point(right):
        return None
    # API request starts are not the server's snapshot revision or read time.
    if left["evidence"]["source"] != "telegram" or right["evidence"]["source"] != "telegram":
        return None
    if left == right:
        return 0
    if observation_is_newer(right["at"], right["evidence"], left["at"], left["evidence"]):
        return 1
    if observation_is_newer(left["at"], left["evidence"], right["at"], right["evidence"]):
        return -1
    return None


def _valid_interval(start, end):
    if not valid_point(end, telegram_only=True):
        return False
    if start is None:
        return True
    return (
        valid_point(start, telegram_only=True)
        and start["evidence"]["chat_id"] == end["evidence"]["chat_id"]
        and not start["evidence"]["edited"]
        and start["evidence"]["msg_id"] < end["evidence"]["msg_id"]
        and start["at"] <= end["at"]
    )


def _valid_entry(entry):
    if not isinstance(entry, dict) or entry.keys() != {"key", "amount", "start", "end", "revision", "amount_floor", "conflicted", "sources"}:
        return False
    key = entry["key"]
    sources = entry["sources"]
    return (
        isinstance(key, str) and 0 < len(key) <= 160
        and all(char.isascii() and (char.isalnum() or char in ":._-") for char in key)
        and type(entry["amount"]) is int
        and type(entry["conflicted"]) is bool
        and _valid_interval(entry["start"], entry["end"])
        and valid_point(entry["revision"], telegram_only=True)
        and entry["revision"]["evidence"]["chat_id"] == entry["end"]["evidence"]["chat_id"]
        and compare_points(entry["end"], entry["revision"]) in {-1, 0}
        and (
            entry["amount_floor"] is None or (
                valid_point(entry["amount_floor"], telegram_only=True)
                and entry["amount_floor"]["evidence"]["chat_id"] == entry["end"]["evidence"]["chat_id"]
                and compare_points(entry["amount_floor"], entry["end"]) in {-1, 0}
            )
        )
        and isinstance(sources, list) and 0 < len(sources) <= MAX_SOURCES_PER_ENTRY
        and all(type(msg_id) is int and msg_id > 0 for msg_id in sources)
        and sources == sorted(set(sources))
        and entry["end"]["evidence"]["msg_id"] in sources
        and entry["revision"]["evidence"]["msg_id"] in sources
        and (entry["amount_floor"] is None or entry["amount_floor"]["evidence"]["msg_id"] in sources)
        and (entry["start"] is None or entry["start"]["evidence"]["msg_id"] < min(sources))
    )


def read_ledger(value):
    """Copy valid persisted facts; corruption must not become an empty history."""
    if value == {}:
        return empty_ledger()
    if (
        not isinstance(value, dict) or value.keys() != {"version", "baseline", "entries", "overflow_at"}
        or type(value["version"]) is not int or value["version"] != 1
        or type(value["overflow_at"]) not in {int, float}
        or value["overflow_at"] < 0 or timestamp(value["overflow_at"]) != value["overflow_at"]
        or not isinstance(value["entries"], list) or len(value["entries"]) > MAX_ENTRIES
    ):
        return None
    baseline = value["baseline"]
    if baseline is not None and (
        not isinstance(baseline, dict) or baseline.keys() != {"value", "point", "conflicted"}
        or type(baseline["value"]) is not int or baseline["value"] < 0
        or type(baseline["conflicted"]) is not bool
        or not valid_point(baseline["point"], telegram_only=True)
    ):
        return None
    if any(not _valid_entry(entry) for entry in value["entries"]):
        return None
    keys = [entry["key"] for entry in value["entries"]]
    if len(keys) != len(set(keys)):
        return None
    sources = [
        (entry["key"].partition(":")[0], entry["end"]["evidence"]["chat_id"], msg_id)
        for entry in value["entries"] for msg_id in entry["sources"]
    ]
    if len(sources) != len(set(sources)):
        return None
    commands = [
        (entry["key"].partition(":")[0], entry["start"]["evidence"]["chat_id"], entry["start"]["evidence"]["msg_id"])
        for entry in value["entries"] if entry["start"] is not None
    ]
    if len(commands) != len(set(commands)):
        return None
    return copy.deepcopy(value)


def _relative_to_baseline(entry, baseline):
    if baseline is None or baseline["conflicted"]:
        return "unknown"
    if entry["conflicted"] and baseline["point"]["at"] <= entry["revision"]["at"]:
        return "unknown"
    if compare_points(entry["end"], baseline["point"]) in {-1, 0}:
        return "included"
    if entry["start"] is not None and compare_points(entry["start"], baseline["point"]) == 1:
        return "after"
    return "unknown"


def record_snapshot(ledger, value, point):
    result = read_ledger(ledger)
    if result is None or type(value) is not int or value < 0 or not valid_point(point, telegram_only=True):
        return None
    previous = result["baseline"]
    if previous is not None:
        order = compare_points(point, previous["point"])
        if order is None or (order == 0 and value != previous["value"]):
            previous["conflicted"] = True
        if previous["conflicted"] and point["at"] <= previous["point"]["at"]:
            return result
        if order != 1:
            return result
    result["baseline"] = {"value": value, "point": copy.deepcopy(point), "conflicted": False}
    if point["evidence"]["source"] == "telegram" and point["at"] > result["overflow_at"]:
        result["overflow_at"] = 0.0
    return result


def _matches_operation(entry, key, start, end):
    if entry["key"] == key:
        return True
    if (
        entry["key"].partition(":")[0] != key.partition(":")[0]
        or entry["end"]["evidence"]["chat_id"] != end["evidence"]["chat_id"]
    ):
        return False
    return end["evidence"]["msg_id"] in entry["sources"] or (
        start is not None and entry["start"] is not None
        and entry["start"]["evidence"]["msg_id"] == start["evidence"]["msg_id"]
    )


def matching_entries(ledger, key, start, end):
    """Find retained owners without treating a changed display key as new work."""
    result = read_ledger(ledger)
    if result is None or not isinstance(key, str) or not _valid_interval(start, end):
        return []
    return [entry for entry in result["entries"] if _matches_operation(entry, key, start, end)]


def _revise_entry(previous, incoming):
    order = compare_points(incoming["revision"], previous["revision"])
    if previous["conflicted"] and incoming["revision"]["at"] <= previous["revision"]["at"]:
        return
    if order == -1:
        if incoming["conflicted"] or previous["amount"] != incoming["amount"]:
            if compare_points(incoming["revision"], previous["end"]) != -1:
                previous["amount_floor"] = copy.deepcopy(previous["revision"])
                previous["end"] = copy.deepcopy(previous["revision"])
        elif previous["amount_floor"] is None or compare_points(incoming["end"], previous["amount_floor"]) in {0, 1}:
            if compare_points(incoming["end"], previous["end"]) == -1:
                previous["end"] = copy.deepcopy(incoming["end"])
        return
    if incoming["conflicted"] or order not in {0, 1} or (order == 0 and previous["amount"] != incoming["amount"]):
        previous["conflicted"] = True
        if incoming["revision"]["at"] > previous["revision"]["at"] or order == 1:
            previous["revision"] = copy.deepcopy(incoming["revision"])
        previous["end"] = copy.deepcopy(previous["revision"])
        previous["amount_floor"] = copy.deepcopy(previous["revision"])
        return
    # A changed final amount is a revision, not a new debit. A baseline
    # between the old result and this revision cannot yet prove inclusion.
    if previous["conflicted"] or previous["amount"] != incoming["amount"]:
        previous["end"] = copy.deepcopy(incoming["revision"])
        previous["amount_floor"] = copy.deepcopy(incoming["revision"])
    elif (
        compare_points(incoming["end"], previous["end"]) == -1
        and (previous["amount_floor"] is None or compare_points(incoming["end"], previous["amount_floor"]) in {0, 1})
    ):
        previous["end"] = copy.deepcopy(incoming["end"])
    previous["amount"] = incoming["amount"]
    previous["revision"] = copy.deepcopy(incoming["revision"])
    previous["conflicted"] = False


def _record_delta(ledger, key, amount, start, end, revision, *, unresolved=False):
    result = read_ledger(ledger)
    if result is None or not _valid_interval(start, end) or not valid_point(revision, telegram_only=True):
        return None
    incoming = {
        "key": key, "amount": amount, "start": start, "end": end,
        "revision": revision, "amount_floor": None, "conflicted": unresolved,
        "sources": sorted({end["evidence"]["msg_id"], revision["evidence"]["msg_id"]}),
    }
    if not _valid_entry(incoming):
        return None
    # A first-seen edit does not prove that its amount was already final at
    # the original message time. The revision remains the upper bound.
    incoming["end"] = revision
    matches = [entry for entry in result["entries"] if _matches_operation(entry, key, start, revision)]
    if matches:
        sources = sorted({source for entry in [*matches, incoming] for source in entry["sources"]})
        if len(sources) > MAX_SOURCES_PER_ENTRY:
            if all(_relative_to_baseline(entry, result["baseline"]) == "included" for entry in [*matches, incoming]):
                return result
            result["overflow_at"] = max(result["overflow_at"], revision["at"])
            return result
        ordered = [*sorted(matches, key=lambda entry: entry["revision"]["at"]), incoming]
        previous = copy.deepcopy(ordered[0])
        for entry in ordered[1:]:
            if entry["end"]["evidence"]["chat_id"] != previous["end"]["evidence"]["chat_id"]:
                return None
            if previous["start"] is not None and entry["start"] is not None and previous["start"] != entry["start"]:
                conflicted = copy.deepcopy(matches[0])
                _revise_entry(conflicted, {**incoming, "conflicted": True})
                conflicted["sources"] = sources
                if len(matches) == 1 and _valid_entry(conflicted):
                    result["entries"][result["entries"].index(matches[0])] = conflicted
                else:
                    result["overflow_at"] = max(result["overflow_at"], revision["at"])
                return result
            _revise_entry(previous, entry)
            previous["start"] = copy.deepcopy(previous["start"] or entry["start"])
            floor = entry["amount_floor"]
            if floor is not None:
                if previous["amount_floor"] is None or compare_points(floor, previous["amount_floor"]) == 1:
                    previous["amount_floor"] = copy.deepcopy(floor)
                if compare_points(previous["amount_floor"], previous["end"]) not in {-1, 0}:
                    previous["end"] = copy.deepcopy(previous["revision"])
        previous["key"] = matches[0]["key"]
        previous["sources"] = sources
        if not _valid_entry(previous):
            return None
        result_index = result["entries"].index(matches[0])
        result["entries"] = [entry for entry in result["entries"] if entry not in matches]
        result["entries"].insert(result_index, previous)
        return result
    if len(result["entries"]) >= MAX_ENTRIES:
        retired = next((entry for entry in result["entries"] if _relative_to_baseline(entry, result["baseline"]) == "included"), None)
        if retired is not None:
            result["entries"].remove(retired)
        elif _relative_to_baseline(incoming, result["baseline"]) == "included":
            return result
        else:
            result["overflow_at"] = max(result["overflow_at"], incoming["revision"]["at"])
            return result
    result["entries"].append(copy.deepcopy(incoming))
    return result


def record_delta(ledger, key, amount, start, end, *, revision=None):
    """Record one family/operation; source and command enrichment are not charges."""
    return _record_delta(ledger, key, amount, start, end, revision if revision is not None else end)


def evaluate(ledger):
    result = read_ledger(ledger)
    if result is None:
        return {"status": "corrupt", "value": None}
    if result["overflow_at"]:
        return {"status": "capacity", "value": None}
    baseline = result["baseline"]
    if baseline is None:
        return {"status": "no_baseline", "value": None}
    if baseline["conflicted"]:
        return {"status": "conflicting_snapshot", "value": None}
    value = baseline["value"]
    for entry in result["entries"]:
        relation = _relative_to_baseline(entry, baseline)
        if relation == "included":
            continue
        if entry["conflicted"]:
            return {"status": "conflicting_revision", "value": None}
        if relation == "unknown":
            return {"status": "overlapping_observation", "value": None}
        value += entry["amount"]
    if value < 0:
        return {"status": "inconsistent_balance", "value": None}
    if value > MAX_BALANCE:
        return {"status": "out_of_range", "value": None}
    return {"status": "ready", "value": value}


def record_unresolved_delta(ledger, key, start, end):
    """Retain an owned result whose amount is unknown, not an assumed zero."""
    return _record_delta(ledger, key, 0, start, end, end, unresolved=True)
