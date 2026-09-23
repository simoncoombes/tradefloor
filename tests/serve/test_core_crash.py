"""A real crash: SIGKILL a process that is trading, then resume from its files.

The resumed session must be exactly the state its own call log says it
committed last, and a twin that replays that log from scratch must reach the
same state_hash and then move in lockstep with it.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import time

import pytest

from tradefloor.serve.core import LocalSessionService
from tradefloor.serve.store import FileStore, MemoryStore
from tradefloor.serve.types import OrderRequest, SessionConfig

OWNER = "local"
CFG = SessionConfig(seed=11, universe_size=6, ticks_per_step=30)

CHILD = textwrap.dedent("""
    import sys
    from tradefloor.serve.core import LocalSessionService
    from tradefloor.serve.store import FileStore
    from tradefloor.serve.types import OrderRequest
    root, sid = sys.argv[1], sys.argv[2]
    svc = LocalSessionService(FileStore(root))
    tickers = svc.info("local", sid).tickers
    k = 0
    while True:
        t = tickers[k % len(tickers)]
        side = "buy" if (k // len(tickers)) % 2 == 0 else "sell"
        svc.place_order("local", sid, OrderRequest(ticker=t, side=side, quantity=25))
        svc.advance("local", sid, 1 + k % 3)
        k += 1
        print(k, flush=True)
""")


def replay(service: LocalSessionService, session_id: str, calls: list[dict]) -> None:
    """Apply a call log (after its `open`) to another session."""
    for call in calls[1:]:
        args = call["args"]
        if call["op"] == "place_order":
            service.place_order(OWNER, session_id, OrderRequest(**args["request"]))
        elif call["op"] == "advance":
            service.advance(OWNER, session_id, args["steps"], until=args["until"])
        else:  # pragma: no cover - the child makes no other calls
            raise AssertionError(call["op"])


@pytest.mark.parametrize("wait_ms", [0, 7, 23])
def test_sigkill_mid_run_resumes_to_the_last_commit(tmp_path, wait_ms):
    sid = LocalSessionService(FileStore(tmp_path)).open(OWNER, CFG).session_id
    env = dict(os.environ)
    proc = subprocess.Popen([sys.executable, "-c", CHILD, str(tmp_path), sid],
                            stdout=subprocess.PIPE, text=True, env=env)
    try:
        assert proc.stdout is not None
        for _ in range(12):                      # let it get going
            assert proc.stdout.readline(), "child died early"
        time.sleep(wait_ms / 1000)
        os.kill(proc.pid, signal.SIGKILL)
    finally:
        proc.wait(timeout=30)
    assert proc.returncode == -signal.SIGKILL

    resumed = LocalSessionService(FileStore(tmp_path))
    obs = resumed.observe(OWNER, sid)
    calls = resumed.calls(OWNER, sid)
    assert [c["seq"] for c in calls] == list(range(1, len(calls) + 1))
    assert len(calls) >= 1 + 2 * 12
    # The state on disk is the state the last logged call committed.
    assert calls[-1]["state_hash"] == obs.state_hash

    # A twin replaying the log from scratch reaches the same state...
    twin_svc = LocalSessionService(MemoryStore())
    twin = twin_svc.open(OWNER, CFG).session_id
    replay(twin_svc, twin, calls)
    assert twin_svc.observe(OWNER, twin).state_hash == obs.state_hash
    assert [f.to_dict() for f in twin_svc.fills(OWNER, twin)] == \
        [f.to_dict() for f in resumed.fills(OWNER, sid)]
    # ...and the two move in lockstep afterwards.
    t = obs.quotes[0].ticker
    for svc, s in ((resumed, sid), (twin_svc, twin)):
        svc.place_order(OWNER, s, OrderRequest(ticker=t, side="buy", quantity=40))
        svc.advance(OWNER, s, 4)
    assert resumed.observe(OWNER, sid).state_hash == \
        twin_svc.observe(OWNER, twin).state_hash
