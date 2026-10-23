"""The API against in-memory SQLite and fakeredis: no services needed."""

import fakeredis
import pytest
from fastapi.testclient import TestClient

from marginalia.api.app import build_services, create_app
from marginalia.api.db import Database
from marginalia.config import Settings
from tests.pdfs import chapter, make_book

PASSWORD = 'correct horse battery'


def make_settings(tmp_path, **overrides):
    # No configured admin unless a test asks for one (.env may name one for the real stack).
    values = {'jwt_secret': 'test-secret-' * 4, 'database_url': 'sqlite+aiosqlite://',
              'library_dir': str(tmp_path / 'library'), 'admin_email': '', 'admin_password': '', **overrides}
    return Settings(**values)


class FakeAirflow:
    """Records the admin's pipeline calls; `fail` makes Airflow unreachable."""

    def __init__(self):
        self.calls, self.fail, self.paused = [], False, False
        self.plan_value = None
        self.task_list = [{'task_id': 'plan', 'map_index': -1, 'state': 'success', 'try_number': 1}]

    async def _do(self, name, *args, result=None):
        if self.fail:
            raise ConnectionError('Airflow is down')
        self.calls.append((name, *args))
        return result

    async def schedule(self):
        return await self._do('schedule', result={'paused': self.paused, 'schedule': '0 * * * *',
                                                  'next_run_at': '2026-10-23T14:00:00Z', 'import_errors': False})

    async def runs(self, limit=10, state=None, offset=0):
        runs = [{'run_id': 'scheduled__2', 'state': 'failed', 'duration': 30.0},
                {'run_id': 'scheduled__1', 'state': 'success', 'duration': 90.0}]
        runs = [r for r in runs if state in (None, r['state'])]
        return await self._do('runs', result=(runs, len(runs)))

    async def trigger(self, force=(), note=None, only=()):
        return await self._do('trigger', list(force), list(only), result={'run_id': 'manual__1', 'state': 'queued'})

    async def plan(self, run_id):
        return await self._do('plan', run_id, result=self.plan_value)

    async def task_log(self, run_id, task_id, try_number, map_index=-1):
        return await self._do('task_log', run_id, task_id, try_number, map_index,
                              result=[{'at': '2026-10-23T07:03:32Z', 'level': 'info', 'logger': 'task',
                                       'message': 'indexed version 3'}])

    async def stop_run(self, run_id):
        return await self._do('stop_run', run_id, result={'run_id': run_id, 'state': 'failed'})

    async def tasks(self, run_id):
        return await self._do('tasks', run_id, result=[dict(t) for t in self.task_list])

    async def stop_task(self, run_id, task_id, map_index=-1):
        return await self._do('stop_task', run_id, task_id, map_index)

    async def retry_task(self, run_id, task_id):
        return await self._do('retry_task', run_id, task_id)

    async def set_paused(self, paused):
        self.paused = paused
        return await self._do('set_paused', paused, result={'paused': paused})


@pytest.fixture
def svc(tmp_path):
    settings = make_settings(tmp_path, max_upload_mb=5)
    return build_services(settings, db=Database(settings.database_url),
                          redis=fakeredis.aioredis.FakeRedis(decode_responses=True), airflow=FakeAirflow())


@pytest.fixture
def client(svc):
    with TestClient(create_app(svc)) as c:
        c.portal.call(svc.db.create_all)
        yield c


def signup(client, email='reader@example.com', password=PASSWORD):
    r = client.post('/api/auth/signup', json={'email': email, 'password': password})
    assert r.status_code == 201, r.text
    return r.json()


def login(client, email='reader@example.com', password=PASSWORD):
    r = client.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.text
    return {'Authorization': f"Bearer {r.json()['access_token']}"}


def user(client, email='reader@example.com'):
    signup(client, email)
    return login(client, email)


def make_admin(client, svc, email):
    from sqlalchemy import update
    from marginalia.api.db import User

    async def promote():
        async with svc.db.sessions() as s:
            await s.execute(update(User).where(User.email == email).values(role='admin'))
            await s.commit()
    client.portal.call(promote)


def book_pdf(title='The Test Book', n=3):
    topics = ['the river', 'the axe', 'the letter', 'the bridge']
    return make_book([chapter(f'Chapter {i}', topics[i - 1]) for i in range(1, n + 1)], title=title)


def upload(client, headers, data=None, filename='book.pdf', **prompts):
    return client.post('/api/books', headers=headers, files={'file': (filename, data or book_pdf(), 'application/pdf')},
                       data={k: v for k, v in prompts.items() if v is not None})


def stream_length(client, svc, queue):
    from marginalia.api.jobs import QUEUES
    return client.portal.call(svc.redis.xlen, QUEUES[queue])
