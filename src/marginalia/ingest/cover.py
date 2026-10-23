"""A cover for every book, from the best source available:

    pdf          the PDF's own first page, when it is a picture with (almost) no text: a
                 portrait image of reasonable size. A publisher's banner page (landscape, with
                 text) is not taken for a cover.
    openlibrary  Open Library's cover for the title and author (covers.openlibrary.org), when
                 lookups are allowed (CP_COVERS_ONLINE). Requests carry no personal data.
    generated    a typographic SVG made here: the title and author on a gradient whose colours
                 come from the title, the same as the web UI's placeholder covers.
"""

import difflib
import html
import io
import logging
import re

log = logging.getLogger(__name__)

COVER_VERSION = 1
MAX_HEIGHT = 900


def pdf_cover(path):
    """JPEG bytes of the PDF's cover page, or None."""
    from pypdf import PdfReader
    try:
        page = PdfReader(path).pages[0]
        if len((page.extract_text() or '').strip()) > 40:
            return None
        images = list(page.images)
        if not images:
            return None
        image = max(images, key=lambda im: len(im.data)).image
    except Exception:                      # unreadable images or pages: no cover from the PDF
        log.debug('no usable cover image in %s', path, exc_info=True)
        return None
    width, height = image.size
    if width < 250 or not 1.2 <= height / width <= 1.8:
        return None
    return _jpeg(image)


def _jpeg(image):
    from PIL import Image
    image = image.convert('RGB')
    if image.height > MAX_HEIGHT:
        image = image.resize((round(image.width * MAX_HEIGHT / image.height), MAX_HEIGHT), Image.LANCZOS)
    out = io.BytesIO()
    image.save(out, 'JPEG', quality=88, optimize=True)
    return out.getvalue()


def _norm(text):
    return re.sub(r'[^a-z0-9]+', ' ', (text or '').casefold()).strip()


def title_candidates(title):
    """'Harry Potter 1 - Harry Potter and the Sorcerer's Stone' -> the whole title, then the part
    after a series prefix, then the part before a subtitle."""
    out = [title]
    if ' - ' in title:
        out.append(title.split(' - ', 1)[1])
    if ':' in title:
        out.append(title.split(':', 1)[0])
    return [t.strip() for t in dict.fromkeys(out) if t.strip()]


def open_library_cover(title, author, user_agent, http=None, timeout=10):
    """JPEG bytes of Open Library's cover for this book, or None."""
    import httpx
    client = http or httpx.Client(timeout=timeout, follow_redirects=True, headers={'User-Agent': user_agent})
    surname = max(re.findall(r'[^\W\d_]+', author or ''), key=len, default='')
    try:
        for candidate in title_candidates(title):
            params = {'title': candidate, 'limit': 5, 'fields': 'title,author_name,cover_i'}
            if surname:
                params['author'] = surname
            docs = client.get('https://openlibrary.org/search.json', params=params).raise_for_status().json()
            for doc in docs.get('docs', []):
                if not doc.get('cover_i'):
                    continue
                if difflib.SequenceMatcher(None, _norm(doc.get('title')), _norm(candidate)).ratio() < 0.85:
                    continue
                res = client.get(f"https://covers.openlibrary.org/b/id/{doc['cover_i']}-L.jpg",
                                 params={'default': 'false'})
                if res.status_code == 200 and res.headers.get('content-type', '').startswith('image/') \
                        and len(res.content) > 2000:
                    from PIL import Image
                    return _jpeg(Image.open(io.BytesIO(res.content)))
    except Exception as exc:               # offline, rate-limited, a bad image: fall back
        log.warning('Open Library cover lookup for %r failed: %s', title, exc)
    finally:
        if http is None:
            client.close()
    return None


def cover_colors(title):
    """The web UI's coverColors(): FNV-1a of the title -> two HSL colours."""
    h = 2166136261
    for ch in title:
        h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
    hue = h % 360
    return f'hsl({hue}, 62%, 46%)', f'hsl({(hue + 40) % 360}, 70%, 30%)'


def _wrap(text, width):
    lines, line = [], ''
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = f'{line} {word}'.strip()
    return lines + ([line] if line else [])


def generated_cover(title, author=None):
    """An SVG cover, 600x900."""
    top, bottom = cover_colors(title)
    name = title_candidates(title)[1] if ' - ' in title else title
    size = 64 if len(name) <= 24 else 52 if len(name) <= 48 else 42
    lines = _wrap(name, max(8, round(560 / (size * 0.55))))[:6]
    text = ''.join(f'<tspan x="60" dy="{0 if i == 0 else round(size * 1.15)}">{html.escape(line)}</tspan>'
                   for i, line in enumerate(lines))
    by = (f'<text x="60" y="820" font-family="Helvetica, Arial, sans-serif" font-size="24" letter-spacing="4" '
          f'fill="rgba(255,255,255,0.85)">{html.escape((author or "").upper())}</text>') if author else ''
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="600" height="900" viewBox="0 0 600 900">'
            f'<defs><linearGradient id="g" x1="0" y1="0" x2="0.6" y2="1">'
            f'<stop offset="0" stop-color="{top}"/><stop offset="1" stop-color="{bottom}"/></linearGradient></defs>'
            f'<rect width="600" height="900" fill="url(#g)"/>'
            f'<rect x="0" y="0" width="18" height="900" fill="rgba(0,0,0,0.18)"/>'
            f'<rect x="60" y="120" width="80" height="4" fill="rgba(255,255,255,0.8)"/>'
            f'<text x="60" y="{200 + size}" font-family="Georgia, \'Times New Roman\', serif" font-size="{size}" '
            f'font-weight="700" fill="#fff">{text}</text>{by}</svg>').encode()


def make_cover(source_pdf, title, author=None, online=True, user_agent='marginalia', http=None):
    """(bytes, extension, source) from the first source that has a cover."""
    data = pdf_cover(source_pdf)
    if data:
        return data, 'jpg', 'pdf'
    if online:
        data = open_library_cover(title, author, user_agent, http=http)
        if data:
            return data, 'jpg', 'openlibrary'
    return generated_cover(title, author), 'svg', 'generated'
