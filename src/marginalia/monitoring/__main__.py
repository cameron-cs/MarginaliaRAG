"""Write Evidently reports on recent answers to the Evidently UI.

    python -m marginalia.monitoring --once                     # one report now
    python -m marginalia.monitoring --every 3600               # one an hour, until stopped
    python -m marginalia.monitoring --once --workspace ./ev    # into a local workspace folder instead

Settings: CP_REPORTING_DATABASE_URL (the read-only reporting login), CP_EVIDENTLY_URL (the UI).
"""

import argparse
import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from marginalia import logs
from marginalia.config import Settings
from marginalia.monitoring import data, report

log = logging.getLogger('marginalia.monitoring')


def _workspace(settings, local=None):
    if local:
        from evidently.ui.workspace import Workspace
        return Workspace.create(local)
    from evidently.ui.workspace import RemoteWorkspace
    return RemoteWorkspace(settings.evidently_url, secret=settings.evidently_secret or None)


def run_once(settings, window_hours=24, reference_days=7, local=None, now=None):
    """One report: the last `window_hours` against the `reference_days` before them."""
    now = now or datetime.now(timezone.utc)
    start = now - timedelta(hours=window_hours)
    current = asyncio.run(data.fetch(settings.reporting_database_url, start, now))
    if not current:
        log.info('no finished questions since %s: no report', start.isoformat(timespec='minutes'))
        return None
    reference = asyncio.run(data.fetch(settings.reporting_database_url, start - timedelta(days=reference_days), start))
    snapshot = report.build(data.to_frame(current), data.to_frame(reference) if reference else None)
    workspace = _workspace(settings, local)
    ref = workspace.add_run(report.project(workspace).id, snapshot)
    log.info('report on %s question(s), %s in the reference week', len(current), len(reference),
             extra={'run': str(getattr(ref, 'id', ref))})
    return ref


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--once', action='store_true', help='one report, then stop')
    p.add_argument('--every', type=int, default=3600, help='seconds between reports (default: an hour)')
    p.add_argument('--window', type=int, default=24, help='hours of questions per report')
    p.add_argument('--reference-days', type=int, default=7)
    p.add_argument('--workspace', help='a local workspace folder instead of the Evidently UI')
    args = p.parse_args()
    logs.setup('monitor')
    settings = Settings()
    while True:
        try:
            run_once(settings, args.window, args.reference_days, args.workspace)
        except Exception:
            log.exception('the report failed; trying again next time')
        if args.once:
            return
        time.sleep(args.every)


if __name__ == '__main__':
    main()
