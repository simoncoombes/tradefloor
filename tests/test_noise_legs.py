"""`random_noise` split into its market, sector and company legs.

A name's tick shock is `Gamma M + upsilon + xi l G + xi I`: the market leg
(the crash-amplified loading on the market draw, plus the down-tick
recentring `upsilon`), the sector leg and the company's own. The attribution
books all of it in `random_noise`, so that column won about four days in
five on pt-v21 and naming it every day scored 0.60 to 1.0. The legs are
booked beside it as `market_noise`, `sector_noise` and `company_noise`.

These tests hold four things. The legs sum to `random_noise` to rounding,
per tick in `truth()` and per day in `attribution()`, on every preset and on
seeds drawn at random. They are reporting state: the twelve `FACTORS`, the
state hash and the prices are what they were, and a restore carries the
legs. Only a night split makes the legs differ from `noise_split`, which
carries the night as well. And the explanation score with the legs split is
a separate key that leaves the default score where it was.
"""

import math
import random
import struct

import pytest

import tradefloor as tf
from tradefloor import harness, manifest
from tradefloor.baselines import Oracle

pa = pytest.importorskip("pyarrow")

UNIVERSE = tf.Universe.random(6, seed=21)

#: Seeds drawn at random from the whole 64-bit range, so the identity is held
#: on seeds nobody picked. From a fixed generator rather than the clock: a
#: failure has to be re-runnable, and a parallel run collects the module
#: once per worker and needs every worker to see the same ids.
SEEDS = [random.Random(20261011 + k).getrandbits(64) for k in range(3)]

LEGS = ("market_noise", "sector_noise", "company_noise")

#: Rounding: three sums against one, each a few hundred terms of 1e-3.
TOLERANCE = 1e-15


def floats(raw):
    return list(struct.unpack("<%dd" % (len(raw) // 8), raw))


def run(preset, seed, days=2, ticks=390, record=True):
    e = tf.Engine(seed=seed, universe=UNIVERSE, model=preset)
    for day in range(days):
        e.open_market()
        e.run_session(9, 30, 3, ticks)
        e.close_market()
        if record:
            e.record(day)
    return e


def table(stream):
    return stream if isinstance(stream, pa.Table) else pa.table(stream)


def test_the_names():
    assert tf.Engine.NOISE_LEGS == list(LEGS)
    assert harness.NOISE_LEG_NAMES == LEGS
    # The twelve are the twelve they were, and no leg is among them.
    assert tf.Engine.FACTORS == [
        "reversion", "momentum", "crowd_lean", "company_news",
        "order_flow_impact", "short_squeeze_effect", "random_noise",
        "circuit_breaker", "jump", "overnight", "fair_value_shift",
        "dividend"]
    assert not set(LEGS) & set(tf.Engine.FACTORS)
    # The split key is the ten drivers with `random_noise` replaced, in its
    # place, by its legs.
    assert harness.SPLIT_DRIVER_NAMES == (
        harness.DRIVER_NAMES[:6] + LEGS + harness.DRIVER_NAMES[7:])
    assert "random_noise" not in harness.SPLIT_DRIVER_NAMES


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("preset", tf.preset_names())
def test_the_legs_sum_to_random_noise_per_day_and_per_tick(preset, seed):
    e = run(preset, seed)
    noise = floats(e.attribution("random_noise"))
    legs = [floats(e.attribution(name)) for name in LEGS]
    assert any(x != 0.0 for x in noise)
    for i, whole in enumerate(noise):
        assert abs(math.fsum(leg[i] for leg in legs) - whole) <= TOLERANCE, (
            preset, seed, i)
    for day in range(2):
        t = table(e.truth(day=day))
        assert t.column_names[-3:] == list(LEGS)
        whole = t.column("random_noise").to_pylist()
        parts = [t.column(name).to_pylist() for name in LEGS]
        for k, value in enumerate(whole):
            assert abs(math.fsum(p[k] for p in parts) - value) <= TOLERANCE, (
                preset, seed, day, k)
        # The day grain is the tape summed, for the last day recorded.
    t = table(e.truth(day=1))
    ids = t.column("instrument_id").to_pylist()
    for j, name in enumerate(LEGS):
        column = t.column(name).to_pylist()
        for i in range(len(UNIVERSE)):
            summed = math.fsum(v for v, k in zip(column, ids) if k == i)
            assert abs(summed - legs[j][i]) <= TOLERANCE


def test_every_leg_moves():
    e = run("pt-v21", 7)
    for name in LEGS:
        assert any(abs(x) > 1e-6 for x in floats(e.attribution(name))), name


def test_the_unrecorded_session_carries_the_legs():
    e = run("pt-v21", 7, days=1, ticks=60, record=False)
    t = table(e.truth())
    whole = t.column("random_noise").to_pylist()
    parts = [t.column(name).to_pylist() for name in LEGS]
    assert len(whole) == 60 * len(UNIVERSE)
    for k, value in enumerate(whole):
        assert abs(math.fsum(p[k] for p in parts) - value) <= TOLERANCE


def test_noise_split_carries_the_night_and_the_legs_do_not():
    """Under pt-v21's night split the open adds the night's three parts to
    the GJR's split, `noise_split`, which the attribution books in
    `overnight`. The legs are the session's alone."""
    e = run("pt-v21", 7)
    noise = floats(e.attribution("random_noise"))
    split = [floats(e.noise_split(p)) for p in ("market", "sector", "idio")]
    night = [a - b for a, b in zip(
        floats(e.garch_innovation()), noise)]
    for i in range(len(noise)):
        assert abs(math.fsum(p[i] for p in split) - (noise[i] + night[i])) < 1e-15
    assert max(abs(x) for x in night) > 1e-6
    # Without a night split the two are the same sums, to the bit.
    e = run("pt-v20", 7)
    for name, part in zip(LEGS, ("market", "sector", "idio")):
        assert e.attribution(name) == e.noise_split(part)


def test_an_unknown_name_lists_the_legs():
    e = run("pt-v21", 7, days=1, ticks=10)
    with pytest.raises(tf.ValidationError, match="market_noise"):
        e.attribution("noise")


# --------------------------------------------------------------------------
# Reporting state: nothing on the price path moves
# --------------------------------------------------------------------------


@pytest.mark.parametrize("preset", ["pt-v19", "pt-v20", "pt-v21"])
def test_a_snapshot_carries_the_legs_only_where_they_differ(preset):
    # The second day: the night split leaves the first open alone.
    e = run(preset, 3, days=1, record=False)
    e.open_market()
    e.run_session(9, 30, 3, 120)
    snap = e.state_snapshot()
    night = preset == "pt-v21"
    assert ("noise_legs" in snap) == night
    # The legs are outside both state hashes.
    assert manifest.state_hash(snap) == e.state_hash()
    if night:
        edited = dict(snap)
        edited["noise_legs"] = bytes(len(snap["noise_legs"]))
        assert manifest.state_hash(edited) == manifest.state_hash(snap)


@pytest.mark.parametrize("preset", ["pt-v20", "pt-v21"])
def test_a_mid_day_restore_keeps_the_identity_and_the_market(preset):
    e = run(preset, 5, days=1, record=False)
    e.open_market()
    e.run_session(9, 30, 3, 150)
    r = tf.Engine(seed=5, universe=UNIVERSE, model=preset)
    r.restore_state(e.state_snapshot())
    assert r.state_hash() == e.state_hash()
    for name in LEGS:
        assert r.attribution(name) == e.attribution(name)
    for engine in (e, r):
        engine.run_session(12, 0, 3, 240)
        engine.close_market()
    assert r.prices() == e.prices()
    assert r.state_hash() == e.state_hash()
    noise = floats(r.attribution("random_noise"))
    legs = [floats(r.attribution(name)) for name in LEGS]
    for i, whole in enumerate(noise):
        assert abs(math.fsum(leg[i] for leg in legs) - whole) <= TOLERANCE


def test_a_snapshot_from_before_the_legs_restores():
    """A night-split snapshot written before the key existed restores, its
    legs read from the parts until the next open resets them."""
    e = run("pt-v21", 5, days=1, record=False)
    e.open_market()
    e.run_session(9, 30, 3, 150)
    snap = dict(e.state_snapshot())
    del snap["noise_legs"]
    r = tf.Engine(seed=5, universe=UNIVERSE, model="pt-v21")
    r.restore_state(snap)
    assert r.state_hash() == e.state_hash()
    for name, part in zip(LEGS, ("market", "sector", "idio")):
        assert r.attribution(name) == r.noise_split(part)
    for engine in (e, r):
        engine.run_session(12, 0, 3, 240)
        engine.close_market()
        engine.open_market()
        engine.run_session(9, 30, 3, 30)
    assert r.prices() == e.prices()
    for name in LEGS:
        assert r.attribution(name) == e.attribution(name)


def test_a_fork_and_a_delisting_carry_the_legs():
    e = tf.Engine(seed=5, universe=UNIVERSE, model="pt-v21")
    e.open_market()
    e.run_session(9, 30, 3, 100)
    twin, = e.fork(1)
    for name in LEGS:
        assert twin.attribution(name) == e.attribution(name)
    e.close_market()
    e.delist(2)
    e.open_market()
    e.run_session(9, 30, 3, 100)
    noise = floats(e.attribution("random_noise"))
    legs = [floats(e.attribution(name)) for name in LEGS]
    assert all(len(leg) == len(noise) for leg in legs)
    for i, whole in enumerate(noise):
        assert abs(math.fsum(leg[i] for leg in legs) - whole) <= TOLERANCE


# --------------------------------------------------------------------------
# The explanation score
# --------------------------------------------------------------------------


def ranked(engine, names):
    best, size = None, 0.0
    for name in names:
        total = math.fsum(abs(x) for x in floats(engine.attribution(name)))
        if total > size:
            best, size = name, total
    return best


def test_the_dominant_factor_keeps_its_default_and_splits_on_request():
    e = run("pt-v21", 9, days=1)
    assert harness._dominant_factor(e) == ranked(e, harness.DRIVER_NAMES)
    split = harness._dominant_factor(e, noise_legs=True)
    assert split == ranked(e, harness.SPLIT_DRIVER_NAMES)
    assert split != "random_noise"


def test_evaluate_scores_the_split_key_only_when_asked():
    common = dict(seed=13, universe=UNIVERSE, days=5, model="pt-v21")
    whole = tf.evaluate({"oracle": Oracle()}, **common)["oracle"]
    again = tf.evaluate({"oracle": Oracle()}, noise_legs=False,
                        **common)["oracle"]
    split = tf.evaluate({"oracle": Oracle(noise_legs=True)}, noise_legs=True,
                        **common)["oracle"]
    # The default card is the card it was: no slot in its dict, no flag.
    assert whole.noise_legs is False
    assert "noise_legs" not in whole.as_dict()
    assert "noise-legs" not in repr(whole)
    assert whole.as_dict() == again.as_dict()
    # The market is the same run; only the answer key moved.
    assert split.pnl == whole.pnl and split.equity_curve == whole.equity_curve
    assert split.noise_legs is True and split.as_dict()["noise_legs"] is True
    assert "noise-legs" in repr(split)
    answers = {actual for _, actual in split.explanations}
    assert answers <= set(harness.SPLIT_DRIVER_NAMES)
    assert "random_noise" not in answers
    # The Oracle names the dominant factor on either key.
    assert whole.explanation_accuracy == 1.0
    assert split.explanation_accuracy == 1.0
    # Only the noise moved between the keys: a day whose split answer is
    # not a leg has the answer it had, unless that answer was the whole
    # column, which no longer competes.
    for (_, a), (_, b) in zip(whole.explanations, split.explanations):
        if b not in LEGS and a != "random_noise":
            assert b == a


def test_evaluate_refuses_a_non_flag():
    with pytest.raises(tf.ValidationError, match="noise_legs"):
        tf.evaluate({"oracle": Oracle()}, seed=1, universe=UNIVERSE, days=1,
                    noise_legs="yes")


def test_explain_carries_the_legs():
    e = tf.Engine(seed=3, universe=UNIVERSE, model="pt-v21")
    e.keep_explanations(1, 1)
    for day in range(2):
        e.open_market()
        e.run_session(9, 30, 3, 390)
        e.close_market()
        e.record(day)
    x = e.explain(e.tickers[1], 1)
    assert list(x.noise_legs) == list(LEGS)
    noise = next(c for c in x.root.children if c.name == "random_noise")
    assert abs(math.fsum(x.noise_legs.values()) - noise.value) <= TOLERANCE
    # Not contributions: the fifteen still sum to the move.
    assert not {c.name for c in x.root.children} & set(LEGS)
    assert x.check() == []
