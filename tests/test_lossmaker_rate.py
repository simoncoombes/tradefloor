"""`lossmaker_rate_sensitivity`: a rate term on a loss-maker's book value.

Off, as every shipped preset ships it, a company with no earnings is valued
at 1.2 times book with no rate term, and nothing about any preset moves:
the known-answer digests hold that, and these tests hold the fingerprint and
the valuation function. On, a loss-maker's fair value carries the profitable
multiple's rate adjustment with its own sensitivity.

The term is booked where a macro valuation term belongs: in the fair value
the economy sets each tick, beside the profitable companies' rate term. It
is not written into the company's own fair-value level `v`
(`fair_value_offset`) and not into the attribution's `fair_value_shift`,
which book what a company's own shocks did to its value. The engine test
below proves that on a rate shock with the permanent shares off: the yield
moves the loss-makers' fair value by a fifth or more and moves `v` and
`fair_value_shift` by exactly nothing, and the dial adds no writer of `v`.

At the open, the stationary opening splits each name's day-zero premium
between `s` and `v`, and that premium carries the opening's rate term, a
loss-maker's as a profitable company's (MODEL.md, "The stationary
opening"). That is the existing rule, not a booking of the term's moves.
"""

import math
import struct

import pytest

import tradefloor as tf

UNIVERSE = tf.Universe.random(20, seed=4)
LOSS = [i for i, inst in enumerate(UNIVERSE) if inst.eps <= 0]
PROFIT = [i for i, inst in enumerate(UNIVERSE) if inst.eps > 0]


def floats(raw):
    return list(struct.unpack("<%dd" % (len(raw) // 8), raw))


def test_off_on_every_shipped_preset_and_out_of_the_digest():
    for name in tf.preset_names():
        assert tf.ModelParams.from_preset(name).to_dict()[
            "lossmaker_rate_sensitivity"] == 0.0, name
    # At zero it is the preset, by name; off zero it is a custom model.
    assert tf.ModelParams.from_preset(
        "pt-v21", lossmaker_rate_sensitivity=0.0).fingerprint == "pt-v21"
    assert tf.ModelParams.from_preset(
        "pt-v21", lossmaker_rate_sensitivity=11.0).fingerprint.startswith("custom-")


@pytest.mark.parametrize("value", [-0.1, 20.5, float("nan")])
def test_out_of_range_is_refused(value):
    with pytest.raises((tf.ValidationError, ValueError)):
        tf.ModelParams.from_preset("pt-v21", lossmaker_rate_sensitivity=value)


def value(eps, growth, y, **kw):
    return tf.fair_value(eps=eps, sector="technology", book_value_per_share=10.0,
                         revenue_growth=growth, corporate_bond_yield=y, **kw)


def test_the_valuation_mirrors_the_profitable_rate_term():
    neutral = 0.0482
    for y in (0.03, neutral, 0.065):
        off = value(-1.0, 0.2, y, neutral_discount_rate=neutral)
        assert off.fair_value == 12.0 and off.rate_adjustment == 1.0
        on = value(-1.0, 0.2, y, neutral_discount_rate=neutral,
                   lossmaker_rate_sensitivity=4.0)
        # The profitable path's adjustment at the same sensitivity.
        profitable = value(1.0, 0.2, y, neutral_discount_rate=neutral,
                           rate_pe_sensitivity=4.0)
        assert on.rate_adjustment == profitable.rate_adjustment
        assert on.fair_value == pytest.approx(12.0 * profitable.rate_adjustment, rel=1e-15)
        assert on.book_value_path
    # A profitable company does not read it.
    assert (value(2.0, 0.2, 0.065, lossmaker_rate_sensitivity=11.0).fair_value
            == value(2.0, 0.2, 0.065).fair_value)
    # Through a model, as every engine path reads it.
    model = tf.ModelParams.from_preset("pt-v21", lossmaker_rate_sensitivity=11.0)
    assert value(-1.0, 0.2, 0.065, model=model).fair_value < 12.0
    with pytest.raises(tf.ValidationError):
        value(-1.0, 0.2, 0.065, model=model, lossmaker_rate_sensitivity=1.0)


def shocked(k):
    """pt-v21 with the permanent shares off, so nothing but the opening
    writes `v`, through the packaged rate shock (+200 bp on day 50)."""
    dials = {"fair_value_news_share": 0.0, "fair_value_market_share": 0.0}
    if k:
        dials["lossmaker_rate_sensitivity"] = k
    e = tf.Engine(seed=7, universe=UNIVERSE,
                  model=tf.ModelParams.from_preset("pt-v21", **dials))
    sc = tf.Scenario.load("rate_shock")
    out = []
    for day in range(52):
        sc.apply(e, day)
        e.open_market()
        e.run_session(9, 30, 3, 390)
        e.close_market()
        out.append({"v": floats(e.state_snapshot()["fair_value_offset"]),
                    "shift": floats(e.attribution("fair_value_shift")),
                    "fair": e.fair_values()})
    return out


def test_the_rate_term_is_booked_to_fair_value_and_not_to_the_company_level():
    assert LOSS and PROFIT
    on = shocked(11.0)
    off = shocked(0.0)
    for run in (on, off):
        # The shock's day and the day after: the yield is 200 bp up and `v`
        # does not move, on a loss-maker or on any other name.
        assert run[49]["v"] == run[50]["v"] == run[51]["v"]
        # Nothing reaches the attribution's fair-value shift, on any day.
        for day in run:
            assert all(x == 0.0 for x in day["shift"])
    # The only other writer of `v` here is pt-v21's earnings calendar, which
    # moves a name's level on its report days. The dial adds no writer: the
    # same names move on the same days with it on as off.
    for day in range(1, 52):
        moved = [[i for i, (a, b) in enumerate(zip(run[day]["v"], run[day - 1]["v"])) if a != b]
                 for run in (on, off)]
        assert moved[0] == moved[1], (day, moved)
    # The 200 bp lands in the loss-makers' fair value with the dial on, by
    # about kappa * D * 0.02 of it, and hardly at all with it off.
    drop_on = [math.log(on[50]["fair"][i] / on[49]["fair"][i]) for i in LOSS]
    drop_off = [math.log(off[50]["fair"][i] / off[49]["fair"][i]) for i in LOSS]
    assert max(drop_on) < -0.20, drop_on
    assert min(drop_off) > -0.05, drop_off
