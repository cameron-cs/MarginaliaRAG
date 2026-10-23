"""Run the golden set against retrieval alone and against the full agent, and summarise."""

import asyncio
import hashlib
import json
import logging
import os
import random
import statistics
import time
from datetime import datetime
from pathlib import Path

from marginalia.config import ROOT
from marginalia.evals.golden import CATEGORIES
from marginalia.evals.scoring import answer_correct, missing_terms, score_run

RESULTS_DIR = ROOT / 'evals' / 'results'


# --- retrieval only ----------------------------------------------------------------------------

def run_retrieval(questions, retriever, k=10):
    """Rank of the first result from a gold chapter, for the raw question, per index kind."""
    rows = []
    for q in (q for q in questions if q.answerable):
        row = {'id': q.id, 'category': q.category}
        for kind in ('chunk', 'quote', 'summary'):
            hits = retriever.search(q.question, kind=kind, limit=k)
            ranks = [i for i, h in enumerate(hits, 1) if (h['part'], h['chapter']) in q.gold_chapters]
            row[kind] = ranks[0] if ranks else None
        rows.append(row)
    return rows


def summarize_retrieval(rows, k=10):
    out = {}
    for kind in ('chunk', 'quote', 'summary'):
        ranks = [r[kind] for r in rows]
        out[kind] = {f'hit@{n}': _mean(r is not None and r <= n for r in ranks) for n in (1, 3, 5, k)}
        out[kind]['mrr'] = _mean(1 / r if r else 0 for r in ranks)
    # Best case for an agent that searches both passages and summaries.
    either = [min(x for x in (r['chunk'], r['summary']) if x) if (r['chunk'] or r['summary']) else None for r in rows]
    out['chunk_or_summary'] = {f'hit@{n}': _mean(r is not None and r <= n for r in either) for n in (1, 3, 5, k)}
    return out


# --- agent runs --------------------------------------------------------------------------------

def run_config(settings, book_id, golden_file, max_tool_calls, max_revisions, grade):
    from marginalia.agent import prompts
    prompt_text = ''.join(v for k, v in sorted(vars(prompts).items()) if k.isupper() and isinstance(v, str))
    return {
        'book_id': book_id,
        'model': settings.agent_model, 'num_ctx': settings.agent_num_ctx, 'embed_model': settings.embed_model,
        'ollama_url': settings.ollama_url, 'ollama_kv_cache': os.environ.get('CP_OLLAMA_KV_CACHE_TYPE', 'f16'),
        'max_tool_calls': max_tool_calls, 'max_revisions': max_revisions, 'grade': grade,
        'prompts_sha': hashlib.sha256(prompt_text.encode()).hexdigest()[:12],
        'golden_sha': hashlib.sha256(Path(golden_file).read_bytes()).hexdigest()[:12],
    }


def default_out_dir(config):
    stamp = datetime.now().strftime('%Y%m%d-%H%M')
    return RESULTS_DIR / f"{stamp}-{config['model'].replace(':', '-')}{'' if config['grade'] else '-nograde'}"


def read_runs(out_dir):
    path = Path(out_dir) / 'runs.jsonl'
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def _prepare(out_dir, config):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = out_dir / 'config.json'
    if cfg_path.exists():
        existing = json.loads(cfg_path.read_text(encoding='utf-8'))
        changed = {k for k in config if existing.get(k) != config[k]}
        if changed:
            raise ValueError(f'{out_dir} was run with a different {sorted(changed)}; use a new --out directory')
    else:
        cfg_path.write_text(json.dumps(config, indent=1), encoding='utf-8')
    return out_dir


async def run_agent(questions, agent, out_dir, config, locate, runs=3, concurrency=1, log=None):
    """Ask every question `runs` times, appending each scored run to runs.jsonl as it finishes.

    Re-running with the same out_dir resumes: completed runs are skipped, errored ones retried.
    """
    log = log or logging.getLogger(__name__).info
    from marginalia.agent.react_agent import collect_evidence

    out_dir = _prepare(out_dir, config)
    done = {(r['id'], r['run']) for r in read_runs(out_dir) if not r.get('error')}
    # Run-major order, so a stopped evaluation still covers every question at least once.
    todo = [(q, i) for i in range(runs) for q in questions if (q.id, i) not in done]
    log(f'{len(todo)} runs to do ({len(done)} already done) -> {out_dir}')
    sem, lock, finished = asyncio.Semaphore(concurrency), asyncio.Lock(), 0

    async def one(q, i):
        nonlocal finished
        async with sem:
            start = time.monotonic()
            try:
                result = await agent.ask(q.question)
                found = collect_evidence(result.messages)
                evidence = [e.citation for e in found]
                # The passages' text too, for metrics that judge the answer against them (DeepEval).
                record = {**score_run(q, result, evidence, locate), 'answer': result.answer,
                          'citations': result.citations, 'retrieved': evidence,
                          'evidence': [{'citation': e.citation, 'text': e.text[:2000]} for e in found[:15]],
                          'tokens': getattr(result, 'usage', None)}
            except Exception as exc:          # one broken run must not lose the others
                record = {'correct': False, 'error': f'{type(exc).__name__}: {exc}'}
            record = {'id': q.id, 'run': i, 'category': q.category,
                      'seconds': round(time.monotonic() - start, 1), **record}
        async with lock:
            with open(out_dir / 'runs.jsonl', 'a', encoding='utf-8') as f:
                f.write(json.dumps(record, ensure_ascii=False) + '\n')
            finished += 1
            mark = 'ERROR' if record.get('error') else ('ok' if record['correct'] else 'WRONG')
            log(f"[{finished}/{len(todo)}] {q.id} #{i}: {mark} ({record['seconds']}s)")

    await asyncio.gather(*(one(q, i) for q, i in todo))
    return read_runs(out_dir)


# --- summaries ---------------------------------------------------------------------------------

def _mean(values):
    values = list(values)
    return round(sum(values) / len(values), 4) if values else None


def bootstrap_ci(per_question, n=2000, seed=0):
    """95% interval for mean accuracy, resampling questions (runs of one question are not independent)."""
    if not per_question:
        return None
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choices(per_question, k=len(per_question))) for _ in range(n))
    return round(means[int(0.025 * n)], 4), round(means[int(0.975 * n) - 1], 4)


def rescore(runs, questions):
    """Re-apply the current scorer to stored answers, so scorer fixes reach past runs."""
    by_id = {q.id: q for q in questions}
    return [{**r, 'correct': answer_correct(by_id[r['id']], r['answer']),
             'missing': missing_terms(by_id[r['id']], r['answer'])} if 'answer' in r and r['id'] in by_id else r
            for r in runs]


def summarize(runs, questions):
    by_id = {q.id: q for q in questions}
    runs = rescore([r for r in runs if r['id'] in by_id], questions)
    per_q = {}
    for r in runs:
        per_q.setdefault(r['id'], []).append(r)
    rates = {qid: _mean(r['correct'] for r in rs) for qid, rs in per_q.items()}
    answerable = [r for r in runs if by_id[r['id']].answerable and not r.get('error')]
    ok = [r for r in runs if not r.get('error')]
    multi = [qid for qid, rs in per_q.items() if len(rs) > 1]

    return {
        'questions': len(per_q), 'runs': len(runs), 'errors': sum(bool(r.get('error')) for r in runs),
        'accuracy': _mean(rates.values()), 'accuracy_ci95': bootstrap_ci(list(rates.values())),
        # Share of questions answered correctly in every run (only questions run more than once).
        'consistency': _mean(all(r['correct'] for r in per_q[qid]) for qid in multi) if multi else None,
        'by_category': {c: _mean(rates[q] for q in rates if by_id[q].category == c)
                        for c in CATEGORIES if any(by_id[q].category == c for q in rates)},
        'retrieved_gold': _mean(r['retrieved_gold'] for r in answerable),
        'cited_gold': _mean(r['cited_gold'] for r in answerable),
        'grader_supported': _mean(r['supported'] for r in ok if r.get('supported') is not None),
        'runs_with_unknown_citations': _mean(r['unknown_citations'] > 0 for r in ok),
        'mean_tool_calls': _mean(r['tool_calls'] for r in ok),
        'revised': _mean(r['revisions'] > 0 for r in ok),
        'budget_exhausted': sum(r['budget_exhausted'] for r in ok),
        'mean_seconds': _mean(r['seconds'] for r in runs),
        'per_question': {qid: {'category': by_id[qid].category, 'correct': sum(r['correct'] for r in rs),
                               'runs': len(rs), 'retrieved_gold': [r.get('retrieved_gold') for r in rs]}
                         for qid, rs in sorted(per_q.items())},
    }


def _pct(x):
    return '-' if x is None else f'{100 * x:.0f}%'


def render_report(summary, config, runs, questions):
    by_id = {q.id: q for q in questions}
    ci = summary['accuracy_ci95']
    lines = [
        f"# Agent evaluation: {config['model']}", '',
        f"Config: max_tool_calls={config['max_tool_calls']}, max_revisions={config['max_revisions']}, "
        f"grade={config['grade']}, prompts {config['prompts_sha']}, golden {config['golden_sha']}", '',
        f"- **Accuracy: {_pct(summary['accuracy'])}** (95% CI {_pct(ci[0])}-{_pct(ci[1])}) over "
        f"{summary['questions']} questions, {summary['runs']} runs, {summary['errors']} errors",
        f"- Consistency (right in every run): {_pct(summary['consistency'])}",
        f"- Retrieved a gold chapter: {_pct(summary['retrieved_gold'])}; cited a gold chapter: {_pct(summary['cited_gold'])}",
        f"- Grader said supported: {_pct(summary['grader_supported'])}; "
        f"runs citing something never retrieved: {_pct(summary['runs_with_unknown_citations'])}",
        f"- Tool calls per run: {summary['mean_tool_calls']}; revised: {_pct(summary['revised'])}; "
        f"budget exhausted: {summary['budget_exhausted']}; seconds per run: {summary['mean_seconds']}", '',
        '| Category | Accuracy |', '|---|---|',
        *[f'| {c} | {_pct(a)} |' for c, a in summary['by_category'].items()], '',
        '## Questions not always answered correctly', '',
        '| Question | Category | Correct | Retrieved gold | Example wrong answer |', '|---|---|---|---|---|',
    ]
    runs = rescore(runs, questions)
    for qid, s in summary['per_question'].items():
        if s['correct'] == s['runs']:
            continue
        wrong = next((r for r in runs if r['id'] == qid and not r['correct']), {})
        example = wrong.get('error') or wrong.get('answer', '')
        example = example.replace('|', '/').replace('\n', ' ')[:160]
        retrieved = ''.join({True: 'y', False: 'n', None: '-'}[x] for x in s['retrieved_gold'])
        lines.append(f"| {by_id[qid].question} | {s['category']} | {s['correct']}/{s['runs']} | {retrieved} | {example} |")
    return '\n'.join(lines) + '\n'


def write_report(out_dir, questions):
    out_dir = Path(out_dir)
    runs = read_runs(out_dir)
    config = json.loads((out_dir / 'config.json').read_text(encoding='utf-8'))
    summary = summarize(runs, questions)
    (out_dir / 'summary.json').write_text(json.dumps(summary, indent=1), encoding='utf-8')
    (out_dir / 'report.md').write_text(render_report(summary, config, runs, questions), encoding='utf-8')
    return summary


def compare(dir_a, dir_b, questions, seed=0):
    """Per-question change in accuracy from A to B, with a paired bootstrap interval for the mean change."""
    a, b = summarize(read_runs(dir_a), questions), summarize(read_runs(dir_b), questions)
    shared = sorted(set(a['per_question']) & set(b['per_question']))
    rate = lambda s, q: s['per_question'][q]['correct'] / s['per_question'][q]['runs']
    deltas = {q: round(rate(b, q) - rate(a, q), 4) for q in shared}
    return {
        'questions': len(shared),
        'accuracy_a': _mean(rate(a, q) for q in shared), 'accuracy_b': _mean(rate(b, q) for q in shared),
        'mean_change': _mean(deltas.values()), 'change_ci95': bootstrap_ci(list(deltas.values()), seed=seed),
        'improved': [q for q, d in deltas.items() if d > 0], 'regressed': [q for q, d in deltas.items() if d < 0],
    }
