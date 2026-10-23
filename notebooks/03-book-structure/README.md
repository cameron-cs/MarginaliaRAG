# 03 · Splitting a book into parts and chapters

Notebook: [03-book-structure.ipynb](03-book-structure.ipynb) · Code: `src/marginalia/ingest/structure.py`,
`src/marginalia/ingest/book.py`, `src/marginalia/ingest/quotes.py`

## What it does

Finds the book's structure: its parts ("Part One", "Book II") and chapters ("Chapter IV", "CHAPTER
ONE: The Boy Who Lived"), with their titles and where they start. It cuts the text into one piece per
chapter. Along the way it also learns how this edition writes dialogue (which quotation marks) and
which names appear most often.

## Why?

Readers ask about chapters ("what happens in chapter 3?"), and answers must say where they come from:
`[Part I, Chapter VII]`, not "page 84 of this particular PDF". Chapters are also the natural unit for
summaries and for narrowing a search. A book with no recognisable chapters is refused (`no_chapters`),
because the rest of the system depends on them.

## For what

- Citations every reader can check in any edition: part, chapter, passage.
- One summary per chapter (batch step `summarize`).
- Search filters (`part`, `chapter`) for the agent, and a table of contents in the web UI.
- The quote style tells step 04 how to cut out lines of dialogue; the names help the agent spell them
  as this translation does (Dounia, not Dunya).

## Simple explanation

Open a book and look for the contents. If the PDF has bookmarks (the clickable outline), use them. If
not, leaf through and look for the lines that look like chapter headings: short, alone, "CHAPTER
FOUR", maybe a title under it. Keep the result only if it looks like a real book: several
chapters, in order, not all on one page.

## Examples

| Source | Found by | Result |
|---|---|---|
| Crime and Punishment (bookmarks) | `outline` | 6 parts + Epilogue, 41 chapters |
| Harry Potter (no usable bookmarks) | `headings` | 17 chapters, "Chapter One · The Boy Who Lived" |
| A collection of notes, no headings | — | refused: `no_chapters` |

Heading lines it recognises: `CHAPTER IV`, `Chapter 12: The Mirror`, `CHAPTER TWELVE`, `PART TWO`,
`Book One`, `Prologue`, `Epilogue`. Lines it does not take for headings: `Contents`, sentences,
contents pages listing many headings.

## Technical

- **The cascade:** `structure.detect(outline, pages)` first tries `from_outline`, which maps bookmarks to
  pages and finds the heading line on each page. If the result isn't `plausible`, it uses
  `from_headings`, which scans the pages for lines that `classify(line)` recognises as part or chapter
  headings.
- **Plausibility:** it checks for enough chapters, increasing positions, no part without chapters, and
  sections spread across pages. Contents pages are skipped when a page holds three or more headings
  in a short span.
- **Numbers:** `parse_number` reads `IV`, `4`, `four` or `fourth`. A title on the line after the heading
  is kept when it looks like a title (short, mostly capitalised).
- **Output:** `load_book(source)` returns `BookText(chapters, method, parts, wrap_style, quote_style,
  running_lines, names, title, author)`. Each `Chapter` has `part`, `chapter`, `index`, `label`
  (`Part I, Chapter VII`), `title` and `text`.
- **Quote style:** `detect_quote_style(text)` counts the candidates (`“…”`, `‘…’`, `"…"`, guillemets) and
  picks the one used for dialogue. `frequent_names(text)` lists capitalised words that are frequent and
  rarely sentence-initial.
- The profile (`library/<book_id>/profile.json`) records all of it; `chapters.jsonl` holds the text.

## Tech examples

```python
from marginalia.ingest.book import load_book
from marginalia.ingest.structure import classify, NoChapters

book = load_book('library/crime-and-punishment-ddd22f99/source.pdf')
print(book.method, len(book.chapters), book.quote_style, book.names[:6])
print(book.chapters[6].label, book.chapters[6].title, len(book.chapters[6].text))

classify('CHAPTER TWELVE')        # Heading(kind='chapter', number=12, label='Chapter Twelve', ...)
```

## How it is connected to other steps

- **Input:** clean pages and the outline from step 01.
- **Output:** chapters for chunking (step 04) and summaries, labels for citations (steps 07–09), and
  parts and chapters for the MCP server's filters and `list_chapters` (step 08).
- **The guard (step 10):** builds its vocabulary of names from the chapter texts.
- **Batch (step 13):** this is the `structure` step. Its key is the file hash and the structure code
  version; a rejected book (`no_chapters`) stays rejected until an admin redoes it.
