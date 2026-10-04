"""The roster's beta normalised at construction (`market_beta_normalise`;
0.8.5, sim/r17-tails).

`market_beta_normalise` is 0.0 on every shipped preset, and there the engine
takes no sum and every name keeps the instrument's beta. Off zero,
`Engine::with_params_from_opening` divides each public name's beta by
`B^d`, `B` the roster's cap-weighted beta at the opening caps, so at 1.0 the
market factor is the systematic part of the roster's own index. These tests
hold the default, the domain, the arithmetic as the engine's own beta column
reads it, that the instruments passed in are not touched, and that a
snapshot restores to the same market.
"""

import struct

import pytest

import tradefloor as tf

UNIVERSE = list(tf.Universe.random(12, seed=3))


def floats(raw):
    return struct.unpack("<%dd" % (len(raw) // 8), raw)


def engine(seed=7, universe=UNIVERSE, **dials):
    return tf.Engine(seed=seed, universe=universe,
                     model=tf.ModelParams.from_preset("pt-v20", **dials))


def cap_weighted_beta(universe, betas):
    caps = [inst.market_cap for inst in universe]
    return sum(c * b for c, b in zip(caps, betas)) / sum(caps)


@pytest.mark.parametrize("preset", tf.preset_names())
def test_the_normalisation_is_off_on_every_shipped_preset(preset):
    assert tf.ModelParams.from_preset(preset).to_dict()["market_beta_normalise"] == 0.0


def test_the_dial_is_refused_outside_its_domain():
    tf.ModelParams.from_preset("pt-v20", market_beta_normalise=1.0)
    tf.ModelParams.from_preset("pt-v20", market_beta_normalise=0.5)
    for bad in (-0.1, 1.1):
        with pytest.raises(Exception):
            tf.ModelParams.from_preset("pt-v20", market_beta_normalise=bad)


def test_off_every_beta_is_the_instruments():
    betas = floats(engine().column("beta"))
    assert list(betas) == [inst.beta for inst in UNIVERSE]


def test_on_the_roster_beta_is_one_and_the_ratios_hold():
    given = [inst.beta for inst in UNIVERSE]
    b0 = cap_weighted_beta(UNIVERSE, given)
    assert abs(b0 - 1.0) > 0.01
    betas = floats(engine(market_beta_normalise=1.0).column("beta"))
    assert cap_weighted_beta(UNIVERSE, betas) == pytest.approx(1.0, abs=1e-12)
    for got, was in zip(betas, given):
        assert got == pytest.approx(was / b0, rel=1e-12)
    # Half the power leaves half the log distance.
    half = floats(engine(market_beta_normalise=0.5).column("beta"))
    assert cap_weighted_beta(UNIVERSE, half) == pytest.approx(b0 ** 0.5, rel=1e-12)
    # The instruments passed in are the caller's and are not touched.
    assert [inst.beta for inst in UNIVERSE] == given


def test_on_is_the_same_market_as_a_roster_built_normalised():
    # The dial is the engine doing what a caller could do to the roster:
    # the same run to the bit as instruments built with the divided betas.
    b0 = cap_weighted_beta(UNIVERSE, [inst.beta for inst in UNIVERSE])
    rebuilt = [tf.Instrument(i.ticker, i.sector, initial_price=i.initial_price,
                             shares_outstanding=i.shares_outstanding, eps=i.eps,
                             book_value_per_share=i.book_value_per_share,
                             revenue_growth=i.revenue_growth, avg_volume=i.avg_volume,
                             beta=i.beta * (1.0 / b0), short_interest=i.short_interest)
               for i in UNIVERSE]
    a = engine(market_beta_normalise=1.0)
    b = engine(universe=rebuilt)
    for e in (a, b):
        e.run_days(3, record=False)
    assert floats(a.prices()) == floats(b.prices())
    off = engine()
    off.run_days(3, record=False)
    assert floats(off.prices()) != floats(a.prices())


def test_a_snapshot_restores_to_the_same_market():
    a = engine(market_beta_normalise=1.0)
    a.run_days(3, record=False)
    snap = a.state_snapshot()
    b = engine(market_beta_normalise=1.0)
    b.restore_state(snap)
    assert b.state_hash() == a.state_hash()
    for e in (a, b):
        e.run_days(2, record=False)
    assert b.state_hash() == a.state_hash()
    assert floats(b.prices()) == floats(a.prices())
