"""The admin's view of readers' questions: the log, its filters, one question, and the stats."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from marginalia.api.db import Question, User, UserBook
from tests.api.conftest import make_admin, upload, user

T0 = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)

QUESTIONS = [   # text, status, answered_by, details, seconds to finish
    ('Who is Tom?', 'done', 'agent', {'supported': True, 'tool_calls': [{'name': 'search_passages'}] * 2,
                                      'tokens': {'total': {'total': 9000, 'per_second': 50.0}}}, 60),
    ('who is tom', 'done', 'cache', {'supported': True, 'cached': {'kind': 'exact', 'similarity': 1.0}}, 2),
    ('How do I bake bread?', 'refused', 'guard', {'refused': 'off_topic'}, 1),
    ('Where is the axe?', 'failed', 'agent', {}, 30),
    ('Where is the river?', 'queued', None, {'sampling': {'temperature': 0.9}}, None),
    ('Who is Hagrid?', 'refused', 'guard', {'refused': 'not_in_book'}, 1),
    ('What happens at the end?', 'done', 'agent', {'supported': False, 'tool_calls': [{'name': 'x'}] * 4,
                                                   'tokens': {'total': {'total': 3000, 'per_second': 70.0}}}, 120),
]


async def _seed(svc, email):
    async with svc.db.sessions() as s:
        reader = (await s.execute(select(User).where(User.email == email))).scalar_one()
        ub = (await s.execute(select(UserBook).where(UserBook.user_id == reader.id))).scalar_one()
        for i, (text, state, by, details, secs) in enumerate(QUESTIONS):
            created = T0 + timedelta(minutes=i)
            s.add(Question(user_id=reader.id, user_book_id=ub.id, text=text, status=state, answered_by=by,
                           details=details, created_at=created,
                           finished_at=created + timedelta(seconds=secs) if secs is not None else None,
                           error='Ollama is down' if state == 'failed' else None))
        await s.commit()


def _setup(client, svc):
    upload(client, user(client, 'reader@example.com'))
    client.portal.call(_seed, svc, 'reader@example.com')
    admin = user(client, 'boss@example.com')
    make_admin(client, svc, 'boss@example.com')
    return admin


def test_the_log_lists_every_question_newest_first(client, svc):
    admin = _setup(client, svc)
    body = client.get('/api/admin/questions', headers=admin).json()
    assert [q['text'] for q in body['items']] == [q[0] for q in reversed(QUESTIONS)]
    first = body['items'][-1]
    assert (first['user'], first['book'], first['seconds'], first['searches'], first['tokens']) == \
        ('reader@example.com', 'The Test Book', 60.0, 2, 9000)
    assert body['next'] is None


def test_filters(client, svc):
    admin = _setup(client, svc)
    texts = lambda **params: [q['text'] for q in client.get('/api/admin/questions', headers=admin,  # noqa: E731
                                                            params=params).json()['items']]
    assert texts(outcome='cache') == ['who is tom']
    assert texts(outcome='refused') == ['Who is Hagrid?', 'How do I bake bread?']
    assert texts(outcome='agent') == ['What happens at the end?', 'Who is Tom?']
    assert texts(outcome='failed') == ['Where is the axe?']
    assert texts(outcome='active') == ['Where is the river?']
    assert texts(search='TOM') == ['who is tom', 'Who is Tom?']
    assert texts(search='100%') == []
    assert texts(user='nobody') == []
    assert client.get('/api/admin/questions', headers=admin, params={'outcome': 'odd'}).status_code == 422


def test_pages_follow_the_cursor(client, svc):
    admin = _setup(client, svc)
    page1 = client.get('/api/admin/questions', headers=admin, params={'limit': 4}).json()
    page2 = client.get('/api/admin/questions', headers=admin, params={'limit': 4, 'before': page1['next']}).json()
    assert len(page1['items']) == 4 and len(page2['items']) == 3 and page2['next'] is None
    assert {q['id'] for q in page1['items']}.isdisjoint(q['id'] for q in page2['items'])


def test_one_question_in_full(client, svc):
    admin = _setup(client, svc)
    [refused] = client.get('/api/admin/questions', headers=admin, params={'search': 'Hagrid'}).json()['items']
    full = client.get(f"/api/admin/questions/{refused['id']}", headers=admin).json()
    assert full['refused'] == 'not_in_book' and full['details'] == {'refused': 'not_in_book'}
    assert client.get('/api/admin/questions/00000000-0000-0000-0000-000000000000', headers=admin).status_code == 404


def test_stats(client, svc):
    admin = _setup(client, svc)
    s = client.get('/api/admin/questions/stats', headers=admin, params={'hours': 24 * 90}).json()
    assert s['total'] == 7 and s['readers'] == 1
    assert s['outcomes'] == {'agent': 2, 'cache': 1, 'refused': 2, 'failed': 1, 'active': 1}
    assert s['refused'] == {'off_topic': 1, 'not_in_book': 1}
    assert s['cache_hit_rate'] == round(1 / 3, 3) and s['supported_rate'] == 0.5
    assert s['agent_seconds']['max'] == 120.0 and s['mean_searches'] == 3
    assert s['tokens'] == {'total': 12000, 'mean': 6000, 'max': 9000, 'min': 3000, 'per_second': 60.0}


def test_readers_cannot_see_the_log(client, svc):
    reader = user(client, 'reader@example.com')
    assert client.get('/api/admin/questions', headers=reader).status_code == 403
