"""The index variance the VIX update read, term by term, reaching Python.

Nothing of `market::index_var` was reachable from Python: the engine
exposed `vix_anchor` and the factor's own state and stopped there, so the
one quantity the level identity is BUILT on -- the index's conditional
variance, and which of its six blocks a VIX move went into -- could not be
measured on a running engine at all. `Engine.index_variance_terms()` is
that reading. It is an instrument and not a mechanism: it adds no draw, no
state and no term, and `tests/known_answer.py` is the assertion of record
that the model did not move.

Three claims are asserted here, and the third is the one worth having.

1. **Absent when there is nothing to read.** With the identity off the
   read-back is never computed, so the getter says `None` rather than
   handing back a dictionary of zeroes that reads as a market with no
   variance.
2. **The parts are the whole.** `total` is the sum of the six terms
   through `k`, on bits, and `implied` is that variance as a VIX through
   `(1 + premium) * 100 * sqrt(252 * V)`, on bits.
3. **IT IS THE NUMBER THE UPDATE READ, AND THAT IS A DAY.** The identity's
   instantaneous terms -- the sector draw's sigma and the jump arrival
   rate -- are read at the VIX the close saw, and the update then moves
   the VIX. A getter that evaluated the identity afresh would read them at
   today's VIX and disagree with the update by a day's move, which is the
   size of the quantity a loop measurement is trying to see. The jump term
   is exactly reconstructible from the dials, so the day it was read at is
   a fact and not an inference: it is rebuilt at both VIXs and must match
   one and not the other.
"""
from __future__ import annotations

import math
import struct

import tradefloor as pt

#: Small enough for the suite, long enough that the VIX moves every day
#: and the two candidate readings in claim 3 separate.
NAMES, DAYS, SEED = 12, 8, 3
UNIVERSE_SEED = 111

#: The preset the loop measurement runs on, plus the identity. Written out
#: at the test rather than imported, so the arm a test runs is visible at
#: the test.
PRESET = "pt-v16"


def universe():
    return pt.Universe.random(NAMES, seed=UNIVERSE_SEED)


def model(**over):
    d = pt.ModelParams.from_preset(PRESET).to_dict()
    d.update(over)
    return pt.ModelParams.from_dict(d)


def run(engine, days=DAYS):
    """One row per day: the VIX before the close, the VIX after it, and the
    terms the day's update read."""
    rows = []
    for _ in range(days):
        before = engine.state_snapshot()["economy"]["vix"]
        engine.open_market()
        engine.run_session(9, 30, 3, 390)
        engine.close_market()
        after = engine.state_snapshot()["economy"]["vix"]
        rows.append((before, after, engine.index_variance_terms()))
    return rows


def market_jump_at(params, anchor, vix):
    """The identity's market-jump term at a given VIX.

    `apply_jumps`' own rate scale spelled out -- `(1 - c) + c * ratio^2`
    with the multiply order `Engine::index_conditional_variance_terms_now`
    uses -- then the second moment less the square of the mean, which is
    what `index_var.rs` computes. Written here rather than read off the
    engine, because the whole point is to have a second side to the
    equation.
    """
    c = params["jump_vix_coupling"]
    ratio = vix / anchor
    scale = 1.0 if c == 0.0 else (1.0 - c) + ((c * ratio) * ratio)
    lam = params["jump_intensity_market"] * scale
    mu, sig = params["jump_mean_market"], params["jump_sigma_market"]
    return lam * (mu * mu + sig * sig) - (lam * mu) * (lam * mu)


def bits(x):
    return struct.pack("<d", x)


# ── 1. Absent when there is nothing to read ──────────────────────────────


def test_a_run_without_the_identity_has_no_terms_to_report():
    """Off the identity the read-back is never computed. `None` says so;
    zeroes would say the index has no variance, which is a different and
    false statement."""
    engine = pt.Engine(seed=SEED, universe=universe(), model=model())
    assert engine.model_params["vix_level_identity"] == 0.0, "pt-v16 ships it off"
    assert engine.index_variance_terms() is None, "before any day"
    for before, after, terms in run(engine):
        assert terms is None, "a day advanced off the identity reported terms"


def test_no_day_has_closed_yet_is_also_absent():
    """A fresh engine under the identity has run no VIX update, so there is
    no variance any update read. The mirror of the test above: `None` has
    to mean both, and both have to be reachable."""
    engine = pt.Engine(seed=SEED, universe=universe(),
                       model=model(vix_level_identity=1.0))
    assert engine.index_variance_terms() is None


def test_under_the_identity_every_day_reports_its_own_terms():
    """The mirror that stops the two tests above passing on a broken
    getter: with the identity on there is a reading every day, and the
    readings MOVE. A field set once and never updated would pass an
    is-not-None test and report day one's market for a year."""
    engine = pt.Engine(seed=SEED, universe=universe(),
                       model=model(vix_level_identity=1.0))
    rows = run(engine)
    totals = []
    for before, after, terms in rows:
        assert terms is not None
        assert terms["total"] > 0.0, "an index with no variance"
        totals.append(terms["total"])
    assert len(set(totals)) == len(totals), f"the terms went stale: {totals}"


# ── 2. The parts are the whole ───────────────────────────────────────────


def test_the_reported_total_is_the_sum_of_the_reported_terms():
    """On bits, not to a tolerance. The three noise blocks are reported
    BEFORE the intraday curve and the three jump-and-news blocks after it,
    because the curve reaches one half of the identity and not the other;
    a total that could not be rebuilt from the terms would leave the split
    unusable for the thing it exists for."""
    engine = pt.Engine(seed=SEED, universe=universe(),
                       model=model(vix_level_identity=1.0))
    for before, after, t in run(engine):
        want = (t["k"] * (t["factor"] + t["sector"] + t["idio"])
                + t["market_jump"] + t["idio_jump"] + t["news"])
        assert bits(t["total"]) == bits(want), f'{t["total"]} vs {want}'


def test_the_implied_is_the_identity_on_the_total():
    """`VIX = (1 + pi) * 100 * sqrt(252 * V)`, asserted against the
    expression rather than against a number, so a change to the premium or
    to the conversion is caught rather than reproduced."""
    engine = pt.Engine(seed=SEED, universe=universe(),
                       model=model(vix_level_identity=1.0))
    premium = engine.model_params["vix_variance_premium"]
    for before, after, t in run(engine):
        want = (1.0 + premium) * 100.0 * math.sqrt(252.0 * t["total"])
        assert bits(t["implied"]) == bits(want), f'{t["implied"]} vs {want}'


def test_the_curve_multiplies_the_noise_blocks_and_not_the_jumps():
    """`k` travels with the terms because the total is not reconstructible
    without it, and it is the intraday curve's own second moment: above
    one, and the same every day, since it is a property of the tick grid
    and not of the market."""
    engine = pt.Engine(seed=SEED, universe=universe(),
                       model=model(vix_level_identity=1.0))
    seen = {t["k"] for _, _, t in run(engine)}
    assert len(seen) == 1, f"k moved with the market: {seen}"
    k = seen.pop()
    assert 1.0 < k < 1.1, f"k is not the curve's second moment: {k}"


# ── 3. It is the number the update read, and that is a day ───────────────


def test_the_terms_were_read_at_the_vix_the_close_saw():
    """THE CLAIM THE INSTRUMENT STANDS OR FALLS ON.

    The market-jump term is a closed form in the VIX, so the day it was
    evaluated at can be recovered rather than assumed. Rebuilt at the VIX
    before the update and at the VIX after it: it must equal the first on
    bits and differ from the second, on every day. A getter that
    recomputed the identity when it was CALLED would fail this the other
    way round, which is the failure it exists to catch.
    """
    engine = pt.Engine(seed=SEED, universe=universe(),
                       model=model(vix_level_identity=1.0))
    params, anchor = engine.model_params, engine.vix_anchor
    assert params["jump_vix_coupling"] != 0.0, (
        "with the jumps uncoupled from the VIX this test cannot tell the "
        "two days apart and would pass on a getter that read either")
    for day, (before, after, t) in enumerate(run(engine)):
        assert before != after, f"day {day}: the VIX did not move, nothing to tell apart"
        want = market_jump_at(params, anchor, before)
        assert bits(t["market_jump"]) == bits(want), (
            f'day {day}: read at neither VIX: {t["market_jump"]} vs {want}')
        stale = market_jump_at(params, anchor, after)
        assert bits(t["market_jump"]) != bits(stale), (
            f"day {day}: the terms were read at today's VIX, not the close's")


def test_reading_the_terms_moves_no_draw_and_no_price():
    """An instrument that consumed a draw, or cached into engine state,
    would put the run it is measuring somewhere the same run without it
    never goes. Asserted on the draw counters and on the whole price
    cross-section, since a getter can reach the market without reaching
    the generator."""
    watched = pt.Engine(seed=SEED, universe=universe(),
                        model=model(vix_level_identity=1.0))
    quiet = pt.Engine(seed=SEED, universe=universe(),
                      model=model(vix_level_identity=1.0))
    for _ in range(DAYS):
        for engine in (watched, quiet):
            engine.open_market()
            engine.run_session(9, 30, 3, 390)
            engine.close_market()
        # Read it twice, so a getter that advanced anything advances it
        # twice as far as a run that read it once.
        watched.index_variance_terms()
        watched.index_variance_terms()
    assert watched.draws_by_stream() == quiet.draws_by_stream()
    assert watched.state_hash() == quiet.state_hash()
    a, b = watched.state_snapshot(), quiet.state_snapshot()
    assert a["columns"]["price"] == b["columns"]["price"]
    assert a["economy"]["vix"] == b["economy"]["vix"]


def test_a_fork_carries_the_reading_and_a_restore_does_not():
    """The price of staying out of the state hash, asserted rather than
    left for a reader to discover.

    A fork is a copy of the engine, so its last VIX update really was the
    parent's and it carries the parent's reading. `restore_state` rebuilds
    an engine from a snapshot, and the snapshot does NOT hold this — that
    is what keeps it out of `state_hash` — so a restored engine keeps
    whatever reading it had of its own until its next day advances. Both
    are correct and neither is obvious, which is why they are here.
    """
    engine = pt.Engine(seed=SEED, universe=universe(),
                       model=model(vix_level_identity=1.0))
    run(engine, 3)
    parent = engine.index_variance_terms()
    assert parent is not None

    (child,) = engine.fork(1)
    assert child.index_variance_terms() == parent, "a fork lost the parent's reading"

    fresh = pt.Engine(seed=SEED, universe=universe(),
                      model=model(vix_level_identity=1.0))
    fresh.restore_state(engine.state_snapshot())
    assert fresh.index_variance_terms() is None, (
        "the snapshot carried the reading, so it is in the state hash after all")
    run(fresh, 1)
    assert fresh.index_variance_terms() is not None, (
        "a restored engine never picked the reading up again")
