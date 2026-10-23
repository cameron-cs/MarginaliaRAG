"""Evaluate retrieval and the agent on the golden question set.

    python -m marginalia.evals retrieval --book crime     # seconds; no LLM
    python -m marginalia.evals agent --book crime --runs 3  # ~1-2 min per question per run
    python -m marginalia.evals agent --book crime --ids victims,weapon --runs 1
    python -m marginalia.evals report evals/results/<dir>
    python -m marginalia.evals compare evals/results/<a> evals/results/<b>
    python -m marginalia.evals deepeval --book crime [--from evals/results/<dir>] [--metrics a,b] [--ids ...]
The golden set for a book is evals/golden/<book id without its hash>.jsonl.
"""

import argparse
import asyncio
import json
from pathlib import Path

from marginalia.evals import runner
from marginalia.evals.golden import CATEGORIES, golden_path, load_golden
from marginalia.library import Library
from marginalia.config import Settings


def _select(questions, ids, category):
    if ids:
        wanted = set(ids.split(','))
        unknown = wanted - {q.id for q in questions}
        if unknown:
            raise SystemExit(f'unknown ids: {sorted(unknown)}')
        questions = [q for q in questions if q.id in wanted]
    if category:
        questions = [q for q in questions if q.category == category]
    return questions


def cmd_retrieval(args, questions, profile):
    from marginalia.index.retriever import Retriever
    rows = runner.run_retrieval(questions, Retriever.for_book(profile), k=args.k)
    summary = runner.summarize_retrieval(rows, k=args.k)
    for kind, metrics in summary.items():
        print(f"{kind:17} " + '  '.join(f'{m}={v:.2f}' for m, v in metrics.items()))
    misses = [r['id'] for r in rows if not r['chunk'] and not r['summary']]
    print(f'no gold chapter in top {args.k} passages or summaries: {misses or "none"}')


async def cmd_agent(args, questions, profile):
    from marginalia.agent.react_agent import book_agent
    settings = Settings()
    config = runner.run_config(settings, profile.book_id, golden_path(profile.book_id), args.max_tool_calls,
                               args.max_revisions, not args.no_grade)
    out_dir = args.out or runner.default_out_dir(config)
    options = {'max_tool_calls': args.max_tool_calls, 'max_revisions': args.max_revisions}
    if args.no_grade:
        options['grader'] = None
    async with book_agent(profile, settings, **options) as agent:
        await runner.run_agent(questions, agent, out_dir, config, profile.locate, runs=args.runs,
                               concurrency=args.concurrency or settings.ollama_parallel)
    summary = runner.write_report(out_dir, load_golden(golden_path(profile.book_id)))
    print(f"\naccuracy {summary['accuracy']} (95% CI {summary['accuracy_ci95']}); report: {out_dir}/report.md")


async def cmd_deepeval(args, questions, profile):
    """DeepEval metrics (marginalia.evals.deepeval_suite), on a saved run or on a fresh one."""
    from marginalia.evals import deepeval_suite as de
    settings = Settings()
    if args.source:
        records = de.runs_with_evidence(args.source)
        out_dir = Path(args.source)
    else:
        from marginalia.agent.react_agent import book_agent
        config = runner.run_config(settings, profile.book_id, golden_path(profile.book_id), args.max_tool_calls, 1, True)
        out_dir = args.out or runner.default_out_dir(config)
        async with book_agent(profile, settings, max_tool_calls=args.max_tool_calls) as agent:
            rows = await runner.run_agent(questions, agent, out_dir, config, profile.locate, runs=1,
                                          concurrency=args.concurrency or settings.ollama_parallel)
        records = {r['id']: r for r in rows if not r.get('error')}
    names = args.metrics.split(',') if args.metrics else de.METRICS
    judge = de.make_judge(settings)
    rows = await de.score(questions, records, de.make_metrics(judge, names),
                          concurrency=args.concurrency or settings.ollama_parallel)
    summary = de.summarize(rows)
    de.write(out_dir, rows, summary)
    for name, s in summary.items():
        print(f"{name:21} mean {s['mean']}  passed {s['passed']}/{s['scored']}  errors {s['errors']}")
    print(f'details: {Path(out_dir) / "deepeval.jsonl"}')


def _golden_for(results_dir):
    """The golden set a results directory was run against (older runs predate per-book sets)."""
    config = json.loads((Path(results_dir) / 'config.json').read_text(encoding='utf-8'))
    return golden_path(config.get('book_id', 'crime-and-punishment-x'))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='command', required=True)
    for name in ('retrieval', 'agent', 'deepeval'):
        s = sub.add_parser(name)
        s.add_argument('--book', required=True, help='book id, unique prefix or title fragment')
        s.add_argument('--ids', help='comma-separated question ids')
        s.add_argument('--category', choices=CATEGORIES)
    sub.choices['retrieval'].add_argument('-k', type=int, default=10)
    a = sub.choices['agent']
    a.add_argument('--runs', type=int, default=3)
    a.add_argument('--out', help='results directory; reuse one to resume')
    a.add_argument('--concurrency', type=int,
                   help='questions in flight at once (default: CP_OLLAMA_NUM_PARALLEL, more would only queue)')
    a.add_argument('--max-tool-calls', type=int, default=6)
    a.add_argument('--max-revisions', type=int, default=1)
    a.add_argument('--no-grade', action='store_true')
    d = sub.choices['deepeval']
    d.add_argument('--from', dest='source', help='score this saved results directory instead of running the agent')
    d.add_argument('--metrics', help='comma-separated: faithfulness, answer_relevancy, contextual_relevancy, '
                                     'contextual_recall, correctness (default: all)')
    d.add_argument('--out', help='results directory for a fresh run')
    d.add_argument('--concurrency', type=int, help='judge calls at once (default: CP_OLLAMA_NUM_PARALLEL)')
    d.add_argument('--max-tool-calls', type=int, default=6)
    sub.add_parser('report').add_argument('dir')
    c = sub.add_parser('compare')
    c.add_argument('a')
    c.add_argument('b')
    args = p.parse_args()

    if args.command in ('retrieval', 'agent', 'deepeval'):
        profile = Library().resolve(args.book)
        questions = _select(load_golden(golden_path(profile.book_id)), args.ids, args.category)
        if args.command == 'retrieval':
            cmd_retrieval(args, questions, profile)
        elif args.command == 'agent':
            asyncio.run(cmd_agent(args, questions, profile))
        else:
            asyncio.run(cmd_deepeval(args, questions, profile))
        return
    questions = load_golden(_golden_for(args.dir if args.command == 'report' else args.a))
    if args.command == 'report':
        summary = runner.write_report(args.dir, questions)
        print(json.dumps({k: v for k, v in summary.items() if k != 'per_question'}, indent=1))
    else:
        print(json.dumps(runner.compare(args.a, args.b, questions), indent=1))


if __name__ == '__main__':
    from marginalia import logs
    logs.setup('evals')
    main()
