"""From a PDF to clean, structured chapters: the book-independent ingestion front end.

    pdf -> pages -> strip running headers/footers -> find parts and chapters
        -> join wrapped lines into paragraphs -> chapters, plus a profile of what was detected
"""

import re
from collections import Counter
from dataclasses import dataclass, field

from marginalia.ingest import layout, structure
from marginalia.ingest.pdf import read_pdf
from marginalia.ingest.quotes import detect_quote_style
from marginalia.ingest.structure import classify, to_roman


@dataclass
class Chapter:
    part: int
    chapter: int
    index: int                     # 1-based position in the book
    text: str
    label: str = ''                # citation label: "Part IV, Chapter IV", "Chapter One", "Prologue"
    title: str | None = None
    part_label: str = ''
    start_page: int | None = None  # 0-based PDF page where the chapter starts

    def __post_init__(self):
        if not self.label:
            self.label = f'Part {to_roman(self.part)}, Chapter {to_roman(self.chapter)}'


@dataclass
class BookText:
    chapters: list
    method: str                    # how chapters were found: outline | headings
    parts: list                    # [{'part', 'label', 'chapters'}]
    wrap_style: str
    quote_style: str
    running_lines: list = field(default_factory=list)
    names: list = field(default_factory=list)
    title: str | None = None
    author: str | None = None
    num_pages: int = 0


def _section_text(pages, section, nxt):
    """Text from just after this section's heading up to the next section's heading."""
    first = pages[section.start_page].split('\n')
    start = section.start_line + 1 if section.start_line is not None else 0
    if nxt is None:
        body = ['\n'.join(first[start:])] + pages[section.start_page + 1:]
    elif nxt.start_page == section.start_page:
        stop = nxt.start_line if nxt.start_line is not None else len(first)
        body = ['\n'.join(first[start:stop])]
    else:
        middle = pages[section.start_page + 1:nxt.start_page]
        last = pages[nxt.start_page].split('\n')
        stop = nxt.start_line if nxt.start_line is not None else 0
        body = ['\n'.join(first[start:])] + middle + (['\n'.join(last[:stop])] if stop else [])
    lines = '\n'.join(body).split('\n')
    # Part and chapter headings that sit between chapters ("Part II" above "Chapter I").
    lines = [l for l in lines if not structure.is_heading_line(l)]
    # A chapter title printed on the line after the heading ("THE BOY WHO LIVED").
    if section.title is None:
        content = [i for i, l in enumerate(lines) if l.strip()]
        if content and _looks_like_title(lines[content[0]]):
            section.title = ' '.join(lines[content[0]].split()).title()
            lines.pop(content[0])
    return '\n'.join(lines)


def _looks_like_title(line):
    text = ' '.join(line.split())
    return 3 <= len(text) <= 60 and text.isupper() and not re.search(r'[.!?,;:]$', text)


def frequent_names(text, limit=30, min_count=12):
    """Capitalised words and word pairs that mostly appear mid-sentence: the book's names.

    A cheap stand-in for NER, enough to tell the agent how this edition spells its characters
    (Razumihin, Dounia, Svidrigaïlov).
    """
    # A name word: capital, then 2+ lower-case letters; a possessive ’s is dropped, and words with
    # other apostrophes (I’ve, I’ll) never match.
    word = r"[A-Z][a-zà-öø-ÿ]{2,}"
    mid = Counter()
    for m in re.finditer(rf"(?<=[a-z,;] )({word}(?: {word})?)(?:['’]s)?\b(?!['’])", text):
        mid[m.group(1)] += 1
    starts = Counter(re.findall(rf'(?:^|[.!?]\s+)({word})', text, re.M))
    common = {'Mr', 'Mrs', 'Miss', 'Sir', 'God', 'Lord', 'Monday', 'Tuesday', 'Wednesday', 'Thursday',
              'Friday', 'Saturday', 'Sunday', 'The', 'She', 'But', 'And', 'Professor', 'Uncle', 'Aunt'}
    pairs = [name for name, n in mid.items() if ' ' in name and n >= min_count]
    out = []
    for name, n in mid.most_common():
        head = name.split()[0]
        if n < min_count or head in common or starts[head] > 2 * n:
            continue
        # A lone word that mostly occurs inside a kept pair ("Petrovitch" in "Pyotr Petrovitch").
        if ' ' not in name and sum(mid[p] for p in pairs if name in p.split()) >= 0.5 * n:
            continue
        out.append(name)
        if len(out) >= limit:
            break
    return out


def load_book(source):
    """Parse a PDF (path or bytes). Raises PdfError or structure.NoChapters."""
    pdf = read_pdf(source)
    pages = [layout.normalize_page(p) for p in pdf.pages]
    running = layout.find_running_lines(pages)
    pages = [layout.strip_running_lines(p, running) for p in pages]
    found = structure.detect(pdf.outline, pages)
    wrap_style = layout.detect_wrap_style('\n'.join(pages))
    vocabulary = layout.word_counts('\n'.join(pages))

    chapters = []
    secs = found.sections
    for i, (sec, nxt) in enumerate(zip(secs, secs[1:] + [None]), start=1):
        raw = _section_text(pages, sec, nxt)
        text = layout.fix_drop_cap(layout.to_paragraphs(raw, wrap_style, vocabulary), vocabulary)
        chapters.append(Chapter(sec.part, sec.chapter, i, text, sec.label, sec.title, sec.part_label,
                                sec.start_page))
    full = '\n\n'.join(c.text for c in chapters)
    return BookText(chapters=chapters, method=found.method, parts=found.parts, wrap_style=wrap_style,
                    quote_style=detect_quote_style(full), running_lines=sorted(running),
                    names=frequent_names(full), title=pdf.title, author=pdf.author, num_pages=len(pdf.pages))
