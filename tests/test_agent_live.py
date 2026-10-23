"""The full agent on real questions: qwen3:4b on Ollama, the MCP server over stdio, Docker Qdrant.

Slow (1-2 minutes per question) and sampled at temperature 0.6, so the checks are about what any
acceptable answer must contain, not exact wording.

    pytest -m agent
"""

import asyncio

import httpx
import pytest

from marginalia.agent.react_agent import _NOT_FOUND, book_agent
from marginalia.config import Settings
from marginalia.library import Library, UnknownBook

settings = Settings()


def _ready():
    try:
        tags = httpx.get(f'{settings.ollama_url}/api/tags', timeout=2).json()
        has_model = any(m['name'] == settings.agent_model for m in tags.get('models', []))
        return has_model and Library().resolve('crime-and-punishment').status == 'ready'
    except (httpx.HTTPError, UnknownBook):
        return False


pytestmark = [pytest.mark.integration, pytest.mark.agent,
              pytest.mark.skipif(not _ready(), reason=f'needs Ollama with {settings.agent_model} and the Qdrant index')]


def ask(question):
    async def run():
        async with book_agent(Library().resolve('crime-and-punishment'), settings) as agent:
            return await agent.ask(question)
    result = asyncio.run(run())
    print(f'\nQ: {question}\nA: {result.answer}\n   tools={[c["name"] for c in result.tool_calls]} '
          f'revisions={result.revisions} supported={result.supported} unknown={result.unknown_citations}')
    return result


@pytest.mark.parametrize('question,must_mention', [
    ('Who did Raskolnikov kill?', ['Alyona', 'Lizaveta']),
    ('What story from the Gospel did Sonia read aloud to Raskolnikov?', ['Lazarus']),
    ("What is the name of Raskolnikov's friend, the student who helps him when he is ill?", ['Razumihin']),
])
def test_answers_known_facts_with_citations(question, must_mention):
    result = ask(question)
    assert result.tool_calls, 'answered without searching'
    for word in must_mention:
        assert word in result.answer
    assert not _NOT_FOUND.search(result.answer), 'hedged about a fact the book states'
    assert result.citations, 'no citation matched a retrieved passage'
    assert not result.unknown_citations


def test_declines_question_the_book_does_not_answer():
    result = ask("Which football team does Raskolnikov support?")
    assert result.tool_calls
    assert 'football' not in result.answer.lower() or any(
        p in result.answer.lower() for p in ('not', 'no ', "n't", 'nothing'))
    assert not result.unknown_citations
