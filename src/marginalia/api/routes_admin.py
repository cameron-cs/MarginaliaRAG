"""Administration (role `admin`).

    GET   /api/admin/users                     every account
    PATCH /api/admin/users/{id}                {role?, is_active?}
    GET   /api/admin/books                     every library book: readers, questions (and how many the
                                               guard refused), cover
    POST  /api/admin/books/{book_id}/reingest  redo the book from scratch in a batch started now
    GET   /api/admin/queues                    worker job queues (questions, deletions)
    GET   /api/admin/observability             where the dashboards are (CP_GRAFANA_URL)

The batch pipeline (Airflow DAG marginalia_ingest):
    GET   /api/admin/pipeline                            schedule, next run, runs (?state=, ?limit=, ?offset=)
                                                         with a summary: runs by state, mean duration
    GET   /api/admin/pipeline/waiting                    what the next batch would do: books and the steps
                                                         they need, prompt reviews
    POST  /api/admin/pipeline/runs                       start a batch now {force?: [book_id], only?: [book_id]}:
                                                         `only` processes just those books
    GET   /api/admin/pipeline/runs/{run_id}/tasks        the run's tasks, each mapped one labelled with its book
    GET   /api/admin/pipeline/runs/{run_id}/tasks/{task_id}/logs   ?map_index=&try_number= (default: the last)
    POST  /api/admin/pipeline/runs/{run_id}/stop         stop the run (running tasks are killed)
    POST  /api/admin/pipeline/runs/{run_id}/tasks/{task_id}/stop    ?map_index= for one book's task
    POST  /api/admin/pipeline/runs/{run_id}/tasks/{task_id}/retry   run it (and what follows) again
    POST  /api/admin/pipeline/pause | /api/admin/pipeline/resume        the hourly schedule
Live task progress arrives on the admin websocket as `pipeline.task` events.
"""

import asyncio
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select

from marginalia.api import jobs
from marginalia.api.airflow import AirflowError
from marginalia.api.db import LibraryBook, Question, User, UserBook
from marginalia.api.deps import Services, admin_user, parse_uuid, services, session
from marginalia.api.routes_auth import UserOut, user_out
from marginalia.api.routes_covers import cover_url

router = APIRouter(prefix='/admin', tags=['admin'], dependencies=[Depends(admin_user)])


class UserPatch(BaseModel):
    role: Literal['user', 'admin'] | None = None
    is_active: bool | None = None


@router.get('/users', response_model=list[UserOut])
async def users(db=Depends(session)):
    return [user_out(u) for u in (await db.execute(select(User).order_by(User.created_at))).scalars()]


@router.patch('/users/{user_id}', response_model=UserOut)
async def patch_user(user_id: str, body: UserPatch, me: User = Depends(admin_user), db=Depends(session)):
    user = await db.get(User, parse_uuid(user_id))
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'Not found')
    if user.id == me.id and (body.role == 'user' or body.is_active is False):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, 'Admins cannot demote or disable themselves')
    if body.role is not None:
        user.role = body.role
    if body.is_active is not None:
        user.is_active = body.is_active
    await db.commit()
    return user_out(user)


@router.get('/books')
async def books(svc: Services = Depends(services), db=Depends(session)):
    readers = dict((await db.execute(select(UserBook.book_id, func.count()).group_by(UserBook.book_id))).all())
    asked = (await db.execute(select(UserBook.book_id, Question.status, func.count())
                              .join(Question, Question.user_book_id == UserBook.id)
                              .group_by(UserBook.book_id, Question.status))).all()
    questions, refused = {}, {}
    for book_id, state, n in asked:
        questions[book_id] = questions.get(book_id, 0) + n
        if state == 'refused':
            refused[book_id] = n
    rows = (await db.execute(select(LibraryBook).order_by(LibraryBook.created_at))).scalars()
    return [{'book_id': b.book_id, 'title': b.title, 'author': b.author, 'status': b.status,
             'chapters': b.chapters, 'error_code': b.error_code, 'error_message': b.error_message,
             'readers': readers.get(b.book_id, 0), 'questions': questions.get(b.book_id, 0),
             'refused': refused.get(b.book_id, 0), 'cover_url': cover_url(svc.tokens, b),
             'cover_source': b.cover_source, 'updated_at': b.updated_at} for b in rows]


@router.post('/books/{book_id}/reingest', status_code=202)
async def reingest(book_id: str, svc: Services = Depends(services), db=Depends(session)):
    if await db.get(LibraryBook, book_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'Not found')
    run = await _airflow(svc, lambda af: af.trigger(force=[book_id], note=f'Reingest {book_id} (admin)'))
    return {'message': 'A batch was started to redo this book.', 'book_id': book_id, 'run': run}


@router.get('/queues')
async def queues(svc: Services = Depends(services)):
    return await jobs.queue_stats(svc.redis)


@router.get('/observability')
async def observability(svc: Services = Depends(services)):
    return {'grafana_url': svc.settings.grafana_url or None, 'evidently_url': svc.settings.evidently_ui_url or None}


# --- the batch pipeline --------------------------------------------------------------------------

class RunIn(BaseModel):
    force: list[str] = []
    only: list[str] = []


async def _airflow(svc, call):
    if svc.airflow is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, 'Airflow is not configured (CP_AIRFLOW_PASSWORD)')
    try:
        return await call(svc.airflow)
    except HTTPException:
        raise                            # our own answer, such as an unknown task
    except AirflowError as exc:
        code = exc.status if exc.status in (404, 409) else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(code, f'Airflow: {exc.detail}') from None
    except Exception as exc:              # unreachable
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f'Airflow is unreachable: {type(exc).__name__}') from None


RunState = Literal['queued', 'running', 'success', 'failed']


@router.get('/pipeline')
async def pipeline(state: RunState | None = None, limit: int = Query(20, ge=1, le=100),
                   offset: int = Query(0, ge=0), svc: Services = Depends(services)):
    async def call(af):
        schedule, (runs, total) = await asyncio.gather(af.schedule(), af.runs(limit=limit, state=state, offset=offset))
        done = [r['duration'] for r in runs if r.get('duration') is not None and r['state'] in ('success', 'failed')]
        by_state = {}
        for r in runs:
            by_state[r['state']] = by_state.get(r['state'], 0) + 1
        return {**schedule, 'runs': runs, 'total': total,
                'summary': {'by_state': by_state, 'mean_duration': round(sum(done) / len(done), 1) if done else None,
                            'longest': round(max(done), 1) if done else None}}
    return await _airflow(svc, call)


def _batch(svc):
    from marginalia.batch.work import BatchWork
    from marginalia.ingest.pipeline import Pipeline
    return BatchWork(svc.db, svc.redis, svc.library, Pipeline(svc.library, svc.settings), guard=None)


async def _titles(db, book_ids):
    rows = await db.execute(select(LibraryBook.book_id, LibraryBook.title).where(LibraryBook.book_id.in_(book_ids)))
    return dict(rows.all())


async def _reviewers(db, user_book_ids):
    ids = [uuid.UUID(i) for i in user_book_ids]
    rows = await db.execute(select(UserBook.id, User.email, UserBook.book_id, LibraryBook.title)
                            .join(User, User.id == UserBook.user_id)
                            .join(LibraryBook, LibraryBook.book_id == UserBook.book_id).where(UserBook.id.in_(ids)))
    return {str(i): {'user': email, 'book_id': book_id, 'title': title} for i, email, book_id, title in rows.all()}


@router.get('/pipeline/waiting')
async def waiting(svc: Services = Depends(services), db=Depends(session)):
    plan = await _batch(svc).waiting()
    titles = await _titles(db, [b['book_id'] for b in plan['books']])
    reviewers = await _reviewers(db, plan['reviews'])
    readers = dict((await db.execute(select(UserBook.book_id, func.count()).group_by(UserBook.book_id))).all())
    return {'books': [{**b, 'title': titles.get(b['book_id'], b['book_id']), 'readers': readers.get(b['book_id'], 0)}
                      for b in plan['books']],
            'reviews': [{'user_book_id': i, **reviewers.get(i, {})} for i in plan['reviews']]}


@router.post('/pipeline/runs', status_code=202)
async def start_run(body: RunIn = RunIn(), svc: Services = Depends(services), db=Depends(session)):
    asked = set(body.force) | set(body.only)
    known = set(await _titles(db, list(asked))) if asked else set()
    if asked - known:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f'Unknown book(s): {", ".join(sorted(asked - known))}')
    note = f'Started by an admin for {", ".join(body.only)}' if body.only else 'Started by an admin'
    return await _airflow(svc, lambda af: af.trigger(force=body.force, only=body.only, note=note))


@router.get('/pipeline/runs/{run_id}/tasks')
async def run_tasks(run_id: str, svc: Services = Depends(services), db=Depends(session)):
    async def call(af):
        return await asyncio.gather(af.tasks(run_id), af.plan(run_id))
    tasks, plan = await _airflow(svc, call)
    plan = plan or {}
    books = [b['book_id'] for b in plan.get('books', [])]
    titles = await _titles(db, books) if books else {}
    reviewers = await _reviewers(db, plan.get('reviews', [])) if plan.get('reviews') else {}
    for t in tasks:
        i = t.get('map_index', -1)
        if i is None or i < 0:
            continue
        if t['task_id'].startswith('process.') and i < len(books):
            t['book_id'] = books[i]
            t['label'] = titles.get(books[i], books[i])
        elif t['task_id'] == 'review' and i < len(plan.get('reviews', [])):
            r = reviewers.get(plan['reviews'][i], {})
            t['book_id'] = r.get('book_id')
            t['label'] = f"{r.get('user', '?')} · {r.get('title', '?')}"
    return tasks


@router.get('/pipeline/runs/{run_id}/tasks/{task_id}/logs')
async def task_logs(run_id: str, task_id: str, map_index: int = -1, try_number: int | None = Query(None, ge=1),
                    svc: Services = Depends(services)):
    async def call(af):
        tries = try_number
        if tries is None:
            match = [t for t in await af.tasks(run_id) if t['task_id'] == task_id and t.get('map_index', -1) == map_index]
            if not match:
                raise HTTPException(status.HTTP_404_NOT_FOUND, 'No such task in this run')
            tries = match[0].get('try_number') or 1
        return {'task_id': task_id, 'map_index': map_index, 'try_number': tries,
                'lines': await af.task_log(run_id, task_id, tries, map_index)}
    return await _airflow(svc, call)


@router.post('/pipeline/runs/{run_id}/stop')
async def stop_run(run_id: str, svc: Services = Depends(services)):
    return await _airflow(svc, lambda af: af.stop_run(run_id))


@router.post('/pipeline/runs/{run_id}/tasks/{task_id}/stop')
async def stop_task(run_id: str, task_id: str, map_index: int = -1, svc: Services = Depends(services)):
    await _airflow(svc, lambda af: af.stop_task(run_id, task_id, map_index))
    return {'stopped': task_id, 'map_index': map_index}


@router.post('/pipeline/runs/{run_id}/tasks/{task_id}/retry')
async def retry_task(run_id: str, task_id: str, svc: Services = Depends(services)):
    await _airflow(svc, lambda af: af.retry_task(run_id, task_id))
    return {'retrying': task_id}


@router.post('/pipeline/pause')
async def pause(svc: Services = Depends(services)):
    return await _airflow(svc, lambda af: af.set_paused(True))


@router.post('/pipeline/resume')
async def resume(svc: Services = Depends(services)):
    return await _airflow(svc, lambda af: af.set_paused(False))
