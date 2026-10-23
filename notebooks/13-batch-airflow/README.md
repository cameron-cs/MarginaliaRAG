# 13 · Batches with Airflow

Notebook: [13-batch-airflow.ipynb](13-batch-airflow.ipynb) · Code: `dags/marginalia_ingest.py`,
`src/marginalia/batch/`, `src/marginalia/ingest/pipeline.py`, `src/marginalia/ingest/cover.py`

## What it does

Once an hour, an Airflow DAG processes every book readers uploaded since the last run, together:

- **Plan:** what is waiting.
- **Review:** each new reader's prompts are reviewed by the guard model.
- **Process:** every new or changed book goes through four steps: find the chapters, find a cover,
  summarize each chapter, index for search.
- **Finalize:** write a persona for readers who gave none, and tell every reader the outcome.

An admin can run a batch now, for everything or just chosen books, stop or retry single tasks, and
read each task's log from the admin page.

## Why?

Processing a book takes minutes of CPU and GPU, and the GPU is shared with answering questions.
Doing it on upload would let one reader's upload slow everyone's answers and make failures hard to
see and retry. A batch puts heavy work in one predictable slot. It runs CPU and GPU work in pools
with limits, so summaries never fight the agent for the GPU, and it gives admins Airflow's view of
every run and task.

Every step is idempotent: it stores a key, a hash of its inputs and code version, and is skipped
while that key is unchanged. Re-running a batch only does new work. A code change, such as a new
chunking version, re-processes exactly the books it affects, on its own.

## For what

- Processing uploads from many readers without disturbing the answering.
- Re-indexing books when the pipeline changes (a new index version, a new cover step).
- A place where failures are visible and retryable task by task.

## Simple explanation

A print shop that collects the day's orders and runs them in one go each hour, instead of starting
the presses for every customer. Each order goes through the same stations; a station skips any order
whose work it already did. The shop's board shows every run and every station, and the manager can
re-run one station for one order.

## Examples

| Task | Pool | What it does |
|---|---|---|
| `plan` | — | lists prompt reviews and books needing steps (`--only book_id` narrows it) |
| `review[*]` | GPU | the guard model judges a reader's prompts for relevance and safety |
| `process[*].structure` | CPU | parts and chapters (step 03) |
| `process[*].cover` | CPU | the PDF's cover page, else Open Library, else a generated SVG |
| `process[*].summarize` | GPU | one summary per chapter (step 04) |
| `process[*].index` | CPU | chunks, embeddings, a new index version (steps 04–06) |
| `finalize` | GPU | a persona per book for readers without prompts; "Ready: 17 chapters. Ask away." |

A ready book that only needs a newer step (say a cover) keeps answering. Its readers get no progress
messages and no second "ready".

![The admin's Pipeline page: schedule, waiting work, batches and their tasks](../../docs/screenshots/admin-pipeline.png)
*The admin's Pipeline page: schedule, waiting work, batches and their tasks.*

![A task's log, read from Airflow](../../docs/screenshots/admin-task-log.png)
*A task's log, read from Airflow.*

![The marginalia_ingest DAG in Airflow](../../docs/screenshots/airflow-dag.png)
*The marginalia_ingest DAG in Airflow.*

## Technical

- **The DAG:** Airflow 3.3 with LocalExecutor. Its tasks are `BashOperator` / `@task.bash` running
  `/opt/marginalia/bin/python -m marginalia.batch ...`, from MarginaliaRAG's own virtualenv in the
  Airflow image. `process` is a task group mapped over the plan's books, and `review` is mapped over
  its reviews.
  - **Pools:** `marginalia_gpu` (1 slot) and `marginalia_cpu` (2).
  - **Failures:** `structure` uses `trigger_rule='all_done'`, and so does `finalize`, which tells
    readers about whatever finished.
  - **Params:** `force` (redo from scratch) and `only` (process just these books).
- **The plan:** `BatchWork.waiting(force, only)` lists each book with the `steps` it still needs, and
  `plan` adds an event for admins. The plan's JSON is the task's XCom; Airflow reads the last stdout
  line.
- **Steps:** `Pipeline.run(book_id, steps)` checks each step's key (`keys(profile)`). On failure it
  records the error on the book; a `structure` failure means the book is rejected (`no_chapters`),
  any other means failed.
- **Covers:** `make_cover(source_pdf, title, author)` tries three sources in order:
  - a text-free portrait image on page 1 of the PDF;
  - Open Library's search and cover APIs (no personal data in requests; `CP_COVERS_ONLINE`);
  - a typographic SVG in the UI's colours.
- **The API's view:** `marginalia.api.airflow.Airflow` uses REST API v2. It covers runs (by state, with
  durations), triggers with `force`/`only`, stopping runs and tasks, `clearTaskInstances` for retry,
  task logs, and the plan's XCom, so mapped tasks can be labelled with their books.
- **Sign-in:** Airflow's UI signs its sessions with `CP_AIRFLOW_SECRET_KEY`, which must stay fixed or
  open pages fail after a restart.

## Tech examples

```bash
python -m marginalia.batch plan --only crime-and-punishment-ddd22f99
python -m marginalia.batch step --book crime-and-punishment-ddd22f99 --step cover --force
python -m marginalia ingest --all            # the same steps, without Airflow, on the host
```

```python
from marginalia.ingest.pipeline import Pipeline
Pipeline().run('crime-and-punishment-ddd22f99')   # ['cover'] if only the cover was missing
```

## How it is connected to other steps

- **Runs:** steps 01, 03, 04, 05 and 06 for real books.
- **Reads:** the uploads made through the API (step 12).
- **Tells readers:** through the same events the web UI shows (step 16).
- **Controlled from:** the admin page's Pipeline tab.
- **Observed by:** Airflow's StatsD metrics, which feed the "batches" dashboard (step 14).
