"""Logging for every marginalia process, set up in one place.

    CP_LOG_LEVEL   DEBUG | INFO (default) | WARNING | ERROR
    CP_LOG_FORMAT  text (default; for a terminal) | json (one object per line; for Loki)

JSON lines carry the time, level, logger, message, the service name and whatever context is
current: the API's request id, or fields passed as `extra=` (question_id, book_id, run_id...).
Logs go to stderr, which keeps stdout free for command output (the batch plan Airflow reads, an
MCP server's protocol stream).
"""

import contextvars
import json
import logging
import os
import sys
from datetime import datetime, timezone

request_id = contextvars.ContextVar('request_id', default=None)

# LogRecord's own attributes; anything else on a record came from `extra=` and is context.
_STANDARD = set(vars(logging.LogRecord('', 0, '', 0, '', (), None))) | {'message', 'asctime', 'taskName'}
# Libraries that log every request at INFO.
_NOISY = ('httpx', 'httpcore', 'huggingface_hub', 'urllib3', 'asyncio', 'mcp', 'fastmcp', 'uvicorn.access')


class JsonFormatter(logging.Formatter):
    def __init__(self, service):
        super().__init__()
        self.service = service

    def format(self, record):
        out = {'ts': datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec='milliseconds'),
               'level': record.levelname.lower(), 'logger': record.name, 'msg': record.getMessage(),
               'service': self.service}
        rid = request_id.get()
        if rid:
            out['request_id'] = rid
        for key, value in vars(record).items():
            if key not in _STANDARD and not key.startswith('_'):
                out[key] = value
        if record.exc_info:
            out['exc'] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    def __init__(self):
        super().__init__('%(asctime)s %(levelname)-7s %(name)s: %(message)s')

    def format(self, record):
        text = super().format(record)
        rid = request_id.get()
        return f'{text} [{rid}]' if rid else text


def setup(service, level=None, fmt=None, stream=None):
    """Configure the root logger for this process (idempotent)."""
    level = (level or os.environ.get('CP_LOG_LEVEL') or 'INFO').upper()
    fmt = (fmt or os.environ.get('CP_LOG_FORMAT') or 'text').lower()
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter(service) if fmt == 'json' else TextFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for name in _NOISY:
        logging.getLogger(name).setLevel(max(logging.WARNING, root.level))
    logging.captureWarnings(True)
    return root
