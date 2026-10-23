"""The batch processing that Airflow runs every hour, one small command per task.

Uploads are only validated in the API; everything slow happens here, for every user's waiting
books at once:

    plan      which owner prompts await review and which library books need pipeline steps;
              up-to-date books are skipped, so an hourly run over the whole library is cheap
    review    the LLM review of one shelf entry's prompts
    step      one pipeline step (structure | summarize | index) for one library book
    finalize  write personas for owners who gave no prompts, sync the database, tell every reader

Every command publishes live events (book.status to readers, pipeline.task to admins), so the
website shows progress while a batch runs.
"""

import asyncio
import logging
import uuid

from sqlalchemy import select

from marginalia.api import events
from marginalia.api.db import LibraryBook, UserBook
from marginalia.guard.prompts import ReviewFailed, persona_text
from marginalia.ingest.pipeline import STEPS, StepFailed
from marginalia.library import UnknownBook

log = logging.getLogger(__name__)

STEP_MESSAGES = {
    'structure': 'Finding the chapters...',
    'cover': 'Finding a cover...',
    'summarize': 'Summarizing the chapters...',
    'index': 'Indexing the book for search...',
}


def book_name(title, author):
    return f'{title!r}' + (f' by {author}' if author else '')


class BatchWork:
    def __init__(self, db, redis, library, pipeline, guard, run_id=None):
        self.db, self.redis, self.library, self.pipeline, self.guard = db, redis, library, pipeline, guard
        self.run_id = run_id

    # --- events ----------------------------------------------------------------------------

    async def _book_event(self, ub, status, message, **extra):
        await events.publish(self.redis, events.event('book.status', user_book_id=ub.id, book_id=ub.book_id,
                                                      status=status, message=message, run_id=self.run_id, **extra),
                             [ub.user_id])

    async def _task_event(self, task, state, **extra):
        await events.publish(self.redis, events.event('pipeline.task', run_id=self.run_id, task=task, state=state,
                                                      **extra), [])

    async def _readers(self, s, book_id, active=True):
        """Shelf entries of a library book; `active` leaves out readers whose prompts were rejected."""
        rows = list((await s.execute(select(UserBook).where(UserBook.book_id == book_id))).scalars())
        return [ub for ub in rows if not active or ub.prompt_status not in ('rejected', 'error')]

    async def _mark_failed(self, s, book_id):
        row = await s.get(LibraryBook, book_id)
        if row is not None and row.status not in ('ready', 'rejected', 'failed'):
            row.status, row.error_code = 'failed', row.error_code or 'processing_failed'
            await s.commit()

    async def _sync(self, s, profile):
        row = await s.get(LibraryBook, profile.book_id)
        if row is not None:
            row.status, row.title, row.author = profile.status, profile.title, profile.author
            row.chapters = len(profile.chapters)
            row.cover, row.cover_source = (profile.cover or {}).get('file'), (profile.cover or {}).get('source')
            row.error_code = (profile.error or {}).get('code')
            row.error_message = (profile.error or {}).get('message')
            await s.commit()

    # --- plan ------------------------------------------------------------------------------

    async def plan(self, force=(), only=()):
        """This run's work (see `waiting`), announced to admins."""
        plan = await self.waiting(force, only)
        await self._task_event('plan', 'done', reviews=len(plan['reviews']), books=[b['book_id'] for b in plan['books']])
        return plan

    async def waiting(self, force=(), only=()):
        """What a batch would do now: {'reviews': [user_book ids], 'books': [{'book_id', 'force',
        'ready', 'steps'}]}, plus 'only' when narrowed.
        `force`: books to redo from scratch. `only`: just these books (and their readers' prompt
        reviews), not everything that is waiting. `ready`: the book already answers questions and
        only needs a newer step (a cover, say); its readers are not told about the work.
        `steps`: the steps the book still needs."""
        force, only = set(force), set(only)
        async with self.db.sessions() as s:
            shelf = list((await s.execute(select(UserBook))).scalars())
        reviews = [str(ub.id) for ub in shelf
                   if ub.prompt_status == 'pending' and persona_text(ub.system_prompt, ub.user_prompt)
                   and (not only or ub.book_id in only)]
        wanted = {ub.book_id for ub in shelf if ub.prompt_status not in ('rejected', 'error')}
        wanted = (wanted & only if only else wanted) | force
        books = []
        for book_id in sorted(wanted):
            try:
                profile = self.library.get(book_id)
            except UnknownBook:
                continue
            stale = [st for st in STEPS if not self.pipeline.is_fresh(profile, st)]
            if book_id in force:
                books.append({'book_id': book_id, 'force': True, 'ready': False, 'steps': list(STEPS)})
            elif profile.status == 'rejected':
                continue                                   # stays rejected until an admin forces it
            elif profile.status != 'ready' or stale:
                books.append({'book_id': book_id, 'force': False, 'ready': profile.status == 'ready',
                              'steps': stale})
        plan = {'reviews': reviews, 'books': books}
        if only:
            plan['only'] = sorted(only)
        return plan

    # --- prompt review ---------------------------------------------------------------------

    async def review(self, user_book_id):
        async with self.db.sessions() as s:
            # Ids arrive as strings (CLI, plan JSON); UUID columns need UUIDs on every database.
            ub = await s.get(UserBook, uuid.UUID(str(user_book_id)))
            if ub is None or ub.prompt_status != 'pending' or not persona_text(ub.system_prompt, ub.user_prompt):
                return 'skipped'
            await self._book_event(ub, 'checking', 'Checking your prompts...', step='prompts')
            try:
                verdict = await self.guard.review(book_name(ub.book.title, ub.book.author), ub.system_prompt,
                                                  ub.user_prompt)
            except ReviewFailed as exc:
                log.warning('prompt review for %s failed: %s', ub.id, exc)
                ub.prompt_status, ub.prompt_feedback = 'error', 'The prompts could not be checked.'
                await s.commit()
                await self._book_event(ub, 'failed', 'We could not check your prompts right now. '
                                                     'Save them again to retry.', error_code='prompt_check_failed')
                return 'error'
            ub.prompt_status = 'approved' if verdict.approved else 'rejected'
            ub.prompt_feedback = None if verdict.approved else verdict.reason
            await s.commit()
            if not verdict.approved:
                await self._book_event(ub, 'rejected', f'The prompts were not accepted: {verdict.reason}',
                                       error_code='prompt_rejected', category=verdict.category)
            return ub.prompt_status

    # --- pipeline steps --------------------------------------------------------------------

    async def step(self, book_id, step, force=False):
        """Run one pipeline step. A rejected book (no chapters) is an outcome, not an error."""
        async with self.db.sessions() as s:
            readers = await self._readers(s, book_id)
            if not readers and not force:
                # The plan was made before this run's reviews; if they rejected every reader's
                # prompts, nobody is waiting for this book any more.
                await self._task_event(step, 'skipped', book_id=book_id)
                return 'skipped'
            loop = asyncio.get_running_loop()
            row = await s.get(LibraryBook, book_id)
            # A ready book keeps answering while a newer step runs (the index switches versions
            # atomically), so its readers see no progress for it.
            quiet = row is not None and row.status == 'ready' and not force

            def on_step(name, state):
                if state == 'started' and not quiet:
                    for ub in readers:
                        asyncio.run_coroutine_threadsafe(
                            self._book_event(ub, 'checking', STEP_MESSAGES.get(name, name), step=name), loop)

            await self._task_event(step, 'started', book_id=book_id)
            if row is not None and row.status != 'ready':
                row.status = 'processing'
                await s.commit()
            try:
                ran = await asyncio.to_thread(self.pipeline.run, book_id, steps=[step], force=force, on_step=on_step)
                outcome = 'done' if ran else 'up_to_date'
            except StepFailed as exc:
                log.info('%s of %s stopped: %s', step, book_id, exc)
                outcome = 'rejected' if exc.step == 'structure' and exc.code != 'error' else 'failed'
                if outcome == 'failed':
                    await self._sync(s, self.library.get(book_id))
                    await self._task_event(step, 'failed', book_id=book_id, error=str(exc))
                    raise
            await self._sync(s, self.library.get(book_id))
            await self._task_event(step, outcome, book_id=book_id)
            return outcome

    # --- finishing up ----------------------------------------------------------------------

    async def finalize(self, plan):
        """Personas for owners without prompts, and news for every reader this run concerned:
        readers of the books it processed, owners whose prompts it reviewed, and new readers of
        books that were already ready (shared uploads)."""
        processed = {b['book_id'] for b in plan.get('books', [])}
        refreshed = {b['book_id'] for b in plan.get('books', []) if b.get('ready')}
        only = set(plan.get('only') or ())               # a run for chosen books leaves the others alone
        reviewed = set(plan.get('reviews', []))
        told = 0
        async with self.db.sessions() as s:
            for ub in list((await s.execute(select(UserBook))).scalars()):
                if only and ub.book_id not in only:
                    continue
                try:
                    profile = self.library.get(ub.book_id)
                except UnknownBook:
                    continue
                if ub.book_id in processed:
                    await self._sync(s, profile)
                if ub.prompt_status in ('rejected', 'error'):
                    continue                                   # already told by the review
                if profile.status == 'ready':
                    news = (ub.book_id in processed and ub.book_id not in refreshed) or str(ub.id) in reviewed                         or ub.prompt_status == 'pending'
                    if ub.prompt_status == 'pending' and not persona_text(ub.system_prompt, ub.user_prompt):
                        # One persona per book, shared by every reader who gave no prompts: they
                        # then also share cached answers (marginalia.agent.cache).
                        if not profile.persona:
                            await self._book_event(ub, 'checking', 'Preparing the assistant for this book...',
                                                   step='persona')
                            profile.persona = await self.guard.write_persona(
                                book_name(profile.title, profile.author), profile.title, profile.author)
                            self.library.save(profile)
                        ub.system_prompt = profile.persona
                        ub.prompt_status = 'generated'
                        await s.commit()
                    if news and ub.prompt_status in ('approved', 'generated'):
                        await self._book_event(ub, 'ready', f'Ready: {len(profile.chapters)} chapters. Ask away.',
                                               chapters=len(profile.chapters))
                        told += 1
                elif ub.book_id in processed:
                    if profile.status == 'rejected':
                        message = 'No chapters were found in this PDF, so it cannot be used.'
                        code = (profile.error or {}).get('code')
                    else:
                        # Anything this run was meant to finish that is not ready: a step failed,
                        # possibly before the pipeline could record it on the book.
                        code = (profile.error or {}).get('code') or 'processing_failed'
                        message = 'Processing this book failed. An admin can retry it.'
                        await self._mark_failed(s, ub.book_id)
                    await self._book_event(ub, 'rejected' if profile.status == 'rejected' else 'failed',
                                           message, error_code=code)
                    told += 1
        await self._task_event('finalize', 'done', notified=told)
        return told
