import pytest

from marginalia.config import Settings
from marginalia.index import store
from marginalia.index.retriever import BookNotReady, Retriever
from marginalia.index.store import Record, Scope
from tests.conftest import FakeDense, FakeSparse, synthetic_profile

COLLECTION = 'test_books'
BOOK, OTHER = Scope('book-a', 1), Scope('book-b', 1)

PASSAGES = [
    (1, 1, 'Raskolnikov climbed the stairs to the flat of the old pawnbroker Alyona Ivanovna.'),
    (1, 2, 'In the tavern Marmeladov told the story of his daughter Sonia and his drinking.'),
    (2, 1, 'Razumihin brought the doctor Zossimov to see his feverish friend.'),
    (4, 4, 'Sonia read the raising of Lazarus aloud from the New Testament.'),
]


def _records(scope, passages=PASSAGES):
    return [Record(scope.book_id, scope.index_version, 'chunk', text, part, ch, i, f'P{part}C{ch}', 0, 0, len(text))
            for i, (part, ch, text) in enumerate(passages, start=1)]


def _load(client, records):
    dense, sparse = FakeDense(), FakeSparse()
    store.ensure_collection(client, COLLECTION, dense.dim)
    texts = [r.text for r in records]
    store.upsert(client, COLLECTION, records, dense.embed_passages(texts), sparse.embed_passages(texts))


@pytest.fixture
def loaded(memory_client):
    _load(memory_client, _records(BOOK))
    # Another book with a passage that matches the same words: it must never show up.
    _load(memory_client, _records(OTHER, [(1, 1, 'Sonia read the raising of Lazarus in another book entirely.')]))
    return memory_client


def _search(client, query, scope=BOOK, **kw):
    dense, sparse = FakeDense(), FakeSparse()
    return store.hybrid_search(client, COLLECTION, scope, dense.embed_query(query), sparse.embed_query(query), **kw)


def test_hybrid_search_finds_keyword_match(loaded):
    hits = _search(loaded, 'Lazarus New Testament', limit=1)
    assert hits[0].payload['text'].startswith('Sonia read the raising of Lazarus aloud')


def test_search_never_crosses_books(loaded):
    assert {h.payload['book_id'] for h in _search(loaded, 'Sonia Lazarus', limit=10)} == {'book-a'}
    assert [h.payload['text'] for h in _search(loaded, 'Lazarus', scope=OTHER)] == [
        'Sonia read the raising of Lazarus in another book entirely.']


def test_filters_by_part_chapter_and_kind(loaded):
    assert {h.payload['part'] for h in _search(loaded, 'Sonia', part=1, limit=5)} == {1}
    assert [h.payload['label'] for h in _search(loaded, 'Sonia', part=4, chapter=4)] == ['P4C4']
    assert _search(loaded, 'Sonia', kind='quote') == []


def test_upsert_is_idempotent(loaded):
    _load(loaded, _records(BOOK))
    assert store.count(loaded, COLLECTION, BOOK) == len(PASSAGES)


def test_get_refuses_points_of_another_scope(loaded):
    other_id = _records(OTHER, [(1, 1, 'x')])[0].id
    assert store.get(loaded, COLLECTION, BOOK, other_id) is None
    assert store.get(loaded, COLLECTION, OTHER, other_id)['book_id'] == 'book-b'


def test_versions_are_isolated_and_old_ones_deleted(loaded):
    v2 = Scope('book-a', 2)
    _load(loaded, _records(v2, PASSAGES[:2]))
    assert store.count(loaded, COLLECTION, BOOK) == 4 and store.count(loaded, COLLECTION, v2) == 2
    store.delete_book(loaded, COLLECTION, 'book-a', keep_version=2)
    assert store.count(loaded, COLLECTION, BOOK) == 0 and store.count(loaded, COLLECTION, v2) == 2
    assert store.count(loaded, COLLECTION, OTHER) == 1, 'other books are untouched'
    store.delete_book(loaded, COLLECTION, 'book-a')
    assert store.count(loaded, COLLECTION, book_id='book-a') == 0


def test_payload_carries_citation_and_tenant_fields(loaded):
    payload = _search(loaded, 'pawnbroker', limit=1)[0].payload
    assert {'book_id', 'index_version', 'kind', 'text', 'part', 'chapter', 'chapter_index', 'label', 'seq',
            'char_start', 'char_end'} <= payload.keys()


def test_point_ids_differ_per_book_and_version():
    ids = {r.id for s in (BOOK, OTHER, Scope('book-a', 2)) for r in _records(s)}
    assert len(ids) == 3 * len(PASSAGES)


def test_dimension_mismatch_is_reported(memory_client):
    store.ensure_collection(memory_client, COLLECTION, 32)
    store.ensure_collection(memory_client, COLLECTION, 32)   # existing, same size: fine
    with pytest.raises(ValueError, match='re-indexed'):
        store.ensure_collection(memory_client, COLLECTION, 768)
    store.ensure_collection(memory_client, COLLECTION, 768, recreate=True)


def test_fetch_pages_through_everything_in_book_order(loaded):
    rows = store.fetch(loaded, COLLECTION, BOOK, kind='chunk', page_size=1)
    assert [r['chapter_index'] for r in rows] == [1, 2, 3, 4]
    assert [r['label'] for r in store.fetch(loaded, COLLECTION, BOOK, part=1, chapter=2)] == ['P1C2']


def test_retriever_is_bound_to_one_book(loaded):
    retriever = Retriever(BOOK, Settings(collection=COLLECTION), client=loaded, dense=FakeDense(), sparse=FakeSparse())
    [hit] = retriever.search('Zossimov doctor', limit=1)
    assert hit['label'] == 'P2C1' and 'score' in hit and 'id' in hit
    assert all(r['book_id'] == 'book-a' for r in retriever.fetch(kind='chunk'))


def test_retriever_refuses_books_that_are_not_ready():
    profile = synthetic_profile()
    profile.status, profile.index_version = 'processing', None
    with pytest.raises(BookNotReady):
        Retriever.for_book(profile)
