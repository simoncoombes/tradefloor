"""`python -m tradefloor.serve`: the trading session server over HTTP.

    python -m tradefloor.serve                 # HTTP on 127.0.0.1:8765, MCP at /mcp
    python -m tradefloor.serve --port 9000 --store ./sessions
    python -m tradefloor.serve mcp             # the MCP server over stdio instead

The HTTP server has NO authentication: every request acts as one owner
("local" unless --owner says otherwise). That is right for a server on your
own machine and wrong for anything else, so binding beyond loopback prints a
warning. Bound to 127.0.0.1 or localhost, it refuses requests whose Host header
is anything else, so a web page cannot reach it through DNS rebinding. The
hosted layer supplies authentication through `create_app`'s `owner_resolver`.

Needs FastAPI and uvicorn (the `serve` extra); the MCP server needs `mcp`.
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> None:
    args_in = list(sys.argv[1:] if argv is None else argv)
    if args_in[:1] == ["mcp"]:
        from tradefloor.serve.mcp import main as mcp_main
        mcp_main(args_in[1:])
        return

    from tradefloor.serve.http import DEFAULT_HOST, DEFAULT_PORT, LOCAL_OWNER

    parser = argparse.ArgumentParser(
        prog="python -m tradefloor.serve",
        description="The tradefloor trading session server: native HTTP API under /v1, "
                    "an Alpaca-shaped broker facade under /broker/{session_id}/v2, MCP over "
                    "streamable HTTP at /mcp, OpenAPI docs at /docs. `python -m "
                    "tradefloor.serve mcp` runs the MCP server over stdio instead.")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Bind address (default {DEFAULT_HOST}).")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port (default {DEFAULT_PORT}).")
    parser.add_argument("--store", default=None,
                        help="Session store directory (default ~/.tradefloor/sessions).")
    parser.add_argument("--owner", default=LOCAL_OWNER,
                        help="The owner every request acts as (default 'local').")
    parser.add_argument("--service", default=None, metavar="MODULE:FACTORY",
                        help="Serve the SessionService a zero-argument factory returns, "
                             "instead of the local one over --store.")
    parser.add_argument("--no-mcp", action="store_true", help="Do not serve MCP at /mcp.")
    parser.add_argument("--log-level", default="info",
                        choices=["critical", "error", "warning", "info", "debug"])
    args = parser.parse_args(args_in)

    try:
        import uvicorn
    except ImportError:
        raise SystemExit("The HTTP server needs FastAPI and uvicorn, which tradefloor does not "
                         "depend on by default:\n\n    pip install 'tradefloor[serve]'\n") from None

    from tradefloor.serve.http import create_app, default_service, is_loopback, loopback_guard
    from tradefloor.serve.mcp import _load_factory

    service = _load_factory(args.service)() if args.service else default_service(args.store)
    owner = args.owner
    app = create_app(service, owner_resolver=lambda request: owner,
                     middleware=loopback_guard(args.host),
                     mcp_path=None if args.no_mcp else "/mcp")

    if not is_loopback(args.host):
        print(f"WARNING: binding {args.host} with no authentication. Everyone who can reach "
              f"this port can open sessions and trade as owner {owner!r}.", file=sys.stderr)
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)


if __name__ == "__main__":
    main()
