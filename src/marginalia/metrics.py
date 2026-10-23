"""Prometheus metrics for the API and the worker.

Each process serves its own metrics on a separate port (CP_METRICS_PORT; 0 = off), inside the
Docker network only: never on the public API port. Prometheus scrapes them (docker/observability).

    API     marginalia_http_*                       requests by route template, method, status; latency
            marginalia_queue_length, marginalia_books, marginalia_users      refreshed from Redis and Postgres
    worker  marginalia_questions_total{outcome}     agent | cache | refused | failed
            marginalia_llm_tokens_total{role,kind,model}  prompt and completion tokens (agent, grader)
            marginalia_llm_tokens_per_answer, marginalia_llm_tokens_per_second{role}
            marginalia_questions_refused_total{reason}, marginalia_answers_checked_total{verdict}
            marginalia_answer_seconds, marginalia_screen_seconds, marginalia_agent_tool_calls
            marginalia_jobs_total{queue,outcome}, marginalia_job_seconds{queue}

The batch runs as Airflow tasks: Airflow's own StatsD metrics (run and task outcomes and
durations) reach Prometheus through the StatsD exporter.
"""

import logging

from prometheus_client import Counter, Gauge, Histogram, start_http_server

log = logging.getLogger(__name__)

HTTP_REQUESTS = Counter('marginalia_http_requests_total', 'API requests', ['method', 'route', 'status'])
HTTP_SECONDS = Histogram('marginalia_http_request_duration_seconds', 'API request time', ['method', 'route'],
                         buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10))

QUESTIONS = Counter('marginalia_questions_total', 'Questions by how they were settled', ['outcome'])
REFUSALS = Counter('marginalia_questions_refused_total', 'Questions the guard refused', ['reason'])
CHECKED = Counter('marginalia_answers_checked_total', "Agent answers by the grader's verdict", ['verdict'])
ANSWER_SECONDS = Histogram('marginalia_answer_seconds', 'Time the agent took to answer a question',
                           buckets=(5, 10, 20, 30, 45, 60, 90, 120, 180, 240, 300, 450, 600))
SCREEN_SECONDS = Histogram('marginalia_screen_seconds', 'Time to screen a question (guard and cache)',
                           buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30))
TOOL_CALLS = Histogram('marginalia_agent_tool_calls', 'Tool calls per answer', buckets=(1, 2, 3, 4, 5, 6, 8, 10))
TOKENS = Counter('marginalia_llm_tokens_total', 'LLM tokens', ['role', 'kind', 'model'])
LLM_CALLS = Counter('marginalia_llm_calls_total', 'LLM calls', ['role', 'model'])
TOKENS_PER_ANSWER = Histogram('marginalia_llm_tokens_per_answer', 'Tokens (agent and grader) behind one answer',
                              buckets=(500, 1000, 2000, 4000, 8000, 12000, 16000, 24000, 32000, 48000, 64000))
TOKENS_PER_SECOND = Histogram('marginalia_llm_tokens_per_second', 'Generation speed for one answer', ['role'],
                              buckets=(5, 10, 20, 30, 40, 50, 60, 80, 100, 150, 200))
JOBS = Counter('marginalia_jobs_total', 'Worker jobs', ['queue', 'outcome'])
JOB_SECONDS = Histogram('marginalia_job_seconds', 'Worker job time', ['queue'],
                        buckets=(0.1, 0.5, 1, 5, 10, 30, 60, 120, 300, 600))

QUEUE = Gauge('marginalia_queue_length', 'Jobs in a worker queue', ['queue', 'state'])
BOOKS = Gauge('marginalia_books', 'Library books by status', ['status'])
USERS = Gauge('marginalia_users', 'Accounts', ['state'])


def _zero():
    """Create the known label combinations at 0, so a series exists from start-up (and after a
    restart) rather than appearing at its first event: rates and sums then have something to work on."""
    for outcome in ('agent', 'cache', 'refused', 'failed'):
        QUESTIONS.labels(outcome)
    for reason in ('injection', 'not_in_book', 'off_topic'):
        REFUSALS.labels(reason)
    for verdict in ('supported', 'unsupported', 'unchecked'):
        CHECKED.labels(verdict)
    for queue in ('ingest', 'screen', 'ask'):
        for outcome in ('ok', 'error'):
            JOBS.labels(queue, outcome)


def serve(port, service):
    """Serve this process's metrics on `port` (0: off)."""
    if not port:
        return False
    _zero()
    start_http_server(port, addr='0.0.0.0')
    log.info('%s metrics on :%s/metrics', service, port)
    return True


def record_question(outcome, reason=None):
    QUESTIONS.labels(outcome).inc()
    if reason:
        REFUSALS.labels(reason).inc()


def record_tokens(usage, model):
    """An answer's token usage (marginalia.agent.usage summary)."""
    if not usage:
        return
    for role, t in usage.items():
        if role == 'total':
            continue
        TOKENS.labels(role, 'prompt', model).inc(t['prompt'])
        TOKENS.labels(role, 'completion', model).inc(t['completion'])
        LLM_CALLS.labels(role, model).inc(t['calls'])
        if t.get('per_second'):
            TOKENS_PER_SECOND.labels(role).observe(t['per_second'])
    TOKENS_PER_ANSWER.observe(usage['total']['total'])


def record_answer(result, seconds):
    """An answer the agent finished (`result`: marginalia.agent.react_agent.AgentResult)."""
    ANSWER_SECONDS.observe(seconds)
    TOOL_CALLS.observe(len(result.tool_calls))
    CHECKED.labels({True: 'supported', False: 'unsupported'}.get(result.supported, 'unchecked')).inc()
