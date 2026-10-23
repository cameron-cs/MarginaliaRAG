"""The ReAct agent driven by a scripted chat model.

The tools are real: LangChain tools from langchain-mcp-adapters, bound to the MCP server running
in memory over the synthetic book, so these tests cover the whole loop except the LLM itself.
"""

import asyncio
import json
import uuid
from contextlib import asynccontextmanager

import pytest
from fastmcp import Client
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_mcp_adapters.tools import load_mcp_tools

from marginalia.agent.prompts import BUDGET_EXHAUSTED_PROMPT
from marginalia.agent.react_agent import (BookAgent, Evidence, LlmGrader, Verdict, check_citations, collect_evidence,
                               extract_citations, message_text, parse_verdict)
from marginalia.mcp_server.server import build_server
from tests.conftest import book_retriever, synthetic_profile


class ScriptedModel(BaseChatModel):
    """Answers from `respond(messages) -> AIMessage`, recording every prompt it was sent."""

    respond: object
    calls: list = []

    @property
    def _llm_type(self):
        return 'scripted'

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=self.respond(messages))])


def tool_call(name, **args):
    return AIMessage(content='', tool_calls=[{'name': name, 'args': args, 'id': str(uuid.uuid4())}])


def script(*steps):
    """Reply with each step in turn, based on how many model calls came before."""
    def respond(messages):
        n = sum(isinstance(m, AIMessage) for m in messages)
        step = steps[min(n, len(steps) - 1)]
        return step(messages) if callable(step) else step
    return respond


class FakeGrader:
    def __init__(self, *verdicts):
        self.verdicts, self.seen = list(verdicts), []

    async def __call__(self, question, evidence, answer):
        self.seen.append((question, evidence, answer))
        return self.verdicts.pop(0) if self.verdicts else Verdict(supported=True)


@pytest.fixture(scope='module')
def server():
    retriever = book_retriever('agent_test')
    yield build_server(synthetic_profile(), lambda: retriever)
    retriever.client.close()


@asynccontextmanager
async def agent_for(server, respond, **kwargs):
    async with Client(server) as client:
        tools = await load_mcp_tools(client.session)
        model = ScriptedModel(respond=respond, calls=[])
        # Scripts count the model's turns; the agent's own first search is tested on its own.
        yield BookAgent(model, tools, **{'first_search': False, **kwargs}), model


def ask(server, respond, question='What did Sonia read?', **kwargs):
    async def run():
        async with agent_for(server, respond, **kwargs) as (agent, model):
            return await agent.ask(question), model
    return asyncio.run(run())


ANSWER = AIMessage('Sonia read about Lazarus [Part IV, Chapter IV].')


# --- helpers -----------------------------------------------------------------------------------

def test_extract_citations_splits_groups_and_ignores_other_brackets():
    text = ('She read [Part IV, Chapter IV, passage 3; Part IV, Chapter IV, passage 4] and wept '
            '[Epilogue, Chapter II]. [sic] [Part IV, Chapter IV, passage 3]')
    assert extract_citations(text) == [
        'Part IV, Chapter IV, passage 3', 'Part IV, Chapter IV, passage 4', 'Epilogue, Chapter II']


def test_extract_citations_of_books_without_parts():
    text = 'Dumbledore sent it [Chapter Seventeen, passage 22]; see [Prologue] and [Book One, Chapter 3]. [1]'
    assert extract_citations(text) == ['Chapter Seventeen, passage 22', 'Prologue', 'Book One, Chapter 3']


def test_check_citations_accepts_chapter_level_and_flags_unknown():
    evidence = [Evidence('Part IV, Chapter IV, passage 3', ''), Evidence('Part I, Chapter II', '')]
    valid, unknown = check_citations(
        ['Part IV, Chapter IV', 'Part IV, Chapter IV, passage 3', 'Part I, Chapter II', 'Part IV, Chapter I',
         'Part IV, Chapter IV, passage 30'], evidence)
    assert valid == ['Part IV, Chapter IV', 'Part IV, Chapter IV, passage 3', 'Part I, Chapter II']
    assert unknown == ['Part IV, Chapter I', 'Part IV, Chapter IV, passage 30']


def test_collect_evidence_reads_lists_objects_and_skips_errors():
    blocks = [{'type': 'text', 'text': json.dumps([{'citation': 'A, passage 1', 'text': 'one'},
                                                   {'citation': 'A, passage 2', 'text': 'two'}])}]
    messages = [
        ToolMessage(blocks, tool_call_id='1'),
        ToolMessage(json.dumps({'citation': 'B', 'summary': 'sum'}), tool_call_id='2'),
        ToolMessage('Error: Part 3 has chapters 1-6.', tool_call_id='3'),
        ToolMessage(json.dumps([{'citation': 'A, passage 1', 'text': 'one'}]), tool_call_id='4'),
    ]
    assert collect_evidence(messages) == [
        Evidence('A, passage 1', 'one'), Evidence('A, passage 2', 'two'), Evidence('B', 'sum')]


@pytest.mark.parametrize('text,expected', [
    ('Reasoning {not json}.\n{"unsupported_claims": ["x"], "supported": false}', Verdict(unsupported_claims=['x'], supported=False)),
    ('{"supported": true} then {"unsupported_claims": [], "supported": true}', Verdict(supported=True)),
    ('no verdict here', None),
    ('{"supported": "maybe"}', None),
])
def test_parse_verdict(text, expected):
    assert parse_verdict(text) == expected


# --- the ReAct loop ----------------------------------------------------------------------------

def test_search_then_answer(server):
    result, model = ask(server, script(tool_call('search_passages', query='Lazarus Gospel', part=4, chapter=4),
                                       ANSWER))
    assert result.answer == ANSWER.content
    assert result.tool_calls == [{'name': 'search_passages',
                                  'args': {'query': 'Lazarus Gospel', 'part': 4, 'chapter': 4}}]
    assert result.citations == ['Part IV, Chapter IV'] and result.unknown_citations == []
    assert result.revisions == 0 and not result.budget_exhausted and result.supported is None
    # The observation the model saw is the MCP tool output.
    observation = next(m for m in model.calls[1] if isinstance(m, ToolMessage))
    assert 'Lazarus' in message_text(observation) and 'Part IV, Chapter IV, passage' in message_text(observation)


def test_the_question_is_searched_first(server):
    question = 'What did Sonia read about Lazarus?'
    seen = []
    async def run():
        async with agent_for(server, script(ANSWER), first_search=True) as (agent, model):
            return await agent.ask(question, on_message=seen.append), model
    result, model = asyncio.run(run())
    assert result.tool_calls[0] == {'name': 'search_passages', 'args': {'query': question}}
    first_prompt = model.calls[0]
    observation = next(m for m in first_prompt if isinstance(m, ToolMessage))
    assert 'Lazarus' in message_text(observation), 'the model starts from the search results'
    assert [type(m).__name__ for m in seen[:3]] == ['HumanMessage', 'AIMessage', 'ToolMessage']
    assert result.citations == ['Part IV, Chapter IV']


def test_system_prompt_states_the_budget(server):
    _, model = ask(server, script(ANSWER), max_tool_calls=4)
    assert 'at most 4 tool calls' in message_text(model.calls[0][0])


def test_tool_error_is_shown_to_the_model(server):
    _, model = ask(server, script(tool_call('get_chapter_summary', part=1, chapter=3), ANSWER),
                   min_searches=0)
    observation = next(m for m in model.calls[1] if isinstance(m, ToolMessage))
    assert 'Part I has chapters 1-2' in message_text(observation)


def test_unsupported_answer_is_revised_once(server):
    grader = FakeGrader(Verdict(unsupported_claims=['from John 11:1-44'], supported=False), Verdict(supported=True))
    first = AIMessage('Sonia read Lazarus, John 11:1-44 [Part IV, Chapter IV].')
    result, model = ask(server, script(tool_call('search_passages', query='Lazarus'), first, ANSWER), grader=grader)
    assert result.revisions == 1 and result.supported is True
    assert result.answer == ANSWER.content
    feedback = model.calls[2][-1]
    assert isinstance(feedback, HumanMessage) and 'from John 11:1-44' in feedback.content
    assert [s[2] for s in grader.seen] == [first.content, ANSWER.content]
    assert all(s[1] for s in grader.seen), 'grader got the retrieved evidence'


def test_revisions_are_capped(server):
    grader = FakeGrader(*[Verdict(unsupported_claims=['x'], supported=False)] * 5)
    result, _ = ask(server, script(tool_call('search_passages', query='Lazarus'), ANSWER), grader=grader,
                    max_revisions=2)
    assert result.revisions == 2 and result.supported is False and result.unsupported_claims == ['x']


def test_unretrieved_citation_triggers_revision_without_a_grader(server):
    fabricated = AIMessage('Sonia read Lazarus [Part II, Chapter V, passage 9].')
    result, model = ask(server, script(tool_call('search_passages', query='Lazarus', part=4), fabricated, ANSWER))
    assert result.revisions == 1 and result.unknown_citations == []
    assert '[Part II, Chapter V, passage 9] does not match' in model.calls[2][-1].content


def test_giving_up_without_searching_is_sent_back(server):
    gave_up = AIMessage('The book passages do not say what Sonia read.')
    result, model = ask(server, script(gave_up, tool_call('search_passages', query='Sonia read'), ANSWER))
    assert result.revisions == 1 and result.answer == ANSWER.content
    assert 'after only 0 search(es)' in model.calls[1][-1].content


def test_giving_up_after_enough_searches_is_accepted(server):
    gave_up = AIMessage('The book passages do not say what colour her dress was.')
    steps = [tool_call('search_passages', query=q) for q in ('dress', 'colour', 'clothes')] + [gave_up]
    result, _ = ask(server, script(*steps), min_searches=3)
    assert result.revisions == 0 and result.answer == gave_up.content


def test_tool_budget_blocks_extra_calls(server):
    steps = [tool_call('search_passages', query=q) for q in ('a b', 'c d', 'e f')] + [ANSWER]
    result, model = ask(server, script(*steps), max_tool_calls=2, min_searches=0)
    tool_messages = [m for m in model.calls[-1] if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 3
    assert sum(m.status == 'error' or 'limit' in message_text(m).lower() for m in tool_messages) == 1
    assert result.answer == ANSWER.content


def test_runaway_tool_calling_is_cut_off_and_forced_to_answer(server):
    def respond(messages):
        if isinstance(messages[-1], HumanMessage) and messages[-1].content == BUDGET_EXHAUSTED_PROMPT:
            return AIMessage('Forced answer [Part IV, Chapter IV].')
        return tool_call('list_chapters')
    result, model = ask(server, respond, max_tool_calls=1, min_searches=0)
    assert result.budget_exhausted
    assert result.answer == 'Forced answer [Part IV, Chapter IV].'
    forced_prompt = model.calls[-1]
    assert not (isinstance(forced_prompt[-2], AIMessage) and forced_prompt[-2].tool_calls), \
        'unanswered tool request was dropped before forcing the answer'


# --- grader ------------------------------------------------------------------------------------

class PromptRecorder:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.prompts = reply, error, []

    async def ainvoke(self, prompt):
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return AIMessage(self.reply)


def test_llm_grader_shows_only_cited_evidence():
    llm = PromptRecorder('Checked.\n{"unsupported_claims": [], "supported": true}')
    evidence = [Evidence('Part IV, Chapter IV, passage 3', 'Lazarus text'), Evidence('Part I, Chapter I, passage 1', 'Axe text')]
    verdict = asyncio.run(LlmGrader(llm)('q', evidence, 'Lazarus [Part IV, Chapter IV].'))
    assert verdict == Verdict(supported=True)
    assert 'Lazarus text' in llm.prompts[0] and 'Axe text' not in llm.prompts[0]


def test_llm_grader_falls_back_to_all_evidence_and_survives_errors():
    llm = PromptRecorder('{"unsupported_claims": ["y"], "supported": false}')
    evidence = [Evidence('A, passage 1', 'one'), Evidence('B, passage 2', 'two')]
    asyncio.run(LlmGrader(llm)('q', evidence, 'No citations here.'))
    assert 'one' in llm.prompts[0] and 'two' in llm.prompts[0]
    assert asyncio.run(LlmGrader(PromptRecorder(error=ConnectionError()))('q', evidence, 'a')) is None


def test_stdio_server_gets_settings_but_not_secrets(monkeypatch):
    from marginalia.agent.react_agent import stdio_connection
    monkeypatch.setenv('CP_LIBRARY_DIR', '/data/library')
    monkeypatch.setenv('CP_QDRANT_URL', 'http://qdrant:6333')
    monkeypatch.setenv('CP_JWT_SECRET', 'do-not-leak')
    monkeypatch.setenv('CP_DATABASE_URL', 'postgresql://user:pw@db/x')
    conn = stdio_connection('some-book-12345678')
    assert conn['args'][-2:] == ['--book', 'some-book-12345678']
    assert conn['env']['CP_LIBRARY_DIR'] == '/data/library' and conn['env']['CP_QDRANT_URL'] == 'http://qdrant:6333'
    assert 'CP_JWT_SECRET' not in conn['env'] and 'CP_DATABASE_URL' not in conn['env']
