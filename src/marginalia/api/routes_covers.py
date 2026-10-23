"""Book covers, as signed links an <img> can load without an Authorization header.

    GET /api/covers/{book_id}/{file}?v=&exp=&sig=

Book responses carry each cover's link (`cover_url`). The signature covers the book, the file,
its version and the expiry, which falls one to two days ahead and stays the same all day, so
the browser caches the image and a link cannot be turned to another book or kept for good.
"""

import hmac
import time

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse

from marginalia.api.deps import Services, services

router = APIRouter(prefix='/covers', tags=['covers'])

DAY = 86400
MEDIA_TYPES = {'.jpg': 'image/jpeg', '.png': 'image/png', '.svg': 'image/svg+xml'}


def _message(book_id, file, version, expires):
    return f'cover:{book_id}:{file}:{version}:{expires}'


def cover_url(tokens, row, now=None):
    """The signed link to a library book's cover, or None if it has none yet."""
    if not row.cover:
        return None
    expires = int((now or time.time()) // DAY * DAY) + 2 * DAY
    version = int(row.updated_at.timestamp()) if row.updated_at else 0
    sig = tokens.sign(_message(row.book_id, row.cover, version, expires))
    return f'/api/covers/{row.book_id}/{row.cover}?v={version}&exp={expires}&sig={sig}'


@router.get('/{book_id}/{file}', include_in_schema=False)
async def cover(book_id: str, file: str, v: int, exp: int, sig: str, svc: Services = Depends(services)):
    expected = svc.tokens.sign(_message(book_id, file, v, exp))
    if exp < time.time() or not hmac.compare_digest(sig, expected):
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'Not found')
    try:
        path = svc.library.cover_path(book_id)
    except KeyError:                       # UnknownBook
        path = None
    if path is None or path.name != file or path.suffix not in MEDIA_TYPES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, 'Not found')
    # An SVG opened on its own must not run anything (ours never contain scripts anyway).
    return FileResponse(path, media_type=MEDIA_TYPES[path.suffix],
                        headers={'Cache-Control': 'private, max-age=86400',
                                 'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'",
                                 'X-Content-Type-Options': 'nosniff'})
