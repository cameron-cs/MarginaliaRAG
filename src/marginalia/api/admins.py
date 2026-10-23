"""Admin accounts.

The configured admin (CP_ADMIN_EMAIL and CP_ADMIN_PASSWORD) is set up whenever the API starts:
the account is created if missing, made an active admin if it exists, and given the configured
password if it has another one. The configuration is the source of truth, so a forgotten password
is fixed by editing .env and restarting. More admins: `python -m marginalia.api create-admin EMAIL`,
or the switches on the admin page.
"""

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from marginalia.api.db import User
from marginalia.api.security import hash_password, verify_password

log = logging.getLogger(__name__)


async def ensure_admin(db, email, password=None):
    """Create or promote the admin `email`; set `password` if given and different.
    Returns 'created', 'updated' or 'unchanged'."""
    email = email.strip().lower()
    async with db.sessions() as s:
        user = (await s.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user is None:
            if not password:
                raise ValueError(f'a password is needed to create the admin {email}')
            s.add(User(email=email, password_hash=hash_password(password), role='admin', is_active=True))
            try:
                await s.commit()
            except IntegrityError:           # another API process created it at the same moment
                await s.rollback()
                return 'unchanged'
            return 'created'
        changed = False
        if user.role != 'admin' or not user.is_active:
            user.role, user.is_active, changed = 'admin', True, True
        if password and not verify_password(user.password_hash, password):
            user.password_hash, changed = hash_password(password), True
        if changed:
            await s.commit()
        return 'updated' if changed else 'unchanged'


async def ensure_configured_admin(db, settings):
    """The admin from the settings, if any; logged, never fatal for the API."""
    if not settings.admin_email:
        return None
    try:
        outcome = await ensure_admin(db, settings.admin_email, settings.admin_password or None)
    except Exception:
        log.exception('could not set up the configured admin %s', settings.admin_email)
        return None
    log.info('configured admin %s: %s', settings.admin_email.lower(), outcome)
    return outcome
