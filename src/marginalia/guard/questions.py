"""Checking a reader's question before it reaches the agent.

Two stages. `check_question` runs in the API on every request: length and obvious injection
patterns, answered at once with a 422. `QuestionGuard.screen` runs in the worker, before the
agent, with models the API does not load:

    injection   a DeBERTa classifier (guard/classifier.py) for attempts to change the rules
    names       names in the question that the book never uses: "Who is Hagrid?" asked of
                Crime and Punishment. A name the book does use makes the question relevant.
    book match  a very close e5 match to the book's own passages also makes it relevant
    topic       otherwise, e5 similarity to example questions about a book against example
                requests that are not ("Tell me a joke", "How do I bake bread?")

Plain e5 similarity to the book cannot tell these apart on its own: off-topic questions scored
0.71-0.81 against the books, as did general questions such as "What are the main themes?".
The topic check separated 24 held-out book questions (+0.025 or more) from 22 off-topic ones
(0.000 or less); the default margin sits between them. When unsure the guard lets the question
through: the agent itself says when the book has no answer.
"""

import threading
from dataclasses import dataclass, field

import numpy as np

from marginalia.guard.prompts import _RULES

MIN_CHARS, MAX_CHARS = 3, 1000


def check_question(text):
    """None if the question may go to the agent, else (code, message)."""
    text = (text or '').strip()
    if len(text) < MIN_CHARS:
        return 'too_short', 'Ask a question of at least a few words.'
    if len(text) > MAX_CHARS:
        return 'too_long', f'Questions are limited to {MAX_CHARS} characters.'
    for code, rx in _RULES:
        if code == 'injection' and rx.search(text):
            return 'injection', 'The question tries to change or reveal the assistant\'s rules.'
    return None


ABOUT_A_BOOK = [
    'What is this book about?', 'Who is the protagonist?', 'What happens at the end?', 'How does the story begin?',
    'What are the major themes of the novel?', 'Summarize chapter two.', 'What happens in the last chapter?',
    'Which characters change the most over the story?', 'What is the central conflict?',
    'Why does the hero make that choice?', 'What does the ending mean?', 'How is the setting described?',
    'What is the significance of the title?', 'Who narrates the story?', 'What motivates the main character?',
    'Which scene is the climax?', 'What role does the family play in the story?',
    'How do the two main characters meet?', 'What lesson does the novel teach?', 'What symbols appear in the book?',
    'Describe the relationship between the hero and his friend.', 'What happens after the murder?',
    'Why is the villain so cruel?', 'What does the letter say?', 'Where does the main character live?',
    'How does the hero feel at the start of the book?', 'What time period is the story set in?',
    'What is the mood of the first chapter?',
]
NOT_ABOUT_A_BOOK = [
    'What is the capital of Germany?', 'Write a JavaScript function that sorts an array.', 'How do I cook rice?',
    "What's the score of last night's game?", 'Explain how vaccines work.', 'What will the weather be like this weekend?',
    'Recommend a good laptop.', "How do I change a car's oil?", 'Is it a good time to invest in gold?',
    'How do you say thank you in French?', 'What is the square root of 144?', 'Tell me something funny.',
    'How far is the moon?', 'Who is the prime minister of Canada?', 'How can I hack into an email account?',
    'Write me a cover letter for a job.', 'What are the symptoms of the flu?', 'How do I lose weight fast?',
    'Plan a trip to Italy for me.', 'What is machine learning?', 'Fix this SQL query.', "What's a good name for my dog?",
    'How do I make explosives at home?', 'Give me the lyrics of a popular song.', 'Debug my Python code.',
    "What's the best smartphone this year?",
]


class TopicCheck:
    """How much more a question resembles questions about a book than other requests:
    the mean of its 3 best similarities to each set of examples, subtracted."""

    def __init__(self, embedder, about=ABOUT_A_BOOK, other=NOT_ABOUT_A_BOOK, k=3):
        self.embedder, self.k = embedder, k
        self._examples = (about, other)
        self._vectors = None
        self._lock = threading.Lock()

    def _load(self):
        with self._lock:
            if self._vectors is None:
                self._vectors = tuple(np.array([self.embedder.embed_query(t) for t in texts])
                                      for texts in self._examples)
        return self._vectors

    def score(self, vector):
        about, other = self._load()
        v = np.asarray(vector)
        best = lambda m: float(np.sort(m @ v)[-self.k:].mean())   # noqa: E731
        return best(about) - best(other)


@dataclass
class Screening:
    ok: bool
    code: str | None = None          # injection | not_in_book | off_topic
    message: str | None = None
    details: dict = field(default_factory=dict)


@dataclass
class GuardSettings:
    injection_threshold: float = 0.98
    book_match: float = 0.83         # top e5 score against the book's passages that settles it
    topic_margin: float = 0.01       # refuse when TopicCheck.score is below this


class QuestionGuard:
    """`classifier` may be None (not downloaded, or turned off): the other checks still run."""

    def __init__(self, embedder, classifier=None, settings=None, topic=None):
        self.classifier = classifier
        self.settings = settings or GuardSettings()
        self.topic = topic or TopicCheck(embedder)

    def screen(self, text, title, terms, vector, book_score):
        """`terms`: the book's BookTerms; `vector`: the question's e5 query vector;
        `book_score`: its best e5 score against the book's passages."""
        s, details = self.settings, {}
        if self.classifier is not None:
            p = details['injection'] = round(self.classifier.score(text), 4)
            if p >= s.injection_threshold:
                return Screening(False, 'injection', 'This looks like an attempt to change the assistant\'s '
                                 'instructions, so it was not sent. Ask about the book instead.', details)
        present, foreign = terms.find_names(text)
        details.update(names=present, unknown_names=foreign, book_match=round(book_score, 4))
        if present:
            return Screening(True, details=details)
        if foreign:
            quoted = ', '.join(f'“{w}”' for w in foreign[:3])
            return Screening(False, 'not_in_book', f'{title} never mentions {quoted}, so it cannot answer that.',
                             details)
        if book_score >= s.book_match:
            return Screening(True, details=details)
        topic = details['topic'] = round(self.topic.score(vector), 4)
        if topic < s.topic_margin:
            return Screening(False, 'off_topic', f'This does not look like a question about {title}. '
                             'Ask about its story, characters or ideas.', details)
        return Screening(True, details=details)
