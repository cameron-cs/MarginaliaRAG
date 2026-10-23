"""Chapter summaries from a local Ollama model, cached on disk by chapter text hash."""

import hashlib
import json
import logging
import math
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

SYSTEM_PROMPT = (
    "You summarize one chapter of {book} for a search index. "
    "Use only the chapter text you are given."
)
USER_PROMPT = """Summarize this chapter in 150-250 words of plain prose, for someone looking for where things happen in the book.
Lead with the events that matter to the story, in the order they happen: what characters do, decide, learn and reveal, and how they are related to each other (family, marriage, engagement, work).
Name every character who appears, using the names as spelled in the text, and the places.
Skip descriptions of rooms, clothes, furniture and weather. Do not add commentary, headings or bullet points.

{label}

{text}"""

_THINK = re.compile(r'<think>.*?</think>', re.DOTALL)
_LEAKED_REASONING = re.compile(r'^(?:okay|ok|hmm|alright|let me|the user)\b|\bthe user (?:wants|is asking)', re.I)
# Part of the cache key, so changing the prompts or settings re-summarizes.
PROMPT_ID = hashlib.sha256(f'{SYSTEM_PROMPT}|{USER_PROMPT}|think=on'.encode()).hexdigest()[:12]


def _num_ctx(text):
    # ~3.5 characters per token for this text, plus room for prompt and answer.
    needed = len(text) / 3.5 + 1500
    return int(min(32768, max(8192, 4096 * math.ceil(needed / 4096))))


def summarize_chapter(chapter, model, ollama_url, client=None, timeout=600, book='the book'):
    client = client or httpx.Client(timeout=timeout)
    resp = client.post(f'{ollama_url}/api/chat', json={
        'model': model,
        'stream': False,
        # Thinking-only builds (qwen3:4b is Qwen3-4B-Thinking-2507) ignore
        # think=false and leak their reasoning into `content`; with think=true
        # it goes to `message.thinking` and `content` is just the answer.
        'think': True,
        'messages': [
            {'role': 'system', 'content': SYSTEM_PROMPT.format(book=book)},
            {'role': 'user', 'content': USER_PROMPT.format(label=chapter.label, text=chapter.text)},
        ],
        # Ollama's default context window is far smaller than a chapter and
        # truncates silently, so size it to the chapter.
        'options': {'num_ctx': _num_ctx(chapter.text), 'temperature': 0, 'num_predict': 6000},
    })
    resp.raise_for_status()
    summary = _THINK.sub('', resp.json()['message']['content']).strip()
    if not summary:
        raise RuntimeError(f'{model} returned no summary for {chapter.label} (reasoning used up num_predict?)')
    if _LEAKED_REASONING.search(summary[:300]):
        raise RuntimeError(f'{model} returned its reasoning instead of a summary for {chapter.label}: {summary[:120]!r}')
    return summary


def summarize_book(chapters, model, ollama_url, cache_path, book='the book', concurrency=1, log=None):
    """One summary per chapter, in order. Cached chapters are not sent again; up to `concurrency`
    chapters are summarized at once (match it to the Ollama server's parallel slots)."""
    log = log or logging.getLogger(__name__).info
    cache_path = Path(cache_path)
    cache = json.loads(cache_path.read_text(encoding='utf-8')) if cache_path.exists() else {}
    lock = threading.Lock()

    def fresh(ch):
        hit = cache.get(str(ch.index))
        digest = hashlib.sha256(ch.text.encode()).hexdigest()
        return hit if hit and (hit['model'], hit['sha256'], hit.get('prompt_id')) == (model, digest, PROMPT_ID) else None

    def work(ch, client):
        log(f'  summarizing {ch.label} ({len(ch.text):,} chars) with {model}')
        summary = summarize_chapter(ch, model, ollama_url, client, book=book)
        with lock:
            cache[str(ch.index)] = {'model': model, 'sha256': hashlib.sha256(ch.text.encode()).hexdigest(),
                                    'prompt_id': PROMPT_ID, 'label': ch.label, 'summary': summary}
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding='utf-8')

    todo = [ch for ch in chapters if not fresh(ch)]
    with httpx.Client(timeout=600) as client, ThreadPoolExecutor(max(1, concurrency)) as pool:
        for future in [pool.submit(work, ch, client) for ch in todo]:
            future.result()
    return [cache[str(ch.index)]['summary'] for ch in chapters]
