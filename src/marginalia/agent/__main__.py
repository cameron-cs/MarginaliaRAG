"""Ask questions about a book in the library.

    python -m marginalia.agent --book crime "What did Sonia read to Raskolnikov?"
    python -m marginalia.agent --book crime -v "..."   # show the Thought / Action / Observation trace
    python -m marginalia.agent --book harry            # interactive
--book takes a book id, a unique id prefix or a title fragment; with one ready book it is optional.
"""

import argparse
import asyncio
import json
import textwrap

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from marginalia.agent.react_agent import book_agent, collect_evidence, message_text
from marginalia.library import Library


def _trace(m):
    if isinstance(m, AIMessage):
        thinking = (m.additional_kwargs or {}).get('reasoning_content', '')
        if thinking:
            print(f'  Thought: {textwrap.shorten(thinking, 300)}')
        for c in m.tool_calls:
            print(f"  Action: {c['name']}({json.dumps(c['args'], ensure_ascii=False)})")
    elif isinstance(m, ToolMessage):
        found = [e.citation for e in collect_evidence([m])]
        print(f"  Observation: {', '.join(found) if found else textwrap.shorten(message_text(m), 200)}")
    elif isinstance(m, HumanMessage):
        print(f'  > {textwrap.shorten(message_text(m), 200)}')


def _report(result):
    print('\n' + result.answer + '\n')
    verdict = {True: 'supported by retrieved text', False: 'NOT fully supported', None: 'not checked'}
    print(f'[{len(result.tool_calls)} tool calls, {result.revisions} revisions, '
          f'grounding: {verdict[result.supported]}]')
    for claim in result.unsupported_claims:
        print(f'  unsupported: {claim}')
    for c in result.unknown_citations:
        print(f'  citation not retrieved: [{c}]')


async def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('question', nargs='?')
    p.add_argument('--book')
    p.add_argument('-v', '--verbose', action='store_true')
    p.add_argument('--max-tool-calls', type=int, default=6)
    p.add_argument('--max-revisions', type=int, default=1)
    p.add_argument('--no-grade', action='store_true', help='skip the LLM grounding check (saves ~30s)')
    args = p.parse_args()

    options = {'max_tool_calls': args.max_tool_calls, 'max_revisions': args.max_revisions}
    if args.no_grade:
        options['grader'] = None
    library = Library()
    if args.book:
        profile = library.resolve(args.book)
    else:
        ready = [b for b in library.books() if b.status == 'ready']
        if len(ready) != 1:
            raise SystemExit(f'--book is needed: ready books are {[b.book_id for b in ready]}')
        profile = ready[0]
    async with book_agent(profile, **options) as agent:
        questions = [args.question] if args.question else iter(lambda: input('\nquestion> ').strip(), '')
        for q in questions:
            _report(await agent.ask(q, on_message=_trace if args.verbose else None))


if __name__ == '__main__':
    from marginalia import logs
    logs.setup('agent')
    asyncio.run(main())
