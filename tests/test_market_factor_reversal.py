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


def test_the_vix_power_at_zero_is_the_fixed_share():
    a, b = engine(market_factor_reversal=K), engine(market_factor_reversal=K, market_factor_reversal_vix_power=0.0)
    for x in (a, b):
        x.run_days(20, record=False)
    assert floats(a.prices()) == floats(b.prices())
    assert a.state_hash() == b.state_hash()


def test_the_vix_power_scales_the_share_by_the_last_close_vix():
    q = 2.0
    base, live = engine(), engine(market_factor_reversal=K, market_factor_reversal_vix_power=q)
    for x in (base, live):
        x.run_days(1, record=False)
    f, vix = prev_day_factor(live), live.state_snapshot()["economy"]["vix"]
    for x in (base, live):
        x.run_days(1, record=False, first_day=1)
    beta = {c.ticker: c.beta for c in UNIVERSE}
    ab, al = attribution(base), attribution(live)
    for i, t in enumerate(live.tickers):
        want = -K * (vix / 20.0) ** q * beta[t] * f
        assert al[i][CROWD] - ab[i][CROWD] == pytest.approx(want, rel=1e-6, abs=1e-12)


@pytest.mark.parametrize("value", [-0.5, 4.5])
def test_the_vix_power_out_of_range_is_refused(value):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", market_factor_reversal=K, market_factor_reversal_vix_power=value)


def test_the_transient_share_at_zero_is_the_reversal_as_it_stands():
    a, b = engine(market_factor_reversal=K), engine(market_factor_reversal=K, market_factor_reversal_transient=0.0)
    assert a.model.fingerprint == b.model.fingerprint
    for x in (a, b):
        x.run_days(20, record=False)
    assert floats(a.prices()) == floats(b.prices())
    assert a.state_hash() == b.state_hash()


@pytest.mark.parametrize("tau", [1.0, 0.25])
def test_the_transient_share_stays_in_the_mispricing(tau):
    """The fair-value level takes (1 - tau) of what it takes at tau 0; at tau 1 it is the run without the reversal,
    to the bit. The crowd slot still carries the reversal (the lean on the mispricing it left moves a little too)."""
    base = engine()
    whole = engine(market_factor_reversal=K)
    live = engine(market_factor_reversal=K, market_factor_reversal_transient=tau)
    for x in (base, whole, live):
        x.run_days(1, record=False)
    f = prev_day_factor(live)
    assert f == prev_day_factor(base) and f != 0.0
    for x in (base, whole, live):
        x.run_days(1, record=False, first_day=1)
    beta = {c.ticker: c.beta for c in UNIVERSE}
    ab, al = attribution(base), attribution(live)
    vb = floats(base.state_snapshot()["fair_value_offset"])
    vw = floats(whole.state_snapshot()["fair_value_offset"])
    vl = floats(live.state_snapshot()["fair_value_offset"])
    if tau == 1.0:
        assert vl == vb
    eq = [i for i, t in enumerate(live.tickers) if t in beta]
    for j, i in enumerate(eq):
        want = -K * beta[live.tickers[i]] * f
        assert al[i][CROWD] - ab[i][CROWD] == pytest.approx(want, rel=2e-2)
        assert vl[j] - vb[j] == pytest.approx((1.0 - tau) * (vw[j] - vb[j]), rel=1e-6, abs=1e-12)


def test_the_transient_share_takes_no_draw_and_restores():
    base, live = engine(), engine(market_factor_reversal=K, market_factor_reversal_transient=1.0)
    for x in (base, live):
        x.run_days(30, record=False)
    assert base.draws_consumed == live.draws_consumed
    e = engine(market_factor_reversal=K, market_factor_reversal_transient=1.0)
    e.run_days(6, record=False)
    twin = engine(market_factor_reversal=K, market_factor_reversal_transient=1.0)
    twin.restore_state(e.state_snapshot())
    for x in (e, twin):
        x.run_days(4, record=False, first_day=6)
    assert floats(e.prices()) == floats(twin.prices())
    assert e.state_hash() == twin.state_hash()


@pytest.mark.parametrize("value", [-0.1, 1.1])
def test_the_transient_share_out_of_range_is_refused(value):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", market_factor_reversal=K, market_factor_reversal_transient=value)


def test_the_cap_at_zero_is_the_share_as_it_stands():
    q = 2.0
    a = engine(market_factor_reversal=K, market_factor_reversal_vix_power=q)
    b = engine(market_factor_reversal=K, market_factor_reversal_vix_power=q, market_factor_reversal_cap=0.0)
    assert a.model.fingerprint == b.model.fingerprint
    for x in (a, b):
        x.run_days(20, record=False)
    assert floats(a.prices()) == floats(b.prices())
    assert a.state_hash() == b.state_hash()


def test_the_cap_holds_the_share_under_it():
    """With the VIX power lifting k (X/20)^q over the cap, the crowd slot carries -cap * beta * F; a cap above the
    share changes nothing, to the bit."""
    q, cap = 2.0, 0.02
    base = engine()
    live = engine(market_factor_reversal=K, market_factor_reversal_vix_power=q, market_factor_reversal_cap=cap)
    loose = engine(market_factor_reversal=K, market_factor_reversal_vix_power=q, market_factor_reversal_cap=1.0)
    free = engine(market_factor_reversal=K, market_factor_reversal_vix_power=q)
    for x in (base, live, loose, free):
        x.run_days(1, record=False)
    f, vix = prev_day_factor(live), live.state_snapshot()["economy"]["vix"]
    assert K * (vix / 20.0) ** q > cap
    for x in (base, live, loose, free):
        x.run_days(1, record=False, first_day=1)
    beta = {c.ticker: c.beta for c in UNIVERSE}
    ab, al = attribution(base), attribution(live)
    for i, t in enumerate(live.tickers):
        assert al[i][CROWD] - ab[i][CROWD] == pytest.approx(-cap * beta[t] * f, rel=1e-6, abs=1e-12)
    assert K * (vix / 20.0) ** q < 1.0
    assert floats(loose.prices()) == floats(free.prices())


@pytest.mark.parametrize("value", [-0.01, 1.01])
def test_the_cap_out_of_range_is_refused(value):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", market_factor_reversal=K, market_factor_reversal_cap=value)
