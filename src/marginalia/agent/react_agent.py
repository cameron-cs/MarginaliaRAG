"""ReAct agent over the book: qwen3 (thinking on) via Ollama, tools from the MCP server.

Loop: the model thinks, calls MCP retrieval tools, reads their results and repeats until it
answers (LangChain `create_agent`). Tool calls are capped per run. Before the model's first turn,
the agent runs one search of the whole book with the question itself, so the model always starts
from the passages that match the question best: left to choose, a small model may search too
narrowly ("invisibility cloak" instead of who sent it) or in the wrong chapter, and conclude the
book does not say. The answer then goes through
one reflection step: its citations must match retrieved passages, and an LLM grader checks it
against the retrieved evidence. If either check fails, the agent gets the problems as feedback and
answers again, at most `max_revisions` times.
"""

import json
import os
import re
import sys
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from langchain.agents import create_agent
from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from langchain_ollama import ChatOllama
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel, Field

from marginalia.agent.prompts import (BUDGET_EXHAUSTED_PROMPT, GAVE_UP_TOO_EARLY, GRADER_PROMPT, PERSONA_SECTION,
                           REVISION_PROMPT, SYSTEM_PROMPT)
from marginalia.agent.sampling import Sampling
from marginalia.agent.usage import TokenUsage, UsageCallback
from marginalia.config import ROOT, Settings

# [Part I, Chapter II, passage 3], [Chapter Twelve, passage 16], [Epilogue, Chapter II], [Book One, ...]
_CITATION = re.compile(r'\[([^\[\]]*?\b(?:Part|Chapter|Book|Prologue|Epilogue)\b[^\[\]]*)\]')
_QUERY_CHARS = 500           # the search tools' longest query
_EVIDENCE_CHARS = 12000      # keeps the grader prompt inside agent_num_ctx
_NOT_FOUND = re.compile(
    r"\b(?:do|does|did)(?: not|n't) (?:say|specify|mention|state|describe|answer|reveal)"
    r"|\bno (?:information|mention|details?)\b|\bnot (?:found|mentioned|specified|stated)\b"
    r"|\bcould(?: not|n't) find\b", re.I)


class Verdict(BaseModel):
    unsupported_claims: list[str] = Field(
        default_factory=list, description='Claims in the answer that the evidence does not support.')
    supported: bool = Field(description='True if every claim in the answer is supported by the evidence.')


@dataclass
class Evidence:
    citation: str
    text: str


@dataclass
class AgentResult:
    question: str
    answer: str
    citations: list[str]              # cited in the answer and found among retrieved results
    unknown_citations: list[str]      # cited but never retrieved
    tool_calls: list[dict]
    supported: bool | None            # grader verdict; None if the grader failed
    unsupported_claims: list[str]
    revisions: int
    budget_exhausted: bool
    usage: dict | None = None         # tokens by role (marginalia.agent.usage)
    messages: list = field(repr=False, default_factory=list)


def make_llm(settings=None, sampling=None, role='agent', model=None):
    """The agent's chat model. `sampling` (marginalia.agent.sampling.Sampling) overrides the server's
    sampling settings; without it they apply as they are. `role` labels its tokens (agent, grader,
    judge); `model` replaces the agent model (the DeepEval judge may use another one)."""
    settings = settings or Settings()
    s = (sampling or Sampling()).resolve(settings)
    # Qwen3 thinking models are meant to be sampled (Qwen recommends T=0.6, top_p=0.95);
    # greedy decoding makes their reasoning loop. reasoning=True keeps the thinking out of
    # `content`: qwen3:4b ignores think=false.
    return ChatOllama(model=model or settings.agent_model, base_url=settings.ollama_url, reasoning=True,
                      num_ctx=settings.agent_num_ctx, temperature=s.temperature, top_p=s.top_p, top_k=s.top_k,
                      seed=s.seed, callbacks=[UsageCallback(role)])



# What the MCP server process must not see: it only reads the library and Qdrant.
_SECRET_ENV = ('CP_JWT_SECRET', 'CP_DATABASE_URL', 'CP_POSTGRES_PASSWORD')


def stdio_connection(book_id):
    # The MCP SDK starts stdio servers with a minimal environment (PATH, HOME...), so settings that
    # come from the environment (CP_LIBRARY_DIR, CP_QDRANT_URL in Docker) must be passed explicitly.
    env = {k: v for k, v in os.environ.items() if k not in _SECRET_ENV}
    return {'transport': 'stdio', 'command': sys.executable,
            'args': ['-m', 'marginalia.mcp_server', '--book', book_id], 'cwd': str(ROOT), 'env': env}


def book_name(profile):
    return f'{profile.title!r} by {profile.author}' if profile.author else repr(profile.title)


@asynccontextmanager
async def mcp_tools(connection):
    """LangChain tools bound to one long-lived MCP session (one server process for the whole run)."""
    client = MultiServerMCPClient({'book': connection})
    async with client.session('book') as session:
        yield await load_mcp_tools(session)


def message_text(message):
    content = message.content
    if isinstance(content, str):
        return content
    return ''.join(b.get('text', '') if isinstance(b, dict) else str(b) for b in content)


def collect_evidence(messages):
    """Every cited passage or summary returned by a tool, in the order retrieved, without repeats."""
    out, seen = [], set()
    for m in messages:
        if not isinstance(m, ToolMessage):
            continue
        try:
            data = json.loads(message_text(m))
        except ValueError:
            continue                      # tool errors come back as plain text
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and 'citation' in item and item['citation'] not in seen:
                seen.add(item['citation'])
                out.append(Evidence(item['citation'], item.get('text') or item.get('summary') or ''))
    return out


def extract_citations(answer):
    cites = []
    for group in _CITATION.findall(answer):
        cites += [c.strip() for c in group.split(';') if c.strip()]
    return list(dict.fromkeys(cites))


def check_citations(citations, evidence):
    """A citation is valid if it names a retrieved result, or the chapter one came from."""
    retrieved = [e.citation for e in evidence]
    valid = [c for c in citations if any(r == c or r.startswith(c + ',') for r in retrieved)]
    return valid, [c for c in citations if c not in valid]


def format_evidence(evidence, limit=_EVIDENCE_CHARS):
    text = '\n\n'.join(f'[{e.citation}] {e.text}' for e in evidence) or '(nothing was retrieved)'
    return text if len(text) <= limit else text[:limit] + '\n[... truncated]'


def parse_verdict(text):
    """The last JSON object in the reply, or None if there is no valid one."""
    for match in reversed(re.findall(r'\{[^{}]*\}', text)):
        try:
            return Verdict.model_validate_json(match)
        except ValueError:
            continue
    return None


class LlmGrader:
    """Checks an answer against the evidence it cites (all evidence if it cites none).

    Uses the thinking model. On a real answer, thinking-off grading with a JSON-schema format
    called a correct answer unsupported 3 times out of 3; thinking-on judged 6 of 6 cases right.
    Ollama returns empty content when thinking is combined with a JSON-schema format, so the
    verdict is parsed out of the reply text instead.
    """

    def __init__(self, llm, book='the book'):
        self.llm, self.book = llm, book

    async def __call__(self, question, evidence, answer):
        cited = extract_citations(answer)
        relevant = [e for e in evidence if any(e.citation == c or e.citation.startswith(c + ',') for c in cited)]
        prompt = GRADER_PROMPT.format(book=self.book, question=question,
                                      evidence=format_evidence(relevant or evidence), answer=answer)
        try:
            return parse_verdict(message_text(await self.llm.ainvoke(prompt)))
        except Exception:
            return None                   # an unreachable grader means "unchecked", not a failure


class BookAgent:
    def __init__(self, llm, tools, profile=None, grader=None, max_tool_calls=6, max_revisions=1, min_searches=3,
                 persona=None, first_search=True):
        """`profile` describes the book (title, citation style, name spellings); `persona` is the book
        owner's prompt, which shapes answers but ranks below the rules.
        `grader(question, evidence, answer) -> Verdict | None`; without one, answers are not graded.
        `first_search`: search the whole book for the question before the model's first turn."""
        self.llm = llm
        self.tools = {t.name: t for t in tools}
        self.first_search = first_search
        self.max_revisions = max_revisions
        self.min_searches = min(min_searches, max_tool_calls)
        self.system_prompt = system_prompt(profile, max_tool_calls, self.min_searches, persona)
        self.grader = grader
        # Calls past the budget get an error ToolMessage, so the model has to answer.
        self.graph = create_agent(
            llm, tools, system_prompt=self.system_prompt,
            middleware=[ToolCallLimitMiddleware(run_limit=max_tool_calls, exit_behavior='continue')])
        # Backstop for a model that keeps calling tools after being refused.
        self.recursion_limit = 4 * (max_tool_calls + 4)

    async def ask(self, question, on_message=None):
        """Answer one question; the result carries the tokens its LLM calls used."""
        usage = TokenUsage()
        with usage.counting():
            result = await self._ask(question, on_message)
        result.usage = usage.summary()
        return result

    async def _ask(self, question, on_message=None):
        on_message = on_message or (lambda m: None)
        messages = [HumanMessage(question)]
        on_message(messages[0])
        if self.first_search:
            messages += await self._search_question(question, on_message)
        revisions, exhausted = 0, False
        while True:
            messages, ran_out = await self._run(messages, on_message)
            exhausted |= ran_out
            answer = message_text(messages[-1])
            evidence = collect_evidence(messages)
            citations, unknown = check_citations(extract_citations(answer), evidence)
            searches = sum(len(m.tool_calls) for m in messages if isinstance(m, AIMessage))
            verdict = await self.grader(question, evidence, answer) if self.grader else None
            problems = (verdict.unsupported_claims if verdict and not verdict.supported else []) + [
                f'The citation [{c}] does not match any result you retrieved.' for c in unknown]
            gave_up = bool(_NOT_FOUND.search(answer)) and searches < self.min_searches
            if not (problems or gave_up) or revisions >= self.max_revisions:
                break
            revisions += 1
            if gave_up:
                feedback = HumanMessage(GAVE_UP_TOO_EARLY.format(searches=searches))
            else:
                feedback = HumanMessage(REVISION_PROMPT.format(claims='\n'.join(f'- {p}' for p in problems)))
            on_message(feedback)
            messages = messages + [feedback]

        return AgentResult(
            question=question, answer=answer, citations=citations, unknown_citations=unknown,
            tool_calls=[{'name': c['name'], 'args': c['args']}
                        for m in messages if isinstance(m, AIMessage) for c in m.tool_calls],
            supported=verdict.supported if verdict else None,
            unsupported_claims=verdict.unsupported_claims if verdict else [],
            revisions=revisions, budget_exhausted=exhausted, messages=messages)

    async def _search_question(self, question, on_message):
        """[the search call, its result] for search_passages over the whole book with the question
        as the query; nothing if the tool is missing or fails (the model then searches on its own)."""
        tool = self.tools.get('search_passages')
        if tool is None:
            return []
        call = {'name': 'search_passages', 'args': {'query': question[:_QUERY_CHARS]},
                'id': f'first-search-{uuid.uuid4().hex[:8]}', 'type': 'tool_call'}
        try:
            result = await tool.ainvoke(call)
        except Exception:
            return []
        if not isinstance(result, ToolMessage):
            result = ToolMessage(content=str(result), tool_call_id=call['id'], name=call['name'])
        request = AIMessage(content='', tool_calls=[call])
        for m in (request, result):
            on_message(m)
        return [request, result]

    async def _run(self, messages, on_message):
        """One ReAct run; returns the messages, ending in a final answer, and whether it was cut off."""
        state, reported = {'messages': messages}, len(messages)
        try:
            async for state in self.graph.astream({'messages': messages}, {'recursion_limit': self.recursion_limit},
                                                  stream_mode='values'):
                for m in state['messages'][reported:]:
                    on_message(m)
                reported = len(state['messages'])
        except GraphRecursionError:
            pass
        out = list(state['messages'])
        last = out[-1]
        if isinstance(last, AIMessage) and not last.tool_calls and message_text(last).strip():
            return out, False
        # Cut off mid-loop: drop an unanswered tool request and force an answer without tools.
        if isinstance(last, AIMessage) and last.tool_calls:
            out.pop()
        nudge = HumanMessage(BUDGET_EXHAUSTED_PROMPT)
        final = await self.llm.ainvoke([SystemMessage(self.system_prompt), *out, nudge])
        for m in (nudge, final):
            on_message(m)
        return out + [nudge, final], True


def system_prompt(profile, max_tool_calls, min_searches, persona=None):
    if profile is None:
        base = SYSTEM_PROMPT.format(book='you are asked about', citation_example='Chapter 1, passage 3',
                                    names='', max_tool_calls=max_tool_calls, min_searches=min_searches)
    else:
        first = profile.chapters[0]['label'] if profile.chapters else 'Chapter 1'
        spelled = ', '.join(profile.names[:8])
        base = SYSTEM_PROMPT.format(
            book=book_name(profile), citation_example=f'{first}, passage 3',
            names=f'; this edition writes {spelled}' if spelled else '',
            max_tool_calls=max_tool_calls, min_searches=min_searches)
    if persona:
        base += PERSONA_SECTION.format(persona=persona.strip())
    return base


@asynccontextmanager
async def book_agent(profile, settings=None, connection=None, **kwargs):
    """An agent for one ready book of the library, with its own MCP server process."""
    llm = make_llm(settings)
    kwargs.setdefault('grader', LlmGrader(make_llm(settings, role='grader'), book_name(profile)))
    async with mcp_tools(connection or stdio_connection(profile.book_id)) as tools:
        yield BookAgent(llm, tools, profile=profile, **kwargs)
