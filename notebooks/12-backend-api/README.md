# 12 · The backend: API, worker and live events

Notebook: [12-backend-api.ipynb](12-backend-api.ipynb) · Code: `src/marginalia/api/`, `src/marginalia/worker/`, `migrations/`

## What it does

A FastAPI service that the web UI (and anyone with a token) talks to:

- **Accounts:** sign-up and sign-in.
- **Uploads:** checked at once.
- **The shelf:** each reader's books with their own prompts.
- **Questions.**
- **Admin controls:** users, books, questions log, batches, logs.
- **Live events:** a websocket pushes book progress and the agent's steps while they happen.

A separate worker process takes the slow jobs from Redis queues. It screens questions, runs the
agent, and deletes books nobody reads.

## Why?

An agent answer takes minutes, and a book takes a batch to process. A request cannot wait that long,
so the API accepts the work (202), a queue holds it, and the worker does it while events report
progress. Postgres keeps what must survive (accounts, shelves, questions); Redis carries what is
fast and short-lived (jobs, events, rate limits). Uploads are validated before anything is stored:
a bad PDF or a hostile prompt is refused in the same request, with every problem listed.

## For what

- Everything the web UI shows.
- Several readers sharing one processed book, each with their own prompts and questions.
- An admin running the service from a browser.

## Simple explanation

A front desk and a back office. The front desk (API) takes your request, checks your ID, gives
you a ticket, and keeps you informed by text message (websocket). The back office (worker) does the
actual work, one ticket at a time per specialist, and posts updates as it goes.

## Examples

```
POST /api/auth/signup   {"email": "reader@example.com", "password": "..."}       201
POST /api/auth/login    → {"access_token": "...", "expires_in": 900, "user": {...}} + refresh cookie
POST /api/books         multipart: file, system_prompt?, user_prompt?            202 "processed in the next batch at 15:00 UTC"
POST /api/books         book.epub + "Ignore all previous instructions"           422 problems: [file: not_pdf, system_prompt: injection]
POST /api/books/{id}/questions  {"text": "Who is Sonia?", "sampling": {"temperature": 0.9}}  202 queued
WS   /api/ws            first message {"type": "auth", "token": "..."} → question.status, question.done, book.status
```

![An upload refused in the same request, with every problem next to its field](../../docs/screenshots/reader-upload-errors.png)
*An upload refused in the same request, with every problem next to its field.*

![What the API stores with a question: the guard's verdict, the searches, tokens per role](../../docs/screenshots/admin-question-detail.png)
*What the API stores with a question: the guard's verdict, the searches, tokens per role.*

## Technical

- **Auth:**
  - argon2id password hashes;
  - 15-minute access JWTs;
  - refresh tokens in an HttpOnly cookie scoped to `/api/auth`, rotated on each use. Reusing a rotated
    token revokes the whole family, since it was stolen or replayed;
  - rate limits on sign-up and sign-in;
  - the admin account comes from `CP_ADMIN_EMAIL` and `CP_ADMIN_PASSWORD`, set up at start.
- **Database:**
  - async SQLAlchemy 2 with portable types (SQLite in tests, Postgres in Docker), and Alembic
    migrations `0001`–`0004`;
  - `library_books` holds one row per distinct PDF, and `user_books` a reader's access to it with
    their prompts;
  - `questions` carry their outcome (`answered_by`) and `details`: guard verdict, cache match, tool
    calls, sampling, tokens.
- **Queues:**
  - Redis Streams with a consumer group: `ingest` (deletions), `screen` (guard and cache, 4 at a time)
    and `ask` (the agent, as many as Ollama has slots);
  - a job is acknowledged after its handler, and jobs of a dead worker are reclaimed;
  - streams are capped at about 10,000 entries.
- **Events:** Redis pub/sub on a channel per user, plus an admin channel. The websocket authenticates
  with its first message (closing with 4401 when the token is refused) and the UI reconnects with
  backoff.
- **Production:**
  - every response has an `X-Request-ID` and security headers, and errors are JSON;
  - with `CP_ENV=production` the API refuses unsafe settings and hides the docs;
  - logs are JSON, metrics are on a private port, and `/health` and `/health/live` are available.
- **Covers:** served by signed links an `<img>` can load without a token.

## Tech examples

```bash
python -m marginalia.api                       # the API on 127.0.0.1:8010 (on the host)
python -m marginalia.worker                    # the worker
python -m marginalia.api create-admin you@example.com
alembic upgrade head
```

```python
from marginalia.api.app import build_services, create_app
app = create_app(build_services())             # what `python -m marginalia.api` serves
```

## How it is connected to other steps

- **Uploads:** call step 01 (validation) and store in the library; the batch (step 13) processes them.
- **Questions:** go through the worker's screening (step 10) and the agent (step 09).
- **The web UI (step 16):** is served by this API from the same origin.
- **Observability:** the API's and worker's metrics and logs feed step 14, and the questions table
  feeds the reporting views used by Grafana and Evidently (steps 14–15).
