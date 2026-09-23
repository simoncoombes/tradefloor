"""The admin CLI, offline against the files and online against a running
server's loopback admin listener."""

from __future__ import annotations

import io
import json
import socket
import threading
import time

import pytest

from tradefloor.serve.hosted import admin
from tradefloor.serve.hosted.config import Settings, build_hosted
from tradefloor.serve.hosted.keys import parse_key
from tradefloor.serve.hosted.testing import FakeSessionService
from tradefloor.serve.types import SessionConfig

PEPPER = "a-test-pepper-of-some-length"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADEFLOOR_HOSTED_DATA", str(tmp_path))
    monkeypatch.setenv("TRADEFLOOR_HOSTED_PEPPER", PEPPER)
    return tmp_path


def run(*argv, factory=None):
    buf = io.StringIO()
    code = admin.main(list(argv), out=buf, hosted_factory=factory)
    return code, buf.getvalue()


def test_create_key_shows_it_once_and_the_server_accepts_it(env):
    code, text = run("create-key", "acme", "--plan", "standard", "--label", "bot one")
    assert code == 0 and "only time the key is shown" in text
    key = next(w for w in text.split() if w.startswith("tfk_"))
    assert parse_key(key)
    hosted = build_hosted(Settings.from_env(), inner=FakeSessionService())
    assert hosted.authenticate(key) == "acme"
    code, text = run("--json", "list-keys")
    rows = json.loads(text)
    assert rows[0]["owner"] == "acme" and rows[0]["label"] == "bot one"
    assert key not in text and parse_key(key).secret not in text and "hash" not in rows[0]


def test_create_key_refuses_a_different_pepper(env, monkeypatch):
    assert run("create-key", "acme")[0] == 0
    monkeypatch.setenv("TRADEFLOOR_HOSTED_PEPPER", "a-different-pepper-entirely")
    assert run("create-key", "acme")[0] == 1


def test_create_key_needs_a_pepper(env, monkeypatch):
    monkeypatch.delenv("TRADEFLOOR_HOSTED_PEPPER")
    with pytest.raises(SystemExit):
        run("create-key", "acme")


def test_revoke_plan_suspend(env):
    _, text = run("--json", "create-key", "acme")
    made = json.loads(text)
    hosted = build_hosted(Settings.from_env(), inner=FakeSessionService())
    assert hosted.authenticate(made["api_key"]) == "acme"
    assert run("set-plan", "acme", "research")[0] == 0
    assert hosted.accounts.plan_of("acme").name == "research"
    assert run("set-plan", "acme", "gold")[0] == 1
    assert run("suspend", "acme")[0] == 0
    with pytest.raises(Exception):
        hosted.authenticate(made["api_key"])
    assert run("unsuspend", "acme")[0] == 0
    assert run("revoke-key", made["key_id"])[0] == 0
    with pytest.raises(Exception) as e:
        hosted.authenticate(made["api_key"])
    assert "revoked" in str(e.value)
    assert run("revoke-key", "000000000000")[0] == 1
    _, text = run("--json", "owners")
    assert json.loads(text)[0]["plan"] == "research"
    _, text = run("--json", "plans")
    assert set(json.loads(text)) == {"trial", "standard", "research"}


def test_usage_sessions_expire_and_audit_offline(env):
    fake = FakeSessionService()
    _, text = run("--json", "create-key", "acme")
    key = json.loads(text)["api_key"]
    hosted = build_hosted(Settings.from_env(), inner=fake)
    p = hosted.authenticate(key)
    s = hosted.open(p, SessionConfig(universe_size=3))
    hosted.advance(p, s.session_id, steps=2)
    hosted.stop()                                   # flushes the ledger
    _, text = run("--json", "usage", "acme")
    rep = json.loads(text)[0]
    assert rep["today"]["steps"] == 2 and rep["sessions"]["open"] == 1
    factory = lambda settings: build_hosted(settings, inner=fake)  # noqa: E731
    _, text = run("--json", "sessions", "acme", "--offline", factory=factory)
    assert [r["session_id"] for r in json.loads(text)] == [s.session_id]
    _, text = run("--json", "expire-idle", "--offline", factory=factory)
    assert json.loads(text) == {"expired": []}      # not idle yet
    _, text = run("audit", "--owner", "acme")
    calls = [json.loads(line)["call"] for line in text.splitlines()]
    assert calls == ["open", "advance"]


def _free_port() -> int:
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        return so.getsockname()[1]


def test_sessions_and_expire_idle_go_through_the_running_server(env):
    uvicorn = pytest.importorskip("uvicorn")
    from tradefloor.serve.hosted._files import atomic_write_json
    from tradefloor.serve.hosted.app import create_admin_app

    fake = FakeSessionService()
    _, text = run("--json", "create-key", "acme")
    key = json.loads(text)["api_key"]
    hosted = build_hosted(Settings.from_env(), inner=fake)
    p = hosted.authenticate(key)
    s = hosted.open(p, SessionConfig(universe_size=3))

    port = _free_port()
    atomic_write_json(env / "admin.json", {"port": port, "token": "tok-123"})
    server = uvicorn.Server(uvicorn.Config(create_admin_app(hosted, "tok-123"), host="127.0.0.1",
                                           port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        code, text = run("--json", "sessions", "acme")
        assert code == 0
        rows = json.loads(text)
        assert rows[0]["session_id"] == s.session_id and rows[0]["activity"]["status"] == "open"
        hosted.accounts.set_plan("acme", "trial")
        hosted.quotas._sessions[s.session_id].last_active -= 2 * 86400   # idle for two days
        code, text = run("--json", "expire-idle")
        assert json.loads(text) == {"expired": [s.session_id]}
        # a wrong token is refused
        atomic_write_json(env / "admin.json", {"port": port, "token": "wrong"})
        assert run("sessions", "acme")[0] == 1
    finally:
        server.should_exit = True
        t.join(timeout=5)
    (env / "admin.json").unlink()
    assert run("sessions", "acme")[0] == 1          # no server: a clear error, not a traceback
