"""The market's next-session reversal (`market_factor_reversal`).

Every preset ships it at 0.0, which is left out of the digest, and every
known-answer digest is the one it was (tests/test_known_answer.py). These hold
the mechanism on pt-v20: at 0.0 the run is the preset's own; out of range is
refused; off zero each name's crowd slot carries `-k * beta * F` over the
session, with F yesterday's whole market factor, and the fair-value level takes
the same move, so the mispricing is untouched on the session it applies; a
restore reproduces the run; and the dial takes no draw.
"""

import struct

import pytest

import tradefloor as tf

UNIVERSE = list(tf.Universe.random(12, seed=3))
K = 0.08
CROWD = 2   # the crowd lean's attribution slot


def floats(raw):
    return struct.unpack("<%dd" % (len(raw) // 8), raw)


def engine(seed=7, **dials):
    return tf.Engine(seed=seed, universe=UNIVERSE, model=tf.ModelParams.from_preset("pt-v20", **dials))


def attribution(e):
    a = floats(e.state_snapshot()["attribution"])
    n = len(a) // len(e.tickers)
    return [a[i * n:(i + 1) * n] for i in range(len(e.tickers))]


def prev_day_factor(e):
    return e.state_snapshot()["market_variance"][4]


@pytest.mark.parametrize("preset", tf.preset_names())
def test_off_on_every_shipped_preset(preset):
    assert tf.ModelParams.from_preset(preset).to_dict()["market_factor_reversal"] == 0.0


@pytest.mark.parametrize("value", [-0.01, 0.51])
def test_out_of_range_is_refused(value):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", market_factor_reversal=value)


def test_at_zero_it_is_the_presets_own_run():
    a, b = engine(), engine(market_factor_reversal=0.0)
    assert a.model.fingerprint == b.model.fingerprint
    for x in (a, b):
        x.run_days(30, record=False)
    assert floats(a.prices()) == floats(b.prices())
    assert a.state_hash() == b.state_hash()


def test_the_crowd_slot_carries_the_reversal_and_the_level_keeps_it():
    base, live = engine(), engine(market_factor_reversal=K)
    # pt-v20 opens with no prehistory, so yesterday's factor is 0 on day 1 and
    # the two runs are one run through it.
    for x in (base, live):
        x.run_days(1, record=False)
    assert floats(base.prices()) == floats(live.prices())
    f = prev_day_factor(live)
    assert f == prev_day_factor(base) and f != 0.0
    s0 = floats(base.state_snapshot()["fair_value_offset"])
    s1 = floats(live.state_snapshot()["fair_value_offset"])
    assert s0 == s1
    for x in (base, live):
        x.run_days(1, record=False, first_day=1)
    beta = {c.ticker: c.beta for c in UNIVERSE}
    ab, al = attribution(base), attribution(live)
    vb = floats(base.state_snapshot()["fair_value_offset"])
    vl = floats(live.state_snapshot()["fair_value_offset"])
    eq = [i for i, t in enumerate(live.tickers) if t in beta]
    for j, i in enumerate(eq):
        want = -K * beta[live.tickers[i]] * f
        assert al[i][CROWD] - ab[i][CROWD] == pytest.approx(want, rel=1e-6, abs=1e-12)
        assert vl[j] - vb[j] == pytest.approx(want, rel=1e-3, abs=1e-9)


def test_a_restore_reproduces_the_run():
    e = engine(market_factor_reversal=K)
    e.run_days(6, record=False)
    snap = e.state_snapshot()
    twin = engine(market_factor_reversal=K)
    twin.restore_state(snap)
    assert twin.state_hash() == e.state_hash()
    for x in (e, twin):
        x.run_days(4, record=False, first_day=6)
    assert floats(e.prices()) == floats(twin.prices())
    assert e.state_hash() == twin.state_hash()


def test_no_draw_and_the_market_moves():
    base, live = engine(), engine(market_factor_reversal=K)
    for x in (base, live):
        x.run_days(30, record=False)
    assert base.draws_consumed == live.draws_consumed
    assert floats(base.prices()) != floats(live.prices())
