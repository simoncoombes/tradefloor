"""Which valuation `tradefloor.fair_value` computes, and when it is the engine's.

`fair_value` has three kinds of caller that want different answers. A caller
reporting on a run wants the valuation that run's engine applies. The EDGAR
universe builder wants the valuation of the engine the roster will be run in.
The manifest's probe and the known-answer scripts want a valuation from fixed
inputs that no preset can move, or their digests would follow the default
preset.

So the call with no model is the reference valuation at fixed constants, and
every value in which an engine can differ from it is a keyword the caller can
supply, one at a time or all at once through `model=`. These tests use values
at which the two really differ (a QE gain off 1.0 with a non-zero QE boost, a
neutral rate off 0.04, a rate sensitivity off 1.5), because at the reference
values every form agrees and a test there proves nothing.
"""

from __future__ import annotations

import math
import struct

import pyarrow as pa
import pyarrow.compute as pc
import pytest

import tradefloor as tf
from tradefloor.edgar import Snapshot, to_instruments

#: The reference constants the no-model form values at (rust/src/fair_value.rs).
REFERENCE = dict(neutral_discount_rate=0.04, qe_pe_gain=1.0, qe_pe_stock_gain=0.0,
                 rate_pe_sensitivity=1.5, fair_value_book_floor=0.0)

#: Every ModelParams value the valuation reads.
VALUATION_KEYS = tuple(REFERENCE)

#: A model whose valuation differs from the reference in every value it reads.
DIVERGENT = dict(qe_pe_gain=0.5, rate_pe_sensitivity=2.25, qe_pe_stock_gain=0.1,
                 fair_value_book_floor=1.0)

ROWS = [
    dict(ticker="ALPHA", sector="technology", eps=6.10,
         book_value_per_share=18.0, revenue_growth=0.19, shares_outstanding=1.5e10),
    dict(ticker="BETA", sector="energy", eps=8.40,
         book_value_per_share=52.0, revenue_growth=0.02, shares_outstanding=4.2e9),
    dict(ticker="GAMMA", sector="utilities", eps=3.05,
         book_value_per_share=41.0, revenue_growth=0.01, shares_outstanding=8.0e8),
    dict(ticker="DELTA", sector="healthcare", eps=-1.20,
         book_value_per_share=12.0, revenue_growth=0.31, shares_outstanding=2.4e8),
]


def _first_tick_fundamentals(engine: tf.Engine) -> list[float]:
    """The engine's own fair value per name on the first tick of the run."""
    t = pa.table(engine.truth())
    first = pc.filter(t, pc.equal(t["tick"], pc.min(t["tick"])))
    first = first.sort_by("instrument_id")
    return first["fundamental_value"].to_pylist()


def _value(inst, m: tf.Macro, **kw) -> tf.FairValue:
    return tf.fair_value(
        eps=inst.eps, sector=inst.sector, revenue_growth=inst.revenue_growth,
        book_value_per_share=inst.book_value_per_share,
        federal_funds_rate=m.federal_funds_rate,
        corporate_bond_yield=m.corporate_bond_yield,
        qe_pe_boost=m.qe_pe_boost, qe_assets_ratio=m.qe_assets_ratio, **kw)


# --------------------------------------------------------------------------
# The reference form
# --------------------------------------------------------------------------

def test_the_reference_form_values_at_the_fixed_constants():
    """With no model the QE channel is `1 + boost` and the rate channel is
    `1 - (yield - 0.04) * 1.5 * duration`, whatever the default preset says.

    This is what the manifest's probe and the known-answer scripts rely on.
    The default preset ships a QE gain of 0.0, a neutral rate of 0.0482 and
    a rate sensitivity of 3.0, so none of these numbers is the default
    engine's.
    """
    v = tf.fair_value(eps=2.0, sector="technology", revenue_growth=0.2,
                      corporate_bond_yield=0.07, qe_pe_boost=0.1)
    assert v.qe_adjustment == 1.0 + 0.1
    assert v.rate_adjustment == 1.0 - (0.07 - 0.04) * 1.5 * (1.0 + 0.2 * 2.0)
    assert v.fair_value == 2.0 * v.target_pe

    explicit = tf.fair_value(eps=2.0, sector="technology", revenue_growth=0.2,
                             corporate_bond_yield=0.07, qe_pe_boost=0.1,
                             **REFERENCE)
    assert explicit.fair_value == v.fair_value
    assert explicit.qe_adjustment == v.qe_adjustment


def test_each_value_an_engine_can_differ_in_is_a_keyword():
    """Each keyword moves the valuation on its own, so none is ignored."""
    base = dict(eps=0.5, sector="technology", revenue_growth=0.2,
                corporate_bond_yield=0.07, qe_pe_boost=0.1, qe_assets_ratio=1.6,
                book_value_per_share=40.0)
    reference = tf.fair_value(**base).fair_value
    for key, value in dict(neutral_discount_rate=0.0482, **DIVERGENT).items():
        moved = tf.fair_value(**base, **{key: value}).fair_value
        assert moved != reference, key


# --------------------------------------------------------------------------
# With a model: the engine's valuation
# --------------------------------------------------------------------------

def test_with_a_model_the_helper_is_the_engines_own_valuation():
    """`fair_value(model=...)` equals the engine's fair value to the bit, on
    a state where the QE boost is non-zero and every valuation value of the
    model differs from the reference.

    pt-v18 on its first tick, where the nominal restatement and the buyback
    term are exactly 1.0, the fair-value news share is 0.0 and there is no
    VIX discount, so the tick's `fundamental_value` is the valuation alone.
    """
    model = tf.ModelParams.from_preset("pt-v18", **DIVERGENT)
    values = model.to_dict()
    assert values["neutral_discount_rate"] != REFERENCE["neutral_discount_rate"]
    roster = tf.Universe.random(12, seed=5)
    e = tf.Engine(seed=9, universe=roster, model=model)
    e.pin_macro(corporate_bond_yield=0.065, qe_pe_boost=0.08, qe_assets_ratio=1.6)
    e.open_market()
    m = e.macro_state
    assert m.qe_pe_boost == 0.08
    e.run_session(9, 30, 3, 1)
    engine = _first_tick_fundamentals(e)

    helper = [_value(inst, m, model=model).fair_value for inst in roster]
    assert helper == engine

    # The same, spelled out value by value.
    spelled = [_value(inst, m, **{k: values[k] for k in VALUATION_KEYS}).fair_value
               for inst in roster]
    assert spelled == engine

    # And the reference form really is somewhere else on this state, so the
    # equality above is not a constant compared with itself.
    reference = [_value(inst, m).fair_value for inst in roster]
    worst = max(abs(r / g - 1.0) for r, g in zip(reference, engine))
    assert worst > 0.05, worst


@pytest.mark.parametrize("preset", ["pt-v16", "pt-v19", "pt-v20"])
def test_a_preset_name_values_under_that_presets_own_values(preset):
    """A preset name is read as that preset, on shipped presets where the
    QE gain is 0.0 and the neutral rate or the rate sensitivity is off the
    reference."""
    values = tf.ModelParams.from_preset(preset).to_dict()
    assert values["qe_pe_gain"] == 0.0
    kw = dict(eps=3.0, sector="industrials", revenue_growth=0.1,
              corporate_bond_yield=0.06, qe_pe_boost=0.07)
    named = tf.fair_value(**kw, model=preset)
    spelled = tf.fair_value(**kw, **{k: values[k] for k in VALUATION_KEYS})
    assert named.fair_value == spelled.fair_value
    assert named.qe_adjustment == 1.0
    assert named.fair_value != tf.fair_value(**kw).fair_value


def test_a_model_and_a_value_it_carries_are_refused_together():
    """Two sources for one number: the call says which wins by refusing."""
    with pytest.raises(tf.ValidationError, match="neutral_discount_rate"):
        tf.fair_value(eps=1.0, sector="energy", model="pt-v20",
                      neutral_discount_rate=0.05)
    with pytest.raises(tf.ValidationError, match="qe_pe_gain"):
        tf.fair_value(eps=1.0, sector="energy", model="pt-v20", qe_pe_gain=1.0)


def test_a_non_finite_value_is_refused():
    for key in ("qe_pe_gain", "qe_pe_stock_gain", "rate_pe_sensitivity",
                "fair_value_book_floor", "qe_assets_ratio"):
        with pytest.raises(tf.ValidationError, match=key):
            tf.fair_value(eps=1.0, sector="energy", **{key: float("nan")})


# --------------------------------------------------------------------------
# The engine's valuation in its current state
# --------------------------------------------------------------------------

def test_engine_fair_values_is_what_the_next_tick_starts_from():
    """On the default preset the engine's fair value also carries the
    nominal restatement, the earnings cycle, buybacks, each name's fair-value
    level and the VIX discount, which no stateless call can see.
    `Engine.fair_values()` reads them from the engine.

    The tick's fundamental then adds that tick's own share of the news and
    market shocks, so both shares are off here and the two must agree to the
    bit after several days of every other term moving.
    """
    model = tf.ModelParams.from_preset(
        "pt-v20", fair_value_news_share=0.0, fair_value_market_share=0.0)
    e = tf.Engine(seed=4, universe=tf.Universe.random(10, seed=8), model=model)
    e.run_days(3)
    e.open_market()
    before = list(e.fair_values())
    e.run_session(9, 30, 3, 1)
    raw = e.state_snapshot()["tick_fundamental"]
    assert before == list(struct.unpack(f"<{len(raw) // 8}d", raw))


# --------------------------------------------------------------------------
# The universe builder
# --------------------------------------------------------------------------

@pytest.mark.parametrize("model", [
    "pt-v19",
    tf.ModelParams.from_preset("pt-v19", rate_pe_sensitivity=3.0),
])
def test_a_built_roster_opens_at_the_engines_fair_value(model):
    """No day-zero mispricing from the valuation alone.

    pt-v19 ships a QE gain of 0.0, and the macro here carries a QE boost, so
    a builder that valued at the reference gain priced every profitable name
    6% above the engine it then ran in. The second case moves the rate
    sensitivity too, which the default preset does.
    """
    macro = dict(federal_funds_rate=0.03, corporate_bond_yield=0.065,
                 qe_pe_boost=0.06)
    instruments = to_instruments(Snapshot(as_of="x", rows=ROWS), model=model,
                                 **macro)
    e = tf.Engine(seed=1, universe=tf.Universe(instruments), model=model,
                  macro_state=tf.Macro(**macro))
    e.open_market()
    e.run_session(9, 30, 3, 1)
    engine = _first_tick_fundamentals(e)
    assert [i.initial_price for i in instruments] == engine


def test_on_the_default_preset_the_remaining_opening_gap_is_one_common_scale():
    """pt-v20 restates fundamentals by the economy's opening earnings cycle,
    which a builder cannot see, so the roster opens under the engine's fair
    value by that one factor on every name. That preset's opening books the
    gap into each name's fair-value level, so it does not open as
    mispricing (`test_edgar.test_matching_the_macro_matters`).
    """
    macro = dict(federal_funds_rate=0.03, corporate_bond_yield=0.065)
    instruments = to_instruments(Snapshot(as_of="x", rows=ROWS), **macro)
    e = tf.Engine(seed=1, universe=tf.Universe(instruments),
                  macro_state=tf.Macro(**macro))
    ratios = [fv / i.initial_price for fv, i in zip(e.fair_values(), instruments)]
    assert max(ratios) - min(ratios) < 1e-12
    # Measured at 1.0133 on this macro and on two others, whatever the seed.
    assert 0.0 < math.log(ratios[0]) < 0.02, ratios[0]
