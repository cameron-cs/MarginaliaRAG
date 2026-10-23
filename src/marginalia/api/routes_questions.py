"""Questions about a ready book.

    POST /api/books/{id}/questions   {text, fresh?, sampling?}  -> 202 queued; progress and the answer arrive
                                     over the websocket. The worker screens it first (guard, then the answer
                                     cache; `fresh` skips the cache) and may refuse it: status `refused`.
                                     `sampling`: temperature, top_p, top_k, max_searches, seed (any of them).
    GET  /api/questions/sampling             -> the server's sampling defaults and the allowed ranges
    GET  /api/books/{id}/questions          -> this book's questions, newest first
    GET  /api/questions/{id}                -> one question with its answer, once done
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from marginalia.agent.sampling import Sampling, limits
from marginalia.api import events, jobs
from marginalia.api.db import Question, User, user_book_status
from marginalia.api.deps import Services, current_user, parse_uuid, services, session
from marginalia.api.routes_books import owned
from marginalia.guard.questions import check_question

router = APIRouter(tags=['questions'])


class QuestionIn(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    fresh: bool = False             # ask the agent even if an earlier answer would do
    sampling: Sampling | None = None


class QuestionOut(BaseModel):
    id: uuid.UUID
    book: uuid.UUID
    text: str
    status: str
    answer: str | None
    citations: list
    details: dict
    error: str | None
    created_at: datetime
    finished_at: datetime | None


def question_out(q):
    return QuestionOut(id=q.id, book=q.user_book_id, text=q.text, status=q.status, answer=q.answer,
                       citations=q.citations or [], details=q.details or {}, error=q.error,
                       created_at=q.created_at, finished_at=q.finished_at)


@router.post('/books/{user_book_id}/questions', status_code=202, response_model=None)
async def ask(user_book_id: str, body: QuestionIn, user: User = Depends(current_user),
              svc: Services = Depends(services), db=Depends(session)):
    ub = await owned(db, user, user_book_id)
    if user_book_status(ub) != 'ready':
        return JSONResponse(status_code=409, content={'error': {
            'code': 'not_ready', 'message': f'The book is {user_book_status(ub)}, not ready for questions yet.'}})
    problem = check_question(body.text)
    if problem:
        return JSONResponse(status_code=422, content={'error': {'code': problem[0], 'message': problem[1]}})
    custom = body.sampling.custom() if body.sampling else {}
    q = Question(user_id=user.id, user_book_id=ub.id, text=body.text.strip(),
                 details={'sampling': custom} if custom else {})
    db.add(q)
    await db.commit()
    await jobs.enqueue(svc.redis, 'screen', 'screen_question', question_id=str(q.id), fresh=body.fresh)
    await events.publish(svc.redis, events.event('question.status', question_id=q.id, user_book_id=ub.id,
                                                 status='screening', message='Checking the question...'), [user.id])
    return JSONResponse(status_code=202, content=question_out(q).model_dump(mode='json'))


@router.get('/questions/sampling')
async def sampling_options(user: User = Depends(current_user), svc: Services = Depends(services)):
    return {'defaults': Sampling().resolve(svc.settings).model_dump(exclude={'seed'}), 'limits': limits()}


@router.get('/books/{user_book_id}/questions', response_model=list[QuestionOut])
async def history(user_book_id: str, user: User = Depends(current_user), db=Depends(session)):
    ub = await owned(db, user, user_book_id)
    rows = await db.execute(select(Question).where(Question.user_book_id == ub.id)
                            .order_by(Question.created_at.desc()).limit(200))
    return [question_out(q) for q in rows.scalars()]


@router.get('/questions/{question_id}', response_model=QuestionOut)
async def get_question(question_id: str, user: User = Depends(current_user), db=Depends(session)):
    q = await db.get(Question, parse_uuid(question_id))
    if q is None or q.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'Not found')
    return question_out(q)
