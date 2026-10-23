# 01 · Reading a PDF

Notebook: [01-pdf-reading.ipynb](01-pdf-reading.ipynb) · Code: `src/marginalia/ingest/pdf.py`, `src/marginalia/ingest/layout.py`

## What it does

Turns an uploaded file into clean page text. It checks that the file is a real, readable PDF, extracts
its text page by page, its bookmarks (the outline), title and author, and a fingerprint of the bytes.
Then it removes what is not the book's text: running headers and footers (the book title or chapter
name repeated on every page, page numbers), and it joins lines that the page layout broke in the
middle of a sentence back into paragraphs.

## Why?

Everything later depends on this text. A page number glued to a sentence ends up in a chunk, a
running header repeated 400 times outweighs the real words in search, and a line broken in the middle
of a name makes the name unfindable. A PDF is a description of where to draw characters on a page,
not a document with paragraphs, so the structure has to be recovered.

It is also the first gate: a scanned book (pictures of pages, no text layer), an encrypted file or an
EPUB renamed to `.pdf` cannot be used, and the reader should hear that at once, not after a batch.

## For what

- Rejecting unusable uploads immediately, with a reason the web UI shows next to the file field.
- Recognising the same file uploaded twice (by its SHA-256), so a book is processed once and shared.
- Giving chapter detection (step 03) and chunking (step 04) text without noise.

## Simple explanation

Think of photocopying a book and handing the pages to someone who must retype it. They skip the page
numbers at the bottom, ignore the book's title printed at the top of every page, and when a sentence
continues on the next line they don't press Enter there. This step does that retyping.

## Examples

| Upload | Result |
|---|---|
| A normal novel PDF | accepted; 300 pages of text, the outline, "Crime and Punishment" by "Fyodor Dostoevsky" |
| `book.epub` renamed | refused: `not_pdf` |
| A scan without OCR | refused: `no_text` |
| A password-protected PDF | refused: `encrypted` |
| The same PDF again | accepted as the same book (same SHA-256), not processed twice |

Before and after cleaning a page:

```
Crime and Punishment                          | It was a hot evening early in July when a young
It was a hot evening early in July when a     | man came out of the garret in which he lodged...
young man came out of the garret in which he  |
lodged...                                  3  |
```

## Technical

- **Reading:** `read_pdf(source)` uses `pypdf`. It returns a `PdfDocument`: the pages' text, the outline
  (`OutlineEntry(depth, title, page)`), metadata, `sha256`, size. It raises `PdfError(code, message)`
  with the codes `not_pdf`, `unreadable`, `encrypted`, `no_text`, `empty`.
- **Running lines:** `find_running_lines(pages)` normalizes the lines at the top and bottom edges of each
  page (digits removed, so "Page 12" and "Page 13" match). It keeps the lines that repeat on a share of
  the pages. Lines ending in sentence punctuation are never treated as running lines, so real text
  isn't stripped. Upper-case Roman numerals count as page numbers only with case-sensitive matching,
  so a word like "I" survives.
- **Line wrapping:** `detect_wrap_style(text)` tells how the book breaks lines (`trailing_space`, `indent`,
  `blank_line` or `heuristic`). `to_paragraphs(text, style, vocabulary)` joins accordingly. The
  book's own vocabulary decides hyphenation: `well-\nknown` stays hyphenated if the book uses
  "well-known", and becomes one word otherwise. `fix_drop_cap` repairs decorative first letters
  ("I t was" → "It was") by checking the book's words.

## Tech examples

```python
from marginalia.ingest.pdf import read_pdf, PdfError
from marginalia.ingest import layout

doc = read_pdf('test-books/my-book.pdf')
pages = [layout.normalize_page(p) for p in doc.pages]
running = layout.find_running_lines(pages)            # e.g. {'crime and punishment'}
pages = [layout.strip_running_lines(p, running) for p in pages]
text = '\n'.join(pages)
paragraphs = layout.to_paragraphs(text, layout.detect_wrap_style(text), layout.word_counts(text))

try:
    read_pdf(b'PK\x03\x04 not a pdf')
except PdfError as e:
    print(e.code)                                     # not_pdf
```

## How it is connected to other steps

- **Upload (step 12):** the API calls `read_pdf` while the reader waits, and refuses bad files at once.
- **Book structure (step 03):** gets the cleaned pages and the outline, and finds parts and chapters.
- **Library:** the SHA-256 becomes part of the book id (`crime-and-punishment-ddd22f99`), so a duplicate
  upload joins the existing book.
- **Batch (step 13):** the `structure` step runs this again in Airflow on the stored file; its key is
  the file hash, so it reruns only for a new file or a new version of the code.
