"""End-to-end checks against the Docker Qdrant and the real library.

    docker compose up -d
    python -m marginalia add crime-and-punishment.pdf
    pytest -m integration
"""

import asyncio
import sys
from pathlib import Path

import httpx
import pytest

from marginalia.config import Settings
from marginalia.index import store
from marginalia.index.retriever import Retriever
from marginalia.library import Library, UnknownBook

pytestmark = pytest.mark.integration
settings = Settings()
ROOT = Path(__file__).resolve().parents[1]


def _book(ref):
    try:
        httpx.get(f'{settings.qdrant_url}/collections/{settings.collection}', timeout=2).raise_for_status()
        profile = Library().resolve(ref)
    except (httpx.HTTPError, UnknownBook):
        return None
    return profile if profile.status == 'ready' else None


CRIME = _book('crime-and-punishment')
HARRY = _book('harry-potter')
needs_crime = pytest.mark.skipif(CRIME is None, reason='Qdrant not running or Crime and Punishment not ingested')
needs_harry = pytest.mark.skipif(HARRY is None, reason='Harry Potter not ingested (local-only book)')


@pytest.fixture(scope='module')
def crime():
    return Retriever.for_book(CRIME)


@needs_crime
def test_live_version_is_complete(crime):
    for kind in ('chunk', 'quote', 'summary'):
        assert store.count(crime.client, settings.collection, crime.scope, kind) == CRIME.counts[kind]
    # Only the live version exists: earlier versions were deleted when it went live.
    assert store.count(crime.client, settings.collection, book_id=CRIME.book_id) == sum(CRIME.counts.values())


@needs_crime
@pytest.mark.parametrize('query,kind,expect_label,expect_word', [
    ('Sonia reads the raising of Lazarus to Raskolnikov', 'chunk', 'Part IV', 'Lazarus'),
    ('Raskolnikov kills the old pawnbroker with an axe', 'chunk', 'Part I', 'axe'),
    ('Marmeladov in the tavern talks about his daughter', None, 'Part I', 'Marmeladov'),
])
def test_known_scenes_are_retrieved(crime, query, kind, expect_label, expect_word):
    hits = crime.search(query, kind=kind, limit=5)
    assert any(h['label'].startswith(expect_label) and expect_word in h['text'] for h in hits), \
        [(h['label'], h['text'][:80]) for h in hits]


@needs_crime
def test_ascii_spelling_matches_diacritic_name_in_bm25(crime):
    # BM25 alone: e5 already matches the name through subwords, so hybrid search would hide a regression.
    res = crime.client.query_points(
        settings.collection, query=crime.sparse.embed_query('Svidrigailov'), using=store.SPARSE, limit=5,
        query_filter=store._filter(crime.scope), with_payload=True)
    assert len(res.points) == 5
    assert all('Svidrigaïlov' in p.payload['text'] for p in res.points)


@needs_crime
def test_summaries_are_clean(crime):
    summaries = crime.fetch(kind='summary')
    assert len(summaries) == 41
    leaked = [s['label'] for s in summaries if 'the user' in s['text'].lower() or s['text'].startswith(('Okay', 'Hmm'))]
    assert not leaked, f'model reasoning stored as summary: {leaked}'


@needs_crime
@needs_harry
def test_books_do_not_leak_into_each_other(crime):
    harry = Retriever.for_book(HARRY, client=crime.client, dense=crime.dense, sparse=crime.sparse)
    assert all(h['book_id'] == CRIME.book_id for h in crime.search('Harry Potter Hogwarts wand', limit=10))
    hits = harry.search('Hogwarts letter', kind='chunk', limit=5)
    assert hits and all(h['book_id'] == HARRY.book_id for h in hits)
    assert any('Hogwarts' in h['text'] for h in hits)


@needs_crime
def test_mcp_server_over_stdio():
    """Spawn `python -m marginalia.mcp_server --book …` as an MCP client would and use it end to end."""
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport

    async def run():
        transport = StdioTransport(command=sys.executable, args=['-m', 'marginalia.mcp_server', '--book', CRIME.book_id],
                                   cwd=str(ROOT))
        async with Client(transport, timeout=120) as c:
            assert len(await c.list_tools()) == 6
            hits = (await c.call_tool('search_passages', {'query': 'Sonia reads the raising of Lazarus'})).data
            around = (await c.call_tool('get_surrounding_text', {'passage_id': hits[0].id})).data
            chapters = (await c.call_tool('list_chapters', {})).data
            summary = (await c.call_tool('get_chapter_summary', {'part': 7, 'chapter': 2})).data
            return hits, around, chapters, summary, c.initialize_result.instructions

    hits, around, chapters, summary, instructions = asyncio.run(run())
    assert any(h.citation.startswith('Part IV') and 'Lazarus' in h.text for h in hits)
    assert len(around) == 3
    assert len(chapters) == 41 and sum(c.passages for c in chapters) == CRIME.counts['chunk']
    assert summary.citation == 'Epilogue, Chapter II'
    assert 'Razumihin' in instructions and 'Epilogue = part 7' in instructions
