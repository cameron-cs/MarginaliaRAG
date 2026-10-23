"""Shared services and FastAPI dependencies."""

import uuid
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from marginalia.api.db import Database, User
from marginalia.api.security import TokenError, Tokens
from marginalia.config import Settings
from marginalia.library import Library


@dataclass
class Services:
    settings: Settings
    db: Database
    redis: object               # redis.asyncio.Redis
    tokens: Tokens
    library: Library
    prompt_rules: object        # marginalia.guard.prompts.check_rules
    airflow: object = None      # marginalia.api.airflow.Airflow, if configured

    async def next_batch(self):
        """When Airflow will next process waiting books (ISO time), or None if unknown."""
        if self.airflow is None:
            return None
        try:
            schedule = await self.airflow.schedule()
        except Exception:
            return None
        return None if schedule['paused'] else schedule['next_run_at']


def services(request: Request) -> Services:
    return request.app.state.services


async def session(svc: Services = Depends(services)):
    async with svc.db.sessions() as s:
        yield s


_bearer = HTTPBearer(auto_error=False)


def unauthorized(detail='Not authenticated'):
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail, headers={'WWW-Authenticate': 'Bearer'})


async def current_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
                       svc: Services = Depends(services), db=Depends(session)) -> User:
    if creds is None:
        raise unauthorized()
    try:
        claims = svc.tokens.decode(creds.credentials, 'access')
    except TokenError as exc:
        raise unauthorized(str(exc)) from exc
    user = await db.get(User, claims['sub'])
    if user is None or not user.is_active:
        raise unauthorized('account not found or disabled')
    return user


async def admin_user(user: User = Depends(current_user)) -> User:
    if user.role != 'admin':
        raise HTTPException(status.HTTP_403_FORBIDDEN, 'Admins only')
    return user


def rate_limit(name, limit, window_seconds=60):
    """At most `limit` requests per client address per window, counted in Redis."""
    async def check(request: Request, svc: Services = Depends(services)):
        client = request.client.host if request.client else 'unknown'
        key = f'marginalia:rate:{name}:{client}'
        count = await svc.redis.incr(key)
        if count == 1:
            await svc.redis.expire(key, window_seconds)
        if count > limit:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, 'Too many requests, try again in a minute')
    return check


def parse_uuid(value):
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'Not found') from None
