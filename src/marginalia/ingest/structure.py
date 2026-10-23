"""Finding a book's parts and chapters.

The cascade, most reliable first:
  1. the PDF's bookmarks (outline), when they describe chapters;
  2. heading lines in the text: "Chapter IV", "CHAPTER ONE", "Chapter 12: The Mirror", "Part II",
     "Book One", "Prologue", "Epilogue", and bare numerals ("II") inside an Epilogue;
  3. otherwise the book is rejected with `no_chapters`.
A candidate structure is accepted only if it is plausible (enough chapters, in page order).

Parts are numbered by position, from 1. Explicit parts ("Part II", "Book One") come from the
book; when chapter numbering restarts without a part heading, a part is inferred. A Prologue or
Epilogue in a book with parts is a part of its own; in a book without parts it is a chapter
(Prologue = chapter 0). A book without parts has a single part 1 with an empty label.
"""

import re
from dataclasses import dataclass, field

_ONES = ['one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve',
         'thirteen', 'fourteen', 'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen']
_TENS = ['twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety']
_UNITS = '|'.join(_ONES[:9])
_WORD_NUM = rf"(?:(?:{'|'.join(_TENS)})(?:[\s-](?:{_UNITS}))?|{'|'.join(sorted(_ONES, key=len, reverse=True))})"
_ROMAN = r'(?-i:(?=[MDCLXVI])M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3}))'
_NUM = rf'(?:\d{{1,3}}|{_ROMAN}|{_WORD_NUM})'
_TAIL = r'(?:\s*[.:\-–—]\s*|\s+)?(?P<title>[^\n]{0,80}?)\s*'

CHAPTER_RE = re.compile(rf'^(?P<word>chapter|chap\.)\s+(?P<num>{_NUM})\b{_TAIL}$', re.I)
PART_RE = re.compile(rf'^(?P<word>part|book|volume)\s+(?P<num>{_NUM})\b{_TAIL}$', re.I)
SPECIAL_RE = re.compile(rf'^(?P<word>prologue|epilogue)\b{_TAIL}$', re.I)
BARE_RE = re.compile(rf'^(?P<num>{_ROMAN}|\d{{1,3}})\.?$')

# Bookmarks for front and back matter, which are not part of the story.
_MATTER = re.compile(r'contents|copyright|preface|foreword|introduction|acknowledg|dedication|about the|'
                     r'also by|title page|^cover$|^notes?$|^index$|glossary|bibliograph|how to share|'
                     r'free ebooks|appendi|afterword|translator', re.I)

MIN_CHAPTERS = 2


def roman_to_int(s):
    values = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}
    return sum(-values[a] if b and values[b] > values[a] else values[a] for a, b in zip(s, list(s[1:]) + [None]))


def parse_number(token):
    """'12' / 'XII' / 'twelve' / 'Twenty-One' -> int, or None."""
    token = token.strip()
    if token.isdigit():
        return int(token)
    if token and re.fullmatch(_ROMAN, token):
        return roman_to_int(token)
    words = re.split(r'[\s-]+', token.casefold())
    if len(words) == 1 and words[0] in _ONES:
        return _ONES.index(words[0]) + 1
    if words and words[0] in _TENS and (len(words) == 1 or words[1] in _ONES[:9]):
        return 20 + 10 * _TENS.index(words[0]) + (_ONES.index(words[1]) + 1 if len(words) == 2 else 0)
    return None


def to_roman(n):
    out = ''
    for value, sym in ((1000, 'M'), (900, 'CM'), (500, 'D'), (400, 'CD'), (100, 'C'), (90, 'XC'), (50, 'L'),
                       (40, 'XL'), (10, 'X'), (9, 'IX'), (5, 'V'), (4, 'IV'), (1, 'I')):
        while n >= value:
            out, n = out + sym, n - value
    return out


@dataclass(frozen=True)
class Heading:
    kind: str                 # chapter | part | special | bare | titled
    number: int | None
    label: str                # normalized: "Chapter IV", "Chapter One", "Part II", "Book One", "Epilogue"
    title: str | None = None


def _numeral_label(word, token):
    token = ' '.join(token.split())
    return f'{word.capitalize()} {token if re.fullmatch(_ROMAN, token) or token.isdigit() else token.title()}'


def classify(line):
    text = ' '.join(line.split())
    if not text or len(text) > 100:
        return None
    for kind, rx in (('chapter', CHAPTER_RE), ('part', PART_RE), ('special', SPECIAL_RE), ('bare', BARE_RE)):
        m = rx.match(text)
        if not m:
            continue
        g = m.groupdict()
        title = (g.get('title') or '').strip() or None
        if kind == 'special':
            return Heading('special', None, m.group('word').capitalize(), title)
        number = parse_number(m.group('num'))
        if number is None:
            continue
        if kind == 'bare':
            return Heading('bare', number, f"Chapter {m.group('num')}")
        word = 'Chapter' if kind == 'chapter' else m.group('word')
        return Heading(kind, number, _numeral_label(word, m.group('num')), title)
    return None


def is_heading_line(line):
    h = classify(line)
    return h is not None and h.kind in ('chapter', 'part', 'special')


@dataclass
class Section:
    """One chapter-level unit of the story, located in the page list."""
    part: int
    part_label: str
    chapter: int
    chapter_label: str        # "Chapter IV", "Chapter One", "Prologue", or a chapter's own title
    title: str | None
    start_page: int
    start_line: int | None    # line of the heading on start_page, if found
    end_page: int = 0         # exclusive

    @property
    def label(self):
        if self.part_label and self.chapter_label != self.part_label:
            return f'{self.part_label}, {self.chapter_label}'
        return self.chapter_label


@dataclass
class Structure:
    method: str               # outline | headings
    sections: list
    parts: list = field(default_factory=list)    # [{'part', 'label', 'chapters'}]


class NoChapters(ValueError):
    code = 'no_chapters'


def find_line(page_text, heading):
    """Line index on the page that prints this heading, matched loosely."""
    target = re.sub(r'\W+', '', heading).casefold()
    for i, line in enumerate(page_text.split('\n')):
        flat = re.sub(r'\W+', '', line).casefold()
        if target and flat and (flat == target or (flat.startswith(target) and len(flat) - len(target) < 60)):
            return i
    return None


class _Builder:
    """Turns a stream of (part key, part label, chapter heading, location) into numbered sections."""

    def __init__(self):
        self.sections, self._keys, self._last = [], {}, {}

    def add(self, key, part_label, h, page, line):
        part = self._keys.setdefault(key, len(self._keys) + 1)
        if h.kind in ('chapter', 'bare') and h.number is not None:
            number = h.number
        elif h.kind == 'special' and h.label == 'Prologue' and not part_label:
            number = 0
        else:
            number = self._last.get(part, 0) + 1
        self._last[part] = number
        label = h.label
        title = h.title
        self.sections.append(Section(part, part_label, number, label, title, page, line))


# --- bookmarks ---------------------------------------------------------------------------------

def from_outline(outline, pages):
    entries = [e for e in outline if 0 <= e.page < len(pages)]
    if len(entries) < MIN_CHAPTERS:
        return None
    # A single root bookmark holding everything else is the book's title.
    if entries[0].depth == 0 and entries[0].has_children and sum(e.depth == 0 for e in entries) == 1:
        entries = entries[1:]
    parents, stack = [], []
    for e in entries:
        while stack and stack[-1].depth >= e.depth:
            stack.pop()
        parents.append(stack[-1] if stack else None)
        stack.append(e)

    story = [(e, p) for e, p in zip(entries, parents)
             if not _MATTER.search(e.title) and not (p and _MATTER.search(p.title))]
    leaves = [(e, p, classify(e.title)) for e, p in story if not e.has_children]
    numbered = [x for x in leaves if x[2] and x[2].kind in ('chapter', 'bare', 'special')]
    if len(numbered) >= 0.6 * len(leaves):
        # Mostly "Chapter N" bookmarks: other leaves are a title page, a map, an illustration.
        chosen = numbered
    else:
        # Chapters known only by their titles ("A Long-expected Party").
        chosen = [(e, p, h if h and h.kind != 'part' else Heading('titled', None, e.title, e.title))
                  for e, p, h in leaves]
    if len(chosen) < MIN_CHAPTERS:
        return None

    has_parts = any(p is not None for _, p, _ in chosen)
    builder, run = _Builder(), 0
    for e, parent, h in chosen:
        if parent is not None:
            ph = classify(parent.title)
            label = ph.label if ph and ph.kind in ('part', 'special') else parent.title
            builder.add(('parent', id(parent)), label, h, e.page, find_line(pages[e.page], e.title))
            run += 1
        elif h.kind == 'special' and has_parts:
            builder.add(('special', e.page), h.label, h, e.page, find_line(pages[e.page], e.title))
            run += 1
        else:
            builder.add(('top', run), '', h, e.page, find_line(pages[e.page], e.title))
    return _close(Structure('outline', builder.sections), pages)


# --- heading lines -----------------------------------------------------------------------------

def from_headings(pages):
    events = [(p, i, h) for p, page in enumerate(pages) for i, line in enumerate(page.split('\n'))
              if (h := classify(line))]
    events = _drop_contents_pages(events, pages)
    has_chapter_word = any(h.kind == 'chapter' for _, _, h in events)
    explicit_parts = any(h.kind == 'part' for _, _, h in events)
    builder = _Builder()
    part_key, part_label, mode, last_number, inferred = ('top', 0), '', 'normal', 0, 0

    for k, (p, i, h) in enumerate(events):
        if h.kind == 'part':
            part_key, part_label, mode, last_number = ('part', k), h.label, 'normal', 0
        elif h.kind == 'special':
            following = next((x[2] for x in events[k + 1:] if x[2].kind != 'bare'), None)
            subs = [x for x in events[k + 1:] if x[2].kind == 'bare']
            numbered_inside = subs and (following is None or (p, i) < subs[0][:2])
            if numbered_inside and not (following and following.kind == 'chapter'):
                # "Epilogue" followed by "I", "II": a part whose chapters are bare numerals.
                part_key, part_label, mode, last_number = ('special', k), h.label, 'special', 0
            elif explicit_parts or inferred:
                builder.add(('special', k), h.label, h, p, i)
            else:
                builder.add(part_key, part_label, h, p, i)
        elif h.kind == 'bare':
            if mode == 'special' or not has_chapter_word:
                builder.add(part_key, part_label, h, p, i)
                last_number = h.number
        else:
            if mode == 'special':
                part_key, part_label, mode = ('after', k), '', 'normal'
            if not explicit_parts and h.number is not None and h.number <= last_number:
                # Numbering restarts with no part heading: a part the PDF does not name.
                inferred += 1
                part_key, part_label = ('inferred', k), f'Part {to_roman(inferred + 1)}'
            builder.add(part_key, part_label, h, p, i)
            last_number = h.number or last_number + 1

    sections = builder.sections
    if inferred:
        # The chapters before the first restart are Part I.
        sections = [s if s.part_label or s.part != 1 else
                    Section(s.part, 'Part I', s.chapter, s.chapter_label, s.title, s.start_page, s.start_line)
                    for s in sections]
    if len(sections) < MIN_CHAPTERS:
        return None
    return _close(Structure('headings', sections), pages)


MIN_SECTION_CHARS = 200


def _drop_contents_pages(events, pages):
    """A table of contents lists headings with (almost) no text between them; those headings are
    not where chapters start. Drops pages holding 3+ headings, then chapter headings followed by
    less than MIN_SECTION_CHARS of text before the next heading."""
    per_page = {}
    for e in events:
        if e[2].kind != 'bare':
            per_page[e[0]] = per_page.get(e[0], 0) + 1
    crowded = {p for p, n in per_page.items() if n >= 3}
    events = [e for e in events if e[0] not in crowded]
    lines = [page.split('\n') for page in pages]

    def text_between(a, b):
        (pa, la), (pb, lb) = a[:2], b[:2]
        if pa == pb:
            chunk = lines[pa][la + 1:lb]
        else:
            chunk = lines[pa][la + 1:] + [l for p in range(pa + 1, pb) for l in lines[p]] + lines[pb][:lb]
        return sum(len(l.strip()) for l in chunk)

    kept = []
    for k, e in enumerate(events):
        nxt = events[k + 1] if k + 1 < len(events) else None
        if e[2].kind == 'chapter' and nxt and text_between(e, nxt) < MIN_SECTION_CHARS:
            continue
        kept.append(e)
    return kept


# --- shared ------------------------------------------------------------------------------------

def _close(structure, pages):
    secs = structure.sections
    for a, b in zip(secs, secs[1:] + [None]):
        a.end_page = b.start_page + 1 if b else len(pages)
    parts = {}
    for s in secs:
        parts.setdefault(s.part, {'part': s.part, 'label': s.part_label, 'chapters': 0})['chapters'] += 1
    structure.parts = list(parts.values())
    return structure


def plausible(structure):
    secs = structure.sections
    if len(secs) < MIN_CHAPTERS:
        return False
    if any(b.start_page < a.start_page for a, b in zip(secs, secs[1:])):
        return False
    return len({s.label for s in secs}) == len(secs)


def detect(outline, pages):
    """Bookmarks if they hold up, then heading lines; raises NoChapters otherwise."""
    for candidate in (from_outline(outline, pages), from_headings(pages)):
        if candidate and plausible(candidate):
            return candidate
    raise NoChapters('No chapters were found: the PDF has no chapter bookmarks and no chapter headings.')
