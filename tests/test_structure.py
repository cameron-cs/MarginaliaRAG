"""Chapter detection on synthetic PDFs, one structural feature at a time."""

import pytest

from marginalia.ingest.book import load_book
from marginalia.ingest.pdf import PdfError, read_pdf
from marginalia.ingest.structure import NoChapters, classify, parse_number
from tests.pdfs import chapter, make_book, part, scanned_book


@pytest.mark.parametrize('token,value', [
    ('12', 12), ('XII', 12), ('IV', 4), ('twelve', 12), ('Twenty-One', 21), ('thirty', 30), ('ONE', 1),
    ('mix', None), ('iv', None), ('', None),
])
def test_parse_number(token, value):
    assert parse_number(token) == value


@pytest.mark.parametrize('line,kind,label,title', [
    ('Chapter IV', 'chapter', 'Chapter IV', None),
    ('CHAPTER\tONE', 'chapter', 'Chapter One', None),
    ('Chapter 1: A Long-expected Party', 'chapter', 'Chapter 1', 'A Long-expected Party'),
    ('CHAPTER TWENTY-ONE', 'chapter', 'Chapter Twenty-One', None),
    ('Part II', 'part', 'Part II', None),
    ('BOOK ONE', 'part', 'Book One', None),
    ('Epilogue', 'special', 'Epilogue', None),
    ('PROLOGUE', 'special', 'Prologue', None),
    ('II', 'bare', 'Chapter II', None),
])
def test_classify_headings(line, kind, label, title):
    h = classify(line)
    assert (h.kind, h.label, h.title) == (kind, label, title)


@pytest.mark.parametrize('line', [
    'Chapter and verse were quoted to him.', 'Part of the problem was the rain.', 'I', 'Book',
    'He read chapter four twice before he slept that night, and then he read it again.',
])
def test_prose_is_not_a_heading(line):
    h = classify(line)
    assert h is None or h.kind == 'bare' and line == 'I'


def _labels(book):
    return [c.label for c in book.chapters]


def test_bookmarks_with_books_prologue_and_titled_chapters():
    units = [chapter('PROLOGUE', 'concerning hobbits', outline='Prologue'),
             part('BOOK ONE', outline='Book One'),
             chapter('Chapter 1: A Long-expected Party', 'the party', outline='Chapter 1: A Long-expected Party', level=1),
             chapter('Chapter 2: The Shadow of the Past', 'the shadow', outline='Chapter 2: The Shadow of the Past', level=1),
             part('BOOK TWO', outline='Book Two'),
             chapter('Chapter 1: Many Meetings', 'the meetings', outline='Chapter 1: Many Meetings', level=1)]
    book = load_book(make_book(units))
    assert book.method == 'outline'
    assert [(p['part'], p['label'], p['chapters']) for p in book.parts] == [
        (1, 'Prologue', 1), (2, 'Book One', 2), (3, 'Book Two', 1)]
    assert _labels(book) == ['Prologue', 'Book One, Chapter 1', 'Book One, Chapter 2', 'Book Two, Chapter 1']
    assert [c.title for c in book.chapters][1:] == ['A Long-expected Party', 'The Shadow of the Past', 'Many Meetings']
    assert book.chapters[1].text.startswith('The party began day 0')
    assert 'BOOK TWO' not in book.chapters[2].text, 'part heading leaked into the previous chapter'


def test_headings_with_spelled_numbers_titles_and_a_contents_page():
    units = [chapter('CHAPTER ONE', 'the boy', title='THE BOY WHO LIVED'),
             chapter('CHAPTER TWO', 'the glass', title='THE VANISHING GLASS'),
             chapter('CHAPTER THREE', 'the letters', title='LETTERS FROM NO ONE')]
    book = load_book(make_book(units, contents_page=True))
    assert book.method == 'headings'
    assert book.parts == [{'part': 1, 'label': '', 'chapters': 3}]
    assert _labels(book) == ['Chapter One', 'Chapter Two', 'Chapter Three']
    assert [c.title for c in book.chapters] == ['The Boy Who Lived', 'The Vanishing Glass', 'Letters From No One']
    first = book.chapters[0].text
    assert first.startswith('The boy began day 0') and first.count('\n\n') == 5
    assert 'THE TEST BOOK' not in first and 'BOY WHO LIVED' not in first


def test_numbering_restarts_become_parts_and_epilogue_keeps_its_numerals():
    units = [chapter('Chapter I', 'a'), chapter('Chapter II', 'b'), chapter('Chapter I', 'c'),
             chapter('Chapter II', 'd'), part('Epilogue'), chapter('I', 'e'), chapter('II', 'f')]
    book = load_book(make_book(units))
    assert _labels(book) == ['Part I, Chapter I', 'Part I, Chapter II', 'Part II, Chapter I', 'Part II, Chapter II',
                             'Epilogue, Chapter I', 'Epilogue, Chapter II']
    assert [c.part for c in book.chapters] == [1, 1, 2, 2, 3, 3]


def test_explicit_part_headings():
    units = [part('PART ONE'), chapter('Chapter 1', 'a'), chapter('Chapter 2', 'b'), part('PART TWO'),
             chapter('Chapter 3', 'c')]
    book = load_book(make_book(units))
    assert _labels(book) == ['Part One, Chapter 1', 'Part One, Chapter 2', 'Part Two, Chapter 3']


def test_flat_bookmarks_with_a_title_page_entry():
    """Like the Harry Potter PDF: one bookmark for the title page, then one per chapter."""
    units = [{'heading': 'THE TEST BOOK', 'outline': 'The Test Book', 'body': ['By A. Tester.']},
             chapter('CHAPTER ONE', 'a', outline='CHAPTER ONE'), chapter('CHAPTER TWO', 'b', outline='CHAPTER TWO'),
             chapter('CHAPTER THREE', 'c', outline='CHAPTER THREE')]
    book = load_book(make_book(units))
    assert book.method == 'outline' and _labels(book) == ['Chapter One', 'Chapter Two', 'Chapter Three']


def test_metadata_and_running_header():
    book = load_book(make_book([chapter(f'Chapter {n}', 'a') for n in range(1, 5)]))
    assert (book.title, book.author) == ('The Test Book', 'A. Tester')
    assert 'the test book' in book.running_lines


# --- rejected files (scenarios 3 and 4 of the product plan) -----------------------------------

def test_book_without_chapters_is_rejected():
    with pytest.raises(NoChapters) as err:
        load_book(make_book([{'heading': 'Some thoughts', 'body': ['Plain text. ' * 60] * 8}]))
    assert err.value.code == 'no_chapters'


@pytest.mark.parametrize('data,code', [
    (b'PK\x03\x04 mimetypeapplication/epub+zip', 'not_pdf'),
    (b'%PDF-1.7 but then garbage', 'unreadable'),
])
def test_files_that_are_not_usable_pdfs(data, code):
    with pytest.raises(PdfError) as err:
        read_pdf(data)
    assert err.value.code == code


def test_scanned_pdf_is_rejected():
    with pytest.raises(PdfError) as err:
        read_pdf(scanned_book())
    assert err.value.code == 'no_text'


def test_password_protected_pdf_is_rejected_but_owner_only_opens():
    units = [chapter('Chapter 1', 'a'), chapter('Chapter 2', 'b')]
    with pytest.raises(PdfError) as err:
        read_pdf(make_book(units, encrypt=('secret', 'owner')))
    assert err.value.code == 'encrypted'
    assert len(read_pdf(make_book(units, encrypt=('', 'owner'))).pages) == 2
