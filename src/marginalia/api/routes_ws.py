"""Live events over a websocket.

    ws://<host>/ws
    -> {"type": "auth", "token": "<access token>"}      first message, within 10 seconds
    <- {"type": "ready", "user": "<id>", "role": "user"}
    <- {"type": "book.status", ...} / {"type": "question.status", ...} / {"type": "question.done", ...}

The token goes in the first message, not the URL, so it never lands in access logs. A user gets
their own events; an admin also gets everyone's. Closes with 4401 on a bad or missing token.
"""

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from marginalia.api import events
from marginalia.api.db import User
from marginalia.api.security import TokenError

router = APIRouter()
AUTH_TIMEOUT = 10


@router.websocket('/ws')
async def websocket(ws: WebSocket):
    svc = ws.app.state.services
    await ws.accept()
    try:
        first = json.loads(await asyncio.wait_for(ws.receive_text(), AUTH_TIMEOUT))
        claims = svc.tokens.decode(first.get('token', ''), 'access') if first.get('type') == 'auth' else None
    except (asyncio.TimeoutError, ValueError, TokenError, WebSocketDisconnect):
        claims = None
    if claims is None:
        await ws.close(code=4401, reason='unauthorized')
        return
    async with svc.db.sessions() as db:
        user = await db.get(User, claims['sub'])
    if user is None or not user.is_active:
        await ws.close(code=4401, reason='unauthorized')
        return

    # Every event is also published to the admin channel, so admins need only that one.
    channels = [events.ADMIN_CHANNEL if user.role == 'admin' else events.user_channel(user.id)]
    pubsub = svc.redis.pubsub()
    await pubsub.subscribe(*channels)
    await ws.send_json({'type': 'ready', 'user': str(user.id), 'role': user.role})

    async def relay():
        async for message in pubsub.listen():
            if message.get('type') != 'message':
                continue
            data = message['data'] if isinstance(message['data'], str) else message['data'].decode()
            await ws.send_text(data)

    async def drain():
        # Keeps the socket read side open; clients may send pings, which are ignored.
        while True:
            await ws.receive_text()

    tasks = [asyncio.create_task(relay()), asyncio.create_task(drain())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        await pubsub.unsubscribe(*channels)
        await pubsub.aclose()
