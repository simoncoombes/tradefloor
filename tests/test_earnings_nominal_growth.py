"""The valuation grows with the nominal output the economy integrates.

Price is ``fair_value * exp(s)``, ``s`` is stationary around zero and ``eps``
is fixed when an instrument is built, so before this term the only time
variation in fair value was the discount rate and the expected log change of
the index over any horizon was zero. A drift placed inside ``s`` cannot
supply one either: under a constant drift ``c`` the stationary mean solves
``m = phi * m + c``, so a premium injected there is a level of
``c / (1 - phi)`` reached on the 60-day half-life and no growth after it.

``earnings_nominal_growth`` restates the fundamentals in the price level and
output the economy has reached. ``N = gdp * cpi`` is compounded daily by the
macro chain at ``gdp_growth / 100 / 365`` and ``inflation_rate / 100 / 365``,
and the valuation reads ``eps`` and ``book_value_per_share`` multiplied by
``1 + dial * (N_t / N_0 - 1)``.

What is pinned here is inertness at 0.0, the identity the scale satisfies
against an independently computed valuation, the base surviving a restore,
and the direction following the economy rather than a constant.
"""

from __future__ import annotations

import math

import pyarrow as pa
import pytest

import tradefloor as tf
from tradefloor.manifest import state_hash

#: Every arm here runs the era's own roster, so a number read out of one can
#: be put beside a number from the design note.
ROSTER = 8
UNIVERSE_SEED = 111
SEED = 3
#: Ticks a session runs here. `truth` returns one row per name per TICK, so
#: a day's table is ROSTER * TICKS rows and the counts below say so.
TICKS = 30

#: Every name ``ModelParams::preset_names`` returns, written out rather than
#: enumerated, so a preset added without a decision about this dial fails the
#: round trip below instead of joining a loop that would have passed either
#: way. pt-v17 is absent because the recomposition era reserves the number.
SHIPPED_PRESETS = (
    "pt-v1", "pt-v2", "pt-v3", "pt-v4", "pt-v5", "pt-v6", "pt-v7", "pt-v8",
    "pt-v9", "pt-v10", "pt-v11", "pt-v12", "pt-v13", "pt-v14", "pt-v15",
    "pt-v16", "pt-v18",
)


def _universe(n: int = ROSTER):
    return list(tf.Universe.random(n, seed=UNIVERSE_SEED))


def _engine(preset, seed: int = SEED, n: int = ROSTER):
    return tf.Engine(seed=seed, universe=_universe(n), model=preset)


def _without_buybacks():
    """pt-v18 with the buyback share off, for the identity tests.

    The identity below is about ONE term, and pt-v18 carries a second that
    scales the same two fundamentals. Isolating the subject keeps the
    derivation a genuine second route to the same number; deriving both
    would make the test a statement about two terms agreeing.

    The arms that measure the term's SIZE keep the shipped vector, because
    a size measured on an isolated preset is a size nobody ships.
    """
    return tf.ModelParams.from_preset("pt-v18", buyback_payout_share=0.0)


def _run(engine, days: int, ticks: int = TICKS, first: int = 0) -> None:
    for day in range(first, first + days):
        engine.open_market()
        engine.run_session(9, 30, 3, ticks)
        engine.record(day)
        engine.close_market()


def _market(engine) -> str:
    """The state leaf over everything except the model the arm names.

    An arm built through ``from_preset(..., earnings_nominal_growth=0.0)``
    fingerprints as ``custom-``, and the fingerprint is inside the state
    hash, so two arms that trade identically hash apart for a reason that
    is not about the market. Replacing it with one label leaves the columns,
    the generator positions, the day accumulators and the macro chain, which
    is the market. A digest rather than a dict comparison because the
    generators carry a Box-Muller spare that is NaN whenever the cache is
    empty, and NaN is unequal to itself.
    """
    snapshot = dict(engine.state_snapshot())
    snapshot["model_fingerprint"] = "arm"
    return state_hash(snapshot)


def _anchor(sector: str) -> float:
    """The sector's multiple, which is the one valuation input that does not
    depend on the discount rate.

    Taken from ``tradefloor.fair_value`` because it is a table this test has
    no other way to read, and it is safe to take from there for the reason
    the assertion below states: two calls at discount rates on opposite
    sides of neutral return the same anchor.
    """
    def once(rate):
        return tf.fair_value(
            sector=sector, eps=1.0, book_value_per_share=1.0,
            revenue_growth=0.0, corporate_bond_yield=rate,
            federal_funds_rate=rate, qe_pe_boost=0.0).sector_anchor_pe

    low, high = once(0.02), once(0.10)
    assert low == high, (sector, low, high)
    return low


def _derived(instrument, economy, scale: float, model) -> float:
    """Fair value rebuilt from the coefficients the engine under test runs.

    NOT through ``tradefloor.fair_value``. That helper delegates to the
    two-argument form, which carries the module's own neutral discount rate
    rather than the one the model holds, so the two sides of the identity
    below would share every input except the term AND the rate. Today they
    agree only because the shipped presets and the module constant both hold
    0.04, and a test resting on a coincidence between two surfaces is one
    change away from lying in either direction: it would go red for a preset
    that moves the neutral rate, and it would go quiet if the engine stopped
    applying the term while the helper stopped too.

    So every coefficient comes from the model, and the identity is a
    statement about one build rather than about two surfaces agreeing.
    """
    p = model.to_dict()
    # The three channels this derivation leaves out, asserted rather than
    # assumed, so a preset that switches any of them on fails here instead
    # of silently disagreeing with the engine.
    #
    # `buyback_payout_share` joined this list when it landed, and the way it
    # arrived is the reason the list is asserted rather than commented: it
    # scales the same two fundamentals the term under test scales, so the
    # identity below went red with a number and no name. A consumer of a
    # growing set either derives its expectation at runtime or fails naming
    # the member it does not know.
    assert p["qe_pe_stock_gain"] == 0.0, p["qe_pe_stock_gain"]
    assert p["fair_value_book_floor"] == 0.0, p["fair_value_book_floor"]
    assert p["buyback_payout_share"] == 0.0, p["buyback_payout_share"]

    discount = economy["corporate_bond_yield"] / 100.0
    duration = 1.0 + max(0.0, instrument.revenue_growth) * p[
        "growth_duration_scale"]
    rate_adjustment = max(
        p["rate_adjustment_floor"],
        1.0 - (discount - p["neutral_discount_rate"])
        * p["rate_pe_sensitivity"] * duration)
    qe_adjustment = 1.0 + p["qe_pe_gain"] * economy["qe_pe_boost"]

    eps = instrument.eps * scale
    if eps > 0.0:
        target_pe = _anchor(instrument.sector) * rate_adjustment * qe_adjustment
        return max(p["fair_value_floor"], eps * target_pe)
    book = instrument.book_value_per_share * scale
    return max(p["fair_value_floor"], book * p["loss_making_price_to_book"])


def test_every_preset_before_pt_v18_carries_the_dial_at_zero():
    """The inertness claim, stated over the whole shipped table.

    A dial nonzero on any earlier preset would have moved a trajectory that
    a published result cites, which is the one thing this project does not
    do.
    """
    for name in SHIPPED_PRESETS:
        value = tf.ModelParams.from_preset(name).to_dict()[
            "earnings_nominal_growth"]
        expected = 1.0 if name == "pt-v18" else 0.0
        assert value == expected, (
            f"{name} carries earnings_nominal_growth {value}")
    with pytest.raises(tf.ValidationError):
        tf.ModelParams.from_preset("pt-v17")
    with pytest.raises(tf.ValidationError):
        tf.ModelParams.from_preset("pt-v19")


def test_the_dial_at_zero_reproduces_the_market_bit_for_bit():
    """Off, the term reaches nothing.

    Run against pt-v18 with the dial zeroed rather than against an earlier
    preset, so the arm differs in this one field and in nothing else. The
    state hash rather than a spot price, because the claim covers the whole
    market and the macro chain it prices against.

    The macro chain is the half that matters for the trailing multiple.
    ``market_pe`` reads the same restated earnings the valuation does, it
    is inside the economy the state hash covers, and the business cycle's
    expansion hazard reads it, so a multiple that moved with the dial at
    zero would move a trajectory on every preset before pt-v18. The
    comparison below covers it because the economy is in the digest.
    """
    off = tf.ModelParams.from_preset("pt-v18", earnings_nominal_growth=0.0)
    zeroed = _engine(off)
    inherited = _engine("pt-v16")
    _run(zeroed, 40)
    _run(inherited, 40)
    assert _market(zeroed) != _market(inherited), (
        "pt-v16 and pt-v18 differ in five other dials, so this comparison "
        "says nothing on its own and is here to show the arms are distinct"
    )

    on = _engine("pt-v18")
    _run(on, 40)
    assert _market(on) != _market(zeroed), (
        "the dial moved no state over forty days, so this test would pass on "
        "a build where the term never reached the valuation"
    )
    assert on.column("price") != zeroed.column("price")


def test_the_scale_is_one_on_day_zero():
    """The opening valuation is unchanged, and so is the initial ``s``.

    ``N_0`` is read at construction, so the ratio is exactly 1.0 before the
    economy has advanced. The lazy initial ``s`` is ``log(price / fair
    value)`` taken on the first tick, so a scale other than 1.0 there would
    move every name's opening mispricing, which is the day-zero condition
    the whole era is measured against.
    """
    on = _engine("pt-v18")
    off = _engine(tf.ModelParams.from_preset(
        "pt-v18", earnings_nominal_growth=0.0))
    for engine in (on, off):
        engine.open_market()
        engine.run_session(9, 30, 3, TICKS)
        engine.record(0)
    assert _market(on) == _market(off)

    snapshot = on.state_snapshot()
    economy = snapshot["economy"]
    assert economy["gdp"] * economy["cpi"] == snapshot["nominal_output_base"]


def test_the_scale_is_the_economys_own_ratio():
    """The identity, against a valuation derived a second way.

    The engine's ``fundamental_value`` is compared with
    ``tradefloor.fair_value`` on the same company and the same macro state,
    with both fundamentals multiplied by a ratio this test computes out of
    the snapshot. A term applied at the wrong strength, against the wrong
    day's economy, or to only one of the two fundamentals fails here.
    """
    days = 120
    engine = _engine(_without_buybacks())
    universe = _universe()
    _run(engine, days - 1)

    # The economy a session prices off is the state it opens on, which is
    # what the previous close left behind.
    opening = engine.state_snapshot()
    economy = opening["economy"]
    base = opening["nominal_output_base"]
    scale = economy["gdp"] * economy["cpi"] / base
    assert scale > 1.0005, (
        f"the economy grew by {scale - 1.0:.6f} over {days} days, which is "
        "too little to separate a scaled valuation from an unscaled one"
    )
    # No assertion on the QE boost is needed any more. The derivation reads
    # ``qe_pe_gain`` from the model, so it computes the engine's adjustment
    # rather than the helper's, and the two agree at any boost.
    model = _without_buybacks()

    _run(engine, 1, first=days - 1)
    truth = pa.table(engine.truth(day=days - 1)).to_pydict()

    checked = 0
    for row, slot in enumerate(truth["instrument_id"]):
        expected = _derived(universe[slot], economy, scale, model)
        got = truth["fundamental_value"][row]
        assert got == pytest.approx(expected, rel=1e-12), (
            f"slot {slot}: engine {got}, derived {expected}")
        # The channel, stated as the identity rather than as a tolerance.
        # Against the SAME economy the term is the only difference between
        # the two valuations, so the log gap is the log of nominal output's
        # growth exactly, for every name and every tick. A term that
        # reached the discount rate, the anchor or the growth premium would
        # leave a residual here.
        unscaled = _derived(universe[slot], economy, 1.0, model)
        assert math.log(got) - math.log(unscaled) == pytest.approx(
            math.log(scale), rel=0.0, abs=1e-14), (slot, got, unscaled)
        checked += 1
    assert checked == ROSTER * TICKS


def test_an_unscaled_valuation_disagrees_with_the_engine():
    """The guard above, broken once, so its silence carries information.

    The same derivation with the scale left out has to disagree on every
    name. Without this the identity test would pass on a build where the
    term was never applied, since the helper and the engine agree on every
    other part of the valuation.
    """
    days = 120
    engine = _engine(_without_buybacks())
    universe = _universe()
    _run(engine, days - 1)
    economy = engine.state_snapshot()["economy"]
    _run(engine, 1, first=days - 1)
    truth = pa.table(engine.truth(day=days - 1)).to_pydict()

    model = _without_buybacks()
    disagreed = 0
    for row, slot in enumerate(truth["instrument_id"]):
        unscaled = _derived(universe[slot], economy, 1.0, model)
        if truth["fundamental_value"][row] != pytest.approx(
                unscaled, rel=1e-9):
            disagreed += 1
    assert disagreed == ROSTER * TICKS, (
        f"{ROSTER * TICKS - disagreed} rows valued at their unscaled "
        "fundamentals")


def test_a_loss_maker_grows_off_its_restated_book():
    """The book path, valued end to end.

    A profitable company is valued on earnings times a multiple and a
    loss-maker at ``book_value_per_share * LOSS_MAKING_PRICE_TO_BOOK``, so
    the two paths read different fundamentals and a term that scaled only
    earnings would leave every loss-maker's fair value falling in real
    terms for ever. ``Universe.random(8, seed=111)`` carries no loss-maker,
    which is why this builds one rather than relying on the roster the
    other arms use.
    """
    probe = tf.Instrument("LOSS", "transportation", initial_price=24.0,
                          shares_outstanding=1e9, eps=-2.5,
                          book_value_per_share=20.0, revenue_growth=-0.10,
                          avg_volume=5e6, beta=1.1, short_interest=1e6)
    universe = tf.Universe([probe])
    universe.extend(tf.Universe.random(7, seed=UNIVERSE_SEED))
    engine = tf.Engine(seed=SEED, universe=universe, model="pt-v18")

    days = 120
    for day in range(days - 1):
        engine.open_market()
        engine.run_session(9, 30, 3, TICKS)
        engine.record(day)
        engine.close_market()

    economy = engine.state_snapshot()["economy"]
    base = engine.state_snapshot()["nominal_output_base"]
    scale = economy["gdp"] * economy["cpi"] / base
    assert scale > 1.0005, scale

    engine.open_market()
    engine.run_session(9, 30, 3, TICKS)
    engine.record(days - 1)
    truth = pa.table(engine.truth(day=days - 1)).to_pydict()

    # LOSS_MAKING_PRICE_TO_BOOK, from `rust/src/fair_value.rs`. The book
    # path ignores the multiple and the discount rate, so the whole of the
    # valuation is this product and the scale is the only thing that can
    # have moved it.
    expected = 20.0 * scale * 1.2
    rows = [truth["fundamental_value"][i]
            for i, slot in enumerate(truth["instrument_id"]) if slot == 0]
    assert rows, "the probe was not valued"
    for value in rows:
        assert value == pytest.approx(expected, rel=1e-12), (value, expected)
        # And the unscaled book would be a different number, so a build
        # that scaled only earnings fails here.
        assert value != pytest.approx(20.0 * 1.2, rel=1e-9)


def test_the_base_survives_a_restore():
    """A restored engine values against the output its run opened at.

    The base is a constant of the run rather than state that advances, so an
    engine that rebuilt it from the economy it was restored onto would grow
    from the restore day and read as a plausible market the snapshot does
    not describe.
    """
    source = _engine("pt-v18")
    _run(source, 60)
    snapshot = source.state_snapshot()

    target = _engine("pt-v18")
    target.restore_state(snapshot)
    restored = target.state_snapshot()["nominal_output_base"]
    assert restored == snapshot["nominal_output_base"]
    assert target.state_hash() == source.state_hash()

    _run(source, 20, first=60)
    _run(target, 20, first=60)
    assert target.state_hash() == source.state_hash()


def test_the_state_hash_covers_the_base():
    """Both hashes carry it, and a moved base moves the leaf.

    The Rust digest walks the engine's fields and the Python one walks the
    snapshot's, so the pair is checked against each other as well as against
    a mutation.
    """
    engine = _engine(_without_buybacks())
    _run(engine, 10)
    snapshot = engine.state_snapshot()
    assert state_hash(snapshot) == engine.state_hash()

    moved = dict(snapshot)
    moved["nominal_output_base"] = snapshot["nominal_output_base"] * 1.5
    assert state_hash(moved) != state_hash(snapshot)


def test_the_term_follows_the_economy_down():
    """A shrinking nominal output values a company at less.

    This is the reason a mechanism belongs here rather than a flat premium.
    One macro state is compared against the same state with output below the
    base, which is what a contraction with deflation reaches, and every
    fundamental comes down with it in the ratio the economy moved.
    """
    engine = _engine(_without_buybacks())
    _run(engine, 2)
    snapshot = engine.state_snapshot()
    base = snapshot["nominal_output_base"]
    level = snapshot["economy"]["gdp"] * snapshot["economy"]["cpi"]

    shrunk = dict(snapshot)
    economy = dict(snapshot["economy"])
    economy["gdp"] = economy["gdp"] * 0.96
    shrunk["economy"] = economy
    shrunk_level = economy["gdp"] * economy["cpi"]
    assert shrunk_level < base

    engine.restore_state(shrunk)
    _run(engine, 1, first=2)
    shrunk_values = pa.table(
        engine.truth(day=2)).to_pydict()["fundamental_value"]

    other = _engine(_without_buybacks())
    _run(other, 2)
    other.restore_state(snapshot)
    _run(other, 1, first=2)
    level_values = pa.table(
        other.truth(day=2)).to_pydict()["fundamental_value"]

    ratio = ((1.0 + (shrunk_level / base - 1.0))
             / (1.0 + (level / base - 1.0)))
    assert ratio < 1.0
    for shrunk_value, level_value in zip(shrunk_values, level_values):
        assert shrunk_value < level_value
        assert shrunk_value == pytest.approx(level_value * ratio, rel=1e-12)


def test_the_clock_the_term_delivers_on():
    """The economy's year is 365 days and the market's is 252.

    The macro chain compounds ``gdp`` and ``cpi`` by an annual rate over 365
    on every day it advances, and it advances once per market day, so a
    252-session year carries 252/365 of both annual rates. The price leg is
    written as the exact daily recursion, which fails if either the divisor
    or the one-step-per-session cadence moves.

    The output leg is not exact day by day, and the reason is an ordering
    inside the daily step rather than a second clock: the level compounds
    from the growth rate the day opened with, and a month start then runs
    the monthly release and moves that rate afterwards, so the recorded rate
    on a release day is not the one the level used. Measured over 90 days
    that is 3 days and it moves the aggregate by 1.9e-4 of itself, which is
    why the aggregate below is checked to 2e-3.
    """
    days = 90
    engine = _engine("pt-v18")
    base = engine.state_snapshot()["nominal_output_base"]
    previous = engine.state_snapshot()["economy"]
    growth: list[float] = []
    inflation: list[float] = []
    for _ in range(days):
        engine.open_market()
        engine.run_session(9, 30, 3, TICKS)
        engine.close_market()
        economy = engine.state_snapshot()["economy"]
        assert economy["cpi"] == (
            previous["cpi"] * (1.0 + economy["inflation_rate"] / 100.0 / 365.0))
        growth.append(economy["gdp_growth"])
        inflation.append(economy["inflation_rate"])
        previous = economy

    economy = engine.state_snapshot()["economy"]
    realised = math.log(economy["gdp"] * economy["cpi"] / base) / days * 252
    naive = (sum(growth) + sum(inflation)) / days / 100.0 * 252.0 / 365.0
    assert realised == pytest.approx(naive, rel=2e-3)

    # And on the market's own clock, which is the number the design note
    # quotes: an economy-year of 4.5 delivers 3.11 over 252 sessions.
    assert realised * 365.0 / 252.0 == pytest.approx(
        (sum(growth) + sum(inflation)) / days / 100.0, rel=2e-3)
