"""S3Store against a local in-memory fake of S3 (never real S3): the
SessionStore protocol, bit-exact floats, crash safety at every PUT, the
conditional-write guard against a second writer, and stream compaction."""

from __future__ import annotations

import io
import math
import struct
import threading

import pytest

pytest.importorskip("tradefloor.serve.store")

from tradefloor.serve.hosted.s3store import S3Store, StoreConflict  # noqa: E402


class ClientError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeS3:
    """The five calls S3Store makes, with S3's semantics: atomic PUTs, strong
    read-after-write, ETags, If-Match / If-None-Match, paged listing."""

    def __init__(self, page: int = 3) -> None:
        self.objects: dict[tuple[str, str], tuple[bytes, str]] = {}
        self.page = page
        self.n = 0
        self.counts = {"put": 0, "get": 0, "delete": 0, "list": 0}
        self.fail_put: callable | None = None
        self._lock = threading.Lock()

    def put_object(self, *, Bucket, Key, Body, ContentType=None, IfMatch=None, IfNoneMatch=None):
        with self._lock:
            self.counts["put"] += 1
            if self.fail_put and self.fail_put(Key):
                raise ConnectionError(f"simulated crash before PUT {Key} landed")
            cur = self.objects.get((Bucket, Key))
            if IfNoneMatch == "*" and cur is not None:
                raise ClientError("PreconditionFailed")
            if IfMatch is not None and (cur is None or cur[1] != IfMatch):
                raise ClientError("PreconditionFailed")
            self.n += 1
            etag = f'"{self.n:08x}"'
            self.objects[(Bucket, Key)] = (bytes(Body), etag)
            return {"ETag": etag}

    def get_object(self, *, Bucket, Key):
        with self._lock:
            self.counts["get"] += 1
            cur = self.objects.get((Bucket, Key))
            if cur is None:
                raise ClientError("NoSuchKey")
            return {"Body": io.BytesIO(cur[0]), "ETag": cur[1]}

    def delete_object(self, *, Bucket, Key):
        with self._lock:
            self.counts["delete"] += 1
            self.objects.pop((Bucket, Key), None)
            return {}

    def list_objects_v2(self, *, Bucket, Prefix, ContinuationToken=None):
        with self._lock:
            self.counts["list"] += 1
            keys = sorted(k for b, k in self.objects if b == Bucket and k.startswith(Prefix))
            start = int(ContinuationToken or 0)
            chunk = keys[start:start + self.page]
            out = {"Contents": [{"Key": k, "Size": len(self.objects[(Bucket, k)][0])} for k in chunk],
                   "IsTruncated": start + self.page < len(keys)}
            if out["IsTruncated"]:
                out["NextContinuationToken"] = str(start + self.page)
            return out


NAN_PAYLOAD = struct.unpack("<d", bytes.fromhex("0100000000f87f7f"))[0]


def rec(seq, owner="alice", **extra):
    return {"seq": seq, "head": {"owner": owner, "status": "open"},
            "floats": [NAN_PAYLOAD, -0.0, math.inf, -math.inf, 0.1], "raw": b"\x00\xff", **extra}


def bits(x):
    return struct.pack("<d", x)


def store(fake=None, **kw):
    fake = fake or FakeS3()
    return S3Store("bucket", "tf/prod", client=fake, **kw), fake


def test_it_is_a_session_store():
    from tradefloor.serve.types import SessionStore
    assert isinstance(store()[0], SessionStore)


def test_round_trip_is_bit_exact():
    s, _ = store()
    s.commit("s1", rec(1), {"fills": [{"px": 1.5}], "calls": [{"c": "open"}]})
    s.commit("s1", rec(2), {"calls": [{"c": "advance", "x": NAN_PAYLOAD}]})
    got = s.load("s1")
    assert [bits(x) for x in got["floats"]] == [bits(x) for x in rec(2)["floats"]]
    assert got["raw"] == b"\x00\xff" and got["seq"] == 2
    assert s.read_stream("s1", "fills") == [{"px": 1.5}]
    calls = s.read_stream("s1", "calls")
    assert calls[0] == {"c": "open"} and bits(calls[1]["x"]) == bits(NAN_PAYLOAD)
    assert s.version("s1") == 2 and s.version("nope") is None and s.load("nope") is None
    assert s.read_stream("s1", "orders") == [] and s.read_stream("bad/id", "fills") == []


def test_a_fresh_process_reads_what_was_committed():
    s, fake = store()
    s.commit("s1", rec(1), {"fills": [{"a": 1}]})
    s.commit("s1", rec(2), {"fills": [{"a": 2}]})
    s2, _ = store(fake)        # a restarted server, empty cache
    assert s2.version("s1") == 2
    assert s2.read_stream("s1", "fills") == [{"a": 1}, {"a": 2}]
    # the superseded record object is deleted after the commit point
    assert sum(1 for _, k in fake.objects if "/record-" in k) == 1


@pytest.mark.parametrize("crash_on", ["/stream-fills-000000000002-", "/record-000000000002-",
                                      "/head.json"])
def test_a_crash_at_any_put_leaves_the_last_commit_whole(crash_on):
    s, fake = store()
    s.commit("s1", rec(1), {"fills": [{"a": 1}]})
    fake.fail_put = lambda key: crash_on in key
    with pytest.raises(ConnectionError):
        s.commit("s1", rec(2, marker="new"), {"fills": [{"a": 2}]})
    fake.fail_put = None
    for reader in (s, store(fake)[0]):
        assert reader.version("s1") == 1
        assert "marker" not in reader.load("s1")
        assert reader.read_stream("s1", "fills") == [{"a": 1}]
    # the retried commit lands whole, and the orphan entries never appear
    s.commit("s1", rec(2, marker="new"), {"fills": [{"a": 2}]})
    fresh = store(fake)[0]
    assert fresh.read_stream("s1", "fills") == [{"a": 1}, {"a": 2}]
    assert fresh.load("s1")["marker"] == "new"


def test_a_second_writer_is_refused_not_interleaved():
    fake = FakeS3()
    a, _ = store(fake)
    b, _ = store(fake)
    a.commit("s1", rec(1), {})
    b.commit("s1", rec(2), {"fills": [{"from": "b"}]})
    with pytest.raises(StoreConflict):
        a.commit("s1", rec(2), {"fills": [{"from": "a"}]})
    assert store(fake)[0].read_stream("s1", "fills") == [{"from": "b"}]
    # two first commits racing: d read "no head yet" just before c's landed.
    # If-None-Match on d's head PUT catches it.
    c, _ = store(fake)
    d, _ = store(fake)
    d._read_head = lambda sid, fresh=False: None
    c.commit("s2", rec(1, marker="c"), {})
    with pytest.raises(StoreConflict):
        d.commit("s2", rec(1, marker="d"), {})
    assert store(fake)[0].load("s2")["marker"] == "c"


def test_compaction_keeps_reads_short_and_gc_tidies():
    s, fake = store(compact_every=4)
    for i in range(1, 11):
        s.commit("s1", rec(i), {"calls": [{"i": i}]})
    fresh, _ = store(fake)
    before = fake.counts["get"]
    assert [e["i"] for e in fresh.read_stream("s1", "calls")] == list(range(1, 11))
    assert fake.counts["get"] - before <= 1 + 1 + 4     # head, base, < compact_every chunks
    assert fresh.gc("s1") > 0
    assert [e["i"] for e in store(fake)[0].read_stream("s1", "calls")] == list(range(1, 11))


def test_heads_by_owner_and_a_half_created_session():
    s, fake = store()
    s.commit("a1", rec(1, "alice"), {})
    s.commit("a2", rec(1, "alice"), {})
    s.commit("b1", rec(1, "bob"), {})
    fake.fail_put = lambda key: key.endswith("a3/head.json")
    with pytest.raises(ConnectionError):
        s.commit("a3", rec(1, "alice"), {})
    fake.fail_put = None
    for reader in (s, store(fake)[0]):
        assert sorted(h["owner"] for h in reader.heads("alice")) == ["alice", "alice"]
        assert [h["owner"] for h in reader.heads("bob")] == ["bob"]
        assert reader.heads("carol") == []


def test_size_of_meters_storage():
    s, _ = store()
    s.commit("s1", rec(1), {"fills": [{"a": "x" * 1000}]})
    assert s.size_of(["s1"]) > 1000
    assert s.size_of([]) == 0


def test_the_core_service_runs_on_s3store_and_resumes_bit_for_bit():
    core = pytest.importorskip("tradefloor.serve.core")
    from tradefloor.serve.types import OrderRequest, SessionConfig

    fake = FakeS3(page=1000)
    svc = core.LocalSessionService(S3Store("b", "p", client=fake))
    info = svc.open("alice", SessionConfig(universe_size=5, ticks_per_step=65))
    t = info.tickers[0]
    svc.place_order("alice", info.session_id, OrderRequest(t, "buy", 100))
    svc.advance("alice", info.session_id, steps=3)
    before = svc.observe("alice", info.session_id)
    resumed = core.LocalSessionService(S3Store("b", "p", client=fake))   # a new process
    after = resumed.observe("alice", info.session_id)
    assert after.to_dict() == before.to_dict()
    assert after.state_hash == before.state_hash
    assert [s.session_id for s in resumed.list("alice")] == [info.session_id]
    assert resumed.list("bob") == []


def test_against_moto_with_a_real_botocore_client(monkeypatch):
    """The same guarantees through boto3 and botocore's real error shapes,
    against moto's local S3 (still never real S3)."""
    pytest.importorskip("boto3")
    moto = pytest.importorskip("moto")
    import boto3

    for k, v in {"AWS_ACCESS_KEY_ID": "test", "AWS_SECRET_ACCESS_KEY": "test",
                 "AWS_DEFAULT_REGION": "us-east-1"}.items():
        monkeypatch.setenv(k, v)
    with moto.mock_aws():
        client = boto3.client("s3")
        client.create_bucket(Bucket="tradefloor-test")
        a = S3Store("tradefloor-test", "hosted", client=client)
        b = S3Store("tradefloor-test", "hosted", client=client)
        a.commit("s1", rec(1), {"fills": [{"a": 1}]})
        assert b.version("s1") == 1
        a.commit("s1", rec(2), {"fills": [{"a": 2}]})
        with pytest.raises(StoreConflict):
            b.commit("s1", rec(2), {"fills": [{"b": 2}]})
        fresh = S3Store("tradefloor-test", "hosted", client=client)
        assert fresh.read_stream("s1", "fills") == [{"a": 1}, {"a": 2}]
        assert [bits(x) for x in fresh.load("s1")["floats"]] == [bits(x) for x in rec(2)["floats"]]
        assert fresh.load("missing") is None and fresh.version("missing") is None
        assert [h["owner"] for h in fresh.heads("alice")] == ["alice"]
