"""A user's books: upload, status, prompts, removal.

    POST   /api/books                 multipart: file (PDF), system_prompt?, user_prompt?
    GET    /api/books                 the user's books
    GET    /api/books/{id}            one of them, with its structure once known
    PUT    /api/books/{id}/prompts    {system_prompt, user_prompt}: replace and re-check the prompts
    DELETE /api/books/{id}            remove the book from the user's shelf

Upload checks; the first two answer at once and report every problem together (422):
  1. the file: size, then a real PDF with a text layer, not password-protected
  2. the prompts: rule checks
  3. duplicates: the user already has this file (409); someone else has it (shared, no reprocessing)
Nothing is stored unless the file and the prompts pass.
Then 202 "The book was uploaded..." and the book waits for the next Airflow batch (hourly), which
does the slow part: the LLM review of the prompts, chapter detection, summaries and indexing.
Progress arrives over the websocket while the batch runs.
"""

import asyncio
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from marginalia.api import events, jobs
from marginalia.api.db import LibraryBook, User, UserBook, user_book_status
from marginalia.api.deps import Services, current_user, parse_uuid, services, session
from marginalia.api.routes_covers import cover_url
from marginalia.ingest.pdf import PdfError, read_pdf
from marginalia.library import DuplicateBook, UnknownBook

router = APIRouter(prefix='/books', tags=['books'])


class BookOut(BaseModel):
    id: uuid.UUID
    book_id: str
    title: str
    author: str | None
    filename: str
    status: str                 # queued | checking | ready | rejected | failed
    library_status: str
    prompt_status: str
    error_code: str | None = None
    error_message: str | None = None
    prompt_feedback: str | None = None
    system_prompt: str | None = None
    user_prompt: str | None = None
    chapters: int = 0
    created_at: datetime
    structure: dict | None = None
    cover_url: str | None = None        # a signed link (routes_covers); None until the cover step ran
    cover_source: str | None = None


class PromptsIn(BaseModel):
    system_prompt: str | None = Field(None, max_length=10000)
    user_prompt: str | None = Field(None, max_length=10000)


def book_out(ub, profile=None, tokens=None):
    b = ub.book
    error_code, error_message = b.error_code, b.error_message
    if ub.prompt_status == 'rejected':
        error_code, error_message = 'prompt_rejected', ub.prompt_feedback
    structure = None
    if profile is not None and profile.chapters:
        structure = {'parts': profile.parts, 'chapters': [{k: c[k] for k in ('label', 'title', 'part', 'chapter')}
                                                           for c in profile.chapters],
                     'quote_style': profile.quote_style}
    return BookOut(id=ub.id, book_id=b.book_id, title=b.title, author=b.author, filename=ub.filename,
                   status=user_book_status(ub), library_status=b.status, prompt_status=ub.prompt_status,
                   error_code=error_code, error_message=error_message, prompt_feedback=ub.prompt_feedback,
                   system_prompt=ub.system_prompt, user_prompt=ub.user_prompt, chapters=b.chapters,
                   created_at=ub.created_at, structure=structure,
                   cover_url=cover_url(tokens, b) if tokens else None, cover_source=b.cover_source)


def _error(code, message, status_code=422, **extra):
    return JSONResponse(status_code=status_code, content={'error': {'code': code, 'message': message, **extra}})


async def owned(db, user, user_book_id):
    ub = await db.get(UserBook, parse_uuid(user_book_id))
    if ub is None or ub.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'Not found')
    return ub


def library_row(profile):
    return LibraryBook(book_id=profile.book_id, sha256=profile.sha256, title=profile.title, author=profile.author,
                       status=profile.status, chapters=len(profile.chapters),
                       cover=(profile.cover or {}).get('file'), cover_source=(profile.cover or {}).get('source'))


@router.post('', status_code=202, response_model=None)
async def upload(file: UploadFile = File(...), system_prompt: str | None = Form(None),
                 user_prompt: str | None = Form(None), user: User = Depends(current_user),
                 svc: Services = Depends(services), db=Depends(session)):
    limit = svc.settings.max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        return _error('too_large', f'Books are limited to {svc.settings.max_upload_mb} MB.', 413)
    filename = file.filename or 'upload.pdf'
    problems, pdf = [], None
    try:
        # Parsing a large PDF takes seconds; keep the event loop free.
        pdf = await asyncio.to_thread(read_pdf, data)
    except PdfError as exc:
        problems.append({'field': 'file', 'code': exc.code, 'message': str(exc)})
    problems += [{'field': i.field, 'code': i.code, 'message': i.message}
                 for i in svc.prompt_rules(system_prompt, user_prompt)]
    if problems:
        code = problems[0]['code'] if problems[0]['field'] == 'file' else 'prompt_rejected'
        return _error(code, problems[0]['message'], problems=problems)

    try:
        profile = await asyncio.to_thread(svc.library.add, data, filename, pdf=pdf)
    except DuplicateBook as exc:
        profile = svc.library.get(exc.book_id)

    lib = await db.get(LibraryBook, profile.book_id)
    if lib is None:
        lib = library_row(profile)
        db.add(lib)
    existing = (await db.execute(select(UserBook).where(UserBook.user_id == user.id,
                                                        UserBook.book_id == profile.book_id))).scalar_one_or_none()
    if existing:
        return _error('duplicate', 'You have already uploaded this book.', 409, book=str(existing.id))
    ub = UserBook(user_id=user.id, book_id=profile.book_id, filename=filename,
                  system_prompt=system_prompt or None, user_prompt=user_prompt or None)
    db.add(ub)
    await db.commit()
    await db.refresh(ub)
    next_batch = await svc.next_batch()
    when = f' at {next_batch[11:16]} UTC' if next_batch else ''
    message = (f'This book is already in the library. It will be ready for you after the next batch{when}.'
               if lib.status == 'ready' else f'The book was uploaded. It will be processed in the next batch{when}.')
    await events.publish(svc.redis, events.event('book.status', user_book_id=ub.id, book_id=ub.book_id,
                                                 status='queued', message=message, next_batch_at=next_batch),
                         [user.id])
    return JSONResponse(status_code=202, content={'message': message, 'next_batch_at': next_batch,
                                                  'book': book_out(ub, tokens=svc.tokens).model_dump(mode='json')})


@router.get('', response_model=list[BookOut])
async def my_books(user: User = Depends(current_user), svc: Services = Depends(services), db=Depends(session)):
    rows = await db.execute(select(UserBook).where(UserBook.user_id == user.id).order_by(UserBook.created_at.desc()))
    return [book_out(ub, tokens=svc.tokens) for ub in rows.scalars()]


@router.get('/{user_book_id}', response_model=BookOut)
async def get_book(user_book_id: str, user: User = Depends(current_user), svc: Services = Depends(services),
                   db=Depends(session)):
    ub = await owned(db, user, user_book_id)
    try:
        profile = svc.library.get(ub.book_id)
    except UnknownBook:
        profile = None
    return book_out(ub, profile, svc.tokens)


@router.put('/{user_book_id}/prompts', status_code=202, response_model=None)
async def set_prompts(user_book_id: str, body: PromptsIn, user: User = Depends(current_user),
                      svc: Services = Depends(services), db=Depends(session)):
    ub = await owned(db, user, user_book_id)
    issues = svc.prompt_rules(body.system_prompt, body.user_prompt)
    if issues:
        return _error('prompt_rejected', issues[0].message, problems=[i.__dict__ for i in issues])
    ub.system_prompt, ub.user_prompt = body.system_prompt or None, body.user_prompt or None
    ub.prompt_status, ub.prompt_feedback = 'pending', None
    await db.commit()
    next_batch = await svc.next_batch()
    return JSONResponse(status_code=202, content={'message': 'Your new prompts will be checked in the next batch.',
                                                  'next_batch_at': next_batch,
                                                  'book': book_out(ub, tokens=svc.tokens).model_dump(mode='json')})


@router.delete('/{user_book_id}', status_code=204)
async def remove_book(user_book_id: str, user: User = Depends(current_user), svc: Services = Depends(services),
                      db=Depends(session)):
    ub = await owned(db, user, user_book_id)
    book_id = ub.book_id
    await db.delete(ub)
    await db.commit()
    # The last reader gone: the library book goes too (index, files, row), in the worker.
    others = (await db.execute(select(UserBook.id).where(UserBook.book_id == book_id).limit(1))).first()
    if others is None:
        await jobs.enqueue(svc.redis, 'ingest', 'delete_book', book_id=book_id)
