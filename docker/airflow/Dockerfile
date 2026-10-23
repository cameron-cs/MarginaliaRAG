# Airflow with marginalia in a separate virtualenv: tasks run `/opt/marginalia/bin/python -m marginalia.batch`,
# so Airflow's pinned dependencies and marginalia's never have to agree.
FROM apache/airflow:3.3.2-python3.12

USER root
# Dependencies first, pinned by constraints.txt, so a code change does not reinstall them.
COPY pyproject.toml README.md constraints.txt /tmp/marginalia/
RUN python -m venv /opt/marginalia \
    && mkdir -p /tmp/marginalia/src/marginalia && touch /tmp/marginalia/src/marginalia/__init__.py \
    && /opt/marginalia/bin/pip install --no-cache-dir --disable-pip-version-check \
        -c /tmp/marginalia/constraints.txt /tmp/marginalia \
    && /opt/marginalia/bin/pip uninstall -y marginalia
COPY src /tmp/marginalia/src
RUN /opt/marginalia/bin/pip install --no-cache-dir --no-deps /tmp/marginalia \
    && rm -rf /tmp/marginalia \
    && mkdir -p /data/library /data/models \
    && chown -R airflow /opt/marginalia /data
USER airflow
ENV CP_LIBRARY_DIR=/data/library FASTEMBED_CACHE_PATH=/data/models HF_HOME=/data/models/hf
