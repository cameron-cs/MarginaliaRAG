"""Book-independent page cleanup: running headers and footers, control characters, and turning
wrapped PDF lines back into paragraphs.

PDF extractors give one text line per printed line, and books mark paragraph ends differently.
`detect_wrap_style` measures which convention a book uses:
  trailing_space  wrapped lines end with a space; a paragraph's last line does not
                  (Crime and Punishment, Planet eBook edition)
  indent          a new paragraph starts with tabs or spaces (Harry Potter, calibre export)
  blank_line      paragraphs are separated by an empty line
  heuristic       none of the above: a short line ending a sentence ends the paragraph
"""

import re
import statistics
from collections import Counter

from marginalia.ingest.structure import is_heading_line

_CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
# Arabic or lower-case Roman (front matter). Upper-case Roman lines are chapter numerals ("II").
_PAGE_NUMBER = re.compile(r'^\s*(?:\d{1,4}|[ivxlc]{1,7})\s*$')
_TERMINAL = re.compile(r'[.!?…:;”’"\')\]]\s*$')


def normalize_page(text):
    return _CONTROL.sub('', text.replace('\r\n', '\n').replace('\r', '\n'))


def _key(line):
    """Lines that differ only in page numbers or spacing are the same running header."""
    return re.sub(r'\d+', '#', ' '.join(line.split())).casefold()


def find_running_lines(pages, edge=2, min_share=0.05, min_count=3):
    """Keys of lines repeated at the top or bottom of many pages: headers, footers, page labels.

    Heading-like lines ("Chapter 3") repeat at page tops too, once per chapter; they are never
    treated as running lines.
    """
    counts = Counter()
    for page in pages:
        lines = [l for l in page.split('\n') if l.strip()]
        counts.update({_key(l) for l in lines[:edge] + lines[-edge:] if not is_heading_line(l)})
    threshold = max(min_count, min_share * len(pages))
    # A line ending a sentence is text that happens to fall at a page edge, not a header: removing
    # it would cut words out of the book.
    return {k for k, n in counts.items()
            if n >= threshold and 0 < len(k) <= 120 and k != '#' and not re.search(r'[.!?;,”"’]$', k)}


def strip_running_lines(page, keys, edge=3):
    lines = page.split('\n')
    content = [i for i, l in enumerate(lines) if l.strip()]
    edges = set(content[:edge] + content[-edge:])
    keep = [l for i, l in enumerate(lines)
            if i not in edges or not (_key(l) in keys or _PAGE_NUMBER.match(l))]
    return '\n'.join(keep)


def detect_wrap_style(text):
    lines = text.split('\n')
    content = [l for l in lines if l.strip()]
    if not content:
        return 'heuristic'
    share = lambda pred: sum(1 for l in content if pred(l)) / len(content)
    if share(lambda l: l.endswith((' ', '\t'))) > 0.3:
        return 'trailing_space'
    if share(lambda l: l.startswith(('\t', '  '))) > 0.05:
        return 'indent'
    if sum(1 for l in lines if not l.strip()) / len(lines) > 0.05:
        return 'blank_line'
    return 'heuristic'


def word_counts(text):
    return Counter(w.casefold() for w in re.findall(r"[^\W\d_]+", text))


def to_paragraphs(text, style, vocabulary=None):
    """Join wrapped lines; paragraphs end up separated by one blank line.

    `vocabulary` (word counts for the whole book) decides whether "pre-\\nvaricate" is a word
    split by the typesetter ("prevaricate" occurs elsewhere) or a real compound ("tavern-keeper").
    """
    vocabulary = vocabulary or Counter()
    lines = text.split('\n')
    if style == 'heuristic':
        lengths = [len(l.strip()) for l in lines if len(l.strip()) >= 30]
        full = statistics.median(lengths) if lengths else 0
    paragraphs, current = [], []

    def flush():
        if current:
            paragraphs.append(current[:])
            current.clear()

    for i, raw in enumerate(lines):
        line = raw.replace('\t', ' ')
        if not line.strip():
            if style in ('blank_line', 'heuristic', 'indent'):
                flush()
            continue
        if style == 'indent' and raw.startswith(('\t', '  ')) and current:
            flush()
        current.append(line)
        if style == 'trailing_space' and not raw.endswith((' ', '\t')) and not raw.rstrip().endswith('-'):
            flush()
        elif style == 'heuristic' and full and _TERMINAL.search(line) and len(line.strip()) < 0.8 * full:
            flush()
    flush()
    return '\n\n'.join(_join(p, vocabulary) for p in paragraphs if ''.join(p).strip())


def _join(lines, vocabulary):
    out = ''
    for line in lines:
        line = ' '.join(line.split())
        if not out:
            out = line
        elif out.endswith(' -'):
            # "be -" + "fore": a soft hyphen the typesetter marked with a space.
            out = out[:-2] + line
        elif out.endswith('-') and out[-2:-1].isalpha() and line[:1].isalpha():
            head = re.search(r'([^\W\d_]+)-$', out).group(1)
            tail = re.match(r'[^\W\d_]+', line).group(0)
            joined = (head + tail).casefold()
            hyphenated = f'{head}-{tail}'.casefold()
            # Keep the hyphen unless the unhyphenated word is the more common spelling.
            out = out[:-1] + line if vocabulary[joined] > vocabulary[hyphenated] else out + line
        else:
            out += ' ' + line
    # "blood- stained": a compound whose hyphen ended a line that kept its trailing space.
    return re.sub(r'(\w)- (\w)', r'\1-\2', out)


def fix_drop_cap(text, vocabulary):
    """Rejoin a chapter's enlarged first letter: 'O\\n\\nn an exceptionally' or 'M r. and Mrs.'.

    A lone capital is joined only if the joined word is more common in the book than the
    fragment on its own, so 'A man walked' is left alone ('aman' never occurs).
    """
    m = re.match(r'([A-Z])\s+([a-z][^\W\d_]*)', text)
    if not m:
        return text
    joined = (m.group(1) + m.group(2)).casefold()
    if vocabulary[joined] > vocabulary[m.group(2).casefold()]:
        return m.group(1) + text[m.start(2):]
    return text
