"""Run the API, or manage admins.

    python -m marginalia.api [--host 127.0.0.1] [--port 8010]
    python -m marginalia.api create-admin EMAIL [--password-stdin]
"""

import argparse
import asyncio
import getpass
import os
import sys


async def create_admin(email, password):
    from marginalia.api.admins import ensure_admin
    from marginalia.api.db import Database
    from marginalia.config import Settings

    db = Database(Settings().database_url)
    try:
        outcome = await ensure_admin(db, email, password or None)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    finally:
        await db.dispose()
    print(f'admin {email.strip().lower()}: {outcome}')


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd')
    a = sub.add_parser('create-admin')
    a.add_argument('email')
    a.add_argument('--password-stdin', action='store_true', help='read the password from stdin')
    p.add_argument('--host', default='127.0.0.1')
    p.add_argument('--port', type=int, default=int(os.environ.get('CP_API_PORT', '8010')))
    args = p.parse_args()

    if args.cmd == 'create-admin':
        if args.password_stdin:
            password = sys.stdin.readline().strip()
        else:
            password = getpass.getpass('Password (empty to keep the existing one): ')
        if password and len(password) < 8:
            raise SystemExit('passwords need at least 8 characters')
        asyncio.run(create_admin(args.email, password))
        return

    import uvicorn
    from marginalia import logs
    from marginalia.api.app import create_app
    from marginalia.config import Settings

    logs.setup('api')
    settings = Settings()
    # Our own access log (marginalia.api.app) replaces uvicorn's; forwarded headers are trusted only
    # from the proxies in CP_FORWARDED_ALLOW_IPS.
    uvicorn.run(create_app(), host=args.host, port=args.port, proxy_headers=True,
                forwarded_allow_ips=settings.forwarded_allow_ips, log_config=None, access_log=False,
                timeout_graceful_shutdown=20, server_header=False)


if __name__ == '__main__':
    main()
