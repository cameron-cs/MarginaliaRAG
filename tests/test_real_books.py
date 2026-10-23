"""Ingestion of real books, pinned to their known structure.

Crime and Punishment (public domain) is in the repository. Copyrighted books are tested only when
present locally in test-books/ (git-ignored), e.g. test-books/harry-potter-1.pdf.
"""

import re
from collections import Counter
from pathlib import Path

import pytest

from marginalia.ingest.book import load_book

ROOT = Path(__file__).resolve().parents[1]
CRIME = ROOT / 'crime-and-punishment.pdf'
HARRY = ROOT / 'test-books' / 'harry-potter-1.pdf'
pytestmark = pytest.mark.book


def _artifacts(book):
    text = '\n\n'.join(c.text for c in book.chapters)
    return {
        'soft hyphens': len(re.findall(r'\w -\s\w', text)),
        'wrapped lines': len(re.findall(r'[^\n]\n[^\n]', text)),
        'double spaces': text.count('  '),
        'tabs': text.count('\t'),
    }


@pytest.fixture(scope='module')
def crime():
    if not CRIME.exists():
        pytest.skip('crime-and-punishment.pdf missing')
    return load_book(CRIME)


@pytest.fixture(scope='module')
def harry():
    if not HARRY.exists():
        pytest.skip('test-books/harry-potter-1.pdf not present (copyrighted; local only)')
    return load_book(HARRY)


def test_crime_structure(crime):
    assert crime.method == 'outline'
    assert [(p['label'], p['chapters']) for p in crime.parts] == [
        ('Part I', 7), ('Part II', 7), ('Part III', 6), ('Part IV', 6), ('Part V', 5), ('Part VI', 8), ('Epilogue', 2)]
    assert Counter(c.part for c in crime.chapters) == {1: 7, 2: 7, 3: 6, 4: 6, 5: 5, 6: 8, 7: 2}
    assert crime.chapters[0].label == 'Part I, Chapter I' and crime.chapters[-1].label == 'Epilogue, Chapter II'


def test_crime_text_is_clean(crime):
    assert crime.chapters[0].text.startswith('On an exceptionally hot evening early in July')   # drop cap rejoined
    assert crime.chapters[-1].text.endswith('but our present story is ended.')
    assert _artifacts(crime) == {'soft hyphens': 0, 'wrapped lines': 0, 'double spaces': 0, 'tabs': 0}
    text = ' '.join(c.text for c in crime.chapters)
    assert 'Planet eBook' not in text and 'Translator' not in crime.chapters[0].text
    assert not any(re.search(r'Part [IVX]+\s*$', c.text) for c in crime.chapters), 'part heading at a chapter end'


def test_crime_profile(crime):
    assert (crime.title, crime.author) == ('Crime and Punishment', 'Fyodor Dostoevsky')
    assert (crime.wrap_style, crime.quote_style) == ('trailing_space', 'single_curly')
    assert {'crime and punishment', 'free ebooks at planet ebook.com'} <= set(crime.running_lines)
    # This edition's spellings, which differ from other translations.
    assert {'Raskolnikov', 'Razumihin', 'Dounia', 'Sonia', 'Svidrigaïlov'} <= set(crime.names)


def test_harry_structure(harry):
    assert harry.method == 'outline' and harry.parts == [{'part': 1, 'label': '', 'chapters': 17}]
    assert [c.label for c in harry.chapters[:3]] == ['Chapter One', 'Chapter Two', 'Chapter Three']
    assert harry.chapters[-1].label == 'Chapter Seventeen'
    assert [c.title for c in harry.chapters[:2]] == ['The Boy Who Lived', 'The Vanishing Glass']


def test_harry_text_is_clean(harry):
    assert harry.chapters[0].text.startswith('Mr. and Mrs. Dursley, of number four, Privet Drive')
    artifacts = _artifacts(harry)
    assert artifacts['wrapped lines'] == 0 and artifacts['tabs'] == 0 and artifacts['double spaces'] == 0
    assert not any('HP 1 - Harry Potter' in c.text or 'THE BOY WHO LIVED' in c.text for c in harry.chapters)


def test_harry_profile(harry):
    assert (harry.wrap_style, harry.quote_style) == ('indent', 'double_curly')
    assert {'Harry', 'Hermione', 'Hagrid', 'Dumbledore'} <= set(harry.names)
