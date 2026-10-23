"""The library: one directory per book, holding the source PDF and everything derived from it.

    library/<book_id>/
        source.pdf        the uploaded file, unchanged
        profile.json      what the book is and what has been done to it (BookProfile)
        chapters.jsonl    clean chapter text, one chapter per line
        summaries.json    LLM chapter summaries, cached by chapter text hash

A book's id is a slug of its title plus the first 8 hex digits of the file's SHA-256, so the same
file always gets the same id and a different file never collides.
"""

import json
import os
import re
import shutil
import unicodedata
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path

from marginalia.config import Settings
from marginalia.ingest.book import Chapter
from marginalia.ingest.pdf import read_pdf

STATUSES = ('uploaded', 'processing', 'ready', 'rejected', 'failed')


class DuplicateBook(ValueError):
    code = 'duplicate'

    def __init__(self, book_id):
        super().__init__(f'This file is already in the library as {book_id!r}.')
        self.book_id = book_id


class UnknownBook(KeyError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def slugify(text, max_len=40):
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode()
    slug = re.sub(r'[^a-z0-9]+', '-', text.casefold()).strip('-')
    if len(slug) > max_len:
        slug = slug[:max_len].rsplit('-', 1)[0]
    return slug or 'book'


@dataclass
class BookProfile:
    book_id: str
    title: str
    author: str | None
    source_filename: str
    sha256: str
    num_bytes: int
    num_pages: int = 0
    status: str = 'uploaded'
    error: dict | None = None             # {'code', 'message'} when rejected or failed
    # Filled in by the structure step.
    structure_method: str | None = None   # outline | headings
    parts: list = field(default_factory=list)       # [{'part', 'label', 'chapters'}]
    chapters: list = field(default_factory=list)    # [{'index', 'part', 'chapter', 'label', 'title', 'start_page', 'chars'}]
    wrap_style: str | None = None
    quote_style: str | None = None
    running_lines: list = field(default_factory=list)
    names: list = field(default_factory=list)
    # Filled in by the index step: points with this version are the live index.
    index_version: int | None = None
    counts: dict = field(default_factory=dict)      # {'chunk': n, 'quote': n, 'summary': n}
    # Filled in by the cover step: {'file': 'cover.jpg', 'source': 'pdf' | 'openlibrary' | 'generated'}.
    cover: dict | None = None
    # The assistant persona written for readers who gave no prompts; one per book, so they share it
    # (and the answers cached for it).
    persona: str | None = None
    # Pipeline bookkeeping: step -> {'key', 'done_at'}; a step is skipped while its key is unchanged.
    steps: dict = field(default_factory=dict)
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)

    @property
    def part_sizes(self):
        return {p['part']: p['chapters'] for p in self.parts}

    @property
    def has_parts(self):
        return any(p['label'] for p in self.parts)

    def locate(self, citation):
        """(part, chapter) of a citation such as 'Part IV, Chapter IV, passage 3', or None."""
        base = citation.split(', passage')[0].strip()
        for c in self.chapters:
            if c['label'] == base:
                return c['part'], c['chapter']
        return None


_PROFILE_FIELDS = {f.name for f in fields(BookProfile)}


class Library:
    def __init__(self, root=None):
        self.root = Path(root or Settings().library_dir)

    def path(self, book_id):
        return self.root / book_id

    # --- profiles --------------------------------------------------------------------------

    def get(self, book_id):
        path = self.path(book_id) / 'profile.json'
        if not path.exists():
            raise UnknownBook(book_id)
        data = json.loads(path.read_text(encoding='utf-8'))
        # Ignore fields a newer version wrote, so an older reader of the library keeps working.
        return BookProfile(**{k: v for k, v in data.items() if k in _PROFILE_FIELDS})

    def save(self, profile):
        profile.updated_at = now()
        _write_json(self.path(profile.book_id) / 'profile.json', asdict(profile))
        return profile

    def books(self):
        if not self.root.exists():
            return []
        return sorted((self.get(p.name) for p in self.root.iterdir() if (p / 'profile.json').exists()),
                      key=lambda b: b.created_at)

    def find_by_sha(self, sha256):
        return next((b for b in self.books() if b.sha256 == sha256), None)

    def resolve(self, ref):
        """A book by id, or by a unique id prefix / title fragment (for command lines)."""
        books = self.books()
        exact = [b for b in books if b.book_id == ref]
        matches = exact or [b for b in books if b.book_id.startswith(ref) or ref.casefold() in b.title.casefold()]
        if len(matches) != 1:
            raise UnknownBook(f'{ref!r} matches {len(matches)} books: {[b.book_id for b in matches]}')
        return matches[0]

    # --- adding and removing ---------------------------------------------------------------

    def add(self, source, filename=None, title=None, author=None, pdf=None):
        """Validate a PDF and store it as a new book. Raises PdfError or DuplicateBook.
        `pdf` is the file already parsed by read_pdf, to avoid parsing it twice."""
        data = source if isinstance(source, bytes) else Path(source).read_bytes()
        filename = filename or (Path(source).name if not isinstance(source, bytes) else 'upload.pdf')
        pdf = pdf or read_pdf(data)
        existing = self.find_by_sha(pdf.sha256)
        if existing:
            raise DuplicateBook(existing.book_id)
        title = title or pdf.title or Path(filename).stem.replace('_', ' ')
        book_id = f'{slugify(title)}-{pdf.sha256[:8]}'
        folder = self.path(book_id)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'source.pdf').write_bytes(data)
        profile = BookProfile(book_id=book_id, title=title, author=author or pdf.author,
                              source_filename=filename, sha256=pdf.sha256, num_bytes=pdf.num_bytes,
                              num_pages=len(pdf.pages))
        return self.save(profile)

    def remove(self, book_id):
        shutil.rmtree(self.path(book_id), ignore_errors=True)

    # --- derived files ---------------------------------------------------------------------

    def source(self, book_id):
        return self.path(book_id) / 'source.pdf'

    def cover_path(self, book_id):
        """The book's cover file, or None before the cover step has run."""
        profile = self.get(book_id)
        if not profile.cover:
            return None
        path = self.path(book_id) / profile.cover['file']
        return path if path.exists() else None

    def summaries_path(self, book_id):
        return self.path(book_id) / 'summaries.json'

    def write_chapters(self, book_id, chapters):
        lines = [json.dumps(c.__dict__, ensure_ascii=False) for c in chapters]
        _write_text(self.path(book_id) / 'chapters.jsonl', '\n'.join(lines) + '\n')

    def chapters(self, book_id):
        path = self.path(book_id) / 'chapters.jsonl'
        if not path.exists():
            return []
        return [Chapter(**json.loads(l)) for l in path.read_text(encoding='utf-8').splitlines() if l.strip()]


def _write_text(path, text):
    """Write atomically, so a crash never leaves a half-written profile or chapter file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(text, encoding='utf-8', newline='\n')
    os.replace(tmp, path)


def _write_json(path, data):
    _write_text(path, json.dumps(data, indent=1, ensure_ascii=False) + '\n')
