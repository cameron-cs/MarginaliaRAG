import asyncio
import json
from types import SimpleNamespace

import pytest

from marginalia.evals import runner
from marginalia.evals.golden import GoldQuestion, load_golden
from marginalia.evals.scoring import answer_correct, chapters_of, declines, fold, mentions, score_run
from marginalia.ingest.structure import roman_to_int

VICTIMS = GoldQuestion('victims', 'single_fact', 'Who did Raskolnikov kill?', 'Alyona and Lizaveta.',
                       must_mention=[['Alyona'], ['Lizaveta']], gold=[[1, 7]])
SENTENCE = GoldQuestion('sentence', 'single_fact', 'How many years?', 'Eight.', must_mention=[['eight', '8']],
                        gold=[[7, 1]])
FOOTBALL = GoldQuestion('football', 'unanswerable', 'Which football team?', 'None.', absent=['football'])


# --- scoring -----------------------------------------------------------------------------------

def test_fold_strips_case_and_diacritics():
    assert fold('Svidrigaïlov') == 'svidrigailov'


@pytest.mark.parametrize('answer,alt,expected', [
    ('He was trampled by the horses.', 'horse', True),     # matches at a word start
    ('a weight', 'eight', False),                          # never inside a word
    ('SVIDRIGAILOV shot himself', 'Svidrigaïlov', True),
    # Known limitation of word-start matching: a longer word starting with the term also counts.
    ('eighty years', 'eight', True),
])
def test_mentions(answer, alt, expected):
    assert mentions(answer, alt) is expected


def test_answer_needs_every_group():
    assert answer_correct(VICTIMS, 'He killed Alyona Ivanovna and her sister Lizaveta [Part I, Chapter VII].')
    assert not answer_correct(VICTIMS, 'He killed Alyona Ivanovna.')
    assert answer_correct(SENTENCE, 'Eight years of penal servitude.')
    assert answer_correct(SENTENCE, '8 years.')


@pytest.mark.parametrize('answer,expected', [
    ('The book passages do not mention any football team.', True),
    ("The book doesn't say.", True),
    ('There is no reference to football in the passages found.', True),
    # A real baseline answer the first version of the scorer marked wrong:
    ('The novel’s 19th-century Russian setting predates automobiles, so no car make is mentioned.', True),
    ('Raskolnikov supports Zenit.', False),
])
def test_unanswerable_needs_a_decline(answer, expected):
    assert declines(answer) is expected
    assert answer_correct(FOOTBALL, answer) is expected


def locate(citation):
    """Stand-in for BookProfile.locate with Crime and Punishment's labels."""
    import re
    m = re.match(r'(?:Part ([IVX]+)|Epilogue), Chapter ([IVX]+)', citation)
    return ((roman_to_int(m.group(1)) if m.group(1) else 7), roman_to_int(m.group(2))) if m else None


def test_chapters_of_uses_the_books_locator():
    assert chapters_of(['Part I, Chapter VII, passage 3', 'Epilogue, Chapter II', 'nonsense'], locate) == {(1, 7), (7, 2)}


def _result(answer, citations=(), supported=True, tools=('search_passages',)):
    return SimpleNamespace(answer=answer, citations=list(citations), unknown_citations=[], supported=supported,
                           tool_calls=[{'name': t, 'args': {}} for t in tools], revisions=0, budget_exhausted=False)


def test_score_run_separates_retrieval_from_citation():
    result = _result('He killed Alyona and Lizaveta [Part II, Chapter I].', ['Part II, Chapter I'])
    s = score_run(VICTIMS, result, ['Part I, Chapter VII, passage 4', 'Part II, Chapter I, passage 2'], locate)
    assert s['correct'] and s['retrieved_gold'] and not s['cited_gold']
    assert s['tool_calls'] == 1 and s['missing'] == []


def test_score_run_for_unanswerable_has_no_retrieval_metrics():
    s = score_run(FOOTBALL, _result('The passages do not say.'), [], locate)
    assert s['correct'] and s['retrieved_gold'] is None and s['cited_gold'] is None


# --- golden file -------------------------------------------------------------------------------

def test_golden_validation_rejects_bad_entries(tmp_path):
    good = {'id': 'a', 'category': 'single_fact', 'question': 'q', 'answer': 'x', 'must_mention': [['x']],
            'gold': [[1, 1]]}
    for bad, message in [
        ([good, good], 'duplicate'),
        ([{**good, 'category': 'trivia'}], 'unknown category'),
        ([{**good, 'gold': []}], 'need must_mention and gold'),
        ([{**good, 'category': 'unanswerable'}], 'take only `absent`'),
    ]:
        path = tmp_path / 'g.jsonl'
        path.write_text('\n'.join(json.dumps(x) for x in bad), encoding='utf-8')
        with pytest.raises(ValueError, match=message):
            load_golden(path)


# --- runner ------------------------------------------------------------------------------------

class FakeAgent:
    """Answers from a table; fails questions listed in `broken` once."""

    def __init__(self, answers, broken=()):
        self.answers, self.broken, self.asked = answers, set(broken), []

    async def ask(self, question):
        self.asked.append(question)
        if question in self.broken:
            self.broken.discard(question)
            raise ConnectionError('Ollama went away')
        from langchain_core.messages import ToolMessage
        answer, cites = self.answers[question]
        evidence = ToolMessage(json.dumps([{'citation': c + ', passage 1', 'text': '...'} for c in cites]),
                               tool_call_id='t')
        return SimpleNamespace(answer=answer, citations=cites, unknown_citations=[], supported=True,
                               tool_calls=[{'name': 'search_passages', 'args': {}}], revisions=0,
                               budget_exhausted=False, messages=[evidence])


CONFIG = {'book_id': 'crime-and-punishment-x', 'model': 'fake', 'num_ctx': 1, 'embed_model': 'e5', 'max_tool_calls': 6, 'max_revisions': 1,
          'grade': True, 'prompts_sha': 'p', 'golden_sha': 'g'}
ANSWERS = {
    VICTIMS.question: ('Alyona and Lizaveta [Part I, Chapter VII].', ['Part I, Chapter VII']),
    SENTENCE.question: ('Nine years [Part I, Chapter I].', ['Part I, Chapter I']),
    FOOTBALL.question: ('The book does not say.', []),
}
QUESTIONS = [VICTIMS, SENTENCE, FOOTBALL]


def run(agent, out_dir, runs=2, config=CONFIG, concurrency=1):
    return asyncio.run(runner.run_agent(QUESTIONS, agent, out_dir, config, locate, runs=runs,
                                        concurrency=concurrency, log=lambda *_: None))


def test_runs_are_scored_and_summarised(tmp_path):
    runs = run(FakeAgent(ANSWERS), tmp_path)
    assert len(runs) == 6
    s = runner.summarize(runs, QUESTIONS)
    assert s['accuracy'] == pytest.approx(2 / 3, abs=1e-3)
    assert s['consistency'] == pytest.approx(2 / 3, abs=1e-3)
    assert s['by_category'] == {'single_fact': 0.5, 'unanswerable': 1.0}
    assert s['retrieved_gold'] == 0.5 and s['cited_gold'] == 0.5
    assert s['per_question']['sentence'] == {'category': 'single_fact', 'correct': 0, 'runs': 2,
                                             'retrieved_gold': [False, False]}


def test_resume_skips_done_runs_and_retries_errors(tmp_path):
    first = FakeAgent(ANSWERS, broken=[SENTENCE.question])
    run(first, tmp_path, runs=1)
    assert [r.get('error') is not None for r in runner.read_runs(tmp_path)].count(True) == 1

    second = FakeAgent(ANSWERS)
    run(second, tmp_path, runs=2)
    # Run 0: only the errored question is redone. Run 1: everything is new.
    assert second.asked.count(SENTENCE.question) == 2 and second.asked.count(VICTIMS.question) == 1
    s = runner.summarize([r for r in runner.read_runs(tmp_path) if not r.get('error')], QUESTIONS)
    assert s['runs'] == 6


def test_summary_rescores_stored_answers(tmp_path):
    run(FakeAgent(ANSWERS), tmp_path, runs=1)
    path = tmp_path / 'runs.jsonl'
    rows = [json.loads(l) for l in path.read_text(encoding='utf-8').splitlines()]
    for r in rows:
        r['correct'] = False                  # as if an older, buggier scorer had judged them
    path.write_text('\n'.join(json.dumps(r) for r in rows), encoding='utf-8')
    s = runner.summarize(runner.read_runs(tmp_path), QUESTIONS)
    assert s['per_question']['victims']['correct'] == 1 and s['per_question']['football']['correct'] == 1


def test_changed_config_refuses_to_mix_results(tmp_path):
    run(FakeAgent(ANSWERS), tmp_path, runs=1)
    with pytest.raises(ValueError, match='prompts_sha'):
        run(FakeAgent(ANSWERS), tmp_path, runs=1, config={**CONFIG, 'prompts_sha': 'changed'})


def test_concurrent_runs_write_whole_lines(tmp_path):
    run(FakeAgent(ANSWERS), tmp_path, runs=4, concurrency=4)
    assert len(runner.read_runs(tmp_path)) == 12


def test_report_lists_failing_questions(tmp_path):
    run(FakeAgent(ANSWERS), tmp_path)
    runner.write_report(tmp_path, QUESTIONS)
    report = (tmp_path / 'report.md').read_text(encoding='utf-8')
    assert '**Accuracy: 67%**' in report
    assert 'How many years? | single_fact | 0/2 | nn | Nine years' in report
    assert 'Who did Raskolnikov kill?' not in report.split('## Questions not always')[1]
    assert json.loads((tmp_path / 'summary.json').read_text())['questions'] == 3


def test_bootstrap_interval_contains_mean_and_is_deterministic():
    rates = [1, 1, 0, 0.5, 1, 0, 1, 1]
    lo, hi = runner.bootstrap_ci(rates)
    assert lo <= sum(rates) / len(rates) <= hi
    assert runner.bootstrap_ci(rates) == (lo, hi)
    assert runner.bootstrap_ci([1, 1, 1]) == (1, 1)


def test_compare_reports_paired_change(tmp_path):
    run(FakeAgent(ANSWERS), tmp_path / 'a')
    better = {**ANSWERS, SENTENCE.question: ('Eight years [Part I, Chapter I].', ['Part I, Chapter I'])}
    run(FakeAgent(better), tmp_path / 'b')
    c = runner.compare(tmp_path / 'a', tmp_path / 'b', QUESTIONS)
    assert c['improved'] == ['sentence'] and c['regressed'] == []
    assert c['accuracy_b'] - c['accuracy_a'] == pytest.approx(1 / 3, abs=1e-3)


def test_retrieval_eval_ranks_first_gold_hit():
    class FakeRetriever:
        def search(self, query, kind, limit):
            return {'chunk': [{'part': 2, 'chapter': 1}, {'part': 1, 'chapter': 7}],
                    'quote': [], 'summary': [{'part': 1, 'chapter': 7}]}[kind]
    rows = runner.run_retrieval([VICTIMS, FOOTBALL], FakeRetriever())
    assert rows == [{'id': 'victims', 'category': 'single_fact', 'chunk': 2, 'quote': None, 'summary': 1}]
    s = runner.summarize_retrieval(rows)
    assert s['chunk']['hit@1'] == 0 and s['chunk']['hit@3'] == 1 and s['chunk']['mrr'] == 0.5
    assert s['chunk_or_summary']['hit@1'] == 1
