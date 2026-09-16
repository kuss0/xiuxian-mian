"""Explicit native resource baselines for the legacy business-policy tests."""

from model import profile_observation, state as state_module
from model.features import yinluo
from model.verified_event import VerifiedGameEvent


CHAT = -10031001
BOT = 31099


def native_reply(identity_id, command, text, now, *, root=10000, msg_id=None, command_at=None, edited=False, chat=CHAT):
    return VerifiedGameEvent(
        event_type="edit" if edited else "message", chat_id=chat, msg_id=msg_id or root + 1,
        sender_id=BOT, text=text, identity_id=identity_id, family="", root_msg_id=root,
        route_source="native_test", reply_to_sender_id=identity_id, server_event_at=now,
        reply_context={
            "send_as_id": identity_id, "chat_id": chat, "root_msg_id": root, "reply_to_msg_id": root,
            "reply_to_command": command, "reply_to_sender_id": identity_id,
            "reply_to_server_at": now - 1 if command_at is None else command_at, "reply_to_command_edited": False,
        },
    )


def native_logs(*events):
    rows, roots = [], set()
    for received in events:
        context = received.reply_context
        root_key = received.chat_id, received.root_msg_id
        if root_key not in roots:
            roots.add(root_key)
            rows.append({
                "event_type": "message", "chat_id": received.chat_id, "message_id": received.root_msg_id,
                "sender_id": context["reply_to_sender_id"], "text": context["reply_to_command"],
                "reply_to_msg_id": 0, "server_event_at": context["reply_to_server_at"],
            })
        rows.append({
            "event_type": received.event_type, "chat_id": received.chat_id, "message_id": received.msg_id,
            "sender_id": received.sender_id, "text": received.text, "reply_to_msg_id": received.root_msg_id,
            "server_event_at": received.server_event_at,
        })
    return rows


def seed_resources(identity_id, now, *, sha=2000, stocks=None, cultivation=None, profile_msg_id=10, panel_root=20):
    state_module.set_game_group_id(CHAT)
    state_module.set_game_bot_ids([BOT])
    state_module.set_identity_account(identity_id, identity_id)
    state_module._meta_state["global_enabled"] = True
    state_module.update_send_as_profile(identity_id, enabled=True)
    if cultivation is None:
        cultivation = state_module.get_send_as_profile(identity_id).get("xiuwei_current", 0) or 50000
    profile_observation.apply_profile_observation(identity_id, {"xiuwei_current": cultivation}, now - 120, evidence={
        "source": "telegram", "chat_id": CHAT, "msg_id": profile_msg_id, "edited": False,
    })
    stocks = stocks if stocks is not None else {"妖兽精魄": 4, "凶兽戾魄": 4}
    text = f"【道友的阴罗幡】\n煞气池: {sha} / 350000 (0%)\n魂魄储备:\n"
    text += "\n".join(f"- {name}: {count} 缕" for name, count in stocks.items())
    return yinluo.observe_yinluo_resources(native_reply(identity_id, ".我的阴罗幡", text, now - 60, root=panel_root), now=now)


def apply_native(identity_id, command, text, now, **source):
    event = native_reply(identity_id, command, text, now, **source)
    return yinluo.apply_yinluo_passive(text, now=now, event_context=event)
