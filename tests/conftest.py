import math
import os
import re
import zlib

import pytest
from qdrant_client import QdrantClient, models

# Tests never look up covers online; the cover tests pass a fake HTTP client instead.
os.environ['CP_COVERS_ONLINE'] = '0'

_TOKEN = re.compile(r'[a-z]+')


def _tokens(text):
    return _TOKEN.findall(text.lower().removeprefix('query: ').removeprefix('passage: '))


class FakeDense:
    """Bag-of-words hashed into a small normalized vector: deterministic, no model download."""

    dim = 32

    def _vec(self, text):
        v = [0.0] * self.dim
        for tok in _tokens(text):
            v[zlib.crc32(tok.encode()) % self.dim] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed_passages(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


class FakeSparse:
    def _vec(self, text):
        counts = {}
        for tok in _tokens(text):
            idx = zlib.crc32(tok.encode())
            counts[idx] = counts.get(idx, 0) + 1.0
        return models.SparseVector(indices=list(counts), values=list(counts.values()))

    def embed_passages(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


def _chapter_text(topic, n=30):
    return ' '.join(f'Sentence {i} of the chapter tells how {topic} went on that day.' for i in range(n))


SYNTHETIC_ID = 'synthetic-book-00000000'


def synthetic_book():
    """Three small chapters in two parts, with summaries: enough to exercise every tool."""
    from marginalia.ingest.book import Chapter
    chapters = [
        Chapter(1, 1, 1, _chapter_text('Raskolnikov visited the pawnbroker')
                + ' ‘I am a titular counsellor and my daughter Sonia walks the streets for us,’ said Marmeladov.',
                part_label='Part I'),
        Chapter(1, 2, 2, _chapter_text('the drunken clerk spoke in the tavern'), part_label='Part I'),
        Chapter(4, 4, 3, _chapter_text('Sonia read about Lazarus from the Gospel'), part_label='Part IV'),
    ]
    summaries = ['Raskolnikov visits the pawnbroker and meets Marmeladov.',
                 'Marmeladov tells his story in the tavern.',
                 'Sonia reads the raising of Lazarus to Raskolnikov.']
    return chapters, summaries


def synthetic_profile(version=1):
    from marginalia.library import BookProfile
    chapters, _ = synthetic_book()
    return BookProfile(
        book_id=SYNTHETIC_ID, title='The Synthetic Book', author='A. Tester', source_filename='x.pdf',
        sha256='0' * 64, num_bytes=1, status='ready', index_version=version, quote_style='single_curly',
        parts=[{'part': 1, 'label': 'Part I', 'chapters': 2}, {'part': 4, 'label': 'Part IV', 'chapters': 1}],
        chapters=[{'index': c.index, 'part': c.part, 'chapter': c.chapter, 'label': c.label, 'title': None,
                   'start_page': 0, 'chars': len(c.text)} for c in chapters],
        names=['Raskolnikov', 'Sonia', 'Marmeladov'])


def index_synthetic(client, collection, book_id=SYNTHETIC_ID, version=1, dense=None, sparse=None):
    from marginalia.index import store
    from marginalia.ingest.pipeline import build_records, summary_records
    chapters, summaries = synthetic_book()
    dense, sparse = dense or FakeDense(), sparse or FakeSparse()
    records = (build_records(chapters, book_id, version, 'single_curly', chunk_target=300, chunk_overlap=60)
               + summary_records(chapters, summaries, book_id, version))
    store.ensure_collection(client, collection, dense.dim)
    texts = [r.text for r in records]
    store.upsert(client, collection, records, dense.embed_passages(texts), sparse.embed_passages(texts))
    return records


def book_retriever(collection):
    """A Retriever over the synthetic book in an in-memory Qdrant, with fake encoders."""
    from marginalia.config import Settings
    from marginalia.index.retriever import Retriever
    from marginalia.index.store import Scope

    client, dense, sparse = QdrantClient(':memory:'), FakeDense(), FakeSparse()
    index_synthetic(client, collection, dense=dense, sparse=sparse)
    return Retriever(Scope(SYNTHETIC_ID, 1), Settings(collection=collection), client=client, dense=dense,
                     sparse=sparse)


@pytest.fixture
def memory_client():
    client = QdrantClient(':memory:')
    yield client
    client.close()
