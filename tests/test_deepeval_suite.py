"""DeepEval on the golden set: the local judge, test cases, scoring and the summary (no LLM)."""

import asyncio
import json

import pytest

pytest.importorskip('deepeval', reason='the eval extra is not installed')

from langchain_core.messages import AIMessage  # noqa: E402

from marginalia.evals import deepeval_suite as de  # noqa: E402
from marginalia.evals.golden import GoldQuestion  # noqa: E402

Q = GoldQuestion(id='victims', category='single_fact', question='Who did Raskolnikov kill?',
                 answer='The old pawnbroker Alyona Ivanovna and her sister Lizaveta.',
                 must_mention=[['Alyona'], ['Lizaveta']], gold=[[1, 7]])
RECORD = {'id': 'victims', 'run': 0, 'answer': 'He killed Alyona Ivanovna and Lizaveta [Part I, Chapter VII].',
          'evidence': [{'citation': 'Part I, Chapter VII, passage 3', 'text': 'He struck the old woman with the axe.'},
                       {'citation': 'Part I, Chapter VII, passage 5', 'text': 'Lizaveta came in.'}]}


class ScriptedLLM:
    """Answers each prompt with the next reply, as Ollama would: thinking, then JSON."""

    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    async def ainvoke(self, prompt):
        self.prompts.append(prompt)
        return AIMessage(self.replies.pop(0) if len(self.replies) > 1 else self.replies[0])


def test_last_json_reads_the_answer_after_the_thinking():
    assert de.last_json('Let me think {not json}. So: {"a": {"b": 1}} done') == {'a': {'b': 1}}
    assert de.last_json('first {"x": 1} then {"y": [1, {"z": 2}]}') == {'y': [1, {'z': 2}]}
    assert de.last_json('no json here') is None


def test_the_judge_returns_the_schema_and_retries_once():
    from deepeval.metrics.faithfulness.schema import Truths
    llm = ScriptedLLM('I am thinking... no JSON yet', 'Fine. {"truths": ["Alyona was killed."]}')
    judge = de.make_judge(llm=llm)
    result = asyncio.run(judge.a_generate('List the truths.', schema=Truths))
    assert isinstance(result, Truths) and result.truths == ['Alyona was killed.']
    assert len(llm.prompts) == 2 and judge.get_model_name().startswith('ollama/')
    assert asyncio.run(de.make_judge(llm=ScriptedLLM('plain text')).a_generate('Say hi')) == 'plain text'


def test_a_real_deepeval_metric_runs_on_the_local_judge():
    replies = {'truths': '{"truths": ["He struck the old woman.", "Lizaveta came in."]}',
               'claims': '{"claims": ["He killed Alyona.", "He killed Lizaveta."]}',
               'verdicts': '{"verdicts": [{"verdict": "yes", "reason": null}, {"verdict": "no", "reason": "not said"}]}',
               'reason': '{"reason": "One claim is not in the passages."}'}

    class ByStep:
        async def ainvoke(self, prompt):
            text = str(prompt).lower()
            key = ('verdicts' if '"verdicts"' in text else 'claims' if '"claims"' in text
                   else 'truths' if '"truths"' in text else 'reason')
            return AIMessage('thinking... ' + replies[key])

    metric = de.make_metrics(de.make_judge(llm=ByStep()), ['faithfulness'])['faithfulness']()
    asyncio.run(metric.a_measure(de.test_case(Q, RECORD), _show_indicator=False))
    assert metric.score == pytest.approx(0.5) and 'not in the passages' in metric.reason


def test_test_case_from_a_run():
    case = de.test_case(Q, RECORD)
    assert (case.input, case.expected_output) == (Q.question, Q.answer)
    assert case.retrieval_context == ['He struck the old woman with the axe.', 'Lizaveta came in.']
    assert de.test_case(Q, {'answer': None}).retrieval_context == ['']


def test_saved_runs_need_the_passages_text(tmp_path):
    runs = [{'id': 'victims', 'run': 0, 'error': 'boom'}, RECORD, {**RECORD, 'run': 1, 'answer': 'later'}]
    (tmp_path / 'runs.jsonl').write_text('\n'.join(json.dumps(r) for r in runs), encoding='utf-8')
    assert de.runs_with_evidence(tmp_path)['victims']['answer'] == RECORD['answer'], 'first good run'
    (tmp_path / 'runs.jsonl').write_text(json.dumps({'id': 'victims', 'run': 0, 'answer': 'x'}), encoding='utf-8')
    with pytest.raises(ValueError, match='no passage text'):
        de.runs_with_evidence(tmp_path)


def test_scoring_and_summary_with_a_stub_metric(tmp_path):
    class Stub:
        def __init__(self, value, fail=False):
            self.value, self.fail, self.score, self.reason = value, fail, None, None

        async def a_measure(self, case, _show_indicator=True):
            if self.fail:
                raise RuntimeError('judge returned nothing')
            self.score, self.reason = self.value, f'stub for {case.name}'

    other = GoldQuestion(id='weapon', category='single_fact', question='What weapon?', answer='An axe.',
                         must_mention=[['axe']], gold=[[1, 7]])
    rows = asyncio.run(de.score([Q, other], {'victims': RECORD, 'weapon': {**RECORD, 'id': 'weapon'}},
                                {'good': lambda: Stub(0.9), 'bad': lambda: Stub(0.2), 'broken': lambda: Stub(0, fail=True)},
                                log=lambda *_: None))
    assert [r['id'] for r in rows] == ['victims', 'weapon']
    assert rows[0]['scores'] == {'good': 0.9, 'bad': 0.2} and 'judge returned nothing' in rows[0]['errors']['broken']
    summary = de.summarize(rows)
    assert summary['good'] == {'mean': 0.9, 'passed': 2, 'scored': 2, 'errors': 0}
    assert summary['bad']['passed'] == 0 and summary['broken'] == {'mean': None, 'passed': 0, 'scored': 0, 'errors': 2}
    de.write(tmp_path, rows, summary)
    assert json.loads((tmp_path / 'deepeval.json').read_text())['good']['mean'] == 0.9


def test_unknown_metrics_are_refused():
    with pytest.raises(ValueError, match='unknown metrics'):
        de.make_metrics(object(), ['vibes'])
