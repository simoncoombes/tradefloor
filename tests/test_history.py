"""The pre-history is the same market run earlier, and reading it moves nothing.

These pin the identity the design rests on: N untraded sessions of
pre-history followed by T more reach, state hash for state hash, the market
an untraded run of N+T sessions reaches, forks of it are identical, and the
harness's own untraded baseline agrees on the prices.
"""

import pytest

import tradefloor as tf
from tradefloor.harness import _f64, _run_untraded
from tradefloor.sandbox import SandboxError
from tradefloor.history import (MAX_HISTORY_DAYS, History, check_history_days,
                                prehistory, run_untraded_day)

SEED = 2001
SPD, TPS = 6, 65


def _universe():
    return list(tf.Universe.random(6, seed=111))


def _engine(u):
    return tf.Engine(seed=SEED, universe=u, model="pt-v20")


@pytest.mark.parametrize("n,t", [(0, 2), (3, 2)])
def test_prehistory_then_run_equals_longer_untraded_run(n, t):
    u = _universe()
    e = _engine(u)
    hist = prehistory(e, n)
    assert len(hist) == n and hist.days == list(range(-n, 0))
    for _ in range(t):
        run_untraded_day(e, hist, SPD, TPS, (9, 30, 3))
    off = _engine(u)
    prehistory(off, n + t)
    assert e.state_hash() == off.state_hash()
    assert hist.days == list(range(-n, t))
    prices = list(_f64(e.prices()))
    assert prices == _run_untraded(SEED, u, None, n + t, SPD, TPS, 9, 30, 3,
                                   None, "pt-v20")


def test_forks_after_prehistory_are_identical():
    u = _universe()
    e = _engine(u)
    prehistory(e, 2)
    a, b = e.fork(2)
    ha, hb = History(a.tickers, SPD, 0), History(b.tickers, SPD, 0)
    run_untraded_day(a, ha, SPD, TPS, (9, 30, 3))
    run_untraded_day(b, hb, SPD, TPS, (9, 30, 3))
    assert a.state_hash() == b.state_hash()
    assert ha.closes() == hb.closes()


def test_bars_are_consistent_and_published_only():
    u = _universe()
    e = _engine(u)
    hist = prehistory(e, 2)
    for d in range(2):
        for i in range(len(u)):
            hi, lo = hist.rows("high")[d][i], hist.rows("low")[d][i]
            cl, op = hist.rows("close")[d][i], hist.rows("open")[d][i]
            assert lo <= cl <= hi and lo <= op <= hi
    assert len(hist.steps()) == 2 * SPD
    assert hist.window(3, 1) == hist.rows("open")[-2:]
    assert len(hist.macro("vix")) == 2
    with pytest.raises(SandboxError):
        hist.macro("qe_pe_boost")


@pytest.mark.parametrize("bad", [-1, MAX_HISTORY_DAYS + 1, 1.5, True, "252"])
def test_history_days_validated(bad):
    with pytest.raises(tf.ValidationError):
        check_history_days(bad)
