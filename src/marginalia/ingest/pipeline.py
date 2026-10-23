"""The per-book ingestion pipeline: idempotent steps that the CLI (and later Airflow) run.

    structure  PDF -> parts, chapters, quote style, names       (chapters.jsonl + profile)
    cover      the PDF's own cover, Open Library's, or a generated one   (cover.jpg / cover.svg)
    summarize  chapters -> one LLM summary per chapter          (summaries.json)
    index      chunks + quotes + summaries -> Qdrant as a new index version, then switch to it

Each step stores a key (a hash of its inputs and code version) in the book's profile and is
skipped while that key is unchanged, so a daily run over the whole library only does new work.
"""

import hashlib
import json
import logging
import time
import traceback
from dataclasses import dataclass, field

from marginalia.config import Settings
from marginalia.index import store
from marginalia.index.store import Record
from marginalia.ingest.book import load_book
from marginalia.ingest.chunking import chunk_text
from marginalia.ingest.pdf import PdfError
from marginalia.ingest.quotes import extract_quotes
from marginalia.ingest.structure import NoChapters
from marginalia.library import Library, now

log = logging.getLogger(__name__)

# Bump when a step's output would change for the same input, so existing books are redone.
STRUCTURE_VERSION = 1      # chapter detection and text cleaning
INDEX_VERSION = 2          # chunking, quote extraction, payload layout (2: chapter titles in summaries)
STEPS = ('structure', 'cover', 'summarize', 'index')


class StepFailed(RuntimeError):
    def __init__(self, step, code, message):
        super().__init__(f'{step}: {message}')
        self.step, self.code = step, code


def build_records(chapters, book_id, version, quote_style, chunk_target=1000, chunk_overlap=150):
    records = []
    for ch in chapters:
        for kind, spans in (('chunk', chunk_text(ch.text, chunk_target, chunk_overlap)),
                            ('quote', extract_quotes(ch.text, quote_style))):
            records += [Record(book_id, version, kind, s.text, ch.part, ch.chapter, ch.index, ch.label, i,
                               s.start, s.end) for i, s in enumerate(spans)]
    return records


def summary_records(chapters, summaries, book_id, version):
    """One record per chapter summary, led by the chapter's label and title: a summary may never
    use the words of its title (Harry Potter's "Halloween" chapter summary does not say Halloween),
    and a search for them should still find the chapter."""
    return [Record(book_id, version, 'summary', f'{_heading(ch)} {text}', ch.part, ch.chapter, ch.index, ch.label,
                   0, 0, len(ch.text))
            for ch, text in zip(chapters, summaries)]


def _heading(chapter):
    return f'{chapter.label}: {chapter.title}.' if chapter.title else f'{chapter.label}.'


def embed(records, dense, sparse, batch=256, log=log.info):
    dense_vecs, sparse_vecs = [], []
    for i in range(0, len(records), batch):
        texts = [r.text for r in records[i:i + batch]]
        dense_vecs += dense.embed_passages(texts)
        sparse_vecs += sparse.embed_passages(texts)
        log(f'  embedded {min(i + batch, len(records))}/{len(records)}')
    return dense_vecs, sparse_vecs


def _key(*parts):
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16] if path.exists() else None


@dataclass
class Pipeline:
    library: Library = field(default_factory=Library)
    settings: Settings = field(default_factory=Settings)
    client: object = None          # QdrantClient; created on first use
    dense: object = None           # E5Embedder
    sparse: object = None          # Bm25Encoder
    summarizer: object = None      # callable(chapters, cache_path, book) -> summaries; default: Ollama
    http: object = None            # httpx.Client for cover lookups; tests pass a fake one
    log: object = log.info           # progress lines; the CLI and tests may pass their own

    # --- lazily built dependencies ---------------------------------------------------------

    def _qdrant(self):
        if self.client is None:
            from qdrant_client import QdrantClient
            self.client = QdrantClient(url=self.settings.qdrant_url)
        return self.client

    def _index_deps(self):
        self._qdrant()
        if self.dense is None:
            from marginalia.index.embeddings import E5Embedder
            self.dense = E5Embedder(self.settings.embed_model)
        if self.sparse is None:
            from marginalia.index.embeddings import Bm25Encoder
            self.sparse = Bm25Encoder()

    def _summarize(self, chapters, cache_path, book):
        if self.summarizer:
            return self.summarizer(chapters, cache_path, book)
        from marginalia.ingest.summarize import summarize_book
        return summarize_book(chapters, self.settings.summary_model, self.settings.ollama_url, cache_path,
                              book=book, concurrency=self.settings.ollama_parallel, log=self.log)

    # --- steps ---------------------------------------------------------------------------

    def keys(self, profile, summaries=True):
        """The key each step would run with now; a step is up to date if its stored key matches."""
        lib = self.library
        chapters_hash = _file_hash(lib.path(profile.book_id) / 'chapters.jsonl')
        from marginalia.ingest.cover import COVER_VERSION
        from marginalia.ingest.summarize import PROMPT_ID
        summaries_hash = _file_hash(lib.summaries_path(profile.book_id)) if summaries else None
        return {
            'structure': _key('structure', STRUCTURE_VERSION, profile.sha256),
            'cover': _key('cover', COVER_VERSION, profile.sha256, profile.title, profile.author,
                          self.settings.covers_online),
            'summarize': _key('summarize', chapters_hash, self.settings.summary_model, PROMPT_ID),
            'index': _key('index', INDEX_VERSION, chapters_hash, summaries_hash, self.settings.embed_model,
                          profile.quote_style),
        }

    def is_fresh(self, profile, step, summaries=True):
        done = profile.steps.get(step)
        return bool(done) and done['key'] == self.keys(profile, summaries)[step]

    def structure(self, profile):
        try:
            book = load_book(self.library.source(profile.book_id))
        except (PdfError, NoChapters) as exc:
            raise StepFailed('structure', exc.code, str(exc)) from exc
        self.library.write_chapters(profile.book_id, book.chapters)
        profile.title = profile.title or book.title
        profile.author = profile.author or book.author
        profile.num_pages = book.num_pages
        profile.structure_method = book.method
        profile.parts = book.parts
        profile.chapters = [{'index': c.index, 'part': c.part, 'chapter': c.chapter, 'label': c.label,
                             'title': c.title, 'start_page': c.start_page, 'chars': len(c.text)}
                            for c in book.chapters]
        profile.wrap_style, profile.quote_style = book.wrap_style, book.quote_style
        profile.running_lines, profile.names = book.running_lines, book.names
        self.log(f'  {len(book.chapters)} chapters in {len(book.parts)} part(s) via {book.method}; '
                 f'quotes: {book.quote_style}; wrapping: {book.wrap_style}')

    def cover(self, profile):
        from marginalia.ingest.cover import make_cover
        data, ext, source = make_cover(self.library.source(profile.book_id), profile.title, profile.author,
                                       online=self.settings.covers_online,
                                       user_agent=self.settings.covers_user_agent, http=self.http)
        folder = self.library.path(profile.book_id)
        for old in folder.glob('cover.*'):
            old.unlink()
        (folder / f'cover.{ext}').write_bytes(data)
        profile.cover = {'file': f'cover.{ext}', 'source': source}
        self.log(f'  cover from {source}')

    def summarize(self, profile):
        chapters = self.library.chapters(profile.book_id)
        book = f'{profile.title} by {profile.author}' if profile.author else profile.title
        self._summarize(chapters, self.library.summaries_path(profile.book_id), book)

    def index(self, profile, summaries=True):
        self._index_deps()
        chapters = self.library.chapters(profile.book_id)
        version = (profile.index_version or 0) + 1
        records = build_records(chapters, profile.book_id, version, profile.quote_style)
        if summaries:
            cache = json.loads(self.library.summaries_path(profile.book_id).read_text(encoding='utf-8'))
            texts = [cache[str(c.index)]['summary'] for c in chapters]
            records += summary_records(chapters, texts, profile.book_id, version)
        collection = self.settings.collection
        store.ensure_collection(self.client, collection, self.dense.dim)
        # Leftovers of an earlier run of this same version that crashed half-way.
        store.delete_book(self.client, collection, profile.book_id, keep_version=profile.index_version)
        dense_vecs, sparse_vecs = embed(records, self.dense, self.sparse, log=self.log)
        store.upsert(self.client, collection, records, dense_vecs, sparse_vecs)
        # Switch to the new version, then drop the old one: readers never see a partial index.
        profile.index_version = version
        profile.counts = {k: sum(r.kind == k for r in records) for k in store.KINDS}
        self.library.save(profile)
        store.delete_book(self.client, collection, profile.book_id, keep_version=version)
        self.log(f'  indexed version {version}: {profile.counts}')

    # --- running ---------------------------------------------------------------------------

    def run(self, book_id, steps=STEPS, force=False, summaries=True, on_step=None):
        """Run the steps a book needs. Returns the steps that ran (others were up to date).
        `on_step(step, state)` is called with state 'started' and 'done' around each step that runs."""
        on_step = on_step or (lambda step, state: None)
        profile = self.library.get(book_id)
        if profile.status == 'rejected' and not force:
            return []
        steps = [s for s in steps if summaries or s != 'summarize']
        ran = []
        for step in steps:
            if not force and self.is_fresh(profile, step, summaries):
                continue
            profile.status, profile.error = 'processing', None
            self.library.save(profile)
            self.log(f'{book_id}: {step}')
            on_step(step, 'started')
            start = time.monotonic()
            try:
                if step == 'index':
                    self.index(profile, summaries)
                else:
                    getattr(self, step)(profile)
            except StepFailed as exc:
                profile.status = 'rejected' if step == 'structure' else 'failed'
                profile.error = {'step': step, 'code': exc.code, 'message': str(exc)}
                self.library.save(profile)
                raise
            except Exception as exc:
                profile.status = 'failed'
                profile.error = {'step': step, 'code': 'error', 'message': f'{type(exc).__name__}: {exc}',
                                 'trace': traceback.format_exc(limit=5)}
                self.library.save(profile)
                raise StepFailed(step, 'error', str(exc)) from exc
            profile.steps[step] = {'key': self.keys(profile, summaries)[step], 'done_at': now(),
                                   'seconds': round(time.monotonic() - start, 1)}
            ran.append(step)
            on_step(step, 'done')
        profile.status = 'ready' if profile.index_version is not None else profile.status
        if profile.status == 'processing':
            profile.status = 'uploaded'
        self.library.save(profile)
        return ran

    def delete(self, book_id):
        """Remove a book everywhere: its points in Qdrant and its library folder."""
        self._qdrant()
        if self.client.collection_exists(self.settings.collection):
            store.delete_book(self.client, self.settings.collection, book_id)
        self.library.remove(book_id)
