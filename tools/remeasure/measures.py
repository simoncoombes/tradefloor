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
    import inspect
    spec = importlib.util.spec_from_file_location(
        "known_answer", ctx.root / "tests" / "known_answer.py")
    ka = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ka)
    digest = ka.known_answer_digest()
    pinned = json.loads((ctx.root / "tests" / "known_answer.json").read_text())

    u = _u(20, 11)
    reversed_u = pt.Universe(list(reversed(list(u))))
    preset = pt.model_preset()

    # README's model-preset section: a nudged coefficient set fingerprints as
    # custom-XXXXXXXX, never as the preset's name
    custom = pt.ModelParams.from_preset("pt-v1", garch_alpha=0.12)
    custom_fp = pt.Engine(seed=42, universe=_u(6, 7), model=custom).model_fingerprint

    from pretium.facts import MARGINAL, REAL_MARKETS
    return {
        "kat_digest": digest,
        "kat_digest_elided": f"{digest[:8]}...{digest[-7:]}",
        "kat_digest_pinned": pinned["sha256"],
        "kat_matches_pinned": digest == pinned["sha256"],
        "kat_version": ka.KAT_VERSION,
        "version": pt.version(),
        "preset_name": preset["name"],
        "preset_keys": len([k for k in preset if k != "name"]),
        "fp_len": len(u.fingerprint),
        "fp_reversal_differs": u.fingerprint != reversed_u.fingerprint,
        "facts_marginal": len(MARGINAL),
        "facts_dependence": len(REAL_MARKETS) - len(MARGINAL),
        "oracle_default_top_k": Oracle().top_k,
        "custom_model_fp": custom_fp,
        "preset_lacks_market_sigma": "market_factor_sigma" not in preset,
        "n_factors": len(pt.Engine.FACTORS),
        "evaluate_steps_default":
            inspect.signature(pt.evaluate).parameters["steps_per_day"].default,
    }


def g_consts(ctx: Ctx) -> dict:
    """Claims that are constants in the source or derivable from the API."""
    tick_rs = (ctx.root / "rust" / "src" / "market" / "tick.rs").read_text()
    garch_rs = (ctx.root / "rust" / "src" / "market" / "garch.rs").read_text()
    fvol_rs = (ctx.root / "rust" / "src" / "market" / "factor_vol.rs").read_text()
    econ_rs = (ctx.root / "rust" / "src" / "economy" / "state.rs").read_text()
    micro_rs = (ctx.root / "rust" / "src" / "microstructure.rs").read_text()

    def const(text: str, name: str) -> float:
        m = re.search(rf"const {name}: f64 = ([0-9.e-]+);", text)
        return float(m.group(1)) if m else float("nan")

    market_sigma = const(tick_rs, "MARKET_FACTOR_SIGMA")
    alpha = const(garch_rs, "ALPHA")
    beta = const(garch_rs, "BETA")
    gamma = const(garch_rs, "GAMMA")
    fvol_alpha = const(fvol_rs, "MARKET_VOL_ALPHA")
    fvol_beta = const(fvol_rs, "MARKET_VOL_BETA")

    # Mispricing reversion half-life, from the AR coefficient the engine
    # actually uses (S_PHI_TICK is stored as exact bits).
    import math
    m = re.search(r"const S_PHI_TICK: f64 = f64::from_bits\(0x([0-9A-Fa-f_]+)\)",
                  tick_rs)
    phi = struct.unpack("<d", struct.pack("<Q", int(m.group(1).replace("_", ""),
                                                   16)))[0] if m else float("nan")
    half_life_days = math.log(0.5) / math.log(phi) / 390.0

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
        "reversion_half_life_days": half_life_days,
        # the realism page publishes the GJR form of the per-name persistence
        "garch_persistence": alpha + beta + gamma / 2.0,
        "gjr_alpha": alpha,
        "gjr_gamma": gamma,
        "gjr_neg_passthrough": alpha + gamma,
        # the market factor's own variance process (factor_vol.rs): the
        # realism page publishes its shock half-life in days
        "factor_halflife_days": math.log(0.5) / math.log(fvol_alpha + fvol_beta),
        "factor_vol_ceiling_multiple": const(fvol_rs, "MARKET_VOL_CEILING_MULTIPLE"),
        "idio_sigma_scale": const(fvol_rs, "IDIO_SIGMA_SCALE"),
        "crisis_vix_threshold": const(econ_rs, "CRISIS_VIX_THRESHOLD"),
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
        # Fourteen f64 buffers per recorded day, not twelve: prices, volumes,
        # mispricing, fundamental and anchor, plus the nine attribution
        # components (rust/src/python_arrow.rs, `struct RecordedDay`).
        "buffers_mb": 14 * rows_100x252 * 8 / 1e6,     # "1.10 GB"
        # The same fourteen buffers for ONE day, which is the figure the page
        # quotes for what each recorded day adds.
        "buffers_mb_per_day": 14 * 390 * 100 * 8 / 1e6,  # "4.37 MB a day"
        "hundred_engines_gb": 100 * 14 * rows_100x252 * 8 / 1e9,  # "roughly 110 GB"
        "crossings_per_year": 252 * 390,               # "about 98,000"
        "crossings_five_fields": 252 * 390 * 5,        # "roughly 500,000"
        "workflow_truth_rows": 10 * 390 * 60,          # "234,000 rows"
        "llm_calls_default_run": 20,                   # one per day, 20 days
        "clean_sweep_p": 2.0 * 0.5 ** 12,              # "even a clean sweep only reaches p = 0.0005"
        "perf_row_gap_s": 28.2 - 27.4,                 # "the 0.8s between the two rows"
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
    for key in _REALISM_KEYS + ["abs_return_acf5", "abs_return_acf20"]:
        values = [r[key] for r in runs]
        out[f"{key}_median"] = _median(values)
        out[f"{key}_lo"] = min(values)
        out[f"{key}_hi"] = max(values)
    # the realism page: "the sign is stable: negative in six seeds of six"
    out["leverage_all_negative"] = out["leverage_effect_hi"] < 0
    return out


def g_realism_heldout(ctx: Ctx) -> dict:
    """The realism page's held-out section: the three checks the 2026-08
    calibration never scored, each with the method the page states.

    1. Fresh sim seeds 101-106 over the published random(40, 111), 252 days.
    2. Five fresh 60-name universes (seeds 1, 7, 11, 42, 222), each the
       median over sim seeds 1-6 at 252 days; the page publishes the range
       of the five per-universe medians.
    3. The published universe over 504 days, seeds 1-6."""
    u40 = _u(*_REALISM_UNIVERSE)

    def batch(universe, seeds, days):
        def one(seed):
            return pt.facts.measure(seed=seed, universe=universe, days=days)
        with ThreadPoolExecutor(max_workers=min(6, ctx.workers)) as pool:
            return list(pool.map(one, seeds))

    def med(runs, key):
        return _median([r[key] for r in runs])

    out: dict = {}

    fresh = batch(u40, range(101, 107), 252)
    out.update({
        "held_seeds_corr": med(fresh, "cross_sectional_corr"),
        "held_seeds_kurt": med(fresh, "excess_kurtosis"),
        "held_seeds_clust": med(fresh, "abs_return_acf1"),
        "held_seeds_volabs": med(fresh, "volume_abs_return_corr"),
        "held_seeds_lev": med(fresh, "leverage_effect"),
    })

    per_universe: dict[str, list[float]] = defaultdict(list)
    for k in (1, 7, 11, 42, 222):
        runs = batch(_u(60, k), range(1, 7), 252)
        for key, name in (("cross_sectional_corr", "corr"),
                          ("excess_kurtosis", "kurt"),
                          ("abs_return_acf1", "clust"),
                          ("leverage_effect", "lev")):
            per_universe[name].append(med(runs, key))
    for name, meds in per_universe.items():
        out[f"held_uni_{name}_lo"] = min(meds)
        out[f"held_uni_{name}_hi"] = max(meds)

    long = batch(u40, range(1, 7), 504)
    out.update({
        "held_long_corr": med(long, "cross_sectional_corr"),
        "held_long_kurt": med(long, "excess_kurtosis"),
        "held_long_clust": med(long, "abs_return_acf1"),
        "held_long_lev": med(long, "leverage_effect"),
        "held_long_vol": med(long, "annualised_vol_pct"),
    })
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
    """agents-and-evaluation horizon bullet: on seed 2026 over random(40,7)
    the Oracle makes $21k in five days and $568k in sixty, and the same
    momentum agent captures 2.98 against the first denominator and 1.47
    against the second. Method stated on the page."""
    u = _u(40, 7)
    out = {}
    for days in (5, 60):
        scores = pt.evaluate(reference_agents(seed=3), seed=2026,
                             universe=u, days=days)
        out[f"capture_{days}d"] = capture_ratio(scores).get("momentum")
        out[f"oracle_pnl_{days}d"] = scores["oracle"].pnl
    return out


def g_oracle_config(ctx: Ctx) -> dict:
    """agents-and-evaluation: Oracle median P&L $87k -> $71k when top_k 5 -> 15,
    'on the ranking grid below at ten days ... over sim seeds 0-7'. Grid stated
    on the page: random(30, seed=11), ten days, evaluate defaults."""
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
    """The twelve-market ranking figures on agents-and-evaluation. Grid stated
    on the page: random(30, seed=11), seeds 0-11, ten days; plus the seeds
    12-23 comparison window and the three-day seeds 0-9 companion study."""
    u = _u(30, 11)
    factory = lambda: reference_agents(seed=3)  # noqa: E731
    rk = pt.rank(factory, seeds=range(12),
                 universe=u, days=10, workers=min(4, ctx.workers))
    records = {r.name: r for r in rk.table()}
    mom, mr = records["momentum"], records["mean_reversion"]
    sep_mr = rk.separation("momentum", "mean_reversion")
    sep_rand = rk.separation("momentum", "random")

    # the beats-the-Oracle table: paired per-seed P&L against the reference
    beats = {name: sum(1 for pnl, ref in zip(records[name].pnls,
                                             rk.reference_pnls) if pnl > ref)
             for name in ("momentum", "mean_reversion", "buy_and_hold",
                          "random")}

    # the prose behind the table: mean-reversion's one win is barely above
    # 1.0, buy-and-hold's is the largest per-seed capture on the whole grid
    def beat_captures(name):
        return [pnl / ref for pnl, ref in zip(records[name].pnls,
                                              rk.reference_pnls) if pnl > ref]
    grid_max = max(pnl / ref
                   for name in ("momentum", "mean_reversion", "buy_and_hold",
                                "random")
                   for pnl, ref in zip(records[name].pnls, rk.reference_pnls))
    mr_beat = max(beat_captures("mean_reversion"), default=float("nan"))
    bh_beat = max(beat_captures("buy_and_hold"), default=float("nan"))

    # the second twelve-seed window (seeds 12-23): same test, different verdict
    rk_b = pt.rank(factory, seeds=range(12, 24),
                   universe=u, days=10, workers=min(4, ctx.workers))
    sep_mr_b = rk_b.separation("momentum", "mean_reversion")

    # three-day companion study, seeds 0-9: the reference P&L span, the pooled
    # mean-reversion figure, and mean-reversion's ratio on the thinnest market
    rk3 = pt.rank(factory, seeds=range(10),
                  universe=u, days=3, workers=min(4, ctx.workers))
    mr3 = {r.name: r for r in rk3.table()}["mean_reversion"]
    thin = min(range(len(rk3.reference_pnls)), key=rk3.reference_pnls.__getitem__)

    # "every ratio above 1.0 it posts sits on one of the four thinnest
    # denominators", and averaging the ten ratios instead of pooling them
    # would read +0.61
    thin4 = set(sorted(range(len(rk3.reference_pnls)),
                       key=rk3.reference_pnls.__getitem__)[:4])
    gt1 = {i for i, c in enumerate(mr3.captures) if c > 1.0}

    return {
        "mr_beat_capture": mr_beat,
        "bh_beat_capture": bh_beat,
        "bh_beat_is_grid_max": bh_beat == grid_max,
        "mr3_gt1_all_on_4_thinnest": bool(gt1) and gt1 <= thin4,
        "mr3_mean_of_ratios": sum(mr3.captures) / len(mr3.captures),
        "pooled_momentum": mom.pooled_capture,
        "pooled_mean_reversion": mr.pooled_capture,
        "momentum_capture_lo": mom.capture_range[0],
        "momentum_capture_hi": mom.capture_range[1],
        "momentum_wins": mom.wins,
        "mr_wins": mr.wins,
        "seeds": len(rk.seeds) if hasattr(rk, "seeds") else 12,
        "sep_mom_mr": f"{sep_mr['wins_a']}-{sep_mr['wins_b']}",
        "sep_mom_mr_p": sep_mr["p_value"],
        "sep_mom_rand": f"{sep_rand['wins_a']}-{sep_rand['wins_b']}",
        "sep_mom_rand_p": sep_rand["p_value"],
        "beat12_momentum": beats["momentum"],
        "beat12_mean_reversion": beats["mean_reversion"],
        "beat12_buy_and_hold": beats["buy_and_hold"],
        "beat12_random": beats["random"],
        "sep_mom_mr_b": f"{sep_mr_b['wins_a']}-{sep_mr_b['wins_b']}",
        "sep_mom_mr_b_p": sep_mr_b["p_value"],
        "oracle_pnl_3d_min": min(rk3.reference_pnls),
        "oracle_pnl_3d_max": max(rk3.reference_pnls),
        "pooled_mr_3d": mr3.pooled_capture,
        "mr_capture_thinnest": mr3.captures[thin],
    }


def g_scenario_leaderboard(ctx: Ctx) -> dict:
    """docs/scenarios.md seed-7 calm/hiked table, method stated on the page
    since the stream-AB rewrite (reference_agents(seed=3), random(20,4),
    seed 7, 20 days), plus the page's sim-seed 5-9 band: buy-and-hold never
    escapes the walk, momentum escapes it on one seed in five."""
    u = _u(20, 4)
    shock = Scenario.rate_shock(start=0.025, end=0.05, over=15)

    def pair(seed):
        calm = pt.evaluate(reference_agents(seed=3), seed=seed, universe=u,
                           days=20)
        hiked = pt.evaluate(reference_agents(seed=3), seed=seed, universe=u,
                            days=20, scenario=shock)
        return calm, hiked

    with ThreadPoolExecutor(max_workers=min(6, ctx.workers)) as pool:
        results = dict(zip((7, 5, 6, 8, 9),
                           pool.map(pair, (7, 5, 6, 8, 9))))

    calm, hiked = results[7]
    out = {}
    for name in ("buy_and_hold", "momentum", "oracle"):
        out[f"{name}_calm"] = calm[name].return_pct
        out[f"{name}_hiked"] = hiked[name].return_pct
        out[f"{name}_delta"] = hiked[name].return_pct - calm[name].return_pct

    bh_deltas, mom_deltas = [], []
    for seed in range(5, 10):
        c, h = results[seed]
        bh_deltas.append(h["buy_and_hold"].return_pct
                         - c["buy_and_hold"].return_pct)
        mom_deltas.append(h["momentum"].return_pct - c["momentum"].return_pct)
    out.update({
        "bh_seedband_lo": min(bh_deltas),
        "bh_seedband_hi": max(bh_deltas),
        "bh_escapes": sum(1 for d in bh_deltas if d > 0),
        "mom_seedband_lo": min(mom_deltas),
        "mom_seedband_hi": max(mom_deltas),
        "mom_escapes": sum(1 for d in mom_deltas if d > 0),
    })
    return out


def g_llm_leaderboard(ctx: Ctx) -> dict:
    """an-llm-agent.md leaderboard, method stated in examples/08-claude-agent.py:
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


def g_llm_impact(ctx: Ctx) -> dict:
    """an-llm-agent: why the impact column went blank. Across seeds 2020-2031
    at the leaderboard's exact configuration, the oracle's twenty-day impact
    spans -235 to +470 bps and is positive in only 7 of 12 seeds; momentum's
    flips sign the same way; over two days both agents are positive in 12 of
    12; by day three the oracle's sign already belongs to the seed (8 of 12,
    span -29 to +90 bps). Method stated on the page."""
    u = _u(12, 7)

    def one(seed):
        out = []
        for days in (20, 3, 2):
            s = pt.evaluate(reference_agents(seed=3), seed=seed, universe=u,
                            days=days, max_leverage=2.0)
            out += [s["oracle"].impact_bps, s["momentum"].impact_bps]
        return out

    with ThreadPoolExecutor(max_workers=min(6, ctx.workers)) as pool:
        rows = list(pool.map(one, range(2020, 2032)))
    o20, m20, o3, m3, o2, m2 = (list(col) for col in zip(*rows))
    return {
        "oracle_imp20_min": min(o20),
        "oracle_imp20_max": max(o20),
        "oracle_imp20_pos": sum(1 for x in o20 if x > 0),
        "mom_imp20_flips": min(m20) < 0 < max(m20),
        "imp2_pos_min": min(sum(1 for x in o2 if x > 0),
                            sum(1 for x in m2 if x > 0)),
        "imp3_oracle_pos": sum(1 for x in o3 if x > 0),
        "imp3_oracle_min": min(o3),
        "imp3_oracle_max": max(o3),
    }


# ---------------------------------------------------------------------------
# RNG streams
# ---------------------------------------------------------------------------

def g_rng(ctx: Ctx) -> dict:
    """docs/rng-streams.md: three substreams, a nine-number snapshot, the
    market stream's schedule a pure function of the tick schedule, and the
    refusal of pre-split snapshots."""
    u = _u(20, 7)
    a = pt.Engine(seed=42, universe=u)
    a.run_days(2)
    streams = a.draws_by_stream()

    # No macro value can move the market stream's position: pin mid-run and
    # compare per-stream draw counts against the unpinned twin.
    b = pt.Engine(seed=42, universe=u)
    b.run_days(1)
    b.pin_macro(corporate_bond_yield=0.09)
    b.run_days(1)
    market_independent = (a.draws_by_stream()["market"]
                          == b.draws_by_stream()["market"])

    snap = a.state_snapshot()
    presplit = dict(snap)
    presplit["rng"] = list(snap["rng"])[:3]
    fresh = pt.Engine(seed=42, universe=u)
    try:
        fresh.restore_state(presplit)
        refused = False
    except Exception:
        refused = True

    return {
        "stream_count": len(streams),
        "stream_names": ",".join(streams),
        "rng_snapshot_len": len(snap["rng"]),
        "market_sched_independent": market_independent,
        "presplit_snapshot_refused": refused,
    }


# ---------------------------------------------------------------------------
# macro and scenarios
# ---------------------------------------------------------------------------

def g_macro_chain(ctx: Ctx) -> dict:
    """core-concepts: the macro chain runs endogenously by default. Over
    run_days(120) on random(20, seed=11), sim seed 42, VIX takes a new value
    every day, the policy-driven fields step at the meeting calendar
    (federal_funds_rate 2 distinct values, corporate_bond_yield 3,
    inflation_rate 4, gdp_growth 6), and fundamental_value takes 3 distinct
    values per instrument (repricing at the day-45 and day-96 meetings)
    except the book-valued loss-maker, which never reprices."""
    u = _u(20, 11)
    e = pt.Engine(seed=42, universe=u)
    e.run_days(120, record=True)
    macro = pa.table(e.macro_table()).to_pydict()
    t = pa.table(e.truth()).to_pydict()

    per: dict[int, set] = defaultdict(set)
    reprice_days: set[int] = set()
    last: dict[int, float] = {}
    order = sorted(range(len(t["instrument_id"])),
                   key=lambda i: (t["instrument_id"][i], t["day"][i],
                                  t["tick"][i]))
    for i in order:
        inst, fv = t["instrument_id"][i], t["fundamental_value"][i]
        per[inst].add(fv)
        if inst in last and fv != last[inst]:
            reprice_days.add(t["day"][i])
        last[inst] = fv
    counts = sorted(len(s) for s in per.values())

    return {
        "vix_distinct": len(set(macro["vix"])),
        "fed_distinct": len(set(macro["federal_funds_rate"])),
        "cby_distinct": len(set(macro["corporate_bond_yield"])),
        "inflation_distinct": len(set(macro["inflation_rate"])),
        "gdp_distinct": len(set(macro["gdp_growth"])),
        "fv_distinct_repricer": counts[-1],
        "fv_distinct_lossmaker": counts[0],
        "reprice_days": ",".join(str(d) for d in sorted(reprice_days)),
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
    Measured on random(20, seed=11), the universe the page's macro-chain run
    names ("the loss-maker above"); the pin lands at day 2, well before the
    first meeting, so the endogenous chain cannot confound the counts."""
    u = _u(20, 11)
    return {
        "loss_makers": sum(1 for i in u if i.eps <= 0),
        "repriced_corp": _repriced_after(u, {"corporate_bond_yield": 0.09}),
        "repriced_fed": _repriced_after(u, {"federal_funds_rate": 0.12}),
    }


def g_fedfunds(ctx: Ctx) -> dict:
    """scenarios.md: a policy-rate-only path moves prices by exactly 0.00%
    over 40 days, and a median -4.29% once a 60-day run crosses the
    central-bank meeting at day 45. Method stated on the page since the
    stream-AB rewrite: ramp 2.5%->5% over 30 days, random(20,4), sim seed 5."""
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
    """scenarios.md VIX tables, methods stated on the page since the
    stream-AB rewrite: volatility over random(20, seed=11), spreads and
    correlation over random(25, seed=11), sim seed 3. The realism page
    quotes the same pinned-VIX correlations. Also the bit-identity boundary:
    VIX 5/10/15 closes are identical on day one and diverge for every pair
    from day two."""
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
    # "a thirteenfold move in VIX moves realised volatility by a factor of 2.5"
    out["vol_ratio_65_5"] = out["vol_65"] / out["vol_5"]

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

    def daily_closes(vix, days=2):
        e = pt.Engine(seed=3, universe=u20)
        sc = Scenario().hold(vix=float(vix))
        closes = []
        for day in range(days):
            sc.apply(e, day)
            e.open_market()
            e.run_session(9, 30, 3, 390)
            e.close_market()
            closes.append(e.prices())
        return closes

    c5, c10, c15 = (daily_closes(v) for v in (5, 10, 15))
    out["bit_day1_identical"] = c5[0] == c10[0] == c15[0]
    out["bit_day2_all_pairs_differ"] = (c5[1] != c10[1] and c5[1] != c15[1]
                                        and c10[1] != c15[1])
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


class _BuyOnce:
    """Buy one name at a fraction of its ADV on the first step, then hold.
    Mirrors tests/test_tca.py's BuyOnce, the configuration the TCA page's
    worked figures state."""

    def __init__(self, participation=0.01, index=0):
        self.participation = participation
        self.index = index
        self.done = False

    def act(self, obs):
        if self.done:
            return {}
        self.done = True
        ticker = obs.tickers[self.index]
        return {ticker: self.participation * obs.avg_volume(ticker)}


class _RoundTrip:
    """Buy on step 0, close the whole position on step 3 (tests/test_tca.py)."""

    def __init__(self, participation=0.01):
        self.participation = participation

    def act(self, obs):
        ticker = obs.tickers[0]
        if obs.step == 0:
            return {ticker: self.participation * obs.avg_volume(ticker)}
        if obs.step == 3:
            return {ticker: -obs.position(ticker)}
        return {}


_TCA_SEEDS = (2026, 1, 2, 3, 4, 5, 7, 11)


def g_tca_example(ctx: Ctx) -> dict:
    """transaction-cost-analysis.md's worked figures, method stated on the
    page: the first name of Universe.random(20, seed=7) (ADV 9,713 shares),
    one six-step day. Entry: 97 shares (1% ADV) at the first step costs
    +16.71 bps on every seed measured. Round trip (sell three steps later):
    a seed range, -17.72 to +2.03 bps over sim seeds 2026,1,2,3,4,5,7,11,
    negative on 7 of 8, median -12.40. Partial fill: a request for 4,856
    shares (half ADV, sim seed 2026) fills 483 - the whole displayed
    depth - and requests of 9,713 and 48,563 fill the same 483, on every
    seed measured."""
    u = _u(20, 7)

    def analyse(agent, seed):
        return pt.tca.analyse(agent, universe=u, days=1, steps_per_day=6,
                              seed=seed)

    def one(seed):
        entry = analyse(_BuyOnce(0.01), seed).shortfall_bps()
        rt = analyse(_RoundTrip(0.01), seed).shortfall_bps()
        half = analyse(_BuyOnce(0.5), seed).partial_fills()
        opening_fill = half[0]["quantity"] if half else None
        return entry, rt, opening_fill

    with ThreadPoolExecutor(max_workers=min(8, ctx.workers)) as pool:
        rows = dict(zip(_TCA_SEEDS, pool.map(one, _TCA_SEEDS)))

    entries = [rows[s][0] for s in _TCA_SEEDS]
    rts = [rows[s][1] for s in _TCA_SEEDS]
    fills = [rows[s][2] for s in _TCA_SEEDS]

    # the saturation claim: half ADV, full ADV and 5x ADV all fill the same
    # shares at sim seed 2026
    sizes = {}
    for part in (0.5, 1.0, 5.0):
        pf = analyse(_BuyOnce(part), 2026).partial_fills()
        sizes[part] = (round(pf[0]["requested"]), pf[0]["quantity"]) \
            if pf else (None, None)

    return {
        "first_adv": round(u[0].avg_volume),
        "entry_bps": rows[2026][0],
        "entry_all_equal": len(set(entries)) == 1,
        "rt_median": _median(rts),
        "rt_lo": min(rts),
        "rt_hi": max(rts),
        "rt_neg": sum(1 for r in rts if r < 0),
        "fill_half_requested": sizes[0.5][0],
        "fill_half": sizes[0.5][1],
        "fills_saturate": len({q for _, q in sizes.values()}) == 1,
        "fill_seed_invariant": len(set(fills)) == 1,
    }


def g_tca_ripple(ctx: Ctx) -> dict:
    """transaction-cost-analysis.md's macro boundary, method stated on the
    page: Momentum() over Universe.random(60, seed=11), sim seed 7, ten
    days. The agent trades 57 names; all 3 untouched names move (-10.72,
    +1.97 and +2.00 bps) against a 9.71 bps median direct impact, so the
    largest ripple now EXCEEDS the median direct impact. The channel needs a
    horizon: nothing leaks at one or two days, nine untouched names leak at
    three and eighteen at four. Pinning VIX returns untouched_moved() to
    empty, byte-exact. Mirrors the assertions
    examples/07-research-workflow.py runs every time."""
    u = _u(60, 11)

    def analyse(days, scenario=None):
        return pt.tca.analyse(Momentum(), seed=7, universe=u, days=days,
                              scenario=scenario)

    jobs = {
        "full": lambda: analyse(10),
        "pinned": lambda: analyse(10, Scenario().hold(vix=15.0)),
        "d1": lambda: analyse(1),
        "d2": lambda: analyse(2),
        "d3": lambda: analyse(3),
        "d4": lambda: analyse(4),
    }
    with ThreadPoolExecutor(max_workers=min(6, ctx.workers)) as pool:
        done = dict(zip(jobs, pool.map(lambda f: f(), jobs.values())))

    ex = done["full"]
    traded = {f["ticker"] for f in ex.fills}
    leaked = ex.untouched_moved()
    leak_bps = sorted(ex.impact_bps(t) for t in leaked)
    direct = sorted(abs(bps) for name, bps in ex.moved().items()
                    if name in traded)

    return {
        "traded_names": len(traded),
        "untouched_names": len(u) - len(traded),
        "leaked_count": len(leaked),
        "leak_min_bps": leak_bps[0] if leak_bps else None,
        "leak_mid_bps": leak_bps[len(leak_bps) // 2] if leak_bps else None,
        "leak_max_bps": leak_bps[-1] if leak_bps else None,
        "direct_median_bps": direct[len(direct) // 2] if direct else None,
        # The horizon profile, one number per day count. The old
        # `short_horizon_no_leak` boolean asserted that days 2, 3 and 4 all
        # leak nothing, which stopped being true when `vix_return_source`
        # went to 1.0: the page publishes the counts instead.
        "leak_d1": len(done["d1"].untouched_moved()),
        "leak_d2": len(done["d2"].untouched_moved()),
        "leak_d3": len(done["d3"].untouched_moved()),
        "leak_d4": len(done["d4"].untouched_moved()),
        "no_leak_through_d2": not (done["d1"].untouched_moved()
                                   or done["d2"].untouched_moved()),
        "d1_no_orders": len({f["ticker"] for f in done["d1"].fills}) == 0,
        "pinned_empty": done["pinned"].untouched_moved() == [],
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
    treat ratios as portable, so the ratios are the comparable outputs.

    The recording overhead is NOT resolvable as a point by wall-clock timing
    on a working machine: measured across two sessions it came out -5.43%,
    -0.48% and +0.96% as a min-of-3 point, and a 32-pair interleaved study
    put the true value near +1% with nearly half the pairs negative. So the
    overhead here is the MEDIAN of eight interleaved pairs with alternating
    within-pair order, and the inventory judges it against a band or a bound,
    never at printed precision. Expect the median to straddle zero."""
    u10 = _u(10, 7)
    u100 = _u(100, 7)

    def best_of(n, fn):
        """Minimum of n runs: single timings carry cold-start noise."""
        return min(_timed(fn) for _ in range(n))

    def _timed(fn):
        t0 = time.perf_counter()
        fn()
        return time.perf_counter() - t0

    pt.Engine(seed=7, universe=u10).run_days(30)  # warm-up

    t10 = best_of(3, lambda: pt.Engine(seed=7, universe=u10).run_days(252))

    # Row count from one untimed recorded run, released before any timing
    # starts: a recorded engine holds ~1 GB of raw buffers, and keeping one
    # alive puts memory pressure on every timing that follows it. It also
    # warms the recording path before the paired timings below.
    e = pt.Engine(seed=7, universe=u100)
    e.run_days(252, record=True)
    truth_rows = pa.table(e.truth()).num_rows
    del e

    def plain():
        pt.Engine(seed=7, universe=u100).run_days(252)

    def recorded():
        engine = pt.Engine(seed=7, universe=u100)
        engine.run_days(252, record=True)

    # Eight interleaved pairs, alternating within-pair order, so thermal or
    # background drift hits both configurations alike and any slow monotonic
    # drift cancels across pairs instead of biasing one side.
    pairs = []
    for i in range(8):
        if i % 2 == 0:
            tp, tr = _timed(plain), _timed(recorded)
        else:
            tr, tp = _timed(recorded), _timed(plain)
        pairs.append((tp, tr))
    overheads = [(tr / tp - 1.0) * 100.0 for tp, tr in pairs]
    t100 = min(tp for tp, _ in pairs)
    t100_rec = min(tr for _, tr in pairs)

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
        "overhead_med": _median(overheads),
        "neg_pair_frac": sum(1 for o in overheads if o < 0) / len(overheads),
        "overhead_pairs": [round(o, 3) for o in overheads],
        "speedup": t_serial / t_8w,
    }


def g_fork(ctx: Ctx) -> dict:
    """forking-a-simulation.md: branch < 1 ms, Checkpoint.resume seconds.
    The page does not say what run length the 2.7 s was measured over;
    replay cost scales with the order log, so the absolute is doubly
    machine- and method-bound. This measures the 30-day run that reproduces
    the page's branch/resume ratio - the claim's portable part (three
    orders of magnitude, judged as a band on log10) - and reports the
    resume wall clock the way every other wall clock is reported: as
    machine_bound, never judged at printed precision."""
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
    """README's worked example: examples/07-research-workflow.py, run whole."""
    import contextlib
    import io
    spec = importlib.util.spec_from_file_location(
        "research_workflow", ctx.root / "examples" / "07-research-workflow.py")
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


# ---------------------------------------------------------------------------
# docs/scenario-recipes.md
# ---------------------------------------------------------------------------
#
# The recipe page publishes two different kinds of number and they age
# differently. The CONFIGS and their historical anchors are the durable half:
# a hiking cycle is 11 increases from 0-0.25% to 5.25-5.50%, and no engine
# change touches that. The MEASURED EFFECTS are this build's answer to those
# configs and move whenever the model does, which is why every one of them is
# a row here rather than a number typed into prose.
#
# These builders are the page's code blocks, copied verbatim. That is
# deliberate duplication in one direction only: the page shows what this
# module runs, so a reader who pastes a block gets the run the harness
# measured. Editing a config here without editing the page is the failure
# this arrangement is meant to make impossible, so the inventory rows carry
# the page's line numbers.

#: The universe/seed pair docs/scenarios.md uses for every RATE claim, reused
#: so a reader can hold one convention in their head across both pages.
_RECIPE_PRICE_U, _RECIPE_PRICE_SEED = (20, 4), 5
#: The pair both pages use for every VOLATILITY claim, for the same reason.
_RECIPE_VOL_U, _RECIPE_VOL_SEED = (20, 11), 3
#: Every published figure on the recipe page was measured under pt-v1, which
#: was the default at the time and is not any more. The page pins it in every
#: block that runs the engine; this harness has to pin the same preset or it
#: measures pt-v12 against pt-v1 figures and reports the whole group MOVED.
_RECIPE_MODEL = "pt-v1"


def _r1_hiking_cycle() -> Scenario:
    """Recipe 1: a hiking cycle of the 2022-23 shape."""
    return Scenario.rate_shock(start=0.00125, end=0.0538, over=90,
                               credit_spread=0.02)


def _r2_inflation_shock() -> Scenario:
    """Recipe 2: an inflation shock of the 2021-22 shape."""
    return Scenario("inflation shock").ramp(
        "inflation_rate", start=0.014, end=0.091, over=100, begin=5)


def _r3_liquidity_crisis() -> Scenario:
    """Recipe 3: a liquidity crisis of the 2008 shape.

    The VIX spike comes from the built-in shape and the credit leg is a ramp
    on a DIFFERENT field. `vix_shock` is a constructor, so it has to come
    first; the page also claims it is exactly a `hold` and a `ramp`, which
    `rule2_vix_shock_is_hold_then_ramp` measures.
    """
    return (Scenario.vix_shock(calm=18.0, peak=80.0, at=20, over=60)
            .ramp("corporate_bond_yield", start=0.055, end=0.095,
                  over=40, begin=20))


def _r4_contraction() -> Scenario:
    """Recipe 4: a contraction regime."""
    return (Scenario("contraction")
            .hold(cycle="contraction")
            .step("fear_greed_index", before=50.0, after=25.0, at=5))


def _r4_baseline() -> Scenario:
    """Recipe 4's baseline, which MUST be passed explicitly: `compare`'s
    default is `hold(**scenario.at(0))`, and for a scenario that is only a
    level that is the scenario itself, so the default call is refused."""
    return (Scenario("expansion baseline")
            .hold(cycle="expansion")
            .hold(fear_greed_index=50.0))


def _r5_compound_path(days: int = 120) -> Scenario:
    """Recipe 5: a compound episode of the 2020 shape.

    Four fields on four different schedules, the policy rate taking three
    levels of its own. Chaining expresses this since pins layer as segments
    -- `_r5_compound_chained` is the same path written that way, and
    `r5_chained_matches_loaded` measures the two against each other -- but
    the recipe builds it as data because every leg is arithmetic, which
    reads better as a function than as ten chained calls.
    """
    rows = []
    for d in range(days):
        if d < 15:                      # calm
            vix = 15.0
        elif d < 60:                    # spike to the record close, decaying
            vix = 82.0 + (18.0 - 82.0) * (d - 15) / 45
        else:
            vix = 18.0

        if d < 18:                      # policy: two cuts, ten days apart
            ff = 0.01625
        elif d < 28:
            ff = 0.01125
        else:
            ff = 0.00125

        if d < 18:                      # credit: blow-out, then facilities
            corp = 0.036
        elif d < 40:
            corp = 0.036 + (0.105 - 0.036) * (d - 18) / 22
        elif d < 90:
            corp = 0.105 + (0.045 - 0.105) * (d - 40) / 50
        else:
            corp = 0.045

        qe = 0.0 if d < 40 else min(0.10, 0.10 * (d - 40) / 30)
        rows.append({"day": d, "vix": vix, "federal_funds_rate": ff,
                     "corporate_bond_yield": corp, "qe_pe_boost": qe})

    return Scenario.from_json(json.dumps(
        {"schema": 1, "label": "compound: pandemic shape",
         "days": days, "path": rows}))


def _r5_compound_chained() -> Scenario:
    """Recipe 5's path written as chained segments instead of loaded data.

    Ten pins across four fields, each field's pins in strictly increasing
    start-day order, with a `hold` in front of every field that does not
    begin moving on day zero. The page publishes the claim that this and
    `_r5_compound_path()` are the same scenario, which is the segment rule
    stated on something big enough for the statement to mean anything.
    """
    return (Scenario("compound: chained")
            .hold(vix=15.0)
            .ramp("vix", start=82.0, end=18.0, over=45, begin=15)
            .hold(federal_funds_rate=0.01625)
            .step("federal_funds_rate", before=0.01625, after=0.01125, at=18)
            .step("federal_funds_rate", before=0.01125, after=0.00125, at=28)
            .hold(corporate_bond_yield=0.036)
            .ramp("corporate_bond_yield", start=0.036, end=0.105,
                  over=22, begin=18)
            .ramp("corporate_bond_yield", start=0.105, end=0.045,
                  over=50, begin=40)
            .hold(qe_pe_boost=0.0)
            .ramp("qe_pe_boost", start=0.0, end=0.10, over=30, begin=40))


def _refusal(call) -> str | None:
    """The `ValidationError` message ``call`` raises, or None if it returns.

    The page's rules are claims about what the surface REFUSES, and a claim
    that something raises is only worth pinning if it also pins why: a call
    that started raising for an unrelated reason would keep a bare
    "it raised" row green while the page's explanation went stale. So the
    message comes back and the callers below assert on what is in it.
    """
    try:
        call()
    except pt.ValidationError as exc:
        return str(exc)
    return None


def _refuses(call, *phrases: str) -> bool:
    """True if ``call`` is refused AND the refusal contains every phrase.

    The phrases are the ones the page quotes or paraphrases, so this is the
    structural form of "the page's account of this refusal is still true".
    """
    message = _refusal(call)
    return message is not None and all(p in message for p in phrases)


def g_recipes(ctx: Ctx) -> dict:
    """docs/scenario-recipes.md: every measured effect, plus the composition
    rules the page teaches, pinned as structural claims so that a future
    change to the `Scenario` surface flags the page rather than leaving it
    describing behaviour the library no longer has.

    That mechanism has fired once already and is worth the note. The page
    used to warn about four silent conflicts and three of them were pinned
    here; PR #39 composed one and refused the other two, the three rows went
    red, and the page was rewritten around what the surface does now. Rows
    that pin a MODEL of the surface, rather than a number it produces, are
    the only ones that can do that -- every measured figure below reproduces
    unchanged across that fix.

    Price effects use `Universe.random(20, seed=4)` at sim seed 5 and
    volatility effects `Universe.random(20, seed=11)` at sim seed 3 -- the
    two conventions docs/scenarios.md already uses, restated on the page."""
    up = _u(*_RECIPE_PRICE_U)
    uv = _u(*_RECIPE_VOL_U)
    out: dict = {}

    def price(scenario, days, baseline=None):
        return compare(scenario, seed=_RECIPE_PRICE_SEED, universe=up,
                       days=days, baseline=baseline, model=_RECIPE_MODEL)

    def vol(scenario, days=120):
        return pt.facts.measure(seed=_RECIPE_VOL_SEED, universe=uv,
                                days=days, scenario=scenario,
                                model=_RECIPE_MODEL)

    jobs = {
        "r1": lambda: price(_r1_hiking_cycle(), 120),
        # 40 days is inside the first central-bank meeting window, 120 is
        # well past it: the pair is the page's evidence that the meeting
        # -cadence trap is not specific to the policy rate.
        "r2_early": lambda: price(_r2_inflation_shock(), 40),
        "r2_late": lambda: price(_r2_inflation_shock(), 120),
        "r3_price": lambda: price(_r3_liquidity_crisis(), 120),
        "r3_vol": lambda: vol(_r3_liquidity_crisis()),
        "r3_calm": lambda: vol(Scenario("calm").hold(vix=18.0)),
        "r4": lambda: price(_r4_contraction(), 120, _r4_baseline()),
        "r5_price": lambda: price(_r5_compound_path(), 120),
        "r5_vol": lambda: vol(_r5_compound_path()),
    }
    with ThreadPoolExecutor(max_workers=min(9, ctx.workers)) as pool:
        got = dict(zip(jobs, pool.map(lambda f: f(), jobs.values())))

    out["r1_median_pct"] = got["r1"]["median_pct"]
    out["r1_worst_pct"] = got["r1"]["worst_pct"]
    out["r1_best_pct"] = got["r1"]["best_pct"]
    out["r1_exact"] = got["r1"]["exact"]

    out["r2_early_max_abs_pct"] = max(abs(x) for x in got["r2_early"]["move_pct"])
    out["r2_late_median_pct"] = got["r2_late"]["median_pct"]

    out["r3_median_pct"] = got["r3_price"]["median_pct"]
    out["r3_worst_pct"] = got["r3_price"]["worst_pct"]
    out["r3_vol_pct"] = got["r3_vol"]["annualised_vol_pct"]
    out["r3_calm_vol_pct"] = got["r3_calm"]["annualised_vol_pct"]
    out["r3_vol_uplift_pct"] = (got["r3_vol"]["annualised_vol_pct"]
                                - got["r3_calm"]["annualised_vol_pct"])
    out["r3_corr"] = got["r3_vol"]["cross_sectional_corr"]
    out["r3_calm_corr"] = got["r3_calm"]["cross_sectional_corr"]

    out["r4_median_pct"] = got["r4"]["median_pct"]
    out["r4_exact"] = got["r4"]["exact"]

    # Recipe 4 pins `cycle` and `fear_greed_index`, NEITHER of which reaches
    # fair value directly -- both steer the macro chain, and the chain is the
    # claim worth publishing because it is the mechanism. Read the recorded
    # macro rather than inferring it from the price delta.
    def macro_end(scenario):
        e = run_scenario(scenario, seed=_RECIPE_PRICE_SEED, universe=up,
                         days=120, record=True, model=_RECIPE_MODEL)
        t = pa.RecordBatchReader.from_stream(
            e.macro_table()).read_all().to_pydict()
        return {"ff": t["federal_funds_rate"][119],
                "corp": t["corporate_bond_yield"][119],
                "infl": t["inflation_rate"][119]}

    con, exp = macro_end(_r4_contraction()), macro_end(_r4_baseline())
    # Published as percentages, because that is how the page's table prints
    # them: the inventory should compare the number a reader can see.
    for world, ended in (("contraction", con), ("expansion", exp)):
        for field in ("ff", "corp", "infl"):
            out[f"r4_{world}_{field}_pct"] = ended[field] * 100.0

    out["r5_median_pct"] = got["r5_price"]["median_pct"]
    out["r5_worst_pct"] = got["r5_price"]["worst_pct"]
    out["r5_best_pct"] = got["r5_price"]["best_pct"]
    out["r5_vol_pct"] = got["r5_vol"]["annualised_vol_pct"]
    out["r5_exact"] = got["r5_price"]["exact"]

    # -- the composition rules, pinned as behaviour -------------------------
    #
    # These rows exist for the same reason the trap rows they replace did.
    # The page teaches a model of the `Scenario` surface, and a model is the
    # half of a document that no measured figure can catch going stale: the
    # numbers below would all still reproduce under a surface that had
    # quietly stopped refusing anything. So each rule the page states is a
    # structural claim here, and the refusals are pinned by what their
    # messages SAY rather than by the bare fact that they raise -- see
    # `_refuses`. Three of these rows replace rows that pinned the opposite
    # behaviour and fired when PR #39 fixed it, which is the mechanism
    # working, and the reason to keep paying for it.

    # Rule 1, clause by clause, on the page's three-segment example. The
    # step is the FIRST pin, so it owns day 0 (before its own start) and
    # days 60-74 (until the next pin begins); the ramp owns the rest.
    segments = (Scenario("spike, plateau, decay")
                .step("vix", before=15.0, after=48.0, at=60)
                .ramp("vix", start=48.0, end=22.0, over=45, begin=75))
    out["rule1_seg_day0"] = segments.at(0)["vix"]
    out["rule1_seg_day60"] = segments.at(60)["vix"]
    out["rule1_seg_day74"] = segments.at(74)["vix"]
    out["rule1_seg_day97"] = segments.at(97)["vix"]
    out["rule1_seg_day120"] = segments.at(120)["vix"]

    # The page's own step-then-decay example, in the form the same-day
    # refusal advises: `hold` states the level, `ramp` states the episode,
    # and a ramp starting AT its start value makes day 60 the jump.
    spike = (Scenario("spike and decay")
             .hold(vix=15.0)
             .ramp("vix", start=48.0, end=18.0, over=40, begin=60))
    out["rule1_example_day0"] = spike.at(0)["vix"]
    out["rule1_example_day30"] = spike.at(30)["vix"]
    out["rule1_example_day59"] = spike.at(59)["vix"]
    out["rule1_example_day60"] = spike.at(60)["vix"]
    out["rule1_example_day80"] = spike.at(80)["vix"]
    out["rule1_example_day100"] = spike.at(100)["vix"]
    out["rule1_table_day0"] = spike.table(5)[0]["vix"]

    # The two refused orderings. The page prints the same-day message in
    # full, so the message itself is the published figure -- reword it and
    # the page is stale, which is exactly what should be reported.
    out["rule1_same_day_message"] = _refusal(
        lambda: (Scenario("looks right, is not")
                 .step("vix", before=15.0, after=48.0, at=60)
                 .ramp("vix", start=48.0, end=18.0, over=40, begin=60)))
    # ...and the reverse of that pair is the same conflict, so it is refused
    # too. The old rows here measured the two orderings failing in two
    # different wrong ways; there is now one rule and it is symmetric.
    out["rule1_same_day_reversed_refused"] = _refuses(
        lambda: (Scenario()
                 .ramp("vix", start=48.0, end=18.0, over=40, begin=60)
                 .step("vix", before=15.0, after=48.0, at=60)),
        "both begin on day 60")
    out["rule1_out_of_order_refused"] = _refuses(
        lambda: (Scenario()
                 .ramp("vix", start=48.0, end=22.0, over=45, begin=75)
                 .step("vix", before=15.0, after=48.0, at=60)),
        "are out of order", "must be declared in the order they happen")

    # Rule 2: the shape constructors build a whole scenario, and calling one
    # on a configured instance is refused rather than silently discarding
    # the receiver. The refusal names the receiver's fields, which is the
    # part of it the page describes.
    base = Scenario("base").hold(inflation_rate=0.06, fear_greed_index=20.0)
    out["rule2_vix_shock_on_instance_refused"] = _refuses(
        lambda: base.vix_shock(calm=15.0, peak=60.0, at=10, over=30),
        "is a CONSTRUCTOR, not a step",
        "drives fear_greed_index, inflation_rate")
    out["rule2_rate_shock_on_instance_refused"] = _refuses(
        lambda: base.rate_shock(start=0.025, end=0.05, over=30),
        "is a CONSTRUCTOR, not a step")
    out["rule2_from_json_on_instance_refused"] = _refuses(
        lambda: base.from_json("{}"), "is a CONSTRUCTOR, not a step")
    out["rule2_base_intact"] = ",".join(base.fields)

    # The composing form, and the page's claim that the constructors are
    # convenience rather than capability: both shapes are writable as pins
    # now that pins layer, and the hand-written versions are the same paths.
    right = (Scenario.vix_shock(calm=15.0, peak=60.0, at=10, over=30)
             .hold(inflation_rate=0.06, fear_greed_index=20.0))
    out["rule2_right_fields"] = ",".join(right.fields)
    out["rule2_vix_shock_is_hold_then_ramp"] = right.table(120) == (
        Scenario()
        .hold(inflation_rate=0.06, fear_greed_index=20.0)
        .hold(vix=15.0)
        .ramp("vix", start=60.0, end=15.0, over=30, begin=10)).table(120)
    out["rule2_rate_shock_is_two_ramps"] = (
        _r1_hiking_cycle().table(120) == (
            Scenario()
            .ramp("federal_funds_rate", start=0.00125, end=0.0538, over=90)
            .ramp("corporate_bond_yield", start=0.02125, end=0.0738,
                  over=90)).table(120))

    # Rule 3: compare()'s default baseline is hold(**scenario.at(0)), which
    # for a path that never moves inside the horizon IS the scenario. All
    # four shapes that reach that are refused; the page lists three of them
    # and the fourth is an explicit baseline realising the same path.
    held = Scenario("held crisis").hold(vix=45.0)
    out["rule3_hold_only_refused"] = _refuses(
        lambda: price(held, 30), "is CONSTANT over all 30 days")
    out["rule3_step_at_zero_refused"] = _refuses(
        lambda: price(Scenario("step at zero")
                      .step("vix", before=15.0, after=45.0, at=0), 30),
        "is CONSTANT over all 30 days")
    out["rule3_after_horizon_refused"] = _refuses(
        lambda: price(Scenario("late")
                      .ramp("federal_funds_rate", start=0.025, end=0.05,
                            over=30, begin=60), 40),
        "begins on or after day 40", "Run at least 61 days")
    out["rule3_empty_refused"] = _refuses(
        lambda: price(Scenario("nothing"), 30), "it drives no fields at all")
    out["rule3_same_path_baseline_refused"] = _refuses(
        lambda: price(held, 30, Scenario("also held").hold(vix=45.0)),
        "would difference two identical worlds")

    # The explicit baseline the page tells readers to pass instead.
    out["rule3_explicit_median_pct"] = price(
        held, 30, Scenario("calm").hold(vix=15.0))["median_pct"]

    # ...and why a PRICE delta is the wrong measure for a volatility
    # scenario even once the baseline is right: across sim seeds 1-8 the
    # same VIX 45-vs-15 comparison spans both signs. The page tells readers
    # to measure realised volatility instead, and this band is the evidence.
    def vix_delta(seed):
        return compare(Scenario().hold(vix=45.0), seed=seed, universe=up,
                       days=30, baseline=Scenario().hold(vix=15.0),
                       model=_RECIPE_MODEL)["median_pct"]

    with ThreadPoolExecutor(max_workers=min(8, ctx.workers)) as pool:
        band = list(pool.map(vix_delta, range(1, 9)))
    out["rule3_seedband_lo"] = min(band)
    out["rule3_seedband_hi"] = max(band)
    out["rule3_seedband_negative"] = sum(1 for x in band if x < 0)

    # The from_json path the compound recipe rests on: an arbitrary
    # piecewise path on any number of fields, holding its final values past
    # the horizon -- and, since pins layer, reproducible by chaining, which
    # is the claim recipe 5 now makes in place of "chaining cannot do this".
    p = _r5_compound_path()
    out["r5_day0_vix"] = p.at(0)["vix"]
    out["r5_day17_vix"] = round(p.at(17)["vix"], 5)
    out["r5_day30_corp"] = round(p.at(30)["corporate_bond_yield"], 5)
    out["r5_day17_ff"] = p.at(17)["federal_funds_rate"]
    out["r5_day30_ff"] = p.at(30)["federal_funds_rate"]
    out["r5_beyond_horizon_ff"] = p.at(500)["federal_funds_rate"]
    out["r5_chained_matches_loaded"] = (
        _r5_compound_chained().table(120) == p.table(120))
    return out


GROUPS = {
    "determinism": g_determinism,
    "consts": g_consts,
    "arith": g_arith,
    "truth_residual": g_truth_residual,
    "realism_sample": g_realism_sample,
    "realism_six": g_realism_six,
    "realism_heldout": g_realism_heldout,
    "rebalance": g_rebalance,
    "horizon": g_horizon,
    "oracle_config": g_oracle_config,
    "ranking": g_ranking,
    "scenario_leaderboard": g_scenario_leaderboard,
    "recipes": g_recipes,
    "llm_leaderboard": g_llm_leaderboard,
    "llm_impact": g_llm_impact,
    "rng": g_rng,
    "macro_chain": g_macro_chain,
    "pin_macro": g_pin_macro,
    "fedfunds": g_fedfunds,
    "spec": g_spec,
    "vix": g_vix,
    "tca_vix": g_tca_vix,
    "tca_example": g_tca_example,
    "tca_ripple": g_tca_ripple,
    "drawdiv": g_drawdiv,
    "replay": g_replay,
    "universe_stats": g_universe_stats,
    "perf": g_perf,
    "fork": g_fork,
    "workflow": g_workflow,
}

#: Groups that measure wall clocks; run serially after everything else.
TIMED_GROUPS = ("perf", "fork", "workflow")
