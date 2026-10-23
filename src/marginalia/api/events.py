"""Live status events over Redis pub/sub.

Every event goes to the channel of each user it concerns and to the admin channel; websocket
connections subscribe to their user's channel (admins: the admin channel too).

    {"type": "book.status", "user_book_id": ..., "book_id": ..., "status": "checking",
     "step": "summarize", "message": "Summarizing chapters", "at": "2026-10-22T21:40:00+00:00"}
"""

import json
from datetime import datetime, timezone

ADMIN_CHANNEL = 'marginalia:events:admin'


def user_channel(user_id):
    return f'marginalia:events:user:{user_id}'


def event(type_, **fields):
    return {'type': type_, **{k: (str(v) if hasattr(v, 'hex') else v) for k, v in fields.items()},
            'at': datetime.now(timezone.utc).isoformat(timespec='seconds')}


async def publish(redis, ev, user_ids):
    data = json.dumps(ev, ensure_ascii=False, default=str)
    for uid in {str(u) for u in user_ids}:
        await redis.publish(user_channel(uid), data)
    await redis.publish(ADMIN_CHANNEL, data)
