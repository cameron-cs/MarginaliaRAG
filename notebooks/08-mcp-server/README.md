# 08 · The MCP retrieval server

Notebook: [08-mcp-server.ipynb](08-mcp-server.ipynb) · Code: `src/marginalia/mcp_server/server.py`, `src/marginalia/mcp_server/__main__.py`

## What it does

Exposes retrieval over one book as tools, through the Model Context Protocol (MCP):

- `search_passages`, `search_quotes` and `search_chapter_summaries`;
- `get_chapter_summary`, `get_surrounding_text` and `list_chapters`.

It also gives instructions describing the book: its title, how its parts and chapters are numbered,
and how this edition spells its names. The agent calls these tools; nothing in the server calls a
language model.

## Why?

MCP is a standard way to hand tools to a model: any MCP client (LangChain, Claude Desktop, another
agent) can use the same server without glue code. Keeping retrieval in its own process with typed
inputs means the tools validate what the model asks for. A chapter that does not exist, or a
`chapter` without its `part` in a book that has parts, comes back as an error the model can read and
correct. A crashed search never brings down the agent.

## For what

- The worker's agent (step 09) runs one server per book over stdio and keeps it while the index
  version stays the same.
- Any MCP client: `python -m marginalia.mcp_server --book crime` (stdio) or `--transport http`.
- Guiding a small model: instructions and error messages are written for it.

## Simple explanation

A reference desk with a fixed menu: "search the text", "search what people said", "search the
chapter summaries", "read around this passage", "show me the contents". The model fills in a form
for each request; a wrong form gets a note back explaining what to fix.

## Examples

```
search_passages(query="Lazarus Gospel", part=4, chapter=4, limit=5)
  -> [{"citation": "Part IV, Chapter IV, passage 3", "text": "...", "id": "...", "score": 0.83}, ...]

search_passages(query="troll", chapter=1)       (Harry Potter, no parts)
  -> error: Chapter One never mentions "troll". They appear in: Chapter Ten (29x); Chapter
     Seventeen (4x); Chapter Eleven (3x); Chapter Six (1x). Search one of those chapters, or leave
     out `chapter`.

get_chapter_summary(chapter=12, part=1)          (a book where Part I has chapters 1-7)
  -> error: Part I has chapters 1-7.
```

The second error exists because the agent once searched "troll" in chapter 1, got five unrelated
passages, and concluded the book has no troll.

## Technical

- **Framework:** FastMCP 3.4.11. Version 4 needs `mcp>=2`, which langchain-mcp-adapters 0.3.2 does not
  support yet.
- **Server:** `build_server(profile, retriever_factory=None, chapter_texts=None)` builds a `FastMCP`
  whose tool argument types are pydantic-annotated (ranges for `part`, `chapter`, `limit`).
  `location()` validates part and chapter against the profile.
- **Wrong-chapter hint:** `check_mentions()` folds the query's content words (no question words, at
  least 3 letters). It checks whether the chapter's text contains any of them; if none does, it lists
  the chapters that do, with counts.
- **Laziness:** the retriever is built on the first call (`retriever_factory`), and `warm_up` is
  started in a background thread, so the process answers MCP's handshake at once.
- **Results:** typed (`Passage`, `ChapterSummary`, `ChapterInfo`), returned as structured content.
- **The worker:** `stdio_connection(book_id)` starts `python -m marginalia.mcp_server --book <id>` with
  the settings it needs but none of the secrets (`CP_JWT_SECRET`, the database URL).

## Tech examples

```python
from fastmcp import Client
from marginalia.mcp_server.server import build_server
from marginalia.library import Library

server = build_server(Library().resolve('crime'))
async with Client(server) as c:
    print([t.name for t in await c.list_tools()])
    hits = (await c.call_tool('search_quotes', {'query': 'titular counsellor', 'limit': 1})).data
```

## How it is connected to other steps

- **Wraps:** the retriever (step 07).
- **Used by:** the agent (step 09), through `langchain-mcp-adapters` (`load_mcp_tools`).
- **The agent's checks:** the tools' `citation` field is what the agent must cite and what its
  citation check compares against.
- **Version changes:** after a re-index (step 13), the worker's `AgentPool` replaces the server process
  because the index version changed.
