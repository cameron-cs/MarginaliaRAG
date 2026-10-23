"""The admin account from the configuration (CP_ADMIN_EMAIL, CP_ADMIN_PASSWORD), set up at start."""

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from marginalia.api.admins import ensure_admin
from marginalia.api.app import build_services, create_app
from marginalia.api.db import Database, User
from tests.api.conftest import FakeAirflow, make_settings

PASSWORD = 'a-long-admin-password'


def _app(tmp_path, **overrides):
    # A database file, so it outlives one app: each app disposes of its database when it stops.
    url = f"sqlite+aiosqlite:///{(tmp_path / 'marginalia.db').as_posix()}"
    settings = make_settings(tmp_path, ui_dir=str(tmp_path / 'no-ui'), database_url=url, **overrides)
    svc = build_services(settings, db=Database(url), redis=fakeredis.aioredis.FakeRedis(decode_responses=True),
                         airflow=FakeAirflow())
    return create_app(svc), svc


def _tables(tmp_path):
    app, svc = _app(tmp_path)
    with TestClient(app) as c:
        c.portal.call(svc.db.create_all)


def _login(client, email, password):
    return client.post('/api/auth/login', json={'email': email, 'password': password})


def test_the_configured_admin_can_sign_in_at_once(tmp_path):
    _tables(tmp_path)
    app, _ = _app(tmp_path, admin_email='Boss@Example.com', admin_password=PASSWORD)
    with TestClient(app) as c:
        r = _login(c, 'boss@example.com', PASSWORD)
        assert r.status_code == 200 and r.json()['user']['role'] == 'admin'
        headers = {'Authorization': f"Bearer {r.json()['access_token']}"}
        assert c.get('/api/admin/users', headers=headers).status_code == 200


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.mark.anyio
async def test_ensure_admin_creates_promotes_and_resets():
    db = Database('sqlite+aiosqlite://')
    await db.create_all()
    assert await ensure_admin(db, 'boss@example.com', PASSWORD) == 'created'
    assert await ensure_admin(db, 'BOSS@example.com', PASSWORD) == 'unchanged'
    async with db.sessions() as s:
        user = (await s.execute(select(User))).scalar_one()
        user.role, user.is_active = 'user', False                  # demoted and disabled on the page
        await s.commit()
    assert await ensure_admin(db, 'boss@example.com', 'a-new-password-123') == 'updated'
    async with db.sessions() as s:
        user = (await s.execute(select(User))).scalar_one()
        assert (user.role, user.is_active) == ('admin', True)
    with pytest.raises(ValueError):
        await ensure_admin(db, 'new@example.com', None)            # no password, no new account
    await db.dispose()


def test_a_password_change_in_the_config_takes_effect_on_restart(tmp_path):
    _tables(tmp_path)
    with TestClient(_app(tmp_path, admin_email='boss@example.com', admin_password=PASSWORD)[0]):
        pass
    with TestClient(_app(tmp_path, admin_email='boss@example.com', admin_password='another-long-one')[0]) as c:
        assert _login(c, 'boss@example.com', PASSWORD).status_code == 401
        assert _login(c, 'boss@example.com', 'another-long-one').status_code == 200


def test_production_needs_a_strong_admin_password(tmp_path):
    with pytest.raises(RuntimeError) as err:
        _app(tmp_path, environment='production', jwt_secret='s' * 48, secure_cookies=True,
             airflow_password='a-real-password', admin_email='boss@example.com', admin_password='change-me')
    assert 'CP_ADMIN_PASSWORD' in str(err.value)
