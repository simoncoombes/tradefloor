"""API keys: generation, hashing at rest, revocation, and the accounts file."""

from __future__ import annotations

import json
import os
import stat

import pytest

from tradefloor.serve.hosted import keys as K
from tradefloor.serve.hosted.accounts import Accounts
from tradefloor.serve.types import ServeError


def test_key_shape_and_parse():
    key = K.format_key(K.new_key_id(), K.new_secret())
    assert key.startswith("tfk_")
    parsed = K.parse_key(key)
    assert parsed is not None and len(parsed.key_id) == 12 and len(parsed.secret) >= 43
    assert K.parse_key("tfk_nothex000000_" + "a" * 43) is None
    assert K.parse_key("sk-live-whatever") is None
    assert K.parse_key("tfk_0123456789ab_short") is None


def test_secrets_are_random():
    assert len({K.new_secret() for _ in range(200)}) == 200
    assert len({K.new_key_id() for _ in range(200)}) == 200


def test_hash_is_salted_and_peppered():
    s = K.new_secret()
    a, b = K.new_salt(), K.new_salt()
    assert K.hash_secret(s, a, b"p") != K.hash_secret(s, b, b"p")
    assert K.hash_secret(s, a, b"p") != K.hash_secret(s, a, b"q")
    h = K.hash_secret(s, a, b"p")
    assert K.verify_secret(s, a, h, b"p")
    assert not K.verify_secret(s + "x", a, h, b"p")


def test_plaintext_is_shown_once_and_never_stored(tmp_path):
    acc = Accounts(tmp_path / "accounts.json", pepper=b"pep")
    rec, plaintext = acc.create_key("acme", plan="standard", label="bot 1")
    parsed = K.parse_key(plaintext)
    text = (tmp_path / "accounts.json").read_text()
    assert parsed.secret not in text
    assert plaintext not in text
    raw = json.loads(text)
    stored = raw["keys"][rec.key_id]
    assert set(stored) >= {"salt", "hash", "owner"} and stored["owner"] == "acme"
    assert raw["owners"]["acme"]["plan"] == "standard"
    # the file holding hashes is readable by its owner only
    assert stat.S_IMODE(os.stat(tmp_path / "accounts.json").st_mode) == 0o600
    # the public view never carries the hash
    assert "hash" not in rec.public() and "salt" not in rec.public()


def test_authenticate(tmp_path):
    acc = Accounts(tmp_path / "a.json", pepper=b"pep")
    rec, key = acc.create_key("acme")
    assert acc.authenticate(key) == ("acme", rec.key_id)
    assert acc.authenticate("  " + key + "\n") == ("acme", rec.key_id)
    for bad in (None, "", "Bearer x", key[:-1] + ("A" if key[-1] != "A" else "B")):
        with pytest.raises(ServeError) as e:
            acc.authenticate(bad)
        assert e.value.code == "unauthorized"
    # a different pepper cannot verify the same stored hash
    other = Accounts(tmp_path / "a.json", pepper=b"another")
    with pytest.raises(ServeError):
        other.authenticate(key)


def test_revocation_reaches_a_running_server_without_restart(tmp_path):
    server = Accounts(tmp_path / "a.json", pepper=b"pep")
    rec, key = server.create_key("acme")
    server.create_key("acme")  # a second key, which must keep working
    assert server.authenticate(key)[0] == "acme"
    cli = Accounts(tmp_path / "a.json", pepper=b"pep")   # a second process
    cli.revoke_key(rec.key_id)
    with pytest.raises(ServeError) as e:
        server.authenticate(key)
    assert e.value.code == "unauthorized" and "revoked" in e.value.message
    assert [k.key_id for k in server.keys("acme") if k.active] != [rec.key_id]


def test_revoke_all_and_suspend(tmp_path):
    acc = Accounts(tmp_path / "a.json", pepper=b"pep")
    _, k1 = acc.create_key("acme")
    _, k2 = acc.create_key("acme")
    _, k3 = acc.create_key("other")
    assert len(acc.revoke_all("acme")) == 2
    for k in (k1, k2):
        with pytest.raises(ServeError):
            acc.authenticate(k)
    assert acc.authenticate(k3)[0] == "other"
    acc.set_suspended("other")
    with pytest.raises(ServeError) as e:
        acc.authenticate(k3)
    assert "suspended" in e.value.message
    acc.set_suspended("other", False)
    assert acc.authenticate(k3)[0] == "other"


def test_owner_names_and_plans_are_validated(tmp_path):
    acc = Accounts(tmp_path / "a.json")
    for bad in ("", "Acme", "../etc", "a b", "x" * 64):
        with pytest.raises(ValueError):
            acc.create_key(bad)
    with pytest.raises(ValueError):
        acc.create_key("acme", plan="platinum")
    acc.create_key("acme")
    assert acc.owner("acme").plan == "trial"
    acc.set_plan("acme", "research")
    assert acc.plan_of("acme").name == "research"
    with pytest.raises(ValueError):
        acc.set_plan("acme", "nope")
