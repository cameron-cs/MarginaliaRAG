"""Query side of one book's index; the MCP server wraps this."""

from qdrant_client import QdrantClient

from marginalia.config import Settings
from marginalia.index import store
from marginalia.index.embeddings import Bm25Encoder, E5Embedder


class BookNotReady(RuntimeError):
    pass


class Retriever:
    """Searches and reads one book's live index version, and nothing else."""

    def __init__(self, scope, settings=None, client=None, dense=None, sparse=None):
        self.scope = scope
        self.settings = settings or Settings()
        self.client = client or QdrantClient(url=self.settings.qdrant_url)
        self.dense = dense or E5Embedder(self.settings.embed_model)
        self.sparse = sparse or Bm25Encoder()

    @classmethod
    def for_book(cls, profile, **kwargs):
        if profile.status != 'ready' or profile.index_version is None:
            raise BookNotReady(f'{profile.book_id} is not indexed yet (status: {profile.status})')
        return cls(store.Scope(profile.book_id, profile.index_version), **kwargs)

    @property
    def collection(self):
        return self.settings.collection

    def search(self, query, kind=None, part=None, chapter=None, limit=5):
        points = store.hybrid_search(
            self.client, self.collection, self.scope,
            self.dense.embed_query(query), self.sparse.embed_query(query),
            kind=kind, part=part, chapter=chapter, limit=limit,
        )
        return [{'score': p.score, 'id': p.id, **p.payload} for p in points]

    def fetch(self, **filters):
        return store.fetch(self.client, self.collection, self.scope, **filters)

    def get(self, point_id):
        return store.get(self.client, self.collection, self.scope, point_id)
