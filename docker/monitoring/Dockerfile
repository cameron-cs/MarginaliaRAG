# LLM monitoring: the Evidently UI (`evidently ui`) and the hourly report job (`python -m marginalia.monitoring`).
# Evidently brings pandas, scipy, plotly and more, so it has an image of its own rather than the app's.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DO_NOT_TRACK=1

WORKDIR /app
COPY docker/monitoring/requirements.txt ./
RUN pip install -r requirements.txt
# The report job needs only marginalia's settings and monitoring code, so the package goes in
# without its own (large) dependencies.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-deps .

RUN useradd --create-home --uid 1000 marginalia && mkdir -p /workspace && chown marginalia /workspace
USER marginalia
VOLUME ["/workspace"]
CMD ["evidently", "ui", "--workspace", "/workspace", "--host", "0.0.0.0", "--port", "8000"]
