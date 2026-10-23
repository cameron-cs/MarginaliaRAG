from types import SimpleNamespace

import pytest
from qdrant_client import QdrantClient

from marginalia.agent.cache import AnswerCache, normalize, scope_of
from marginalia.guard.terms import BookTerms

PROFILE = SimpleNamespace(book_id='crime-1', index_version=2)
TERMS = BookTerms.from_texts(['Sonia met Raskolnikov. Sonia and Dounia. Raskolnikov and Dounia.'])
SCOPE = scope_of('You are a friendly notebooks.', 'qwen3:4b')


def vec(*xs):
    """A 4-d unit vector; questions that should match are given nearly the same one."""
    norm = sum(x * x for x in xs) ** 0.5
    return [x / norm for x in xs]


@pytest.fixture
def cache():
    client = QdrantClient(':memory:')
    c = AnswerCache(client, 'answer_cache_test', dim=4, threshold=0.97)
    c.store(PROFILE, SCOPE, 'Who is Sonia?', vec(1, 0, 0, 0), TERMS, 'Marmeladov\'s daughter [Part I, Chapter II].',
            ['Part I, Chapter II'], True, 'q1')
    yield c
    client.close()


def test_normalize_ignores_case_punctuation_spacing_and_diacritics():
    assert normalize('  Who is  SONIA?! ') == normalize('who is sonia') == 'who is sonia'
    assert normalize('Svidrigaïlov?') == 'svidrigailov'


def test_exact_question_is_found_whatever_its_vector(cache):
    hit = cache.lookup(PROFILE, SCOPE, 'who is sonia', vec(0, 1, 0, 0), TERMS)
    assert (hit.kind, hit.similarity, hit.question_id) == ('exact', 1.0, 'q1')
    assert hit.citations == ['Part I, Chapter II'] and hit.supported is True


def test_a_close_rewording_with_the_same_key_terms_is_found(cache):
    hit = cache.lookup(PROFILE, SCOPE, 'Tell me who Sonia is.', vec(1, 0.1, 0, 0), TERMS)
    assert hit.kind == 'semantic' and hit.similarity >= 0.97


def test_a_close_vector_with_another_name_is_not_reused(cache):
    assert cache.lookup(PROFILE, SCOPE, 'Who is Dounia?', vec(1, 0.05, 0, 0), TERMS) is None


def test_a_less_similar_question_is_not_reused(cache):
    assert cache.lookup(PROFILE, SCOPE, 'Who is Sonia, really?', vec(1, 0.5, 0, 0), TERMS) is None


def test_answers_are_kept_per_scope_and_index_version(cache):
    other_scope = scope_of('Reply in Spanish.', 'qwen3:4b')
    assert other_scope != SCOPE
    assert cache.lookup(PROFILE, other_scope, 'Who is Sonia?', vec(1, 0, 0, 0), TERMS) is None
    reindexed = SimpleNamespace(book_id='crime-1', index_version=3)
    assert cache.lookup(reindexed, SCOPE, 'Who is Sonia?', vec(1, 0, 0, 0), TERMS) is None
    other_book = SimpleNamespace(book_id='potter-1', index_version=2)
    assert cache.lookup(other_book, SCOPE, 'Who is Sonia?', vec(1, 0, 0, 0), TERMS) is None


def test_a_later_answer_replaces_the_earlier_one(cache):
    cache.store(PROFILE, SCOPE, 'Who is SONIA', vec(1, 0, 0, 0), TERMS, 'Newer answer.', [], True, 'q2')
    assert cache.count('crime-1') == 1
    assert cache.lookup(PROFILE, SCOPE, 'Who is Sonia?', vec(1, 0, 0, 0), TERMS).answer == 'Newer answer.'


def test_delete_book(cache):
    cache.delete_book('crime-1')
    assert cache.count('crime-1') == 0
    assert cache.lookup(PROFILE, SCOPE, 'Who is Sonia?', vec(1, 0, 0, 0), TERMS) is None


def test_custom_sampling_has_its_own_answers():
    assert scope_of('p', 'm') == scope_of('p', 'm', {}) != scope_of('p', 'm', {'temperature': 0.9})
    assert scope_of('p', 'm', {'top_k': 5, 'temperature': 0.9}) == scope_of('p', 'm', {'temperature': 0.9, 'top_k': 5})
