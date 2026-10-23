"""Operational behaviour: request ids, security headers, errors, health, docs, production checks, logs."""

import io
import json
import logging

import fakeredis
import pytest
from fastapi.testclient import TestClient

from marginalia import logs
from marginalia.api.app import build_services, create_app
from marginalia.api.db import Database
from tests.api.conftest import FakeAirflow, make_settings


def test_request_ids_are_kept_or_made(client):
    r = client.get('/health/live', headers={'X-Request-ID': 'abc-123-def'})
    assert r.json() == {'status': 'ok'} and r.headers['x-request-id'] == 'abc-123-def'
    made = client.get('/health/live', headers={'X-Request-ID': 'bad id!'}).headers['x-request-id']
    assert made != 'bad id!' and len(made) == 16


def test_security_headers(client):
    h = client.get('/health').headers
    assert h['x-content-type-options'] == 'nosniff' and h['x-frame-options'] == 'DENY'
    assert h['referrer-policy'] == 'same-origin'
    assert 'content-security-policy' not in h, 'only HTML pages get the page policy'
    assert 'strict-transport-security' not in h, 'no HSTS over plain HTTP'


def test_unhandled_errors_are_json_with_the_request_id(tmp_path, caplog):
    app = create_app(_services(tmp_path))

    @app.get('/api/boom')
    async def boom():
        raise ValueError('secret detail')

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get('/api/boom', headers={'X-Request-ID': 'req-00000001'})
    assert r.status_code == 500
    assert r.json() == {'error': {'code': 'internal', 'message': 'Something went wrong on our side.',
                                  'request_id': 'req-00000001'}}
    assert 'secret detail' not in r.text
    assert any('unhandled error' in rec.getMessage() and rec.exc_info for rec in caplog.records)


def _services(tmp_path, **overrides):
    # No web UI: its catch-all route would answer for paths the tests add or expect to be missing.
    settings = make_settings(tmp_path, ui_dir=str(tmp_path / 'no-ui'), **overrides)
    return build_services(settings, db=Database(settings.database_url),
                          redis=fakeredis.aioredis.FakeRedis(decode_responses=True), airflow=FakeAirflow())


def test_production_refuses_unsafe_settings(tmp_path):
    with pytest.raises(RuntimeError) as err:
        create_app(_services(tmp_path, environment='production', jwt_secret='short', airflow_password='change-me'))
    assert 'CP_JWT_SECRET' in str(err.value) and 'CP_SECURE_COOKIES' in str(err.value)
    assert 'CP_AIRFLOW_PASSWORD is a default password' in str(err.value)


def test_production_with_safe_settings_starts_without_docs(tmp_path, caplog):
    app = create_app(_services(tmp_path, environment='production', jwt_secret='s' * 48, secure_cookies=True,
                               airflow_password='a-real-password', api_docs=False, cors_origins=()))
    with TestClient(app) as c:
        c.portal.call(app.state.services.db.create_all)
        assert c.get('/docs').status_code == 404 and c.get('/openapi.json').status_code == 404
        assert c.get('/health').headers['strict-transport-security'].startswith('max-age=')
    assert not [r for r in caplog.records if 'production settings' in r.getMessage()]


def test_docs_are_on_in_development_with_the_package_version(client):
    from importlib.metadata import version
    assert client.get('/openapi.json').json()['info'] == {'title': 'MarginaliaRAG', 'version': version('marginalia-rag')}


def test_json_logs_carry_context():
    out = io.StringIO()
    logs.setup('api', level='INFO', fmt='json', stream=out)
    try:
        token = logs.request_id.set('req-42')
        logging.getLogger('marginalia.test').info('asked %s', 'q1', extra={'question_id': 'q1'})
        logs.request_id.reset(token)
        try:
            raise KeyError('x')
        except KeyError:
            logging.getLogger('marginalia.test').exception('failed')
        logging.getLogger('httpx').info('HTTP Request: GET ...')          # quietened
    finally:
        logs.setup('tests', fmt='text')
    first, second = [json.loads(line) for line in out.getvalue().splitlines()]
    assert {k: first[k] for k in ('level', 'logger', 'msg', 'service', 'request_id', 'question_id')} == {
        'level': 'info', 'logger': 'marginalia.test', 'msg': 'asked q1', 'service': 'api', 'request_id': 'req-42',
        'question_id': 'q1'}
    assert 'request_id' not in second and "KeyError: 'x'" in second['exc']


def test_text_logs_name_the_request():
    out = io.StringIO()
    logs.setup('cli', fmt='text', stream=out)
    try:
        token = logs.request_id.set('req-7')
        logging.getLogger('marginalia.test').warning('slow')
        logs.request_id.reset(token)
    finally:
        logs.setup('tests', fmt='text')
    assert out.getvalue().rstrip().endswith('WARNING marginalia.test: slow [req-7]')
