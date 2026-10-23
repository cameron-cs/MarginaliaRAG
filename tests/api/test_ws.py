import pytest
from starlette.websockets import WebSocketDisconnect

from marginalia.api import events
from tests.api.conftest import make_admin, user


def _token(headers):
    return headers['Authorization'].split(' ', 1)[1]


def _user_id(client, headers):
    return client.get('/api/auth/me', headers=headers).json()['id']


def _publish(client, svc, ev, user_ids):
    client.portal.call(events.publish, svc.redis, ev, user_ids)


def test_user_receives_only_their_events(client, svc):
    alice, bob = user(client, 'alice@example.com'), user(client, 'bob@example.com')
    alice_id, bob_id = _user_id(client, alice), _user_id(client, bob)
    with client.websocket_connect('/api/ws') as ws:
        ws.send_json({'type': 'auth', 'token': _token(alice)})
        assert ws.receive_json() == {'type': 'ready', 'user': alice_id, 'role': 'user'}
        _publish(client, svc, events.event('book.status', status='checking', message='for bob'), [bob_id])
        _publish(client, svc, events.event('book.status', status='ready', message='for alice'), [alice_id])
        received = ws.receive_json()
        assert (received['type'], received['message']) == ('book.status', 'for alice')


def test_admin_receives_everyone_s_events_once(client, svc):
    reader = user(client)
    admin = user(client, 'boss@example.com')
    make_admin(client, svc, 'boss@example.com')
    reader_id, admin_id = _user_id(client, reader), _user_id(client, admin)
    with client.websocket_connect('/api/ws') as ws:
        ws.send_json({'type': 'auth', 'token': _token(admin)})
        assert ws.receive_json()['role'] == 'admin'
        _publish(client, svc, events.event('question.status', status='thinking', message='reader'), [reader_id])
        _publish(client, svc, events.event('question.status', status='thinking', message='admin'), [admin_id])
        assert [ws.receive_json()['message'] for _ in range(2)] == ['reader', 'admin']


@pytest.mark.parametrize('first', [
    {'type': 'auth', 'token': 'nonsense'},
    {'type': 'hello'},
    'not json',
])
def test_bad_or_missing_token_closes_with_4401(client, first):
    with client.websocket_connect('/api/ws') as ws:
        if isinstance(first, dict):
            ws.send_json(first)
        else:
            ws.send_text(first)
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 4401
