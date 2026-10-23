"""Hourly batch: process every book users uploaded since the last run.

    plan                     which prompts await review and which books need pipeline steps
                             (up-to-date books are skipped)
    review[*]                one LLM review per shelf entry with new prompts
    process[*].structure     per book: find parts and chapters        (CPU pool)
    process[*].cover                   the PDF's cover, Open Library's, or a generated one (CPU pool)
    process[*].summarize               summarize each chapter         (GPU pool)
    process[*].index                   embed and index, new version   (CPU pool)
    finalize                 personas for owners without prompts; tell every reader

The commands live in marginalia.batch, installed in its own virtualenv (/opt/marginalia) so Airflow's
dependencies and marginalia's never mix. Trigger with {"force": ["<book_id>", ...]} to redo books, or
{"only": ["<book_id>", ...]} to process just those books instead of everything that is waiting.
Schedule: $CP_INGEST_SCHEDULE (default @hourly).
"""

import json
import os
from datetime import datetime, timedelta

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import dag, task, task_group

# Airflow exports the run id to every task's environment; events carry it to the admin page.
BATCH = '/opt/marginalia/bin/python -m marginalia.batch --run "$AIRFLOW_CTX_DAG_RUN_ID"'


@dag(
    dag_id='marginalia_ingest',
    schedule=os.environ.get('CP_INGEST_SCHEDULE', '@hourly'),
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    params={'force': [], 'only': []},
    default_args={'retries': 2, 'retry_delay': timedelta(minutes=2), 'execution_timeout': timedelta(hours=2)},
    tags=['marginalia'],
    doc_md=__doc__,
)
def marginalia_ingest():
    plan = BashOperator(
        task_id='plan',
        bash_command=BATCH + ' plan {% if params.force %}--force {{ params.force | join(" ") }}{% endif %}'
                             '{% if params.only %} --only {{ params.only | join(" ") }}{% endif %}',
        do_xcom_push=True,          # the plan JSON is the command's last output line
    )

    @task
    def books(plan_json):
        return json.loads(plan_json)['books']

    @task
    def reviews(plan_json):
        return json.loads(plan_json)['reviews']

    @task.bash(pool='marginalia_gpu')
    def review(user_book_id):
        return f'{BATCH} review --user-book {user_book_id}'

    @task_group
    def process(book):
        # Runs even if a review failed: other readers may be waiting for the same book.
        @task.bash(pool='marginalia_cpu', trigger_rule='all_done')
        def structure(book):
            return f"{BATCH} step --book {book['book_id']} --step structure" + (' --force' if book['force'] else '')

        @task.bash(pool='marginalia_cpu')
        def cover(book):
            return f"{BATCH} step --book {book['book_id']} --step cover" + (' --force' if book['force'] else '')

        @task.bash(pool='marginalia_gpu', retries=1)
        def summarize(book):
            return f"{BATCH} step --book {book['book_id']} --step summarize" + (' --force' if book['force'] else '')

        @task.bash(pool='marginalia_cpu')
        def index(book):
            return f"{BATCH} step --book {book['book_id']} --step index" + (' --force' if book['force'] else '')

        structure(book) >> cover(book) >> summarize(book) >> index(book)

    finalize = BashOperator(
        task_id='finalize',
        bash_command=BATCH + ' finalize',
        env={'MARGINALIA_PLAN': '{{ ti.xcom_pull(task_ids="plan") }}'},
        append_env=True,
        pool='marginalia_gpu',          # may write personas with the LLM
        trigger_rule='all_done',     # tell readers about whatever finished, even if a book failed
    )

    reviewed = review.expand(user_book_id=reviews(plan.output))
    processed = process.expand(book=books(plan.output))
    reviewed >> processed >> finalize


marginalia_ingest()
