"""The shadow solver (noise, phase 4), on a synthetic day.

Real data is fetched by the tool and never committed, so what a test can
state is the solver's contract on the engine's own closes: a day whose
draws are known is reached by the solve from zero, the layout names the
schedule, and the year slicing and the idiosyncratic sd read the data
shape the tool feeds them.
"""

import math
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
#: pt-v16's own market and idiosyncratic jump intensities.
INTENSITIES = (0.0565753337, 0.0068895346)


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


# -- what a resumed run carries ----------------------------------------------

def test_a_resumed_run_carries_its_whole_record(short_days):
    """Prices and stream positions matching is necessary and was all the
    resume test checked. The run also ships the engine's truth table and
    daily bars, and a resumed engine has only what it recorded after the
    resume: five committed days gave 1200 truth rows and 30 daily bars run
    straight through, and 720 rows and 18 bars resumed at day 2.
    """
    pytest.importorskip("pyarrow")
    universe = tf.Universe.random(6, seed=3)

    def commit_days(engine, first, last, checkpoint=None):
        for k in range(first, last):
            fwd = shadow.Forward(engine, k, 6)
            x = np.random.default_rng(30 + k).normal(size=fwd.layout.size)
            checkpoint = fwd.commit(x, fwd.jump_patches(None, {}))
            engine.record(k)
        return checkpoint

    whole = tf.Engine(seed=11, universe=universe)
    commit_days(whole, 0, 5)
    whole_truth, whole_bars = shadow.record_of(whole)
    assert whole_truth and whole_bars

    part = tf.Engine(seed=11, universe=universe)
    checkpoint = commit_days(part, 0, 3)
    carried_truth, carried_bars = shadow.record_of(part)
    assert len(carried_truth) < len(whole_truth)

    other = tf.Engine(seed=11, universe=universe)
    shadow.resume(other, checkpoint)
    commit_days(other, 3, 5)
    own_truth, own_bars = shadow.record_of(other)
    # the resumed engine alone is short of the run
    assert len(own_truth) < len(whole_truth)
    assert len(own_bars) < len(whole_bars)
    # `resume` restores the boundary before its checkpoint day and re-runs
    # part of it, so the two records overlap on that day and the tool
    # counts it once
    cut = checkpoint["day"]
    assert any(r["day"] == cut for r in own_truth)
    assert shadow.trim_overlap(carried_truth, own_truth, cut) == whole_truth
    assert shadow.trim_overlap(carried_bars, own_bars, cut) == whole_bars
    # kept whole, the overlap would show up twice
    assert len(carried_truth) + len(own_truth) > len(whole_truth)


# -- what the greedy jump step recovers --------------------------------------

def _planted_day(seed, z, rng):
    engine = tf.Engine(seed=seed, universe=UNIVERSE)
    engine.open_market()
    engine.run_session(9, 30, 3, shadow.TICKS)
    fwd = shadow.Forward(engine, 1, len(UNIVERSE))
    x_true = rng.normal(size=fwd.layout.size)
    r_obs = fwd.returns(x_true, fwd.jump_patches(z, {}))
    return fwd, r_obs


def test_a_planted_downward_market_jump_is_recovered(short_days):
    """The direction the step fires in, on days whose jump is known.

    The jump size is jump_mean_market + jump_sigma_market * z and the
    mean is negative, so a downward jump sits at a normal the prior can
    afford. Four planted downward jumps, six names at 40 ticks, seeds 11
    upward, sigma 1e-3: all four found, none spurious.
    """
    rng = np.random.default_rng(7)
    found = 0
    spurious = 0
    for i, z in enumerate((-2.27, -1.85, -2.18, -3.10)):
        fwd, r_obs = _planted_day(11 + i, z, rng)
        out = shadow.solve_day(fwd, r_obs, INTENSITIES, sigma=1e-3)
        if out["jump_market"] is not None:
            found += 1
            assert out["jump_market"] < 0.0
        spurious += len(out["jump_company"])
    assert found == 4
    assert spurious == 0


def test_an_upward_market_jump_is_not_recoverable(short_days):
    """The direction it cannot fire in, and why.

    An upward jump needs a normal past -mean/sigma, which the preset puts
    at +3.46, and the prior on that normal costs more than the likelihood
    can repay. Reported rather than left for a reader to infer from a
    count of zero.
    """
    model = dict(tf.ModelParams.from_preset().to_dict())
    zero_at = -model["jump_mean_market"] / model["jump_sigma_market"]
    assert 3.0 < zero_at < 4.0
    rng = np.random.default_rng(7)
    for i, z in enumerate((4.14, 6.0, 8.0)):
        fwd, r_obs = _planted_day(41 + i, z, rng)
        out = shadow.solve_day(fwd, r_obs, INTENSITIES, sigma=1e-3)
        assert out["jump_market"] is None
    assert "upward" in shadow.JUMP_RECOVERY
    assert "downward" in shadow.JUMP_RECOVERY


def test_a_day_with_no_jump_fires_none(short_days):
    rng = np.random.default_rng(7)
    for i in range(4):
        fwd, r_obs = _planted_day(61 + i, None, rng)
        out = shadow.solve_day(fwd, r_obs, INTENSITIES, sigma=1e-3)
        assert out["jump_market"] is None
        assert out["jump_company"] == {}


# -- the sensitivity is measured ----------------------------------------------

def test_the_sensitivity_is_a_fresh_jacobian_at_the_solution(short_days):
    """Published as a sensitivity and read for a binding clamp, so it is a
    finite difference at the accepted solution rather than whatever the
    optimiser was carrying when it stopped."""
    rng = np.random.default_rng(5)
    seen_gap = False
    for seed in (11, 12, 13, 14):
        engine = tf.Engine(seed=seed, universe=UNIVERSE)
        engine.open_market()
        engine.run_session(9, 30, 3, shadow.TICKS)
        fwd = shadow.Forward(engine, 1, len(UNIVERSE))
        x_true = rng.normal(size=fwd.layout.size)
        r_obs = fwd.returns(x_true, fwd.jump_patches(None, {}))
        base = shadow.solve(fwd, r_obs, fwd.jump_patches(None, {}),
                            np.zeros(fwd.layout.size), sigma=1e-3)
        out = shadow.solve_day(fwd, r_obs, (0.0, 0.0), sigma=1e-3)
        S = len(fwd.layout.sectors)
        fresh = shadow.fd_jacobian(fwd, r_obs, base["x"],
                                   fwd.jump_patches(None, {}), 0)
        want = np.linalg.norm(fresh[:, 1 + S:1 + S + fwd.n], axis=0)
        assert np.allclose(out["jacobian_idio_norm"], want, rtol=1e-9)
        carried = np.linalg.norm(base["jacobian"][:, 1 + S:1 + S + fwd.n],
                                 axis=0)
        if not np.allclose(carried, want, rtol=1e-3):
            seen_gap = True
    # the two differ on at least one day, which is why it matters
    assert seen_gap


# -- the layout is a partition ------------------------------------------------

def test_each_unknown_moves_its_own_addresses_and_no_others(short_days):
    """The unknown-to-address mapping is the tool's core contract. A unit
    vector on unknown k moves exactly unknown k's addresses, each by
    1/sqrt(T), and the unknowns partition the addresses with none shared.
    """
    engine = tf.Engine(seed=11, universe=UNIVERSE)
    fwd = shadow.Forward(engine, 0, len(UNIVERSE))
    layout = fwd.layout
    step = 1.0 / np.sqrt(layout.ticks)
    zero = {(p.address.stream, p.address.kind, p.address.index): p.value
            for p in layout.patches(np.zeros(layout.size))}
    owned = []
    for k in range(layout.size):
        e = np.zeros(layout.size)
        e[k] = 1.0
        moved = set()
        for p in layout.patches(e):
            key = (p.address.stream, p.address.kind, p.address.index)
            if p.value != zero[key]:
                assert p.value == pytest.approx(zero[key] + step)
                moved.add(key)
        assert len(moved) == layout.ticks, k
        owned.append(moved)
    # a partition: disjoint, and together every address the layout writes
    for i in range(len(owned)):
        for j in range(i + 1, len(owned)):
            assert not (owned[i] & owned[j]), (i, j)
    assert len(set().union(*owned)) == layout.ticks * layout.size


# -- the idiosyncratic sd is the residual, not the return --------------------

def test_the_idiosyncratic_sd_removes_the_systematic_part():
    """A fixture whose beta term is large enough that dropping it leaves
    the band. With beta 2.0 against an index sd of 0.02 the systematic
    part is 0.04 beside an idiosyncratic 0.01, so the raw return's sd is
    four times the answer.
    """
    rng = np.random.default_rng(4)
    n = 400
    steps = rng.normal(0, 0.02, n)
    idx = np.exp(np.cumsum(steps))
    closes = {}
    for t in realdata.TICKERS:
        own = rng.normal(0, 0.01, n)
        closes[t] = list(np.exp(np.cumsum(2.0 * steps + own)))
    data = {"index": list(idx), "closes": closes}
    betas = {t: 2.0 for t in realdata.TICKERS}
    sd = shadow.real_idio_sd(data, betas, 1, n)
    assert all(0.008 < v < 0.012 for v in sd.values()), sd
    # the raw return's sd, which is what dropping the beta term leaves
    raw = float(np.std(np.diff(np.log(closes[realdata.TICKERS[0]]))))
    assert raw > 0.03


# -- the solver settings the lab measured ------------------------------------

def test_the_jacobian_refresh_is_reached(short_days):
    """The refresh interval is the lab's setting and the suite cannot
    separate its effect at six names. What it can state is that the branch
    runs: a solve with refresh off takes fewer forward evaluations than
    one that retakes the Jacobian every few steps.
    """
    engine = tf.Engine(seed=11, universe=UNIVERSE)
    fwd = shadow.Forward(engine, 0, len(UNIVERSE))
    x_true = np.random.default_rng(9).normal(size=fwd.layout.size)
    r_obs = fwd.returns(x_true, fwd.jump_patches(None, {}))
    counts = {}
    for refresh in (0, shadow.SOLVER["refresh"]):
        fwd.evals = 0
        shadow.solve(fwd, r_obs, fwd.jump_patches(None, {}),
                     np.zeros(fwd.layout.size), sigma=1e-3, refresh=refresh)
        counts[refresh] = fwd.evals
    assert counts[shadow.SOLVER["refresh"]] > counts[0]


# -- the prior at the ends of the interval ------------------------------------

def test_an_intensity_of_zero_or_one_does_not_raise(short_days):
    """math.log(0) is a domain error and both ends are values the surface
    accepts. No shipped preset sets either."""
    assert shadow.log_p(0.0) == shadow.LOG_FLOOR
    assert shadow.log_p(1.0) == 0.0
    assert shadow.log_p(0.5) == pytest.approx(math.log(0.5))
    rng = np.random.default_rng(7)
    fwd, r_obs = _planted_day(71, None, rng)
    for intensities in ((1.0, 0.5), (0.5, 0.0), (0.0, 0.0), (1.0, 1.0)):
        out = shadow.solve_day(fwd, r_obs, intensities, sigma=1e-3)
        assert out["residual"]


# -- a report is a function of the saved solve --------------------------------

def _saved_run(short_days_ticks=40, days=3, sensitivity=True):
    """A run dict of the shape the tool writes beside its report.

    Built from real solves on days the engine generated, so every number
    the report reads is one the solver produced.
    """
    universe = tf.Universe.random(6, seed=3)
    tickers = [f"N{i}" for i in range(len(universe))]
    rng = np.random.default_rng(11)
    engine = tf.Engine(seed=11, universe=universe)
    out_days = []
    for k in range(days):
        fwd = shadow.Forward(engine, k, len(universe))
        x_true = rng.normal(size=fwd.layout.size)
        r_obs = fwd.returns(x_true, fwd.jump_patches(None, {}))
        result = shadow.solve_day(fwd, r_obs, (0.05, 0.006), sigma=1e-3)
        fwd.commit(result["x"], result["jumps"])
        engine.record(k)
        macro = engine.macro_state
        out_days.append({
            "day": k, "date": f"2017-01-{k + 3:02d}",
            "x_market": result["x_market"], "x_idio": result["x_idio"],
            "x_sector": result["x_sector"],
            "jump_market": result["jump_market"],
            "jump_company": result["jump_company"],
            "max_abs_residual": float(np.max(np.abs(result["residual"]))),
            "converged": result["converged"], "clamped": [],
            "worst": tickers[0], "evals": result["evals"],
            "reached": True,
            "sensitivity": result["jacobian_idio_norm"],
            "vix_model": float(macro.vix), "vix_real": 14.0 + k,
            "level_gap_mean_abs": 0.01 * (k + 1),
            "mispricing_mean": -0.001 * (k + 1),
            "market_variance": [0.0002] * 6,
            "universe_stress": 0.0,
        })
    provenance = {"tradefloor": tf.version(), "commit": "abcdef0",
                  "preset": "pt-v16",
                  "model_fingerprint": engine.model_fingerprint,
                  "order_flow": "zero", "fundamentals": "synthetic",
                  "source": "a fixture", "fetched": {"N0": "2026-01-01"},
                  "url_template": "https://example.invalid/{ticker}"}
    if sensitivity:
        provenance["sensitivity"] = ("fresh finite difference at the "
                                     "accepted solution")
    return {
        "args": {"year": "calm", "preset": "pt-v16", "seed": 7,
                 "sigma": 1e-3, "days": days, "null_days": 0},
        "year": "2017", "sessions": [0, days], "days": out_days,
        "provenance": provenance, "intensities": [0.05, 0.006],
        "tickers": tickers, "truth_rows": 0, "bars": [], "bar_rows": 0,
        "seconds": 1.0, "null": None,
        "real_idio_sd": {t: 0.02 for t in tickers},
    }


def test_a_report_re_renders_from_the_saved_solve(short_days):
    """A box uploads the JSON beside the report, so the report can be
    regenerated on fixed code without a new solve. That works only while
    every line is a function of the saved per-day solutions, which this
    states by rendering the dict and its JSON round trip and comparing
    byte for byte.
    """
    import json
    run = _saved_run()
    first = shadow.render(run)
    again = shadow.render(json.loads(json.dumps(run)))
    assert first == again
    assert first.encode("utf-8") == again.encode("utf-8")
    # and it says where the sensitivity came from
    assert "Measured as a fresh finite difference" in first


def test_a_report_says_when_the_sensitivity_was_not_measured(short_days):
    """A run solved before the column was measured fresh carries no such
    note, and the report has to say so rather than present the optimiser's
    estimate as a measurement."""
    run = _saved_run(sensitivity=False)
    text = shadow.render(run)
    assert "Measured as a fresh" not in text
    assert "optimiser's own Jacobian" in text
    assert "factor of nine" in text
    assert "binding clamp column below as the optimiser's" in text


def test_the_render_mode_writes_the_report_and_solves_nothing(
        short_days, tmp_path):
    import json
    run = _saved_run()
    saved = tmp_path / "shadow.json"
    saved.write_text(json.dumps(run), encoding="utf-8")
    out = tmp_path / "out"
    assert shadow.main(["--render", str(saved), "--out", str(out)]) == 0
    written = (out / "shadow.md").read_text(encoding="utf-8")
    assert written == shadow.render(run)
