"""Manage the library of books.

    python -m marginalia add book.pdf [--title T --author A] [--no-ingest] [--no-summaries]
    python -m marginalia ingest <book> [--force] [--no-summaries]   # run the steps the book needs
    python -m marginalia ingest --all                               # every book (what the daily job does)
    python -m marginalia list
    python -m marginalia show <book>
    python -m marginalia search <book> "query" [--kind chunk|quote|summary] [-k 5]
    python -m marginalia delete <book>
<book> is a book id, a unique id prefix or a title fragment.
"""

import argparse
import json
import sys
from dataclasses import asdict

from marginalia.ingest.pdf import PdfError
from marginalia.ingest.pipeline import Pipeline, StepFailed
from marginalia.library import DuplicateBook, Library, UnknownBook


def _row(b):
    parts = f"{len(b.parts)} part(s)" if b.has_parts else 'no parts'
    return f"{b.book_id:<52} {b.status:<10} {len(b.chapters):>3} ch, {parts:<11} {b.title}"


def main(argv=None):
    p = argparse.ArgumentParser(prog='python -m marginalia', description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
    a = sub.add_parser('add')
    a.add_argument('pdf')
    a.add_argument('--title')
    a.add_argument('--author')
    a.add_argument('--no-ingest', action='store_true')
    a.add_argument('--no-summaries', action='store_true')
    i = sub.add_parser('ingest')
    i.add_argument('book', nargs='?')
    i.add_argument('--all', action='store_true')
    i.add_argument('--force', action='store_true')
    i.add_argument('--no-summaries', action='store_true')
    sub.add_parser('list')
    sub.add_parser('show').add_argument('book')
    s = sub.add_parser('search')
    s.add_argument('book')
    s.add_argument('query')
    s.add_argument('--kind', choices=['chunk', 'quote', 'summary'])
    s.add_argument('-k', '--limit', type=int, default=5)
    sub.add_parser('delete').add_argument('book')
    args = p.parse_args(argv)

    library = Library()
    try:
        if args.cmd == 'add':
            profile = library.add(args.pdf, title=args.title, author=args.author)
            print(f'added {profile.book_id}')
            if not args.no_ingest:
                Pipeline(library).run(profile.book_id, summaries=not args.no_summaries)
                print(_row(library.get(profile.book_id)))
        elif args.cmd == 'ingest':
            books = library.books() if args.all else [library.resolve(args.book)]
            pipeline, failed = Pipeline(library), 0
            for b in books:
                try:
                    ran = pipeline.run(b.book_id, force=args.force, summaries=not args.no_summaries)
                    print(f"{b.book_id}: {', '.join(ran) if ran else 'up to date'}")
                except StepFailed as exc:
                    failed += 1
                    print(f'{b.book_id}: FAILED {exc}', file=sys.stderr)
            return 1 if failed else 0
        elif args.cmd == 'list':
            for b in library.books():
                print(_row(b))
        elif args.cmd == 'show':
            print(json.dumps(asdict(library.resolve(args.book)), indent=1, ensure_ascii=False))
        elif args.cmd == 'search':
            from marginalia.index.retriever import Retriever
            retriever = Retriever.for_book(library.resolve(args.book))
            for hit in retriever.search(args.query, args.kind, limit=args.limit):
                print(f"[{hit['score']:.3f}] {hit['kind']:<7} {hit['label']}  #{hit['seq'] + 1}")
                print('   ', hit['text'][:300].replace('\n', ' '), '\n')
        elif args.cmd == 'delete':
            book = library.resolve(args.book)
            Pipeline(library).delete(book.book_id)
            print(f'deleted {book.book_id}')
    except DuplicateBook as exc:
        print(f'{exc} (code: duplicate)', file=sys.stderr)
        return 2
    except PdfError as exc:
        print(f'{exc} (code: {exc.code})', file=sys.stderr)
        return 2
    except StepFailed as exc:
        print(f'FAILED {exc} (code: {exc.code})', file=sys.stderr)
        return 1
    except UnknownBook as exc:
        print(f'unknown book: {exc}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    from marginalia import logs
    logs.setup('cli')
    sys.exit(main())
