"""The shadow solver (noise, phase 4), on a synthetic day.

Real data is fetched by the tool and never committed, so what a test can
state is the solver's contract on the engine's own closes: a day whose
draws are known is reached by the solve from zero, the layout names the
schedule, and the year slicing and the idiosyncratic sd read the data
shape the tool feeds them.
"""

import os
import sys

import pytest

np = pytest.importorskip("numpy")

import tradefloor as tf  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools", "shadow"))

import data as realdata  # noqa: E402
import shadow  # noqa: E402

UNIVERSE = tf.Universe.random(6, seed=3)


@pytest.fixture
def short_days(monkeypatch):
    monkeypatch.setattr(shadow, "TICKS", 40)


def test_the_layout_is_the_schedule(short_days):
    engine = tf.Engine(seed=11, universe=UNIVERSE)
    fwd = shadow.Forward(engine, 0, len(UNIVERSE))
    layout = fwd.layout
    assert layout.ticks == 40
    assert sorted(layout.company) == list(range(len(UNIVERSE)))
    assert all(len(v) == 40 for v in layout.company.values())
    assert all(len(v) == 40 for v in layout.sector.values())
    assert layout.size == 1 + len(layout.sectors) + len(UNIVERSE)
    patches = layout.patches(np.ones(layout.size))
    assert len(patches) == 40 * layout.size
    # a shift of one unit on the day is 1/sqrt(T) on every tick
    first = layout.market[0]
    assert patches[0].address == first.address
    assert patches[0].value == first.value + 1.0 / np.sqrt(40)


def test_the_solve_reaches_a_day_whose_draws_are_known(short_days):
    """The closes a known aggregate vector produces are reached from zero
    within the run's tolerance. The vector itself is not claimed: with one
    market and one innovation per sector beside the names, more unknowns
    than closes leave it unidentified, and the report says so of the day
    sum only."""
    engine = tf.Engine(seed=11, universe=UNIVERSE)
    fwd = shadow.Forward(engine, 0, len(UNIVERSE))
    rng = np.random.default_rng(1)
    x_true = rng.normal(size=fwd.layout.size)
    jumps = fwd.jump_patches(None, {})
    assert jumps == []  # day zero has no previous close
    r_obs = fwd.returns(x_true, jumps)
    assert np.any(r_obs != fwd.returns(np.zeros(fwd.layout.size), jumps))
    # The criterion is the run's: five sigma at sigma 1e-3 in log return.
    # The cent grid puts a floor under the residual (two basis points on
    # a fifty-dollar name), so an exact fit is not the claim.
    out = shadow.solve(fwd, r_obs, jumps, np.zeros(fwd.layout.size), sigma=1e-3)
    assert np.max(np.abs(out["residual"])) < 5e-3
    assert np.max(np.abs(out["residual"])) < 0.1 * np.max(np.abs(r_obs))
    day = shadow.solve_day(fwd, r_obs, (0.0, 0.0), sigma=1e-3)
    assert np.max(np.abs(day["residual"])) < 5e-3
    assert day["jump_market"] is None and day["jump_company"] == {}
    assert len(day["jacobian_idio_norm"]) == len(UNIVERSE)
    assert all(v > 0 for v in day["jacobian_idio_norm"])


def test_a_jump_at_the_previous_close_is_addressed(short_days):
    engine = tf.Engine(seed=11, universe=UNIVERSE)
    engine.open_market()
    engine.run_session(9, 30, 3, 40)
    fwd = shadow.Forward(engine, 1, len(UNIVERSE))
    patches = fwd.jump_patches(2.0, {3: -1.0})
    kinds = [(p.address.kind, p.address.index - (fwd.jump_u if p.address.kind == "uniform" else fwd.jump_z), p.value) for p in patches]
    assert ("uniform", 0, 0.0) in kinds and ("normal", 0, 2.0) in kinds
    assert ("uniform", 4, 0.0) in kinds and ("normal", 4, -1.0) in kinds
    assert sum(1 for k in kinds if k[0] == "uniform" and k[2] == 1.0) == len(UNIVERSE) - 1


def test_year_slicing_and_the_idiosyncratic_sd():
    dates = [f"2016-12-{d:02d}" for d in (28, 29, 30)] + [f"2017-01-{d:02d}" for d in (3, 4, 5)] + ["2018-01-02"]
    d = {"dates": dates}
    assert realdata.year_slice(d, "2017") == (3, 6)
    rng = np.random.default_rng(2)
    n = 300
    idx = np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    closes = {}
    for t in realdata.TICKERS:
        closes[t] = list(np.exp(np.cumsum(0.5 * np.diff(np.log(idx), prepend=0.0) + rng.normal(0, 0.02, n))))
    data = {"index": list(idx), "closes": closes}
    betas = {t: 0.5 for t in realdata.TICKERS}
    sd = shadow.real_idio_sd(data, betas, 1, n)
    assert set(sd) == set(realdata.TICKERS)
    assert all(0.015 < v < 0.025 for v in sd.values())


def test_commit_with_a_clean_overlay_matches_a_straight_run(short_days):
    """The overlay is emptied at each closed boundary and refilled with the
    day's patches; the closes are the same as with every day's patches
    kept, and the overlay stays one day deep."""
    universe = tf.Universe.random(6, seed=3)

    sizes = []

    def drive(engine, keep):
        prices = []
        for k in range(3):
            fwd = shadow.Forward(engine, k, 6)
            sizes.append(fwd.layout.size)
            # the same vector on both engines: the layout size is the
            # roster's, and the generator is seeded by the day
            x = np.random.default_rng(10 + k).normal(size=fwd.layout.size)
            if keep:
                fwd._drive(engine, fwd.jump_patches(None, {}) + fwd.layout.patches(x))
                engine.record(k)
            else:
                fwd.commit(x, fwd.jump_patches(None, {}))
            prices.append(shadow.prices(engine).copy())
        return prices

    kept = drive(tf.Engine(seed=11, universe=universe), keep=True)
    clean_engine = tf.Engine(seed=11, universe=universe)
    clean = drive(clean_engine, keep=False)
    for a, b in zip(kept, clean):
        assert np.array_equal(a, b)
    # one day of market patches (every sector's normal is drawn each tick,
    # whatever the roster spans) plus the jump pair per name and market
    one_day = 40 * sizes[-1]
    assert len(clean_engine.draw_patches()) <= one_day + 2 * (6 + 1)


def test_a_resumed_engine_continues_bit_for_bit(short_days):
    universe = tf.Universe.random(6, seed=3)
    engine = tf.Engine(seed=11, universe=universe)
    checkpoint = None
    for k in range(3):
        fwd = shadow.Forward(engine, k, 6)
        x = np.random.default_rng(20 + k).normal(size=fwd.layout.size)
        checkpoint = fwd.commit(x, fwd.jump_patches(None, {}))
    other = tf.Engine(seed=11, universe=universe)
    # through JSON, as the partial record carries it
    import json
    checkpoint = shadow.decode(json.loads(json.dumps(shadow.encode(checkpoint))))
    shadow.resume(other, checkpoint)
    assert np.array_equal(shadow.prices(other), shadow.prices(engine))
    assert other.stream_positions() == engine.stream_positions()
    for e in (engine, other):
        fwd = shadow.Forward(e, 3, 6)
        x = np.random.default_rng(23).normal(size=fwd.layout.size)
        fwd.commit(x, fwd.jump_patches(None, {}))
    assert np.array_equal(shadow.prices(other), shadow.prices(engine))
