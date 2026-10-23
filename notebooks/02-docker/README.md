# 02 · Docker: the stack

Notebook: [02-docker.ipynb](02-docker.ipynb) · Files: `docker-compose.yml`, `Dockerfile`, `docker/`

## What it does

Runs every part of MarginaliaRAG in containers, from one file: the vector database (Qdrant), the
language model server (Ollama), Postgres, Redis, the API with the web UI, the worker, Airflow for the
hourly batches, and the observability tools (Prometheus, Grafana, Loki, Alloy, Evidently). Profiles
choose which parts start.

## Why?

The system is a dozen programs that must find each other, share data and start in the right order.
Installing them by hand on each machine is slow and drifts: a different Postgres, a missing model, a
library at another version. Docker makes the whole stack one command, the same on every machine, and
keeps the data in named volumes that survive rebuilds.

## For what

- Starting everything for development, or only the parts you need.
- Reproducible images: dependencies are pinned (`constraints.txt`) and installed in their own layer,
  so a code change rebuilds in about a minute.
- Keeping data (books, indexes, models, accounts) across restarts and upgrades.

## Simple explanation

Each container is a small, sealed computer that runs one program. `docker-compose.yml` is the plan of
a building: which rooms exist, which doors connect them (the network), which cupboards keep things
(volumes), and which rooms open only on some days (profiles).

## Examples

```bash
docker compose up -d                                                       # Qdrant, Ollama, Postgres, Redis
docker compose --profile app up -d --build                                 # + API and web UI, worker
docker compose --profile app --profile airflow up -d                       # + hourly batches
docker compose --profile app --profile airflow --profile observability up -d   # + dashboards and monitoring
docker compose ps
docker compose logs -f worker
```

| Address | Service |
|---|---|
| http://127.0.0.1:8010 | web UI and API (`/docs` in development) |
| http://127.0.0.1:8090 | Airflow |
| http://127.0.0.1:3030 | Grafana |
| http://127.0.0.1:8085 | Evidently (LLM monitoring) |
| http://127.0.0.1:9095 | Prometheus |

## Technical

| Profile | Services |
|---|---|
| (always) | `qdrant`, `ollama` (this project's own, on 11435), `postgres`, `redis` |
| `app` | `api` (migrations at start, then the API and the built UI), `worker` (questions, deletions) |
| `airflow` | `db-init`, `airflow-init`, `airflow-apiserver`, `airflow-scheduler`, `airflow-dag-processor`, `statsd-exporter` |
| `observability` | `db-init`, `prometheus`, `loki`, `alloy`, `grafana`, `evidently`, `monitor` |

- **Images:** `marginalia:dev` is a multi-stage build. A Node stage builds the UI, and a Python stage
  installs the pinned dependencies first, then the code. It runs as uid 1000. `marginalia-airflow:dev`
  is Airflow with MarginaliaRAG in its own virtualenv (`/opt/marginalia`), so the two sets of
  dependencies never meet. `marginalia-monitoring:dev` holds Evidently.
- **Ports:** they are bound to `127.0.0.1` only. Metrics ports (9100) are never published.
- **Settings:** all come from `.env` (template: `.env.example`), read by compose and by the Python code
  alike. A test checks that every compose variable is documented there.
- **Volumes:** these are named volumes: `postgres_data`, `qdrant_data`, `ollama_models`,
  `app_models` (e5, BM25, the injection classifier), `airflow_logs`, `prometheus_data`, `loki_data`,
  `grafana_data`, `evidently_workspace`. The library (`./library`) is a bind mount shared by the API,
  the worker and Airflow; Airflow runs as `1000:0` so it can write the same folders.
- **Health:** each service has a healthcheck that `depends_on` waits for. `db-init` is a one-shot
  service that creates Airflow's database and the read-only reporting login.

## Tech examples

```bash
docker compose --profile app build api                      # rebuild after a code change
docker exec -it marginalia-api python -m marginalia.api create-admin you@example.com
docker exec -it marginalia-ollama ollama pull qwen3:4b
docker compose --profile app --profile airflow --profile observability config -q   # validate
```

## How it is connected to other steps

Every other step runs inside one of these containers:

- **Steps 01–07** (ingestion and indexing): Airflow tasks.
- **Steps 08–10** (MCP server, agent, guard and cache): the worker.
- **Step 12** (backend): the API.
- **Steps 14–15** (observability, monitoring): Prometheus, Grafana, Loki, Alloy and Evidently.

The step notebooks run on the host and talk to these containers over the `127.0.0.1` ports.
