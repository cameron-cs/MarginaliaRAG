#!/bin/sh
set -e
case "$1" in
  api)
    alembic upgrade head
    exec python -m marginalia.api --host 0.0.0.0 --port 8010
    ;;
  worker)
    exec python -m marginalia.worker
    ;;
  *)
    exec "$@"
    ;;
esac
