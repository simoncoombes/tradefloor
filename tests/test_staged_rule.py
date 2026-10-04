"""The staged screen's kill and settle rules keep their stated rates.

The claim: stage A kills a candidate that stage B would pass at most GAMMA
of the time, over K rows and L looks. These tests check the Student t
arithmetic the threshold is built from, the per-row bound against a
simulation of nested stage-A and stage-B seeds, and the exact rules for
count and every-seed rows.
"""
from __future__ import annotations

import math
import pathlib
import random
import sys

import pytest

CAL = pathlib.Path(__file__).resolve().parent.parent / "tools" / "calibration"
sys.path.insert(0, str(CAL))

import staged_rule as R  # noqa: E402


@pytest.mark.parametrize("p,df,want", [
    (0.975, 9, 2.262157), (0.995, 9, 3.249836), (0.99, 19, 2.539483),
    (0.95, 1, 6.313752), (0.975, 1e9, 1.959964)])
def test_t_quantiles_match_the_tables(p, df, want):
    assert R.t_inv(p, df) == pytest.approx(want, abs=2e-5)
    assert R.t_sf(want, df) == pytest.approx(1 - p, abs=1e-6)


def test_the_threshold_gives_the_rate_it_was_built_for():
    for rows, looks in ((1, 1), (10, 1), (20, 2), (148, 2)):
        c = R.kill_threshold(10, 30, rows=rows, looks=looks)
        assert R.false_kill_bound(c, 10, 30) == pytest.approx(R.GAMMA / (rows * looks), rel=1e-6)


def test_the_full_block_kills_on_its_own_reading():
    assert R.threshold(30, 30, 0.01) == 0.0
    assert math.isinf(R.threshold(1, 30, 0.01))


@pytest.mark.parametrize("theta", [-0.6, -0.2, 0.0, 0.2, 0.4])
def test_the_simulated_false_kill_rate_is_under_the_bound(theta):
    """Nested seeds, normal readings: P(kill | pass) <= the per-row bound."""
    rate = 0.01
    c = R.threshold(10, 30, rate)
    _, false_kill = R.simulate_false_kill(c, 10, 30, theta=theta, reps=6000, seed=11)
    # Monte Carlo noise on a rate near 0.01 over a few thousand passes.
    assert false_kill <= rate + 0.006


def test_skewed_readings_stay_near_the_bound():
    """A lognormal per-seed reading, the shape of a volatility row."""
    rate = 0.01
    c = R.threshold(10, 30, rate)

    def draw(rng: random.Random) -> float:
        return math.exp(rng.gauss(0.0, 0.6)) - math.exp(0.18)

    worst = max(R.simulate_false_kill(c, 10, 30, theta=t, reps=6000, seed=5, draw=draw)[1]
                for t in (-0.3, -0.1, 0.0, 0.1))
    assert worst <= 3 * rate, "skewed rows break the normal bound badly"


def test_a_row_far_outside_is_out_and_one_on_the_edge_is_open():
    row = R.Row("vol", lo=10.0, hi=20.0)
    c_k = R.kill_threshold(10, 30, rows=1)
    c_s = R.settle_threshold(10, 30, rows=1)
    assert R.decide(row, n=10, N=30, value=26.0, se=1.0, c_kill=c_k, c_settle=c_s).state == "out"
    assert R.decide(row, n=10, N=30, value=20.5, se=1.0, c_kill=c_k, c_settle=c_s).state == "open"
    assert R.decide(row, n=10, N=30, value=15.0, se=0.5, c_kill=c_k, c_settle=c_s).state == "in"
    assert R.decide(row, n=30, N=30, value=20.5, se=1.0, c_kill=0, c_settle=0).state == "out"


def test_the_margin_shrinks_what_settles():
    plain = R.Row("r", lo=0.0, hi=1.0)
    margin = R.Row("r", lo=0.0, hi=1.0, margin_se=1.0)
    kw = dict(n=10, N=30, value=0.86, se=0.06, c_kill=3.0, c_settle=2.0)
    assert R.decide(plain, **kw).state == "in"
    assert R.decide(margin, **kw).state == "open"


def test_count_rows_are_exact():
    row = R.Row("lrate", kind="count", max_bad_share=2 / 3)
    kw = dict(N=12, c_kill=0, c_settle=0)
    assert R.decide(row, n=6, bad=6, **kw).state == "open"     # 8 allowed
    assert R.decide(row, n=10, bad=9, **kw).state == "out"
    assert R.decide(row, n=10, bad=6, **kw).state == "in"      # 6 + 2 <= 8


def test_every_seed_rows_die_on_one_failure_and_never_settle_early():
    row = R.Row("s1b", kind="allseeds")
    assert R.decide(row, n=10, N=30, bad=1, c_kill=0, c_settle=0).state == "out"
    assert R.decide(row, n=10, N=30, bad=0, c_kill=0, c_settle=0).state == "open"
    assert R.decide(row, n=30, N=30, bad=0, c_kill=0, c_settle=0).state == "in"


def test_protocols_grow_only_while_a_row_they_feed_is_open():
    d = lambda state: R.Decision("r", state, 10, 30, None, None, "")  # noqa: E731
    grow = R.next_seed_count({"far": [d("in")], "near": [d("in"), d("open")]},
                             n=10, N=30, block=10)
    assert grow == {"far": 10, "near": 20}
    dead = R.next_seed_count({"a": [d("out")], "b": [d("open")]}, n=10, N=30, block=10)
    assert dead == {"a": 10, "b": 10}
    assert R.candidate_state([d("in"), d("open")]) == "open"
    assert R.candidate_state([d("in"), d("in")]) == "settled"


def test_bootstrap_se_is_close_to_the_formula_for_a_mean():
    rng = random.Random(3)
    xs = [rng.gauss(0, 1) for _ in range(30)]
    _, se = R.mean_se(xs)
    assert R.bootstrap_se(xs, lambda v: sum(v) / len(v)) == pytest.approx(se, rel=0.2)


def test_a_reading_with_no_spread_is_decided_without_dividing_by_zero():
    """Two identical seeds give a bootstrap se of 0 (box readings, 2026-10-04)."""
    row = R.Row("sk2", lo=0.998, hi=1.502)
    assert R.decide(row, n=2, N=30, value=1.02, se=0.0, c_kill=9.0, c_settle=9.0).state == "in"
    assert R.decide(row, n=2, N=30, value=1.9, se=0.0, c_kill=9.0, c_settle=9.0).state == "out"
