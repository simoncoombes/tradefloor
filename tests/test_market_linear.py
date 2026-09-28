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


# Every preset through pt-v19. pt-v20 sets fair_value_market_linear to 1 since its graded
# arm (2026-09-26; design repository, programme/ptv20-registration.md),
# which the test below holds. Was parametrized over every preset.
@pytest.mark.parametrize("preset", [p for p in tf.preset_names() if p != "pt-v20"])
def test_off_on_every_shipped_preset(preset):
    assert tf.ModelParams.from_preset(preset).to_dict()["fair_value_market_linear"] == 0.0


def test_pt_v20_sets_the_graded_arms_value():
    assert tf.ModelParams.from_preset("pt-v20").to_dict()["fair_value_market_linear"] == 1.0


def test_it_reads_nothing_without_a_market_share():
    runs = []
    for linear in (0.0, 1.0):
        e = tf.Engine(seed=7, universe=UNIVERSE,
                      # The market share off: pt-v20 carries it at 1.0 since
                      # its graded arm (2026-09-26); was pt-v20 as it stood.
                      model=tf.ModelParams.from_preset("pt-v20", fair_value_market_share=0.0,
                                                       fair_value_market_linear=linear))
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


# ---- fair_value_market_excess_share (r16 spike): a floor under the share
# above the ceiling. Off (0.0) on every shipped preset; 1.0 is no ceiling.

LOW_CEILING = dict(fair_value_market_share=1.0, fair_value_market_linear=1.0,
                   fair_value_market_vol_cap=0.01, fair_value_news_share=0.0,
                   endogenous_news_intensity=0.0, jump_intensity_market=0.0,
                   jump_intensity_idio=0.0, opening_mispricing_sigma=0.0,
                   opening_market_sigma=0.0)


def _level_moves(**dials):
    e = tf.Engine(seed=7, universe=UNIVERSE, model=tf.ModelParams.from_preset("pt-v20", **dials))
    e.run_days(1, record=False)
    before = offsets(e)
    e.run_days(10, record=False, first_day=1)
    return [b - a for a, b in zip(before, offsets(e))], floats(e.prices())


@pytest.mark.parametrize("preset", tf.preset_names())
def test_the_excess_share_is_off_on_every_shipped_preset(preset):
    assert tf.ModelParams.from_preset(preset).to_dict()["fair_value_market_excess_share"] == 0.0


def test_the_excess_share_at_zero_is_the_ceiling_as_it_stood():
    base = _level_moves(**LOW_CEILING)
    zero = _level_moves(fair_value_market_excess_share=0.0, **LOW_CEILING)
    assert base == zero


def test_the_excess_share_at_one_is_no_ceiling():
    # At 1.0 the share above the ceiling is the whole share, which is what a
    # ceiling of 0.0 gives: the same fair-value levels and the same prices.
    one = _level_moves(**{**LOW_CEILING, "fair_value_market_excess_share": 1.0})
    none = _level_moves(**{**LOW_CEILING, "fair_value_market_vol_cap": 0.0})
    assert max(abs(a - b) for a, b in zip(one[0], none[0])) < 1e-12
    assert max(abs(a / b - 1.0) for a, b in zip(one[1], none[1])) < 1e-12


def test_the_excess_share_puts_back_its_share_of_what_the_ceiling_took():
    # At a ceiling of a hundredth of the base sigma the capped share is about
    # nothing, so at a floor of 0.4 the fair-value levels move about 0.4 as far
    # as with no ceiling, and far more than with the ceiling alone.
    capped = sum(abs(x) for x in _level_moves(**LOW_CEILING)[0])
    half = sum(abs(x) for x in _level_moves(**{**LOW_CEILING, "fair_value_market_excess_share": 0.4})[0])
    whole = sum(abs(x) for x in _level_moves(**{**LOW_CEILING, "fair_value_market_vol_cap": 0.0})[0])
    assert capped < 0.05 * whole
    assert 0.3 * whole < half < 0.5 * whole


@pytest.mark.parametrize("value", [-0.1, 1.1])
def test_the_excess_share_is_bounded(value):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", fair_value_market_excess_share=value)
