import asyncio
import copy
from types import SimpleNamespace

import pytest

from model import app, state as state_module
from model.features import concubine, passive_inbox
from tests import test_concubine_fragment_actions as fragment_tests
from tests.test_concubine_fragment_actions import ACCOUNT, BOT, CHAT, ID, NAME, NOW, ROOT


base_env = fragment_tests.env
FIRST = "\u3010\u5760\u9b54\u5fc3\u52ab\u00b7\u7b2c\u4e00\u8f6e\u3011"
ACK1 = "\u3010\u5760\u9b54\u5fc3\u52ab\u00b7\u7b2c1\u8f6e\u5df2\u5b9a\u3011"
SECOND = "\u3010\u5760\u9b54\u5fc3\u52ab\u00b7\u7b2c2\u8f6e\u3011"
ACK2 = "\u3010\u5760\u9b54\u5fc3\u52ab\u00b7\u7b2c2\u8f6e\u5df2\u5b9a\u3011"
THIRD = "\u3010\u5760\u9b54\u5fc3\u52ab\u00b7\u7b2c3\u8f6e\u3011"
SETTLED = "\u3010\u5760\u9b54\u5fc3\u52ab\u00b7\u7ed3\u7b97\u3011"
OPTIONS = "\u8bf7\u56de\u590d\u672c\u6d88\u606f .\u7a33 / .\u72e0 / .\u9a97 \u8fdb\u884c\u6289\u62e9\uff08\u51713\u8f6e\uff09\u3002"
CONTINUE = "\u5e7b\u5883\u518d\u53d8\uff0c\u8bf7\u7ee7\u7eed\u56de\u590d .\u7a33 / .\u72e0 / .\u9a97\u3002"
CHOICES = "\u4e09\u8f6e\u6289\u62e9\uff1a\u7a33 / \u7a33 / \u7a33"
CULTIVATION = "\u4fee\u4e3a\u7ed3\u7b97\uff1a+823"
AFFINITY = "\u60c5\u7f18\u7ed3\u7b97\uff1a+7"
DEMON = "\u5fc3\u9b54\u503c\u7ed3\u7b97\uff1a-5\uff08\u5f53\u524d 0\uff09"
SUCCESS = "\n".join([SETTLED, CHOICES, CULTIVATION, AFFINITY, DEMON])
COOLDOWN = "\u5fc3\u52ab\u4f59\u6ce2\u672a\u6563\uff0c\u8bf7\u5728 2\u5c0f\u65f63\u5206\u949f4\u79d2 \u540e\u518d\u8bd5\u3002"


@pytest.fixture
def env(base_env, monkeypatch):
    base_env.identity.update(
        concubine_enabled=False, concubine_tianji_enabled=False, concubine_heart_enabled=True,
        concubine_voyage_enabled=False, concubine_affinity=300, concubine_phase="heart_choice_reply_pending",
        concubine_heart_msg_id=ROOT, concubine_heart_prompt_msg_id=ROOT + 1, concubine_heart_round=3,
        concubine_heart_choice_prompt_msg_id=ROOT + 1, concubine_heart_choice_round=3,
        concubine_heart_choice_sent_at=NOW - 1, concubine_heart_due_at=NOW + 600,
        next_concubine_time=NOW + 120,
    )
    return base_env


def reply(text, now=NOW):
    with state_module.use_identity(ID):
        return asyncio.run(concubine.handle_concubine_heart_reply(
            text, now, SimpleNamespace(id=ROOT, chat_id=CHAT, sender_id=ID, raw_text=concubine.CMD_CONCUBINE_HEART),
            matched_family="concubine_heart", current_msg_id=ROOT + 1,
            current_chat_id=CHAT, observed_at=now, reply_context={"sender_id": BOT},
        ))


BAD_TEXTS = [
    FIRST, ACK1 + "\n" + SECOND, SECOND + "\n" + CONTINUE,
    FIRST + "\n" + OPTIONS.replace(" / .\u9a97", ""),
    FIRST + "\n" + OPTIONS + "\n" + ACK1 + "\n" + SECOND + "\n" + CONTINUE,
    SETTLED, SUCCESS.replace(CHOICES + "\n", ""), SUCCESS.replace(CULTIVATION + "\n", ""),
    SUCCESS.replace(AFFINITY + "\n", ""), SUCCESS.replace("\n" + DEMON, ""),
    SUCCESS + "\n" + AFFINITY, SUCCESS.replace("+7", "+7,,0"),
    SUCCESS.replace("+823", "unknown"), SUCCESS.replace("\u7a33 / \u7a33 / \u7a33", "\u7a33 / \u7a33"),
    SUCCESS.replace("\u5f53\u524d 0", "\u5f53\u524d -1"),
    SUCCESS + "\n" + FIRST + "\n" + OPTIONS, "quoted:\n" + SUCCESS,
    "\u5fc3\u52ab\u4f59\u6ce2\u672a\u6563", COOLDOWN.replace("2\u5c0f\u65f63\u5206\u949f4\u79d2", "unknown"),
    "unrecognized", "\u6df1\u5ea6\u95ed\u5173\u603b\u7ed3", SUCCESS + "\n" + COOLDOWN,
]


@pytest.mark.parametrize("text", BAD_TEXTS)
def test_partial_conflicting_or_unknown_heart_reply_preserves_active_chain(env, text):
    before = copy.deepcopy(env.identity)
    assert concubine.heart_contract.parse(text, NOW) is None
    assert not reply(text)
    assert env.identity == before
    env.save.assert_not_called()
    env.send.assert_not_awaited()


@pytest.mark.parametrize("text", [SETTLED, SUCCESS, SUCCESS.replace("+7", "+7,,0")])
def test_unowned_passive_heart_text_cannot_bypass_the_active_handler(env, text):
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        assert not asyncio.run(passive_inbox.handle_passive_module_card(
            text, now=NOW, reply_context={"send_as_id": ID, "family": "concubine_heart"},
            event=SimpleNamespace(id=ROOT + 1, chat_id=CHAT, sender_id=BOT, server_event_at=NOW),
            event_type="message",
        ))
    assert env.identity == before


@pytest.mark.parametrize("text,round_no", [(FIRST + "\n" + OPTIONS, 1),
                                        (ACK1 + "\n" + SECOND + "\n" + CONTINUE, 2),
                                        (ACK2 + "\n" + THIRD + "\n" + CONTINUE, 3)])
def test_complete_heart_round_prompts_parse_without_assuming_ownership(env, text, round_no):
    before = copy.deepcopy(env.identity)
    assert concubine.heart_contract.parse(text, NOW)["round"] == round_no
    assert not reply(text)
    assert env.identity == before


def test_complete_heart_settlement_keeps_supported_business_result(env):
    facts = concubine.heart_contract.parse(SUCCESS, NOW)
    assert facts["affinity_delta"] == 7
    assert facts["due_at"] == NOW + concubine.CONCUBINE_HEART_CD_SEC + concubine.CD_BUFFER_SEC


def test_heart_explicit_cooldown_remains_authoritative(env):
    assert concubine.heart_contract.parse(COOLDOWN, NOW)["due_at"] == NOW + 2 * 3600 + 3 * 60 + 4 + concubine.CD_BUFFER_SEC


@pytest.mark.parametrize("style", ["plain", "markdown", "crlf"])
def test_heart_parser_returns_bounded_signed_facts_without_mutation(env, style):
    text = SUCCESS.replace("+823", "-1,823").replace("+7", "-7")
    if style == "markdown":
        text = "\n".join("**" + line + "**" for line in text.splitlines())
    elif style == "crlf":
        text = text.replace("\n", "\r\n")
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        parsed = concubine.heart_contract.parse(text, NOW)
    assert parsed is not None
    assert parsed["outcome"] == "settlement"
    assert parsed["cultivation_delta"] == -1823 and parsed["affinity_delta"] == -7
    assert parsed["demon_delta"] == -5 and parsed["demon_current"] == 0
    assert parsed["due_at"] == NOW + concubine.CONCUBINE_HEART_CD_SEC + concubine.CD_BUFFER_SEC
    assert env.identity == before


@pytest.mark.parametrize("value", ["7,,0", "nan", "1e6", "1.5", "9" * 30, "++7", "7 units"])
@pytest.mark.parametrize("label,old", [(AFFINITY, "+7"), (CULTIVATION, "+823"), (DEMON, "-5")])
def test_invalid_heart_numbers_never_partially_apply(env, label, old, value):
    text = SUCCESS.replace(label, label.replace(old, value))
    before = copy.deepcopy(env.identity)
    assert concubine.heart_contract.parse(text, NOW) is None
    assert not reply(text)
    assert env.identity == before


@pytest.mark.parametrize("clock", [True, False, None, "1700000500", float("nan"), float("inf"), 0, -1])
def test_heart_parser_rejects_invalid_event_clock(env, clock):
    with state_module.use_identity(ID):
        assert concubine.heart_contract.parse(SUCCESS, clock) is None


def test_untimed_heart_in_progress_is_not_a_fabricated_cooldown(env):
    before = copy.deepcopy(env.identity)
    facts = concubine.heart_contract.parse(concubine.heart_contract.PROGRESS + "\u3002", NOW)
    assert facts["outcome"] == "in_progress" and facts["due_at"] == 0
    assert env.identity == before
    env.send.assert_not_awaited()


@pytest.mark.parametrize("prompt_id", [0, False, "88002", None, -1])
def test_round_requires_actual_integer_prompt_id_not_command_fallback(env, prompt_id):
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        assert not asyncio.run(concubine.handle_concubine_heart_reply(
            FIRST + "\n" + OPTIONS, NOW,
            SimpleNamespace(id=ROOT, raw_text=concubine.CMD_CONCUBINE_HEART),
            matched_family="concubine_heart", current_msg_id=prompt_id,
        ))
    assert env.identity == before


@pytest.mark.parametrize("route", ["native", "passive"])
@pytest.mark.parametrize("text", [SETTLED, SUCCESS.replace(AFFINITY + "\n", ""), FIRST, COOLDOWN.split("\uff0c")[0]])
def test_native_and_passive_partial_heart_text_cannot_change_business_state(env, route, text):
    before = copy.deepcopy(env.identity)
    context = {"send_as_id": ID, "account_id": ACCOUNT, "chat_id": CHAT, "family": "concubine_heart",
               "root_msg_id": ROOT, "reply_to_msg_id": ROOT, "sender_id": BOT}
    event = SimpleNamespace(id=ROOT + 1, chat_id=CHAT, sender_id=BOT, server_event_at=NOW)
    if route == "native":
        asyncio.run(app._handle_routed_reply_event(
            event, text, NOW + 1,
            SimpleNamespace(id=ROOT, chat_id=CHAT, sender_id=ID, raw_text=concubine.CMD_CONCUBINE_HEART), context,
        ))
    else:
        asyncio.run(passive_inbox.handle_passive_module_card(
            text, now=NOW + 1, reply_context=context, event=event, event_type="message",
        ))
    assert env.identity == before


@pytest.mark.parametrize("partner", [NAME, "foreign"])
def test_heart_parser_retains_voyage_partner_for_the_ownership_layer(env, partner):
    text = f"\u4f8d\u59be\u3010{partner}\u3011\u6b63\u5728\u8fdc\u822a\u9014\u4e2d\uff0c\u6682\u65e0\u6cd5\u5171\u5386\u5fc3\u52ab\u3002"
    before = copy.deepcopy(env.identity)
    assert concubine.heart_contract.parse(text, NOW)["voyage"]["partner"] == partner
    assert env.identity == before


@pytest.mark.parametrize("text", ["\u5fc3\u52ab\u951a\u70b9\u5df2\u6563",
                                 "quoted: \u5fc3\u52ab\u951a\u70b9\u5df2\u6563\uff0c\u9700\u91cd\u65b0\u5f15\u52a8\u5929\u52ab\u3002",
                                 "\u5fc3\u52ab\u951a\u70b9\u5df2\u6563\uff0c\u9700\u91cd\u65b0\u5f15\u52a8\u5929\u52ab\u3002\n" + SUCCESS])
def test_anchor_loss_recovery_cannot_bypass_complete_text_validation(env, text):
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        assert concubine.heart_contract.parse(text, NOW) is None
        assert not concubine._is_heart_anchor_lost_text(text)
    assert env.identity == before
