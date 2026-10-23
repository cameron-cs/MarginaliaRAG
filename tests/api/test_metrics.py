"""Prometheus metrics: API requests by route template, and the worker's question outcomes."""

from prometheus_client import REGISTRY

from tests.api.conftest import upload, user


def value(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0


def test_requests_are_counted_by_route_template(client):
    headers = user(client)
    book = upload(client, headers).json()['book']
    before = value('marginalia_http_requests_total', method='GET', route='/api/books/{user_book_id}', status='200')
    client.get(f"/api/books/{book['id']}", headers=headers)
    client.get(f"/api/books/{book['id']}", headers=headers)
    after = value('marginalia_http_requests_total', method='GET', route='/api/books/{user_book_id}', status='200')
    assert after - before == 2, 'one series for every book, not one per book id'
    client.get('/api/no-such-thing')
    assert value('marginalia_http_requests_total', method='GET', route='unmatched', status='404') >= 1
    assert value('marginalia_http_request_duration_seconds_count', method='GET', route='/api/books/{user_book_id}') >= 2


def test_metrics_are_not_on_the_public_port(client):
    r = client.get('/metrics')
    assert 'marginalia_http_requests_total' not in r.text
