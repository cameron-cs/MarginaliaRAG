# One image for the API and the worker: `docker compose --profile app up -d --build`.
# The web UI is built in a Node stage and served by the API from /app/ui/dist.
FROM node:24.21.0-alpine AS ui
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY ui ./
RUN npm run build

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CP_HOME=/app \
    CP_LIBRARY_DIR=/data/library \
    FASTEMBED_CACHE_PATH=/data/models \
    HF_HOME=/data/models/hf

WORKDIR /app
# Dependencies first, pinned by constraints.txt, so a code change does not reinstall them.
COPY pyproject.toml README.md constraints.txt ./
RUN mkdir -p src/marginalia && touch src/marginalia/__init__.py \
    && pip install -c constraints.txt . && pip uninstall -y marginalia
COPY src ./src
RUN pip install --no-deps .
COPY alembic.ini ./
COPY migrations ./migrations
COPY evals/golden ./evals/golden
COPY docker/entrypoint.sh /entrypoint.sh
COPY --from=ui /ui/dist ./ui/dist

RUN useradd --create-home --uid 1000 marginalia && mkdir -p /data/library /data/models \
    && chown -R marginalia /data && chmod +x /entrypoint.sh
USER marginalia
VOLUME ["/data"]
ENTRYPOINT ["/entrypoint.sh"]
CMD ["api"]
