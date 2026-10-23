import argparse
import logging
import os
import threading

from marginalia import logs
from marginalia.library import Library
from marginalia.mcp_server.server import build_server


def main():
    p = argparse.ArgumentParser(description='Retrieval MCP server for one book of the library.')
    p.add_argument('--book', default=os.environ.get('CP_BOOK'), required='CP_BOOK' not in os.environ,
                   help='book id (or a unique prefix or title fragment); default: $CP_BOOK')
    p.add_argument('--transport', choices=['stdio', 'http'], default='stdio')
    p.add_argument('--host', default='127.0.0.1')
    p.add_argument('--port', type=int, default=8765)
    args = p.parse_args()
    logs.setup('mcp')          # stderr only: on stdio, stdout carries the protocol

    server = build_server(Library().resolve(args.book))

    # Load e5 and connect to Qdrant in the background so the first tool call is fast;
    # a failure here surfaces again, as a tool error, on that first call.
    def warm():
        try:
            server.warm_up()
        except Exception as exc:
            logging.getLogger(__name__).warning('warm-up failed: %s', exc)
    threading.Thread(target=warm, daemon=True).start()

    if args.transport == 'stdio':
        server.run('stdio', show_banner=False)
    else:
        server.run('http', host=args.host, port=args.port, show_banner=False)


if __name__ == '__main__':
    main()
