import json

import httpx
import pytest

from marginalia.ingest.summarize import summarize_book, summarize_chapter
from marginalia.ingest.book import Chapter

CHAPTER = Chapter(1, 1, 1, 'On an exceptionally hot evening early in July a young man came out. ' * 600)


def _client(content, thinking='Okay, let me think.', seen=None):
    def handler(request):
        if seen is not None:
            seen.append(json.loads(request.content))
        return httpx.Response(200, json={'message': {'content': content, 'thinking': thinking}})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_request_enables_thinking_and_sizes_context():
    seen = []
    out = summarize_chapter(CHAPTER, 'qwen3:4b', 'http://ollama', _client('Raskolnikov leaves.', seen=seen))
    assert out == 'Raskolnikov leaves.'
    body = seen[0]
    assert body['think'] is True
    # ~41k characters of chapter must fit, with room left for the answer
    assert body['options']['num_ctx'] * 3.5 > len(CHAPTER.text) + 1500
    assert 'Part I, Chapter I' in body['messages'][1]['content']


@pytest.mark.parametrize('leaked', [
    'Hmm, the user wants me to summarize one chapter.',
    'Okay, let me read the chapter first.',
    'Summary below. The user wants 200 words, so...',
])
def test_leaked_reasoning_is_rejected(leaked):
    with pytest.raises(RuntimeError, match='reasoning instead of a summary'):
        summarize_chapter(CHAPTER, 'qwen3:4b', 'http://ollama', _client(leaked))


def test_empty_answer_is_rejected():
    with pytest.raises(RuntimeError, match='no summary'):
        summarize_chapter(CHAPTER, 'qwen3:4b', 'http://ollama', _client(''))


def test_think_tags_are_stripped():
    out = summarize_chapter(CHAPTER, 'm', 'http://ollama', _client('<think>x</think>Sonia reads.'))
    assert out == 'Sonia reads.'


def test_cache_is_reused_and_invalidated(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr('marginalia.ingest.summarize.summarize_chapter',
                        lambda ch, model, url, client, book: calls.append(model) or f'summary by {model}')
    cache = tmp_path / 'summaries.json'
    assert summarize_book([CHAPTER], 'a', 'http://x', cache, log=lambda *_: None) == ['summary by a']
    summarize_book([CHAPTER], 'a', 'http://x', cache, log=lambda *_: None)
    assert calls == ['a']
    summarize_book([CHAPTER], 'b', 'http://x', cache, log=lambda *_: None)
    assert calls == ['a', 'b']


def test_book_name_is_in_the_prompt_and_chapters_run_concurrently(tmp_path):
    seen = []
    chapters = [Chapter(1, i, i, f'Text of chapter {i}. ' * 50) for i in range(1, 5)]
    client_factory = _client('A summary.', seen=seen)
    import marginalia.ingest.summarize as summarize
    original = summarize.httpx.Client
    summarize.httpx.Client = lambda **kw: client_factory
    try:
        out = summarize.summarize_book(chapters, 'm', 'http://x', tmp_path / 's.json', book="'Emma' by Jane Austen",
                                       concurrency=4, log=lambda *_: None)
    finally:
        summarize.httpx.Client = original
    assert out == ['A summary.'] * 4 and len(seen) == 4
    assert all("'Emma' by Jane Austen" in body['messages'][0]['content'] for body in seen)
