import json

import pytest

from model import ui


class Writer:
    def __init__(self):
        self.data = b''

    def write(self, data):
        self.data += data


def response(*, action='concubine_voyage_status', **changes):
    extra = {'command': '.远航状态', 'reason': 'unparsed_voyage_status',
             'raw_message': '侍妾已归来\n完整的附加行', 'init_data': 'fixture_secret',
             'session_token': 'fixture_secret'}
    extra.update(changes)
    writer = Writer()
    ui._write_cave_public_entry_result(writer, False, '未解析', action=action, extra=extra)
    header, body = writer.data.split(b'\r\n\r\n', 1)
    return header, json.loads(body)


def test_owned_unparsed_read_exposes_game_text_without_becoming_success():
    header, body = response()
    assert b'400 Bad Request' in header
    assert body['ok'] is False and body['error'] == '未解析'
    assert body['diagnostic']['raw_message'] == '侍妾已归来\n完整的附加行'
    assert 'fixture_secret' not in json.dumps(body)
    assert 'snapshot' not in body


def test_diagnostic_removes_authentication_links_from_game_text():
    _, body = response(raw_message='侍妾归来\nhttps://fixture.invalid/?tgWebAppData=fixture_secret')
    assert 'fixture_secret' not in json.dumps(body)
    assert body['diagnostic']['raw_message'].endswith('[link]')


@pytest.mark.parametrize('changes', [
    {'action': 'concubine_voyage_return'}, {'command': '.远航归来'},
    {'reason': 'identity_mismatch'}, {'raw_message': 'x' * 4097}, {'raw_message': None},
])
def test_mutations_and_unverified_or_unbounded_errors_keep_the_original_envelope(changes):
    _, body = response(**changes)
    assert body == {'ok': False, 'error': '未解析'}
