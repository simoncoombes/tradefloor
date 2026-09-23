"""`python -m tradefloor.serve` as a user runs it: a real process on a real port.

The crash-resume test is the one the contract is written for (section 5): a
server that dies mid-session and is restarted on the same store serves the
session exactly as it was, bit for bit, and carries on exactly as a server
that never died would have.
"""

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

httpx = pytest.importorskip("httpx")
pytest.importorskip("uvicorn", reason="the HTTP server is an opt-in extra")

from fakes import core_available, core_has  # noqa: E402
from tradefloor.serve.http import is_loopback as _is_loopback  # noqa: E402

CORE = pytest.mark.skipif(not core_available(), reason="tradefloor.serve.core has not landed")
HERE = Path(__file__).resolve().parent
SMALL = {"universe_size": 4, "ticks_per_step": 30, "seed": 3}


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    """One `python -m tradefloor.serve` process this test started and owns."""

    def __init__(self, *args, env=None):
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "tradefloor.serve", "--port", str(self.port),
             "--log-level", "warning", *args],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.time() + 60
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"server exited: {self.proc.stderr.read()}")
            try:
                if httpx.get(self.url + "/v1/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:
            self.stop()
            raise RuntimeError("server did not come up")
        self.http = httpx.Client(base_url=self.url, timeout=60)

    def kill(self):
        """A crash: SIGKILL, no shutdown hooks."""
        self.http.close()
        self.proc.send_signal(signal.SIGKILL)
        self.proc.wait(10)

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(10)


def test_loopback_detection():
    assert _is_loopback("127.0.0.1") and _is_loopback("::1") and _is_loopback("localhost")
    assert not _is_loopback("0.0.0.0") and not _is_loopback("192.168.1.10")


def test_the_module_serves_http():
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(HERE), os.environ.get("PYTHONPATH", "")]))
    server = Server("--service", "fakes:FakeSessionService", "--owner", "me", env=env)
    try:
        info = server.http.post("/v1/sessions", json=SMALL).json()
        assert info["owner"] == "me"
        assert server.http.get(f"/broker/{info['session_id']}/v2/clock").json()["is_open"] is True
        assert server.http.get("/openapi.json").status_code == 200
        assert server.http.get("/docs").status_code == 200
        # MCP is served beside the API.
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": "t", "version": "0"}}}
        r = server.http.post("/mcp", json=init,
                             headers={"Accept": "application/json, text/event-stream"})
        assert r.status_code == 200 and r.json()["result"]["serverInfo"]["name"] == "tradefloor-trading"
        # A page that rebinds a hostname to 127.0.0.1 sends its own Host header.
        assert server.http.get("/v1/health", headers={"Host": "evil.example"}).status_code == 400
        assert server.http.post("/mcp", json=init, headers={
            "Host": "evil.example", "Accept": "application/json, text/event-stream"}).status_code == 400
    finally:
        server.stop()


def test_the_mcp_module_serves_streamable_http():
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(HERE), os.environ.get("PYTHONPATH", "")]))
    port = _free_port()
    proc = subprocess.Popen([sys.executable, "-m", "tradefloor.serve.mcp", "--http", "--port",
                             str(port), "--service", "fakes:FakeSessionService"],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": "t", "version": "0"}}}
        deadline = time.time() + 60
        while True:
            try:
                r = httpx.post(f"http://127.0.0.1:{port}/mcp", json=init, timeout=5,
                               headers={"Accept": "application/json, text/event-stream"})
                break
            except httpx.HTTPError:
                assert proc.poll() is None and time.time() < deadline, proc.stderr.read()
                time.sleep(0.1)
        assert r.status_code == 200 and r.json()["result"]["serverInfo"]["name"] == "tradefloor-trading"
    finally:
        proc.terminate()
        proc.wait(10)


def test_the_module_hands_mcp_to_the_mcp_server():
    r = subprocess.run([sys.executable, "-m", "tradefloor.serve", "mcp", "--help"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "tradefloor.serve.mcp" in r.stdout


def _calls(http, sid, tickers):
    """The session's life before the crash: orders of every kind and time."""
    orders = f"/v1/sessions/{sid}/orders"
    advance = f"/v1/sessions/{sid}/advance"
    last = http.get(f"/v1/sessions/{sid}/observation").json()["quotes"][1]["last"]
    assert http.post(orders, json={"ticker": tickers[0], "side": "buy", "quantity": 25}).status_code == 201
    assert http.post(orders, json={"ticker": tickers[1], "side": "buy", "quantity": 5, "type": "limit",
                                   "limit_price": round(last * 0.5, 2), "time_in_force": "gtc",
                                   "client_order_id": "rest"}).status_code == 201
    assert http.post(advance, json={"steps": 3}).status_code == 200
    assert http.post(orders, json={"ticker": tickers[2], "side": "sell", "quantity": 8}).status_code == 201
    assert http.post(advance, json={"until": "close"}).status_code == 200
    assert http.post(orders, json={"ticker": tickers[0], "side": "sell", "quantity": 10}).status_code == 201


def _after(http, sid, tickers):
    """The session's life after the crash."""
    orders = f"/v1/sessions/{sid}/orders"
    advance = f"/v1/sessions/{sid}/advance"
    res = http.post(advance, json={"steps": 4}).json()
    again = http.post(orders, json={"ticker": tickers[1], "side": "buy", "quantity": 6,
                                    "type": "limit", "limit_price": 1.0, "time_in_force": "gtc",
                                    "client_order_id": "rest"})
    assert again.status_code == 409  # the idempotency key survived the crash
    return res["observation"]["state_hash"]


def _snapshot(http, sid, tickers):
    b = f"/broker/{sid}/v2"
    return {
        "info": http.get(f"/v1/sessions/{sid}").json(),
        "observation": http.get(f"/v1/sessions/{sid}/observation").json(),
        "orders": http.get(f"/v1/sessions/{sid}/orders").json(),
        "fills": http.get(f"/v1/sessions/{sid}/fills").json(),
        "account": http.get(f"{b}/account").json(),
        "positions": http.get(f"{b}/positions").json(),
        "broker_orders": http.get(f"{b}/orders", params={"status": "all"}).json(),
        "clock": http.get(f"{b}/clock").json(),
        **({"day_bars": http.get(f"{b}/stocks/bars", params={"symbols": ",".join(tickers),
                                                              "timeframe": "1Day",
                                                              "start": "2000-01-01"}).json(),
            "step_bars": [http.get(f"/v1/sessions/{sid}/bars/{t}",
                                   params={"resolution": "step"}).json() for t in tickers]}
           if core_has("bars") else {}),
    }


@CORE
def test_a_server_killed_mid_session_resumes_bit_for_bit(tmp_path):
    store = tmp_path / "sessions"
    first = Server("--store", str(store))
    try:
        info = first.http.post("/v1/sessions", json=SMALL).json()
        sid = info["session_id"]
        _calls(first.http, sid, info["tickers"])
        before = _snapshot(first.http, sid, info["tickers"])
    finally:
        first.kill()

    second = Server("--store", str(store))
    try:
        after = _snapshot(second.http, sid, info["tickers"])
        for key in before:
            assert after[key] == before[key], key
        resumed = _after(second.http, sid, info["tickers"])
    finally:
        second.stop()

    # A server that never died, given the same calls, ends in the same state.
    control = Server("--store", str(tmp_path / "control"))
    try:
        info = control.http.post("/v1/sessions", json=SMALL).json()
        _calls(control.http, info["session_id"], info["tickers"])
        assert _after(control.http, info["session_id"], info["tickers"]) == resumed
    finally:
        control.stop()
