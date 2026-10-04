"""Populated mode: background traders sharing the book (`tradefloor.population`).

What is pinned here:

- Without a population nothing changes: an engine, an evaluation and a world
  built without one (or with ``population=None``) are the run they were, and
  the population takes no random draw, so a populated run consumes exactly
  the draws an isolated one does.
- A populated run is deterministic, its state is in the snapshot (the
  ``population`` key, required exactly when the engine holds a population)
  and both state hashes cover it, and a run snapshotted, restored in a fresh
  process and continued matches the uninterrupted run.
- The population trades: it moves the market, its fills stay on its own
  ledger, and an agent cannot use its labels.
- The detector learns a programme that trades the same minutes every day and
  positions ahead of it, and does not learn flow at random minutes as well.
- The API: the fingerprint, the scorecard's ``population_fingerprint``, a
  world's fork and manifest.
"""

import json
import pathlib
import random
import struct
import subprocess
import sys
import textwrap

import pytest

import tradefloor as tf
from tradefloor.manifest import state_hash
from tradefloor.population import Participant, Population

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import snapshot_codec  # noqa: E402

UNIVERSE = tf.Universe.random(8, seed=5)
POP = Population.standard()


def _engine(population=POP, seed=3):
    return tf.Engine(seed=seed, universe=UNIVERSE, model="pt-v20",
                     population=population)


def _day(engine, buy=None):
    """One session; `buy` sends 1% of a name's volume at ticks 0 and 200."""
    engine.open_market()
    if buy is not None:
        engine.submit("a", buy.ticker, 0.01 * buy.avg_volume)
    engine.run_session(9, 30, 3, 200)
    if buy is not None:
        engine.submit("a", buy.ticker, 0.01 * buy.avg_volume)
    engine.run_session(12, 50, 3, 190, close_at_end=True)
    engine.close_market()


def _prices(engine):
    raw = engine.prices()
    return struct.unpack("<%dd" % (len(raw) // 8), raw)


# --------------------------------------------------------------- isolated

def test_none_is_the_isolated_engine():
    a = tf.Engine(seed=3, universe=UNIVERSE, model="pt-v20")
    b = _engine(population=None)
    for _ in range(3):
        _day(a, UNIVERSE[1])
        _day(b, UNIVERSE[1])
    assert a.state_hash() == b.state_hash()
    assert "population" not in b.state_snapshot()
    assert b.population_fingerprint is None
    assert b.population_report() == []
    assert b.population_spec() is None


def test_the_population_takes_no_draw():
    iso, pop = _engine(None), _engine()
    for _ in range(4):
        _day(iso)
        _day(pop)
    assert iso.draws_by_stream() == pop.draws_by_stream()
    assert _prices(iso) != _prices(pop)


def test_an_isolated_evaluation_card_is_the_dict_it_was():
    cards = tf.evaluate({"m": tf.StrategySpec.momentum()}, seed=4,
                        universe=UNIVERSE, days=2)
    assert "population_fingerprint" not in cards["m"].as_dict()
    assert cards["m"].population_fingerprint == ""


# ------------------------------------------------------------- determinism

def test_the_same_population_gives_the_same_market():
    a, b = _engine(), _engine()
    for _ in range(4):
        _day(a, UNIVERSE[2])
        _day(b, UNIVERSE[2])
    assert a.state_hash() == b.state_hash()
    assert a.population_report() == b.population_report()
    assert sum(r["orders"] for r in a.population_report()) > 0


def test_the_snapshot_carries_the_population_and_both_hashes_cover_it():
    e = _engine()
    for _ in range(3):
        _day(e, UNIVERSE[2])
    snap = e.state_snapshot()
    assert set(snap["population"]) == {"fingerprint", "tickers", "state"}
    assert snap["population"]["fingerprint"] == POP.fingerprint
    assert state_hash(snap) == e.state_hash()
    edited = snapshot_codec.loads(snapshot_codec.dumps(snap))
    state = bytearray(edited["population"]["state"])
    state[-8:] = struct.pack("<d", 123.0)
    edited["population"]["state"] = bytes(state)
    assert state_hash(edited) != state_hash(snap)


def _restore_case(mid_day):
    e = _engine()
    for _ in range(3):
        _day(e, UNIVERSE[2])
    if mid_day:
        e.open_market()
        e.submit("a", UNIVERSE[2].ticker, 0.01 * UNIVERSE[2].avg_volume)
        e.run_session(9, 30, 3, 120)
    return e


def _finish(e, mid_day):
    if mid_day:
        e.submit("a", UNIVERSE[2].ticker, 0.01 * UNIVERSE[2].avg_volume)
        e.run_session(11, 30, 3, 270, close_at_end=True)
        e.close_market()
    for _ in range(2):
        _day(e, UNIVERSE[2])


@pytest.mark.parametrize("mid_day", [False, True])
def test_a_restore_in_a_fresh_process_continues_the_run(tmp_path, mid_day):
    e = _restore_case(mid_day)
    path = tmp_path / "snap.bin"
    path.write_bytes(snapshot_codec.dumps(e.state_snapshot()).encode()
                     if isinstance(snapshot_codec.dumps({}), str)
                     else snapshot_codec.dumps(e.state_snapshot()))
    _finish(e, mid_day)
    here = pathlib.Path(__file__).resolve().parent
    script = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(here)!r})
        import snapshot_codec, test_population as t
        raw = open({str(path)!r}, "rb").read()
        snap = snapshot_codec.loads(raw.decode() if isinstance(snapshot_codec.dumps({{}}), str) else raw)
        e = t._engine()
        e.restore_state(snap)
        t._finish(e, {mid_day})
        print(e.state_hash())
    """)
    out = subprocess.run([sys.executable, "-c", script], check=True,
                         capture_output=True, text=True).stdout.strip()
    assert out == e.state_hash()


def test_a_restore_needs_the_same_population():
    e = _engine()
    _day(e, UNIVERSE[2])
    snap = e.state_snapshot()
    with pytest.raises(tf.ValidationError, match="population"):
        _engine(population=None).restore_state(snap)
    other = Population([Participant.trend(size=0.02)])
    with pytest.raises(tf.ValidationError, match="population"):
        _engine(population=other).restore_state(snap)
    iso = _engine(population=None)
    _day(iso)
    with pytest.raises(tf.ValidationError, match="population"):
        _engine().restore_state(iso.state_snapshot())


def test_a_fork_carries_the_population():
    e = _engine()
    _day(e, UNIVERSE[2])
    a, b = e.fork(2)
    _day(a, UNIVERSE[2])
    _day(b, UNIVERSE[2])
    assert a.state_hash() == b.state_hash()
    assert a.population_fingerprint == POP.fingerprint


# ------------------------------------------------------------- the trading

def test_its_fills_stay_on_its_ledger_and_its_labels_are_its_own():
    e = _engine()
    for _ in range(3):
        _day(e, UNIVERSE[2])
    fills = e.take_fills(None)
    assert fills and all(f["agent"] == "a" for f in fills)
    assert all(not r["agent"].startswith("population:")
               for r in e.take_impacts(None))
    e.open_market()
    with pytest.raises(tf.OrderError):
        e.submit("population:trend", UNIVERSE[2].ticker, 10.0)
    for r in e.population_report():
        assert set(r) == {"name", "kind", "label", "names", "pnl", "volume",
                          "notional", "orders"}


def _detector_position(schedule, days=5, max_spread=10.0):
    """A detector alone (5% of volume at most, any spread unless
    `max_spread` says otherwise), beside a programme of 10% of a name's
    volume a day in 36 slices; its position at tick 40 of the last day."""
    name = UNIVERSE[3]
    e = _engine(population=Population([Participant.detector(
        size=0.05, rate=0.005, max_spread=max_spread)]))
    rng = random.Random(9)
    q = 0.1 * name.avg_volume / 36
    for d in range(days):
        e.open_market()
        ticks = (list(range(15, 375, 10)) if schedule == "same"
                 else sorted(rng.sample(range(15, 390), 36)))
        if d == days - 1:
            ticks = [t for t in ticks if t < 40]
        t0 = 0
        for t in ticks:
            if t > t0:
                m = 30 + t0
                e.run_session(9 + m // 60, m % 60, 3, t - t0)
                t0 = t
            e.submit("a", name.ticker, q)
        m = 30 + t0
        e.run_session(9 + m // 60, m % 60, 3, (40 if d == days - 1 else 390) - t0,
                      close_at_end=d < days - 1)
        if d < days - 1:
            e.close_market()
    report = e.population_report()[0]
    return report["names"][name.ticker]["position"] / name.avg_volume


def test_the_detector_positions_ahead_of_a_programme_it_can_predict():
    same = _detector_position("same")
    scattered = _detector_position("random")
    assert same > 0.02
    assert scattered < 0.5 * same


def test_the_detector_stays_out_where_the_spread_is_wide():
    assert _detector_position("same", max_spread=1e-6) == 0.0


def test_the_detector_does_nothing_without_agent_flow():
    e = _engine(population=Population([Participant.detector()]))
    for _ in range(3):
        _day(e)
    assert e.population_report()[0]["orders"] == 0
    assert "book" not in e.state_snapshot()


# ---------------------------------------------------------------- the API

def test_the_fingerprint_covers_the_participants_not_the_name():
    a = Population([Participant.trend()], name="a")
    b = Population([Participant.trend()], name="b")
    c = Population([Participant.trend(size=0.02)], name="a")
    assert a.fingerprint == b.fingerprint != c.fingerprint
    assert a == b and hash(a) == hash(b)
    assert Population.from_dict(POP.as_dict()) == POP
    assert Population.named("standard") == POP
    assert [p.kind for p in POP.participants] == [
        "trend", "reversion", "liquidity", "detector"]


@pytest.mark.parametrize("bad", [
    lambda: Participant.trend(size=0.0),
    lambda: Participant.trend(lookback=1.5),
    lambda: Participant.liquidity(vix_calm=40.0, vix_stress=20.0),
    lambda: Participant("market_maker", name="x", size=0.1, rate=0.1,
                        interval=1, band=0.1),
    lambda: Population([]),
    lambda: Population([Participant.trend(), Participant.trend()]),
    lambda: tf.Engine(seed=1, universe=UNIVERSE, population="standard"),
])
def test_bad_populations_are_refused(bad):
    with pytest.raises((tf.ValidationError, ValueError)):
        bad()


def test_the_engine_refuses_a_spec_the_policy_cannot_run():
    class Forged:
        def _engine_spec(self):
            spec = POP._engine_spec()
            spec["participants"][3]["lead"] = 300
            spec["participants"][3]["hold"] = 100
            return spec
    with pytest.raises(tf.ValidationError, match="lead and hold"):
        tf.Engine(seed=1, universe=UNIVERSE, population=Forged())
    with pytest.raises(tf.ValidationError, match="lead and hold"):
        Participant.detector(lead=300, hold=100)


def test_a_populated_evaluation_names_its_population():
    agents = {"m": tf.StrategySpec.momentum(), "h": tf.StrategySpec.hold()}
    iso = tf.evaluate(agents, seed=4, universe=UNIVERSE, days=3)
    pop = tf.evaluate(agents, seed=4, universe=UNIVERSE, days=3, population=POP)
    again = tf.evaluate(agents, seed=4, universe=UNIVERSE, days=3, population=POP)
    assert pop["m"].population_fingerprint == POP.fingerprint
    assert pop["m"].as_dict()["population_fingerprint"] == POP.fingerprint
    assert pop["m"].as_dict() == again["m"].as_dict()
    assert pop["m"].pnl != iso["m"].pnl
    with pytest.raises(tf.ValidationError):
        tf.evaluate(agents, seed=4, universe=UNIVERSE, days=1, population="x")


class _Buyer:
    def __init__(self, ticker):
        self.ticker = ticker

    def act(self, obs):
        return {self.ticker: 100.0}


@pytest.mark.parametrize("pop", [POP, Population.crowded()], ids=["standard", "crowded"])
def test_a_world_forks_and_manifests_with_its_population(pop):
    world = tf.World(seed=6, universe=UNIVERSE, agent=_Buyer(UNIVERSE[2].ticker),
                     population=pop)
    world.run(2)
    checkpoint = tf.Checkpoint.from_json(world.checkpoint().to_json())
    assert checkpoint.resume().state_hash() == world.engine.state_hash()
    a, b = world.fork("a", "b")
    a.run(2)
    b.run(2)
    assert a.engine.state_hash() == b.engine.state_hash()
    manifest = a.manifest(strategy="tests/test_population.py _Buyer")
    doc = json.loads(manifest.to_json())
    assert doc["fingerprints"]["population"] == pop.fingerprint
    loaded = tf.RunManifest.from_json(manifest.to_json())
    assert loaded.population == pop
    replayed = loaded.reproduce()
    assert replayed.state_hash() == a.engine.state_hash()
    doc["population"]["participants"][0]["size"] = 0.5
    with pytest.raises(tf.ValidationError, match="population"):
        tf.RunManifest.from_json(json.dumps(doc))


def test_an_isolated_manifest_has_no_population():
    world = tf.World(seed=6, universe=UNIVERSE, agent=_Buyer(UNIVERSE[2].ticker))
    world.run(1)
    doc = json.loads(world.manifest(strategy="x").to_json())
    assert "population" not in doc and "population" not in doc["fingerprints"]


# ------------------------------------------------------------------ crowds

def _crowd_engine(crowd, seed=3):
    return tf.Engine(seed=seed, universe=UNIVERSE, model="pt-v20",
                     population=Population([crowd]))


def _ranked(prices_now, prices_then, tickers, momentum, k):
    r = [(a / b - 1.0) for a, b in zip(prices_now, prices_then)]
    sign = -1.0 if momentum else 1.0
    order = sorted(range(len(r)), key=lambda i: (sign * r[i], tickers[i]))
    return set(order[:k]), set(order[-k:])


@pytest.mark.parametrize("signal", ["reversal", "momentum"])
def test_a_crowd_holds_the_book_the_ranked_rule_would(signal):
    # One decision a session (interval 390), so its book after a decision is
    # the ranking of that tick's prices against those one session earlier:
    # the long side all long, the short side all short, the rest flat.
    crowd = Participant.crowd(signal=signal, size=0.02, rate=0.04,
                              interval=390, band=0.0, lookback=390, top_k=2,
                              buffer=0)
    e = _crowd_engine(crowd)
    tickers = [c.ticker for c in UNIVERSE]
    opens = []
    for _ in range(4):
        e.open_market()
        opens.append(_prices(e))
        e.run_session(9, 30, 3, 1)
        e.run_session(9, 31, 3, 389, close_at_end=True)
        e.close_market()
    longs, shorts = _ranked(opens[-1], opens[-2], tickers, signal == "momentum", 2)
    held = e.population_report()[0]["names"]
    for i, t in enumerate(tickers):
        q = held[t]["position"]
        assert (q > 0) == (i in longs) and (q < 0) == (i in shorts), (t, q)
    assert e.population_report()[0]["exposure"] == 1.0


def test_a_crowd_with_no_history_does_nothing():
    e = _crowd_engine(Participant.crowd(signal="momentum", lookback=1950))
    for _ in range(3):
        _day(e)
    assert e.population_report()[0]["orders"] == 0


def test_a_crowd_past_its_loss_limit_sells_out_and_comes_back():
    # A limit no book can stay inside: it stops out on its first marked
    # loss, holds nothing, and takes its book back a share at a time.
    crowd = Participant.crowd(signal="reversal", size=0.05, rate=0.05,
                              interval=5, band=0.0, lookback=390, top_k=2,
                              stop=1e-9, recover=0.5)
    e = _crowd_engine(crowd)
    for _ in range(6):
        _day(e)
    report = e.population_report()[0]
    assert report["stops"] >= 1
    assert 0.0 <= report["exposure"] <= 1.0
    free = _crowd_engine(Participant.crowd(
        signal="reversal", size=0.05, rate=0.05, interval=5, band=0.0,
        lookback=390, top_k=2))
    for _ in range(6):
        _day(free)
    assert free.population_report()[0]["stops"] == 0
    assert report["volume"] != free.population_report()[0]["volume"]


@pytest.mark.parametrize("mid_day", [False, True])
def test_a_crowd_restores_in_a_fresh_process(tmp_path, mid_day):
    crowd = Population([Participant.crowd(signal="reversal", lookback=390,
                                          stop=0.002), Participant.detector()])
    e = tf.Engine(seed=3, universe=UNIVERSE, model="pt-v20", population=crowd)
    for _ in range(3):
        _day(e, UNIVERSE[2])
    if mid_day:
        e.open_market()
        e.run_session(9, 30, 3, 120)
    path = tmp_path / "snap.json"
    path.write_text(json.dumps(crowd.as_dict()))
    snap = tmp_path / "snap.bin"
    raw = snapshot_codec.dumps(e.state_snapshot())
    snap.write_bytes(raw.encode() if isinstance(raw, str) else raw)
    if mid_day:
        e.run_session(11, 30, 3, 270, close_at_end=True)
        e.close_market()
    for _ in range(2):
        _day(e, UNIVERSE[2])
    here = pathlib.Path(__file__).resolve().parent
    script = textwrap.dedent(f"""
        import json, sys
        sys.path.insert(0, {str(here)!r})
        import snapshot_codec, test_population as t, tradefloor as tf
        from tradefloor.population import Population
        raw = open({str(snap)!r}, "rb").read()
        snap = snapshot_codec.loads(raw.decode() if isinstance(snapshot_codec.dumps({{}}), str) else raw)
        pop = Population.from_dict(json.load(open({str(path)!r})))
        e = tf.Engine(seed=3, universe=t.UNIVERSE, model="pt-v20", population=pop)
        e.restore_state(snap)
        if {mid_day}:
            e.run_session(11, 30, 3, 270, close_at_end=True)
            e.close_market()
        for _ in range(2):
            t._day(e, t.UNIVERSE[2])
        print(e.state_hash())
    """)
    out = subprocess.run([sys.executable, "-c", script], check=True,
                         capture_output=True, text=True).stdout.strip()
    assert out == e.state_hash()
    assert e.population_report()[0]["orders"] > 0


def test_the_crowded_population_adds_both_crowds_to_the_standard_one():
    crowded = Population.crowded()
    assert Population.named("crowded") == crowded
    kinds = [p.kind for p in crowded.participants]
    assert kinds[:8] == ["trend", "reversion", "liquidity"] + ["detector"] * 5
    assert [p.interval for p in crowded.participants[:2]] == [130, 65]
    assert Population.crowded(detectors=2).fingerprint != crowded.fingerprint
    crowds = [p for p in crowded.participants if p.kind == "crowd"]
    reversal = [p for p in crowds if p.signal == "reversal"]
    momentum = [p for p in crowds if p.signal == "momentum"]
    assert len(reversal) == 1 and len(momentum) == 3
    assert reversal[0].size == pytest.approx(0.006) and reversal[0].interval == 15
    assert sum(p.size for p in momentum) == pytest.approx(0.01)
    # Loss limits spread around `stop`; none at all with stop 0.
    assert sorted(p.stop for p in momentum) == pytest.approx([0.015, 0.03, 0.045])
    assert all(p.stop == 0 for p in Population.crowded(stop=0).participants
               if p.kind == "crowd")
    # The momentum crowd decides once a session, near the close.
    assert all(p.interval == 390 and p.offset >= 375 for p in momentum)
    assert crowded.fingerprint != Population.crowded(momentum=0.02).fingerprint
    e = tf.Engine(seed=3, universe=UNIVERSE, model="pt-v20", population=crowded)
    for _ in range(2):
        _day(e)
    rows = [r for r in e.population_report() if r["kind"] == "crowd"]
    assert len(rows) == 4 and {r["signal"] for r in rows} == {"momentum", "reversal"}
    assert {"exposure", "stops", "price_pnl"} <= set(rows[0])


def test_a_crowd_is_part_of_the_fingerprint():
    a = Population([Participant.crowd(signal="reversal")])
    b = Population([Participant.crowd(signal="momentum")])
    c = Population([Participant.crowd(signal="reversal", stop=0.01)])
    assert len({a.fingerprint, b.fingerprint, c.fingerprint}) == 3
    assert Population.from_dict(c.as_dict()) == c
    assert c.participants[0].as_dict()["signal"] == "reversal"


@pytest.mark.parametrize("bad", [
    lambda: Participant.crowd(signal="value"),
    lambda: Participant.crowd(signal="momentum", lookback=392),
    lambda: Participant.crowd(signal="momentum", interval=7, lookback=392),
    lambda: Participant.crowd(signal="momentum", top_k=0),
    lambda: Participant.crowd(signal="momentum", recover=0.0),
    lambda: Participant.crowd(signal="momentum", stop=-0.1),
])
def test_bad_crowds_are_refused(bad):
    with pytest.raises((tf.ValidationError, ValueError)):
        bad()
