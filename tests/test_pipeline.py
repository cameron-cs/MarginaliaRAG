"""The library and the idempotent ingestion pipeline, end to end on synthetic PDFs."""

import json

import pytest
from qdrant_client import QdrantClient

from marginalia.config import Settings
from marginalia.index import store
from marginalia.index.retriever import Retriever
from marginalia.ingest.pdf import PdfError
from marginalia.ingest.pipeline import Pipeline, StepFailed
from marginalia.library import DuplicateBook, Library, UnknownBook, slugify
from tests.conftest import FakeDense, FakeSparse
from tests.pdfs import chapter, make_book

COLLECTION = 'pipeline_test'


TOPICS = ['the river', 'the axe', 'the letter', 'the bridge']


def _book(title='The Test Book', n=3):
    return make_book([chapter(f'Chapter {i}', TOPICS[i - 1]) for i in range(1, n + 1)], title=title)


class FakeSummarizer:
    def __init__(self, fail=False):
        self.calls, self.fail, self.wording = [], fail, 'Summary'

    def __call__(self, chapters, cache_path, book):
        self.calls.append([c.index for c in chapters])
        if self.fail:
            raise ConnectionError('Ollama is down')
        cache = {str(c.index): {'summary': f'{self.wording} of {c.label} of {book}.'} for c in chapters}
        cache_path.write_text(json.dumps(cache), encoding='utf-8')
        return [cache[str(c.index)]['summary'] for c in chapters]


@pytest.fixture
def env(tmp_path):
    client = QdrantClient(':memory:')
    library = Library(tmp_path / 'library')
    summarizer = FakeSummarizer()
    pipeline = Pipeline(library, Settings(collection=COLLECTION), client=client, dense=FakeDense(),
                        sparse=FakeSparse(), summarizer=summarizer, log=lambda *_: None)
    yield library, pipeline, summarizer, client
    client.close()


# --- library -----------------------------------------------------------------------------------

def test_add_creates_a_book_space(env):
    library, *_ = env
    profile = library.add(_book(), filename='upload.pdf')
    folder = library.path(profile.book_id)
    assert profile.book_id.startswith('the-test-book-') and len(profile.book_id.rsplit('-', 1)[1]) == 8
    assert (folder / 'source.pdf').exists() and (folder / 'profile.json').exists()
    assert (profile.title, profile.author, profile.status) == ('The Test Book', 'A. Tester', 'uploaded')
    assert library.get(profile.book_id) == profile


def test_profiles_from_a_newer_version_still_load(env):
    library, *_ = env
    profile = library.add(_book())
    path = library.path(profile.book_id) / 'profile.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    path.write_text(json.dumps({**data, 'field_from_the_future': 1}), encoding='utf-8')
    assert library.get(profile.book_id).book_id == profile.book_id


def test_cover_step_writes_a_cover_and_records_it(env):
    library, pipeline, *_ = env
    book_id = library.add(_book()).book_id
    pipeline.run(book_id, steps=['structure', 'cover'])
    assert library.get(book_id).cover == {'file': 'cover.svg', 'source': 'generated'}   # offline in tests
    assert library.cover_path(book_id).read_bytes().startswith(b'<svg')
    assert pipeline.run(book_id, steps=['cover']) == [], 'up to date'


def test_same_file_is_a_duplicate_but_another_book_is_not(env):
    library, *_ = env
    first = library.add(_book())
    with pytest.raises(DuplicateBook) as err:
        library.add(_book(), filename='renamed.pdf')
    assert err.value.book_id == first.book_id
    second = library.add(_book(title='Another Book'))
    assert second.book_id != first.book_id and len(library.books()) == 2


def test_invalid_upload_creates_nothing(env):
    library, *_ = env
    with pytest.raises(PdfError):
        library.add(b'PK\x03\x04 not a pdf', filename='book.epub')
    assert library.books() == []


def test_resolve_by_prefix_or_title(env):
    library, *_ = env
    a = library.add(_book(title='Crime and Punishment'))
    library.add(_book(title='The Idiot'))
    assert library.resolve('crime').book_id == a.book_id
    assert library.resolve('Punishment').book_id == a.book_id
    with pytest.raises(UnknownBook):
        library.resolve('zzz')


@pytest.mark.parametrize('title,slug', [
    ("Harry Potter 1 - Harry Potter and the Sorcerer's Stone", 'harry-potter-1-harry-potter-and-the'),
    ('Преступление и наказание', 'book'), ('Les Misérables', 'les-miserables'),
])
def test_slugify(title, slug):
    assert slugify(title) == slug


# --- pipeline ----------------------------------------------------------------------------------

def test_full_run_makes_the_book_ready_and_searchable(env):
    library, pipeline, summarizer, client = env
    book_id = library.add(_book()).book_id
    assert pipeline.run(book_id) == ['structure', 'cover', 'summarize', 'index']
    profile = library.get(book_id)
    assert profile.status == 'ready' and profile.index_version == 1
    assert [c['label'] for c in profile.chapters] == ['Chapter 1', 'Chapter 2', 'Chapter 3']
    assert profile.counts['summary'] == 3 and profile.counts['chunk'] > 3
    retriever = Retriever.for_book(profile, settings=pipeline.settings, client=client, dense=FakeDense(),
                                   sparse=FakeSparse())
    assert retriever.search('the axe began', kind='chunk', limit=1)[0]['label'] == 'Chapter 2'


def test_second_run_does_nothing(env):
    library, pipeline, summarizer, _ = env
    book_id = library.add(_book()).book_id
    pipeline.run(book_id)
    assert pipeline.run(book_id) == [] and len(summarizer.calls) == 1


def test_changed_model_reruns_only_what_depends_on_it(env):
    library, pipeline, summarizer, client = env
    book_id = library.add(_book()).book_id
    pipeline.run(book_id)
    pipeline.settings = Settings(collection=COLLECTION, summary_model='another-model')
    # Same summaries from the new model: nothing to re-index.
    assert pipeline.run(book_id) == ['summarize']
    pipeline.settings = Settings(collection=COLLECTION, summary_model='a-third-model')
    summarizer.wording = 'Précis'
    assert pipeline.run(book_id) == ['summarize', 'index']
    profile = library.get(book_id)
    assert profile.index_version == 2
    # The live version is complete and the previous one is gone.
    assert store.count(client, COLLECTION, book_id=book_id) == sum(profile.counts.values())


def test_force_reruns_everything(env):
    library, pipeline, *_ = env
    book_id = library.add(_book()).book_id
    pipeline.run(book_id)
    assert pipeline.run(book_id, force=True) == ['structure', 'cover', 'summarize', 'index']


def test_without_summaries(env):
    library, pipeline, summarizer, _ = env
    book_id = library.add(_book()).book_id
    assert pipeline.run(book_id, summaries=False) == ['structure', 'cover', 'index']
    assert summarizer.calls == [] and library.get(book_id).counts['summary'] == 0
    # Adding summaries later re-indexes with them.
    assert pipeline.run(book_id) == ['summarize', 'index']


def test_book_without_chapters_is_rejected_and_skipped(env):
    library, pipeline, *_ = env
    pdf = make_book([{'heading': 'Notes', 'body': ['Plain prose. ' * 60] * 8}], title='Notes')
    book_id = library.add(pdf).book_id
    with pytest.raises(StepFailed) as err:
        pipeline.run(book_id)
    assert err.value.code == 'no_chapters'
    profile = library.get(book_id)
    assert profile.status == 'rejected' and profile.error['code'] == 'no_chapters'
    assert pipeline.run(book_id) == [], 'rejected books are not retried on every run'


def test_failed_step_marks_the_book_and_is_retried(env):
    library, pipeline, summarizer, _ = env
    book_id = library.add(_book()).book_id
    summarizer.fail = True
    with pytest.raises(StepFailed):
        pipeline.run(book_id)
    profile = library.get(book_id)
    assert profile.status == 'failed' and profile.error['step'] == 'summarize'
    assert 'structure' in profile.steps, 'finished steps are kept'
    summarizer.fail = False
    assert pipeline.run(book_id) == ['summarize', 'index']
    assert library.get(book_id).status == 'ready' and library.get(book_id).error is None


def test_two_books_are_isolated_and_delete_removes_one(env):
    library, pipeline, _, client = env
    a = library.add(_book(title='Book A')).book_id
    b = library.add(_book(title='Book B', n=4)).book_id
    pipeline.run(a)
    pipeline.run(b)
    count_b = store.count(client, COLLECTION, book_id=b)
    pipeline.delete(a)
    assert store.count(client, COLLECTION, book_id=a) == 0 and store.count(client, COLLECTION, book_id=b) == count_b
    assert [p.book_id for p in library.books()] == [b]


def test_profile_locates_citations(env):
    library, pipeline, *_ = env
    book_id = library.add(_book()).book_id
    pipeline.run(book_id)
    profile = library.get(book_id)
    assert profile.locate('Chapter 2, passage 3') == (1, 2)
    assert profile.locate('Chapter 2') == (1, 2)
    assert profile.locate('Part IX, Chapter 1') is None
