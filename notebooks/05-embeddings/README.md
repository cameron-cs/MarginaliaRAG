# 05 · Embeddings: e5 and BM25

Notebook: [05-embeddings.ipynb](05-embeddings.ipynb) · Code: `src/marginalia/index/embeddings.py`

## What it does

Turns every record (and every question) into two vectors:

- A **dense** vector from the `intfloat/e5-base-v2` model: 768 numbers that capture meaning.
- A **sparse** BM25 vector: which words occur, and how often.

Both run locally on the CPU (ONNX through fastembed); nothing leaves the machine.

## Why?

The two find different things:

- **Dense vectors find meaning:** "Who did Raskolnikov murder?" matches a passage that says "he struck
  the old woman with the axe", although no word is shared.
- **Sparse vectors find exact words:** a rare name, "Lebeziatnikov", or a specific object. Dense models
  blur exactly these.

Using both (hybrid search, step 07) beats either alone on this kind of text.

## For what

- Semantic search over passages, quotes and summaries.
- The guard's topic check (step 10) and the answer cache's similarity (step 10) reuse the same e5
  query vectors.
- Spelling robustness: diacritics are folded on both sides, so "Svidrigailov" finds "Svidrigaïlov".

## Simple explanation

The dense vector is a point on a map of meanings: texts about the same thing sit close together, even
in different words. The sparse vector is a word count: which words, how many times. A search
compares the question's point with the passages' points, and its words with their words.

## Examples

Scores against "Who did Raskolnikov murder?" (cosine similarity, higher is closer):

| Passage | e5 |
|---|---|
| Raskolnikov killed the old pawnbroker with an axe. | 0.80 |
| Sonia read the story of Lazarus to Raskolnikov. | 0.75 |
| The weather in Petersburg was hot and stifling. | 0.70 |

The best match shares no word with the question except the name ("murder" against "killed"). e5's
scores are compressed (here 0.70–0.80), so they rank well but make poor absolute thresholds. That is
why the guard (step 10) compares against example sets instead of a fixed cut-off.

## Technical

- **Prefixes:** `E5Embedder(model_name='intfloat/e5-base-v2')` embeds passages with the `passage: `
  prefix and questions with `query: `. e5 was trained that way and ranks noticeably worse without
  them, so the prefixes live here and nowhere else.
- **Models:** fastembed does not ship the English e5 v2 models; `_register` adds their ONNX exports
  (mean pooling, normalised). They are cached in `FASTEMBED_CACHE_PATH` (`/data/models` in Docker).
- **BM25:** `Bm25Encoder` produces term-frequency sparse vectors (`Qdrant/bm25`); Qdrant adds the IDF
  part at query time (`Modifier.IDF`), so collection statistics stay correct as books are added.
- **Diacritics:** `fold_diacritics(text)` strips combining marks before BM25 tokenization, for texts and
  queries alike.
- **Tests:** they use `FakeDense` and `FakeSparse` from `tests/conftest.py`, which are deterministic
  bag-of-words vectors, so they need no model download.

## Tech examples

```python
from marginalia.index.embeddings import E5Embedder, Bm25Encoder
e5, bm25 = E5Embedder(), Bm25Encoder()
vectors = e5.embed_passages([r.text for r in records])     # list of 768 floats each
q = e5.embed_query('Who did Raskolnikov murder?')
sparse_q = bm25.embed_query('Raskolnikov axe')             # SparseVector(indices, values)
```

## How it is connected to other steps

- **Input:** records from step 04.
- **Output:** vectors stored in step 06.
- **The `index` batch step (13):** embeds in batches. The embedding model is part of the step's key, so
  changing `CP_EMBED_MODEL` re-indexes every book.
- **The MCP server's retriever (07, 08) and the worker's screener (10):** load the same models to embed
  questions.
