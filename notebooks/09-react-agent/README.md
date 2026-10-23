# 09 · The ReAct agent

Notebook: [09-react-agent.ipynb](09-react-agent.ipynb) · Code: `src/marginalia/agent/react_agent.py`,
`src/marginalia/agent/prompts.py`, `src/marginalia/agent/sampling.py`, `src/marginalia/agent/usage.py`

## What it does

Answers a reader's question about one book. It reasons, calls the MCP tools to search, reads the
results, and searches again if it needs to; then it writes an answer citing the passages it used.
The answer is checked before it is accepted:

- Every citation must match a passage that was actually retrieved.
- A grader (the same model, as judge) lists the claims the evidence does not support.

If a check fails, the agent gets the problems as feedback and answers again.

## Why?

"ReAct" (reason + act) lets a model decide what to look up, look at the result, and adjust. One
search with the question is not always enough: "Who falsely confesses to the murders?" needs the
confession scene, and a follow-up on a name. Checking the answer against the retrieved text catches
what small models do most: fill a gap from memory ("Hagrid gave Harry the cloak") and cite a passage
that says no such thing.

## For what

- The reader's questions in the web UI, answered by the worker.
- The eval runner's agent runs (step 11).
- `python -m marginalia.agent --book crime "What did Sonia read to Raskolnikov?"` on the command line.

## Simple explanation

A research assistant with a reading desk. They first look up your question as you asked it, read
what comes back, look up anything still missing, and then write a short answer with page references.
A colleague checks each sentence against the pages on the desk; anything not there goes back with
a note: find it, or drop it.

## Examples

```
Question: Who gave Harry the invisibility cloak?
→ (first search, the question itself) search_passages("Who gave Harry the invisibility cloak?")
← Chapter Seventeen, passage 22: "your father happened to leave it in my possession..."
Answer: Dumbledore gave Harry the invisibility cloak [Chapter Seventeen, passage 22].
Grader: supported.  Tokens: counted per role (agent, grader), with the generation speed.
```

Before the first-search change and the revision-prompt fix, the same question got "Hagrid", and the
revision prompt pushed the model to defend it.

## Technical

- **Model:** `make_llm(settings, sampling, role)` builds `ChatOllama` for qwen3:4b with `reasoning=True`.
  qwen3:4b only thinks; `think=false` leaks the reasoning into the answer. Sampling is temperature
  0.6, top_p 0.95 (Qwen's advice for thinking mode; greedy decoding makes it loop), top_k 40. A
  `UsageCallback(role)` counts tokens.
- **Loop:** `BookAgent(llm, tools, profile, grader, max_tool_calls=6, max_revisions=1, min_searches=3,
  persona, first_search=True)`. LangChain's `create_agent` runs the ReAct loop, and
  `ToolCallLimitMiddleware` caps the tool calls per run; past the cap, the model must answer from
  what it has. `first_search` runs `search_passages` with the question before the model's first
  turn.
- **Citations:** `extract_citations` reads `[Part I, Chapter II, passage 3]`, `[Chapter Twelve,
  passage 16]` and `[Epilogue, Chapter II]` from the answer. `check_citations` keeps the ones that
  match a retrieved result, or the chapter of one.
- **Grader:** `LlmGrader` shows the judge the evidence the answer cites (or all of it) and reads a JSON
  verdict out of the reply text. With thinking on and a JSON schema, qwen3:4b returns nothing.
- **Revision:** `REVISION_PROMPT` asks the model to search for each flagged claim and cite it, or else
  stop repeating it. `GAVE_UP_TOO_EARLY` sends back a "the book doesn't say" answer that came after
  too few searches.
- **The persona:** the owner's prompts are added in an `<owner_instructions>` section that ranks below
  the rules.
- **Sampling per question:** `Sampling(temperature, top_p, top_k, max_searches, seed)`. Readers set it
  in the UI; the grader keeps the server's settings.
- **Tokens:** `TokenUsage`, held in a context variable for the duration of `ask()`, sums Ollama's
  `prompt_eval_count`, `eval_count` and `eval_duration` per role (`agent`, `grader`). Answers asked
  together each count only their own calls.

## Tech examples

```python
from marginalia.agent.react_agent import book_agent
from marginalia.library import Library

async with book_agent(Library().resolve('harry'), max_tool_calls=6) as agent:
    result = await agent.ask('Who gave Harry the invisibility cloak?')
print(result.answer, result.citations, result.supported, result.revisions)
print(result.usage['total'])      # {'calls': 3, 'prompt': ..., 'completion': ..., 'per_second': ...}
```

## How it is connected to other steps

- **Tools:** comes from the MCP server (step 08), backed by retrieval (step 07).
- **Before:** the guard and the cache (step 10) screen each question; a cache hit never reaches the
  agent, and the agent's supported, cited answers fill the cache.
- **Runs in:** the worker (step 12), which streams its steps to the reader: thinking, searching,
  reading, checking, answering.
- **Measured by:** the evaluations (step 11), and watched by the token metrics, Grafana (step 14) and
  Evidently (step 15).
