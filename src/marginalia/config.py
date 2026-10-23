import os
from dataclasses import dataclass, field
from pathlib import Path

# The project home: .env, the library of books and eval data live here. CP_HOME overrides it
# (e.g. in containers); by default it is the repository root.
ROOT = Path(os.environ.get('CP_HOME') or Path(__file__).resolve().parents[2])


def load_dotenv(path=ROOT / '.env'):
    """Read KEY=VALUE lines from .env into os.environ without overriding real environment variables.

    The same file configures docker-compose.yml, so the Ollama settings live in one place.
    """
    path = Path(path)
    if not path.exists():
        return
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"\''))


load_dotenv()


def _env(name, default):
    return os.environ.get(name, default)


# 127.0.0.1, not localhost: on Windows "localhost" tries IPv6 ::1 first, and the
# containers only listen on IPv4, which added ~2s to every Qdrant request.
@dataclass(frozen=True)
class Settings:
    qdrant_url: str = field(default_factory=lambda: _env('CP_QDRANT_URL', 'http://127.0.0.1:6340'))
    # One collection for every book; points carry book_id (a tenant index) and index_version.
    collection: str = field(default_factory=lambda: _env('CP_COLLECTION', 'books'))
    library_dir: str = field(default_factory=lambda: _env('CP_LIBRARY_DIR', str(ROOT / 'library')))
    embed_model: str = field(default_factory=lambda: _env('CP_EMBED_MODEL', 'intfloat/e5-base-v2'))
    # This project's own Ollama (docker-compose.yml); CP_OLLAMA_PORT is its host port.
    ollama_url: str = field(default_factory=lambda: _env(
        'CP_OLLAMA_URL', f"http://127.0.0.1:{_env('CP_OLLAMA_PORT', '11435')}"))
    # Requests Ollama runs at once. Matches the server's CP_OLLAMA_NUM_PARALLEL; the eval runner
    # uses it as its default concurrency, since more would only queue.
    ollama_parallel: int = field(default_factory=lambda: int(_env('CP_OLLAMA_NUM_PARALLEL', '2')))
    summary_model: str = field(default_factory=lambda: _env('CP_SUMMARY_MODEL', 'qwen3:4b'))
    agent_model: str = field(default_factory=lambda: _env('CP_AGENT_MODEL', 'qwen3:4b'))
    agent_num_ctx: int = field(default_factory=lambda: int(_env('CP_AGENT_NUM_CTX', '16384')))
    agent_max_tool_calls: int = field(default_factory=lambda: int(_env('CP_AGENT_MAX_TOOL_CALLS', '6')))
    # Sampling defaults for the agent (readers may override them per question, marginalia.agent.sampling).
    # Qwen's advice for qwen3 in thinking mode: temperature 0.6, top_p 0.95.
    agent_temperature: float = field(default_factory=lambda: float(_env('CP_AGENT_TEMPERATURE', '0.6')))
    agent_top_p: float = field(default_factory=lambda: float(_env('CP_AGENT_TOP_P', '0.95')))
    agent_top_k: int = field(default_factory=lambda: int(_env('CP_AGENT_TOP_K', '40')))

    # --- backend ---------------------------------------------------------------------------
    # development | production. Production refuses to start without a JWT secret.
    environment: str = field(default_factory=lambda: _env('CP_ENV', 'development'))
    database_url: str = field(default_factory=lambda: _env(
        'CP_DATABASE_URL', f"postgresql+asyncpg://marginalia:{_env('CP_POSTGRES_PASSWORD', 'marginalia')}"
                           f"@127.0.0.1:{_env('CP_POSTGRES_PORT', '5440')}/marginalia"))
    redis_url: str = field(default_factory=lambda: _env(
        'CP_REDIS_URL', f"redis://127.0.0.1:{_env('CP_REDIS_PORT', '6390')}/0"))
    jwt_secret: str = field(default_factory=lambda: _env('CP_JWT_SECRET', ''))
    # The admin account the API sets up at start (marginalia.api.admins); empty email: none.
    admin_email: str = field(default_factory=lambda: _env('CP_ADMIN_EMAIL', ''))
    admin_password: str = field(default_factory=lambda: _env('CP_ADMIN_PASSWORD', ''))
    access_token_minutes: int = field(default_factory=lambda: int(_env('CP_ACCESS_TOKEN_MINUTES', '15')))
    refresh_token_days: int = field(default_factory=lambda: int(_env('CP_REFRESH_TOKEN_DAYS', '14')))
    # Refresh-token cookies need HTTPS when this is on; off for http://127.0.0.1 development.
    secure_cookies: bool = field(default_factory=lambda: _env('CP_SECURE_COOKIES', '0') == '1')
    max_upload_mb: int = field(default_factory=lambda: int(_env('CP_MAX_UPLOAD_MB', '100')))
    guard_model: str = field(default_factory=lambda: _env('CP_GUARD_MODEL', 'qwen3:4b'))
    # The judge of the DeepEval metrics (marginalia.evals.deepeval_suite); by default the guard model.
    judge_model: str = field(default_factory=lambda: _env('CP_JUDGE_MODEL', _env('CP_GUARD_MODEL', 'qwen3:4b')))
    # Proxies whose X-Forwarded-* headers are believed (uvicorn's forwarded_allow_ips).
    forwarded_allow_ips: str = field(default_factory=lambda: _env('CP_FORWARDED_ALLOW_IPS', '127.0.0.1'))
    # Prometheus metrics on this port, inside the Docker network (marginalia.metrics); 0 = off.
    metrics_port: int = field(default_factory=lambda: int(_env('CP_METRICS_PORT', '0')))
    # Grafana, linked from the admin page when set.
    grafana_url: str = field(default_factory=lambda: _env('CP_GRAFANA_URL', ''))
    # The interactive API docs (/docs): on in development, off in production unless asked for.
    api_docs: bool = field(default_factory=lambda: _env(
        'CP_API_DOCS', '0' if _env('CP_ENV', 'development') == 'production' else '1') == '1')
    cors_origins: tuple = field(default_factory=lambda: tuple(
        o for o in _env('CP_CORS_ORIGINS', 'http://127.0.0.1:5180,http://localhost:5180').split(',') if o))
    # Airflow, which processes uploaded books in batches; the API uses it for the admin controls.
    airflow_url: str = field(default_factory=lambda: _env(
        'CP_AIRFLOW_URL', f"http://127.0.0.1:{_env('CP_AIRFLOW_PORT', '8090')}"))
    airflow_user: str = field(default_factory=lambda: _env('CP_AIRFLOW_USER', 'admin'))
    airflow_password: str = field(default_factory=lambda: _env('CP_AIRFLOW_PASSWORD', ''))
    # The built web UI (ui/dist, from `npm run build`); served at / when present.
    ui_dir: str = field(default_factory=lambda: _env('CP_UI_DIR', str(ROOT / 'ui' / 'dist')))

    # --- questions: guard and answer cache (marginalia.guard.questions, marginalia.agent.cache) -----
    # The injection classifier; empty turns it off (the other checks still run).
    injection_model: str = field(default_factory=lambda: _env(
        'CP_INJECTION_MODEL', 'protectai/deberta-v3-base-prompt-injection-v2'))
    injection_threshold: float = field(default_factory=lambda: float(_env('CP_INJECTION_THRESHOLD', '0.98')))
    topic_margin: float = field(default_factory=lambda: float(_env('CP_TOPIC_MARGIN', '0.01')))
    book_match: float = field(default_factory=lambda: float(_env('CP_BOOK_MATCH', '0.83')))
    cache_collection: str = field(default_factory=lambda: _env('CP_CACHE_COLLECTION', 'answer_cache'))
    cache_similarity: float = field(default_factory=lambda: float(_env('CP_CACHE_SIMILARITY', '0.97')))

    # --- LLM monitoring (marginalia.monitoring) --------------------------------------------------
    # The read-only reporting login (migrations/versions/0004_reporting_views.py).
    reporting_database_url: str = field(default_factory=lambda: _env(
        'CP_REPORTING_DATABASE_URL', f"postgresql://marginalia_reporting:{_env('CP_REPORTING_DB_PASSWORD', '')}"
                                     f"@127.0.0.1:{_env('CP_POSTGRES_PORT', '5440')}/marginalia"))
    evidently_url: str = field(default_factory=lambda: _env(
        'CP_EVIDENTLY_URL', f"http://127.0.0.1:{_env('CP_EVIDENTLY_PORT', '8085')}"))
    evidently_secret: str = field(default_factory=lambda: _env('CP_EVIDENTLY_SECRET', ''))
    # The Evidently UI, linked from the admin page when set.
    evidently_ui_url: str = field(default_factory=lambda: _env('CP_EVIDENTLY_UI_URL', ''))

    # --- covers (marginalia.ingest.cover) -------------------------------------------------------
    # Look up covers on Open Library when the PDF has none; 0 keeps everything offline.
    covers_online: bool = field(default_factory=lambda: _env('CP_COVERS_ONLINE', '1') == '1')
    covers_user_agent: str = field(default_factory=lambda: _env(
        'CP_COVERS_USER_AGENT', 'marginalia/0.5 (self-hosted book Q&A; covers lookup)'))

    # --- production checks ---------------------------------------------------------------------

    @property
    def production(self):
        return self.environment == 'production'

    def problems(self):
        """(errors, warnings) about running these settings in production; none in development.
        The API refuses to start on errors and logs the warnings."""
        if not self.production:
            return [], []
        errors, warnings = [], []
        if len(self.jwt_secret) < 32:
            errors.append('CP_JWT_SECRET must be set to a random string of at least 32 characters')
        if not self.secure_cookies:
            errors.append('CP_SECURE_COOKIES must be 1: the refresh token must only travel over HTTPS')
        if self.airflow_password in ('change-me', 'admin', 'airflow'):
            errors.append('CP_AIRFLOW_PASSWORD is a default password')
        if self.admin_email and (len(self.admin_password) < 12 or self.admin_password == 'change-me'):
            errors.append('CP_ADMIN_PASSWORD must be at least 12 characters (and not a default)')
        if not self.airflow_password:
            warnings.append('CP_AIRFLOW_PASSWORD is not set: the admin page cannot control batches')
        local = [o for o in self.cors_origins if '127.0.0.1' in o or 'localhost' in o]
        if local:
            warnings.append(f'CP_CORS_ORIGINS allows local development origins: {", ".join(local)}')
        if self.api_docs:
            warnings.append('CP_API_DOCS is on: the API docs are public')
        return errors, warnings
