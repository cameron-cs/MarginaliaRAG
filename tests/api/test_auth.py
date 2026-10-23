import pytest

from marginalia.api.security import Tokens
from tests.api.conftest import PASSWORD, login, make_admin, signup, user


def test_signup_normalizes_email_and_hides_the_hash(client):
    body = signup(client, 'Reader@Example.COM')
    assert body['email'] == 'reader@example.com' and body['role'] == 'user'
    assert 'password' not in str(body) and 'hash' not in str(body)


@pytest.mark.parametrize('payload,code', [
    ({'email': 'not-an-email', 'password': PASSWORD}, 422),
    ({'email': 'a@example.com', 'password': 'short'}, 422),
    ({'email': 'a@example.com'}, 422),
])
def test_signup_validation(client, payload, code):
    assert client.post('/api/auth/signup', json=payload).status_code == code


def test_duplicate_email_is_rejected(client):
    signup(client)
    r = client.post('/api/auth/signup', json={'email': 'READER@example.com', 'password': PASSWORD})
    assert r.status_code == 409


def test_login_returns_access_token_and_refresh_cookie(client):
    signup(client)
    r = client.post('/api/auth/login', json={'email': 'reader@example.com', 'password': PASSWORD})
    assert r.status_code == 200
    body = r.json()
    assert body['token_type'] == 'bearer' and body['expires_in'] == 15 * 60 and body['user']['role'] == 'user'
    cookie = r.headers['set-cookie']
    assert 'marginalia_refresh=' in cookie and 'HttpOnly' in cookie and 'Path=/api/auth' in cookie
    assert 'samesite=strict' in cookie.lower()


def test_wrong_password_and_unknown_email_look_the_same(client):
    signup(client)
    wrong = client.post('/api/auth/login', json={'email': 'reader@example.com', 'password': 'wrong password!'})
    unknown = client.post('/api/auth/login', json={'email': 'nobody@example.com', 'password': 'wrong password!'})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_me_needs_a_valid_access_token(client, svc):
    headers = user(client)
    assert client.get('/api/auth/me', headers=headers).json()['email'] == 'reader@example.com'
    assert client.get('/api/auth/me').status_code == 401
    assert client.get('/api/auth/me', headers={'Authorization': 'Bearer nonsense'}).status_code == 401
    me = client.get('/api/auth/me', headers=headers).json()
    expired = Tokens(svc.tokens.secret, access_minutes=-1).access(type('U', (), {'id': me['id'], 'role': 'user'}))
    r = client.get('/api/auth/me', headers={'Authorization': f'Bearer {expired}'})
    assert r.status_code == 401 and r.json()['detail'] == 'token expired'


def test_refresh_token_cannot_be_used_as_access_token(client):
    signup(client)
    client.post('/api/auth/login', json={'email': 'reader@example.com', 'password': PASSWORD})
    refresh = client.cookies.get('marginalia_refresh')
    assert client.get('/api/auth/me', headers={'Authorization': f'Bearer {refresh}'}).status_code == 401


def _only_cookie(client, token):
    """Send exactly this refresh token (the jar may still hold a newer one)."""
    client.cookies.clear()
    client.cookies.set('marginalia_refresh', token)


def test_refresh_rotates_and_reuse_revokes_the_family(client):
    signup(client)
    client.post('/api/auth/login', json={'email': 'reader@example.com', 'password': PASSWORD})
    first = client.cookies.get('marginalia_refresh')
    r = client.post('/api/auth/refresh')
    assert r.status_code == 200 and r.json()['access_token']
    second = client.cookies.get('marginalia_refresh')
    assert second and second != first
    # Someone replays the first (rotated) token: refused, and the current one dies with it.
    _only_cookie(client, first)
    assert client.post('/api/auth/refresh').status_code == 401
    _only_cookie(client, second)
    assert client.post('/api/auth/refresh').status_code == 401


def test_logout_revokes_the_refresh_token(client):
    signup(client)
    client.post('/api/auth/login', json={'email': 'reader@example.com', 'password': PASSWORD})
    token = client.cookies.get('marginalia_refresh')
    assert client.post('/api/auth/logout').status_code == 204
    _only_cookie(client, token)
    assert client.post('/api/auth/refresh').status_code == 401


def test_login_is_rate_limited(client):
    signup(client)
    codes = [client.post('/api/auth/login', json={'email': 'reader@example.com', 'password': 'wrong password!'}).status_code
             for _ in range(11)]
    assert codes[:10] == [401] * 10 and codes[10] == 429


def test_disabled_account_cannot_sign_in_or_use_tokens(client, svc):
    reader = user(client)
    admin = user(client, 'boss@example.com')
    make_admin(client, svc, 'boss@example.com')
    reader_id = client.get('/api/auth/me', headers=reader).json()['id']
    assert client.patch(f'/api/admin/users/{reader_id}', headers=admin, json={'is_active': False}).status_code == 200
    assert client.get('/api/auth/me', headers=reader).status_code == 401
    r = client.post('/api/auth/login', json={'email': 'reader@example.com', 'password': PASSWORD})
    assert r.status_code == 403


def test_admin_routes_need_the_admin_role(client, svc):
    reader = user(client)
    assert client.get('/api/admin/users', headers=reader).status_code == 403
    admin = user(client, 'boss@example.com')
    make_admin(client, svc, 'boss@example.com')
    users = client.get('/api/admin/users', headers=admin).json()
    assert {u['email'] for u in users} == {'reader@example.com', 'boss@example.com'}
    reader_id = next(u['id'] for u in users if u['email'] == 'reader@example.com')
    r = client.patch(f'/api/admin/users/{reader_id}', headers=admin, json={'role': 'admin'})
    assert r.json()['role'] == 'admin'
    assert client.get('/api/admin/queues', headers=admin).json().keys() == {'ingest', 'screen', 'ask'}


def test_admin_cannot_demote_themselves(client, svc):
    admin = user(client, 'boss@example.com')
    make_admin(client, svc, 'boss@example.com')
    me = client.get('/api/auth/me', headers=admin).json()
    assert client.patch(f"/api/admin/users/{me['id']}", headers=admin, json={'role': 'user'}).status_code == 400


def test_health(client):
    assert client.get('/health').json() == {'status': 'ok', 'database': 'ok', 'redis': 'ok'}
