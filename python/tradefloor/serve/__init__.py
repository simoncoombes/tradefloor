"""A trading session server for long-running agents.

See docs/serve/CONTRACT.md. The package imports nothing heavy at import time;
each layer (core, store, mcp, http, hosted) is its own module.
"""

from tradefloor.serve.types import CONTRACT_VERSION, ServeError, SessionService  # noqa: F401
