"""Native dwelling fishing contract, without HTTP, runtime state or scheduling.

Physics follows wxjerry 3c77db65 and the official fishing-v14-world-moon-cue
controller (2026-09-30). Callers must persist intent and bind the selected player
before dispatch. Parsing a context alone does not establish identity ownership.
"""

from copy import deepcopy
from dataclasses import dataclass
import math
import re
import time


MODE = "dwelling_bobber_v1"
TICK_MS = 20
MAX_DURATION_MS = 180000
MAX_EVENTS = 1000
SITES = {
    "west-shore": (-9.3, -0.98, 7.78),
    "waterfall-pool": (2.6, -0.56, -11.05),
    "east-shore": (9.3, -0.98, 7.78),
}


class ProtocolError(ValueError):
    """Unconfirmed server contract; never authorizes a replacement cast."""


def _number(value, name, low, high, *, integer=False):
    if (type(value) not in (int, float) or not low <= value <= high
            or not math.isfinite(value) or (integer and type(value) is not int)):
        raise ProtocolError("invalid_" + name)
    return value


def _identifier(value, name):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", value):
        raise ProtocolError("invalid_" + name)
    return value


def session_identifier(value):
    # Current dwelling sessions use numeric IDs; keep their JSON type intact.
    if type(value) is int:
        return _number(value, "session_id", 1, 2**53 - 1, integer=True)
    return _identifier(value, "session_id")


def _mapping(value, name):
    if not isinstance(value, dict):
        raise ProtocolError("invalid_" + name)
    return value


def _text(value, name, *, empty=False):
    if (not isinstance(value, str) or len(value) > 256
            or (not empty and not value.strip()) or any(ord(c) < 32 for c in value)):
        raise ProtocolError("invalid_" + name)
    return value


def _response(payload):
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise ProtocolError("unconfirmed_response")
    return payload


def placement(site_id, model_id):
    if not isinstance(site_id, str) or site_id not in SITES:
        raise ProtocolError("invalid_site")
    return {"siteId": site_id, "position": list(SITES[site_id]),
            "controlMode": "companion", "modelId": _identifier(model_id, "model_id")}


def parse_context(payload):
    """Return only validated quota/eligibility facts, never inferred catches."""
    context = _mapping(_response(payload).get("context"), "context")
    quota = _mapping(context.get("quota"), "quota")
    counts = {key: _number(quota.get(key), "quota_" + key, 0, 1000, integer=True)
              for key in ("used", "remaining", "limit")}
    if counts["used"] + counts["remaining"] != counts["limit"]:
        raise ProtocolError("inconsistent_quota")
    enabled = context.get("enabled")
    if type(enabled) is not bool:
        raise ProtocolError("invalid_enabled")
    unavailable = _text(context.get("unavailable", ""), "unavailable", empty=True)
    if "rod" not in context:
        raise ProtocolError("missing_rod_field")
    rod = context["rod"]
    if rod is not None:
        rod = _mapping(rod, "rod")
        rod = {"itemId": _identifier(rod.get("itemId"), "rod_id"),
               "name": _text(rod.get("name"), "rod_name")}
    conflict = context.get("conflict")
    if conflict is not None:
        _mapping(conflict, "conflict")
    baits = context.get("baits")
    if not isinstance(baits, list) or len(baits) > 100:
        raise ProtocolError("invalid_baits")
    inventory = []
    for bait in baits:
        _mapping(bait, "bait")
        unlocked = bait.get("unlocked", True)
        if type(unlocked) is not bool:
            raise ProtocolError("invalid_bait_unlocked")
        inventory.append({"itemId": _identifier(bait.get("itemId"), "bait_id"),
                          "name": _text(bait.get("name"), "bait_name"),
                          "count": _number(bait.get("count"), "bait_count", 0, 10**9, integer=True),
                          "unlocked": unlocked})
    if len({bait["itemId"] for bait in inventory}) != len(inventory):
        raise ProtocolError("duplicate_bait")
    return {"quota": counts, "rod": rod, "baits": inventory, "enabled": enabled,
            "unavailable": unavailable, "conflict": conflict is not None}


def cast_block_reason(context, bait_item_id):
    """Use parse_context output; reasons are identity-local, not global fuses."""
    if context["conflict"]:
        return "session_conflict"
    if context["unavailable"]:
        return context["unavailable"]
    if not context["enabled"]:
        return "fishing_site_unavailable"
    if context["rod"] is None:
        return "fishing_rod_missing"
    if context["quota"]["remaining"] == 0:
        return "fishing_daily_limit_reached"
    bait = next((item for item in context["baits"] if item["itemId"] == bait_item_id), None)
    if bait is None or bait["count"] == 0:
        return "fishing_bait_missing"
    return "" if bait["unlocked"] else "fishing_bait_level_low"


def parse_owned_session(payload, *, session_id, site_id):
    """Only a caller-owned, exact session may advance or project settlement."""
    session_identifier(session_id)
    if not isinstance(site_id, str) or site_id not in SITES:
        raise ProtocolError("invalid_site")
    remote = _mapping(_response(payload).get("session"), "session")
    remote_id = session_identifier(remote.get("sessionId"))
    if type(remote_id) is not type(session_id) or remote_id != session_id or remote.get("siteId") != site_id:
        raise ProtocolError("session_mismatch")
    if remote.get("mode") != MODE:
        raise ProtocolError("mode_mismatch")
    result = remote.get("result")
    if result is not None:
        _mapping(result, "result")
        if "ready" in result and type(result["ready"]) is not bool:
            raise ProtocolError("invalid_result_ready")
    if result and result.get("ready") is True:
        if type(result.get("caught")) is not bool:
            raise ProtocolError("invalid_caught")
        catches = {}
        if result["caught"]:
            fish = _mapping(result.get("fish"), "fish")
            name = _text(fish.get("name"), "fish_name")
            count = _number(fish.get("quantity", fish.get("count", 1)), "fish_count", 1, 1000, integer=True)
            if "count" in fish:
                _number(fish["count"], "fish_count", 1, 1000, integer=True)
            if "quantity" in fish and "count" in fish and fish["quantity"] != fish["count"]:
                raise ProtocolError("inconsistent_fish_count")
            catches[name] = count
        return {"session_id": session_id, "site_id": site_id, "phase": "settled", "catches": catches}
    if remote.get("status") == "settling":
        return {"session_id": session_id, "site_id": site_id, "phase": "settling"}
    if remote.get("status") != "active":
        raise ProtocolError("unconfirmed_session_status")
    phase = remote.get("phase")
    if phase == "fighting":
        challenge = normalize_fight_challenge(remote.get("fight"))
        return {"session_id": session_id, "site_id": site_id, "phase": phase, "fight": challenge}
    if phase not in ("casting", "waiting", "bite"):
        raise ProtocolError("invalid_session_phase")
    stamps = {key: _number(remote.get(key), key, 1, 10**15) for key in ("biteAt", "expiresAt", "serverNow")}
    if stamps["biteAt"] >= stamps["expiresAt"]:
        raise ProtocolError("invalid_bite_window")
    return {"session_id": session_id, "site_id": site_id, "phase": phase, **stamps}


def parse_settlement_resources(payload, *, session_id, site_id):
    """Owned settlement facts, separate fish gains from extra drops and stock.

    A missing context leaves stock unknown. It does not mean empty inventory or
    no active chum. Only the owning cast's response/state query may supply it.
    """
    parsed = parse_owned_session(payload, session_id=session_id, site_id=site_id)
    if parsed["phase"] != "settled":
        raise ProtocolError("native_settlement_not_ready")
    result = payload["session"]["result"]
    loot = result.get("bonusLoot")
    if not isinstance(loot, list) or len(loot) > 64:
        raise ProtocolError("invalid_native_bonus_loot")
    rewards = {}
    for item in loot:
        _mapping(item, "bonus_item")
        name = _text(item.get("name"), "bonus_name")
        quantity = _number(item.get("qty"), "bonus_quantity", 1, 10**9, integer=True)
        rewards[name] = _number(rewards.get(name, 0) + quantity, "bonus_total", 1, 10**9, integer=True)
    context = payload.get("context")
    resources = {"rewards": rewards, "baits": None, "active_chum": None, "chum_usage": None}
    if context is None:
        return resources
    facts = parse_context(payload)
    baits = {bait["name"]: bait["count"] for bait in facts["baits"]}
    if len(baits) != len(facts["baits"]):
        raise ProtocolError("ambiguous_native_bait_names")
    shop = _mapping(context.get("shop"), "settlement_shop")
    if "activeChum" not in shop:
        raise ProtocolError("missing_settlement_chum")
    active = shop["activeChum"]
    if active is not None:
        _mapping(active, "settlement_chum")
        active = {"name": _text(active.get("name"), "chum_name"),
                  "remaining": _number(active.get("remaining"), "chum_remaining", 1, 1000, integer=True)}
    rows = shop.get("chums")
    if not isinstance(rows, list) or len(rows) > 100:
        raise ProtocolError("invalid_settlement_chums")
    counts = {}
    for row in rows:
        _mapping(row, "settlement_chum_row")
        name = _text(row.get("name"), "chum_name")
        if name in counts:
            raise ProtocolError("duplicate_settlement_chum")
        counts[name] = _number(row.get("usedToday"), "chum_used", 0, 1000, integer=True)
    resources.update(baits=baits, active_chum=active, chum_usage=counts)
    return resources


def validate_settlement_resources(resources):
    _mapping(resources, "settlement_resources")
    if set(resources) != {"rewards", "baits", "active_chum", "chum_usage"}:
        raise ProtocolError("invalid_settlement_resource_fields")
    for key, limit in (("rewards", 64), ("baits", 100), ("chum_usage", 100)):
        values = resources[key]
        if key != "rewards" and values is None:
            continue
        _mapping(values, "settlement_" + key)
        if len(values) > limit:
            raise ProtocolError("oversized_settlement_" + key)
        for name, count in values.items():
            _text(name, "settlement_resource_name")
            _number(count, "settlement_resource_count", 1 if key == "rewards" else 0,
                    1000 if key == "chum_usage" else 10**9, integer=True)
    if (resources["baits"] is None) != (resources["chum_usage"] is None):
        raise ProtocolError("incomplete_settlement_stock")
    active = resources["active_chum"]
    if active is not None:
        _mapping(active, "settlement_chum")
        if set(active) != {"name", "remaining"} or resources["baits"] is None:
            raise ProtocolError("invalid_settlement_chum")
        _text(active["name"], "chum_name")
        _number(active["remaining"], "chum_remaining", 1, 1000, integer=True)
    return resources


@dataclass(frozen=True)
class ServerClock:
    stamp_ms: float
    received_monotonic: float
    round_trip_ms: float = 0

    @classmethod
    def capture(cls, stamp_ms, started_monotonic, received_monotonic):
        _number(stamp_ms, "server_time", 1, 10**15)
        _number(started_monotonic, "clock_start", 0, 10**12)
        _number(received_monotonic, "clock_end", started_monotonic, 10**12)
        round_trip_ms = (received_monotonic - started_monotonic) * 1000
        # The browser's 250 ms cap leaves a multi-second VPS response stale.
        # Use the midpoint estimate, retaining a full RTT as the hook budget.
        return cls(stamp_ms + round_trip_ms / 2, received_monotonic, round_trip_ms)

    def now_ms(self, monotonic_now):
        _number(monotonic_now, "clock_now", self.received_monotonic, 10**12)
        return self.stamp_ms + (monotonic_now - self.received_monotonic) * 1000


def next_session_action(session, clock, monotonic_now):
    """Choose from a parsed owned session; an expired bite only permits state."""
    phase = session["phase"]
    if phase in ("settled", "settling", "fighting"):
        return {"settled": "settled", "settling": "state", "fighting": "fight"}[phase], 0
    if phase not in ("casting", "waiting", "bite"):
        raise ProtocolError("invalid_session_phase")
    now_ms = clock.now_ms(monotonic_now)
    if now_ms >= session["expiresAt"]:
        return "state", 0
    if now_ms < session["biteAt"]:
        return "wait", (session["biteAt"] - now_ms) / 1000
    if now_ms + clock.round_trip_ms >= session["expiresAt"]:
        return "state", 0
    return "hook", 0


def _fight_state(challenge):
    _mapping(challenge, "challenge")
    challenge_id = _identifier(challenge.get("challengeId"), "challenge_id")
    c = {"challenge_id": challenge_id,
         "low": _number(challenge.get("targetLow", 41), "target_low", 0, 100),
         "high": _number(challenge.get("targetHigh", 68), "target_high", 0, 100),
         "power": _number(challenge.get("fishPower", 1.7), "fish_power", .01, 100),
         "minimum": _number(challenge.get("minDurationMs", 5200), "minimum", 20, MAX_DURATION_MS, integer=True),
         "maximum": _number(challenge.get("maxDurationMs", 70000), "maximum", 20, MAX_DURATION_MS, integer=True),
         "interval": _number(challenge.get("checkpointIntervalMs", 2500), "checkpoint_interval", 20, MAX_DURATION_MS, integer=True),
         "event_limit": _number(challenge.get("maxInputEvents", MAX_EVENTS), "event_limit", 1, MAX_EVENTS, integer=True),
         "version": _number(challenge.get("behaviorVersion", 1), "behavior_version", 1, 2, integer=True)}
    if (c["low"] >= c["high"] or c["minimum"] > c["maximum"]
            or c["minimum"] % TICK_MS or c["maximum"] % TICK_MS):
        raise ProtocolError("invalid_fight_bounds")
    c["behavior"] = challenge.get("behavior", "steady")
    if c["behavior"] not in ("steady", "leap", "surge"):
        raise ProtocolError("invalid_behavior")
    seed = challenge.get("fishSeed", "seed")
    if type(seed) is int:
        _number(seed, "fish_seed", -(2**53 - 1), 2**53 - 1, integer=True)
    if not isinstance(seed, (str, int)) or isinstance(seed, bool) or len(str(seed)) > 256:
        raise ProtocolError("invalid_fish_seed")
    # JS for-of visits code points but charCodeAt(0) takes the first UTF-16 unit.
    c["seed"] = sum(ord(ch) if ord(ch) <= 0xffff else 0xd800 + ((ord(ch) - 0x10000) >> 10)
                    for ch in str(seed or "seed")) / 19
    struggles = challenge.get("struggles", [])
    if not isinstance(struggles, list) or len(struggles) > 100:
        raise ProtocolError("invalid_struggles")
    c["struggles"] = []
    for item in struggles:
        _mapping(item, "struggle")
        c["struggles"].append({
            "start": _number(item.get("startMs"), "struggle_start", 0, c["maximum"]),
            "duration": _number(item.get("durationMs"), "struggle_duration", 1, MAX_DURATION_MS),
            "strength": _number(item.get("strength"), "struggle_strength", 0, 100),
        })
    checkpoint = challenge.get("checkpoint")
    if checkpoint is None:
        checkpoint = {}
    _mapping(checkpoint, "checkpoint")
    elapsed = _number(checkpoint.get("durationMs", 0), "checkpoint_duration", 0, c["maximum"], integer=True)
    if elapsed % TICK_MS:
        raise ProtocolError("unaligned_checkpoint")
    details = checkpoint.get("details", {})
    _mapping(details, "checkpoint_details")
    initial = {"progress": 0, "tension": (c["low"] + c["high"]) / 2 - 8,
               "holding": False, "danger_ms": 0, "slack_ms": 0, "samples": 0, "stable_samples": 0}
    if elapsed and any(key not in details for key in initial):
        raise ProtocolError("incomplete_checkpoint")
    g = {key: details.get(key, value) for key, value in initial.items()}
    for key in ("progress", "tension"):
        _number(g[key], key, 0, 100)
    if type(g["holding"]) is not bool:
        raise ProtocolError("invalid_holding")
    for key in ("danger_ms", "slack_ms", "samples", "stable_samples"):
        _number(g[key], key, 0, elapsed, integer=True)
    if (g["samples"] * TICK_MS != elapsed or g["stable_samples"] > g["samples"]
            or g["danger_ms"] % TICK_MS or g["slack_ms"] % TICK_MS
            or g["danger_ms"] + g["slack_ms"] + g["stable_samples"] * TICK_MS != elapsed):
        raise ProtocolError("inconsistent_checkpoint_samples")
    if not elapsed and (g["progress"] or g["holding"]):
        raise ProtocolError("invalid_initial_checkpoint")
    events = checkpoint.get("events", [])
    if not isinstance(events, list) or len(events) > c["event_limit"]:
        raise ProtocolError("invalid_events")
    prior = 0
    for event in events:
        _mapping(event, "event")
        _number(event.get("t"), "event_time", prior, elapsed, integer=True)
        if type(event.get("holding")) is not bool or set(event) != {"t", "holding"}:
            raise ProtocolError("invalid_event")
        prior = event["t"]
    if bool(events and events[-1]["holding"]) != g["holding"]:
        raise ProtocolError("inconsistent_checkpoint_holding")
    return c, g, elapsed, deepcopy(events)


def fight_steps(challenge):
    """Yield (final, proof, checkpointState); this does not authorize early sends.

    A runner must wait the elapsed proof duration on a monotonic clock, check
    identity ownership and persist intent before each mutation, including fight.
    """
    c, g, elapsed, events = _fight_state(challenge)
    last = elapsed
    ready = lambda: (g["progress"] >= 100 and elapsed >= c["minimum"]) or elapsed >= c["maximum"]
    restored_hold = g["holding"] and not ready()
    if restored_hold:
        g["holding"] = False
        events.append({"t": elapsed + TICK_MS, "holding": False})
    margin = min(7, max(2, (c["high"] - c["low"]) * .3))
    first_restored_tick = restored_hold
    while True:
        if not ready():
            hold = g["holding"]
            if not first_restored_tick:
                if g["tension"] <= c["low"] + margin:
                    hold = True
                elif g["tension"] >= c["high"] - margin:
                    hold = False
            first_restored_tick = False
            elapsed += TICK_MS
            if hold != g["holding"]:
                events.append({"t": elapsed, "holding": hold})
                g["holding"] = hold
            power, seed = c["power"], c["seed"]
            pulse = math.sin(elapsed * .0027 * power + seed)
            surge = max(0, math.sin(elapsed * .0041 + seed * 1.7))
            pull = power * (.72 + pulse * .24 + surge * .42)
            if c["version"] >= 2:
                for struggle in c["struggles"]:
                    offset = elapsed - struggle["start"]
                    if not 0 <= offset < struggle["duration"]:
                        continue
                    portion, strength = offset / struggle["duration"], struggle["strength"]
                    wave = math.sin(math.pi * portion)
                    if c["behavior"] == "steady":
                        pull += power * strength * .18 * wave
                    elif c["behavior"] == "leap":
                        pull *= 1 + strength * .48 * wave
                    elif c["behavior"] == "surge":
                        pull += power * strength * .72 * (.55 + .45 * math.sin(portion * math.pi * 2))
                    break
            tension = g["tension"] + ((24 + pull * 3.1) if hold else (pull * 4.8 - 24)) * .02
            g["tension"] = max(0, min(100, tension + math.sin(elapsed * .012 + seed) * .24))
            if c["low"] <= g["tension"] <= c["high"]:
                g["stable_samples"] += 1
                g["progress"] += (8.2 + power * .7 + (2.2 if hold else .5)) * .02
            elif g["tension"] > c["high"]:
                g["danger_ms"] += TICK_MS
                g["progress"] -= (1.5 + power * .25) * .02
            else:
                g["slack_ms"] += TICK_MS
                g["progress"] -= .9 * .02
            if hold and g["tension"] < c["low"]:
                g["progress"] += 1.1 * .02
            g["progress"] = max(0, min(100, g["progress"]))
            g["samples"] += 1
        final = ready()
        if final or elapsed - last >= c["interval"]:
            # Only record inputs used by physics. The UI's post-submit button
            # release has no simulated tick and is not part of this proof.
            if len(events) > c["event_limit"]:
                raise ProtocolError("input_event_limit")
            proof = {"mode": "xianxiaFishingV2", "challengeId": c["challenge_id"],
                     "durationMs": elapsed, "events": deepcopy(events)}
            if final:
                proof["landed"] = g["progress"] >= 100
            yield final, proof, deepcopy(g)
            if final:
                return
            last = elapsed


def normalize_fight_challenge(challenge):
    config, details, elapsed, events = _fight_state(challenge)
    keys = {"challengeId", "targetLow", "targetHigh", "fishPower", "fishSeed", "minDurationMs",
            "maxDurationMs", "checkpointIntervalMs", "maxInputEvents", "behaviorVersion", "behavior"}
    result = {key: deepcopy(value) for key, value in challenge.items() if key in keys}
    if "struggles" in challenge:
        result["struggles"] = [{"startMs": row["start"], "durationMs": row["duration"], "strength": row["strength"]}
                               for row in config["struggles"]]
    if challenge.get("checkpoint") is not None:
        result["checkpoint"] = {"durationMs": elapsed, "events": events, "details": details}
    return result


def timed_fight_steps(challenge, *, is_current, monotonic=time.monotonic, sleeper=time.sleep):
    """Real-time guard for a blocking worker; no transport or journal side effects.

    Cancellation/draining of the worker and save-before-send are the caller's
    responsibility. A no-op sleeper cannot fast-forward server proof duration.
    """
    challenge = deepcopy(challenge)
    _, _, restored, _ = _fight_state(challenge)
    start = _number(monotonic(), "fight_start", 0, 10**12)
    previous = start
    for final, proof, details in fight_steps(challenge):
        deadline = start + (proof["durationMs"] - restored) / 1000
        while True:
            if is_current() is not True:
                raise ProtocolError("native_owner_changed")
            now = _number(monotonic(), "fight_clock", previous, 10**12)
            remaining = deadline - now
            if remaining <= 1e-9:
                break
            sleeper(min(.25, remaining))
            after = _number(monotonic(), "fight_clock", now, 10**12)
            if after <= now:
                raise ProtocolError("fight_clock_stalled")
            previous = after
        previous = now
        yield final, proof, details
