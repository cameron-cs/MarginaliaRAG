"""The MCP server over an in-memory Qdrant holding a small synthetic book."""

import asyncio

import pytest
from fastmcp import Client

from marginalia.mcp_server.server import build_server
from marginalia.ingest.pipeline import summary_records
from tests.conftest import SYNTHETIC_ID, book_retriever, synthetic_book, synthetic_profile

CHAPTERS, SUMMARIES = synthetic_book()


@pytest.fixture(scope='module')
def server():
    retriever = book_retriever('mcp_test')
    yield build_server(synthetic_profile(), lambda: retriever)
    retriever.client.close()


def call(server, tool, **args):
    async def run():
        async with Client(server) as c:
            return await c.call_tool(tool, args, raise_on_error=False)
    return asyncio.run(run())


def ok(server, tool, **args):
    res = call(server, tool, **args)
    assert not res.is_error, res.content
    return res.structured_content.get('result', res.structured_content)


def error_text(server, tool, **args):
    res = call(server, tool, **args)
    assert res.is_error
    return res.content[0].text


def test_tools_are_listed_with_descriptions(server):
    async def run():
        async with Client(server) as c:
            return await c.list_tools(), c.initialize_result.instructions
    tools, instructions = asyncio.run(run())
    assert {t.name for t in tools} == {
        'search_passages', 'search_quotes', 'search_chapter_summaries',
        'get_chapter_summary', 'get_surrounding_text', 'list_chapters'}
    assert all(len(t.description) > 40 for t in tools)
    assert "'The Synthetic Book' by A. Tester" in instructions and 'Marmeladov' in instructions
    assert 'Part IV = part 4: chapters 4-4' in instructions


def test_search_passages_returns_cited_chunks(server):
    hits = ok(server, 'search_passages', query='Lazarus Gospel', limit=2)
    assert hits[0]['citation'].startswith('Part IV, Chapter IV, passage ')
    assert 'Lazarus' in hits[0]['text']
    assert {'id', 'part', 'chapter', 'score'} <= hits[0].keys()


def test_search_passages_filters_by_location(server):
    hits = ok(server, 'search_passages', query='Lazarus Gospel', part=1, limit=10)
    assert hits and all(h['part'] == 1 for h in hits)
    hits = ok(server, 'search_passages', query='tavern', part=1, chapter=2, limit=10)
    assert hits and all((h['part'], h['chapter']) == (1, 2) for h in hits)


def test_a_chapter_that_never_mentions_the_query_says_where_it_is():
    retriever = book_retriever('mcp_hint_test')
    texts = lambda: {(c.part, c.chapter): c.text for c in CHAPTERS}      # noqa: E731
    hinted = build_server(synthetic_profile(), lambda: retriever, chapter_texts=texts)
    try:
        message = error_text(hinted, 'search_passages', query='What about Lazarus?', part=1, chapter=1)
        assert message == ('Part I, Chapter I never mentions "lazarus". They appear in: Part IV, Chapter IV (30x). '
                           'Search one of those chapters, or leave out `chapter`.')
        assert 'never mentions "unicorns"' in error_text(hinted, 'search_quotes', query='unicorns', part=1, chapter=2)
        assert ok(hinted, 'search_passages', query='Lazarus', part=4, chapter=4), 'the right chapter'
        assert ok(hinted, 'search_passages', query='Lazarus'), 'no chapter: no check'
        assert ok(hinted, 'search_passages', query='What is it about?', part=1, chapter=1), 'no content words'
    finally:
        retriever.client.close()


def test_search_quotes_only_returns_dialogue(server):
    [hit] = ok(server, 'search_quotes', query='titular counsellor daughter', limit=1)
    assert hit['text'] == 'I am a titular counsellor and my daughter Sonia walks the streets for us,'


def test_search_and_get_summaries(server):
    hits = ok(server, 'search_chapter_summaries', query='raising of Lazarus', limit=1)
    assert hits[0]['citation'] == 'Part IV, Chapter IV'
    summary = ok(server, 'get_chapter_summary', part=1, chapter=2)
    assert summary['summary'] == 'Part I, Chapter II. Marmeladov tells his story in the tavern.'


def test_get_surrounding_text_reads_in_order(server):
    hits = ok(server, 'search_passages', query='Lazarus Gospel', part=4, chapter=4, limit=10)
    middle = next(h for h in hits if not h['citation'].endswith('passage 1'))
    around = ok(server, 'get_surrounding_text', passage_id=middle['id'], before=1, after=1)
    numbers = [int(p['citation'].rsplit(' ', 1)[1]) for p in around]
    centre = int(middle['citation'].rsplit(' ', 1)[1])
    assert centre in numbers and numbers == sorted(numbers) and len(numbers) <= 3
    assert all(abs(n - centre) <= 1 for n in numbers)


def test_get_surrounding_text_from_a_quote(server):
    [quote] = ok(server, 'search_quotes', query='titular counsellor', limit=1)
    around = ok(server, 'get_surrounding_text', passage_id=quote['id'], before=0, after=0)
    assert len(around) == 1 and 'titular counsellor' in around[0]['text']


def test_list_chapters(server):
    chapters = ok(server, 'list_chapters')
    assert [(c['part'], c['chapter']) for c in chapters] == [(1, 1), (1, 2), (4, 4)]
    assert all(c['passages'] > 1 for c in chapters)


@pytest.mark.parametrize('tool,args,message', [
    ('search_passages', {'query': 'x y', 'part': 1, 'chapter': 3}, 'Part I has chapters 1-2'),
    ('search_passages', {'query': 'x y', 'chapter': 2}, 'needs `part`'),
    ('get_chapter_summary', {'part': 2, 'chapter': 1}, 'There is no part 2'),
    ('get_chapter_summary', {'part': 4, 'chapter': 1}, 'Part IV has chapters 4-4'),
    ('get_surrounding_text', {'passage_id': 'not-a-real-id'}, 'No passage with id'),
    ('search_passages', {'query': 'x y', 'limit': 50}, 'less than or equal to 10'),
    ('search_passages', {'query': 'x y', 'part': 9}, 'less than or equal to 4'),
])
def test_bad_arguments_give_actionable_errors(server, tool, args, message):
    assert message in error_text(server, tool, **args)


def test_summary_ids_are_not_passages(server):
    summary_id = next(r.id for r in summary_records(CHAPTERS, SUMMARIES, SYNTHETIC_ID, 1))
    assert 'No passage with id' in error_text(server, 'get_surrounding_text', passage_id=summary_id)


def test_retriever_is_built_lazily_and_once():
    calls = []

    def factory():
        calls.append(1)
        raise RuntimeError('Qdrant is down')

    srv = build_server(synthetic_profile(), factory)
    async def run():
        async with Client(srv) as c:
            await c.list_tools()
            assert calls == []                     # listing tools needs no backend
            res = await c.call_tool('list_chapters', {}, raise_on_error=False)
            return res
    res = asyncio.run(run())
    assert res.is_error and calls == [1]


def test_book_without_parts_needs_no_part():
    """A Harry Potter-like book: one unlabeled part; `chapter` alone is enough."""
    profile = synthetic_profile()
    profile.parts = [{'part': 1, 'label': '', 'chapters': 2}]
    profile.chapters = [c for c in profile.chapters if c['part'] == 1]
    retriever = book_retriever('mcp_flat')
    srv = build_server(profile, lambda: retriever)
    hits = ok(srv, 'search_passages', query='tavern clerk', chapter=2, limit=3)
    assert hits and all((h['part'], h['chapter']) == (1, 2) for h in hits)
    assert ok(srv, 'get_chapter_summary', chapter=2)['summary'].endswith('Marmeladov tells his story in the tavern.')
    async def run():
        async with Client(srv) as c:
            return c.initialize_result.instructions
    assert '2 chapters (1-2) and no parts' in asyncio.run(run())
    retriever.client.close()


def test_other_books_points_are_invisible():
    retriever = book_retriever('mcp_tenants')
    from tests.conftest import index_synthetic
    index_synthetic(retriever.client, 'mcp_tenants', book_id='someone-elses-book')
    srv = build_server(synthetic_profile(), lambda: retriever)
    hits = ok(srv, 'search_passages', query='Lazarus Gospel', limit=10)
    other = [r.id for r in index_synthetic(retriever.client, 'mcp_tenants', book_id='someone-elses-book')]
    assert hits and not {h['id'] for h in hits} & set(other)
    assert 'No passage with id' in error_text(srv, 'get_surrounding_text', passage_id=other[0])
    retriever.client.close()
