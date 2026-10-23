"""What the real-time worker does. Book processing is not here: Airflow runs it in hourly
batches (marginalia.batch). The worker handles what cannot wait:

    screen_question  the question guard and the answer cache (worker/screening.py): refuse it,
                     answer it from the cache, or queue it for the agent
    answer_question  run the agent and stream its progress (thinking, searching, reading...)
    delete_book      the last reader removed the book: delete its index, cached answers and files
"""

import asyncio
import logging
import time
import uuid
from contextlib import AsyncExitStack
from datetime import datetime, timezone

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from sqlalchemy import select

from marginalia.agent.react_agent import BookAgent, LlmGrader, book_name, collect_evidence, make_llm, mcp_tools, \
    stdio_connection
from marginalia import metrics
from marginalia.agent.cache import scope_of
from marginalia.agent.sampling import Sampling
from marginalia.api import events, jobs
from marginalia.api.db import LibraryBook, Question, UserBook
from marginalia.guard.prompts import persona_text
from marginalia.library import UnknownBook

log = logging.getLogger(__name__)

def _now():
    return datetime.now(timezone.utc)


class AgentPool:
    """One MCP server process per book, shared by every question about it; replaced when the
    book's index version changes (a re-index)."""

    def __init__(self, settings):
        self.settings = settings
        self._books = {}          # book_id -> (index_version, AsyncExitStack, tools)
        self._lock = asyncio.Lock()

    async def tools(self, profile):
        async with self._lock:
            current = self._books.get(profile.book_id)
            if current and current[0] == profile.index_version:
                return current[2]
            if current:
                await current[1].aclose()
            stack = AsyncExitStack()
            tools = await stack.enter_async_context(mcp_tools(stdio_connection(profile.book_id)))
            self._books[profile.book_id] = (profile.index_version, stack, tools)
            return tools

    async def agent(self, profile, persona, grader_hook=None, sampling=None):
        """An agent for one question. `sampling` is the reader's (marginalia.agent.sampling); the grader
        always samples with the server's settings."""
        tools = await self.tools(profile)
        sampling = sampling or Sampling()
        llm = make_llm(self.settings, sampling)
        grader = LlmGrader(make_llm(self.settings, role='grader'), book_name(profile))
        if grader_hook:
            base = grader

            async def grader(question, evidence, answer):
                await grader_hook()
                return await base(question, evidence, answer)
        return BookAgent(llm, tools, profile=profile, grader=grader, persona=persona,
                         max_tool_calls=sampling.resolve(self.settings).max_searches)

    async def close(self):
        for _, stack, _ in self._books.values():
            await stack.aclose()
        self._books.clear()


class Handlers:
    def __init__(self, db, redis, library, pipeline, agents, screener=None):
        self.db, self.redis, self.library, self.pipeline, self.agents = db, redis, library, pipeline, agents
        self.screener = screener

    async def _readers(self, s, book_id):
        return list((await s.execute(select(UserBook).where(UserBook.book_id == book_id))).scalars())

    def _emitter(self, q, ub):
        async def emit(status, message, type_='question.status', **extra):
            await events.publish(self.redis, events.event(type_, question_id=q.id, user_book_id=ub.id,
                                                          status=status, message=message, **extra), [q.user_id])
        return emit

    def _scope(self, ub, q):
        """Answers are shared only between questions asked with the same prompts, model and sampling."""
        return scope_of(persona_text(ub.system_prompt, ub.user_prompt), self.screener.settings.agent_model,
                        (q.details or {}).get('sampling'))

    async def delete_book(self, book_id):
        async with self.db.sessions() as s:
            if await self._readers(s, book_id):
                return                       # someone added it again in the meantime
            await asyncio.to_thread(self.pipeline.delete, book_id)
            if self.screener is not None:
                try:
                    await asyncio.to_thread(self.screener.forget_book, book_id)
                except Exception:
                    log.exception('could not drop the cached answers of %s', book_id)
            row = await s.get(LibraryBook, book_id)
            if row:
                await s.delete(row)
                await s.commit()

    async def screen_question(self, question_id, fresh=False):
        async with self.db.sessions() as s:
            q = await s.get(Question, uuid.UUID(str(question_id)))
            if q is None or q.status != 'queued' or (q.details or {}).get('screened'):
                return                       # already handled (a redelivered job)
            ub = await s.get(UserBook, q.user_book_id)
            emit = self._emitter(q, ub)
            outcome = None
            if self.screener is not None:
                started = time.perf_counter()
                try:
                    profile = self.library.get(ub.book_id)
                    outcome = await asyncio.to_thread(self.screener.screen, profile, q.text, self._scope(ub, q), fresh)
                except Exception:
                    log.exception('screening question %s failed; sending it to the agent', q.id)
                metrics.SCREEN_SECONDS.observe(time.perf_counter() - started)
            details = {**(q.details or {}), 'screened': True, 'fresh': fresh}
            if outcome is not None:
                details['guard'] = outcome.screening.details
            if outcome is not None and outcome.action == 'refuse':
                details['refused'] = outcome.screening.code
                q.status, q.error, q.details, q.finished_at = 'refused', outcome.screening.message, details, _now()
                q.answered_by = 'guard'
                await s.commit()
                await emit('refused', outcome.screening.message, 'question.refused', code=outcome.screening.code)
                metrics.record_question('refused', outcome.screening.code)
                log.info('question refused', extra={'question_id': str(q.id), 'reason': outcome.screening.code})
                return
            if outcome is not None and outcome.action == 'cached':
                hit = outcome.hit
                details.update(supported=hit.supported, cached={'kind': hit.kind, 'similarity': hit.similarity})
                q.status, q.answer, q.citations, q.details, q.finished_at = \
                    'done', hit.answer, hit.citations, details, _now()
                q.answered_by = 'cache'
                await s.commit()
                await emit('done', 'Answered from an earlier question.', 'question.done', answer=hit.answer,
                           citations=hit.citations, supported=hit.supported, cached=details['cached'])
                metrics.record_question('cache')
                log.info('question answered from the cache',
                         extra={'question_id': str(q.id), 'match': hit.kind, 'similarity': hit.similarity})
                return
            q.details = details
            await s.commit()
            await jobs.enqueue(self.redis, 'ask', 'answer_question', question_id=str(q.id))
            await emit('queued', 'Waiting for a free slot...')

    async def answer_question(self, question_id):
        async with self.db.sessions() as s:
            # Job payloads are JSON, so the id is a string; UUID columns need UUIDs on every database.
            q = await s.get(Question, uuid.UUID(str(question_id)))
            if q is None or q.status in ('done', 'failed', 'refused'):
                return                       # already handled (a redelivered job)
            ub = await s.get(UserBook, q.user_book_id)
            emit = self._emitter(q, ub)
            q.status = 'running'
            await s.commit()
            started = time.perf_counter()
            await emit('thinking', 'Thinking...')
            loop = asyncio.get_running_loop()
            pending = []

            def on_message(m):
                coro = None
                if isinstance(m, AIMessage) and m.tool_calls:
                    call = m.tool_calls[0]
                    coro = emit('searching', _describe(call['name'], call['args']), tool=call['name'], args=call['args'])
                elif isinstance(m, ToolMessage):
                    found = [e.citation for e in collect_evidence([m])]
                    coro = emit('reading', f'Reading {len(found)} passage(s)...' if found else 'Thinking...',
                                citations=found[:5])
                elif isinstance(m, HumanMessage) and m.content != q.text:
                    coro = emit('revising', 'Double-checking the answer against the book...')
                elif isinstance(m, AIMessage):
                    coro = emit('answering', 'Writing the answer...')
                if coro:
                    pending.append(loop.create_task(coro))

            try:
                profile = self.library.get(ub.book_id)
                agent = await self.agents.agent(profile, persona_text(ub.system_prompt, ub.user_prompt),
                                                grader_hook=lambda: emit('checking', 'Checking the answer...'),
                                                sampling=Sampling(**(q.details or {}).get('sampling', {})))
                result = await agent.ask(q.text, on_message=on_message)
            except (UnknownBook, Exception) as exc:
                log.exception('question %s failed', q.id)
                q.status, q.error, q.finished_at = 'failed', f'{type(exc).__name__}: {exc}', _now()
                q.answered_by = 'agent'
                await s.commit()
                await asyncio.gather(*pending, return_exceptions=True)
                await emit('failed', 'Something went wrong while answering. Please try again.', 'question.failed')
                metrics.record_question('failed')
                return
            await asyncio.gather(*pending, return_exceptions=True)
            q.status, q.answer, q.citations, q.finished_at = 'done', result.answer, result.citations, _now()
            q.answered_by = 'agent'
            q.details = {**(q.details or {}), 'tool_calls': result.tool_calls, 'supported': result.supported,
                         'unsupported_claims': result.unsupported_claims, 'revisions': result.revisions,
                         'unknown_citations': result.unknown_citations, 'budget_exhausted': result.budget_exhausted,
                         'tokens': getattr(result, 'usage', None)}
            await s.commit()
            await emit('done', 'Done.', 'question.done', answer=result.answer, citations=result.citations,
                       supported=result.supported)
            seconds = time.perf_counter() - started
            metrics.record_question('agent')
            metrics.record_answer(result, seconds)
            model = getattr(getattr(self.agents, 'settings', None), 'agent_model', 'unknown')
            metrics.record_tokens(getattr(result, 'usage', None), model)
            tokens = (getattr(result, 'usage', None) or {}).get('total') or {}
            log.info('question answered', extra={'question_id': str(q.id), 'seconds': round(seconds, 1),
                                                 'tool_calls': len(result.tool_calls), 'supported': result.supported,
                                                 'tokens': tokens.get('total'), 'tokens_per_second': tokens.get('per_second')})
            if self.screener is not None:
                try:
                    await asyncio.to_thread(self.screener.remember, profile, self._scope(ub, q), q.text, result, q.id)
                except Exception:
                    log.exception('could not cache the answer to %s', q.id)


def _describe(tool, args):
    query = args.get('query')
    if tool == 'search_passages':
        return f'Searching the book for "{query}"...'
    if tool == 'search_quotes':
        return f'Looking for what was said about "{query}"...'
    if tool == 'search_chapter_summaries':
        return f'Looking through chapter summaries for "{query}"...'
    if tool == 'get_surrounding_text':
        return 'Reading the surrounding text...'
    if tool == 'get_chapter_summary':
        return 'Reading a chapter summary...'
    return 'Looking at the chapters...'
