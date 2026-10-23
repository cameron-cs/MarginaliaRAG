"""The worker loop: read jobs from the Redis streams and run their handlers.

Each queue has its own consumer loop and concurrency. A job is acknowledged after its handler
returns, even if it failed (the failure is recorded on the book or question), so a broken job
cannot block a queue. Jobs a crashed worker left unacknowledged are claimed again at start-up.
"""

import asyncio
import json
import logging
import socket
import time

from marginalia import metrics
from marginalia.api.jobs import GROUP, JOB_TIMEOUT_MS, QUEUES, ensure_groups

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, redis, handlers, concurrency=None, name=None):
        self.redis, self.handlers = redis, handlers
        self.concurrency = concurrency or {'ingest': 1, 'screen': 4, 'ask': 2}
        self.name = name or f'{socket.gethostname()}-{id(self) & 0xffff:x}'
        self.stopping = asyncio.Event()

    async def handle(self, queue, entry_id, fields):
        kind = fields.get('kind')
        started, outcome = time.perf_counter(), 'ok'
        try:
            payload = json.loads(fields.get('payload') or '{}')
            handler = getattr(self.handlers, kind)
            log.info('%s job %s: %s %s', queue, entry_id, kind, payload)
            await handler(**payload)
        except Exception:
            outcome = 'error'
            log.exception('%s job %s (%s) failed', queue, entry_id, kind)
        finally:
            await self.redis.xack(QUEUES[queue], GROUP, entry_id)
            metrics.JOBS.labels(queue, outcome).inc()
            metrics.JOB_SECONDS.labels(queue).observe(time.perf_counter() - started)

    async def _reclaim(self, queue):
        """Jobs delivered to a worker that died before acknowledging them."""
        try:
            _, entries, *_ = await self.redis.xautoclaim(QUEUES[queue], GROUP, self.name,
                                                          min_idle_time=JOB_TIMEOUT_MS, start_id='0-0')
        except Exception:
            return []
        return [(e[0], e[1]) for e in entries if e and e[1]]

    async def consume(self, queue):
        stream, limit = QUEUES[queue], self.concurrency[queue]
        sem, tasks = asyncio.Semaphore(limit), set()

        async def run(entry_id, fields):
            async with sem:
                await self.handle(queue, entry_id, fields)

        def spawn(entry_id, fields):
            task = asyncio.create_task(run(entry_id, fields))
            tasks.add(task)
            task.add_done_callback(tasks.discard)

        for entry_id, fields in await self._reclaim(queue):
            spawn(entry_id, fields)
        while not self.stopping.is_set():
            free = limit - sum(1 for t in tasks if not t.done())
            if free <= 0:
                await asyncio.sleep(0.2)
                continue
            try:
                batches = await self.redis.xreadgroup(GROUP, self.name, {stream: '>'}, count=free, block=1000)
            except Exception:
                log.exception('reading %s failed; retrying', queue)
                await asyncio.sleep(2)
                continue
            for _, entries in batches or []:
                for entry_id, fields in entries:
                    spawn(entry_id, fields)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def run(self, queues=None):
        await ensure_groups(self.redis)
        await asyncio.gather(*(self.consume(q) for q in (queues or self.concurrency)))

    def stop(self):
        self.stopping.set()
