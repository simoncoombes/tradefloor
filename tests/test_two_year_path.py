"""The 2-year as the expected policy path plus a term premium (`treasury_2y_expected_path`)."""
import numpy as np
import pytest

import tradefloor as tf

#: TWO_YEAR_TERM_PREMIUM, percentage points (the snapshot's units): ACM TP02's mean, 1961-2026.
TP = 0.433


def engine(seed=7, **dials):
    u = tf.Universe.random(12, seed=111)
    return tf.Engine(seed=seed, universe=u, model=tf.ModelParams.from_preset("pt-v20", **dials))


def day(e):
    e.open_market()
    e.run_session(9, 30, 3, 390)
    e.close_market()
    ec = e.state_snapshot()["economy"]
    return ec["treasury_yield_2y"], ec["federal_funds_rate"], np.frombuffer(e.prices(), "<f8")[:12].copy()


@pytest.mark.parametrize("preset", ["pt-v20", "pt-v21"])
def test_off_on_every_preset(preset):
    assert tf.ModelParams.from_preset(preset).to_dict()["treasury_2y_expected_path"] == 0.0


@pytest.mark.parametrize("value", [-0.1, 1.5])
def test_range(value):
    with pytest.raises(Exception, match="treasury_2y_expected_path"):
        tf.ModelParams.from_preset("pt-v20", treasury_2y_expected_path=value)


def test_zero_is_the_blend_as_it_stood():
    a, b = engine(), engine(treasury_2y_expected_path=0.0)
    assert a.model.fingerprint == b.model.fingerprint
    for _ in range(30):
        ya, _, pa = day(a)
        yb, _, pb = day(b)
        assert ya == yb and np.array_equal(pa, pb)


def test_without_a_forecast_the_path_is_the_policy_rate():
    # pt-v20's 2-year is the formula with no noise of its own, so off a
    # meeting it is the target: the rate now plus the premium.
    e = engine(treasury_2y_noise=0.0, treasury_2y_expected_path=1.0)
    assert e.model.fingerprint != engine(treasury_2y_noise=0.0).model.fingerprint
    hits = 0
    for _ in range(40):
        y2, ff, _ = day(e)
        hits += abs(y2 - (ff + TP)) < 1e-12
    assert hits >= 30


def test_with_a_forecast_the_path_is_the_strips():
    e = engine(treasury_2y_noise=0.0, treasury_2y_expected_path=1.0, forecast_horizon_sessions=130.0)
    hits = 0
    for _ in range(40):
        f = e.forecast()
        y2, ff, _ = day(e)
        if f is None:
            continue
        p = [100 * r for r in f["policy_rate"]]   # fractions, as the binding returns them
        path = (sum(p) + p[-1] * (504 - len(p))) / 504
        hits += abs(y2 - (path + TP)) < 1e-12
    assert hits >= 30


def test_half_the_dial_is_half_the_way():
    blend, half, full = (engine(treasury_2y_noise=0.0, treasury_2y_expected_path=d) for d in (0.0, 0.5, 1.0))
    for _ in range(10):
        y0, _, p0 = day(blend)
        y5, _, p5 = day(half)
        y1, _, p1 = day(full)
    assert abs(y5 - 0.5 * (y0 + y1)) < 1e-12
