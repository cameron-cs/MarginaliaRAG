"""Dialogue quotes in whatever style a book prints them.

Books (and PDF exports of them) differ: “double curly”, ‘single curly’, "straight", ``LaTeX'',
«guillemets», „low-high“, or a dash opening each spoken line. `detect_quote_style` counts the
openers and picks the dominant style; `extract_quotes` then pulls spans in that style.
"""

import re

from marginalia.ingest.chunking import Span, chunk_text

# Closers that double as apostrophes ("don’t", "fellows’") only close a quote when followed
# by whitespace, punctuation or the end of the text.
_AFTER_CLOSE = r'(?=[\s,.;:!?…—–)\]]|$)'

STYLES = {
    'double_curly': ('“', '”'),
    'single_curly': ('‘', '’'),
    'double_straight': ('"', '"'),
    'latex': ('``', "''"),
    'guillemets': ('«', '»'),
    'low_high': ('„', '“'),
    'single_straight': ("'", "'"),
}
MIN_OPENERS = 20


def _count(text, style):
    opener, closer = STYLES[style]
    if opener == closer:
        # A straight quote opens when it follows whitespace or a line start and precedes a letter.
        return len(re.findall(rf'(?:^|(?<=\s)){re.escape(opener)}(?=\w)', text, re.M))
    return text.count(opener)


def detect_quote_style(text):
    counts = {style: _count(text, style) for style in STYLES}
    # Curly double quotes contain the low-high closer; only count low-high when „ is present.
    dash = len(re.findall(r'(?m)^\s*[—–]\s*\S', text))
    counts['dash'] = dash
    style, n = max(counts.items(), key=lambda kv: kv[1])
    return style if n >= MIN_OPENERS else 'none'


def _pattern(style):
    opener, closer = STYLES[style]
    o, c = re.escape(opener), re.escape(closer)
    if style in ('single_curly', 'single_straight', 'double_straight'):
        start = r'(?:^|(?<=[\s(\[—–]))' if opener == closer else ''
        return re.compile(rf'{start}{o}(.+?){c}{_AFTER_CLOSE}', re.S)
    return re.compile(rf'{o}(.+?){c}', re.S)


def extract_quotes(text, style, min_chars=50, max_chars=1500):
    """Spoken spans of at least `min_chars`, with offsets into `text`; long speeches are chunked."""
    if style == 'none':
        return []
    if style == 'dash':
        matches = [(m.start(1), m.group(1)) for m in re.finditer(r'(?m)^\s*[—–]\s*(.+?)(?=\s[—–]\s|$)', text)]
    else:
        matches = [(m.start(1), m.group(1)) for m in _pattern(style).finditer(text)]
    quotes = []
    for start, body in matches:
        lead = len(body) - len(body.lstrip())
        body = body.strip()
        start += lead
        if len(body) < min_chars:
            continue
        if len(body) <= max_chars:
            quotes.append(Span(body, start, start + len(body)))
        else:
            quotes += [Span(c.text, start + c.start, start + c.end)
                       for c in chunk_text(body, target=1000, overlap=150, max_chars=max_chars)]
    return quotes
