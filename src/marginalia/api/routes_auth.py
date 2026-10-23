"""Sign-up, sign-in and token refresh.

    POST /api/auth/signup   {email, password}        -> 201 user
    POST /api/auth/login    {email, password}        -> access token; refresh token as an HttpOnly cookie
    POST /api/auth/refresh  (refresh cookie)         -> new access token; the refresh token is rotated
    POST /api/auth/logout   (refresh cookie)         -> 204; the refresh token is revoked
    GET  /api/auth/me                                -> the signed-in user
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from marginalia.api.db import RefreshToken, User
from marginalia.api.deps import Services, current_user, rate_limit, services, session, unauthorized
from marginalia.api.security import DUMMY_HASH, TokenError, hash_password, verify_password

router = APIRouter(prefix='/auth', tags=['auth'])
COOKIE = 'marginalia_refresh'
COOKIE_PATH = '/api/auth'          # sent only to the token endpoints


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    is_active: bool
    created_at: datetime


class TokenOut(BaseModel):
    access_token: str
    token_type: str = 'bearer'
    expires_in: int
    user: UserOut


def user_out(u):
    return UserOut(id=u.id, email=u.email, role=u.role, is_active=u.is_active, created_at=u.created_at)


async def _issue(svc, db, response, user, family=None):
    jti, family = uuid.uuid4(), family or uuid.uuid4()
    token, expires = svc.tokens.refresh(user.id, jti, family)
    db.add(RefreshToken(id=jti, user_id=user.id, family=family, expires_at=expires))
    await db.commit()
    response.set_cookie(COOKIE, token, httponly=True, secure=svc.settings.secure_cookies, samesite='strict',
                        path=COOKIE_PATH, max_age=int((expires - datetime.now(timezone.utc)).total_seconds()))
    return TokenOut(access_token=svc.tokens.access(user), expires_in=int(svc.tokens.access_ttl.total_seconds()),
                    user=user_out(user))


@router.post('/signup', status_code=201, response_model=UserOut,
             dependencies=[Depends(rate_limit('signup', 5))])
async def signup(body: Credentials, svc: Services = Depends(services), db=Depends(session)):
    user = User(email=body.email.lower(), password_hash=hash_password(body.password))
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, 'An account with this email already exists') from None
    return user_out(user)


@router.post('/login', response_model=TokenOut, dependencies=[Depends(rate_limit('login', 10))])
async def login(body: Credentials, response: Response, svc: Services = Depends(services), db=Depends(session)):
    user = (await db.execute(select(User).where(User.email == body.email.lower()))).scalar_one_or_none()
    ok = verify_password(user.password_hash if user else DUMMY_HASH, body.password)
    if not user or not ok:
        raise unauthorized('Wrong email or password')
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, 'This account is disabled')
    return await _issue(svc, db, response, user)


@router.post('/refresh', response_model=TokenOut)
async def refresh(response: Response, token: str | None = Cookie(None, alias=COOKIE),
                  svc: Services = Depends(services), db=Depends(session)):
    if not token:
        raise unauthorized('No refresh token')
    try:
        claims = svc.tokens.decode(token, 'refresh')
        jti, family = uuid.UUID(claims['jti']), uuid.UUID(claims['fam'])
    except (TokenError, KeyError, ValueError):
        raise unauthorized('Invalid refresh token') from None
    stored = await db.get(RefreshToken, jti)
    now = datetime.now(timezone.utc)
    if stored is None:
        raise unauthorized('Invalid refresh token')
    if stored.revoked_at is not None:
        # A rotated token used again: someone has a copy. End every session of this family.
        await db.execute(update(RefreshToken).where(RefreshToken.family == family, RefreshToken.revoked_at.is_(None))
                         .values(revoked_at=now))
        await db.commit()
        response.delete_cookie(COOKIE, path=COOKIE_PATH)
        raise unauthorized('Refresh token reused; please sign in again')
    user = await db.get(User, stored.user_id)
    if user is None or not user.is_active:
        raise unauthorized('account not found or disabled')
    stored.revoked_at = now
    return await _issue(svc, db, response, user, family=family)


@router.post('/logout', status_code=204)
async def logout(response: Response, token: str | None = Cookie(None, alias=COOKIE),
                 svc: Services = Depends(services), db=Depends(session)):
    if token:
        try:
            claims = svc.tokens.decode(token, 'refresh')
            stored = await db.get(RefreshToken, uuid.UUID(claims['jti']))
            if stored and stored.revoked_at is None:
                stored.revoked_at = datetime.now(timezone.utc)
                await db.commit()
        except (TokenError, KeyError, ValueError):
            pass
    response.delete_cookie(COOKIE, path=COOKIE_PATH)
    response.status_code = 204
    return response


@router.get('/me', response_model=UserOut)
async def me(user: User = Depends(current_user)):
    return user_out(user)
