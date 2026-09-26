"""The anticipated earnings path (`earnings_anticipation_half_life`).

Fair value reads the earnings cycle's level averaged over its expected path,
`A = c e + g_phase`, instead of today's level alone
(`Engine::earnings_anticipation_terms`). These tests hold the three things the
mechanism is for: it is inert where it is off, a turn of phase moves prices at
once, and a trough reads above a contraction, so a price can turn up before
earnings do.
"""

import struct

import pytest

import tradefloor as tf

UNIVERSE = list(tf.Universe.random(8, seed=1))
PHASES = ("expansion", "peak", "contraction", "trough", "recovery")


def anticipated(model, phase):
    e = tf.Engine(seed=1, universe=UNIVERSE, model=model)
    e.pin_macro(cycle=phase)
    return (e.state_snapshot()["economy"]["earnings_cycle"]
            + e.earnings_anticipation)


def prices(engine):
    raw = engine.prices()
    return struct.unpack("<%dd" % (len(raw) // 8), raw)


@pytest.mark.parametrize("preset", tf.preset_names())
def test_off_on_every_shipped_preset_until_one_turns_it_on(preset):
    e = tf.Engine(seed=1, universe=UNIVERSE, model=preset)
    e.pin_macro(cycle="contraction")
    params = tf.ModelParams.from_preset(preset).to_dict()
    if params["earnings_anticipation_half_life"] == 0.0:
        assert e.earnings_anticipation == 0.0


def test_a_trough_reads_above_a_contraction_and_a_peak_below_an_expansion():
    model = tf.ModelParams.from_preset(
        "pt-v20", earnings_anticipation_half_life=126.0)
    level = {ph: anticipated(model, ph) for ph in PHASES}
    # A recovery is nearer from a trough than from a contraction, and a
    # contraction nearer from a peak than from an expansion.
    assert level["trough"] > level["contraction"]
    assert level["peak"] < level["expansion"]
    assert level["contraction"] < level["peak"]


def test_a_short_horizon_prices_todays_level():
    """As the half-life shrinks the discount leaves only today's level:
    `c = rho / (rho + kappa)` goes to one and every `g` to zero."""
    model = tf.ModelParams.from_preset(
        "pt-v20", earnings_anticipation_half_life=0.5)
    for ph in PHASES:
        e = tf.Engine(seed=1, universe=UNIVERSE, model=model)
        e.pin_macro(cycle=ph)
        assert abs(e.earnings_anticipation) < 0.02, ph


def test_news_of_a_turn_moves_prices_at_once():
    """The same market, the same draws, and a contraction pinned before the
    open: with the anticipation on, the first session already trades lower;
    with it off, the valuation reads only today's level, which a pin does not
    move."""
    moved = {}
    for label, h in (("off", 0.0), ("on", 126.0)):
        model = tf.ModelParams.from_preset(
            "pt-v20", earnings_anticipation_half_life=h)
        base = tf.Engine(seed=5, universe=UNIVERSE, model=model)
        # A day in first: the opening books whatever the valuation reads on
        # day zero into the names' levels, so a turn has to come after it.
        base.run_days(1)
        base.pin_macro(cycle=base.macro_fields["cycle"])
        turned, = tf.branch(base, 1)
        turned.pin_macro(cycle="contraction")
        for engine in (base, turned):
            engine.open_market()
            engine.run_session(9, 30, 3, 30)
            engine.close_market()
        moved[label] = sum(prices(turned)) / sum(prices(base)) - 1.0
    assert moved["on"] < moved["off"] - 0.02, moved
