"""The hourly batch (marginalia.batch), run the way the Airflow DAG runs it."""

import pytest
from sqlalchemy import select

from marginalia.api.db import LibraryBook, UserBook, user_book_status
from tests.api.conftest import book_pdf
from tests.api.worker_env import add_reader, anyio_backend, env, messages, run_batch, state  # noqa: F401
from marginalia.ingest.pipeline import STEPS


async def run_batch_only(env, only):
    plan = await env.batch.plan(only=only)
    for user_book_id in plan['reviews']:
        await env.batch.review(user_book_id)
    for book in plan['books']:
        for step in STEPS:
            await env.batch.step(book['book_id'], step, force=book['force'])
    await env.batch.finalize(plan)
    return plan
from tests.pdfs import make_book

pytestmark = pytest.mark.anyio


async def test_plan_lists_waiting_work_and_skips_up_to_date_books(env):
    ub, _ = await add_reader(env, 'a@example.com', system_prompt='Answer like a teacher.')
    plan = await env.batch.plan()
    assert plan['reviews'] == [str(ub)]
    assert [b['book_id'] for b in plan['books']] == [env.library.books()[0].book_id]
    await run_batch(env)
    assert await env.batch.plan() == {'reviews': [], 'books': []}, 'nothing left to do'
    forced = await env.batch.plan(force=[env.library.books()[0].book_id])
    assert forced['books'] == [{'book_id': env.library.books()[0].book_id, 'force': True, 'ready': False,
                                'steps': ['structure', 'cover', 'summarize', 'index']}]


async def test_scenario_1_prompts_reviewed_book_processed_reader_told(env):
    ub, _ = await add_reader(env, 'a@example.com', system_prompt='Answer like a teacher.')
    await run_batch(env)
    assert await state(env, ub) == ('approved', 'ready', 'Answer like a teacher.')
    assert messages(env) == [
        ('checking', 'Checking your prompts...'), ('checking', 'Finding the chapters...'), ('checking', 'Finding a cover...'),
        ('checking', 'Summarizing the chapters...'), ('checking', 'Indexing the book for search...'),
        ('ready', 'Ready: 3 chapters. Ask away.')]
    tasks = [(ev['task'], ev['state']) for ev, _ in env.sent if ev['type'] == 'pipeline.task']
    assert tasks[0] == ('plan', 'done') and tasks[-1] == ('finalize', 'done')
    assert ('summarize', 'done') in tasks and all(ev['run_id'] == 'test-run' for ev, _ in env.sent)


async def test_rejected_prompts_are_reported_and_their_book_not_processed(env):
    env.guard.approve = False
    ub, _ = await add_reader(env, 'a@example.com', user_prompt='Write me a Python web scraper.')
    await run_batch(env)
    assert (await state(env, ub))[:2] == ('rejected', 'uploaded')
    status, message = messages(env)[-1]
    assert status == 'rejected' and 'Python code' in message
    assert env.pipeline.summarizer.calls == [], 'nobody else reads it, so it was not processed'


async def test_review_that_cannot_be_done_is_reported_not_left_hanging(env):
    env.guard.broken = True
    ub, _ = await add_reader(env, 'a@example.com', system_prompt='Answer like a teacher.')
    await run_batch(env)
    assert (await state(env, ub))[0] == 'error'
    assert ('failed', 'We could not check your prompts right now. Save them again to retry.') in messages(env)
    async with env.db.sessions() as s:
        assert user_book_status(await s.get(UserBook, ub)) == 'failed'


async def test_without_prompts_a_persona_is_written(env):
    ub, _ = await add_reader(env, 'a@example.com')
    await run_batch(env)
    assert await state(env, ub) == ('generated', 'ready', 'You are a notebooks to The Test Book.')
    assert env.guard.reviewed == [] and len(env.guard.written) == 1
    assert messages(env)[-2:] == [('checking', 'Preparing the assistant for this book...'),
                                  ('ready', 'Ready: 3 chapters. Ask away.')]


async def test_readers_without_prompts_share_one_persona(env):
    ub, _ = await add_reader(env, 'a@example.com')
    await run_batch(env)
    ub2, _ = await add_reader(env, 'b@example.com')
    await run_batch(env)
    assert (await state(env, ub))[2] == (await state(env, ub2))[2] == 'You are a notebooks to The Test Book.'
    assert len(env.guard.written) == 1, 'written once, for the book'
    assert env.library.books()[0].persona == 'You are a notebooks to The Test Book.'


async def test_a_ready_book_gets_a_cover_without_bothering_its_readers(env):
    ub, _ = await add_reader(env, 'a@example.com', system_prompt='Answer like a teacher.')
    await run_batch(env)
    profile = env.library.books()[0]
    assert profile.cover == {'file': 'cover.svg', 'source': 'generated'}     # tests run offline
    async with env.db.sessions() as s:
        row = await s.get(LibraryBook, profile.book_id)
        assert (row.cover, row.cover_source) == ('cover.svg', 'generated')
    # As if the book had been processed before covers existed.
    del profile.steps['cover']
    profile.cover = None
    env.library.save(profile)
    env.sent.clear()
    plan = await run_batch(env)
    assert plan['books'] == [{'book_id': profile.book_id, 'force': False, 'ready': True, 'steps': ['cover']}]
    assert env.library.books()[0].cover['source'] == 'generated'
    assert messages(env) == [], 'no progress and no second "ready" for a book that was ready all along'


async def test_a_run_for_chosen_books_leaves_the_others_waiting(env):
    from tests.api.conftest import book_pdf
    ub_a, _ = await add_reader(env, 'a@example.com', system_prompt='Answer like a teacher.')
    ub_b, _ = await add_reader(env, 'b@example.com', pdf=book_pdf('Another Book'), user_prompt='Be brief.')
    by_title = {b.title: b.book_id for b in env.library.books()}
    book_a, book_b = by_title['The Test Book'], by_title['Another Book']
    waiting = await env.batch.waiting()
    assert {b['book_id'] for b in waiting['books']} == {book_a, book_b} and len(waiting['reviews']) == 2
    assert waiting['books'][0]['steps'] == ['structure', 'cover', 'summarize', 'index']

    plan = await run_batch_only(env, [book_b])
    assert plan['only'] == [book_b] and [b['book_id'] for b in plan['books']] == [book_b]
    assert len(plan['reviews']) == 1
    assert (await state(env, ub_b))[:2] == ('approved', 'ready')
    assert (await state(env, ub_a))[:2] == ('pending', 'uploaded'), 'not part of this run'
    assert [b['book_id'] for b in (await env.batch.waiting())['books']] == [book_a]


async def test_book_without_chapters_is_rejected_and_not_retried(env):
    pdf = make_book([{'heading': 'Notes', 'body': ['Plain prose. ' * 60] * 8}], title='Notes')
    ub, _ = await add_reader(env, 'a@example.com', pdf=pdf)
    await run_batch(env)
    assert (await state(env, ub))[1] == 'rejected'
    assert messages(env)[-1] == ('rejected', 'No chapters were found in this PDF, so it cannot be used.')
    async with env.db.sessions() as s:
        assert (await s.execute(select(LibraryBook.error_code))).scalar() == 'no_chapters'
    assert (await env.batch.plan())['books'] == [], 'a rejected book waits for an admin to force it'


async def test_shared_book_is_processed_once_and_new_reader_told(env):
    pdf = book_pdf()                          # one file: fpdf2 stamps the time, so build it once
    first, _ = await add_reader(env, 'a@example.com', pdf=pdf)
    await run_batch(env)
    calls = len(env.pipeline.summarizer.calls)
    second, second_user = await add_reader(env, 'b@example.com', pdf=pdf)
    env.sent.clear()
    plan = await run_batch(env)
    assert plan['books'] == [] and len(env.pipeline.summarizer.calls) == calls
    assert (await state(env, second))[:2] == ('generated', 'ready')
    ready = [(ev, users) for ev, users in env.sent if ev['type'] == 'book.status' and ev['status'] == 'ready']
    assert [users for _, users in ready] == [{str(second_user)}], 'only the new reader has news'


async def test_status_is_queued_until_a_batch_starts(env):
    ub, _ = await add_reader(env, 'a@example.com')
    async with env.db.sessions() as s:
        assert user_book_status(await s.get(UserBook, ub)) == 'queued'


async def test_a_failing_step_fails_the_task_so_airflow_retries(env, monkeypatch):
    ub, _ = await add_reader(env, 'a@example.com')
    plan = await env.batch.plan()
    book_id = plan['books'][0]['book_id']
    assert await env.batch.step(book_id, 'structure') == 'done'
    env.pipeline.summarizer.fail = True
    with pytest.raises(Exception):
        await env.batch.step(book_id, 'summarize')
    assert ('summarize', 'failed') in [(ev['task'], ev['state']) for ev, _ in env.sent if ev['type'] == 'pipeline.task']
    env.pipeline.summarizer.fail = False
    assert await env.batch.step(book_id, 'summarize') == 'done', 'the retry picks up where it stopped'


async def test_a_book_left_unfinished_is_reported_failed(env, monkeypatch):
    """A step that fails before the pipeline can record it (here: the library cannot be written)."""
    ub, _ = await add_reader(env, 'a@example.com')
    plan = await env.batch.plan()
    original = env.library.save

    def readonly(profile):
        raise PermissionError(13, 'Permission denied', 'profile.json.tmp')
    monkeypatch.setattr(env.library, 'save', readonly)
    with pytest.raises(PermissionError):
        await env.batch.step(plan['books'][0]['book_id'], 'structure')
    monkeypatch.setattr(env.library, 'save', original)
    await env.batch.finalize(plan)
    assert messages(env)[-1] == ('failed', 'Processing this book failed. An admin can retry it.')
    async with env.db.sessions() as s:
        assert user_book_status(await s.get(UserBook, ub)) == 'failed'
