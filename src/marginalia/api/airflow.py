"""A small client for Airflow's REST API (v2, Airflow 3), for the admin's pipeline controls.

Authenticates with a username and password (`POST /auth/token`) and gets a new token when the
old one is refused. Only the calls the admin page needs are here; the request bodies follow the
API server's own OpenAPI schema (TriggerDAGRunPostBody, DAGRunPatchBody, PatchTaskInstanceBody,
ClearTaskInstancesBody).
"""

import json

import httpx

DAG_ID = 'marginalia_ingest'
_TASK_FIELDS = ('task_id', 'map_index', 'state', 'try_number', 'start_date', 'end_date', 'duration',
                'rendered_map_index', 'pool')


class AirflowError(RuntimeError):
    def __init__(self, status, detail):
        super().__init__(f'Airflow answered {status}: {detail}')
        self.status, self.detail = status, detail


class Airflow:
    def __init__(self, base_url, username, password, dag_id=DAG_ID, client=None, timeout=10):
        self.base_url, self.username, self.password, self.dag_id = base_url.rstrip('/'), username, password, dag_id
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._token = None

    async def _login(self):
        r = await self._client.post(f'{self.base_url}/auth/token',
                                    json={'username': self.username, 'password': self.password})
        if r.status_code >= 400:
            raise AirflowError(r.status_code, 'login failed')
        self._token = r.json()['access_token']

    async def _call(self, method, path, **kwargs):
        if self._token is None:
            await self._login()
        extra = kwargs.pop('headers', {})
        for attempt in (1, 2):
            r = await self._client.request(method, f'{self.base_url}{path}',
                                           headers={**extra, 'Authorization': f'Bearer {self._token}'}, **kwargs)
            if r.status_code == 401 and attempt == 1:
                await self._login()                     # the token expired: log in again once
                continue
            if r.status_code >= 400:
                try:
                    detail = r.json().get('detail', r.text)
                except ValueError:
                    detail = r.text
                raise AirflowError(r.status_code, detail)
            return r.json() if r.content else None

    @property
    def _dag(self):
        return f'/api/v2/dags/{self.dag_id}'

    # --- the schedule ----------------------------------------------------------------------

    async def schedule(self):
        d = await self._call('GET', self._dag)
        return {'paused': d['is_paused'], 'schedule': d.get('timetable_summary'),
                'next_run_at': d.get('next_dagrun_run_after'), 'import_errors': d.get('has_import_errors', False)}

    async def set_paused(self, paused):
        d = await self._call('PATCH', self._dag, json={'is_paused': paused})
        return {'paused': d['is_paused']}

    # --- runs ------------------------------------------------------------------------------

    @staticmethod
    def _run(r):
        return {'run_id': r['dag_run_id'], 'state': r['state'], 'run_type': r.get('run_type'),
                'start_date': r.get('start_date'), 'end_date': r.get('end_date'), 'duration': r.get('duration'),
                'conf': r.get('conf') or {}, 'note': r.get('note')}

    async def runs(self, limit=10, state=None, offset=0):
        """Newest first; `state` (queued, running, success, failed) narrows them."""
        params = {'order_by': '-run_after', 'limit': limit, 'offset': offset}
        if state:
            params['state'] = state
        d = await self._call('GET', f'{self._dag}/dagRuns', params=params)
        return [self._run(r) for r in d['dag_runs']], d.get('total_entries', len(d['dag_runs']))

    async def plan(self, run_id):
        """What the run's plan task decided ({'reviews': [...], 'books': [...]}), or None before it ran."""
        try:
            d = await self._call('GET', f'{self._dag}/dagRuns/{run_id}/taskInstances/plan/xcomEntries/return_value')
        except AirflowError as exc:
            if exc.status == 404:
                return None
            raise
        value = d.get('value')
        try:
            return json.loads(value) if isinstance(value, str) else value
        except ValueError:
            return None

    async def trigger(self, force=(), note=None, only=()):
        """Start a batch now. `force`: books to redo from scratch; `only`: process just these books
        (and review just their readers' prompts) instead of everything that is waiting."""
        body = {'logical_date': None, 'conf': {'force': list(force), 'only': list(only)}}
        if note:
            body['note'] = note
        return self._run(await self._call('POST', f'{self._dag}/dagRuns', json=body))

    async def stop_run(self, run_id):
        """Marking a run failed stops its running tasks and skips what was still waiting."""
        return self._run(await self._call('PATCH', f'{self._dag}/dagRuns/{run_id}', json={'state': 'failed'}))

    # --- tasks -----------------------------------------------------------------------------

    async def tasks(self, run_id):
        d = await self._call('GET', f'{self._dag}/dagRuns/{run_id}/taskInstances', params={'limit': 500})
        return [{k: t.get(k) for k in _TASK_FIELDS} for t in d['task_instances']]

    async def task_log(self, run_id, task_id, try_number, map_index=-1):
        """The task's log for one try, as [{'at', 'level', 'logger', 'message'}]."""
        d = await self._call('GET', f'{self._dag}/dagRuns/{run_id}/taskInstances/{task_id}/logs/{try_number}',
                             params={'map_index': map_index, 'full_content': 'true'},
                             headers={'Accept': 'application/json'})
        lines = []
        for entry in (d or {}).get('content') or []:
            if isinstance(entry, str):
                entry = {'event': entry}
            message = str(entry.get('event', ''))
            if message.startswith('::group::') or message == '::endgroup::':
                continue                   # Airflow's own folding markers
            lines.append({'at': entry.get('timestamp'), 'level': entry.get('level'), 'logger': entry.get('logger'),
                          'message': message})
        return lines

    async def stop_task(self, run_id, task_id, map_index=-1):
        path = f'{self._dag}/dagRuns/{run_id}/taskInstances/{task_id}'
        if map_index >= 0:
            path += f'/{map_index}'
        return await self._call('PATCH', path, json={'new_state': 'failed'})

    async def retry_task(self, run_id, task_id, downstream=True):
        """Clear a task (and by default what depends on it) so the scheduler runs it again."""
        return await self._call('POST', f'{self._dag}/clearTaskInstances', json={
            'dag_run_id': run_id, 'task_ids': [task_id], 'dry_run': False, 'only_failed': False,
            'include_downstream': downstream, 'reset_dag_runs': True})

    async def close(self):
        await self._client.aclose()
