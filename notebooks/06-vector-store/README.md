# 06 · Qdrant: the vector store

Notebook: [06-vector-store.ipynb](06-vector-store.ipynb) · Code: `src/marginalia/index/store.py`

## What it does

Stores every record with its two vectors and its metadata in Qdrant, in one collection (`books`) for
all books. It can find the nearest records to a query, read records back by part and chapter, and
replace a book's index with a new version without a moment where the book has none.

## Why?

Searching millions of vectors quickly needs an index built for it; a database of vectors also keeps
the payload (text, chapter, position) next to each vector, so a search returns readable results in
one call. One collection with a tenant field scales better than a collection per book and keeps the
configuration in one place.

The version switch matters because re-indexing a book takes minutes. Readers keep asking during
that time and must never search a half-written index.

## For what

- Hybrid search for the agent's tools (step 07).
- Reading neighbours (`get_surrounding_text`) and whole chapters' summaries.
- Re-indexing (a new chunk size, a new embedding model) without downtime.
- Deleting a book everywhere when its last reader removes it.

## Simple explanation

A library catalogue where every card has a position on the map of meanings, a word list, and a label
saying which book, which edition and which chapter it belongs to. A search asks "the 40 cards closest
to this question, from this book's current edition only". A new edition is shelved next to the old
one; the catalogue then points at the new one, and the old cards are thrown away.

## Examples

```
book_id=crime-and-punishment-ddd22f99  index_version=3  kind=chunk    label=Part I, Chapter VII  seq=4
book_id=crime-and-punishment-ddd22f99  index_version=3  kind=quote    label=Part I, Chapter II   seq=0
book_id=harry-potter-1-...-fa2d730b    index_version=3  kind=summary  label=Chapter Ten          seq=0
```

Re-indexing Harry Potter: write version 4 (both versions exist for a while), set `index_version: 4` in
the book's profile, delete version 3.

## Technical

- **Collection:** `ensure_collection(client, name, dim)` creates a named dense vector `e5` (cosine)
  and a sparse vector `bm25` with `Modifier.IDF`. Payload indexes: `book_id` (keyword,
  `is_tenant=True`, so a book's points sit together on disk), `kind`, and integers `index_version`,
  `part`, `chapter`, `chapter_index` and `seq`.
- **Scope:** every read goes through `Scope(book_id, index_version)`: the filter always includes both,
  so a query never sees another book or a stale version.
- **Hybrid search:** `hybrid_search(client, name, scope, dense_query, sparse_query, kind, part,
  chapter, limit, candidates=40)` uses Qdrant's query API: two prefetches (dense and sparse, 40
  candidates each) fused with reciprocal rank fusion.
- **Writing and deleting:** `upsert` writes in batches of 128. `delete_book(..., keep_version=v)`
  removes every other version of a book, or everything when `keep_version` is None.
- **In Docker:** Qdrant 1.19 on host ports 6340/6341. In tests, `QdrantClient(':memory:')` gives the
  same API in process.

## Tech examples

```python
from qdrant_client import QdrantClient
from marginalia.index import store
from marginalia.index.store import Scope

client = QdrantClient(url='http://127.0.0.1:6340')
store.ensure_collection(client, 'books', 768)
store.upsert(client, 'books', records, dense_vectors, sparse_vectors)
hits = store.hybrid_search(client, 'books', Scope('crime-and-punishment-ddd22f99', 3),
                           e5.embed_query(q), bm25.embed_query(q), kind='chunk', limit=5)
store.delete_book(client, 'books', 'crime-and-punishment-ddd22f99', keep_version=3)
```

## How it is connected to other steps

- **Input:** records (step 04) and vectors (step 05).
- **Read by:** the retriever (step 07) and so the MCP server (step 08); the guard's "close match to the
  book" check (step 10).
- **The answer cache (step 10):** lives in a second collection (`answer_cache`) with the same tenancy.
- **Batch (step 13):** the `index` step writes the new version and then switches. The worker's
  `delete_book` job removes a book's points and cached answers.
