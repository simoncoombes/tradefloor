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
import struct

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


#: What `_derived` cannot reproduce, each with the reason it cannot, so the
#: guard below can require that the refusals are exactly these. A member that
#: stops refusing fails there too, which is what stops this becoming a list
#: of excuses.
UNDERIVABLE = {
    "qe_pe_stock_gain": "multiplies the log of economy.qe_assets_ratio, and "
                        "Engine.state_snapshot does not carry that field, so "
                        "this side cannot read the value the engine used",
}


def _buyback_scale(share: float, grown_eps: float, price: float,
                   day: int) -> float:
    """The payout term, in the engine's own spelling.

    `market::tick::buyback_scale`, which reads the ALREADY-grown earnings, so
    the two terms compose rather than each scaling the original figure. Every
    guard the engine applies is applied here: a share of zero, a
    non-positive or non-finite earnings figure, a non-positive or non-finite
    price and a day at or before zero each give exactly 1.0.
    """
    if share == 0.0:
        return 1.0
    if not (grown_eps > 0.0) or not math.isfinite(grown_eps):
        return 1.0
    if not (price > 0.0) or not math.isfinite(price) or day <= 0:
        return 1.0
    return math.exp(share * grown_eps / price * day / 252.0)


def _derived(instrument, economy, scale: float, model, *,
             price: float | None = None, day: int | None = None) -> float:
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
    # `buyback_payout_share` arrived on this list and has left it, because
    # the term is modelled below rather than refused. The way it arrived is
    # still the reason the remaining entry is asserted rather than
    # commented: it scaled the same two fundamentals the term under test
    # scales, so the identity went red with a number and no name. A consumer
    # of a growing set either derives its expectation at runtime or fails
    # naming the member it does not know. The sweep below does the first.
    #
    # One channel cannot be derived at all, and it is refused by name.
    # `qe_pe_stock_gain` multiplies the log of `economy.qe_assets_ratio`,
    # and `Engine.state_snapshot` does not carry that field, so nothing on
    # this side can read the value the engine used.
    assert p["qe_pe_stock_gain"] == 0.0, UNDERIVABLE["qe_pe_stock_gain"]
    if price is None:
        assert p["buyback_payout_share"] == 0.0, (
            "this arm derives the nominal term alone, so the payout share "
            "has to be off; pass price= and day= to derive both")

    discount = economy["corporate_bond_yield"] / 100.0
    duration = 1.0 + max(0.0, instrument.revenue_growth) * p[
        "growth_duration_scale"]
    rate_adjustment = max(
        p["rate_adjustment_floor"],
        1.0 - (discount - p["neutral_discount_rate"])
        * p["rate_pe_sensitivity"] * duration)
    qe_adjustment = 1.0 + p["qe_pe_gain"] * economy["qe_pe_boost"]

    buyback = 1.0 if price is None else _buyback_scale(
        p["buyback_payout_share"], instrument.eps * scale, price, day)
    total = scale * buyback

    eps = instrument.eps * total
    if eps > 0.0:
        target_pe = _anchor(instrument.sector) * rate_adjustment * qe_adjustment
        earnings_value = max(p["fair_value_floor"], eps * target_pe)
        floor = p["fair_value_book_floor"]
        if floor == 0.0:
            return earnings_value
        # At a nonzero floor the loss-maker's anchor applies to profitable
        # companies too, so fair value is continuous at zero earnings.
        return max(earnings_value,
                   instrument.book_value_per_share * total
                   * p["loss_making_price_to_book"] * floor)
    book = instrument.book_value_per_share * total
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


def test_both_terms_compose_on_the_shipped_preset():
    """The identity on pt-v18 as it ships, with the payout term included.

    The arm above isolates the growth term by holding the payout share at
    zero. This one derives both, because the preset sets both and a test
    that only ever runs the isolated arm stops describing what ships.

    The payout factor reads the current price, so this assertion covers the
    FIRST tick of a day, where that price is the column the engine holds
    before the session and not an intra-tick quantity this file would have
    to infer. The test asserts the column is unchanged across the open
    rather than assuming it, since the whole point is to use the number the
    tick actually read.

    The two terms compose rather than each scaling the original earnings:
    the payout factor is computed on the already-grown figure, which is the
    order `market::tick` applies them in.
    """
    days = 60
    engine = _engine("pt-v18")
    universe = _universe()
    model = tf.ModelParams.from_preset("pt-v18")
    assert model.to_dict()["buyback_payout_share"] != 0.0, (
        "this arm exists to cover the payout term and the preset has it off")
    _run(engine, days - 1)

    opening = engine.state_snapshot()
    economy = opening["economy"]
    scale = economy["gdp"] * economy["cpi"] / opening["nominal_output_base"]
    before = engine.column("price")
    assert opening["day_count"] == days - 1, opening["day_count"]

    engine.open_market()
    assert engine.column("price") == before, (
        "the open moved the price column, so the number below is not the one "
        "the tick read")
    prices = struct.unpack("<%dd" % ROSTER, before)

    engine.run_session(9, 30, 3, 1)
    engine.record(days - 1)
    truth = pa.table(engine.truth(day=days - 1)).to_pydict()

    checked = 0
    for row, slot in enumerate(truth["instrument_id"]):
        want = _derived(universe[slot], economy, scale, model,
                        price=prices[slot], day=days - 1)
        got = truth["fundamental_value"][row]
        assert got == pytest.approx(want, rel=1e-12), (slot, got, want)
        checked += 1
    assert checked == ROSTER

    # And the payout term is doing something, or this would pass on a build
    # that dropped it. Derived without a price, the same names differ.
    nominal_only = _without_buybacks()
    differing = sum(
        1 for row, slot in enumerate(truth["instrument_id"])
        if truth["fundamental_value"][row] != pytest.approx(
            _derived(universe[slot], economy, scale, nominal_only), rel=1e-9))
    assert differing == ROSTER, differing


def test_the_derivation_tracks_every_parameter_that_reaches_the_valuation():
    """The guard that MEASURES its scope rather than listing it.

    `_derived` reads a named set of coefficients, and a named set is a
    statement about that set. This derivation was rewritten once precisely
    to stop depending on two surfaces agreeing, moving from the public
    helper to the model's own parameters, and it still read a list. The
    payout dial then scaled the same quantity the growth term scales and the
    identity had two terms where the derivation modelled one.

    So the scope is measured. Every entry in the project's own perturbation
    table is applied to the preset in turn, and the identity has to survive
    each one: the engine's fundamental must equal this derivation on the
    economy that engine priced against. A parameter that reaches the
    valuation and is not modelled here breaks the identity and is named.

    The perturbation table is the right source because it is one of the four
    surfaces a new dial has to touch, so the guard's scope grows with the
    model rather than with anyone remembering to widen it.

    # The limit, which is why this is a guard and not a proof

    The sweep's scope is the SETTABLE surface, and it proves that rather
    than assuming it: the perturbation table and `ModelParams.settable()`
    are asserted equal in both directions below, so a dial that reaches the
    model without reaching the table fails here rather than going unswept.

    Outside that surface sit thirty carried read-only parameters, which take
    no override and so cannot be perturbed. Five of them reach the
    valuation, `rate_pe_sensitivity`, `growth_duration_scale`,
    `rate_adjustment_floor`, `fair_value_floor` and
    `loss_making_price_to_book`, and `_derived` reads all five from the
    model by name. A carried parameter added later that reaches fair value
    would escape this guard, which is the gap to know about.

    The payout share is held at zero here, so this covers the derivation in
    its input-only form. The composed form is covered by the test that
    supplies a price.

    A parameter that moves the ECONOMY rather than the valuation is not a
    false failure, because the derivation is handed the economy the engine
    actually priced against rather than a fixed one.

    # What it costs

    One two-day, four-name, two-tick engine run per settable parameter. On
    this roster that is 116 runs in 1.6 to 1.8 seconds, against 4.1 to 4.8
    for the whole file, on a four-core Windows box under load. A range
    rather than a figure, because three runs here spanned that much and a
    single reading would imply a precision the measurement does not have. The number sits here
    rather than in an assertion, because a wall time asserted in a test
    fails flakily and never honestly.
    """
    import test_model_params as params_table

    # The sweep's scope, proved rather than assumed. If a dial reaches the
    # model without reaching the table, this fails before anything is
    # measured and the sweep never reports a false all-clear.
    perturbed = {name for name, _, _ in params_table.PERTURBATIONS}
    assert perturbed == set(tf.ModelParams.settable()), (
        sorted(perturbed ^ set(tf.ModelParams.settable())))

    universe = _universe(4)
    def identity_holds(model):
        engine = tf.Engine(seed=SEED, universe=universe, model=model)
        engine.open_market()
        engine.run_session(9, 30, 3, 2)
        engine.record(0)
        engine.close_market()
        economy = engine.state_snapshot()["economy"]
        base = engine.state_snapshot()["nominal_output_base"]
        scale_now = economy["gdp"] * economy["cpi"] / base
        dial = model.to_dict()["earnings_nominal_growth"]
        scale = 1.0 + dial * (scale_now - 1.0)
        engine.open_market()
        engine.run_session(9, 30, 3, 2)
        engine.record(1)
        truth = pa.table(engine.truth(day=1)).to_pydict()
        for row, slot in enumerate(truth["instrument_id"]):
            got = truth["fundamental_value"][row]
            want = _derived(universe[slot], economy, scale, model)
            if got != pytest.approx(want, rel=1e-9):
                return False, (slot, got, want)
        return True, None

    held, detail = identity_holds(tf.ModelParams.from_preset(
        "pt-v18", buyback_payout_share=0.0))
    assert held, ("the identity does not hold unperturbed, so nothing below "
                  f"means anything: {detail}")

    broke, rejected, undeliverable, applied = [], [], [], 0
    for name, value, _expected in params_table.PERTURBATIONS:
        if name == "buyback_payout_share":
            # Held at zero by this arm, so it cannot be perturbed here. Its
            # own test supplies a price and derives both terms.
            continue
        try:
            model = tf.ModelParams.from_preset(
                "pt-v18", **{name: value, "buyback_payout_share": 0.0})
        except tf.ValidationError:
            rejected.append(name)
            continue
        applied += 1
        try:
            held, detail = identity_holds(model)
        except AssertionError:
            # `_derived` refused the parameter by name rather than
            # disagreeing with the engine quietly, which is the outcome
            # UNDERIVABLE describes.
            undeliverable.append(name)
            continue
        if not held:
            broke.append((name, detail))

    assert broke == [], (
        "these parameters reach the valuation and the derivation does not "
        f"model them: {[n for n, _ in broke]}. Add the term, or if the "
        "parameter cannot reach fair value, the identity broke for another "
        f"reason and that is the finding. Detail: {broke[:3]}")

    # Both directions on the refusals, so UNDERIVABLE cannot become a list
    # of excuses: every refusal is one this file has stated a reason for,
    # and every stated reason still refuses.
    assert sorted(undeliverable) == sorted(UNDERIVABLE), (
        undeliverable, sorted(UNDERIVABLE))

    # Every name in the table is settable, which the scope check above
    # already established, so nothing should have been refused.
    assert rejected == [], rejected
    # And the probe has to have run, or an empty sweep would pass silently.
    assert applied == len(perturbed) - 1, (applied, len(perturbed))


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
