import random
from collections import Counter

import pytest

from marginalia.ingest.layout import (detect_wrap_style, find_running_lines, fix_drop_cap, normalize_page,
                                   strip_running_lines, to_paragraphs)


_WORDS = ('river garret letter clerk tavern stairs window bridge candle coat rain street axe pawnbroker '
          'sister mother friend landlady money debt fever dream church square').split()


def _body(i, lines=30):
    """Page text whose lines differ even with numbers masked, like real prose."""
    return '\n'.join(' '.join(random.Random(i * 1000 + k).sample(_WORDS, 7)) for k in range(lines))


def _pages(n, header='THE TEST BOOK'):
    return [f'{header}\nChapter {i + 1}\n{_body(i)}\n{i + 1}' for i in range(n)]


def test_running_header_and_page_numbers_are_removed():
    pages = _pages(20)
    keys = find_running_lines(pages)
    assert keys == {'the test book'}
    cleaned = strip_running_lines(pages[4], keys).split('\n')
    assert cleaned[0] == 'Chapter 5' and cleaned[-1] == _body(4).split('\n')[-1]


def test_headers_that_differ_by_page_number_are_one_header():
    pages = [f'Crime and Punishment {i}\n{_body(i)}' for i in range(30)]
    assert find_running_lines(pages) == {'crime and punishment #'}


def test_chapter_headings_and_sentences_are_never_running_lines():
    # "Chapter #" heads many pages and "that morning." ends many; neither is a header.
    pages = [f'Chapter {i}\n{_body(i)}\nthat morning.' for i in range(40)]
    assert find_running_lines(pages) == set()


def test_upper_case_roman_numerals_are_not_page_numbers():
    page = 'Epilogue\nII\nThe text begins.\nxii'
    assert strip_running_lines(page, set()) == 'Epilogue\nII\nThe text begins.'


def test_control_characters_are_removed():
    assert normalize_page('Crime and Punishment\x18\r\nText\x0c here') == 'Crime and Punishment\nText here'


@pytest.mark.parametrize('text,style', [
    ('a young \nman came out \nof the garret.\nHe had', 'trailing_space'),
    ('\t\tMr. Dursley was\nthe director of a firm.\n\t\tHe was a big man\nwith hardly any neck.', 'indent'),
    ('One line\nanother line.\n\nNew paragraph\nhere.\n\nAnd a third\none.', 'blank_line'),
    ('Some line of text that is long enough to count\nends here.\nAnother line of text that is long enough', 'heuristic'),
])
def test_wrap_style_detection(text, style):
    assert detect_wrap_style(text) == style


def test_trailing_space_style():
    text = 'Raskolnikov was not used to crowds, and, as we said be -\nfore, he avoided society of \nevery sort.\nBut now'
    assert to_paragraphs(text, 'trailing_space') == (
        'Raskolnikov was not used to crowds, and, as we said before, he avoided society of every sort.\n\nBut now')


def test_indent_style_with_tabs_between_words():
    text = '\t\t\tMr.\tand\tMrs.\tDursley\twere\nproud\tto\tsay.\n\t\t\t“A\twhat?”'
    assert to_paragraphs(text, 'indent') == 'Mr. and Mrs. Dursley were proud to say.\n\n“A what?”'


def test_heuristic_style_ends_paragraph_on_short_sentence_end():
    long = 'x' * 60
    text = f'{long}\n{long}\nend of it.\n{long}\nlast.'
    assert to_paragraphs(text, 'heuristic').split('\n\n') == [f'{long} {long} end of it.', f'{long} last.']


def test_line_end_hyphen_kept_or_joined_by_book_vocabulary():
    vocab = Counter({'prevaricate': 3, 'tavern-keeper': 2})
    text = 'he would pre-\nvaricate, said the tavern-\nkeeper.'
    assert to_paragraphs(text, 'blank_line', vocab) == 'he would prevaricate, said the tavern-keeper.'


def test_compound_with_trailing_space_is_rejoined():
    assert to_paragraphs('his blood- \nstained hands.', 'trailing_space') == 'his blood-stained hands.'


@pytest.mark.parametrize('text,expected', [
    ('O\n\nn an exceptionally hot evening', 'On an exceptionally hot evening'),
    ('M r. and Mrs. Dursley', 'Mr. and Mrs. Dursley'),
    ('A man walked in.', 'A man walked in.'),          # "aman" is no word in the book
    ('I was there.', 'I was there.'),
])
def test_drop_cap(text, expected):
    vocab = Counter({'on': 50, 'n': 0, 'mr': 30, 'r': 1, 'man': 20, 'was': 90})
    assert fix_drop_cap(text, vocab) == expected
