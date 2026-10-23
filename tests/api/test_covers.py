"""Book covers as signed links, and what the API tells about covers and screened questions."""

import json
import time
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import update

from marginalia.api.db import LibraryBook, UserBook
from marginalia.api.jobs import QUEUES
from marginalia.api.routes_covers import cover_url
from marginalia.ingest.cover import generated_cover
from tests.api.conftest import make_admin, upload, user


async def _give_cover(svc, book_id):
    profile = svc.library.get(book_id)
    (svc.library.path(book_id) / 'cover.svg').write_bytes(generated_cover(profile.title, profile.author))
    profile.cover = {'file': 'cover.svg', 'source': 'generated'}
    svc.library.save(profile)
    async with svc.db.sessions() as s:
        await s.execute(update(LibraryBook).where(LibraryBook.book_id == book_id)
                        .values(cover='cover.svg', cover_source='generated', status='ready'))
        await s.execute(update(UserBook).where(UserBook.book_id == book_id).values(prompt_status='approved'))
        await s.commit()


def _book_with_cover(client, svc):
    headers = user(client)
    book = upload(client, headers).json()['book']
    assert book['cover_url'] is None, 'no cover before the batch'
    client.portal.call(_give_cover, svc, book['book_id'])
    return headers, client.get('/api/books', headers=headers).json()[0]


def test_books_carry_a_signed_cover_link_that_loads_without_a_token(client, svc):
    _, book = _book_with_cover(client, svc)
    assert book['cover_source'] == 'generated'
    r = client.get(book['cover_url'])                        # no Authorization header, as an <img> loads it
    assert r.status_code == 200 and r.headers['content-type'].startswith('image/svg+xml')
    assert r.content.startswith(b'<svg') and "default-src 'none'" in r.headers['content-security-policy']
    assert 'max-age' in r.headers['cache-control']


def test_the_link_is_stable_for_the_day(client, svc):
    headers, book = _book_with_cover(client, svc)
    assert client.get('/api/books', headers=headers).json()[0]['cover_url'] == book['cover_url']


def test_tampered_expired_or_redirected_links_are_refused(client, svc):
    _, book = _book_with_cover(client, svc)
    url = urlsplit(book['cover_url'])
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert client.get(url.path, params={**q, 'sig': '0' * 32}).status_code == 404
    assert client.get(url.path, params={**q, 'exp': int(q['exp']) + 86400}).status_code == 404
    other = url.path.replace(book['book_id'], 'another-book')
    assert client.get(other, params=q).status_code == 404
    assert client.get(url.path.replace('cover.svg', 'profile.json'), params=q).status_code == 404

    class Row:
        book_id, cover, updated_at = book['book_id'], 'cover.svg', None
    old = cover_url(svc.tokens, Row, now=time.time() - 5 * 86400)
    assert client.get(old).status_code == 404, 'expired'


def test_admin_book_list_has_covers_and_question_counts(client, svc):
    headers, book = _book_with_cover(client, svc)
    client.post(f"/api/books/{book['id']}/questions", headers=headers, json={'text': 'Who is Tom?'})
    admin = user(client, 'boss@example.com')
    make_admin(client, svc, 'boss@example.com')
    row = client.get('/api/admin/books', headers=admin).json()[0]
    assert row['cover_url'].startswith(f"/api/covers/{book['book_id']}/cover.svg?")
    assert (row['questions'], row['refused'], row['cover_source']) == (1, 0, 'generated')


def test_a_fresh_question_is_screened_without_the_cache(client, svc):
    headers, book = _book_with_cover(client, svc)
    r = client.post(f"/api/books/{book['id']}/questions", headers=headers, json={'text': 'Who is Tom?', 'fresh': True})
    assert r.status_code == 202
    [(_, fields)] = client.portal.call(svc.redis.xrange, QUEUES['screen'])
    assert fields['kind'] == 'screen_question'
    assert json.loads(fields['payload']) == {'question_id': r.json()['id'], 'fresh': True}


def test_sampling_settings_are_validated_and_kept_with_the_question(client, svc):
    headers, book = _book_with_cover(client, svc)
    options = client.get('/api/questions/sampling', headers=headers).json()
    assert options['defaults'] == {'temperature': 0.6, 'top_p': 0.95, 'top_k': 40, 'max_searches': 6}
    assert options['limits']['temperature'] == {'min': 0.1, 'max': 1.5,
                                                'description': 'Randomness of the wording; lower is more focused.'}
    url = f"/api/books/{book['id']}/questions"
    r = client.post(url, headers=headers, json={'text': 'Who is Tom?', 'sampling': {'temperature': 0.9, 'top_k': 20}})
    assert r.status_code == 202 and r.json()['details'] == {'sampling': {'temperature': 0.9, 'top_k': 20}}
    for bad in ({'temperature': 0}, {'top_p': 1.5}, {'max_searches': 50}, {'creativity': 1}):
        assert client.post(url, headers=headers, json={'text': 'Who is Tom?', 'sampling': bad}).status_code == 422, bad
    plain = client.post(url, headers=headers, json={'text': 'Who is Tom?'})
    assert plain.json()['details'] == {}
