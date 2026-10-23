#!/bin/sh
# One-shot set-up of the shared Postgres, run on every `docker compose up` (it is idempotent):
#   - Airflow's own database
#   - the read-only login used by Grafana and the Evidently job (marginalia_reporting): it may read
#     the views in the `reporting` schema only (migrations/versions/0004_reporting_views.py)
set -eu
export PGHOST=postgres PGUSER=marginalia PGDATABASE=marginalia PGPASSWORD="$CP_POSTGRES_PASSWORD"

psql -tc "SELECT 1 FROM pg_database WHERE datname = 'airflow'" | grep -q 1 || psql -c 'CREATE DATABASE airflow'

psql -v ON_ERROR_STOP=1 -v pw="$CP_REPORTING_DB_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE marginalia_reporting LOGIN PASSWORD %L', :'pw')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'marginalia_reporting') \gexec
SELECT format('ALTER ROLE marginalia_reporting LOGIN PASSWORD %L', :'pw') \gexec
SQL
echo "database set-up done"
