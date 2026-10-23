# 04 · Chunking: passages, quotes and summaries

Notebook: [04-chunking.ipynb](04-chunking.ipynb) · Code: `src/marginalia/ingest/chunking.py`,
`src/marginalia/ingest/quotes.py`, `src/marginalia/ingest/summarize.py`, `src/marginalia/ingest/pipeline.py`

## What it does

Cuts each chapter into the pieces that will be searched:

- **Passages** of about 1,000 characters of whole sentences, overlapping by about 150.
- **Quotes:** every line of dialogue long enough to matter, as its own record.
- **Summaries:** one per chapter, written by the local model and led by the chapter's label and title.

Every piece becomes a record with its book, index version, part, chapter and position.

## Why?

A search engine compares the question with pieces of text, not with whole chapters: a 30,000-character
chapter "matches" everything a little. Pieces that are too small lose context ("he said no" — who?),
pieces that are too large dilute the match and overflow the model's prompt. Whole sentences keep
meaning intact; the overlap means a fact split across a boundary is still found whole in one of
the two pieces.

Quotes and summaries answer different kinds of question. "Who said *I am a titular counsellor*?"
matches a quote far better than a passage that also describes the room. "What happens in chapter 3?"
matches a summary, which says in a few lines what the passages say in pages.

## For what

- Precise retrieval (step 07): the agent reads the 5 best passages, not a chapter.
- Citations down to the passage: `[Part I, Chapter II, passage 3]`.
- Three tools for the agent (step 08): `search_passages`, `search_quotes`, `search_chapter_summaries`.

## Simple explanation

Imagine highlighting a book for a friend who will only read your index cards. Each card holds one
paragraph-sized excerpt, starts and ends at full stops, and repeats the last sentence of the
previous card, so nothing falls between two cards. You also make one card per memorable line of
dialogue, and one card per chapter saying what happens in it.

## Examples

| Chapter text | Records |
|---|---|
| 12,000 characters | about 14 passages of 800–1,000 characters |
| 9 lines of dialogue over 50 characters | 9 quotes |
| the chapter | 1 summary: "Chapter Ten: Halloween. Harry and Ron..." |

Why the summary leads with the title: the "Halloween" chapter of Harry Potter never says "Halloween"
in its summary, so a search for the word found the wrong chapter until the title was added.

## Technical

- **Chunks:** `chunk_text(text, target=1000, overlap=150, max_chars=1500)` splits into sentences
  (abbreviation-aware), packs them up to `target`, and starts the next chunk `overlap` characters
  back at a sentence boundary. A single sentence longer than `max_chars` is cut at word boundaries.
  It returns `Span(text, start, end)` with offsets into the chapter.
- **Quotes:** `extract_quotes(text, style, min_chars=50, max_chars=1500)` uses the book's quote style.
  It handles quotes that continue across paragraphs, and apostrophes inside single-quoted dialogue
  ("don't" is not a closing quote).
- **Summaries:** `summarize_book(...)` asks the summary model (qwen3:4b, thinking on) for each chapter,
  several at a time. Answers are cached by chapter-text hash in `summaries.json`, and reasoning that
  leaks into the reply is rejected. The prompt asks it to lead with events and skip descriptions of
  rooms and clothes, which made summary search noticeably better.
- **Records:** `build_records(chapters, book_id, version, quote_style)` and `summary_records(...)`
  produce `Record(book_id, index_version, kind, text, part, chapter, chapter_index, label, seq,
  char_start, char_end)`. The point id is a stable hash of book, version, kind, chapter and sequence.

## Tech examples

```python
from marginalia.ingest.chunking import chunk_text
from marginalia.ingest.quotes import extract_quotes
from marginalia.ingest.pipeline import build_records

spans = chunk_text(chapter.text)                       # [Span(text, start, end), ...]
quotes = extract_quotes(chapter.text, book.quote_style)
records = build_records(book.chapters, 'my-book-1234abcd', 1, book.quote_style)
```

## How it is connected to other steps

- **Input:** chapters from step 03.
- **Output:** records, embedded in step 05 and stored in step 06.
- **Batch (step 13):** the `summarize` step (GPU pool) writes `summaries.json`. The `index` step
  chunks, embeds and stores. Changing the chunk size or the summary prompt changes the steps' keys,
  so the next batch re-indexes affected books on its own.
