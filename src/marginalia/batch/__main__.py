"""Batch commands, one per Airflow task (see dags/marginalia_ingest.py). Also usable by hand.

    python -m marginalia.batch plan [--force BOOK_ID ...] [--only BOOK_ID ...]   # prints the plan as JSON (last line)
    python -m marginalia.batch review --user-book ID
    python -m marginalia.batch step --book BOOK_ID --step structure|cover|summarize|index [--force]
    python -m marginalia.batch finalize --plan '<json>'        # or the plan in $MARGINALIA_PLAN

--run tags events with the Airflow run id. Exit code 1 means the task should be retried; a book
rejected for having no chapters is a normal outcome (exit code 0).
"""

import argparse
import asyncio
import json
import logging
import os
import sys

import redis.asyncio as aioredis

from marginalia import logs
from marginalia.api.db import Database
from marginalia.batch.work import BatchWork
from marginalia.config import Settings
from marginalia.guard.prompts import PromptGuard
from marginalia.ingest.pipeline import STEPS, Pipeline
from marginalia.library import Library


async def run(args):
    settings = Settings()
    db = Database(settings.database_url)
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    library = Library(settings.library_dir)
    work = BatchWork(db, redis, library, Pipeline(library, settings),
                     PromptGuard(settings.ollama_url, settings.guard_model), run_id=args.run)
    try:
        # Each command's result goes to stdout, which Airflow keeps (it reads the plan from the last
        # line, as the task's XCom); progress and errors are logs, on stderr.
        if args.cmd == 'plan':
            print(json.dumps(await work.plan(args.force, args.only)))
        elif args.cmd == 'review':
            print(await work.review(args.user_book))
        elif args.cmd == 'step':
            print(await work.step(args.book, args.step, force=args.force))
        elif args.cmd == 'finalize':
            plan = json.loads(args.plan or os.environ.get('MARGINALIA_PLAN') or '{}')
            print(f'notified {await work.finalize(plan)} reader(s)')
    finally:
        await db.dispose()
        await redis.aclose()


def main():
    p = argparse.ArgumentParser(prog='python -m marginalia.batch', description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--run', help='Airflow run id, attached to events')
    sub = p.add_subparsers(dest='cmd', required=True)
    pl = sub.add_parser('plan')
    pl.add_argument('--force', nargs='*', default=[], help='book ids to redo from scratch')
    pl.add_argument('--only', nargs='*', default=[], help='process just these book ids')
    sub.add_parser('review').add_argument('--user-book', required=True)
    st = sub.add_parser('step')
    st.add_argument('--book', required=True)
    st.add_argument('--step', required=True, choices=STEPS)
    st.add_argument('--force', action='store_true')
    sub.add_parser('finalize').add_argument('--plan')
    args = p.parse_args()
    logs.setup('batch')
    try:
        asyncio.run(run(args))
    except Exception:
        logging.getLogger('marginalia.batch').exception('%s failed', args.cmd)
        sys.exit(1)


if __name__ == '__main__':
    main()
