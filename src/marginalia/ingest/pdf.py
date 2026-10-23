"""Reading an uploaded PDF: validation, per-page text, bookmarks and metadata.

Everything here is book-independent. Failures raise PdfError with a stable `code`, so an API
can report them to the user: not_pdf, unreadable, encrypted, empty, no_text.
"""

import hashlib
import io
import logging
from dataclasses import dataclass
from pathlib import Path

import pypdf

# Below this many characters per page on average, the PDF is page images (a scan) with no
# usable text layer. OCR is out of scope, so such files are rejected.
MIN_CHARS_PER_PAGE = 200

# pypdf logs every malformed object it recovers from; uploaded PDFs are often sloppy.
logging.getLogger('pypdf').setLevel(logging.ERROR)


class PdfError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class OutlineEntry:
    depth: int
    title: str
    page: int                       # 0-based page index
    has_children: bool = False


@dataclass
class PdfDocument:
    pages: list                     # raw extracted text per page
    outline: list                   # OutlineEntry, in document order
    title: str | None
    author: str | None
    sha256: str
    num_bytes: int


def sha256_of(data):
    return hashlib.sha256(data).hexdigest()


def read_pdf(source):
    """`source` is a path or the file's bytes."""
    data = source if isinstance(source, bytes) else Path(source).read_bytes()
    # The PDF header may follow a little junk, but must appear in the first 1 KB.
    if b'%PDF-' not in data[:1024]:
        raise PdfError('not_pdf', 'The file is not a PDF.')
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not _decrypt(reader):
            raise PdfError('encrypted', 'The PDF is password-protected.')
        pages = [_page_text(p) for p in reader.pages]
    except PdfError:
        raise
    except Exception as exc:              # pypdf raises many types on broken files
        raise PdfError('unreadable', f'The PDF could not be read: {exc}') from exc
    if not pages:
        raise PdfError('empty', 'The PDF has no pages.')
    if sum(len(p.strip()) for p in pages) / len(pages) < MIN_CHARS_PER_PAGE:
        raise PdfError('no_text', 'The PDF has almost no text; it looks like a scan, and scanned books '
                                  'are not supported.')
    meta = reader.metadata or {}
    return PdfDocument(pages=pages, outline=_outline(reader), title=_meta(meta.get('/Title')),
                       author=_meta(meta.get('/Author')), sha256=sha256_of(data), num_bytes=len(data))


def _decrypt(reader):
    # Many "encrypted" PDFs only restrict printing and open with an empty password.
    try:
        return bool(reader.decrypt(''))
    except Exception:
        return False


def _page_text(page):
    try:
        return page.extract_text() or ''
    except Exception:
        return ''                          # one bad page should not sink the book


def _outline(reader):
    try:
        items = reader.outline
    except Exception:
        return []
    out = []

    def walk(nodes, depth):
        for i, node in enumerate(nodes):
            if isinstance(node, list):
                walk(node, depth + 1)
                continue
            try:
                page = reader.get_destination_page_number(node)
            except Exception:
                page = None
            if page is None or page < 0:
                continue
            has_children = i + 1 < len(nodes) and isinstance(nodes[i + 1], list)
            out.append(OutlineEntry(depth, ' '.join(str(node.title).split()), page, has_children))

    walk(items, 0)
    return out


def _meta(value):
    value = ' '.join(str(value).split()) if value else ''
    return value or None
