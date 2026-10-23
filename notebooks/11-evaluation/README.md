# 11 · Evaluation: golden questions, retrieval, the agent and DeepEval

Notebook: [11-evaluation.ipynb](11-evaluation.ipynb) · Code: `src/marginalia/evals/`, `evals/golden/`, `evals/results/`

## What it does

It measures how well the system answers, on a fixed set of questions with known answers (the golden
set), at three levels:

- **Retrieval:** is a chapter that contains the answer among the top results?
- **The agent:** does the answer mention what it must, cite the right chapter, and say "the book
  doesn't say" for questions the book cannot answer? Scored over repeated runs, with a confidence
  interval.
- **DeepEval:** standard RAG metrics judged by a model: faithfulness to the retrieved passages,
  relevance of the answer and of the passages, recall of what the golden answer needs, and
  correctness against the golden answer.

## Why?

Every change (a chunk size, a prompt, a new model, the first-search fix) can improve some answers and
break others. Without a fixed set of questions you cannot tell which; with one, you can compare two
versions question by question.

The layers say where a failure comes from:

- a retrieval miss means fix chunking or search;
- a good retrieval with a wrong answer means fix the agent or prompts;
- an answer unfaithful to the passages is a hallucination.

DeepEval adds well-known, comparable metrics on top of the project's own checks.

## For what

- Deciding whether a change goes in (compare two runs).
- Finding the questions that fail, with the reason (the report).
- Tracking quality over models and settings.

## Simple explanation

An exam with an answer key. Sixteen questions about Crime and Punishment, each with the facts a
correct answer must mention and the chapter where they are. Some are trick questions ("Which football
team does Raskolnikov support?") where the right answer is "the book doesn't say". The system takes
the exam several times; the score, and which questions it gets wrong, tell you what to fix.

## Examples

```json
{"id": "victims", "category": "single_fact", "question": "Who did Raskolnikov kill?",
 "must_mention": [["Alyona"], ["Lizaveta"]], "gold": [[1, 7]],
 "answer": "The old pawnbroker Alyona Ivanovna and her sister Lizaveta."}
```

The categories are `single_fact`, `who_said`, `multi_hop` and `unanswerable`. A saved baseline is in
`evals/results/baseline-qwen3-4b` (report, per-run records, summary).

| Metric (DeepEval) | Asks |
|---|---|
| faithfulness | is every claim in the answer supported by the retrieved passages? |
| answer_relevancy | does the answer address the question? |
| contextual_relevancy | are the retrieved passages about the question? |
| contextual_recall | do the passages contain what the golden answer needs? |
| correctness (G-Eval) | does the answer agree with the golden answer? |

## Technical

- **Golden set:** `load_golden(path)` validates ids, categories and gold chapters, and a test checks
  that every `must_mention` term actually occurs in its gold chapters, so the key itself is right.
- **Retrieval:** `runner.run_retrieval(questions, retriever, k=10)` measures hit@k and MRR for passages
  and summaries, in seconds, with no LLM.
- **The agent:** `runner.run_agent(questions, agent, out_dir, config, locate, runs=3, concurrency)`
  appends each scored run to `runs.jsonl`, so a stopped evaluation resumes. Each record holds the
  answer, the citations, the retrieved citations, the passages' text and the tokens.
  `summarize` computes accuracy, a bootstrap 95% CI, and results per category and question.
  `compare(a, b)` gives the paired difference.
- **DeepEval** (`evals/deepeval_suite.py`, the `eval` extra, `deepeval==4.2.7`):
  - each question becomes an `LLMTestCase(input, actual_output, expected_output, retrieval_context)`;
  - the judge is `OllamaJudge`, a `DeepEvalBaseLLM` on the local model (`CP_JUDGE_MODEL`). It asks in
    plain text and returns the schema parsed from the reply's last JSON object, retrying once;
  - each metric gets a fresh object per test case, because metrics keep state;
  - DeepEval's telemetry is turned off;
  - the results go to `deepeval.jsonl` and `deepeval.json` next to the run.

## Tech examples

```bash
python -m marginalia.evals retrieval --book crime                       # seconds
python -m marginalia.evals agent --book crime --runs 3                  # minutes per question per run
python -m marginalia.evals compare evals/results/<a> evals/results/<b>
python -m marginalia.evals deepeval --book crime                        # agent run, then DeepEval
python -m marginalia.evals deepeval --book crime --from evals/results/<run> --metrics faithfulness,correctness
```

## How it is connected to other steps

- **Retrieval evaluation:** exercises steps 04–07.
- **Agent evaluation:** exercises 07–09 together, with the same agent the worker uses.
- **DeepEval:** reuses the passages the agent retrieved, so it judges what the agent actually saw.
- **Monitoring:** Evidently (step 15) watches the same qualities on real traffic: the grader's verdict,
  answers that cite the book, answers that say the book does not answer.
