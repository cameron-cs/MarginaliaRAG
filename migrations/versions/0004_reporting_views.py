"""read-only reporting views for dashboards (Grafana) and LLM monitoring (Evidently)

The `reporting` schema holds views over the questions, one row per question with its outcome,
timing and token usage. The role `marginalia_reporting` may read these views and nothing else
(no password hashes, no tokens): docker/postgres/db-init.sh gives it a password so Grafana and the
Evidently job can log in with it. Postgres only; SQLite (tests) skips this.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-23 14:00:00
"""
from alembic import op


revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None

QUESTIONS_VIEW = """
CREATE OR REPLACE VIEW reporting.questions AS
SELECT q.id,
       q.created_at,
       q.finished_at,
       EXTRACT(EPOCH FROM (q.finished_at - q.created_at))::float          AS seconds,
       q.status,
       q.answered_by,
       u.email                                                            AS reader,
       ub.book_id,
       lb.title                                                           AS book,
       q.text                                                             AS question,
       q.answer,
       json_array_length(COALESCE(q.citations, '[]'::json))               AS citations,
       (q.details ->> 'supported')::boolean                               AS supported,
       q.details ->> 'refused'                                            AS refused_reason,
       q.details -> 'cached' ->> 'kind'                                   AS cache_match,
       json_array_length(COALESCE(q.details -> 'tool_calls', '[]'::json)) AS searches,
       (q.details ->> 'revisions')::int                                   AS revisions,
       (q.details -> 'sampling' ->> 'temperature')::float                 AS temperature,
       (q.details -> 'tokens' -> 'total' ->> 'prompt')::int               AS prompt_tokens,
       (q.details -> 'tokens' -> 'total' ->> 'completion')::int           AS completion_tokens,
       (q.details -> 'tokens' -> 'total' ->> 'total')::int                AS total_tokens,
       (q.details -> 'tokens' -> 'agent' ->> 'total')::int                AS agent_tokens,
       (q.details -> 'tokens' -> 'grader' ->> 'total')::int               AS grader_tokens,
       (q.details -> 'tokens' -> 'total' ->> 'per_second')::float         AS tokens_per_second
FROM questions q
JOIN users u ON u.id = q.user_id
JOIN user_books ub ON ub.id = q.user_book_id
JOIN library_books lb ON lb.book_id = ub.book_id
"""


def upgrade():
    if op.get_bind().dialect.name != 'postgresql':
        return
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'marginalia_reporting') THEN
                CREATE ROLE marginalia_reporting NOLOGIN;
            END IF;
        END $$""")
    op.execute('CREATE SCHEMA IF NOT EXISTS reporting')
    op.execute(QUESTIONS_VIEW)
    op.execute('GRANT USAGE ON SCHEMA reporting TO marginalia_reporting')
    op.execute('GRANT SELECT ON ALL TABLES IN SCHEMA reporting TO marginalia_reporting')


def downgrade():
    if op.get_bind().dialect.name != 'postgresql':
        return
    op.execute('DROP SCHEMA IF EXISTS reporting CASCADE')
