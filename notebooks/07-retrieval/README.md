# 07 · Retrieval: hybrid search for RAG

Notebook: [07-retrieval.ipynb](07-retrieval.ipynb) · Code: `src/marginalia/index/retriever.py`, `src/marginalia/index/store.py`

## What it does

Given a question, finds the best passages, quotes or chapter summaries of one book. It combines the
meaning-based (e5) and word-based (BM25) searches into one ranking. It can narrow the search to a
part or chapter, and it reads records back by position. This is the "R" in RAG
(retrieval-augmented generation): the language model answers from what retrieval hands it, not from
memory.

## Why?

A small local model (qwen3:4b) does not know a book well, and even a large one invents details. If
the answer must come from the book, and every claim must be checkable, the model needs the right
pages in front of it. Retrieval decides what those pages are; most wrong answers are retrieval
misses, not reasoning errors.

Neither search alone is enough. Dense search misses rare names and exact phrases; word search misses
paraphrase ("murdered" against "struck with the axe"). Fusing the two rankings keeps what each is
good at.

## For what

- The agent's search tools (step 08): `search_passages`, `search_quotes`, `search_chapter_summaries`.
- The first search the agent always gets, with the question itself (step 09).
- The retrieval evaluation (step 11): is a gold chapter among the top k?

## Simple explanation

Ask two librarians: one understands what you mean, the other looks for your exact words. Each brings
a ranked pile. A passage near the top of both piles wins, near the top of one is still good, and
the bottom of both is dropped. That is reciprocal rank fusion.

## Examples

| Question | Kind | Top hit |
|---|---|---|
| "Who gave Harry the invisibility cloak?" | passage | Chapter Seventeen, passage 22, Dumbledore: "your father happened to leave it in my possession" |
| "Halloween" | summary | Chapter Ten: Halloween (after the title was added to summaries) |
| "troll" restricted to chapter 1 | passage | an error naming the chapters that mention a troll (see step 08) |

A miss this project found and fixed: the agent searched "invisibility cloak" (too broad) instead of
the question; the question itself ranked the right passage first. Hence the automatic first search.

## Technical

- **Construction:** `Retriever.for_book(profile)` opens the book's live index version. It builds a
  `Scope(book_id, index_version)` and loads e5 and BM25 lazily, so the MCP server starts instantly.
- **Search:** `search(query, kind=None, part=None, chapter=None, limit=5)` embeds the query both ways
  and calls `store.hybrid_search`. That runs two Qdrant prefetches of 40 candidates, fused with
  `FusionQuery(fusion=RRF)`. Each hit's `score` is the fused score (higher is better, not a cosine).
- **Reading by position:** `fetch(**filters)` returns records in book order; `get(point_id)` refuses
  ids from another book or version.
- **Not ready:** `BookNotReady` is raised for a book that has no index version yet.
- **Settings:** `CP_QDRANT_URL`, `CP_COLLECTION` (`books`), `CP_EMBED_MODEL`.

## Tech examples

```python
from marginalia.library import Library
from marginalia.index.retriever import Retriever

profile = Library().resolve('harry')
r = Retriever.for_book(profile)
for h in r.search('Who gave Harry the invisibility cloak?', kind='chunk', limit=5):
    print(round(h['score'], 3), h['label'], h['seq'] + 1, h['text'][:80])
r.search('Halloween', kind='summary', limit=3)
r.search('troll', kind='chunk', part=1, chapter=10)
```

## How it is connected to other steps

- **Reads:** the index built in steps 04–06.
- **Used by:** the MCP server (step 08), and so the agent (step 09).
- **Measured by:** step 11; summaries quality from step 04 shows up here directly.
- **Shares its e5 query vectors with:** the guard and cache (step 10).
