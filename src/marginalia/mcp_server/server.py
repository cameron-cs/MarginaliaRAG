"""MCP server exposing retrieval over one book of the library.

The tools only retrieve: no LLM calls happen here, so results are fast and deterministic, and
all reasoning stays in the agent. The book's profile supplies what the agent needs to know up
front: title, structure and how this edition spells its names.

A search narrowed to a chapter that never mentions any word of the query is an error that says
where those words do occur. Otherwise a model that guessed the wrong chapter ("troll" in chapter
1, when the troll is in chapter 10) gets five unrelated passages and concludes the book is silent.

    python -m marginalia.mcp_server --book <id>                    # stdio (default)
    python -m marginalia.mcp_server --book <id> --transport http   # streamable HTTP on 127.0.0.1:8765/mcp
"""

import re
import threading
from typing import Annotated, Callable

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field

from marginalia.guard.terms import COMMON, fold, words
from marginalia.index.retriever import Retriever


def describe_structure(profile):
    if not profile.has_parts:
        nums = [c['chapter'] for c in profile.chapters]
        return (f'{len(nums)} chapters ({min(nums)}-{max(nums)}) and no parts: '
                f'leave `part` out, or use part={profile.parts[0]["part"]}.')
    rows = []
    for p in profile.parts:
        nums = [c['chapter'] for c in profile.chapters if c['part'] == p['part']]
        rows.append(f"{p['label'] or 'Untitled part'} = part {p['part']}: chapters {min(nums)}-{max(nums)}")
    return 'Parts, as the tools number them: ' + '; '.join(rows) + '.'


def instructions(profile):
    by = f' by {profile.author}' if profile.author else ''
    names = ', '.join(profile.names[:25])
    return f"""\
Retrieval over the book {profile.title!r}{by}.
{describe_structure(profile)}
{f'Names as this edition spells them; search with these forms: {names}.' if names else ''}

Typical use: search_passages for facts; search_quotes for what someone said; search_chapter_summaries
to locate where something happens; get_surrounding_text to read more around a hit. Cite results by
their `citation` field.
"""


class Passage(BaseModel):
    id: str = Field(description='Pass to get_surrounding_text to read more around this passage.')
    citation: str
    part: int
    chapter: int
    text: str
    score: float | None = None


class ChapterSummary(BaseModel):
    citation: str
    part: int
    chapter: int
    summary: str = Field(description='Machine-written; confirm details with search_passages.')
    score: float | None = None


class ChapterInfo(BaseModel):
    citation: str
    part: int
    chapter: int
    title: str | None = None
    passages: int


def _citation(hit):
    return f"{hit['label']}, passage {hit['seq'] + 1}" if hit['kind'] != 'summary' else hit['label']


def _passage(hit):
    score = round(hit['score'], 3) if 'score' in hit else None
    return Passage(id=str(hit['id']), citation=_citation(hit), part=hit['part'], chapter=hit['chapter'],
                   text=hit['text'], score=score)


def _summary(hit):
    score = round(hit['score'], 3) if 'score' in hit else None
    return ChapterSummary(citation=hit['label'], part=hit['part'], chapter=hit['chapter'],
                          summary=hit['text'], score=score)


def query_words(query):
    """The words of a query worth looking for in the text: no question or function words."""
    return list(dict.fromkeys(w.casefold() for w, _ in words(query) if len(w) > 2 and w.casefold() not in COMMON))


def _library_texts(profile):
    from marginalia.library import Library
    return {(c.part, c.chapter): c.text for c in Library().chapters(profile.book_id)}


def build_server(profile, retriever_factory: Callable[[], Retriever] | None = None,
                 chapter_texts: Callable[[], dict] | None = None) -> FastMCP:
    """Tools for one book. `retriever_factory` is called once, on first use, so startup never waits
    on models or Qdrant; by default it opens the book's live index version. `chapter_texts()` gives
    {(part, chapter): text} for the wrong-chapter hint; by default the library's chapters."""
    retriever_factory = retriever_factory or (lambda: Retriever.for_book(profile))
    chapter_texts = chapter_texts or (lambda: _library_texts(profile))
    lock, cache, texts = threading.Lock(), [], []
    chapters_in = {}
    for c in profile.chapters:
        chapters_in.setdefault(c['part'], []).append(c['chapter'])
    only_part = profile.parts[0]['part'] if len(profile.parts) == 1 else None
    max_part = max(chapters_in) if chapters_in else 1
    max_chapter = max((n for ns in chapters_in.values() for n in ns), default=1)

    def retriever() -> Retriever:
        with lock:
            if not cache:
                cache.append(retriever_factory())
            return cache[0]

    def folded_texts():
        with lock:
            if not texts:
                try:
                    texts.append({k: fold(v).casefold() for k, v in chapter_texts().items()})
                except Exception:              # no chapter files: no hint, searches still work
                    texts.append({})
            return texts[0]

    labels = {(c['part'], c['chapter']): c['label'] for c in profile.chapters}

    def check_mentions(query, part, chapter):
        """Raise if the chapter searched never mentions any word of the query, naming the chapters
        that do. A query with no content words, or a book without chapter files, passes."""
        found = query_words(query)
        texts_by_chapter = folded_texts()
        if chapter is None or not found or (part, chapter) not in texts_by_chapter:
            return
        patterns = [re.compile(r'\b' + re.escape(fold(w))) for w in found]
        if any(p.search(texts_by_chapter[(part, chapter)]) for p in patterns):
            return
        counts = sorted(((sum(len(p.findall(t)) for p in patterns), key) for key, t in texts_by_chapter.items()),
                        key=lambda x: -x[0])
        where = [f'{labels.get(key, key)} ({n}x)' for n, key in counts[:4] if n]
        said = ', '.join(f'"{w}"' for w in found)
        here = labels.get((part, chapter), f'Chapter {chapter}')
        raise ToolError(f'{here} never mentions {said}. ' + (
            f'They appear in: {"; ".join(where)}. Search one of those chapters, or leave out `chapter`.'
            if where else 'No chapter does: search with other words.'))

    def location(part, chapter):
        """Validated (part, chapter); a book without parts needs no part."""
        if part is None and chapter is not None:
            if only_part is None:
                raise ToolError('`chapter` needs `part` as well: chapter numbers restart in every part.')
            part = only_part
        if part is not None and part not in chapters_in:
            raise ToolError(f'There is no part {part}. {describe_structure(profile)}')
        if chapter is not None and chapter not in chapters_in[part]:
            label = next(p['label'] for p in profile.parts if p['part'] == part) or 'The book'
            nums = chapters_in[part]
            raise ToolError(f'{label} has chapters {min(nums)}-{max(nums)}.')
        return part, chapter

    Query = Annotated[str, Field(min_length=2, max_length=500, description='What to look for, in plain words.')]
    Part = Annotated[int | None, Field(ge=0, le=max_part, description='Only search this part (see the server '
                                                                      'instructions for how parts are numbered).')]
    ChapterNo = Annotated[int | None, Field(ge=0, le=max_chapter, description='Only search this chapter.')]
    Limit = Annotated[int, Field(ge=1, le=10)]

    mcp = FastMCP(profile.book_id, instructions=instructions(profile))
    mcp.warm_up = retriever

    @mcp.tool
    def search_passages(query: Query, part: Part = None, chapter: ChapterNo = None, limit: Limit = 5) -> list[Passage]:
        """Search the full text of the book for passages about events, places, objects or details.
        The best first tool for most factual questions. Narrow with part/chapter once you know where to look."""
        part, chapter = location(part, chapter)
        check_mentions(query, part, chapter)
        return [_passage(h) for h in retriever().search(query, 'chunk', part, chapter, limit)]

    @mcp.tool
    def search_quotes(query: Query, part: Part = None, chapter: ChapterNo = None, limit: Limit = 5) -> list[Passage]:
        """Search the characters' spoken lines of dialogue. Use for what someone said or to find who said
        something. Quotes do not name the speaker; read get_surrounding_text to see who is talking."""
        part, chapter = location(part, chapter)
        check_mentions(query, part, chapter)
        return [_passage(h) for h in retriever().search(query, 'quote', part, chapter, limit)]

    @mcp.tool
    def search_chapter_summaries(query: Query, part: Part = None, limit: Limit = 3) -> list[ChapterSummary]:
        """Search short summaries of every chapter. Use for broad questions (what happens to someone,
        how the plot develops) and to find which chapter an event is in before searching passages."""
        part, _ = location(part, None)
        return [_summary(h) for h in retriever().search(query, 'summary', part, None, limit)]

    @mcp.tool
    def get_chapter_summary(chapter: Annotated[int, Field(ge=0, le=max_chapter)],
                            part: Part = None) -> ChapterSummary:
        """Get the summary of one chapter by its chapter number (and part, if the book has parts)."""
        part, chapter = location(part, chapter)
        hits = retriever().fetch(kind='summary', part=part, chapter=chapter)
        if not hits:
            raise ToolError(f'No summary is indexed for part {part}, chapter {chapter}.')
        return _summary(hits[0])

    @mcp.tool
    def get_surrounding_text(passage_id: Annotated[str, Field(description='`id` from a search result.')],
                             before: Annotated[int, Field(ge=0, le=3)] = 1,
                             after: Annotated[int, Field(ge=0, le=3)] = 1) -> list[Passage]:
        """Read the book text around a search result: the passage itself plus the ones just before and
        after it, in reading order. Works for ids from search_passages and search_quotes."""
        r = retriever()
        try:
            hit = r.get(passage_id)
        except Exception:      # malformed ids are rejected by Qdrant itself
            hit = None
        if hit is None or hit['kind'] == 'summary':
            raise ToolError(f'No passage with id {passage_id!r}; use an id from search_passages or search_quotes.')
        chunks = r.fetch(kind='chunk', chapter_index=hit['chapter_index'])
        if hit['kind'] == 'quote':
            # Centre on the chunk where the quote starts.
            centre = next((c['seq'] for c in chunks if c['char_start'] <= hit['char_start'] < c['char_end']), 0)
        else:
            centre = hit['seq']
        return [_passage(c) for c in chunks if centre - before <= c['seq'] <= centre + after]

    @mcp.tool
    def list_chapters() -> list[ChapterInfo]:
        """List every chapter of the book with its part, chapter number, title and number of passages."""
        counts = {}
        for c in retriever().fetch(kind='chunk'):
            counts[c['chapter_index']] = counts.get(c['chapter_index'], 0) + 1
        return [ChapterInfo(citation=c['label'], part=c['part'], chapter=c['chapter'], title=c.get('title'),
                            passages=counts.get(c['index'], 0)) for c in profile.chapters]

    return mcp
