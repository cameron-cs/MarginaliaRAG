"""The admin's controls over the Airflow batch, through the API (Airflow faked)."""

import pytest

from tests.api.conftest import make_admin, upload, user


@pytest.fixture
def admin(client, svc):
    headers = user(client, 'boss@example.com')
    make_admin(client, svc, 'boss@example.com')
    return headers


def test_pipeline_overview(client, admin):
    body = client.get('/api/admin/pipeline', headers=admin).json()
    assert body['schedule'] == '0 * * * *' and body['next_run_at'] == '2026-10-23T14:00:00Z'
    assert [r['run_id'] for r in body['runs']] == ['scheduled__2', 'scheduled__1'] and body['total'] == 2
    assert body['summary'] == {'by_state': {'failed': 1, 'success': 1}, 'mean_duration': 60.0, 'longest': 90.0}
    failed = client.get('/api/admin/pipeline', headers=admin, params={'state': 'failed'}).json()
    assert [r['run_id'] for r in failed['runs']] == ['scheduled__2']
    assert client.get('/api/admin/pipeline', headers=admin, params={'state': 'odd'}).status_code == 422


def test_runs_for_chosen_books_and_redos_need_known_books(client, svc, admin):
    book = upload(client, user(client)).json()['book']['book_id']
    r = client.post('/api/admin/pipeline/runs', headers=admin, json={'only': [book]})
    assert r.status_code == 202 and svc.airflow.calls[-1] == ('trigger', [], [book])
    client.post('/api/admin/pipeline/runs', headers=admin, json={'force': [book]})
    assert svc.airflow.calls[-1] == ('trigger', [book], [])
    r = client.post('/api/admin/pipeline/runs', headers=admin, json={'only': [book, 'missing-book']})
    assert r.status_code == 404 and 'missing-book' in r.json()['detail']


def test_waiting_work_lists_books_and_reviews(client, svc, admin):
    upload(client, user(client), user_prompt='Answer briefly.')
    body = client.get('/api/admin/pipeline/waiting', headers=admin).json()
    [book] = body['books']
    assert (book['title'], book['readers'], book['force']) == ('The Test Book', 1, False)
    assert book['steps'] == ['structure', 'cover', 'summarize', 'index']
    [review] = body['reviews']
    assert (review['user'], review['title']) == ('reader@example.com', 'The Test Book')


def test_mapped_tasks_are_labelled_with_their_books(client, svc, admin):
    book = upload(client, user(client), user_prompt='Answer briefly.').json()['book']
    async def review_id():
        from sqlalchemy import select
        from marginalia.api.db import UserBook
        async with svc.db.sessions() as s:
            return str((await s.execute(select(UserBook.id))).scalar_one())
    ub = client.portal.call(review_id)
    svc.airflow.plan_value = {'reviews': [ub], 'books': [{'book_id': book['book_id'], 'force': False}]}
    svc.airflow.task_list = [{'task_id': 'plan', 'map_index': -1, 'state': 'success'},
                             {'task_id': 'review', 'map_index': 0, 'state': 'success'},
                             {'task_id': 'process.index', 'map_index': 0, 'state': 'failed'}]
    tasks = client.get('/api/admin/pipeline/runs/manual__1/tasks', headers=admin).json()
    assert 'label' not in tasks[0]
    assert tasks[1]['label'] == 'reader@example.com · The Test Book'
    assert (tasks[2]['label'], tasks[2]['book_id']) == ('The Test Book', book['book_id'])


def test_task_logs(client, svc, admin):
    r = client.get('/api/admin/pipeline/runs/manual__1/tasks/plan/logs', headers=admin)
    assert r.status_code == 200 and r.json()['try_number'] == 1
    assert r.json()['lines'][0]['message'] == 'indexed version 3'
    assert svc.airflow.calls[-1] == ('task_log', 'manual__1', 'plan', 1, -1)
    assert client.get('/api/admin/pipeline/runs/manual__1/tasks/nope/logs', headers=admin).status_code == 404
    client.get('/api/admin/pipeline/runs/manual__1/tasks/process.index/logs?map_index=2&try_number=3', headers=admin)
    assert svc.airflow.calls[-1] == ('task_log', 'manual__1', 'process.index', 3, 2)


@pytest.mark.parametrize('method,path,body,call', [
    ('post', '/api/admin/pipeline/runs', {}, ('trigger', [], [])),
    ('get', '/api/admin/pipeline/runs/manual__1/tasks', None, ('plan', 'manual__1')),
    ('post', '/api/admin/pipeline/runs/manual__1/stop', None, ('stop_run', 'manual__1')),
    ('post', '/api/admin/pipeline/runs/manual__1/tasks/process.summarize/stop?map_index=2', None,
     ('stop_task', 'manual__1', 'process.summarize', 2)),
    ('post', '/api/admin/pipeline/runs/manual__1/tasks/plan/retry', None, ('retry_task', 'manual__1', 'plan')),
    ('post', '/api/admin/pipeline/pause', None, ('set_paused', True)),
    ('post', '/api/admin/pipeline/resume', None, ('set_paused', False)),
])
def test_controls_call_airflow(client, svc, admin, method, path, body, call):
    r = getattr(client, method)(path, headers=admin, **({'json': body} if body is not None else {}))
    assert r.status_code in (200, 202), r.text
    assert svc.airflow.calls[-1] == call


def test_only_admins(client):
    reader = user(client)
    assert client.get('/api/admin/pipeline', headers=reader).status_code == 403
    assert client.post('/api/admin/pipeline/runs', headers=reader, json={}).status_code == 403


def test_airflow_down_is_a_503(client, svc, admin):
    svc.airflow.fail = True
    r = client.post('/api/admin/pipeline/runs', headers=admin, json={})
    assert r.status_code == 503 and 'unreachable' in r.json()['detail']


def test_airflow_not_configured_is_a_503(client, svc, admin):
    svc.airflow = None
    r = client.get('/api/admin/pipeline', headers=admin)
    assert r.status_code == 503 and 'not configured' in r.json()['detail']
