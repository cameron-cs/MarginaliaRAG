"""Shared fakes and the async environment for worker and batch tests: real pipeline on an
in-memory Qdrant with fake encoders, fake LLMs, SQLite and fakeredis."""

import json
from types import SimpleNamespace

import fakeredis
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from qdrant_client import QdrantClient

from marginalia.api.db import Database, LibraryBook, User, UserBook
from marginalia.batch.work import BatchWork
from marginalia.guard.prompts import PromptVerdict, ReviewFailed
from marginalia.ingest.pipeline import STEPS, Pipeline
from marginalia.library import Library
from marginalia.worker.handlers import Handlers
from tests.api.conftest import book_pdf, make_settings
from tests.conftest import FakeDense, FakeSparse
from tests.test_pipeline import FakeSummarizer


@pytest.fixture
def anyio_backend():
    return 'asyncio'


class FakeGuard:
    def __init__(self, approve=True):
        self.approve, self.reviewed, self.written, self.broken = approve, [], [], False

    async def review(self, book, system_prompt, user_prompt):
        self.reviewed.append((book, system_prompt, user_prompt))
        if self.broken:
            raise ReviewFailed('no verdict after 3 attempts')
        return PromptVerdict(self.approve, 'ok' if self.approve else 'irrelevant',
                             '' if self.approve else 'It asks for Python code, not about the book.')

    async def write_persona(self, book, title, author=None):
        self.written.append(book)
        return f'You are a guide to {title}.'


class FakeAgent:
    def __init__(self, fail=False):
        self.fail, self.asked = fail, []

    async def ask(self, question, on_message=None):
        self.asked.append(question)
        if self.fail:
            raise RuntimeError('Ollama is down')
        for m in (HumanMessage(question),
                  AIMessage('', tool_calls=[{'name': 'search_passages', 'args': {'query': 'the axe'}, 'id': '1'}]),
                  ToolMessage(json.dumps([{'citation': 'Chapter 2, passage 1', 'text': 'the axe'}]), tool_call_id='1'),
                  AIMessage('The axe was in chapter 2 [Chapter 2].')):
            on_message(m)
        return SimpleNamespace(answer='The axe was in chapter 2 [Chapter 2].', citations=['Chapter 2'],
                               tool_calls=[{'name': 'search_passages', 'args': {'query': 'the axe'}}], supported=True,
                               unsupported_claims=[], revisions=0, unknown_citations=[], budget_exhausted=False,
                               usage={'agent': {'calls': 2, 'prompt': 900, 'completion': 300, 'total': 1200,
                                                'seconds': 5.0, 'per_second': 60.0},
                                      'total': {'calls': 2, 'prompt': 900, 'completion': 300, 'total': 1200,
                                                'seconds': 5.0, 'per_second': 60.0}})


class FakeAgents:
    def __init__(self, agent):
        self.agent_obj, self.personas, self.samplings = agent, [], []

    async def agent(self, profile, persona, grader_hook=None, sampling=None):
        self.personas.append(persona)
        self.samplings.append(sampling)
        return self.agent_obj


@pytest.fixture
async def env(tmp_path, monkeypatch):
    settings = make_settings(tmp_path, collection='worker_test')
    db = Database(settings.database_url)
    await db.create_all()
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    library = Library(settings.library_dir)
    client = QdrantClient(':memory:')
    pipeline = Pipeline(library, settings, client=client, dense=FakeDense(), sparse=FakeSparse(),
                        summarizer=FakeSummarizer(), log=lambda *_: None)
    sent = []

    async def capture(_redis, ev, user_ids):
        sent.append((ev, {str(u) for u in user_ids}))
    monkeypatch.setattr('marginalia.worker.handlers.events.publish', capture)
    monkeypatch.setattr('marginalia.batch.work.events.publish', capture)
    guard, agent = FakeGuard(), FakeAgent()
    handlers = Handlers(db, redis, library, pipeline, FakeAgents(agent))
    batch = BatchWork(db, redis, library, pipeline, guard, run_id='test-run')
    env = SimpleNamespace(db=db, redis=redis, library=library, pipeline=pipeline, handlers=handlers, batch=batch,
                          sent=sent, guard=guard, agent=agent)
    yield env
    client.close()
    await db.dispose()


async def add_reader(env, email, pdf=None, **prompts):
    async with env.db.sessions() as s:
        user = User(email=email, password_hash='x')
        s.add(user)
        await s.flush()
        try:
            profile = env.library.add(pdf or book_pdf())
        except Exception as exc:          # DuplicateBook: share the existing library book
            profile = env.library.get(exc.book_id)
        if await s.get(LibraryBook, profile.book_id) is None:
            s.add(LibraryBook(book_id=profile.book_id, sha256=profile.sha256, title=profile.title,
                              author=profile.author, status=profile.status))
        ub = UserBook(user_id=user.id, book_id=profile.book_id, filename='book.pdf', **prompts)
        s.add(ub)
        await s.commit()
        return ub.id, user.id


async def state(env, user_book_id):
    async with env.db.sessions() as s:
        ub = await s.get(UserBook, user_book_id)
        return ub.prompt_status, ub.book.status, ub.system_prompt


def messages(env, type_='book.status'):
    return [(ev['status'], ev['message']) for ev, _ in env.sent if ev['type'] == type_]


async def run_batch(env, force=()):
    """One whole batch, as the Airflow DAG runs it: plan, reviews, per-book steps, finalize."""
    plan = await env.batch.plan(force)
    for user_book_id in plan['reviews']:
        await env.batch.review(user_book_id)
    for book in plan['books']:
        for step in STEPS:
            await env.batch.step(book['book_id'], step, force=book['force'])
    await env.batch.finalize(plan)
    return plan
