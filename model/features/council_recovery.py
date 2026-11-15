"""Prove a manually completed council from bounded, existing local messages.

Recovery releases occupancy only. It never sends commands or invents rewards
for the configured party when the actual game party was different.
"""
import re

from . import council

NAMES = re.compile(r"@([^\s，。、：:；;（）()【】!?！？]+)")


def completed_manual_council(run, rows, bot_ids):
    if (run.get("kind") != "qipai" or run.get("phase") not in {"held", "interrupted"}
            or run.get("bound") or not run.get("members")):
        return None
    leader = run["members"][0]
    name = leader["username"].lower()
    opens = {a.get("msg_id") for a in run.get("attempts", {}).values()
             if a.get("command") == ".开密议" and a.get("status") == "sent" and a.get("msg_id")}
    if len(opens) != 1:
        return None
    beginning = float(run["started_at"])
    # A council takes minutes. Never infer ownership from a later day's room.
    scoped = [r for r in rows if r.get("chat_id") == run["chat_id"]
              and r.get("topic_id", 0) == run["topic_id"] and not r.get("forwarded")
              and beginning - 1 <= float(r.get("server_event_at") or 0) <= beginning + 3600]
    scoped.sort(key=lambda r: (float(r["server_event_at"]), int(r.get("message_id") or 0)))
    commands, actors, known = {}, {name: leader["identity_id"]}, []
    roster, recruited, started, nomination = [], False, None, None
    completed_round, commander = 0, name
    seen = set()
    for row in scoped:
        mid, text = int(row.get("message_id") or 0), str(row.get("text") or "").strip()
        signature = (mid, text)
        if signature in seen:
            continue
        seen.add(signature)
        reply, sender = int(row.get("reply_to_msg_id") or 0), row.get("sender_id")
        if sender not in bot_ids:
            if text in {".入议", ".开议", ".准", ".驳"} or text.startswith(".点将 "):
                commands[mid] = row
            continue
        if "召集密议" in text:
            if started:
                return None
            if reply in opens and [n.lower() for n in NAMES.findall(text)[:1]] == [name]:
                recruited = True
                roster = [name]
                known.append(mid)
            elif recruited:
                return None
        joined = re.search(r"@([^\s]+) 入议，现有\s*(\d+)\s*位长老：([^\n]+)", text)
        if joined and recruited and not started:
            listed = [n.lower() for n in NAMES.findall(joined[3])]
            command = commands.get(reply, {})
            if (command.get("text") != ".入议" or not listed or listed[0] != name
                    or int(joined[2]) != len(set(listed)) or len(listed) != len(set(listed))
                    or not set(roster).issubset(listed) or joined[1].lower() not in listed):
                return None
            actors[joined[1].lower()] = command["sender_id"]
            roster = listed
            known.append(mid)
        if "【七派密议·开议】" in text:
            command = commands.get(reply, {})
            if (started or not recruited or command.get("sender_id") != leader["identity_id"]
                    or command.get("text") != ".开议" or not 3 <= len(roster) <= 8
                    or any(n not in actors for n in roster)
                    or not re.search(r"第\s*1\s*关.*主帅\s*@" + re.escape(name) + r"\s", text, re.I)):
                return None
            started = {"command_id": reply, "message_id": mid}
            known.append(mid)
        if not started:
            continue
        nominated = re.search(r"【密议·第\s*(\d+)\s*关】主帅\s*@([^\s]+)\s*点将：", text)
        if nominated:
            number, actor = int(nominated[1]), nominated[2].lower()
            command = commands.get(reply, {})
            if (number != completed_round + 1 or actor not in roster or actor != commander
                    or command.get("sender_id") != actors.get(actor)
                    or not str(command.get("text") or "").startswith(".点将 ")):
                return None
            nomination = (number, float(row["server_event_at"]))
            known.append(mid)
        observation = council.observation(text)
        if observation:
            if not nomination or observation["round"] != nomination[0] or observation["round"] != completed_round + 1:
                return None
            completed_round = observation["round"]
            nomination_at = nomination[1]
            nomination = None
            known.append(mid)
            if "【七派密议·散议】" in text:
                count = re.search(r"(\d+)\s*位长老各得修为", text)
                command = commands.get(reply, {})
                if (not count or int(count[1]) != len(roster) or completed_round not in {3, 4, 5}
                        or command.get("text") not in {".准", ".驳"}
                        or command.get("sender_id") not in actors.values()
                        or float(command.get("server_event_at") or 0) < nomination_at):
                    return None
                return {"source": "local_message_chain", "opening_command_id": next(iter(opens)),
                        "start_command_id": started["command_id"], "start_message_id": started["message_id"],
                        "terminal_message_id": mid, "terminal_at": float(row["server_event_at"]),
                        "roster_names": roster, "actual_identity_ids": list(actors.values()),
                        "message_ids": known, "reward_accounted": False}
            next_commander = re.search(r"第\s*" + str(completed_round + 1) + r"\s*关.*主帅\s*@([^\s]+)\s", text)
            if not next_commander or next_commander[1].lower() not in roster:
                return None
            commander = next_commander[1].lower()
    return None
