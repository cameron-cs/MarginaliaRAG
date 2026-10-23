"""Synthetic book PDFs with known structure, built with fpdf2 for tests.

Real books cannot be committed (copyright), and these let each test control exactly one feature:
bookmarks, part headings, running headers, page numbers, a contents page, encryption.
Text uses the core Helvetica font, so it is Latin-1 only (straight quotes).
"""

from datetime import datetime, timezone

from fpdf import FPDF

# A fixed creation date: otherwise fpdf2 stamps the current time, and two copies of "the same"
# book built a second apart are different files (a duplicate-upload test then fails at random).
CREATED = datetime(2026, 1, 1, tzinfo=timezone.utc)

LOREM = ('The rain fell on the old town while the clerk walked home along the river, thinking about '
         'the letter he had not yet opened and the visitor who had asked for him that morning.')


def paragraphs(topic, n=6):
    # Each paragraph differs, so no line repeats across pages the way a running header does.
    return [f'{topic.capitalize()} began day {i} as usual. {LOREM} "We shall see," said Tom, '
            f'and nobody answered him for {i + 2} minutes. ' + LOREM for i in range(n)]


class _Book(FPDF):
    def __init__(self, header=None, page_numbers=True):
        super().__init__()
        self.running_header, self.page_numbers = header, page_numbers
        self.set_auto_page_break(True, 20)

    def header(self):
        if self.running_header:
            self.set_font('Helvetica', size=8)
            self.cell(0, 6, self.running_header, align='C')
            self.ln(10)

    def footer(self):
        if self.page_numbers:
            self.set_y(-15)
            self.set_font('Helvetica', size=8)
            self.cell(0, 8, str(self.page_no()), align='C')


def make_book(units, header='THE TEST BOOK', page_numbers=True, title='The Test Book', author='A. Tester',
              contents_page=False, encrypt=None, blank_pages=0):
    """`units` is a list of dicts, in reading order:
        {'heading': 'CHAPTER ONE', 'title': None, 'outline': 'Chapter One', 'level': 0, 'body': [...]}
    A unit with body=None is a part heading page ("BOOK ONE"); 'outline' (bookmark text) and
    'level' (bookmark depth) are optional: units without 'outline' get no bookmark.
    `encrypt` is ('user', 'owner') passwords. `blank_pages` adds pages with no text (a scan).
    """
    pdf = _Book(header, page_numbers)
    pdf.set_creation_date(CREATED)
    pdf.set_title(title)
    pdf.set_author(author)
    pdf.set_font('Helvetica', size=11)
    if contents_page:
        pdf.add_page()
        pdf.multi_cell(0, 7, 'CONTENTS', new_x='LMARGIN', new_y='NEXT')
        for u in units:
            pdf.multi_cell(0, 7, u['heading'], new_x='LMARGIN', new_y='NEXT')
    for _ in range(blank_pages):
        pdf.add_page()
    for u in units:
        pdf.add_page()
        if u.get('outline'):
            pdf.start_section(u['outline'], level=u.get('level', 0))
        pdf.set_font('Helvetica', size=14)
        pdf.multi_cell(0, 9, u['heading'], new_x='LMARGIN', new_y='NEXT')
        if u.get('title'):
            pdf.multi_cell(0, 9, u['title'], new_x='LMARGIN', new_y='NEXT')
        pdf.set_font('Helvetica', size=11)
        pdf.ln(4)
        for para in u.get('body') or []:
            pdf.multi_cell(0, 6, para, new_x='LMARGIN', new_y='NEXT')
            pdf.ln(3)
    if encrypt:
        pdf.set_encryption(owner_password=encrypt[1], user_password=encrypt[0])
    return bytes(pdf.output())


def chapter(heading, topic, title=None, outline=None, level=0, n=6):
    return {'heading': heading, 'title': title, 'outline': outline, 'level': level, 'body': paragraphs(topic, n)}


def part(heading, outline=None, level=0):
    return {'heading': heading, 'outline': outline, 'level': level, 'body': None}


def scanned_book(pages=5):
    pdf = FPDF()
    pdf.set_creation_date(CREATED)
    for _ in range(pages):
        pdf.add_page()
        pdf.rect(20, 20, 170, 250)       # a page image stand-in: drawing, no text
    return bytes(pdf.output())
