"""The 10-year's lagged equity term (`treasury_equity_lag`, `treasury_equity_lag_gain`).

Every preset ships both at 0.0, which is left out of the digest, and every
known-answer digest is the one it was (tests/test_known_answer.py). These hold
the mechanism on pt-v20 with credit's leverage gap carried: at 0.0 the run is
the run without it; the gain is refused without the lag; the priced gap steps
toward the leverage gap at the lag's half-life after the gap's own step; a
restore reproduces the run; and the dials take no draw while the 10-year moves.
"""

import struct

import pytest

import tradefloor as tf

UNIVERSE = list(tf.Universe.random(12, seed=3))
GAP = {"corporate_spread_vix_cut": 1.0, "corporate_spread_equity_gain": 1.5,
       "corporate_spread_equity_half_life": 126.0}
LAG, GAIN = 21.0, 2.0


def floats(raw):
    return struct.unpack("<%dd" % (len(raw) // 8), raw)


def engine(seed=7, **dials):
    return tf.Engine(seed=seed, universe=UNIVERSE,
                     model=tf.ModelParams.from_preset("pt-v20", **dials))


@pytest.mark.parametrize("preset", tf.preset_names())
def test_off_on_every_shipped_preset(preset):
    d = tf.ModelParams.from_preset(preset).to_dict()
    assert d["treasury_equity_lag"] == 0.0 and d["treasury_equity_lag_gain"] == 0.0


@pytest.mark.parametrize("dial,value", [("treasury_equity_lag", -1.0), ("treasury_equity_lag", 253.0),
                                        ("treasury_equity_lag_gain", -0.1), ("treasury_equity_lag_gain", 10.5)])
def test_out_of_range_is_refused(dial, value):
    dials = {**GAP, "treasury_equity_lag": LAG, dial: value}
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", **dials)


def test_the_gain_is_refused_without_the_lag():
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", treasury_equity_lag_gain=GAIN, **GAP)


def test_at_zero_it_is_the_run_without_it():
    # The gain at 0.0 is left out of the digest, so the hash is the run's own;
    # the lag alone is inert (no state, no draw) but, being non-zero, is in
    # the digest, so only its prices and economy are compared.
    a = engine(**GAP)
    b = engine(treasury_equity_lag_gain=0.0, **GAP)
    c = engine(treasury_equity_lag=LAG, treasury_equity_lag_gain=0.0, **GAP)
    for x in (a, b, c):
        x.run_days(30, record=False)
    assert a.state_hash() == b.state_hash()
    assert floats(a.prices()) == floats(c.prices())
    assert a.state_snapshot()["economy"] == c.state_snapshot()["economy"]
    assert "treasury_equity_priced_gap" not in c.state_snapshot()["economy"]


def test_the_priced_gap_follows_the_leverage_gap():
    e = engine(treasury_equity_lag=LAG, treasury_equity_lag_gain=GAIN, **GAP)
    keep = 0.5 ** (1.0 / LAG)
    before = e.state_snapshot()["economy"]
    moved = 0
    for day in range(40):
        e.run_days(1, record=False, first_day=day)
        now = e.state_snapshot()["economy"]
        p, d = before["treasury_equity_priced_gap"], now["spread_equity_gap"]
        assert now["treasury_equity_priced_gap"] == pytest.approx(p + (1 - keep) * (d - p), abs=1e-14)
        moved += now["treasury_equity_priced_gap"] != p
        before = now
    assert moved > 30


def test_a_restore_reproduces_the_run():
    dials = dict(treasury_equity_lag=LAG, treasury_equity_lag_gain=GAIN, **GAP)
    e = engine(**dials)
    e.run_days(6, record=False)
    snap = e.state_snapshot()
    twin = engine(**dials)
    twin.restore_state(snap)
    assert twin.state_hash() == e.state_hash()
    for x in (e, twin):
        x.run_days(4, record=False, first_day=6)
    assert floats(e.prices()) == floats(twin.prices())
    assert e.state_hash() == twin.state_hash()


def test_no_draw_and_the_ten_year_moves():
    base, live = engine(**GAP), engine(treasury_equity_lag=LAG, treasury_equity_lag_gain=GAIN, **GAP)
    for x in (base, live):
        x.run_days(60, record=False)
    assert base.draws_consumed == live.draws_consumed
    assert base.state_snapshot()["economy"]["treasury_yield_10y"] != \
        live.state_snapshot()["economy"]["treasury_yield_10y"]
    assert base.state_hash() != live.state_hash()
