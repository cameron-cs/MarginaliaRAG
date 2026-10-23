"""The readers' questions, for admins (role `admin`).

    GET /api/admin/questions          newest first; ?outcome=agent|cache|refused|failed|active,
                                      ?search= (question text), ?user= (email), ?book= (book id),
                                      ?limit=, ?before= (the `next` cursor of the previous page)
    GET /api/admin/questions/stats    ?hours=24: outcomes, cache hit rate, refusals, agent timing, tokens
    GET /api/admin/questions/{id}     one question with its answer, guard verdict and tool calls

Live updates: admins already receive every question.* event on the websocket.
"""

import statistics
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from marginalia.api.db import LibraryBook, Question, User, UserBook
from marginalia.api.deps import admin_user, parse_uuid, session

router = APIRouter(prefix='/admin/questions', tags=['admin'], dependencies=[Depends(admin_user)])

Outcome = Literal['agent', 'cache', 'refused', 'failed', 'active']

_OUTCOMES = {
    'agent': lambda: (Question.answered_by == 'agent') & (Question.status == 'done'),
    'cache': lambda: Question.answered_by == 'cache',
    'refused': lambda: Question.status == 'refused',
    'failed': lambda: Question.status == 'failed',
    'active': lambda: Question.status.in_(('queued', 'running')),
}


def _base():
    return (select(Question, User.email, UserBook.book_id, LibraryBook.title)
            .join(User, User.id == Question.user_id)
            .join(UserBook, UserBook.id == Question.user_book_id)
            .join(LibraryBook, LibraryBook.book_id == UserBook.book_id))


def _seconds(q):
    if q.finished_at is None or q.created_at is None:
        return None
    finished, created = q.finished_at, q.created_at
    if (finished.tzinfo is None) != (created.tzinfo is None):        # SQLite drops the zone
        finished, created = finished.replace(tzinfo=None), created.replace(tzinfo=None)
    return round((finished - created).total_seconds(), 1)


def _like(text):
    return '%' + text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'


def summary(q, email, book_id, title):
    d = q.details or {}
    return {'id': str(q.id), 'created_at': q.created_at, 'finished_at': q.finished_at, 'seconds': _seconds(q),
            'user': email, 'book_id': book_id, 'book': title, 'text': q.text, 'status': q.status,
            'answered_by': q.answered_by, 'error': q.error, 'supported': d.get('supported'),
            'refused': d.get('refused'), 'cached': (d.get('cached') or {}).get('kind'),
            'searches': len(d.get('tool_calls') or []), 'revisions': d.get('revisions'),
            'sampling': d.get('sampling') or {}, 'tokens': ((d.get('tokens') or {}).get('total') or {}).get('total')}


@router.get('')
async def questions(outcome: Outcome | None = None, search: str | None = Query(None, max_length=200),
                    user: str | None = Query(None, max_length=320), book: str | None = Query(None, max_length=80),
                    limit: int = Query(50, ge=1, le=200), before: datetime | None = None, db=Depends(session)):
    stmt = _base()
    if outcome:
        stmt = stmt.where(_OUTCOMES[outcome]())
    if search:
        stmt = stmt.where(Question.text.ilike(_like(search), escape='\\'))
    if user:
        stmt = stmt.where(User.email.ilike(_like(user), escape='\\'))
    if book:
        stmt = stmt.where(UserBook.book_id == book)
    if before:
        stmt = stmt.where(Question.created_at < before)
    rows = (await db.execute(stmt.order_by(Question.created_at.desc()).limit(limit + 1))).all()
    items = [summary(*row) for row in rows[:limit]]
    return {'items': items, 'next': items[-1]['created_at'] if len(rows) > limit else None}


@router.get('/stats')
async def stats(hours: int = Query(24, ge=1, le=24 * 90), db=Depends(session)):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = (await db.execute(_base().where(Question.created_at >= since).limit(20000))).all()
    qs = [r[0] for r in rows]
    by = {k: 0 for k in ('agent', 'cache', 'refused', 'failed', 'active')}
    refused, agent_seconds, searches, supported, tokens, speed = {}, [], [], [], [], []
    for q in qs:
        d = q.details or {}
        if q.status == 'refused':
            by['refused'] += 1
            refused[d.get('refused') or 'other'] = refused.get(d.get('refused') or 'other', 0) + 1
        elif q.status == 'failed':
            by['failed'] += 1
        elif q.status in ('queued', 'running'):
            by['active'] += 1
        elif q.answered_by == 'cache':
            by['cache'] += 1
        else:
            by['agent'] += 1
            if _seconds(q) is not None:
                agent_seconds.append(_seconds(q))
            searches.append(len(d.get('tool_calls') or []))
            total = (d.get('tokens') or {}).get('total') or {}
            if total.get('total'):
                tokens.append(total['total'])
            if total.get('per_second'):
                speed.append(total['per_second'])
            if d.get('supported') is not None:
                supported.append(bool(d['supported']))
    answered = by['agent'] + by['cache']
    pct = lambda xs, p: round(statistics.quantiles(xs, n=100)[p - 1], 1) if len(xs) >= 2 else (xs[0] if xs else None)  # noqa: E731
    return {'hours': hours, 'total': len(qs), 'outcomes': by, 'refused': refused,
            'cache_hit_rate': round(by['cache'] / answered, 3) if answered else None,
            'supported_rate': round(sum(supported) / len(supported), 3) if supported else None,
            'agent_seconds': {'p50': pct(agent_seconds, 50), 'p95': pct(agent_seconds, 95),
                              'max': max(agent_seconds) if agent_seconds else None},
            'mean_searches': round(statistics.mean(searches), 2) if searches else None,
            'tokens': {'total': sum(tokens), 'mean': round(statistics.mean(tokens)) if tokens else None,
                       'max': max(tokens, default=None), 'min': min(tokens, default=None),
                       'per_second': round(statistics.mean(speed), 1) if speed else None},
            'readers': len({r[1] for r in rows})}


@router.get('/{question_id}')
async def question(question_id: str, db=Depends(session)):
    row = (await db.execute(_base().where(Question.id == parse_uuid(question_id)))).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'Not found')
    q = row[0]
    return {**summary(*row), 'answer': q.answer, 'citations': q.citations or [], 'details': q.details or {}}
