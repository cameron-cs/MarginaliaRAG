"""The FastAPI application.

    python -m marginalia.api                     # serve on 127.0.0.1:$CP_API_PORT (8010)
    python -m marginalia.api create-admin EMAIL  # create or promote an admin (password from a prompt)

Every response carries an X-Request-ID (the caller's, if it sent a sane one) that also tags the
request's log lines, and security headers. Unhandled errors become a JSON 500 with that id, and
the traceback goes to the log only. With CP_ENV=production the app refuses to start on unsafe
settings (Settings.problems) and hides the API docs unless CP_API_DOCS=1.
"""

import asyncio
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy import func, select, text

from marginalia import metrics
from marginalia.api import (jobs, routes_admin, routes_admin_questions, routes_auth, routes_books, routes_covers,
                        routes_questions, routes_ws)
from marginalia.api.admins import ensure_configured_admin
from marginalia.api.airflow import Airflow
from marginalia.api.db import Database, LibraryBook, User
from marginalia.api.deps import Services
from marginalia.api.security import Tokens, resolve_secret
from marginalia.config import Settings
from marginalia.guard.prompts import check_rules
from marginalia.library import Library
from marginalia.logs import request_id

log = logging.getLogger('marginalia.api')
access_log = logging.getLogger('marginalia.api.access')

_REQUEST_ID = re.compile(r'^[A-Za-z0-9._-]{8,64}$')
_QUIET = ('/health', '/assets/', '/favicon')          # logged at DEBUG: probes and static files
# The web UI loads only its own scripts and styles; React sets inline style attributes; covers are
# same-origin images; live events use a same-origin websocket.
_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self' ws: wss:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")


def _version():
    try:
        return version('marginalia-rag')
    except PackageNotFoundError:
        return '0.0.0'


def build_services(settings=None, db=None, redis=None, library=None, airflow=None):
    settings = settings or Settings()
    if redis is None:
        import redis.asyncio as aioredis
        redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    if airflow is None and settings.airflow_password:
        airflow = Airflow(settings.airflow_url, settings.airflow_user, settings.airflow_password)
    return Services(settings=settings, db=db or Database(settings.database_url), redis=redis,
                    tokens=Tokens(resolve_secret(settings), settings.access_token_minutes, settings.refresh_token_days),
                    library=library or Library(settings.library_dir), prompt_rules=check_rules, airflow=airflow)


def create_app(services=None):
    svc = services or build_services()
    errors, warnings = svc.settings.problems()
    for w in warnings:
        log.warning('production settings: %s', w)
    if errors:
        raise RuntimeError('Unsafe production settings:\n- ' + '\n- '.join(errors))

    @asynccontextmanager
    async def lifespan(app):
        await jobs.ensure_groups(svc.redis)
        await ensure_configured_admin(svc.db, svc.settings)
        gauges = None
        if metrics.serve(svc.settings.metrics_port, 'api'):
            gauges = asyncio.create_task(_refresh_gauges(svc))
        log.info('MarginaliaRAG API %s ready (%s)', app.version, svc.settings.environment)
        yield
        if gauges:
            gauges.cancel()
        await svc.db.dispose()

    docs = svc.settings.api_docs
    app = FastAPI(title='MarginaliaRAG', version=_version(), lifespan=lifespan, docs_url='/docs' if docs else None,
                  redoc_url=None, openapi_url='/openapi.json' if docs else None)
    app.state.services = svc

    @app.middleware('http')
    async def request_context(request: Request, call_next):
        rid = request.headers.get('x-request-id', '')
        rid = rid if _REQUEST_ID.match(rid) else uuid.uuid4().hex[:16]
        token = request_id.set(rid)
        start = time.perf_counter()
        try:
            try:
                response = await call_next(request)
            except Exception:
                log.exception('unhandled error on %s %s', request.method, request.url.path)
                response = JSONResponse(status_code=500, content={'error': {
                    'code': 'internal', 'message': 'Something went wrong on our side.', 'request_id': rid}})
            seconds = time.perf_counter() - start
            ms = round(seconds * 1000, 1)
            path = request.url.path
            route = _route(request)
            metrics.HTTP_REQUESTS.labels(request.method, route, str(response.status_code)).inc()
            metrics.HTTP_SECONDS.labels(request.method, route).observe(seconds)
            level = logging.DEBUG if path.startswith(_QUIET) else logging.INFO
            access_log.log(level, '%s %s %s %sms', request.method, path, response.status_code, ms,
                           extra={'method': request.method, 'path': path, 'status': response.status_code,
                                  'duration_ms': ms})
            headers = response.headers
            headers['X-Request-ID'] = rid
            headers.setdefault('X-Content-Type-Options', 'nosniff')
            headers.setdefault('Referrer-Policy', 'same-origin')
            headers.setdefault('X-Frame-Options', 'DENY')
            headers.setdefault('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
            if response.headers.get('content-type', '').startswith('text/html'):
                headers.setdefault('Content-Security-Policy', _CSP)
            if svc.settings.secure_cookies:
                headers.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
            return response
        finally:
            request_id.reset(token)

    app.add_middleware(CORSMiddleware, allow_origins=list(svc.settings.cors_origins), allow_credentials=True,
                       allow_methods=['*'], allow_headers=['*'])
    # Everything under /api, so the web UI can own the other paths (/books/<id>, /admin, ...).
    for module in (routes_auth, routes_books, routes_covers, routes_questions, routes_admin_questions, routes_admin,
                   routes_ws):
        app.include_router(module.router, prefix='/api')

    @app.get('/health/live', tags=['ops'])
    async def live():
        """The process is up (no dependencies checked): for restart decisions."""
        return {'status': 'ok'}

    @app.get('/health', tags=['ops'])
    async def health():
        """Ready to serve: the database and Redis answer."""
        checks = {}
        try:
            async with svc.db.sessions() as s:
                await s.execute(text('SELECT 1'))
            checks['database'] = 'ok'
        except Exception as exc:
            checks['database'] = f'error: {type(exc).__name__}'
        try:
            await svc.redis.ping()
            checks['redis'] = 'ok'
        except Exception as exc:
            checks['redis'] = f'error: {type(exc).__name__}'
        ok = all(v == 'ok' for v in checks.values())
        return JSONResponse(status_code=200 if ok else 503, content={'status': 'ok' if ok else 'degraded', **checks})

    ui = Path(svc.settings.ui_dir)
    if (ui / 'index.html').exists():
        app.mount('/', SinglePageApp(directory=ui, html=True), name='ui')
    return app


def _route(request):
    """The route template (/api/books/{user_book_id}), so metrics do not get a label per book."""
    route = request.scope.get('route')
    template = getattr(route, 'path', None)
    if template is None:
        return 'unmatched'
    if not template:
        return 'ui'                      # the web UI is mounted at ''
    # Routers are included under /api, but their routes keep the paths they were declared with.
    if request.url.path.startswith('/api/') and not template.startswith('/api'):
        template = '/api' + template
    return template


async def _refresh_gauges(svc, every=30):
    """Queue lengths, books by status and accounts, for Prometheus; cheap queries, twice a minute."""
    while True:
        try:
            for queue, s in (await jobs.queue_stats(svc.redis)).items():
                metrics.QUEUE.labels(queue, 'waiting').set(s['waiting'])
                metrics.QUEUE.labels(queue, 'pending').set(s['pending'])
            async with svc.db.sessions() as db:
                books = dict((await db.execute(select(LibraryBook.status, func.count())
                                               .group_by(LibraryBook.status))).all())
                users = dict((await db.execute(select(User.is_active, func.count()).group_by(User.is_active))).all())
            for status in ('uploaded', 'processing', 'ready', 'rejected', 'failed'):
                metrics.BOOKS.labels(status).set(books.get(status, 0))
            metrics.USERS.labels('active').set(users.get(True, 0))
            metrics.USERS.labels('disabled').set(users.get(False, 0))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning('could not refresh the metrics gauges', exc_info=True)
        await asyncio.sleep(every)


class SinglePageApp(StaticFiles):
    """The web UI: real files as they are, any other path the app's index.html, so the browser's
    router handles /books/<id> and /admin. Unknown /api paths stay 404s."""

    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            url_path = scope['path']          # `path` is OS-normalized (backslashes on Windows)
            if exc.status_code != 404 or url_path.startswith('/api/') or '.' in url_path.rsplit('/', 1)[-1]:
                raise
            return await super().get_response('index.html', scope)
