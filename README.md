# MarginaliaRAG

Question answering over books, running locally: chapter-aware ingestion of PDFs, hybrid (e5 + BM25)
retrieval in Qdrant, an MCP retrieval server, and a ReAct agent on a local Ollama model that cites
the passages it uses. Around it: a FastAPI backend with a worker, hourly Airflow batches, a React web
UI, and monitoring with Prometheus, Loki, Grafana (including LLM token use) and Evidently, plus
offline evaluation with golden sets and DeepEval.

![A book page: questions answered with citation chips, an answer reused from the cache, and a refused question](docs/screenshots/reader-book.png)

## Screenshots

### For readers

| | |
|---|---|
| ![The shelf](docs/screenshots/reader-shelf.png) | ![The agent's steps, live](docs/screenshots/reader-live-answer.png) |
| **The shelf**: every book with its cover and status. | **A live answer**: each step of the agent arrives over the websocket. |
| ![Answer settings](docs/screenshots/reader-answer-settings.png) | ![Upload problems](docs/screenshots/reader-upload-errors.png) |
| **Answer settings**: temperature, top P, top K, searches and a seed, within safe limits. | **Uploads are checked at once**: not a PDF, a prompt injection, a prompt that forbids searching. |
| ![A book with the reader's own persona](docs/screenshots/reader-book-persona.png) | ![Sign in](docs/screenshots/reader-signin.png) |
| **The reader's own persona and instructions**, reviewed in the batch. | **Sign in**, or create an account. |

### For admins

| | |
|---|---|
| ![Pipeline](docs/screenshots/admin-pipeline.png) | ![A task's log](docs/screenshots/admin-task-log.png) |
| **Pipeline**: schedule, what is waiting, every batch with its tasks; run, stop and retry. | **Task logs** straight from Airflow. |
| ![Questions](docs/screenshots/admin-questions.png) | ![One question in full](docs/screenshots/admin-question-detail.png) |
| **Questions**: every reader's question with its outcome and tokens, and 24-hour stats. | **One question in full**: the guard's verdict, the searches, tokens per role. |
| ![Books](docs/screenshots/admin-books.png) | ![Users](docs/screenshots/admin-users.png) |
| **Books**: the library with readers, questions and refusals. | **Users**: admin and active switches. |

### Monitoring

| | |
|---|---|
| ![Grafana: tokens](docs/screenshots/grafana-tokens.png) | ![Grafana: readers and questions](docs/screenshots/grafana-questions.png) |
| **Grafana, tokens**: per answer, per reader, per book, tokens per second. | **Grafana, readers and questions**: outcomes, answer time, refusals, API, queues, logs. |
| ![Evidently dashboard](docs/screenshots/evidently-dashboard.png) | ![An Evidently report](docs/screenshots/evidently-report.png) |
| **Evidently**: hourly reports on outcomes, quality, tokens and drift. | **One Evidently report**: distributions and text statistics. |
| ![Grafana: batches](docs/screenshots/grafana-batches.png) | ![Airflow](docs/screenshots/airflow-dag.png) |
| **Grafana, batches**: runs, durations, tasks and pools. | **Airflow**: the `marginalia_ingest` DAG. |

The screenshots come from the running stack with demo data: `python docs/screenshots/capture.py seed`,
then `capture` (see the script's header). Email addresses outside example.com are replaced, and covers
of books still in copyright are blurred.

## Step-by-step notebooks

Each step of the system has its own README (what it does, why, for what, a simple explanation,
examples, the technical side, technical examples, and how it connects to the other steps) and a
Jupyter notebook to run it (`notebooks/build_notebooks.py` generates the notebooks; install
`.[dev,monitoring,eval]` to run them all).

| # | Step | |
|---|---|---|
| 01 | [Reading PDFs](notebooks/01-pdf-reading/README.md) | validation, text extraction, running headers, wrapped lines |
| 02 | [Docker](notebooks/02-docker/README.md) | the services, profiles, volumes and ports |
| 03 | [Book structure](notebooks/03-book-structure/README.md) | parts and chapters by bookmarks or headings |
| 04 | [Chunking and splitting](notebooks/04-chunking/README.md) | sentence chunks, dialogue quotes, chapter summaries |
| 05 | [Embeddings](notebooks/05-embeddings/README.md) | e5 dense vectors and BM25 sparse vectors |
| 06 | [Vector store](notebooks/06-vector-store/README.md) | Qdrant, per-book tenancy, index versions |
| 07 | [Retrieval](notebooks/07-retrieval/README.md) | hybrid search, fusion, surrounding text |
| 08 | [MCP server](notebooks/08-mcp-server/README.md) | the retrieval tools the agent calls |
| 09 | [ReAct agent](notebooks/09-react-agent/README.md) | reason, search, cite, grade, revise; token counts |
| 10 | [Guard and cache](notebooks/10-guard-and-cache/README.md) | refusing bad questions, reusing checked answers |
| 11 | [Evaluation](notebooks/11-evaluation/README.md) | golden sets, retrieval metrics, agent runs, DeepEval |
| 12 | [Backend API](notebooks/12-backend-api/README.md) | auth, uploads, queues, the worker, live events |
| 13 | [Batches with Airflow](notebooks/13-batch-airflow/README.md) | the hourly DAG, pools, admin controls |
| 14 | [Observability](notebooks/14-observability/README.md) | logs, metrics, dashboards, tokens |
| 15 | [LLM monitoring](notebooks/15-llm-monitoring/README.md) | Evidently reports, drift, dashboards |
| 16 | [Web UI](notebooks/16-web-ui/README.md) | the reader's and the admin's pages |

## Running the app

Everything runs in Docker. You need Docker Desktop (with GPU support for Ollama, ideally) and about
15 GB of disk for the images and models.

```bash
cp .env.example .env              # then set the passwords and secrets in it (see its comments)
docker compose --profile app --profile airflow --profile observability up -d --build
docker exec marginalia-ollama ollama pull qwen3:4b
```

| What | Where | Sign in with (from `.env`) |
|---|---|---|
| Web UI and API (docs at `/docs` in development) | http://127.0.0.1:8010 | `CP_ADMIN_EMAIL` / `CP_ADMIN_PASSWORD`, or sign up as a reader |
| Airflow | http://127.0.0.1:8090 | `CP_AIRFLOW_USER` / `CP_AIRFLOW_PASSWORD` |
| Grafana | http://127.0.0.1:3030 | `CP_GRAFANA_USER` / `CP_GRAFANA_PASSWORD` |
| Evidently | http://127.0.0.1:8085 | (no sign-in; bound to 127.0.0.1) |
| Prometheus | http://127.0.0.1:9095 | |

Upload a PDF on the shelf; it is processed in the next hourly batch, or at once with **Run all now**
on the admin's Pipeline page. Fewer profiles start less: `--profile app` alone is the web UI, API
and worker, with books processed from the command line (`python -m marginalia ingest --all`).
Stop everything with `docker compose --profile app --profile airflow --profile observability down`
(the data stays in the volumes).

## Quick start (command line)

```bash
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"   # Windows; use .venv/bin on Linux/macOS
cp .env.example .env                                            # adjust Ollama/Qdrant settings if needed
# (extras: .[monitoring] for Evidently, .[eval] for DeepEval)
docker compose up -d                                            # Qdrant (6340) and Ollama (11435)
docker exec marginalia-ollama ollama pull qwen3:4b
python -m marginalia add crime-and-punishment.pdf                  # validate, structure, summarize, index
python -m marginalia.agent --book crime -v "What did Sonia read to Raskolnikov?"
```

## The library

Every book gets its own space, `library/<book_id>/` (git-ignored):

| File | Contents |
|---|---|
| `source.pdf` | the uploaded file, unchanged |
| `profile.json` | what was detected and done: title, author, parts, chapters, quote style, line-wrap style, running headers, character names, pipeline steps, live index version, status |
| `chapters.jsonl` | clean chapter text |
| `summaries.json` | LLM chapter summaries, cached by chapter text hash |
| `cover.jpg` / `cover.svg` | the cover (see the cover step below) |

A book id is a slug of the title plus 8 hex digits of the file's SHA-256, so the same file is always
the same book and is rejected as a duplicate when uploaded again.

```bash
python -m marginalia list
python -m marginalia show crime                 # the profile as JSON
python -m marginalia search crime "Lazarus" --kind chunk
python -m marginalia ingest --all               # run whatever each book still needs (the daily job)
python -m marginalia ingest crime --force       # redo every step
python -m marginalia delete crime               # remove its points and its folder
```

### Ingestion pipeline

`marginalia.ingest.pipeline` runs four idempotent steps. Each stores a key (a hash of its inputs and
code version) in the profile and is skipped while the key is unchanged:

1. **structure**: read and validate the PDF, strip running headers and footers (lines repeated at
   page edges), find parts and chapters, rejoin wrapped lines into paragraphs, detect the quote
   style, list frequent names.
2. **cover**: the PDF's own first page when it is a picture (portrait, no text; a publisher's
   banner page is not taken for a cover), else Open Library's cover for the title and author
   (`CP_COVERS_ONLINE`; requests carry no personal data), else a generated typographic SVG in the
   same colours as the web UI's placeholders.
3. **summarize**: one summary per chapter from Ollama, several chapters at once.
4. **index**: chunks (about 1,000 characters of whole sentences), dialogue quotes and summaries,
   embedded and written to Qdrant as a new index version. The profile then switches to the new
   version and older versions are deleted, so a search never sees a half-built index.

Chapters are found by a cascade: PDF bookmarks first, then heading lines ("Chapter IV",
"CHAPTER ONE", "Chapter 1: Title", "Part II", "Book One", "Prologue", "Epilogue" with numbered
sub-chapters), with contents pages ignored. When neither yields a plausible structure, the book is
rejected with `no_chapters`.

Rejections carry stable codes: `not_pdf`, `unreadable`, `encrypted`, `no_text` (a scan), `empty`,
`no_chapters` and `duplicate`.

### Retrieval

All books share one Qdrant collection (`books`). Every point is tagged with `book_id` (a tenant
index) and `index_version`, and every query is scoped to one book's live version
(`marginalia.index.store.Scope`).

## Agent and MCP server

```bash
python -m marginalia.mcp_server --book crime                    # stdio
python -m marginalia.mcp_server --book crime --transport http   # http://127.0.0.1:8765/mcp
python -m marginalia.agent --book harry                         # interactive
```

The MCP server exposes six retrieval tools (`search_passages`, `search_quotes`,
`search_chapter_summaries`, `get_chapter_summary`, `get_surrounding_text`, `list_chapters`). Its
instructions describe the book's structure and spellings, taken from the profile. The agent is
LangChain `create_agent` on qwen3:4b, with thinking on, a tool-call budget, citation checks and an
LLM grounding check that can send the answer back for one revision.

## Backend

A FastAPI service (`marginalia.api`), a real-time worker for questions (`marginalia.worker`), and Airflow
batches for book processing (`marginalia.batch`), with Postgres
(accounts, shelves, questions), Redis (job queues on streams, live events over pub/sub, rate limits)
and the library, Qdrant and Ollama described above.

```bash
# Everything in Docker (the web UI and API on http://127.0.0.1:8010, API docs at /docs):
docker compose --profile app up -d --build
# More admins besides the configured one (below):
docker exec -it marginalia-api python -m marginalia.api create-admin you@example.com

# Or only the services in Docker, with the API and worker on the host:
docker compose up -d
alembic upgrade head
python -m marginalia.api          # and, in another terminal:
python -m marginalia.worker
```

All endpoints live under `/api`; everything else is the web UI.

**Signing in.** The web UI's admin comes from `.env`: `CP_ADMIN_EMAIL` and `CP_ADMIN_PASSWORD`. The
API sets the account up whenever it starts: it creates it, or makes it an active admin and gives it
that password, so a forgotten password is reset by editing `.env` and restarting. Airflow's own UI
(http://127.0.0.1:8090) uses `CP_AIRFLOW_USER` and `CP_AIRFLOW_PASSWORD`; its sign-in sessions are
signed with `CP_AIRFLOW_SECRET_KEY`, which must stay fixed, or a restart invalidates open pages
("The CSRF session token is missing").

| Endpoint | Purpose |
|---|---|
| `POST /auth/signup`, `POST /auth/login` | email + password; login returns a 15-minute access token, and the refresh token comes in an HttpOnly cookie |
| `POST /auth/refresh`, `POST /auth/logout` | rotate or revoke the refresh token; reusing a rotated token revokes the whole session family |
| `POST /books` | multipart `file` plus optional `system_prompt` / `user_prompt` |
| `GET /books`, `GET /books/{id}`, `PUT /books/{id}/prompts`, `DELETE /books/{id}` | the user's shelf |
| `POST /books/{id}/questions`, `GET /questions/{id}` | ask a question (`{text, fresh?, sampling?}`); the answer arrives over the websocket |
| `GET /questions/sampling` | the server's sampling defaults and the ranges a reader may choose from |
| `GET /admin/users`, `PATCH /admin/users/{id}`, `GET /admin/books`, `POST /admin/books/{id}/reingest`, `GET /admin/queues` | admin role only |
| `GET /admin/questions`, `GET /admin/questions/stats`, `GET /admin/questions/{id}` | every reader's questions: filters (outcome, text, reader, book), paging, stats |
| `GET /admin/pipeline`, `GET /admin/pipeline/waiting`, `POST /admin/pipeline/runs`, `.../runs/{id}/tasks`, `.../tasks/{task}/logs`, `.../runs/{id}/stop`, `.../tasks/{task}/stop`, `.../tasks/{task}/retry`, `POST /admin/pipeline/pause`, `/resume` | batch controls (Airflow) |
| `GET /admin/observability` | where Grafana and Evidently are |
| `GET /health`, `GET /health/live` | ready (database and Redis answer) and alive |
| `WS /ws` | live events; the first message must be `{"type": "auth", "token": "<access token>"}` |

**Uploads.** The file and the prompts are checked at once, and every problem is returned together:
- a real PDF with a text layer, not password-protected, within `CP_MAX_UPLOAD_MB`;
- prompt rules against injection and against asking the agent not to search or cite.

Nothing is stored unless both pass. Nothing heavy runs on upload either: the response is
`202 The book was uploaded. It will be processed in the next batch at HH:MM UTC.`, and the book
shows as `queued`. Two users uploading the same file share one processed book, but each keeps
their own prompts and questions.

**Batches (Airflow).** The DAG `marginalia_ingest` (`dags/marginalia_ingest.py`) runs on
`CP_INGEST_SCHEDULE` (default `@hourly`) and processes every user's waiting books together:

| Task | What it does |
|---|---|
| `plan` | lists prompts awaiting review and books needing pipeline steps; up-to-date books are skipped |
| `review[*]` | the guard model judges each owner's prompts for relevance and safety (GPU pool) |
| `process[*]` | per book: `structure`, `cover` (CPU pool), `summarize` (GPU pool), `index` (CPU pool) |
| `finalize` | writes personas for owners without prompts and tells every reader the outcome |

The tasks call `python -m marginalia.batch`, installed in its own virtualenv inside the Airflow image,
and stream `book.status` events to readers and `pipeline.task` events to admins while they run.

```bash
docker compose --profile app --profile airflow up -d --build   # Airflow UI: http://127.0.0.1:8090
```

Admins control the batches through the API:

- `GET /admin/pipeline`: the schedule and the runs (filter by state, page through them), with a
  summary: runs by state, average and longest duration.
- `GET /admin/pipeline/waiting`: what the next batch would do, without waiting for it: every book
  with the steps it still needs, and every reader whose prompts await review.
- `POST /admin/pipeline/runs`: a batch now, for everything waiting, or with `{"only": [book_id, ...]}`
  for just those books (and their readers' prompt reviews); `{"force": [book_id]}` redoes books.
- Per run: its tasks, each mapped task labelled with its book (from the run's plan), each task's
  log (`.../tasks/{task}/logs?map_index=&try_number=`), stop the run, stop or retry one task.
- `POST /admin/pipeline/pause` and `/resume` control the schedule.

**Questions** stream `question.status` events while the agent works (`thinking`, `searching`,
`reading`, `checking`, `revising`, `answering`), then `question.done` with the answer and its
citations.

### Question guard and answer cache

Before a question reaches the agent, the worker's `screen` queue checks it and looks for an
earlier answer (`marginalia.worker.screening`). It runs on the CPU with local models, so it never waits
behind the agent, and it lets a question through if it breaks.

| Check | Refuses | How |
|---|---|---|
| rules (in the API) | too short or long, obvious injection | patterns; answered at once with 422 |
| injection | attempts to change or reveal the assistant's rules | `protectai/deberta-v3-base-prompt-injection-v2`, ONNX on the CPU, threshold 0.98 |
| names | names the book never uses: "Who is Hagrid?" asked of Crime and Punishment | the book's vocabulary; a word the book always capitalizes is a name, so "halloween" matches "Halloween"; typos are matched |
| topic | requests that are not about a book: "Tell me a joke", "How do I bake bread?" | e5 similarity to example questions about a book against examples of other requests |

A name from the book, or an e5 match of 0.83 or more to the book's passages, makes a question
relevant without the topic check. Plain e5 similarity to the book could not do the job alone:
off-topic questions scored 0.71-0.81 against the books, as did general ones such as "What are the
main themes?". The topic check separated 24 held-out book questions from 22 off-topic ones; the
injection classifier scored 0.0 on book questions and 0.99+ on 7 of 8 attacks (the eighth, a
"grandmother" jailbreak, is caught as off-topic). A refused question is kept with status `refused`
and the reason, sent as `question.refused`.

The **answer cache** (`marginalia.agent.cache`, its own Qdrant collection) reuses answers the grader
found supported and that cite the book (an uncited "the book does not say" may be a search that
missed, and caching it would make the miss stick), for the same book, index version and prompts (readers without prompts share the
book's one generated persona, and so its answers):

- **exact**: the same question once case, punctuation and diacritics are normalized;
- **semantic**: e5 similarity of 0.97 or more *and* the same key terms: names in order, numbers,
  the question word and negation. "Who killed Lizaveta?" is not "Who killed Alyona?", and "What
  does Sonia read to Raskolnikov?" is not the reverse (e5 scores that pair 0.997).

The threshold is high on purpose: rewordings scored 0.95-0.99, but so did different questions
about the same people ("Where does Raskolnikov live?" / "...work?": 0.961). A miss costs an agent
run; a wrong hit would be served as checked. `{"fresh": true}` (the UI's "Ask again") skips the
cache. A re-index starts a new cache, and deleting a book drops its answers.

**Covers** are served as signed links (`GET /api/covers/{book_id}/{file}?v&exp&sig`), so an `<img>`
loads them without a token; book responses carry `cover_url`.

## Web UI

A React app in `ui/` (Vite, TypeScript, React Router), built into the app image and served by
the API from the same origin, so there is no CORS and the refresh cookie stays HttpOnly.

- **Readers:** sign up and sign in; the shelf with covers and live status per book; upload a PDF with an
  optional persona and instructions, with every problem shown next to its field; a book page to
  ask questions, with each step of the agent shown live, citation chips and whether the answer
  was checked against the book; refused questions with the reason; answers reused from earlier
  questions marked as such, with "Ask again" for a fresh one; the table of contents; editing the
  prompts.
- **Answer settings:** under the question box, a reader can set temperature, top P, top K, the
  number of searches and a seed (within limits that keep the agent working; the server's defaults
  until changed, remembered in the browser). Only the agent uses them: the grader keeps the
  server's settings. Answers given with custom settings are cached apart.
- **Admins** (`/admin`):
  - *Pipeline:* schedule on/off, next batch, success and failure counts with average and longest
    duration; what is waiting, with **Run all now**, **Process now** for one book, or **Run
    selected** for several; every batch (filter by state, older ones on demand), each with its
    tasks labelled by book, durations, stop and retry, and each task's **Logs**; a live feed.
  - *Books:* every library book with its cover, readers, questions (and how many were refused)
    and a redo button.
  - *Questions:* every reader's question as it arrives: outcome (agent, cache, refused, failed, in
    progress), time taken, searches, tokens, answer settings; filters and search; one question in
    full with the guard's verdict, the agent's searches, tokens by role and any unchecked claims;
    24-hour stats including tokens (average, most, fewest, tokens per second).
  - *Users:* admin and active switches. **Dashboards** opens Grafana, **LLM monitoring** Evidently.

The access token is kept in memory only and renewed a minute before it expires; a reload signs
back in through the refresh cookie. The websocket reconnects with backoff and renews the token
when the server refuses it.

Node is not needed on the host; these commands run in the Node image:

```bash
docker run --rm -v "$PWD/ui:/app" -w /app node:24.21.0-alpine sh -c "npm ci && npm test && npm run build"
# Development with hot reload on http://127.0.0.1:5180, proxying /api to the API on 8010:
docker run --rm -it -p 5180:5180 -e MARGINALIA_API=http://host.docker.internal:8010 \
  -v "$PWD/ui:/app" -w /app node:24.21.0-alpine npm run dev
```

## Observability

```bash
docker compose --profile app --profile airflow --profile observability up -d
# Grafana: http://127.0.0.1:3030 (CP_GRAFANA_USER / CP_GRAFANA_PASSWORD), Prometheus: http://127.0.0.1:9095
```

| Part | What it does |
|---|---|
| Prometheus | scrapes the API and the worker (`marginalia.metrics`, port 9100 inside the network, never published), Airflow through the StatsD exporter, Loki and Alloy; keeps 15 days; counts a counter's first event too (`created-timestamp-zero-ingestion`, since the StatsD exporter creates counters already at 1); alert rules in `docker/observability/alerts.yml` |
| StatsD exporter | turns Airflow's StatsD metrics into labelled Prometheus metrics: batch runs and tasks by state and duration, pools, executor (`statsd-mapping.yml`); runs with the `airflow` profile |
| Loki + Alloy | Alloy reads this compose project's container logs through the Docker socket and ships them to Loki (7 days); marginalia's JSON log lines become a `level` label plus `logger`, `request_id` and `question_id` metadata |
| Grafana | provisioned data sources (Prometheus, Loki, and Postgres through a read-only login) and three dashboards: *readers and questions* and *batches (Airflow)*, each with its logs, and *tokens* |
| Evidently | LLM monitoring: see below |

The *tokens* dashboard shows tokens in total and per answer (average, most, fewest), generation
speed (tokens per second, by role), the grader's share, tokens per minute, and tokens per reader
and per book, with the most expensive answers. Token counts come from Ollama's own counts
(`eval_count`, `prompt_eval_count`, `eval_duration`) for every LLM call an answer makes, counted per
role (`marginalia.agent.usage`) and saved with the question. The per-reader and per-book panels read
`reporting.questions`, a view (migration `0004`) that the `marginalia_reporting` login may read and
nothing else; `db-init` gives it the password `CP_REPORTING_DB_PASSWORD`.

marginalia's own metrics:

| Metric | Meaning |
|---|---|
| `marginalia_http_requests_total{method,route,status}`, `marginalia_http_request_duration_seconds` | API traffic by route template (not by id) |
| `marginalia_questions_total{outcome}` | questions settled by the agent, the cache, the guard (refused) or failed |
| `marginalia_questions_refused_total{reason}` | injection, not_in_book, off_topic |
| `marginalia_answer_seconds`, `marginalia_agent_tool_calls`, `marginalia_answers_checked_total{verdict}` | the agent's time, searches and the grader's verdicts |
| `marginalia_llm_tokens_total{role,kind,model}`, `marginalia_llm_calls_total{role,model}` | prompt and completion tokens, and LLM calls, by role (agent, grader) |
| `marginalia_llm_tokens_per_answer`, `marginalia_llm_tokens_per_second{role}` | tokens per answer, and generation speed |
| `marginalia_screen_seconds`, `marginalia_jobs_total{queue,outcome}`, `marginalia_job_seconds{queue}` | the worker |
| `marginalia_queue_length{queue,state}`, `marginalia_books{status}`, `marginalia_users{state}` | refreshed by the API every 30 s |

The dashboards are generated: edit `docker/observability/build_dashboards.py` and run it.

### LLM monitoring (Evidently)

The `monitor` service (`python -m marginalia.monitoring --every 3600`) builds an Evidently report
every hour on the last 24 hours of questions against the 7 days before. It covers outcome shares
(refused, cached, failed), answer quality (checked as supported, cites the book, says the book does
not answer), tokens per answer, tokens per second, time to answer, searches, text statistics of
questions and answers, and data drift against the reference week. Each report becomes a run in the
Evidently UI (http://127.0.0.1:8085, project *MarginaliaRAG answers*), whose dashboard charts them
over time.

The monitor reads `reporting.questions` through the read-only login and writes with
`CP_EVIDENTLY_SECRET`. Both services use their own image (`docker/monitoring/`), since Evidently
needs pandas < 3; its telemetry is off. On the host: `pip install -e ".[monitoring]"`, then
`python -m marginalia.monitoring --once --workspace ./evidently-workspace` and
`evidently ui --workspace ./evidently-workspace`.

## Production

- `CP_ENV=production` makes the API refuse to start on unsafe settings: a JWT secret shorter than
  32 characters, cookies without HTTPS (`CP_SECURE_COOKIES=1` is required), a default Airflow
  password. It warns about local CORS origins, and hides the API docs unless `CP_API_DOCS=1`.
- Every response carries an `X-Request-ID` (the caller's, or a new one) that also tags the
  request's log lines, plus security headers (a content security policy on the web UI, no framing,
  no sniffing; HSTS under HTTPS). Unhandled errors return a JSON 500 with the request id; the
  traceback stays in the log.
- Logs go to stderr through one setup (`marginalia.logs`): JSON lines in the containers
  (`CP_LOG_FORMAT=json`, for Loki), text on a terminal; `CP_LOG_LEVEL` sets the level. Commands
  print only their results to stdout (the batch plan Airflow reads from it).
- `CP_FORWARDED_ALLOW_IPS` names the proxies whose `X-Forwarded-*` headers are believed.
- Docker images install exact versions from `constraints.txt` (regenerate it after changing
  dependencies; see its header), with the dependencies in their own layer.
- `/health/live` says the process is up; `/health` that it is ready (database and Redis answer).

## Evaluation

```bash
python -m marginalia.evals retrieval --book crime          # seconds, no LLM
python -m marginalia.evals agent --book crime --runs 1
python -m marginalia.evals compare evals/results/<a> evals/results/<b>
python -m marginalia.evals deepeval --book crime --from evals/results/<a>   # DeepEval on a saved run
```

Golden sets live in `evals/golden/<book>.jsonl`; a test checks that every expected answer term
actually occurs in its gold chapters. Agent runs save each answer with its evidence (the passages
it cited) and its tokens.

**DeepEval** (`pip install -e ".[eval]"`, `marginalia.evals.deepeval_suite`) scores answers with
faithfulness, answer relevancy, contextual relevancy, contextual recall and a G-Eval correctness
metric against the golden answer. The judge is the local Ollama model (`CP_JUDGE_MODEL`, by default
the guard model), wrapped as a `DeepEvalBaseLLM` that reads JSON from the reply, since qwen3 with
thinking on returns nothing when asked for a JSON schema. Results go to `deepeval.jsonl` and
`deepeval.json` next to the run; telemetry is off.

## Configuration

`.env` (template: `.env.example`) configures both `docker-compose.yml` and the Python code. Its
`CP_*` names keep globally set `OLLAMA_*` variables from leaking into this project.

## Tests

```bash
pytest                      # unit tests, synthetic PDFs and the public-domain book; about 30 s
pytest -m integration       # needs Docker Qdrant and ingested books
pytest -m agent             # the live agent; 1-2 minutes per question
pytest -m e2e -s            # upload / reject / ask scenarios against a running API and worker
```

The UI has its own unit tests (`npm test` in `ui/`, see above): the API client's token refresh,
the websocket reconnects, the live-progress reducers, the upload form's error display, the answer
settings, the admin's waiting-work card, task logs and question outcomes.

Copyrighted books are never committed. To test with one locally, put it in `test-books/`
(git-ignored); for example, `test-books/harry-potter-1.pdf` enables the Harry Potter tests.
