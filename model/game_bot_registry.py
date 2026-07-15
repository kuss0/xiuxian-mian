from dataclasses import dataclass


@dataclass(frozen=True)
class GameBotCandidateDecision:
    sender_id: int
    should_notify: bool = False
    ready_to_learn: bool = False
    already_decided: bool = False


class GameBotCandidateRegistry:
    """Own candidate evidence without performing persistence or network I/O."""

    def __init__(
        self,
        *,
        ttl_sec,
        min_replies,
        min_players,
        min_commands,
    ):
        self.ttl_sec = max(1.0, float(ttl_sec))
        self.min_replies = max(1, int(min_replies))
        self.min_players = max(1, int(min_players))
        self.min_commands = max(1, int(min_commands))
        self.candidates = {}

    @staticmethod
    def _evidence_set(item, key):
        value = (item or {}).get(key)
        if isinstance(value, set):
            return value
        if isinstance(value, (list, tuple)):
            normalized = {entry for entry in value if entry}
        elif value:
            normalized = {value}
        else:
            normalized = set()
        item[key] = normalized
        return normalized

    @staticmethod
    def _new_candidate(now):
        return {
            "count": 0,
            "first_seen": float(now),
            "last_seen": float(now),
            "notified": False,
            "decided": False,
            "learned": False,
            "players": set(),
            "families": set(),
            "commands": set(),
            "reply_to_ids": set(),
        }

    def observe(
        self,
        sender_id,
        *,
        now,
        family="",
        player_id=0,
        command_label="",
        reply_to_msg_id=0,
        known=False,
    ):
        sender_id = int(sender_id or 0)
        if sender_id == 0 or known:
            return GameBotCandidateDecision(sender_id=sender_id, already_decided=True)

        now = float(now)
        item = self.candidates.get(sender_id)
        if item is None or now - float(item.get("first_seen", now) or now) > self.ttl_sec:
            item = self._new_candidate(now)

        reply_to_ids = self._evidence_set(item, "reply_to_ids")
        normalized_reply_id = 0
        try:
            normalized_reply_id = int(reply_to_msg_id or 0)
        except (TypeError, ValueError):
            pass
        duplicate_reply = normalized_reply_id > 0 and normalized_reply_id in reply_to_ids
        if not duplicate_reply:
            item["count"] = int(item.get("count", 0) or 0) + 1
        item["last_seen"] = now

        if family:
            self._evidence_set(item, "families").add(str(family))
        if command_label:
            self._evidence_set(item, "commands").add(str(command_label))
        try:
            normalized_player_id = int(player_id or 0)
        except (TypeError, ValueError):
            normalized_player_id = 0
        if normalized_player_id:
            self._evidence_set(item, "players").add(normalized_player_id)
        if normalized_reply_id > 0:
            reply_to_ids.add(normalized_reply_id)

        should_notify = not bool(item.get("notified"))
        if should_notify:
            item["notified"] = True
        self.candidates[sender_id] = item

        players = self._evidence_set(item, "players")
        commands = self._evidence_set(item, "commands")
        ready = bool(
            not item.get("decided")
            and players
            and commands
            and int(item.get("count", 0) or 0) >= self.min_replies
            and len(players) >= self.min_players
            and len(commands) >= self.min_commands
        )
        return GameBotCandidateDecision(
            sender_id=sender_id,
            should_notify=should_notify,
            ready_to_learn=ready,
            already_decided=bool(item.get("decided")),
        )

    def evidence(self, sender_id, *, username=""):
        item = self.candidates.get(int(sender_id or 0)) or {}
        commands = sorted(
            str(value)
            for value in self._evidence_set(item, "commands")
            if str(value).strip()
        )
        families = sorted(
            str(value)
            for value in self._evidence_set(item, "families")
            if str(value).strip()
        )
        return {
            "username": str(username or "").strip(),
            "reply_count": int(item.get("count", 0) or 0),
            "player_count": len(self._evidence_set(item, "players")),
            "commands": commands or families,
        }

    def mark_decided(self, sender_id, *, learned=False):
        item = self.candidates.get(int(sender_id or 0))
        if not item:
            return False
        item["decided"] = True
        item["learned"] = bool(learned)
        return True

