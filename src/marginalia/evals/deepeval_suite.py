"""DeepEval metrics on the golden set, judged by the local model.

    python -m marginalia.evals deepeval --book crime                       # run the agent, then score
    python -m marginalia.evals deepeval --book crime --from evals/results/<run>   # score a saved run
    python -m marginalia.evals deepeval --book crime --metrics faithfulness,correctness --ids victims

Each golden question becomes a DeepEval test case: the question, the agent's answer, the golden
answer, and the text of the passages the agent retrieved. The metrics:

    faithfulness          the answer's claims are supported by the retrieved passages
    answer_relevancy      the answer addresses the question
    contextual_relevancy  the retrieved passages are about the question
    contextual_recall     the retrieved passages contain what the golden answer needs
    correctness           G-Eval: the answer agrees with the golden answer (rubric below)

The judge is the project's Ollama model (CP_JUDGE_MODEL, by default the guard model), wrapped as a
DeepEval model. qwen3:4b only answers with its thinking on, and then returns nothing when asked for
a JSON schema, so the judge asks in plain text and reads the JSON object out of the reply.
DeepEval's own telemetry is turned off. Needs the `eval` extra: `pip install -e ".[eval]"`.
"""

import asyncio
import json
import os
import time
from pathlib import Path

os.environ.setdefault('DEEPEVAL_TELEMETRY_OPT_OUT', 'YES')
os.environ.setdefault('DEEPEVAL_UPDATE_WARNING_OPT_OUT', 'YES')

METRICS = ('faithfulness', 'answer_relevancy', 'contextual_relevancy', 'contextual_recall', 'correctness')
CORRECTNESS_STEPS = [
    'Compare the facts in the actual output with the expected output.',
    'The actual output is correct if it states the facts of the expected output; extra details that do not '
    'contradict it are fine.',
    'If the expected output says the book does not answer the question, the actual output is correct when it '
    'also says so instead of inventing an answer.',
    'Penalize facts that contradict the expected output.',
]


def last_json(text):
    """The last JSON object in `text` (models think aloud before they answer), or None."""
    end = len(text)
    while True:
        close = text.rfind('}', 0, end)
        if close < 0:
            return None
        depth = 0
        for start in range(close, -1, -1):
            depth += {'}': 1, '{': -1}.get(text[start], 0)
            if depth == 0:
                try:
                    return json.loads(text[start:close + 1])
                except ValueError:
                    break
        end = close


def make_judge(settings=None, llm=None, attempts=2):
    """A DeepEval model that asks the local Ollama model (built here so DeepEval stays optional)."""
    from deepeval.models import DeepEvalBaseLLM
    from marginalia.agent.react_agent import make_llm, message_text
    from marginalia.config import Settings

    settings = settings or Settings()

    class OllamaJudge(DeepEvalBaseLLM):
        def __init__(self):
            self.model_name = settings.judge_model
            self.llm = llm or make_llm(settings, role='judge', model=settings.judge_model)
            super().__init__(self.model_name)

        def load_model(self):
            return self.llm

        def get_model_name(self):
            return f'ollama/{self.model_name}'

        async def a_generate(self, prompt, schema=None):
            text = ''
            for _ in range(attempts):
                text = message_text(await self.llm.ainvoke(prompt))
                if schema is None:
                    return text
                data = last_json(text)
                if data is not None:
                    try:
                        return schema.model_validate(data)
                    except Exception:
                        pass
            return text                       # DeepEval tries its own JSON clean-up on the text

        def generate(self, prompt, schema=None):
            return asyncio.run(self.a_generate(prompt, schema))

    return OllamaJudge()


def make_metrics(judge, names=METRICS, threshold=0.5):
    from deepeval.metrics import (AnswerRelevancyMetric, ContextualRecallMetric, ContextualRelevancyMetric,
                                  FaithfulnessMetric, GEval)
    from deepeval.test_case import LLMTestCaseParams

    makers = {
        'faithfulness': lambda: FaithfulnessMetric(threshold=threshold, model=judge, async_mode=True),
        'answer_relevancy': lambda: AnswerRelevancyMetric(threshold=threshold, model=judge, async_mode=True),
        'contextual_relevancy': lambda: ContextualRelevancyMetric(threshold=threshold, model=judge, async_mode=True),
        'contextual_recall': lambda: ContextualRecallMetric(threshold=threshold, model=judge, async_mode=True),
        'correctness': lambda: GEval(name='Correctness', evaluation_steps=CORRECTNESS_STEPS, threshold=threshold,
                                     model=judge, async_mode=True,
                                     evaluation_params=[LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT,
                                                        LLMTestCaseParams.EXPECTED_OUTPUT]),
    }
    unknown = set(names) - set(makers)
    if unknown:
        raise ValueError(f'unknown metrics {sorted(unknown)}; choose from {", ".join(METRICS)}')
    return {name: makers[name] for name in names}


def test_case(question, record):
    """A DeepEval test case for one golden question and one run's record (from runs.jsonl)."""
    from deepeval.test_case import LLMTestCase
    passages = [e['text'] for e in record.get('evidence') or [] if e.get('text')]
    return LLMTestCase(input=question.question, actual_output=record.get('answer') or '',
                       expected_output=question.answer, retrieval_context=passages or [''], name=question.id)


def runs_with_evidence(path):
    """The first run of each question in a saved results folder, if it kept the passages' text."""
    rows = [json.loads(line) for line in (Path(path) / 'runs.jsonl').read_text(encoding='utf-8').splitlines()
            if line.strip()]
    first = {}
    for r in rows:
        if not r.get('error'):
            first.setdefault(r['id'], r)
    missing = [i for i, r in first.items() if not r.get('evidence')]
    if missing:
        raise ValueError(f'{path} has no passage text for {len(missing)} question(s) (made before runs kept it): '
                         'run the agent again, or leave out --from')
    return first


async def score(questions, records, metric_makers, concurrency=2, log=print):
    """Every metric on every question; a fresh metric object per test case (metrics keep state)."""
    sem = asyncio.Semaphore(concurrency)
    rows = []

    async def one(q):
        case = test_case(q, records[q.id])
        out = {'id': q.id, 'category': q.category, 'scores': {}, 'reasons': {}, 'errors': {}}
        for name, make in metric_makers.items():
            async with sem:
                metric = make()
                start = time.monotonic()
                try:
                    await metric.a_measure(case, _show_indicator=False)
                    out['scores'][name] = round(float(metric.score), 3)
                    out['reasons'][name] = metric.reason
                except Exception as exc:                 # a judge that fails one case must not stop the rest
                    out['errors'][name] = f'{type(exc).__name__}: {exc}'
                log(f"{q.id} {name}: {out['scores'].get(name, 'error')} ({time.monotonic() - start:.0f}s)")
        rows.append(out)

    await asyncio.gather(*(one(q) for q in questions if q.id in records))
    return sorted(rows, key=lambda r: r['id'])


def summarize(rows, threshold=0.5):
    names = sorted({n for r in rows for n in r['scores']} | {n for r in rows for n in r['errors']})
    out = {}
    for name in names:
        scores = [r['scores'][name] for r in rows if name in r['scores']]
        out[name] = {'mean': round(sum(scores) / len(scores), 3) if scores else None,
                     'passed': sum(s >= threshold for s in scores), 'scored': len(scores),
                     'errors': sum(name in r['errors'] for r in rows)}
    return out


def write(out_dir, rows, summary):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / 'deepeval.jsonl', 'w', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')
    (out_dir / 'deepeval.json').write_text(json.dumps(summary, indent=1), encoding='utf-8')
    return out_dir
