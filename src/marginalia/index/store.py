"""Qdrant schema and hybrid search, shared by every book.

One collection holds every retrievable unit of every book. Points are tagged with
  book_id        whose they are; a tenant index keeps each book's points together on disk
  index_version  which indexing run made them; a book's profile names its live version,
                 so a re-index is invisible until it finishes and the old version is deleted
  kind           chunk (~1000-character passage) | quote (a line of dialogue) | summary (a chapter)
Each point carries a named dense vector ("e5") and a sparse BM25 vector ("bm25").

Every read goes through a Scope, so a query can never see another book or a half-built version.
"""

import uuid
from dataclasses import dataclass, field

from qdrant_client import QdrantClient, models

DENSE = 'e5'
SPARSE = 'bm25'
KINDS = ('chunk', 'quote', 'summary')
_ID_NAMESPACE = uuid.UUID('5f0c6a8e-3c1e-4a8e-9a57-0b6f4d1c2e10')


@dataclass(frozen=True)
class Scope:
    book_id: str
    index_version: int


@dataclass
class Record:
    book_id: str
    index_version: int
    kind: str
    text: str
    part: int
    chapter: int
    chapter_index: int
    label: str                 # "Part III, Chapter V", "Chapter One"
    seq: int                   # position among this chapter's records of the same kind
    char_start: int = 0
    char_end: int = 0
    extra: dict = field(default_factory=dict)

    @property
    def id(self):
        # Deterministic, so re-running a step overwrites instead of duplicating.
        key = f'{self.book_id}:{self.index_version}:{self.kind}:{self.chapter_index}:{self.seq}'
        return str(uuid.uuid5(_ID_NAMESPACE, key))

    def payload(self):
        return {
            'book_id': self.book_id, 'index_version': self.index_version, 'kind': self.kind,
            'text': self.text, 'part': self.part, 'chapter': self.chapter, 'chapter_index': self.chapter_index,
            'label': self.label, 'seq': self.seq, 'char_start': self.char_start, 'char_end': self.char_end,
            **self.extra,
        }


def ensure_collection(client: QdrantClient, name, dim, recreate=False):
    if recreate and client.collection_exists(name):
        client.delete_collection(name)
    if client.collection_exists(name):
        existing = client.get_collection(name).config.params.vectors[DENSE].size
        if existing != dim:
            raise ValueError(
                f'collection {name!r} has {existing}-d vectors but the embedder makes {dim}-d; '
                'recreate it (all books must be re-indexed)')
    else:
        client.create_collection(
            name,
            vectors_config={DENSE: models.VectorParams(size=dim, distance=models.Distance.COSINE)},
            sparse_vectors_config={SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)},
        )
    # Idempotent, so collections made before an index was added get it too.
    client.create_payload_index(name, 'book_id', field_schema=models.KeywordIndexParams(
        type=models.KeywordIndexType.KEYWORD, is_tenant=True))
    client.create_payload_index(name, 'kind', field_schema=models.PayloadSchemaType.KEYWORD)
    for key in ('index_version', 'part', 'chapter', 'chapter_index', 'seq'):
        client.create_payload_index(name, key, field_schema=models.PayloadSchemaType.INTEGER)


def upsert(client: QdrantClient, name, records, dense, sparse, batch_size=128):
    for i in range(0, len(records), batch_size):
        batch = records[i:i + batch_size]
        points = [
            models.PointStruct(id=r.id, vector={DENSE: d, SPARSE: s}, payload=r.payload())
            for r, d, s in zip(batch, dense[i:i + batch_size], sparse[i:i + batch_size])
        ]
        client.upsert(name, points=points, wait=True)


def _match(key, value):
    return models.FieldCondition(key=key, match=models.MatchValue(value=value))


def _filter(scope, kind=None, part=None, chapter=None, chapter_index=None, seq_range=None):
    must = [_match('book_id', scope.book_id), _match('index_version', scope.index_version)]
    must += [_match(k, v) for k, v in (('kind', kind), ('part', part), ('chapter', chapter),
                                       ('chapter_index', chapter_index)) if v is not None]
    if seq_range is not None:
        must.append(models.FieldCondition(key='seq', range=models.Range(gte=seq_range[0], lte=seq_range[1])))
    return models.Filter(must=must)


def delete_book(client: QdrantClient, name, book_id, keep_version=None):
    """Delete a book's points, except those of `keep_version` if given."""
    flt = models.Filter(must=[_match('book_id', book_id)],
                        must_not=[_match('index_version', keep_version)] if keep_version is not None else None)
    client.delete(name, points_selector=models.FilterSelector(filter=flt), wait=True)


def fetch(client: QdrantClient, name, scope, kind=None, part=None, chapter=None, chapter_index=None,
          seq_range=None, page_size=1000):
    """All payloads matching exact filters, in book order."""
    flt, out, offset = _filter(scope, kind, part, chapter, chapter_index, seq_range), [], None
    while True:
        points, offset = client.scroll(name, scroll_filter=flt, limit=page_size, offset=offset,
                                       with_payload=True, with_vectors=False)
        out += [{'id': p.id, **p.payload} for p in points]
        if offset is None:
            return sorted(out, key=lambda p: (p['chapter_index'], p['seq']))


def get(client: QdrantClient, name, scope, point_id):
    """A point by id, only if it belongs to the scope."""
    points = client.retrieve(name, [point_id], with_payload=True)
    if not points:
        return None
    payload = points[0].payload
    if (payload.get('book_id'), payload.get('index_version')) != (scope.book_id, scope.index_version):
        return None
    return {'id': points[0].id, **payload}


def hybrid_search(client: QdrantClient, name, scope, dense_query, sparse_query,
                  kind=None, part=None, chapter=None, limit=5, candidates=40):
    """Reciprocal-rank fusion of dense and BM25 results within one book."""
    flt = _filter(scope, kind, part, chapter)
    res = client.query_points(
        name,
        prefetch=[
            models.Prefetch(query=dense_query, using=DENSE, filter=flt, limit=candidates),
            models.Prefetch(query=sparse_query, using=SPARSE, filter=flt, limit=candidates),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        limit=limit,
        with_payload=True,
    )
    return res.points


def count(client: QdrantClient, name, scope=None, kind=None, book_id=None):
    """Points in a scope (optionally of one kind), or all points of a book across versions."""
    if scope is not None:
        flt = _filter(scope, kind)
    else:
        flt = models.Filter(must=[_match('book_id', book_id)] if book_id else [])
    return client.count(name, count_filter=flt, exact=True).count
