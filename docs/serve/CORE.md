MILESTONE 1 pushed aa6eabf

# The session core: LocalSessionService and FileStore

Branch `feat/serve-core`. Work in progress; the full document follows once
the core is hardened.

## For the transport and hosted agents: switching from fakes

```python
from tradefloor.serve.core import LocalSessionService
from tradefloor.serve.store import FileStore, MemoryStore

service = LocalSessionService(FileStore(root))   # or MemoryStore() in tests
info = service.open("local", SessionConfig())
```

`LocalSessionService(store=None, *, max_universe=40, max_advance_sessions=20,
headlines=None, cache_size=64)`. `store=None` means `FileStore()` at
`~/.tradefloor/sessions`. Every method of `SessionService` is implemented and
raises `ServeError` with the contract's codes. Two extras: `calls(owner,
session_id)` returns the append-only call log and `caveats(owner,
session_id)` the caveats a report would carry now.

`SessionStore` (in `store.py`) is `commit(session_id, record, appends)`,
`load`, `read_stream`, `version`, `heads(owner)`; a hosted store implements
those five.
