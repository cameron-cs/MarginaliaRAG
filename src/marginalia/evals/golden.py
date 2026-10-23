"""Golden question sets: questions with checkable answers and the chapters that hold the evidence.

One file per book, evals/golden/<book>.jsonl. Each line:
  id            stable identifier
  category      single_fact | who_said | multi_hop | unanswerable
  question
  must_mention  answerable only: list of groups; the answer must contain one alternative from
                every group (case- and diacritic-insensitive, matched at word starts)
  gold          answerable only: [part, chapter] pairs where the evidence is; part 7 = Epilogue
  absent        unanswerable only: terms that never occur in the book (checked by a test)
  answer        reference answer for people reading reports
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from marginalia.config import ROOT

GOLDEN_DIR = ROOT / 'evals' / 'golden'


def golden_path(book_id):
    """evals/golden/<book id without its hash suffix>.jsonl, e.g. crime-and-punishment.jsonl."""
    return GOLDEN_DIR / f"{book_id.rsplit('-', 1)[0]}.jsonl"
CATEGORIES = ('single_fact', 'who_said', 'multi_hop', 'unanswerable')


@dataclass(frozen=True)
class GoldQuestion:
    id: str
    category: str
    question: str
    answer: str
    must_mention: list = field(default_factory=list)
    gold: list = field(default_factory=list)
    absent: list = field(default_factory=list)

    @property
    def answerable(self):
        return self.category != 'unanswerable'

    @property
    def gold_chapters(self):
        return {tuple(g) for g in self.gold}


def load_golden(path):
    questions = [GoldQuestion(**json.loads(line)) for line in Path(path).read_text(encoding='utf-8').splitlines()
                 if line.strip()]
    ids = [q.id for q in questions]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f'duplicate golden ids: {sorted(dupes)}')
    for q in questions:
        if q.category not in CATEGORIES:
            raise ValueError(f'{q.id}: unknown category {q.category!r}')
        if q.answerable and not (q.must_mention and q.gold):
            raise ValueError(f'{q.id}: answerable questions need must_mention and gold')
        if not q.answerable and (q.must_mention or q.gold or not q.absent):
            raise ValueError(f'{q.id}: unanswerable questions take only `absent`')
    return questions
