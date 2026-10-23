import pytest

from marginalia.ingest.chunking import chunk_text


def _sentences(n, words=12):
    return ' '.join(f'Sentence {i} ' + 'word ' * words + 'ends.' for i in range(n))


def test_offsets_point_back_into_the_source():
    text = _sentences(40)
    for c in chunk_text(text, target=300, overlap=60):
        assert text[c.start:c.end] == c.text


def test_chunks_respect_target_and_cover_everything():
    text = _sentences(40)
    chunks = chunk_text(text, target=300, overlap=60)
    assert all(len(c.text) <= 300 for c in chunks)
    assert chunks[0].start == 0 and chunks[-1].end == len(text)
    assert all(b.start <= a.end for a, b in zip(chunks, chunks[1:])), 'gap between consecutive chunks'


def test_consecutive_chunks_overlap():
    chunks = chunk_text(_sentences(40), target=300, overlap=60)
    assert all(a.end - b.start >= 60 for a, b in zip(chunks, chunks[1:]))


def test_chunks_end_on_sentence_boundaries():
    assert all(c.text.endswith('ends.') for c in chunk_text(_sentences(40), target=300, overlap=60))


def test_sentence_ends_after_closing_quotes_of_any_style():
    from marginalia.ingest.chunking import _sentences
    text = '“He left.” She stayed. ‘Fine.’ They ate. "Done." It rained. «Oui.» Night fell.'
    assert [text[s:e] for s, e in _sentences(text)] == [
        '“He left.”', 'She stayed.', '‘Fine.’', 'They ate.', '"Done."', 'It rained.', '«Oui.»', 'Night fell.']


def test_overlong_sentence_is_cut_at_whitespace():
    chunks = chunk_text('word ' * 1000 + 'end.', target=1000, overlap=150, max_chars=1500)
    assert len(chunks) > 1 and all(len(c.text) <= 1500 for c in chunks)
    assert all(not c.text.startswith(' ') for c in chunks)


def test_short_text_is_one_chunk():
    assert [c.text for c in chunk_text('Only one. Two here.')] == ['Only one. Two here.']


def test_paragraph_break_is_a_sentence_boundary():
    chunks = chunk_text('A heading without stop\n\n' + _sentences(20), target=200, overlap=0)
    assert chunks[0].text.startswith('A heading without stop')


@pytest.mark.parametrize('target', [200, 500, 1000])
def test_chunking_terminates_for_varied_targets(target):
    assert chunk_text(_sentences(100), target=target, overlap=target // 5)
