"""Write the Grafana dashboards (grafana/dashboards/*.json) from the short descriptions below.

    python docker/observability/build_dashboards.py

The JSON is generated rather than hand-edited so the dashboards stay reviewable: a panel is one
line here. Grafana provisions the files at start-up (grafana/provisioning/dashboards).
"""

import json
from pathlib import Path

OUT = Path(__file__).parent / 'grafana' / 'dashboards'
PROM = {'type': 'prometheus', 'uid': 'prometheus'}
LOKI = {'type': 'loki', 'uid': 'loki'}
SQL = {'type': 'grafana-postgresql-datasource', 'uid': 'questions'}     # the reporting views
_Y = [0]
_IDS = [0]


def _id():
    _IDS[0] += 1
    return _IDS[0]


def row(title):
    _Y[0] += 1
    return {'type': 'row', 'title': title, 'id': _id(), 'collapsed': False, 'panels': [],
            'gridPos': {'h': 1, 'w': 24, 'x': 0, 'y': _Y[0] - 1}}


def _panel(kind, title, targets, w, h, x, ds=PROM, unit=None, extra=None, description=None):
    p = {'type': kind, 'title': title, 'id': _id(), 'datasource': ds, 'targets': targets,
         'gridPos': {'h': h, 'w': w, 'x': x, 'y': _Y[0]},
         'fieldConfig': {'defaults': {'unit': unit} if unit else {}, 'overrides': []}, 'options': {}}
    if description:
        p['description'] = description
    p.update(extra or {})
    return p


def prom(expr, legend=None, instant=False):
    return {'datasource': PROM, 'expr': expr, 'legendFormat': legend or '__auto', 'refId': 'A',
            'instant': instant, 'range': not instant}


def count(expr):
    """A whole number, 0 when there is nothing: increase() extrapolates (1.02 batches) and an
    empty result would show as "No data"."""
    return f'round(({expr}) or vector(0))'


def stats(*cards, h=4):
    """A row of stat panels: (title, expr, unit)."""
    w = 24 // len(cards)
    out = [_panel('stat', t, [prom(e, instant=True)], w, h, i * w, unit=u,
                  extra={'options': {'reduceOptions': {'calcs': ['lastNotNull']}, 'colorMode': 'none',
                                     'graphMode': 'none', 'textMode': 'value'}})
           for i, (t, e, u) in enumerate(cards)]
    _Y[0] += h
    return out


def graphs(*specs, h=8):
    """A row of time series: (title, [(expr, legend)...], unit)."""
    w = 24 // len(specs)
    out = []
    for i, (title, series, unit) in enumerate(specs):
        targets = [dict(prom(e, l), refId=chr(65 + j)) for j, (e, l) in enumerate(series)]
        out.append(_panel('timeseries', title, targets, w, h, i * w, unit=unit,
                          extra={'options': {'legend': {'displayMode': 'list', 'placement': 'bottom'},
                                             'tooltip': {'mode': 'multi'}}}))
    _Y[0] += h
    return out


def sql(query, fmt='table'):
    return {'datasource': SQL, 'refId': 'A', 'rawSql': ' '.join(query.split()), 'format': fmt, 'rawQuery': True,
            'editorMode': 'code'}


def sql_stats(*cards, h=4):
    """A row of stat panels from SQL: (title, query returning one value, unit)."""
    w = 24 // len(cards)
    out = [_panel('stat', t, [sql(q)], w, h, i * w, ds=SQL, unit=u,
                  extra={'options': {'reduceOptions': {'calcs': ['lastNotNull'], 'fields': ''}, 'colorMode': 'none',
                                     'graphMode': 'none', 'textMode': 'value'}})
           for i, (t, q, u) in enumerate(cards)]
    _Y[0] += h
    return out


def sql_table(title, query, w=24, h=9, x=0, advance=True):
    p = _panel('table', title, [sql(query)], w, h, x, ds=SQL, extra={'options': {'showHeader': True}})
    if advance:
        _Y[0] += h
    return p


def sql_graph(title, query, unit='none', w=24, h=8, x=0, advance=True):
    p = _panel('timeseries', title, [sql(query, 'time_series')], w, h, x, ds=SQL, unit=unit,
               extra={'options': {'legend': {'displayMode': 'list', 'placement': 'bottom'},
                                  'tooltip': {'mode': 'multi'}}})
    if advance:
        _Y[0] += h
    return p


def logs(title, query, h=10):
    out = [_panel('logs', title, [{'datasource': LOKI, 'expr': query, 'refId': 'A'}], 24, h, 0, ds=LOKI,
                  extra={'options': {'showTime': True, 'wrapLogMessage': True, 'sortOrder': 'Descending',
                                     'enableLogDetails': True}})]
    _Y[0] += h
    return out


def dashboard(uid, title, description, panels, refresh='30s'):
    return {'uid': uid, 'title': title, 'description': description, 'tags': ['marginalia'], 'timezone': 'browser',
            'schemaVersion': 41, 'version': 1, 'editable': True, 'refresh': refresh,
            'time': {'from': 'now-24h', 'to': 'now'}, 'panels': panels, 'templating': {'list': []},
            'annotations': {'list': []}}


def service():
    _Y[0], _IDS[0] = 0, 0
    q = 'marginalia_questions_total'
    panels = [row('Questions')]
    panels += stats(
        ('Answered by the agent, 24 h', count(f'sum(increase({q}{{outcome="agent"}}[24h]))'), 'none'),
        ('From the cache, 24 h', count(f'sum(increase({q}{{outcome="cache"}}[24h]))'), 'none'),
        ('Refused, 24 h', count(f'sum(increase({q}{{outcome="refused"}}[24h]))'), 'none'),
        ('Failed, 24 h', count(f'sum(increase({q}{{outcome="failed"}}[24h]))'), 'none'),
        ('Cache hit rate, 24 h', f'sum(increase({q}{{outcome="cache"}}[24h])) / '
                                 f'clamp_min(sum(increase({q}{{outcome=~"agent|cache"}}[24h])), 1)', 'percentunit'),
        ('Answers checked as supported, 24 h', 'sum(increase(marginalia_answers_checked_total{verdict="supported"}[24h])) / '
                                               'clamp_min(sum(increase(marginalia_answers_checked_total[24h])), 1)',
         'percentunit'))
    panels += graphs(
        ('Questions by outcome (per 5 min)', [(f'sum by (outcome) (increase({q}[5m]))', '{{outcome}}')], 'none'),
        ('Agent answer time', [
            ('histogram_quantile(0.5, sum by (le) (rate(marginalia_answer_seconds_bucket[15m])))', 'median'),
            ('histogram_quantile(0.95, sum by (le) (rate(marginalia_answer_seconds_bucket[15m])))', 'p95')], 's'))
    panels += graphs(
        ('Refusals by reason (per hour)', [('sum by (reason) (increase(marginalia_questions_refused_total[1h]))',
                                            '{{reason}}')], 'none'),
        ("Grader's verdicts (per hour)", [('sum by (verdict) (increase(marginalia_answers_checked_total[1h]))',
                                           '{{verdict}}')], 'none'),
        ('Searches per answer', [
            ('histogram_quantile(0.5, sum by (le) (rate(marginalia_agent_tool_calls_bucket[1h])))', 'median'),
            ('histogram_quantile(0.95, sum by (le) (rate(marginalia_agent_tool_calls_bucket[1h])))', 'p95')], 'none'))
    panels.append(row('API'))
    panels += stats(
        ('Requests per second', 'sum(rate(marginalia_http_requests_total[5m]))', 'reqps'),
        ('Server errors', '(sum(rate(marginalia_http_requests_total{status=~"5.."}[5m])) or vector(0)) / '
                          'clamp_min(sum(rate(marginalia_http_requests_total[5m])), 1e-9)', 'percentunit'),
        ('p95 latency', 'histogram_quantile(0.95, sum by (le) (rate(marginalia_http_request_duration_seconds_bucket'
                        '{route!="ui"}[5m])))', 's'),
        ('Readers', 'sum(marginalia_users{state="active"})', 'none'))
    panels += graphs(
        ('Requests by status', [('sum by (status) (rate(marginalia_http_requests_total[5m]))', '{{status}}')], 'reqps'),
        ('p95 latency by route', [('topk(8, histogram_quantile(0.95, sum by (le, route) '
                                   '(rate(marginalia_http_request_duration_seconds_bucket{route!="ui"}[5m]))))',
                                   '{{route}}')], 's'))
    panels.append(row('Worker and library'))
    panels += graphs(
        ('Queues', [('marginalia_queue_length{state="waiting"}', '{{queue}} waiting'),
                    ('marginalia_queue_length{state="pending"}', '{{queue}} in progress')], 'none'),
        ('Job time p95 by queue', [('histogram_quantile(0.95, sum by (le, queue) (rate(marginalia_job_seconds_bucket[15m])))',
                                    '{{queue}}')], 's'),
        ('Books by status', [('marginalia_books', '{{status}}')], 'none'))
    panels.append(row('Logs'))
    panels += logs('Warnings and errors (API and worker)', '{service=~"api|worker", level=~"warning|error|critical"}')
    panels += logs('All API and worker logs', '{service=~"api|worker"}')
    return dashboard('marginalia-service', 'MarginaliaRAG: readers and questions',
                     'Questions (agent, cache, refusals), answer time, the API and the worker.', panels)


def batches():
    _Y[0], _IDS[0] = 0, 0
    dag = 'dag_id="marginalia_ingest"'
    panels = [row('Batches')]
    panels += stats(
        ('Batches succeeded, 7 days',
         count(f'sum(increase(airflow_dagrun_duration_seconds_count{{{dag}, state="success"}}[7d]))'), 'none'),
        ('Batches failed, 7 days',
         count(f'sum(increase(airflow_dagrun_duration_seconds_count{{{dag}, state="failed"}}[7d]))'), 'none'),
        ('Average batch, 7 days', f'sum(increase(airflow_dagrun_duration_seconds_sum{{{dag}}}[7d])) / '
                                  f'clamp_min(sum(increase(airflow_dagrun_duration_seconds_count{{{dag}}}[7d])), 1)', 's'),
        ('Tasks failed, 24 h', count(f'sum(increase(airflow_task_finished_total{{{dag}, state="failed"}}[24h]))'),
         'none'))
    panels += graphs(
        ('Batch duration', [(f'sum by (state) (rate(airflow_dagrun_duration_seconds_sum{{{dag}}}[1h])) / '
                             f'sum by (state) (rate(airflow_dagrun_duration_seconds_count{{{dag}}}[1h]))', '{{state}}')],
         's'),
        ('Tasks finished by state (per hour)', [(f'sum by (state) (increase(airflow_task_finished_total{{{dag}}}[1h]))',
                                                  '{{state}}')], 'none'))
    panels += graphs(
        ('Task time p95', [(f'histogram_quantile(0.95, sum by (le, task_id) (rate(airflow_task_duration_seconds_bucket'
                            f'{{{dag}}}[6h])))', '{{task_id}}')], 's'),
        ('Pool slots in use', [('airflow_pool_running_slots', '{{pool}} running'),
                               ('airflow_pool_queued_slots', '{{pool}} queued')], 'none'),
        ('Executor', [('airflow_executor_running_tasks', 'running'), ('airflow_executor_queued_tasks', 'queued'),
                      ('airflow_executor_open_slots', 'open slots')], 'none'))
    panels.append(row('Logs'))
    panels += logs('Airflow', '{service=~"airflow-scheduler|airflow-apiserver|airflow-dag-processor"}')
    return dashboard('marginalia-batches', 'MarginaliaRAG: batches (Airflow)',
                     'The hourly book-processing batch: runs, tasks, durations and Airflow logs. Task logs are '
                     'on the admin page (Pipeline, Logs).', panels, refresh='1m')


def tokens():
    _Y[0], _IDS[0] = 0, 0
    answered = "FROM reporting.questions WHERE $__timeFilter(created_at) AND total_tokens IS NOT NULL"
    panels = [row('Tokens behind the answers (agent and grader; thinking included)')]
    panels += sql_stats(
        ('Tokens', f'SELECT COALESCE(sum(total_tokens), 0) {answered}', 'none'),
        ('Per answer, average', f'SELECT round(avg(total_tokens)) {answered}', 'none'),
        ('Per answer, most', f'SELECT max(total_tokens) {answered}', 'none'),
        ('Per answer, fewest', f'SELECT min(total_tokens) {answered}', 'none'),
        ('Tokens per second, average', f'SELECT round(avg(tokens_per_second)::numeric, 1) {answered}', 'none'),
        ('Grader share', f'SELECT sum(grader_tokens)::float / NULLIF(sum(total_tokens), 0) {answered}', 'percentunit'))
    panels += graphs(
        ('Generation speed (tokens per second)', [
            ('histogram_quantile(0.5, sum by (le, role) (rate(marginalia_llm_tokens_per_second_bucket[30m])))',
             '{{role}} median'),
            ('histogram_quantile(0.05, sum by (le, role) (rate(marginalia_llm_tokens_per_second_bucket[30m])))',
             '{{role}} slowest 5%')], 'none'),
        ('Tokens per minute', [('sum by (role, kind) (rate(marginalia_llm_tokens_total[5m])) * 60',
                                '{{role}} {{kind}}')], 'none'),
        ('Tokens per answer', [
            ('histogram_quantile(0.5, sum by (le) (rate(marginalia_llm_tokens_per_answer_bucket[1h])))', 'median'),
            ('histogram_quantile(0.95, sum by (le) (rate(marginalia_llm_tokens_per_answer_bucket[1h])))', 'p95')],
         'none'))
    panels.append(row('By reader and book'))
    panels.append(sql_table('Tokens per reader', f'''
        SELECT reader AS "Reader", count(*) AS "Answers", sum(total_tokens) AS "Tokens",
               round(avg(total_tokens)) AS "Average", max(total_tokens) AS "Most", min(total_tokens) AS "Fewest",
               round(avg(tokens_per_second)::numeric, 1) AS "Tokens/s"
        {answered} GROUP BY reader ORDER BY 3 DESC''', w=14, advance=False))
    panels.append(sql_table('Tokens per book', f'''
        SELECT book AS "Book", count(*) AS "Answers", sum(total_tokens) AS "Tokens", round(avg(total_tokens)) AS "Average"
        {answered} GROUP BY book ORDER BY 3 DESC''', w=10, x=14))
    panels.append(sql_graph('Tokens per reader over time', f'''
        SELECT $__timeGroupAlias(created_at, '1h'), reader AS metric, sum(total_tokens) AS value
        {answered} GROUP BY 1, 2 ORDER BY 1''', w=12, advance=False))
    panels.append(sql_graph('Answer tokens: prompt and completion', f'''
        SELECT $__timeGroupAlias(created_at, '1h'), sum(prompt_tokens) AS prompt, sum(completion_tokens) AS completion
        {answered} GROUP BY 1 ORDER BY 1''', w=12, x=12))
    panels.append(row('The most expensive answers'))
    panels.append(sql_table('Answers with the most tokens', f'''
        SELECT created_at AS "Asked", reader AS "Reader", book AS "Book", left(question, 120) AS "Question",
               total_tokens AS "Tokens", agent_tokens AS "Agent", grader_tokens AS "Grader", searches AS "Searches",
               round(seconds::numeric, 1) AS "Seconds", tokens_per_second AS "Tokens/s"
        {answered} ORDER BY total_tokens DESC LIMIT 20''', h=10))
    return dashboard('marginalia-tokens', 'MarginaliaRAG: tokens',
                     'LLM tokens per answer, reader and book, and generation speed. Speeds come from Prometheus; '
                     'per-reader figures from the reporting views in Postgres.', panels, refresh='1m')


if __name__ == '__main__':
    OUT.mkdir(parents=True, exist_ok=True)
    for board in (service(), batches(), tokens()):
        (OUT / f"{board['uid']}.json").write_text(json.dumps(board, indent=1) + '\n', encoding='utf-8')
        print(f"wrote {board['uid']}.json: {len(board['panels'])} panels")
