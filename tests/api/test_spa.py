"""The web UI is served by the API: files as they are, other paths fall back to index.html."""

from fastapi.testclient import TestClient

from marginalia.api.app import build_services, create_app
from tests.api.conftest import make_settings


def _client(tmp_path):
    ui = tmp_path / 'dist'
    (ui / 'assets').mkdir(parents=True)
    (ui / 'index.html').write_text('<div id="root"></div>', encoding='utf-8')
    (ui / 'assets' / 'app.js').write_text('console.log(1)', encoding='utf-8')
    import fakeredis
    svc = build_services(make_settings(tmp_path, ui_dir=str(ui)), redis=fakeredis.aioredis.FakeRedis())
    return TestClient(create_app(svc))


def test_files_and_client_routes(tmp_path):
    with _client(tmp_path) as c:
        assert c.get('/').text == '<div id="root"></div>'
        assert c.get('/assets/app.js').text == 'console.log(1)'
        # Client-side routes load the app...
        assert c.get('/books/1234').text == '<div id="root"></div>'
        assert c.get('/admin/pipeline').text == '<div id="root"></div>'
        # ...but missing files and unknown API paths stay errors.
        assert c.get('/assets/missing.js').status_code == 404
        assert c.get('/api/nope').status_code == 404
        assert c.get('/health').json()['status'] in ('ok', 'degraded')


def test_no_ui_build_means_api_only(tmp_path):
    import fakeredis
    svc = build_services(make_settings(tmp_path, ui_dir=str(tmp_path / 'absent')), redis=fakeredis.aioredis.FakeRedis())
    with TestClient(create_app(svc)) as c:
        assert c.get('/').status_code == 404
