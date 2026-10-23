"""The golden set must be answerable from the book exactly as it is indexed."""

import re
from pathlib import Path

import pytest

from marginalia.evals.golden import CATEGORIES, GOLDEN_DIR, load_golden
from marginalia.evals.scoring import fold
from marginalia.ingest.book import load_book

PDF = Path(__file__).resolve().parents[1] / 'crime-and-punishment.pdf'
QUESTIONS = load_golden(GOLDEN_DIR / 'crime-and-punishment.jsonl')
# Chapters per part of Crime and Punishment; part 7 is the Epilogue.
PART_SIZES = {1: 7, 2: 7, 3: 6, 4: 6, 5: 5, 6: 8, 7: 2}


def test_golden_set_shape():
    assert len(QUESTIONS) == 16
    assert {q.category for q in QUESTIONS} == set(CATEGORIES)
    for q in QUESTIONS:
        assert all(1 <= p <= len(PART_SIZES) and 1 <= c <= PART_SIZES[p] for p, c in q.gold_chapters), q.id


@pytest.fixture(scope='module')
def chapter_text():
    if not PDF.exists():
        pytest.skip('book PDF missing')
    return {(c.part, c.chapter): fold(c.text) for c in load_book(PDF).chapters}


@pytest.mark.book
@pytest.mark.parametrize('q', [q for q in QUESTIONS if q.answerable], ids=lambda q: q.id)
def test_every_required_term_is_in_a_gold_chapter(q, chapter_text):
    gold_text = ' '.join(chapter_text[g] for g in q.gold_chapters)
    for group in q.must_mention:
        assert any(re.search(r'\b' + re.escape(fold(alt)), gold_text) for alt in group), \
            f'{q.id}: none of {group} occurs in gold chapters {sorted(q.gold_chapters)}'


@pytest.mark.book
@pytest.mark.parametrize('q', [q for q in QUESTIONS if not q.answerable], ids=lambda q: q.id)
def test_unanswerable_terms_never_occur(q, chapter_text):
    everything = ' '.join(chapter_text.values())
    for term in q.absent:
        assert not re.search(r'\b' + re.escape(fold(term)) + r'\b', everything), f'{term!r} is in the book'
