"""Splitting chapter text into retrieval chunks of whole sentences."""

import re
from dataclasses import dataclass

# A sentence ends at . ! ? or … , optionally followed by closing quotes or brackets, then space;
# a paragraph break always ends one.
_SENTENCE_END = re.compile(r'[.!?…][’”"»\')\]]*\s+|\n\n')


@dataclass
class Span:
    text: str
    start: int             # character offsets into the chapter text
    end: int


def _sentences(text):
    spans, start = [], 0
    for m in _SENTENCE_END.finditer(text):
        if m.end() > start:
            spans.append((start, m.start() + len(m.group().rstrip())))
            start = m.end()
    if start < len(text):
        spans.append((start, len(text)))
    return [(s, e) for s, e in spans if text[s:e].strip()]


def _split_long(text, s, e, max_chars):
    """Cut a sentence longer than max_chars at whitespace."""
    out = []
    while e - s > max_chars:
        cut = text.rfind(' ', s, s + max_chars)
        cut = cut if cut > s else s + max_chars
        out.append((s, cut))
        s = cut + 1
    out.append((s, e))
    return out


def chunk_text(text, target=1000, overlap=150, max_chars=1500):
    """Pack whole sentences into chunks of about `target` characters.

    Consecutive chunks share at least `overlap` characters of trailing
    sentences. Sentences longer than `max_chars` are cut at whitespace so every
    chunk stays inside e5's 512-token window.
    """
    units = []
    for s, e in _sentences(text):
        units += _split_long(text, s, e, max_chars)

    chunks, i = [], 0
    while i < len(units):
        j = i
        while j + 1 < len(units) and units[j + 1][1] - units[i][0] <= target:
            j += 1
        start, end = units[i][0], units[j][1]
        chunks.append(Span(text[start:end], start, end))
        if j == len(units) - 1:
            break
        k = j
        while k > i + 1 and end - units[k][0] < overlap:
            k -= 1
        i = k if k > i and end - units[k][0] >= overlap else j + 1
    return chunks
