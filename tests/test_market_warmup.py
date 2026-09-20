"""The market-side warm-up: `market_burn_in_sessions`.

`macro_burn_in_days` settles the ECONOMY by running `Engine::advance_day`.
`Engine::close_market` -- where the slow variance LEVEL is drawn and where
the factor's two variance components close -- is never called during it, so
the level opens from its stationary distribution and every variance state it
acts THROUGH opens cold.  `market_burn_in_sessions` warms those components
to the level the run opens on.

The acceptance test is the one `programme/results/level-sigma-horizon.md`
section 2.1 states and measures, and it needs no model: cut a 504-session
recording at session 252.  Both halves are 252-session windows of a level
that is stationary from session one, so stationarity says they must read the
same.  MEASURED there at `market_vol_level_sigma` 0.085, they do not --
`sd(log window variance)` reads 0.6938 +/- 0.0602 over the first half and
0.9570 +/- 0.0609 over the second.  With the warm-up on they must.

That test runs a few hundred seed-years and is gated on
`TRADEFLOOR_SLOW_TESTS`.  Everything above it is cheap, and the cheap tests
carry the two properties the change stands on: at the off value nothing
moves at all, and at ANY value no draw is taken on any stream.
"""

from __future__ import annotations

import math
import os
from concurrent.futures import ProcessPoolExecutor

import pytest

import tradefloor
from tradefloor import _core

SLOW = bool(os.environ.get("TRADEFLOOR_SLOW_TESTS")
            or os.environ.get("PRETIUM_SLOW_TESTS"))

#: The registered length, `level-sigma-horizon.md` section 8: the measured
#: envelope is flat from session 350 and 504 is the next round number past
#: it.
WARM = 504.0


#: The market variance process the warm-up was built for and MEASURED on:
#: the 2026-09-14 pt-v19 vector's GJR triple, slow pole and stochastic
#: level. The 2026-09-20 recomposition returned all of these to pt-v18's
#: values, so the shipped default no longer enters the level's
#: stationary-opening arm at all; the mechanism is unchanged and every
#: figure below was measured on it, so this file switches it on explicitly
#: rather than re-deriving the shares on a vector that never runs it.
LEVEL_ON = dict(market_vol_alpha=0.0066, market_vol_beta=0.8946,
                market_vol_gamma=0.1556, market_vol_slow_persistence=0.9913,
                market_vol_level_persistence=0.9977,
                market_vol_level_sigma=0.085)


def _engine(seed: int, names: int, **overrides):
    universe = _core.random_instruments(names, seed=seed)
    model = tradefloor.ModelParams.from_preset("pt-v19", **{**LEVEL_ON, **overrides})
    return _core.Engine(seed=seed, universe=universe, model=model), universe


# ── the gate ───────────────────────────────────────────────────────────────

def test_the_dial_at_zero_is_the_engine_that_stood_before_it():
    """Bit-identity, asserted on the state hash rather than on a tolerance.

    The branch is `market_burn_in_sessions > 0.0` inside the level's own
    stationary-opening arm, so at 0.0 no state moves and no arithmetic
    runs.  This is the property the known-answer digest rests on, said once
    where a reader will look for it.
    """
    a, _ = _engine(11, 12)
    b, _ = _engine(11, 12, market_burn_in_sessions=0.0)
    a.run_days(8)
    b.run_days(8)
    assert a.state_hash() == b.state_hash()
    assert tradefloor.ModelParams.from_preset().market_burn_in_sessions == 0.0


def test_the_default_preset_ships_it_off_on_every_preset():
    for name in _core.preset_names():
        value = tradefloor.ModelParams.from_preset(name).market_burn_in_sessions
        assert value == 0.0, f"{name} ships the warm-up on"


# ── the draw schedule ──────────────────────────────────────────────────────

def test_the_warm_up_takes_no_draw_on_any_stream():
    """The constraint the whole design is shaped by.

    Nothing settable may change how many draws are taken or in what order.
    A warm-up that STEPPED the close for 504 sessions would have to draw --
    a level innovation and a day factor per session -- and would then need a
    stream of its own, the way `stream::JUMPS`, `stream::VOLUME` and
    `stream::MARKET_VOL_LEVEL` each needed one.  This one is a
    deterministic function of the level draw the close already makes, so it
    declares no stream and moves no count: the assertion is over EVERY
    stream, in both directions, at three settings.
    """
    off, _ = _engine(21, 10)
    off.run_days(6)
    base = off.draws_by_stream()
    for sessions in (63.0, 504.0, 1008.0):
        on, _ = _engine(21, 10, market_burn_in_sessions=sessions)
        on.run_days(6)
        assert on.draws_by_stream() == base, (
            f"the warm-up moved the draw schedule at {sessions} sessions")
        # And it did move the market, so the equality above is not the
        # equality of two engines that did the same thing.
        assert on.state_hash() != off.state_hash()


def test_it_is_inert_without_the_level_it_exists_to_warm():
    """The scope, spelled as a test rather than as a comment.

    The state warmed is the state the LEVEL acts through.  With
    `market_vol_level_sigma` 0.0 the close never enters the level arm, so
    the warm-up never runs -- which is what keeps the zero-sigma arm usable
    as the control for a warmed ladder and an unwarmed one at once.
    """
    off, _ = _engine(31, 10, market_vol_level_sigma=0.0)
    on, _ = _engine(31, 10, market_vol_level_sigma=0.0,
                    market_burn_in_sessions=WARM)
    off.run_days(6)
    on.run_days(6)
    # The TRAJECTORY, not the state hash: `state_hash` folds in the model
    # fingerprint, so two engines carrying different dial VALUES hash
    # differently whatever they did, and asserting on it here would be
    # asserting that a dial exists rather than that it did nothing.
    assert off.prices() == on.prices()
    assert off.state_snapshot()["market_variance"] == \
        on.state_snapshot()["market_variance"]
    assert off.draws_by_stream() == on.draws_by_stream()


# ── what it actually does to the state ─────────────────────────────────────

#: MEASURED, `tools/calibration/warmup_probe.py` at 32 seeds on a 60-name
#: roster: the regression slope of a 63-session block's mean
#: `log(factor variance)` on the same block's mean `log L`, averaged over
#: blocks 3 to 8 -- that is, over the sessions where
#: `level-sigma-horizon.md` 2.2 measures the envelope to be flat.  It is
#: what "equilibrated" means for this state, as a number.
EQUILIBRATED_ENVELOPE = 0.78


def _opening_state(seed: int, **overrides) -> tuple[float, float, float, float]:
    """`(log level, log(fast/base), log(slow/base), log(mixture/base))`
    after the first close -- the earliest the state can be read, because
    the level the warm-up needs is drawn at that close."""
    engine, _ = _engine(seed, 10, **overrides)
    engine.run_days(1)
    snap = engine.state_snapshot()
    base = tradefloor.ModelParams.from_preset("pt-v19").market_factor_sigma ** 2
    mv = snap["market_variance"]
    return (snap["market_vol_log_level"],
            math.log(mv[2] / base), math.log(mv[3] / base),
            math.log(mv[0] / base))


def _slope(rows, col):
    xs = [r[0] for r in rows]
    ys = [r[col] for r in rows]
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    return sxy / sxx


def test_the_components_open_on_the_level_instead_of_travelling_to_it():
    """The mechanism, read off the state on session one.

    Cold, both components sit at the unscaled baseline whatever the level
    did, so `log(v / base)` is uncorrelated with the level by construction
    and the slope is zero.  Warmed, each carries its own share of the
    opening level.

    The shares `(1 - p) / (1 - p phi)` -- 0.903 fast, 0.792 slow -- are
    asserted on the warm-up's OWN output by
    `factor_vol.rs::the_warmed_components_carry_their_own_share_of_the_opening_level`.
    What this test reads is one close later, which is the earliest a Python
    caller can read anything, and the close has already stepped the state
    once against a day factor drawn at the COLD sigma.  So the number here
    is a little under the share, and the band is drawn around the
    measurement rather than around the derivation.

    The assertion that matters is the last one: the warmed MIXTURE opens at
    the envelope the probe measures a fully relaxed engine to have.  That
    is the whole claim of the change, in one number.
    """
    seeds = range(400, 460)
    cold = [_opening_state(s) for s in seeds]
    warm = [_opening_state(s, market_burn_in_sessions=WARM) for s in seeds]

    # The level is drawn on the same stream at the same position in both
    # arms, so the two lists carry the SAME levels and the comparison is
    # one arm against the other on one random world.
    assert [r[0] for r in cold] == [r[0] for r in warm]

    for col, what in ((1, "fast"), (2, "slow"), (3, "mixture")):
        assert abs(_slope(cold, col)) < 0.12, (
            f"a cold {what} component already knows the level")
    assert _slope(warm, 1) == pytest.approx(0.80, abs=0.08)
    assert _slope(warm, 2) == pytest.approx(0.74, abs=0.08)
    assert _slope(warm, 3) == pytest.approx(EQUILIBRATED_ENVELOPE, abs=0.08), (
        "the warmed engine does not open at the envelope a relaxed one has")


def test_a_warm_up_past_the_slow_component_s_memory_stops_moving():
    """504 is a length and not a switch, so the length has to be checkable.

    The recursion converges geometrically at the slow component's own
    persistence, 0.9913, which leaves 0.012 of the initial condition at 504
    sessions and 0.0002 at 1,008.  So doubling the warm-up must move the
    opening state by far less than halving it does.  If it did not, the
    dial would be a switch wearing a session count's clothes.
    """
    seeds = range(500, 520)
    at = {n: [_opening_state(s, market_burn_in_sessions=float(n))
              for s in seeds]
          for n in (252, 504, 1008)}

    def spread(a, b):
        return max(abs(x[1] - y[1]) + abs(x[2] - y[2])
                   for x, y in zip(at[a], at[b]))

    assert spread(504, 1008) < 0.25 * spread(252, 504)


# ── the acceptance test ────────────────────────────────────────────────────

def _window_log_variance(job) -> tuple[float, float]:
    """`(log var of sessions 1-252, log var of sessions 253-504)`."""
    seed, names, sessions, overrides = job
    import numpy as np

    universe = _core.random_instruments(names, seed=seed)
    model = tradefloor.ModelParams.from_preset("pt-v19", **overrides)
    engine = _core.Engine(seed=seed, universe=universe, model=model)
    shares = np.array([float(i.shares_outstanding) for i in universe])
    levels = []
    for _ in range(sessions + 1):
        prices = np.frombuffer(engine.prices(), dtype="<f8")
        levels.append(float((prices * shares).sum()))
        engine.run_days(1)
    lv = np.log(np.array(levels))
    ret = np.diff(lv)
    half = sessions // 2
    return (math.log(float(ret[:half].var(ddof=1))),
            math.log(float(ret[half:].var(ddof=1))))


def _sd_and_jackknife(values):
    """`sd` across rosters with a delete-one jackknife error, the estimator
    `cascade-level.py` uses and `level-sigma-horizon.md` reproduces."""
    import numpy as np

    v = np.asarray(values, dtype=float)
    full = float(v.std(ddof=1))
    n = len(v)
    reps = np.array([np.delete(v, i).std(ddof=1) for i in range(n)])
    err = float(np.sqrt((n - 1) / n * ((reps - reps.mean()) ** 2).sum()))
    return full, err


def split_half(rosters: int, names: int, sessions: int, workers: int,
               **overrides):
    """`(rows, (sd, err) for h1, (sd, err) for h2)` over one set of
    recordings.  The rows are kept so the two arms can be compared PAIRED:
    the warm-up takes no draw, so an arm with it on and an arm with it off
    share every stream position, including the level's, and the difference
    between them is one change on one random world."""
    jobs = [(1000 + i, names, sessions, overrides) for i in range(rosters)]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        rows = list(pool.map(_window_log_variance, jobs))
    h1 = [r[0] for r in rows]
    h2 = [r[1] for r in rows]
    return rows, _sd_and_jackknife(h1), _sd_and_jackknife(h2)


def _paired_bootstrap(warm_rows, cold_rows, draws=4000, seed=11):
    """The ratio `sd(h2)/sd(h1)` for both arms, resampled over ROSTERS with
    the pairing kept, plus the bootstrap distribution of the difference."""
    import numpy as np

    rng = np.random.default_rng(seed)
    w = np.asarray(warm_rows, dtype=float)
    c = np.asarray(cold_rows, dtype=float)
    n = len(w)
    out = np.empty((draws, 2))
    for d in range(draws):
        idx = rng.integers(0, n, n)
        for j, arm in enumerate((w, c)):
            s = arm[idx]
            out[d, j] = s[:, 1].std(ddof=1) / s[:, 0].std(ddof=1)
    return out[:, 0], out[:, 1]


@pytest.mark.skipif(not SLOW, reason="set TRADEFLOOR_SLOW_TESTS=1")
def test_the_two_halves_of_a_warmed_recording_read_the_same():
    """The acceptance test, written so it can fail.

    Both halves are 252-session windows of a stationary level, so
    stationarity says they must read the same.  Two assertions, and the
    second is what makes the first mean something:

    * the WARMED arm's two halves agree inside three jackknife errors of
      their ratio -- the property the change is for;
    * the UNWARMED arm's do not, on the same rosters, the same seeds and
      the same statistic -- which is the control that says the test is
      capable of seeing the defect at all.

    If the second assertion ever fails, this test has stopped measuring
    anything and the first one passing is not evidence.
    """
    import numpy as np

    rosters = int(os.environ.get("TF_WARMUP_ROSTERS", "24"))
    names = int(os.environ.get("TF_WARMUP_NAMES", "40"))
    workers = int(os.environ.get("TF_WARMUP_WORKERS", "4"))

    wrows, (w1, e1), (w2, e2) = split_half(rosters, names, 504, workers,
                                           market_burn_in_sessions=WARM)
    crows, (c1, f1), (c2, f2) = split_half(rosters, names, 504, workers)

    warm_ratio = w2 / w1
    cold_ratio = c2 / c1
    # The ratio's error, propagated from the two jackknife errors.  They
    # are not independent -- the halves come from the same rosters -- so
    # this is conservative in the direction that makes the test harder to
    # pass, which is the right direction for an acceptance test.
    err = warm_ratio * math.hypot(e1 / w1, e2 / w2)
    wb, cb = _paired_bootstrap(wrows, crows)

    print(f"\nwarmed   h1={w1:.4f} +/- {e1:.4f}  h2={w2:.4f} +/- {e2:.4f}  "
          f"ratio={warm_ratio:.4f} +/- {err:.4f}")
    print(f"unwarmed h1={c1:.4f} +/- {f1:.4f}  h2={c2:.4f} +/- {f2:.4f}  "
          f"ratio={cold_ratio:.4f}")
    print(f"warm-up moves h1 by {w1 - c1:+.4f} and h2 by {w2 - c2:+.4f}")
    print(f"paired bootstrap of the ratio difference, warmed minus "
          f"unwarmed: {np.mean(wb - cb):+.4f} "
          f"[{np.percentile(wb - cb, 2.5):+.4f}, "
          f"{np.percentile(wb - cb, 97.5):+.4f}], "
          f"P(warmed < unwarmed) = {np.mean(wb < cb):.3f}")

    assert abs(warm_ratio - 1.0) < 3.0 * err, (
        f"the warmed halves disagree: {w1:.4f} against {w2:.4f}")
    # The CONTROL, and it is paired: the two arms share every seed and
    # every stream position, so this is one change measured on one random
    # world rather than two runs compared across their own noise.
    assert np.mean(wb < cb) > 0.9, (
        "the unwarmed control did not show the defect this test exists "
        f"for: {cold_ratio:.4f} against {warm_ratio:.4f}")
    # And the warm-up must lift the FIRST half, not the second: that
    # asymmetry is the whole signature, and a change that lifted both
    # equally would be a change in the dose rather than in the burn-in.
    assert (w1 - c1) > 2.0 * abs(w2 - c2), (
        f"the warm-up moved h1 by {w1 - c1:+.4f} and h2 by {w2 - c2:+.4f}, "
        "which is not the signature of a burn-in")
