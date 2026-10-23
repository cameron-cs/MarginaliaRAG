"""LLM monitoring with Evidently: the question table, the report, the dashboard and one run."""

from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip('evidently', reason='the monitoring extra is not installed')

from marginalia.monitoring import __main__ as job, data, report  # noqa: E402
from marginalia.config import Settings  # noqa: E402

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)


def row(i, status='done', answered_by='agent', supported=True, answer='Sonia, Marmeladov\'s daughter [Part I, Chapter II].',
        tokens=8000):
    return {'created_at': NOW - timedelta(minutes=i), 'question': 'Who is Sonia?', 'answer': answer, 'status': status,
            'answered_by': answered_by, 'supported': supported, 'refused_reason': None, 'book': 'Crime and Punishment',
            'seconds': 60.0 + i, 'total_tokens': tokens, 'prompt_tokens': tokens and tokens - 1000,
            'completion_tokens': tokens and 1000,
            'tokens_per_second': 60.0, 'searches': 2, 'revisions': 0, 'citations': 1}


ROWS = [row(0), row(1, answered_by='cache'), row(2, status='refused', answered_by='guard', supported=None, answer=None,
                                               tokens=None),
        row(3, supported=False, answer='The book does not say who Sonia is.'), row(4, status='failed', supported=None,
                                                                                 answer=None, tokens=None)]


def test_the_question_table():
    frame = data.to_frame(ROWS)
    assert list(frame['outcome']) == ['agent', 'cache', 'refused', 'agent', 'failed']
    assert list(frame['verdict']) == ['supported', 'supported', 'unchecked', 'unsupported', 'unchecked']
    assert frame['created_at'].iloc[0].tzinfo is None, 'naive UTC'
    assert frame['total_tokens'].isna().tolist() == [False, False, True, False, True]
    assert frame['answer'].iloc[2] == ''


def test_flags_apply_only_where_there_is_an_answer():
    frame = report.add_flags(data.to_frame(ROWS))
    assert frame['refused'].tolist() == [0, 0, 1, 0, 0] and frame['cached'].tolist() == [0, 1, 0, 0, 0]
    assert frame['cites_book'].tolist()[:2] == [1, 1] and frame['cites_book'].isna().tolist()[2]
    assert frame['not_in_book'].tolist()[3] == 1 and frame['not_in_book'].tolist()[0] == 0
    assert frame['supported'].tolist()[3] == 0 and frame['supported'].isna().tolist()[2]


def _values(snapshot):
    return {m['metric_name']: m['value'] for m in snapshot.dict()['metrics']}


def test_the_report_without_and_with_a_reference():
    current = data.to_frame(ROWS)
    values = _values(report.build(current))
    assert values['RowCount()'] == 5
    assert values['MeanValue(column=refused)'] == pytest.approx(0.2)
    assert values['MaxValue(column=total_tokens)'] == 8000
    assert not any(k.startswith('DriftedColumnsCount') for k in values), 'no reference, no drift'

    reference = data.to_frame([row(i, tokens=2000) for i in range(30)])
    values = _values(report.build(current, reference))
    drift = next(v for k, v in values.items() if k.startswith('DriftedColumnsCount'))
    assert drift['count'] >= 1, 'tokens per answer went from 2000 to 8000'


def test_the_dashboard_and_a_run_into_a_local_workspace(tmp_path, monkeypatch):
    assert len(report.dashboard().panels) == 10
    calls = []

    async def fake_fetch(url, since, until):
        calls.append((since, until))
        return ROWS if len(calls) == 1 else [row(i, tokens=2000) for i in range(30)]

    monkeypatch.setattr(data, 'fetch', fake_fetch)
    ref = job.run_once(Settings(), local=str(tmp_path / 'workspace'), now=NOW)
    assert ref is not None
    assert calls[0] == (NOW - timedelta(hours=24), NOW) and calls[1][1] == NOW - timedelta(hours=24)
    from evidently.ui.workspace import Workspace
    ws = Workspace.create(str(tmp_path / 'workspace'))
    [proj] = ws.search_project(report.PROJECT)
    assert len(ws.list_runs(proj.id)) == 1 and len(ws.get_dashboard(proj.id).panels) == 10


def test_no_questions_no_report(monkeypatch, tmp_path):
    async def nothing(url, since, until):
        return []
    monkeypatch.setattr(data, 'fetch', nothing)
    assert job.run_once(Settings(), local=str(tmp_path / 'ws'), now=NOW) is None
