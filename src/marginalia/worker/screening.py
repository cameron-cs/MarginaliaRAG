"""What a question goes through before the agent: the question guard, then the answer cache.

Everything here is CPU work with local models (e5, the injection classifier), run in threads by
the worker's `screen` queue so it never waits behind the agent. If screening itself breaks, the
question goes to the agent anyway: the guard is a filter, not a gate the service depends on.
"""

import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass

from marginalia.agent.cache import AnswerCache, CacheHit
from marginalia.guard.questions import GuardSettings, QuestionGuard, Screening
from marginalia.guard.terms import BookTerms

log = logging.getLogger(__name__)


@dataclass
class Outcome:
    action: str                       # refuse | cached | agent
    screening: Screening
    hit: CacheHit | None = None


class Screener:
    def __init__(self, settings, library, client=None, embedder=None, classifier=None, guard=None, cache=None,
                 use_classifier=True):
        self.settings, self.library = settings, library
        self._client, self._embedder, self._classifier = client, embedder, classifier
        self._guard, self._cache = guard, cache
        self._use_classifier = use_classifier and bool(settings.injection_model)
        self._terms = OrderedDict()           # (book_id, index_version) -> BookTerms
        self._lock = threading.Lock()

    # --- lazily built dependencies ---------------------------------------------------------

    @property
    def client(self):
        if self._client is None:
            from qdrant_client import QdrantClient
            self._client = QdrantClient(url=self.settings.qdrant_url)
        return self._client

    @property
    def embedder(self):
        if self._embedder is None:
            from marginalia.index.embeddings import E5Embedder
            self._embedder = E5Embedder(self.settings.embed_model)
        return self._embedder

    @property
    def guard(self):
        with self._lock:
            if self._guard is None:
                classifier = self._classifier
                if classifier is None and self._use_classifier:
                    classifier = self._load_classifier()
                self._guard = QuestionGuard(self.embedder, classifier, GuardSettings(
                    injection_threshold=self.settings.injection_threshold, book_match=self.settings.book_match,
                    topic_margin=self.settings.topic_margin))
            return self._guard

    @property
    def cache(self):
        if self._cache is None:
            from marginalia.index.embeddings import E5_MODELS
            dim = self._embedder.dim if self._embedder is not None else E5_MODELS[self.settings.embed_model][0]
            self._cache = AnswerCache(self.client, self.settings.cache_collection, dim, self.settings.cache_similarity)
        return self._cache

    def _load_classifier(self):
        from marginalia.guard.classifier import InjectionClassifier
        classifier = InjectionClassifier(self.settings.injection_model)
        try:
            classifier.load()
        except Exception:
            log.exception('the injection classifier could not be loaded; screening questions without it')
            return None
        return classifier

    def warm(self):
        """Load the models and the topic examples now rather than on the first question."""
        self.guard.topic.score(self.embedder.embed_query('What happens in the first chapter?'))
        self.cache.ensure()

    def terms(self, profile):
        key = (profile.book_id, profile.index_version)
        with self._lock:
            if key in self._terms:
                self._terms.move_to_end(key)
                return self._terms[key]
        texts = [c.text for c in self.library.chapters(profile.book_id)]
        terms = BookTerms.from_texts(texts, [profile.title, profile.author])
        with self._lock:
            self._terms[key] = terms
            while len(self._terms) > 16:
                self._terms.popitem(last=False)
        return terms

    def book_score(self, profile, vector):
        from qdrant_client import models
        flt = models.Filter(must=[
            models.FieldCondition(key='book_id', match=models.MatchValue(value=profile.book_id)),
            models.FieldCondition(key='index_version', match=models.MatchValue(value=profile.index_version))])
        points = self.client.query_points(self.settings.collection, query=vector, using='e5', query_filter=flt,
                                          limit=1).points
        return points[0].score if points else 0.0

    # --- the work --------------------------------------------------------------------------

    def screen(self, profile, text, scope, fresh=False):
        vector = self.embedder.embed_query(text)
        terms = self.terms(profile)
        screening = self.guard.screen(text, profile.title, terms, vector, self.book_score(profile, vector))
        if not screening.ok:
            return Outcome('refuse', screening)
        if not fresh:
            hit = self.cache.lookup(profile, scope, text, vector, terms)
            if hit is not None:
                return Outcome('cached', screening, hit)
        return Outcome('agent', screening)

    def remember(self, profile, scope, text, result, question_id):
        """Keep an answer for later, if the grader found it supported by the book and it cites the
        book. An answer without citations is usually "the book does not say": the grader accepts
        it, but when it comes from a search that missed, caching it would make the miss stick."""
        if result.supported is not True or result.budget_exhausted or result.unknown_citations \
                or not result.citations:
            return False
        self.cache.store(profile, scope, text, self.embedder.embed_query(text), self.terms(profile),
                         result.answer, result.citations, result.supported, question_id)
        return True

    def forget_book(self, book_id):
        self.cache.delete_book(book_id)
