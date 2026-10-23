"""Job queues on Redis Streams, consumed by `python -m marginalia.worker`.

Book processing is not queued here: Airflow picks up waiting books in hourly batches. The queues
hold what must happen now:
  ingest  housekeeping on the library, such as deleting a book nobody reads any more
  screen  check a new question and look for a cached answer (fast, CPU; never waits for the agent)
  ask     answer a question with the agent (as many at once as Ollama has slots)
A consumer group gives each job to one worker; a job is acknowledged only once handled, and jobs
of a crashed worker are claimed again after JOB_TIMEOUT_MS.
"""

import json

QUEUES = {'ingest': 'marginalia:jobs:ingest', 'screen': 'marginalia:jobs:screen', 'ask': 'marginalia:jobs:ask'}
GROUP = 'workers'
JOB_TIMEOUT_MS = 30 * 60 * 1000
# A stream keeps its entries after they are handled; this caps each one (approximately, which is
# cheap for Redis) so they do not grow forever. Far more than ever waits at once.
MAX_ENTRIES = 10_000


async def enqueue(redis, queue, kind, **payload):
    return await redis.xadd(QUEUES[queue], {'kind': kind, 'payload': json.dumps(payload, default=str)},
                            maxlen=MAX_ENTRIES, approximate=True)


async def ensure_groups(redis):
    for stream in QUEUES.values():
        try:
            await redis.xgroup_create(stream, GROUP, id='0', mkstream=True)
        except Exception as exc:            # BUSYGROUP: it exists already
            if 'BUSYGROUP' not in str(exc):
                raise


async def queue_stats(redis):
    """Per queue: `waiting` (not yet given to a worker), `pending` (given, not finished) and
    `length` (entries kept in the stream, handled ones included)."""
    out = {}
    for name, stream in QUEUES.items():
        length = await redis.xlen(stream)
        waiting, pending = None, 0
        try:
            group = next((g for g in await redis.xinfo_groups(stream) if g.get('name') == GROUP), None)
            if group:
                waiting, pending = group.get('lag'), group.get('pending', 0)
        except Exception:                   # no stream or group yet
            pass
        out[name] = {'waiting': length if waiting is None else waiting, 'pending': pending, 'length': length}
    return out
