"""Answers already given, reused when the same question comes again.

A cached answer is reused only for the same book, the same index version (a re-index starts
afresh) and the same scope: the persona and instructions it was written with and the agent model.
Two readers who kept the book's default persona share answers; a reader who asked for answers in
Spanish has their own, and so does one who asked with their own sampling settings.

    exact     the same question once normalized (case, punctuation, spacing, diacritics)
    semantic  an e5 match at or above `threshold`, and the same key terms (guard/terms.py):
              names in order, numbers, the question word and negation. Embeddings put "Who
              killed Lizaveta?" right next to "Who killed Alyona?"; the key terms keep them apart.

The threshold is high on purpose. Rewordings of one question scored 0.95-0.99, but so did
different questions with the same names ("Where does Raskolnikov live?" / "...work?": 0.961).
At 0.97 the rewordings in between become misses, which only cost an agent run, while a wrong
cached answer would be served as if checked against the book.

Only answers the grader found supported by the book, and that cite it, are stored
(Screener.remember): an uncited "the book does not say" may be a search that missed. The cache lives in Qdrant, in a
collection of its own, partitioned by book like the passages.
"""

import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass

from qdrant_client import models

from marginalia.guard.terms import fold, key_terms

_NAMESPACE = uuid.UUID('6f1c1a52-0c55-4d1e-9a0e-3c8a8a6b6f10')
_NON_WORD = re.compile(r'[^\w]+')


def normalize(text):
    return _NON_WORD.sub(' ', fold(text).casefold()).strip()


def scope_of(persona, model, sampling=None):
    """`sampling`: the reader's own sampling settings, if any (marginalia.agent.sampling)."""
    return hashlib.sha256(json.dumps([persona or '', model, sampling or {}], sort_keys=True).encode()).hexdigest()[:16]


@dataclass
class CacheHit:
    kind: str                 # exact | semantic
    similarity: float
    answer: str
    citations: list
    supported: bool | None
    question_id: str


class AnswerCache:
    def __init__(self, client, collection='answer_cache', dim=768, threshold=0.97):
        self.client, self.collection, self.dim, self.threshold = client, collection, dim, threshold
        self._ready = False

    def ensure(self):
        if self._ready:
            return
        if not self.client.collection_exists(self.collection):
            self.client.create_collection(
                self.collection, vectors_config=models.VectorParams(size=self.dim, distance=models.Distance.COSINE))
            self.client.create_payload_index(self.collection, 'book_id', field_schema=models.KeywordIndexParams(
                type=models.KeywordIndexType.KEYWORD, is_tenant=True))
            for key in ('scope', 'key'):
                self.client.create_payload_index(self.collection, key, field_schema=models.PayloadSchemaType.KEYWORD)
            self.client.create_payload_index(self.collection, 'index_version',
                                             field_schema=models.PayloadSchemaType.INTEGER)
        self._ready = True

    def _filter(self, profile, scope, key=None):
        must = [models.FieldCondition(key='book_id', match=models.MatchValue(value=profile.book_id)),
                models.FieldCondition(key='index_version', match=models.MatchValue(value=profile.index_version)),
                models.FieldCondition(key='scope', match=models.MatchValue(value=scope))]
        if key is not None:
            must.append(models.FieldCondition(key='key', match=models.MatchValue(value=key)))
        return models.Filter(must=must)

    def lookup(self, profile, scope, text, vector, terms=None):
        self.ensure()
        key = normalize(text)
        points, _ = self.client.scroll(self.collection, scroll_filter=self._filter(profile, scope, key), limit=1,
                                       with_payload=True)
        if points:
            return _hit('exact', 1.0, points[0].payload)
        wanted = key_terms(text, terms)
        found = self.client.query_points(self.collection, query=vector, query_filter=self._filter(profile, scope),
                                         limit=5, with_payload=True).points
        for p in found:
            if p.score < self.threshold:
                break
            if p.payload.get('terms') == wanted:
                return _hit('semantic', p.score, p.payload)
        return None

    def store(self, profile, scope, text, vector, terms, answer, citations, supported, question_id):
        """Remember an answer; a later answer to the same question replaces it."""
        self.ensure()
        key = normalize(text)
        point_id = str(uuid.uuid5(_NAMESPACE, f'{profile.book_id}:{profile.index_version}:{scope}:{key}'))
        payload = {'book_id': profile.book_id, 'index_version': profile.index_version, 'scope': scope, 'key': key,
                   'terms': key_terms(text, terms), 'question': text, 'answer': answer, 'citations': citations,
                   'supported': supported, 'question_id': str(question_id), 'stored_at': time.time()}
        self.client.upsert(self.collection, [models.PointStruct(id=point_id, vector=list(vector), payload=payload)])

    def delete_book(self, book_id):
        if self.client.collection_exists(self.collection):
            self.client.delete(self.collection, models.FilterSelector(filter=models.Filter(must=[
                models.FieldCondition(key='book_id', match=models.MatchValue(value=book_id))])))

    def count(self, book_id):
        if not self.client.collection_exists(self.collection):
            return 0
        return self.client.count(self.collection, count_filter=models.Filter(must=[
            models.FieldCondition(key='book_id', match=models.MatchValue(value=book_id))]), exact=True).count


def _hit(kind, similarity, payload):
    return CacheHit(kind, round(float(similarity), 4), payload['answer'], payload.get('citations') or [],
                    payload.get('supported'), payload.get('question_id', ''))
