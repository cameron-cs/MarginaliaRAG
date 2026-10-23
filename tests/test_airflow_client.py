"""The Airflow REST client against a mock Airflow (request shapes from Airflow 3.3's OpenAPI)."""

import json

import httpx
import pytest

from marginalia.api.airflow import Airflow, AirflowError

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return 'asyncio'


class MockAirflow:
    def __init__(self, expire_first_token=False):
        self.requests, self.logins, self.expire = [], 0, expire_first_token

    def __call__(self, request):
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.method, request.url.path, body))
        if request.url.path == '/auth/token':
            self.logins += 1
            if body['password'] != 'secret':
                return httpx.Response(401, json={'detail': 'Invalid credentials'})
            return httpx.Response(201, json={'access_token': f'token-{self.logins}'})
        if self.expire and request.headers['authorization'] == 'Bearer token-1':
            return httpx.Response(401, json={'detail': 'Token expired'})
        path = request.url.path
        if path == '/api/v2/dags/marginalia_ingest' and request.method == 'GET':
            return httpx.Response(200, json={'is_paused': False, 'timetable_summary': '0 * * * *',
                                             'next_dagrun_run_after': '2026-10-23T14:00:00Z'})
        if path.endswith('/dagRuns') and request.method == 'GET':
            runs = [{'dag_run_id': 'scheduled__1', 'state': 'success', 'duration': 12.5}]
            return httpx.Response(200, json={'dag_runs': runs, 'total_entries': 7})
        if path.endswith('/xcomEntries/return_value'):
            if '/missing/' in path:
                return httpx.Response(404, json={'detail': 'XCom not found'})
            return httpx.Response(200, json={'key': 'return_value', 'value': json.dumps({'books': [], 'reviews': []})})
        if '/logs/' in path:
            assert request.headers['accept'] == 'application/json'
            return httpx.Response(200, json={'content': [
                {'event': '::group::Log message source details'}, {'event': '/opt/airflow/logs/x.log'},
                {'event': '::endgroup::'},
                {'timestamp': '2026-10-23T07:03:32Z', 'event': 'indexed version 3', 'level': 'info', 'logger': 'task'}]})
        if path.endswith('/dagRuns') and request.method == 'POST':
            return httpx.Response(200, json={'dag_run_id': 'manual__1', 'state': 'queued', 'conf': body['conf']})
        if path.endswith('/missing'):
            return httpx.Response(404, json={'detail': 'DagRun not found'})
        if request.method == 'PATCH' and path == '/api/v2/dags/marginalia_ingest':
            return httpx.Response(200, json={'is_paused': body['is_paused']})
        return httpx.Response(200, json={'dag_run_id': 'manual__1', 'state': 'failed', 'task_instances': []})


def client(mock, password='secret'):
    return Airflow('http://airflow:8080', 'admin', password,
                   client=httpx.AsyncClient(transport=httpx.MockTransport(mock)))


async def test_logs_in_once_and_reads_the_schedule():
    mock = MockAirflow()
    af = client(mock)
    assert await af.schedule() == {'paused': False, 'schedule': '0 * * * *', 'next_run_at': '2026-10-23T14:00:00Z',
                                   'import_errors': False}
    await af.schedule()
    assert mock.logins == 1


async def test_expired_token_is_renewed_once():
    mock = MockAirflow(expire_first_token=True)
    assert (await client(mock).schedule())['schedule'] == '0 * * * *'
    assert mock.logins == 2


async def test_wrong_password_is_an_error():
    with pytest.raises(AirflowError) as err:
        await client(MockAirflow(), password='wrong').schedule()
    assert err.value.status == 401


async def test_request_shapes():
    mock = MockAirflow()
    af = client(mock)
    run = await af.trigger(force=['book-a'], note='by admin')
    assert run['run_id'] == 'manual__1' and run['conf'] == {'force': ['book-a'], 'only': []}
    run = await af.trigger(only=['book-b'])
    assert run['conf'] == {'force': [], 'only': ['book-b']}
    await af.stop_run('manual__1')
    await af.stop_task('manual__1', 'process.summarize', 3)
    await af.stop_task('manual__1', 'plan')
    await af.retry_task('manual__1', 'process.index')
    assert await af.set_paused(True) == {'paused': True}
    calls = [(m, p, b) for m, p, b in mock.requests if p != '/auth/token']
    assert calls == [
        ('POST', '/api/v2/dags/marginalia_ingest/dagRuns',
         {'logical_date': None, 'conf': {'force': ['book-a'], 'only': []}, 'note': 'by admin'}),
        ('POST', '/api/v2/dags/marginalia_ingest/dagRuns', {'logical_date': None, 'conf': {'force': [], 'only': ['book-b']}}),
        ('PATCH', '/api/v2/dags/marginalia_ingest/dagRuns/manual__1', {'state': 'failed'}),
        ('PATCH', '/api/v2/dags/marginalia_ingest/dagRuns/manual__1/taskInstances/process.summarize/3',
         {'new_state': 'failed'}),
        ('PATCH', '/api/v2/dags/marginalia_ingest/dagRuns/manual__1/taskInstances/plan', {'new_state': 'failed'}),
        ('POST', '/api/v2/dags/marginalia_ingest/clearTaskInstances',
         {'dag_run_id': 'manual__1', 'task_ids': ['process.index'], 'dry_run': False, 'only_failed': False,
          'include_downstream': True, 'reset_dag_runs': True}),
        ('PATCH', '/api/v2/dags/marginalia_ingest', {'is_paused': True}),
    ]


async def test_airflow_errors_carry_status_and_detail():
    with pytest.raises(AirflowError) as err:
        await client(MockAirflow()).stop_run('missing')
    assert (err.value.status, err.value.detail) == (404, 'DagRun not found')


async def test_runs_plan_and_logs():
    mock = MockAirflow()
    af = client(mock)
    runs, total = await af.runs(limit=5, state='failed', offset=10)
    assert total == 7 and runs[0]['duration'] == 12.5
    assert mock.requests[-1][1] == '/api/v2/dags/marginalia_ingest/dagRuns'
    assert await af.plan('manual__1') == {'books': [], 'reviews': []}
    assert await af.plan('missing') is None, 'no plan yet'
    lines = await af.task_log('manual__1', 'process.index', 2, map_index=1)
    assert lines == [{'at': None, 'level': None, 'logger': None, 'message': '/opt/airflow/logs/x.log'},
                     {'at': '2026-10-23T07:03:32Z', 'level': 'info', 'logger': 'task', 'message': 'indexed version 3'}]
    assert mock.requests[-1][1].endswith('/taskInstances/process.index/logs/2')
