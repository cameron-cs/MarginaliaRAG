import io

import httpx
from fpdf import FPDF
from PIL import Image

from marginalia.ingest.cover import cover_colors, generated_cover, make_cover, open_library_cover, pdf_cover, \
    title_candidates


def picture(width, height, color=(30, 60, 120)):
    return Image.new('RGB', (width, height), color)


def pdf_with_first_page(image=None, text=None, path=None):
    pdf = FPDF(unit='pt', format=(432, 648))
    pdf.add_page()
    if image is not None:
        pdf.image(image, x=0, y=0, w=432, h=432 * image.height / image.width)
    if text:
        pdf.set_font('Helvetica', size=12)
        pdf.set_xy(20, 500)
        pdf.multi_cell(390, 14, text)
    pdf.add_page()
    pdf.set_font('Helvetica', size=12)
    pdf.cell(0, 14, 'Chapter 1')
    path.write_bytes(bytes(pdf.output()))
    return path


def test_a_portrait_picture_without_text_is_the_cover(tmp_path):
    data = pdf_cover(pdf_with_first_page(picture(500, 710), path=tmp_path / 'b.pdf'))
    image = Image.open(io.BytesIO(data))
    assert image.format == 'JPEG' and image.size == (500, 710)


def test_a_banner_page_with_text_is_not_a_cover(tmp_path):
    banner = pdf_with_first_page(picture(451, 353), text='Download free eBooks of classic literature, books and '
                                 'novels at Planet eBook. Subscribe to our free eBooks blog.', path=tmp_path / 'b.pdf')
    assert pdf_cover(banner) is None
    assert pdf_cover(pdf_with_first_page(picture(600, 400), path=tmp_path / 'c.pdf')) is None, 'landscape'
    assert pdf_cover(pdf_with_first_page(text='Chapter 1', path=tmp_path / 'd.pdf')) is None, 'no picture'


def test_large_covers_are_scaled_down(tmp_path):
    data = pdf_cover(pdf_with_first_page(picture(1000, 1500), path=tmp_path / 'b.pdf'))
    assert Image.open(io.BytesIO(data)).size == (600, 900)


def test_title_candidates():
    assert title_candidates("Harry Potter 1 - Harry Potter and the Sorcerer's Stone") == [
        "Harry Potter 1 - Harry Potter and the Sorcerer's Stone", "Harry Potter and the Sorcerer's Stone"]
    assert title_candidates('Dune: Book One') == ['Dune: Book One', 'Dune']


def _jpeg(color=(200, 10, 10)):
    out = io.BytesIO()
    picture(300, 450, color).save(out, 'JPEG')
    return out.getvalue()


def fake_open_library(docs, image=None, log=None):
    def handler(request):
        if log is not None:
            log.append(request)
        if request.url.host == 'openlibrary.org':
            return httpx.Response(200, json={'docs': docs})
        if image is None:
            return httpx.Response(404)
        return httpx.Response(200, content=image, headers={'content-type': 'image/jpeg'})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_open_library_cover_for_a_matching_title():
    requests = []
    http = fake_open_library([{'title': 'Crime and Punishment: A Study Guide', 'cover_i': 7},     # another book
                              {'title': 'Crime and Punishment', 'cover_i': 42}], _jpeg(), requests)
    data = open_library_cover('Crime and Punishment', 'Fyodor Dostoevsky', 'marginalia-test', http=http)
    assert Image.open(io.BytesIO(data)).format == 'JPEG'
    search, image = requests
    assert search.url.params['title'] == 'Crime and Punishment' and search.url.params['author'] == 'Dostoevsky'
    assert str(image.url) == 'https://covers.openlibrary.org/b/id/42-L.jpg?default=false'
    assert 'gadcam' not in str(search.headers) and '@' not in search.headers.get('user-agent', '')


def test_no_open_library_cover_when_titles_differ_or_the_image_is_missing():
    assert open_library_cover('Crime and Punishment', None, 'ua',
                              http=fake_open_library([{'title': 'The Idiot', 'cover_i': 1}], _jpeg())) is None
    assert open_library_cover('Crime and Punishment', None, 'ua',
                              http=fake_open_library([{'title': 'Crime and Punishment', 'cover_i': 1}])) is None


def test_open_library_failures_fall_back_quietly():
    def broken(request):
        raise httpx.ConnectError('offline')
    http = httpx.Client(transport=httpx.MockTransport(broken))
    assert open_library_cover('Crime and Punishment', None, 'ua', http=http) is None


def test_generated_cover_is_an_escaped_svg_in_the_ui_colours():
    svg = generated_cover('Fish & <Chips>', 'A. Writer').decode()
    assert svg.startswith('<svg') and 'Fish &amp; &lt;Chips&gt;' in svg and 'A. WRITER' in svg
    top, bottom = cover_colors('Fish & <Chips>')
    assert top in svg and bottom in svg
    assert cover_colors('Crime and Punishment') == cover_colors('Crime and Punishment')
    assert '<script' not in svg


def test_make_cover_takes_the_first_source_that_has_one(tmp_path):
    with_cover = pdf_with_first_page(picture(500, 710), path=tmp_path / 'a.pdf')
    without = pdf_with_first_page(text='Chapter 1', path=tmp_path / 'b.pdf')
    http = fake_open_library([{'title': 'The Book', 'cover_i': 3}], _jpeg())
    assert make_cover(with_cover, 'The Book', http=http)[1:] == ('jpg', 'pdf')
    assert make_cover(without, 'The Book', http=http)[1:] == ('jpg', 'openlibrary')
    assert make_cover(without, 'The Book', online=False)[1:] == ('svg', 'generated')
