"""Passwords (argon2id) and tokens (JWT, HS256).

Access tokens are short-lived and stateless; they carry the user id and role. Refresh tokens are
JWTs too, but each one's id (jti) is stored, so they can be rotated on every use and revoked.
"""

import hashlib
import hmac
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()
ALGORITHM = 'HS256'
log = logging.getLogger(__name__)


class TokenError(ValueError):
    pass


def hash_password(password):
    return _hasher.hash(password)


def verify_password(password_hash, password):
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


# Compared against when the email is unknown, so a login takes as long either way and does not
# reveal which emails have accounts.
DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def resolve_secret(settings):
    if settings.jwt_secret:
        return settings.jwt_secret
    if settings.environment == 'production':
        raise RuntimeError('CP_JWT_SECRET must be set in production')
    log.warning('CP_JWT_SECRET is not set: using a random secret, so tokens stop working on restart')
    return secrets.token_urlsafe(48)


class Tokens:
    def __init__(self, secret, access_minutes=15, refresh_days=14):
        self.secret, self.access_ttl, self.refresh_ttl = secret, timedelta(minutes=access_minutes), timedelta(days=refresh_days)

    def sign(self, message):
        """A short HMAC of `message` with the token secret, for signed links (covers)."""
        return hmac.new(self.secret.encode(), message.encode(), hashlib.sha256).hexdigest()[:32]

    def _encode(self, claims, ttl):
        now = datetime.now(timezone.utc)
        return jwt.encode({**claims, 'iat': now, 'exp': now + ttl}, self.secret, algorithm=ALGORITHM)

    def access(self, user):
        return self._encode({'sub': str(user.id), 'role': user.role, 'type': 'access'}, self.access_ttl)

    def refresh(self, user_id, jti, family):
        expires = datetime.now(timezone.utc) + self.refresh_ttl
        token = self._encode({'sub': str(user_id), 'jti': str(jti), 'fam': str(family), 'type': 'refresh'},
                             self.refresh_ttl)
        return token, expires

    def decode(self, token, expected_type):
        try:
            claims = jwt.decode(token, self.secret, algorithms=[ALGORITHM], options={'require': ['exp', 'sub']})
        except jwt.ExpiredSignatureError as exc:
            raise TokenError('token expired') from exc
        except jwt.PyJWTError as exc:
            raise TokenError('invalid token') from exc
        if claims.get('type') != expected_type:
            raise TokenError('wrong token type')
        try:
            claims['sub'] = uuid.UUID(claims['sub'])
        except ValueError as exc:
            raise TokenError('invalid subject') from exc
        return claims
