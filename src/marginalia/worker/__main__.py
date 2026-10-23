"""Run the background worker.

    python -m marginalia.worker                  # every queue
    python -m marginalia.worker --queues ask     # only the agent (e.g. a second worker for answers)
"""

import argparse
import asyncio
import logging
import signal

import redis.asyncio as aioredis

from marginalia import logs
from marginalia.api.db import Database
from marginalia.config import Settings
from marginalia.ingest.pipeline import Pipeline
from marginalia.library import Library
from marginalia.worker.handlers import AgentPool, Handlers
from marginalia.worker.runner import Worker
from marginalia.worker.screening import Screener


async def main(queues):
    settings = Settings()
    db = Database(settings.database_url)
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    library = Library(settings.library_dir)
    agents = AgentPool(settings)
    from marginalia import metrics
    metrics.serve(settings.metrics_port, 'worker')
    screener = Screener(settings, library)
    handlers = Handlers(db, redis, library, Pipeline(library, settings), agents, screener)
    worker = Worker(redis, handlers, concurrency={'ingest': 1, 'screen': 4, 'ask': settings.ollama_parallel})
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, worker.stop)
        except NotImplementedError:        # Windows: Ctrl+C raises KeyboardInterrupt instead
            pass
    log = logging.getLogger(__name__)
    log.info('worker %s on %s', worker.name, queues or list(worker.concurrency))
    if not queues or 'screen' in queues:
        # Load e5 and the injection classifier (a ~740 MB download the first time) in the
        # background; questions that arrive meanwhile wait for them.
        warming = asyncio.create_task(asyncio.to_thread(screener.warm))
        warming.add_done_callback(lambda t: t.exception() and log.error('warming up screening: %r', t.exception()))
    try:
        await worker.run(queues)
    finally:
        await agents.close()
        await db.dispose()
        await redis.aclose()


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--queues', nargs='+', choices=['ingest', 'screen', 'ask'])
    args = p.parse_args()
    logs.setup('worker')
    try:
        asyncio.run(main(args.queues))
    except KeyboardInterrupt:
        pass
