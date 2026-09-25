"""Which part of a market shock is permanent (`fair_value_market_linear`,
`fair_value_market_vol_cap`).

Under `fair_value_market_share` a share of every market shock moves the
names' fair-value levels for good. At 0.0 that is the whole market input;
at 1.0 it is the plain loading on the draw, `beta * F`, and the down-tick
tilt, the lagged wire, the crisis injection, the crash amplifier and the
recentring stay in `s`. These tests hold that it is off on every shipped
preset, that it reads nothing without a market share, and that under a share
of 1.0 each name's fair-value level moves by exactly its beta times one common
amount on a day with no jump and no news.
"""

import struct

import pytest

import tradefloor as tf

UNIVERSE = list(tf.Universe.random(12, seed=3))


def floats(raw):
    return struct.unpack("<%dd" % (len(raw) // 8), raw)


def offsets(engine):
    return floats(engine.state_snapshot()["fair_value_offset"])


@pytest.mark.parametrize("preset", tf.preset_names())
def test_off_on_every_shipped_preset(preset):
    assert tf.ModelParams.from_preset(preset).to_dict()["fair_value_market_linear"] == 0.0


def test_it_reads_nothing_without_a_market_share():
    runs = []
    for linear in (0.0, 1.0):
        e = tf.Engine(seed=7, universe=UNIVERSE,
                      model=tf.ModelParams.from_preset("pt-v20", fair_value_market_linear=linear))
        e.run_days(20, record=False)
        runs.append(floats(e.prices()))
    assert runs[0] == runs[1]


def test_the_permanent_part_is_beta_times_the_draw():
    # Only market shocks reach the fair-value level here: the stock-level
    # share, news and jumps are off, so each name's level moves by
    # beta_i * (the day's summed market draw), one common amount.
    model = tf.ModelParams.from_preset(
        "pt-v20", fair_value_market_share=1.0, fair_value_market_linear=1.0,
        fair_value_news_share=0.0, endogenous_news_intensity=0.0,
        jump_intensity_market=0.0, jump_intensity_idio=0.0,
        opening_mispricing_sigma=0.0, opening_market_sigma=0.0)
    e = tf.Engine(seed=7, universe=UNIVERSE, model=model)
    betas = [ins.beta for ins in UNIVERSE]
    e.run_days(1, record=False)
    before = offsets(e)
    e.run_days(1, record=False, first_day=1)
    after = offsets(e)
    # Each tick adds dv - dv^2/2 with dv = beta * f, so over the day the
    # level moves by beta * C - beta^2 * Q for two amounts common to every
    # name: the move per unit beta is exactly linear in beta.
    moves = [(b - a) / beta for a, b, beta in zip(before, after, betas)]
    n = len(betas)
    mb, mm = sum(betas) / n, sum(moves) / n
    slope = (sum((x - mb) * (y - mm) for x, y in zip(betas, moves))
             / sum((x - mb) ** 2 for x in betas))
    residual = max(abs(y - mm - slope * (x - mb)) for x, y in zip(betas, moves))
    assert abs(mm) > 1e-4
    assert slope < 0.0
    assert residual < 1e-9 * abs(mm) + 1e-12


def test_it_differs_from_the_whole_input_on_a_tilted_market():
    base = dict(fair_value_market_share=1.0, opening_market_sigma=0.0)
    runs = []
    for linear in (0.0, 1.0):
        e = tf.Engine(seed=7, universe=UNIVERSE,
                      model=tf.ModelParams.from_preset("pt-v20", fair_value_market_linear=linear, **base))
        e.run_days(10, record=False)
        runs.append(offsets(e))
    assert runs[0] != runs[1]


def test_the_ceiling_is_inert_without_a_market_share_and_below_itself():
    # Without a market share the ceiling reads nothing; with one, a ceiling
    # far above any sigma the market reaches is the share itself.
    for extra, same in (({}, True), ({"fair_value_market_share": 1.0}, True)):
        runs = []
        for cap in (0.0, 32.0):
            e = tf.Engine(seed=7, universe=UNIVERSE, model=tf.ModelParams.from_preset(
                "pt-v20", fair_value_market_vol_cap=cap, **extra))
            e.run_days(20, record=False)
            runs.append(floats(e.prices()))
        assert (runs[0] == runs[1]) == same


def test_a_low_ceiling_keeps_market_shocks_in_s():
    # At a ceiling of a hundredth of the base sigma almost every market shock
    # stays in `s`: the fair-value levels move far less than with no ceiling.
    moves = []
    for cap in (0.0, 0.01):
        model = tf.ModelParams.from_preset(
            "pt-v20", fair_value_market_share=1.0, fair_value_market_linear=1.0,
            fair_value_market_vol_cap=cap, fair_value_news_share=0.0,
            endogenous_news_intensity=0.0, jump_intensity_market=0.0,
            jump_intensity_idio=0.0, opening_mispricing_sigma=0.0, opening_market_sigma=0.0)
        e = tf.Engine(seed=7, universe=UNIVERSE, model=model)
        e.run_days(1, record=False)
        before = offsets(e)
        e.run_days(10, record=False, first_day=1)
        moves.append(sum(abs(b - a) for a, b in zip(before, offsets(e))))
    assert moves[1] < 0.05 * moves[0]


@pytest.mark.parametrize("value", [-0.1, 32.5])
def test_the_ceiling_is_bounded(value):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", fair_value_market_vol_cap=value)


@pytest.mark.parametrize("value", [-0.1, 1.1])
def test_it_is_a_switch(value):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", fair_value_market_linear=value)
