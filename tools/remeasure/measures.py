"""Measurement groups for the re-measurement harness.

Each function here recomputes one family of published figures from the
installed `pretium` package and returns a flat dict of key -> value. The
mapping from published claims to these keys lives in `inventory.json`;
`remeasure.py` joins the two and writes the delta report.

Groups are independent of each other and are safe to run concurrently in
threads (the engine releases the GIL for session compute), EXCEPT the groups
listed in `TIMED_GROUPS`, which measure wall clocks and must run with the
machine otherwise quiet. `remeasure.py` runs those serially after the pool
has drained.

Every function takes a `Ctx` and must not read or write anything outside the
repository checkout it is given and its output dict. Nothing here edits docs;
the whole point is to make the eventual doc edit mechanical, not to start it.
"""

from __future__ import annotations

import importlib.util
import json
import re
import statistics
import struct
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pretium as pt
import pyarrow as pa
from pretium.baselines import BuyAndHold, Momentum, Oracle, capture_ratio, reference_agents
from pretium.scenario import Scenario, compare, run_scenario


@dataclass
class Ctx:
    root: Path        # repository checkout the docs lines refer to
    workers: int = 6  # threads for seed-parallel measurement


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _u(n: int, seed: int):
    return pt.Universe.random(n, seed=seed)


def _f64(buf: bytes) -> list[float]:
    return list(struct.unpack("<%dd" % (len(buf) // 8), buf))


def _run_days_recorded(engine, days: int, scenario=None) -> None:
    for day in range(days):
        if scenario is not None:
            scenario.apply(engine, day)
        engine.open_market()
        engine.run_session(9, 30, 3, 390)
        engine.close_market()
        engine.record(day)


def _median(xs):
    return statistics.median(xs)


# ---------------------------------------------------------------------------
# determinism, versioning, structural constants
# ---------------------------------------------------------------------------

def g_determinism(ctx: Ctx) -> dict:
    spec = importlib.util.spec_from_file_location(
        "known_answer", ctx.root / "tests" / "known_answer.py")
    ka = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ka)
    digest = ka.known_answer_digest()
    pinned = json.loads((ctx.root / "tests" / "known_answer.json").read_text())

    u = _u(20, 11)
    reversed_u = pt.Universe(list(reversed(list(u))))
    preset = pt.model_preset()

    from pretium.facts import MARGINAL, REAL_MARKETS
    return {
        "kat_digest": digest,
        "kat_digest_pinned": pinned["sha256"],
        "version": pt.version(),
        "preset_name": preset["name"],
        "preset_keys": len([k for k in preset if k != "name"]),
        "fp_len": len(u.fingerprint),
        "fp_reversal_differs": u.fingerprint != reversed_u.fingerprint,
        "facts_marginal": len(MARGINAL),
        "facts_dependence": len(REAL_MARKETS) - len(MARGINAL),
        "oracle_default_top_k": Oracle().top_k,
    }


def g_consts(ctx: Ctx) -> dict:
    """Claims that are constants in the source or derivable from the API."""
    tick_rs = (ctx.root / "rust" / "src" / "market" / "tick.rs").read_text()
    garch_rs = (ctx.root / "rust" / "src" / "market" / "garch.rs").read_text()
    micro_rs = (ctx.root / "rust" / "src" / "microstructure.rs").read_text()

    def const(text: str, name: str) -> float:
        m = re.search(rf"const {name}: f64 = ([0-9.e-]+);", text)
        return float(m.group(1)) if m else float("nan")

    market_sigma = const(tick_rs, "MARKET_FACTOR_SIGMA")
    alpha = const(garch_rs, "ALPHA")
    beta = const(garch_rs, "BETA")

    sigmas = sorted(pt.sector_daily_sigma(s) for s in pt.sectors())
    spread_formula = bool(re.search(r"vix[^\n]*15[^\n]*30", micro_rs)
                          or "(vix - 15.0) / 30.0" in micro_rs)

    # truth-table geometry for the sweeps page's byte arithmetic
    u = _u(6, 7)
    e = pt.Engine(seed=3, universe=u)
    e.run_days(1, record=True)
    truth_cols = len(pa.table(e.truth()).column_names)

    # reading-results: the book table is "40x the rows" of the tick tables.
    e.snapshot_book(day=0, tick=0)
    book = pa.table(e.book_table())
    rows_per_snapshot = book.num_rows / len(u) if book.num_rows else float("nan")

    return {
        "market_factor_sigma": market_sigma,
        "sector_sigma_min": sigmas[0],
        "sector_sigma_max": sigmas[-1],
        "sector_sigma_median": _median(sigmas),
        "garch_persistence": alpha + beta,
        "spread_formula_present": spread_formula,
        "impulse_day2": pt.impulse_response(3)[2],
        "truth_cols": truth_cols,
        "book_rows_per_tick_per_name": rows_per_snapshot,
    }


def g_arith(ctx: Ctx) -> dict:
    """Published numbers that are arithmetic on stated quantities."""
    rows_100x252 = 252 * 390 * 100
    return {
        "truth_rows_100x252": rows_100x252,            # "9.8 million rows"
        "buffers_mb": 12 * rows_100x252 * 8 / 1e6,     # "about 940 MB"
        "hundred_engines_gb": 100 * 12 * rows_100x252 * 8 / 1e9,  # "roughly 90 GB"
        "crossings_per_year": 252 * 390,               # "about 98,000"
        "crossings_five_fields": 252 * 390 * 5,        # "roughly 500,000"
        "workflow_truth_rows": 10 * 390 * 60,          # "234,000 rows"
        "market_share_of_variance_pct": (0.003 / 0.015) ** 2 * 100.0,  # "about 4%"
        "sigma_for_corr_030": 0.65 * 0.015,            # "roughly 0.0098"
        "llm_calls_default_run": 20,                   # one per day, 20 days
    }


# ---------------------------------------------------------------------------
# ground truth residual
# ---------------------------------------------------------------------------

def g_truth_residual(ctx: Ctx) -> dict:
    u = _u(20, 11)
    e = pt.Engine(seed=42, universe=u)
    e.run_days(5, record=True)
    t = pa.table(e.truth()).to_pydict()
    factors = ["reversion", "momentum", "crowd_lean", "company_news",
               "order_flow_impact", "short_squeeze_effect", "random_noise"]
    series: dict[int, list[tuple[tuple[int, int], float, float]]] = defaultdict(list)
    for i in range(len(t["instrument_id"])):
        fsum = sum(t[f][i] for f in factors)
        series[t["instrument_id"][i]].append(
            ((t["day"][i], t["tick"][i]), t["mispricing_s"][i], fsum))
    residuals = []
    for rows in series.values():
        rows.sort()
        for prev, cur in zip(rows, rows[1:]):
            residuals.append(abs((cur[1] - prev[1]) - cur[2]))
    residuals.sort()
    n = len(residuals)
    return {
        "median_residual": residuals[n // 2],
        "p99_residual": residuals[int(n * 0.99)],
        "max_residual": residuals[-1],
        "rows": n,
    }


# ---------------------------------------------------------------------------
# realism statistics
# ---------------------------------------------------------------------------

_REALISM_UNIVERSE = (40, 111)
_REALISM_KEYS = ["annualised_vol_pct", "excess_kurtosis", "return_acf1",
                 "abs_return_acf1", "cross_sectional_corr",
                 "volume_abs_return_corr", "leverage_effect",
                 "volume_change_acf1"]


def g_realism_sample(ctx: Ctx) -> dict:
    u = _u(*_REALISM_UNIVERSE)
    f = pt.facts.measure(seed=3, universe=u, days=252)
    out = {k: f[k] for k in _REALISM_KEYS}
    out["observations"] = f["observations"]
    out["fp_prefix"] = u.fingerprint[:16]
    return out


def g_realism_six(ctx: Ctx) -> dict:
    u = _u(*_REALISM_UNIVERSE)

    def one(seed: int):
        return pt.facts.measure(seed=seed, universe=u, days=252)

    with ThreadPoolExecutor(max_workers=min(6, ctx.workers)) as pool:
        runs = list(pool.map(one, range(1, 7)))

    out: dict = {}
    for key in _REALISM_KEYS + ["abs_return_acf20"]:
        values = [r[key] for r in runs]
        out[f"{key}_median"] = _median(values)
        out[f"{key}_lo"] = min(values)
        out[f"{key}_hi"] = max(values)
    return out


# ---------------------------------------------------------------------------
# evaluate-derived figures
# ---------------------------------------------------------------------------

def g_rebalance(ctx: Ctx) -> dict:
    """README's rebalance-frequency table. Method from the docstring at
    python/pretium/baselines.py:254-261 and tests/test_baselines.py:370."""
    u = _u(40, 7)
    out = {}
    for steps in (3, 6, 12):
        scores = pt.evaluate({"m": Momentum(lookback_days=1.0)}, seed=2026,
                             universe=u, days=30, steps_per_day=steps,
                             ticks_per_step=390 // steps)
        out[f"ret_{steps}"] = scores["m"].return_pct
    return out


def g_horizon(ctx: Ctx) -> dict:
    """agents-and-evaluation: momentum captures 27% at 5 days, 94% at 60."""
    u = _u(40, 7)
    out = {}
    for days in (5, 60):
        scores = pt.evaluate(reference_agents(seed=3), seed=2026,
                             universe=u, days=days)
        out[f"capture_{days}d"] = capture_ratio(scores).get("momentum")
    return out


def g_oracle_config(ctx: Ctx) -> dict:
    """agents-and-evaluation: Oracle median P&L 110k -> 70k when top_k 5 -> 15.
    Universe reconstructed from tests/test_baselines.py (random(30, seed=11));
    eight seeds per the docstring at python/pretium/baselines.py:51-53."""
    u = _u(30, 11)

    def median_pnl(make):
        def one(seed):
            return pt.evaluate({"o": make()}, seed=seed, universe=u,
                               days=10)["o"].pnl
        with ThreadPoolExecutor(max_workers=min(8, ctx.workers)) as pool:
            return _median(list(pool.map(one, range(8))))

    return {
        "narrow_median_pnl": median_pnl(lambda: Oracle()),
        "wide_median_pnl": median_pnl(lambda: Oracle(top_k=15)),
    }


def g_ranking(ctx: Ctx) -> dict:
    """The headline ranking figures. Universe pinned by tests/test_ranking.py
    (HEADLINE = random(30, seed=11)); twelve seeds, ten days."""
    u = _u(30, 11)
    rk = pt.rank(lambda: reference_agents(seed=3), seeds=range(12),
                 universe=u, days=10, workers=min(4, ctx.workers))
    records = {r.name: r for r in rk.table()}
    mom, mr = records["momentum"], records["mean_reversion"]
    sep_mr = rk.separation("momentum", "mean_reversion")
    sep_rand = rk.separation("momentum", "random")

    # three-day companion figure: the +14.4 per-seed capture outlier
    rk3 = pt.rank(lambda: reference_agents(seed=3), seeds=range(10),
                  universe=u, days=3, workers=min(4, ctx.workers))
    max3 = max((c for r in rk3.table() for c in r.measured), default=None)

    return {
        "pooled_momentum": mom.pooled_capture,
        "pooled_mean_reversion": mr.pooled_capture,
        "momentum_capture_lo": mom.capture_range[0],
        "momentum_capture_hi": mom.capture_range[1],
        "momentum_wins": mom.wins,
        "seeds": len(rk.seeds) if hasattr(rk, "seeds") else 12,
        "sep_mom_mr": f"{sep_mr['wins_a']}-{sep_mr['wins_b']}",
        "sep_mom_mr_p": sep_mr["p_value"],
        "sep_mom_rand": f"{sep_rand['wins_a']}-{sep_rand['wins_b']}",
        "sep_mom_rand_p": sep_rand["p_value"],
        "capture_3d_max": max3,
    }


def g_scenario_leaderboard(ctx: Ctx) -> dict:
    """docs/scenarios.md seed-7 calm/hiked table. The page does not name the
    universe; reconstructed as random(20, seed=4) from tests/test_scenario.py."""
    u = _u(20, 4)
    shock = Scenario.rate_shock(start=0.025, end=0.05, over=15)
    calm = pt.evaluate(reference_agents(seed=3), seed=7, universe=u, days=20)
    hiked = pt.evaluate(reference_agents(seed=3), seed=7, universe=u, days=20,
                        scenario=shock)
    out = {}
    for name in ("buy_and_hold", "momentum", "oracle"):
        out[f"{name}_calm"] = calm[name].return_pct
        out[f"{name}_hiked"] = hiked[name].return_pct
        out[f"{name}_delta"] = hiked[name].return_pct - calm[name].return_pct
    return out


def g_llm_leaderboard(ctx: Ctx) -> dict:
    """an-llm-agent.md leaderboard, method stated in examples/claude_agent.py:
    reference agents, Universe.random(12, seed=7), seed 2026, 20 days."""
    u = _u(12, 7)
    scores = pt.evaluate(reference_agents(seed=3), seed=2026, universe=u,
                         days=20, max_leverage=2.0)
    o, m = scores["oracle"], scores["momentum"]
    return {
        "oracle_pnl": o.pnl,
        "oracle_impact": o.impact_bps,
        "oracle_why_pct": None if o.explanation_accuracy is None
        else o.explanation_accuracy * 100.0,
        "momentum_pnl": m.pnl,
        "momentum_impact": m.impact_bps,
        "momentum_why_is_blank": m.explanation_accuracy is None,
    }


# ---------------------------------------------------------------------------
# macro and scenarios
# ---------------------------------------------------------------------------

def g_macro_frozen(ctx: Ctx) -> dict:
    """core-concepts: over run_days(120) every macro field takes one distinct
    value and fundamental_value takes one distinct value per instrument."""
    u = _u(20, 7)
    e = pt.Engine(seed=42, universe=u)
    e.run_days(120, record=True)
    macro = pa.table(e.macro_table()).to_pydict()
    fields = ["vix", "federal_funds_rate", "corporate_bond_yield",
              "inflation_rate", "unemployment_rate", "gdp_growth",
              "qe_pe_boost", "fear_greed_index"]
    max_macro = max(len(set(macro[f])) for f in fields)
    t = pa.table(e.truth()).to_pydict()
    per: dict[int, set] = defaultdict(set)
    for i, fv in zip(t["instrument_id"], t["fundamental_value"]):
        per[i].add(fv)
    return {
        "max_distinct_macro": max_macro,
        "max_distinct_fv": max(len(s) for s in per.values()),
        "default_cby": round(macro["corporate_bond_yield"][0], 6),
    }


def _repriced_after(u, pin: dict) -> int:
    e = pt.Engine(seed=42, universe=u)
    _run_days_recorded(e, 2)
    e.pin_macro(**pin)
    _run_days_recorded_from(e, 2, 4)
    t = pa.table(e.truth()).to_pydict()
    per: dict[int, set] = defaultdict(set)
    for i, fv in zip(t["instrument_id"], t["fundamental_value"]):
        per[i].add(fv)
    return sum(1 for s in per.values() if len(s) > 1)


def _run_days_recorded_from(engine, start: int, stop: int) -> None:
    for day in range(start, stop):
        engine.open_market()
        engine.run_session(9, 30, 3, 390)
        engine.close_market()
        engine.record(day)


def g_pin_macro(ctx: Ctx) -> dict:
    """core-concepts: pinning the corporate bond yield reprices 19 of 20 and
    the twentieth is a loss-maker; pinning the policy rate reprices none.
    Universe reconstructed as random(20, seed=7) (one loss-maker, as stated;
    random(20, seed=11) reproduces the same counts)."""
    u = _u(20, 7)
    return {
        "loss_makers": sum(1 for i in u if i.eps <= 0),
        "repriced_corp": _repriced_after(u, {"corporate_bond_yield": 0.09}),
        "repriced_fed": _repriced_after(u, {"federal_funds_rate": 0.12}),
    }


def g_fedfunds(ctx: Ctx) -> dict:
    """scenarios.md: a policy-rate-only path moves prices by exactly 0.00%
    over 40 days, and a median around -4% once a 60-day run crosses the
    central-bank meeting at day 45. The page names neither universe nor sim
    seed; reconstructed as random(20,4), seed 5, from tests/test_scenario.py."""
    u = _u(20, 4)
    ramp = Scenario().ramp("federal_funds_rate", start=0.025, end=0.05, over=30)
    r40 = compare(ramp, seed=5, universe=u, days=40)
    r60 = compare(ramp, seed=5, universe=u, days=60)
    return {
        "max_abs_move_pct": max(abs(x) for x in r40["move_pct"]),
        "meeting_median_pct": r60["median_pct"],
    }


def g_spec(ctx: Ctx) -> dict:
    """docs/strategy-specs.md: the three fingerprints in the worked methods
    section. The full momentum and oracle fingerprints are pinned in
    tests/test_spec.py; the page prints their first eight hex characters."""
    return {
        "universe_fp8": pt.Universe.random(20, seed=7).fingerprint[:8],
        "momentum_fp8": pt.StrategySpec.momentum().fingerprint[:8],
        "oracle_fp8": pt.StrategySpec.oracle().fingerprint[:8],
    }


def g_vix(ctx: Ctx) -> dict:
    """scenarios.md VIX tables. Volatility over random(20, seed=11), spreads
    over random(25, seed=11) (both reconstructed; VIX 15 reproduces the
    published value exactly on each), correlation over random(25, seed=11)."""
    u20 = _u(20, 11)
    u25 = _u(25, 11)
    out: dict = {}

    def vol(vix):
        f = pt.facts.measure(seed=3, universe=u20, days=120,
                             scenario=Scenario().hold(vix=float(vix)))
        return f["annualised_vol_pct"]

    def corr(vix):
        f = pt.facts.measure(seed=3, universe=u25, days=120,
                             scenario=Scenario().hold(vix=float(vix)))
        return f["cross_sectional_corr"]

    with ThreadPoolExecutor(max_workers=min(7, ctx.workers)) as pool:
        vols = pool.map(vol, (5, 15, 45, 65))
        corrs = pool.map(corr, (15, 45, 65))
        for vix, v in zip((5, 15, 45, 65), vols):
            out[f"vol_{vix}"] = v
        for vix, c in zip((15, 45, 65), corrs):
            out[f"corr_{vix}"] = c
    out["vol_range_max_delta"] = max(out[f"vol_{v}"] for v in (5, 15, 45, 65)) \
        - min(out[f"vol_{v}"] for v in (5, 15, 45, 65))

    def spread(vix):
        e = pt.Engine(seed=3, universe=u25)
        sc = Scenario().hold(vix=float(vix))
        for day in range(5):
            sc.apply(e, day)
            e.open_market()
            e.run_session(9, 30, 3, 390)
            e.close_market()
        vals = []
        for tk in e.tickers:
            b = e.book(tk)
            bid, ask = b.best_bid, b.best_ask
            if bid and ask and bid > 0:
                vals.append((ask - bid) / ((ask + bid) / 2) * 1e4)
        return sum(vals) / len(vals)

    for vix in (15, 25, 45, 65):
        out[f"spread_{vix}"] = spread(vix)

    def prices60(vix):
        e = run_scenario(Scenario().hold(vix=float(vix)), seed=3,
                         universe=u20, days=60)
        return e.prices()

    p5, p10, p15 = (prices60(v) for v in (5, 10, 15))
    out["bit_identical_5_10_15"] = p5 == p10 == p15
    return out


def g_tca_vix(ctx: Ctx) -> dict:
    """scenarios.md: execution cost at VIX 45 vs 15, BuyAndHold over
    Universe.random(20, seed=11), ten days, seeds 1 to 12. Method stated."""
    u = _u(20, 11)

    def one(seed):
        lo = pt.tca.analyse(BuyAndHold(), seed=seed, universe=u, days=10,
                            scenario=Scenario().hold(vix=15.0)).shortfall_bps()
        hi = pt.tca.analyse(BuyAndHold(), seed=seed, universe=u, days=10,
                            scenario=Scenario().hold(vix=45.0)).shortfall_bps()
        return lo, hi

    with ThreadPoolExecutor(max_workers=min(6, ctx.workers)) as pool:
        pairs = list(pool.map(one, range(1, 13)))
    los = [p[0] for p in pairs]
    his = [p[1] for p in pairs]
    return {
        "wins": sum(1 for lo, hi in pairs if hi > lo),
        "median_lo": _median(los),
        "median_hi": _median(his),
        "paired_median_delta": _median([hi - lo for lo, hi in pairs]),
    }


def g_drawdiv(ctx: Ctx) -> dict:
    """scenarios.md: the macro-counterfactual draw divergence is zero in every
    comparison run -- four scenarios at seed 3 plus three of them repeated
    across seeds 1 to 8, twenty instruments, forty days. The four scenarios
    are named in the docstring at python/pretium/scenario.py:110-117; the
    universe is reconstructed as random(20, seed=4) from tests/test_scenario.py."""
    u = _u(20, 4)
    scenarios = {
        "rate_5": Scenario.rate_shock(start=0.025, end=0.05, over=30),
        "rate_10": Scenario.rate_shock(start=0.025, end=0.10, over=30),
        "vix_45": Scenario.vix_shock(calm=15.0, peak=45.0),
        "vix_80": Scenario.vix_shock(calm=15.0, peak=80.0),
    }
    jobs = [(name, 3) for name in scenarios]
    jobs += [(name, seed) for name in ("rate_5", "rate_10", "vix_45")
             for seed in range(1, 9)]

    def one(job):
        name, seed = job
        return compare(scenarios[name], seed=seed, universe=u,
                       days=40)["draw_delta"]

    with ThreadPoolExecutor(max_workers=min(8, ctx.workers)) as pool:
        deltas = list(pool.map(one, jobs))
    return {
        "comparisons": len(deltas),
        "max_abs_draw_delta": max(abs(d) for d in deltas),
        "all_exact": all(d == 0 for d in deltas),
    }


# ---------------------------------------------------------------------------
# replay / archive
# ---------------------------------------------------------------------------

def g_replay(ctx: Ctx) -> dict:
    """reproducing-a-run.md: the complete-archive example, run verbatim."""
    u = _u(20, 11)
    macro = pt.Macro()
    e = pt.Engine(seed=42, universe=u, macro_state=macro)
    e.run_days(20)
    archive = {
        "seed": 42,
        "universe": {"constructor": "random", "n": 20, "seed": 11,
                     "fingerprint": u.fingerprint},
        "order_log": e.order_log,
    }
    rebuilt = pt.Universe.random(archive["universe"]["n"],
                                 seed=archive["universe"]["seed"])
    replayed = pt.replay(archive["order_log"], seed=archive["seed"],
                         universe=rebuilt, macro=pt.Macro())
    return {
        "fingerprint_matches": rebuilt.fingerprint == archive["universe"]["fingerprint"],
        "prices_identical": replayed.prices() == e.prices(),
        "draws_equal": replayed.draws_consumed == e.draws_consumed,
    }


# ---------------------------------------------------------------------------
# universe generation statistics
# ---------------------------------------------------------------------------

def g_universe_stats(ctx: Ctx) -> dict:
    """conventions.md: short interest median about 3.7% of shares outstanding,
    roughly one name in eleven above the 20% squeeze threshold. The page does
    not name a universe; measured over ten generated 100-name universes."""
    pcts = []
    for seed in range(1, 11):
        for inst in _u(100, seed):
            pcts.append(inst.short_interest / inst.shares_outstanding * 100.0)
    above = sum(1 for p in pcts if p > 20.0)
    return {
        "median_si_pct": _median(pcts),
        "one_in_n_above_20": len(pcts) / above if above else float("inf"),
        "names": len(pcts),
    }


# ---------------------------------------------------------------------------
# timed groups: wall clocks. Run serially, machine otherwise quiet.
# ---------------------------------------------------------------------------

def g_perf(ctx: Ctx) -> dict:
    """docs/performance.md. Absolute times are machine-bound; the page says to
    treat ratios as portable, so the ratios are the comparable outputs."""
    u10 = _u(10, 7)
    u100 = _u(100, 7)

    def best_of(n, fn):
        """Minimum of n runs: the page's 3% recording overhead is smaller
        than cold-start noise, so single timings cannot resolve it."""
        return min(_timed(fn) for _ in range(n))

    def _timed(fn):
        t0 = time.perf_counter()
        fn()
        return time.perf_counter() - t0

    pt.Engine(seed=7, universe=u10).run_days(30)  # warm-up

    t10 = best_of(2, lambda: pt.Engine(seed=7, universe=u10).run_days(252))

    # Row count from one untimed recorded run, released before any timing
    # starts: a recorded engine holds ~1 GB of raw buffers, and keeping one
    # alive puts memory pressure on every timing that follows it.
    e = pt.Engine(seed=7, universe=u100)
    e.run_days(252, record=True)
    truth_rows = pa.table(e.truth()).num_rows
    del e

    def plain():
        pt.Engine(seed=7, universe=u100).run_days(252)

    def recorded():
        engine = pt.Engine(seed=7, universe=u100)
        engine.run_days(252, record=True)

    # Interleaved so thermal or background drift hits both configurations
    # alike; the overhead being measured is smaller than cold-start noise.
    plains, recs = [], []
    for _ in range(3):
        plains.append(_timed(plain))
        recs.append(_timed(recorded))
    t100, t100_rec = min(plains), min(recs)

    t0 = time.perf_counter()
    pt.run_many(seeds=range(8), universe=u100, days=21, workers=1)
    t_serial = time.perf_counter() - t0

    t0 = time.perf_counter()
    pt.run_many(seeds=range(8), universe=u100, days=21, workers=8)
    t_8w = time.perf_counter() - t0

    return {
        "t10": t10, "t100": t100, "t100_rec": t100_rec,
        "t_sweep_serial": t_serial, "t_sweep_8w": t_8w,
        "truth_rows": truth_rows,
        "scale_ratio": t100 / t10,
        "overhead_pct": (t100_rec / t100 - 1.0) * 100.0,
        "speedup": t_serial / t_8w,
    }


def g_fork(ctx: Ctx) -> dict:
    """forking-a-simulation.md: branch < 1 ms, Checkpoint.resume seconds.
    The page does not say what run length the 2.7 s was measured over; this
    measures a 30-day run and compares the branch/resume ratio, which is the
    claim's portable part (three orders of magnitude)."""
    import math
    u = _u(20, 11)
    e = pt.Engine(seed=42, universe=u)
    e.run_days(30)
    times = []
    for _ in range(5):
        t0 = time.perf_counter()
        pt.branch(e, 2, universe=u, seed=42)
        times.append((time.perf_counter() - t0) * 1e3)
    branch_ms = _median(times)

    mark = pt.Checkpoint.of(e, universe=u, seed=42)
    t0 = time.perf_counter()
    mark.resume()
    resume_s = time.perf_counter() - t0
    return {
        "branch_ms": branch_ms,
        "resume_s": resume_s,
        "ratio_orders": math.log10(resume_s * 1e3 / branch_ms),
        "run_days": 30,
    }


def g_workflow(ctx: Ctx) -> dict:
    """README's worked example: examples/research_workflow.py, run whole."""
    import contextlib
    import io
    spec = importlib.util.spec_from_file_location(
        "research_workflow", ctx.root / "examples" / "research_workflow.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    buf = io.StringIO()
    t0 = time.perf_counter()
    try:
        with contextlib.redirect_stdout(buf):
            report = mod.main()
        failed = None
    except AssertionError as exc:
        # The example asserts its own published findings; after an engine
        # change a failed assert is a stale example, which is a result of
        # this harness rather than a failure of it.
        report, failed = {}, str(exc)
    wall = time.perf_counter() - t0
    return {
        "wall_s": wall,
        "truth_rows": report.get("truth_rows"),
        "sweep_seeds": report.get("sweep"),
        "agents_evaluated": (len(report["capture"]) + 1) if "capture" in report else None,
        "asserts_passed": failed is None,
        "assert_failure": failed,
        "universe": report.get("universe"),
    }


GROUPS = {
    "determinism": g_determinism,
    "consts": g_consts,
    "arith": g_arith,
    "truth_residual": g_truth_residual,
    "realism_sample": g_realism_sample,
    "realism_six": g_realism_six,
    "rebalance": g_rebalance,
    "horizon": g_horizon,
    "oracle_config": g_oracle_config,
    "ranking": g_ranking,
    "scenario_leaderboard": g_scenario_leaderboard,
    "llm_leaderboard": g_llm_leaderboard,
    "macro_frozen": g_macro_frozen,
    "pin_macro": g_pin_macro,
    "fedfunds": g_fedfunds,
    "spec": g_spec,
    "vix": g_vix,
    "tca_vix": g_tca_vix,
    "drawdiv": g_drawdiv,
    "replay": g_replay,
    "universe_stats": g_universe_stats,
    "perf": g_perf,
    "fork": g_fork,
    "workflow": g_workflow,
}

#: Groups that measure wall clocks; run serially after everything else.
TIMED_GROUPS = ("perf", "fork", "workflow")
