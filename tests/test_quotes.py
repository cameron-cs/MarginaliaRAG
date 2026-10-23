import pytest

from marginalia.ingest.quotes import detect_quote_style, extract_quotes

SPEECH = 'I do not know what the man is called, and I shall not ask him again'


@pytest.mark.parametrize('style,text', [
    ('double_curly', f'“{SPEECH},” he said.'),
    ('single_curly', f'‘{SPEECH},’ he said.'),
    ('double_straight', f'"{SPEECH}," he said.'),
    ('latex', f"``{SPEECH},'' he said."),
    ('guillemets', f'«{SPEECH},» he said.'),
    ('low_high', f'„{SPEECH},“ he said.'),
    ('dash', f'— {SPEECH} — he said.'),
])
def test_each_style_is_extracted(style, text):
    assert [q.text for q in extract_quotes(text, style, min_chars=20)] == [SPEECH + ('' if style == 'dash' else ',')]


@pytest.mark.parametrize('style,opener', [
    ('double_curly', '“'), ('single_curly', '‘'), ('latex', '``'), ('guillemets', '«'), ('double_straight', '"'),
])
def test_dominant_style_is_detected(style, opener):
    closer = {'“': '”', '‘': '’', '``': "''", '«': '»', '"': '"'}[opener]
    text = ' '.join(f'{opener}Line number {i} spoken aloud,{closer} she said.' for i in range(30))
    # A few quotes in another style must not change the verdict.
    text += ' ‘Aside one.’ ‘Aside two.’'
    assert detect_quote_style(text) == style


def test_too_few_quotes_means_no_style():
    assert detect_quote_style('“One,” he said. “Two,” she said.') == 'none'
    assert extract_quotes('“' + SPEECH + '”', 'none') == []


def test_apostrophes_do_not_close_single_quotes():
    text = '‘I don’t know what the man’s name is, and I shan’t ask him again,’ he said.'
    assert [q.text for q in extract_quotes(text, 'single_curly', min_chars=20)] == [
        'I don’t know what the man’s name is, and I shan’t ask him again,']


def test_short_quotes_are_dropped_and_offsets_are_exact():
    text = 'He said: “' + 'a long speech ' * 10 + 'ends.” Then “Yes,” she said.'
    [q] = extract_quotes(text, 'double_curly')
    assert text[q.start:q.end] == q.text


def test_long_speech_is_chunked():
    body = ' '.join(f'Sentence {i} of a very long speech goes on.' for i in range(80))
    text = f'“{body}” he said.'
    quotes = extract_quotes(text, 'double_curly', max_chars=1500)
    assert len(quotes) > 1 and all(len(q.text) <= 1500 for q in quotes)
    assert all(text[q.start:q.end] == q.text for q in quotes)
