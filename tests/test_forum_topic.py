import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model.forum_topic import event_topic_id, resolve_topic_id


def header(**kwargs):
    kwargs.setdefault("reply_to_top_id", 0)
    kwargs.setdefault("reply_to_msg_id", 0)
    return SimpleNamespace(**kwargs)


class ResolveTopicIdTests(unittest.TestCase):
    def test_reply_inside_a_topic_uses_top_id(self):
        self.assertEqual(
            458347,
            resolve_topic_id(header(reply_to_top_id=458347, reply_to_msg_id=12023777)),
        )

    def test_direct_post_is_recognised_via_the_forum_flag(self):
        """直接发进话题：top_id 为空，话题号落在 reply_to_msg_id。"""
        self.assertEqual(
            458347,
            resolve_topic_id(header(reply_to_msg_id=458347, forum_topic=True)),
        )

    def test_direct_post_is_recognised_via_a_known_topic_id(self):
        self.assertEqual(
            458347,
            resolve_topic_id(header(reply_to_msg_id=458347), known_topic_ids=(458347,)),
        )

    def test_plain_reply_in_a_non_forum_group_is_not_a_topic(self):
        """保守：非 forum 群里 reply_to_msg_id 就是被回复的消息，不能当话题号。"""
        self.assertEqual(0, resolve_topic_id(header(reply_to_msg_id=12023777)))
        self.assertEqual(
            0,
            resolve_topic_id(header(reply_to_msg_id=12023777), known_topic_ids=(458347,)),
        )

    def test_forum_topic_id_attribute_is_honoured(self):
        self.assertEqual(99, resolve_topic_id(header(forum_topic_id=99)))

    def test_missing_header_is_zero(self):
        self.assertEqual(0, resolve_topic_id(None))
        self.assertEqual(0, resolve_topic_id(header()))

    def test_garbage_values_do_not_raise(self):
        self.assertEqual(0, resolve_topic_id(header(reply_to_top_id="x", reply_to_msg_id=None)))


class EventTopicIdTests(unittest.TestCase):
    def test_reads_message_reply_to_first(self):
        event = SimpleNamespace(
            message=SimpleNamespace(reply_to=header(reply_to_top_id=458347)),
            reply_to=header(reply_to_top_id=1),
        )
        self.assertEqual(458347, event_topic_id(event))

    def test_falls_back_to_event_reply_to(self):
        event = SimpleNamespace(message=None, reply_to=header(reply_to_top_id=777))
        self.assertEqual(777, event_topic_id(event))


if __name__ == "__main__":
    unittest.main()
