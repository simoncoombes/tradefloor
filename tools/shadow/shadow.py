"""Implied draws and the shadow run.

    python tools/shadow/shadow.py --year calm --out out/shadow-calm
    python tools/shadow/shadow.py --year crisis --out out/shadow-crisis

For each real trading day, find the draw vector under which the engine
reproduces the day's observed closes, feed it in, and walk the engine
along the real path. The unknowns are day aggregates: the market
innovation, one innovation per sector, one idiosyncratic innovation per
name, and the jump indicators at the previous close. The prior is a
standard normal on each innovation and the engine's own intensity on each
indicator. The estimate is the maximum a posteriori per day, by
Levenberg-Marquardt on the forward map with a finite-difference Jacobian
on the first iteration and Broyden updates after it.

# The forward map

The engine is forked at the state before the previous day's close. The
fork closes that day under the jump patches (the jump lands at the close
and is first seen at the next open), opens the real day, runs the session
under the market patches, and its prices before its own close are the
model's closes. This is ``noise.run_day_with`` with the day boundary
moved by one close, for that reason. A day aggregate ``x`` is installed
as a common shift of ``x / sqrt(T)`` on every one of the ``T`` tick
normals of that name (or the market factor, or the sector) on top of the
values the engine's own stream delivered, so the intra-day path keeps its
own randomness and only the day's sum moves; the day sum is the
identified quantity (``noise.attribute`` states why).

# What it cannot do, and says

Order flow from an agent is not in real data and is held at zero. The
fundamentals are synthetic: each real name borrows a same-sector
instrument's valuation multiples from the certified roster, scaled to its
real starting price, so fair value is not the real company's and
mispricing is measured against a synthetic anchor. Where the forward map
is clamped by a breaker or the book so that no draw vector reaches the
observed close, the day is reported with the binding name and its
residual rather than accepted at a nearest fit. The report lists the days
the solve failed before anything it found.

The solver lives here and uses numpy. Nothing in the library imports it.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import struct
import subprocess
import sys
import time

import numpy as np

import tradefloor as tf
from tradefloor import noise

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import data as realdata  # noqa: E402

TICKS = 390
CLOCK = (9, 30, 3)


# -- the roster ----------------------------------------------------------

def build_universe(d: dict, first: int) -> list:
    """The forty real names as engine instruments at the year's start.

    Real: the starting price (the close before the first session), the
    average volume over the sixty prior sessions, and beta against the
    index over the prior 252 sessions. Synthetic: shares, earnings, book
    value, growth and short interest from a same-sector instrument of the
    certified roster, the per-share figures scaled to the real price so
    the donor's multiples carry over.
    """
    donors: dict[str, list] = {}
    for inst in tf.Universe.random(40, seed=111):
        donors.setdefault(inst.sector, []).append(inst)
    if first < 253:
        raise SystemExit(f"only {first} sessions precede the year; the beta "
                         "estimate needs 252 and the window in data.YEARS "
                         "is meant to provide them")
    idx = np.array(d["index"][first - 253:first], dtype=float)
    idx_r = np.diff(np.log(idx))
    out = []
    for n, t in enumerate(realdata.TICKERS):
        sector = realdata.ENGINE_SECTOR[realdata.PANEL_SECTOR[t]]
        pool = donors.get(sector) or [i for p in donors.values() for i in p]
        donor = pool[n % len(pool)]
        closes = np.array(d["closes"][t][first - 253:first], dtype=float)
        r = np.diff(np.log(closes))
        beta = float(np.cov(r, idx_r)[0, 1] / np.var(idx_r, ddof=1))
        vols = [v for v in d["volumes"][t][first - 60:first] if v]
        scale = closes[-1] / donor.initial_price
        out.append(tf.Instrument(
            t, sector, initial_price=float(closes[-1]),
            shares_outstanding=donor.shares_outstanding,
            eps=donor.eps * scale,
            book_value_per_share=donor.book_value_per_share * scale,
            revenue_growth=donor.revenue_growth,
            avg_volume=float(np.mean(vols)) if vols else donor.avg_volume,
            beta=max(0.1, min(3.0, beta)),
            short_interest=donor.short_interest))
    return out


# -- the forward map -----------------------------------------------------

def prices(engine) -> np.ndarray:
    raw = engine.prices()
    return np.array(struct.unpack(f"<{len(raw) // 8}d", raw))


def column(engine, name) -> np.ndarray:
    raw = engine.column(name)
    return np.array(struct.unpack(f"<{len(raw) // 8}d", raw))


class Layout:
    """Where the real day's market normals sit, and what they were.

    From one traced control run of the day: the addresses of the market
    factor, each sector's and each name's normals at every tick, and the
    values the stream delivered there. The layout's arithmetic is the
    site sequence the library pins; the log is used rather than the
    arithmetic so a shift is applied on top of the values actually drawn.
    """

    def __init__(self, entries, n_names: int) -> None:
        self.market: list = []
        self.sector: dict[int, list] = {}
        self.company: dict[int, list] = {}
        for e in entries:
            if e.site == "market_factor_z":
                self.market.append(e)
            elif e.site == "sector_z":
                self.sector.setdefault(e.tag, []).append(e)
            elif e.site == "factor_idio_z":
                self.company.setdefault(e.tag, []).append(e)
        self.sectors = sorted(self.sector)
        self.ticks = len(self.market)
        if self.ticks != TICKS or len(self.company) != n_names:
            raise RuntimeError(
                f"the day's layout has {self.ticks} ticks and "
                f"{len(self.company)} names; expected {TICKS} and {n_names}")

    def patches(self, x: np.ndarray) -> list:
        """The day aggregates ``x`` as tick patches: ``x / sqrt(T)`` on top
        of every delivered value."""
        step = 1.0 / math.sqrt(self.ticks)
        out = []
        k = 0
        for e in self.market:
            out.append(noise.Patch(e.address, e.value + x[k] * step))
        k += 1
        for s in self.sectors:
            for e in self.sector[s]:
                out.append(noise.Patch(e.address, e.value + x[k] * step))
            k += 1
        for i in sorted(self.company):
            for e in self.company[i]:
                out.append(noise.Patch(e.address, e.value + x[k] * step))
            k += 1
        return out

    @property
    def size(self) -> int:
        return 1 + len(self.sectors) + len(self.company)


class Forward:
    """The forward map of one real day on one engine state."""

    def __init__(self, engine, day: int, n_names: int) -> None:
        self.engine = engine
        self.day = day
        self.n = n_names
        self.base = prices(engine)
        u, z = engine.stream_positions()["jumps"]
        self.jump_u, self.jump_z = u, z
        control, = engine.fork(1)
        control.trace_draws("market", day, day)
        self._drive(control, [])
        self.layout = Layout(noise.draw_log(control, "market", day, day), n_names)
        self.evals = 0

    def jump_patches(self, market: float | None, company: dict) -> list:
        """Unfire every jump at the previous close except those named.

        ``market`` is the market jump's normal when it fires or ``None``;
        ``company`` maps a name's index to its jump normal. Day zero has no
        previous close and takes no jump patches.
        """
        if self.day == 0:
            return []
        out = []
        u, z = self.jump_u, self.jump_z
        if market is None:
            out.append(noise.Patch(noise.DrawAddress("jumps", "uniform", u), noise.NO_FIRE))
        else:
            out.append(noise.Patch(noise.DrawAddress("jumps", "uniform", u), 0.0))
            out.append(noise.Patch(noise.DrawAddress("jumps", "normal", z), float(market)))
        for i in range(self.n):
            if i in company:
                out.append(noise.Patch(noise.DrawAddress("jumps", "uniform", u + 1 + i), 0.0))
                out.append(noise.Patch(noise.DrawAddress("jumps", "normal", z + 1 + i), float(company[i])))
            else:
                out.append(noise.Patch(noise.DrawAddress("jumps", "uniform", u + 1 + i), noise.NO_FIRE))
        return out

    def _drive(self, fork, patches) -> None:
        if patches:
            noise.patch_draws(fork, patches)
        if self.day > 0:
            fork.close_market()
        fork.open_market()
        fork.run_session(*CLOCK, TICKS)

    def returns(self, x: np.ndarray, jumps: list) -> np.ndarray:
        """Model log returns of the day under aggregates ``x`` and jumps."""
        fork, = self.engine.fork(1)
        self._drive(fork, jumps + self.layout.patches(x))
        self.evals += 1
        return np.log(prices(fork) / self.base)

    def commit(self, x: np.ndarray, jumps: list) -> None:
        """Install the day's draws on the engine and walk it to the close."""
        self._drive(self.engine, jumps + self.layout.patches(x))
        self.engine.record(self.day)


# -- the solver ----------------------------------------------------------

def solve(forward: Forward, r_obs: np.ndarray, jumps: list, x0: np.ndarray,
          extra: int = 0, *, sigma: float, step: float = 0.25,
          max_iter: int = 8, tol: float = 1e-4) -> dict:
    """Levenberg-Marquardt for the MAP day aggregates.

    Minimises ``|r(x) - r_obs|^2 / (2 sigma^2) + |x|^2 / 2``. ``extra``
    trailing entries of ``x`` are jump normals: they enter the forward map
    through ``jumps`` (a function of ``x[-extra:]``) and carry the same
    prior. Returns the solution, the residual, the Jacobian and whether
    the residual tolerance was met.
    """
    m = len(x0)

    def jump_list(x):
        return jumps(x[m - extra:]) if callable(jumps) else jumps

    def resid(x):
        return forward.returns(x, jump_list(x)) - r_obs

    x = x0.copy()
    r = resid(x)
    J = np.zeros((len(r), m))
    for k in range(m):
        xk = x.copy()
        xk[k] += step
        J[:, k] = (resid(xk) - r) / step
    lam = 1e-3
    cost = (r @ r) / (2 * sigma ** 2) + (x @ x) / 2
    converged = False
    for _ in range(max_iter):
        A = J.T @ J / sigma ** 2 + np.eye(m)
        g = J.T @ r / sigma ** 2 + x
        accepted = False
        for _try in range(6):
            delta = np.linalg.solve(A + lam * np.eye(m), -g)
            x_new = x + delta
            r_new = resid(x_new)
            cost_new = (r_new @ r_new) / (2 * sigma ** 2) + (x_new @ x_new) / 2
            if cost_new < cost:
                dr = r_new - r
                J += np.outer(dr - J @ delta, delta) / (delta @ delta)
                improvement = (cost - cost_new) / max(cost, 1e-12)
                x, r, cost = x_new, r_new, cost_new
                lam = max(lam / 3, 1e-6)
                accepted = True
                # converged: the posterior stopped moving, which is the
                # optimiser's claim; whether the closes were reached is a
                # separate question the residual answers
                converged = improvement < tol
                break
            lam *= 10
        if not accepted:
            converged = True
            break
        if converged:
            break
    return {"x": x, "residual": r, "jacobian": J, "cost": float(cost),
            "converged": bool(converged)}


def solve_day(forward: Forward, r_obs: np.ndarray, intensities: tuple,
              *, sigma: float, candidates: int = 5) -> dict:
    """The day's MAP: continuous aggregates, then the jump indicators.

    Every jump at the previous close starts unfired. The market jump and
    then up to ``candidates`` names, taken in order of the size of their
    implied innovation, are each tried fired with their jump normal as one
    more unknown; a candidate is kept when the posterior cost, prior on
    the indicator included, falls. Greedy, one candidate at a time, and
    said so: a pair of jumps that only pays off together is not found.
    """
    p_market, p_idio = intensities
    m0 = forward.layout.size
    base = solve(forward, r_obs, forward.jump_patches(None, {}),
                 np.zeros(m0), sigma=sigma)
    fired_market = None
    fired: dict[int, float] = {}
    best = base
    best_cost = base["cost"] - math.log(1 - p_market) - forward.n * math.log(1 - p_idio)
    if forward.day > 0:
        # the market jump
        def jumps_m(tail):
            return forward.jump_patches(float(tail[0]), fired)
        trial = solve(forward, r_obs, jumps_m, np.append(best["x"][:m0], 0.0),
                      extra=1, sigma=sigma)
        cost = trial["cost"] - math.log(p_market) - forward.n * math.log(1 - p_idio)
        if cost < best_cost:
            best, best_cost, fired_market = trial, cost, float(trial["x"][-1])
        # the names, largest implied innovation first
        order = np.argsort(-np.abs(best["x"][1 + len(forward.layout.sectors):m0]))
        for i in [int(k) for k in order[:candidates]]:
            if abs(best["x"][1 + len(forward.layout.sectors) + i]) < 2.0:
                break
            tried = dict(fired)
            tried[i] = 0.0

            def jumps_i(tail, tried=tried, i=i):
                here = dict(tried)
                here[i] = float(tail[-1])
                return forward.jump_patches(
                    None if fired_market is None else float(tail[0]) if len(tail) > 1 else fired_market, here)

            extra = (1 if fired_market is not None else 0) + 1
            x0 = np.concatenate([best["x"][:m0],
                                 [fired_market] if fired_market is not None else [],
                                 [0.0]])
            trial = solve(forward, r_obs, jumps_i, x0, extra=extra, sigma=sigma)
            n_fired = len(tried)
            cost = (trial["cost"]
                    - (math.log(p_market) if fired_market is not None else math.log(1 - p_market))
                    - n_fired * math.log(p_idio)
                    - (forward.n - n_fired) * math.log(1 - p_idio))
            if cost < best_cost:
                best, best_cost = trial, cost
                fired = tried
                fired[i] = float(trial["x"][-1])
                if fired_market is not None:
                    fired_market = float(trial["x"][m0])
    x = best["x"][:m0]
    S = len(forward.layout.sectors)
    return {"x_market": float(x[0]), "x_sector": x[1:1 + S].tolist(),
            "x_idio": x[1 + S:].tolist(), "jump_market": fired_market,
            "jump_company": {int(k): v for k, v in fired.items()},
            "residual": best["residual"].tolist(), "cost": best_cost,
            "converged": best["converged"],
            "jacobian_idio_norm": np.linalg.norm(
                best["jacobian"][:, 1 + S:1 + S + forward.n], axis=0).tolist(),
            "evals": forward.evals,
            "jumps": forward.jump_patches(fired_market, fired),
            "x": x}


# -- the shadow run ------------------------------------------------------

def shadow(args) -> dict:
    d = realdata.load(args.year)
    year = {"calm": "2017", "crisis": "2020"}[args.year]
    first, last = realdata.year_slice(d, year)
    if args.days:
        last = min(last, first + args.days)
    universe = build_universe(d, first)
    tickers = realdata.TICKERS
    engine = tf.Engine(seed=args.seed, universe=universe, model=args.preset)
    params = dict(engine.model_params)
    intensities = (float(params["jump_intensity_market"]),
                   float(params["jump_intensity_idio"]))
    n = len(tickers)
    real = np.array([[d["closes"][t][k] for t in tickers]
                     for k in range(first - 1, last)])
    days = []
    started = time.time()
    for k, session in enumerate(range(first, last)):
        r_obs = np.log(real[k + 1] / real[k])
        fwd = Forward(engine, k, n)
        result = solve_day(fwd, r_obs, intensities, sigma=args.sigma)
        fwd.commit(result["x"], result["jumps"])
        model = prices(engine)
        snap = engine.state_snapshot()
        residual = np.array(result["residual"])
        worst = int(np.argmax(np.abs(residual)))
        reached = bool(np.abs(residual).max() < 5 * args.sigma)
        clamped = [tickers[i] for i in range(n)
                   if abs(residual[i]) > 5 * args.sigma
                   and result["jacobian_idio_norm"][i] < 1e-3]
        days.append({
            "day": k, "date": d["dates"][session],
            "x_market": result["x_market"], "x_sector": result["x_sector"],
            "x_idio": result["x_idio"], "jump_market": result["jump_market"],
            "jump_company": {tickers[i]: v for i, v in result["jump_company"].items()},
            "max_abs_residual": float(np.abs(residual).max()),
            "worst": tickers[worst], "converged": result["converged"],
            "reached": reached,
            "clamped": clamped, "evals": result["evals"],
            "vix_model": float(engine.macro_state.vix),
            "vix_real": d["vix"][session],
            "market_variance": snap.get("market_variance"),
            "universe_stress": snap.get("universe_stress"),
            "mispricing_mean": float(column(engine, "mispricing_s").mean()),
            "level_gap_mean_abs": float(np.mean(np.abs(np.log(model / real[k + 1])))),
        })
        if (k + 1) % 10 == 0:
            done = k + 1
            print(f"  day {done}/{last - first} {d['dates'][session]} "
                  f"res {days[-1]['max_abs_residual']:.2e} evals "
                  f"{result['evals']} vix {days[-1]['vix_model']:.1f}/"
                  f"{days[-1]['vix_real']:.1f} {time.time() - started:.0f}s",
                  flush=True)
    truth = []
    try:
        import pyarrow as pa
        truth = pa.table(engine.truth()).to_pylist()
    except ImportError:
        pass
    return {"args": vars(args), "year": year, "sessions": [first, last],
            "provenance": dict(d["provenance"], **build_provenance(engine)),
            "intensities": intensities, "days": days, "truth_rows": len(truth),
            "truth": truth, "seconds": time.time() - started}


def build_provenance(engine) -> dict:
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                                capture_output=True, text=True,
                                check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "unknown"
    return {"tradefloor": tf.version(), "commit": commit,
            "model_fingerprint": engine.model_fingerprint,
            "order_flow": "zero; no agent trades in a shadow run",
            "fundamentals": "synthetic donors from Universe.random(40, seed=111), "
                            "same sector, per-share figures scaled to the real "
                            "starting price"}


# -- diagnostics ---------------------------------------------------------

def acf(x: np.ndarray, lag: int) -> float:
    x = x - x.mean()
    if len(x) <= lag or x.std() == 0:
        return float("nan")
    return float((x[:-lag] @ x[lag:]) / (x @ x))


def diagnostics(run: dict) -> list[dict]:
    """The implied innovation series against an i.i.d. standard normal,
    each statistic mapped to the mechanism class that would move it."""
    days = run["days"]
    N = len(days)
    xm = np.array([d["x_market"] for d in days])
    xi = np.array([d["x_idio"] for d in days])
    se_k = math.sqrt(24 / N)
    se_a = 1 / math.sqrt(N)
    pooled = xi.ravel()
    kurt = lambda v: float(((v - v.mean()) ** 4).mean() / v.var() ** 2 - 3) if v.var() > 0 else float("nan")
    corr = np.corrcoef(xi.T)
    off = corr[np.triu_indices_from(corr, 1)]
    cross = float(np.nanmean(off))
    lead = np.corrcoef(xm[:-1], np.abs(xm[1:]))[0, 1] if N > 2 else float("nan")
    fired_m = sum(1 for d in days if d["jump_market"] is not None)
    fired_c = sum(len(d["jump_company"]) for d in days)
    rows = [
        {"statistic": "market innovation, mean", "value": float(xm.mean()),
         "expected": f"0 +- {se_a:.2f}", "mechanism": "drift",
         "flag": abs(xm.mean()) > 2 * se_a},
        {"statistic": "market innovation, sd", "value": float(xm.std()),
         "expected": f"1 +- {1 / math.sqrt(2 * N):.2f}",
         "mechanism": "market variance level",
         "flag": abs(xm.std() - 1) > 2 / math.sqrt(2 * N)},
        {"statistic": "market innovation, excess kurtosis", "value": kurt(xm),
         "expected": f"0 +- {se_k:.2f}", "mechanism": "jumps (tail weight)",
         "flag": abs(kurt(xm)) > 2 * se_k},
        {"statistic": "idio innovation, excess kurtosis (pooled)",
         "value": kurt(pooled), "expected": f"0 +- {math.sqrt(24 / len(pooled)):.2f}",
         "mechanism": "jumps (tail weight)",
         "flag": abs(kurt(pooled)) > 2 * math.sqrt(24 / len(pooled))},
        {"statistic": "|market innovation| acf lag 1", "value": acf(np.abs(xm), 1),
         "expected": f"0 +- {se_a:.2f}", "mechanism": "variance process (persistence)",
         "flag": abs(acf(np.abs(xm), 1)) > 2 * se_a},
        {"statistic": "|market innovation| acf lag 5", "value": acf(np.abs(xm), 5),
         "expected": f"0 +- {se_a:.2f}", "mechanism": "variance process (persistence)",
         "flag": abs(acf(np.abs(xm), 5)) > 2 * se_a},
        {"statistic": "idio innovation, mean pairwise correlation", "value": cross,
         "expected": f"0 +- {se_a:.2f}", "mechanism": "a missing factor",
         "flag": abs(cross) > 2 * se_a},
        {"statistic": "corr(market innovation, next |market innovation|)",
         "value": float(lead), "expected": f"0 +- {se_a:.2f}",
         "mechanism": "leverage (sign asymmetry)", "flag": abs(lead) > 2 * se_a},
        {"statistic": "market jumps fired", "value": float(fired_m),
         "expected": f"{run['intensities'][0] * N:.1f} at the preset's intensity",
         "mechanism": "jumps (frequency)",
         "flag": fired_m > 2 * run["intensities"][0] * N + 2},
        {"statistic": "company jumps fired", "value": float(fired_c),
         "expected": f"{run['intensities'][1] * N * len(days[0]['x_idio']):.1f}",
         "mechanism": "jumps (frequency)",
         "flag": fired_c > 2 * run["intensities"][1] * N * len(days[0]["x_idio"]) + 2},
    ]
    return rows


def render(run: dict) -> str:
    days = run["days"]
    N = len(days)
    failed = [d for d in days
              if not d.get("reached", d["max_abs_residual"] < 5 * run["args"]["sigma"])
              or d["clamped"]]
    vm = np.array([d["vix_model"] for d in days])
    vr = np.array([d["vix_real"] for d in days])
    gap = vm - vr
    lines = [f"# Shadow run: {run['year']} ({run['args']['year']})", "",
             "## Where the solve fails", ""]
    if not failed:
        lines.append(f"Every one of the {N} days reached its observed closes "
                     f"within five sigma ({5 * run['args']['sigma']:.0e} in "
                     "log return) with no binding clamp.")
    else:
        lines.append(f"{len(failed)} of {N} days did not reach the observed "
                     f"closes within five sigma ({5 * run['args']['sigma']:.0e} "
                     "in log return) or hit a binding clamp. A day is accepted "
                     "at the fit it reached; the residual is reported, not "
                     "absorbed.")
        lines += ["", "| day | date | worst name | max abs residual | "
                  "optimiser converged | binding clamp |", "|---|---|---|---|---|---|"]
        for d in sorted(failed, key=lambda d: -d["max_abs_residual"])[:25]:
            lines.append(f"| {d['day']} | {d['date']} | {d['worst']} | "
                         f"{d['max_abs_residual']:.2e} | {d['converged']} | "
                         f"{', '.join(d['clamped']) or 'none'} |")
    lines += ["", "## Setup", "",
              f"- sessions {run['sessions'][0]}..{run['sessions'][1]} of the "
              f"fetched window, {N} days solved, "
              f"{run['seconds']:.0f} s, {sum(d['evals'] for d in days)} "
              "forward evaluations",
              f"- preset {run['args']['preset']} (fingerprint "
              f"{run['provenance']['model_fingerprint']}), seed "
              f"{run['args']['seed']}, sigma {run['args']['sigma']} in log "
              "return units",
              f"- jump intensities (market, idio) {run['intensities']}",
              f"- order flow: {run['provenance']['order_flow']}",
              f"- fundamentals: {run['provenance']['fundamentals']}",
              f"- data: {run['provenance']['source']}; fetched "
              f"{sorted(set(run['provenance']['fetched'].values()))}",
              f"- tradefloor {run['provenance']['tradefloor']}, commit "
              f"{run['provenance']['commit']}",
              "", "## The implied innovations", "",
              "| statistic | value | i.i.d. N(0,1) expects | mechanism class "
              "| flag |", "|---|---|---|---|---|"]
    for row in diagnostics(run):
        lines.append(f"| {row['statistic']} | {row['value']:.3f} | "
                     f"{row['expected']} | {row['mechanism']} | "
                     f"{'YES' if row['flag'] else 'no'} |")
    lines += ["", "## The model's VIX along the real path", "",
              f"- correlation with the real VIX, day by day: "
              f"{np.corrcoef(vm, vr)[0, 1]:.3f}",
              f"- mean gap (model minus real) {gap.mean():+.2f} points, mean "
              f"absolute gap {np.abs(gap).mean():.2f}",
              f"- model range {vm.min():.1f}..{vm.max():.1f}, real range "
              f"{vr.min():.1f}..{vr.max():.1f}", "",
              "| date | real VIX | model VIX | gap |", "|---|---|---|---|"]
    for i in np.argsort(-np.abs(gap))[:10]:
        lines.append(f"| {days[i]['date']} | {vr[i]:.1f} | {vm[i]:.1f} | "
                     f"{gap[i]:+.1f} |")
    lines += ["", "## State along the path", "",
              f"- mean absolute level gap at the last day: "
              f"{days[-1]['level_gap_mean_abs']:.4f} (log price)",
              f"- mispricing mean, first and last day: "
              f"{days[0]['mispricing_mean']:+.4f} to "
              f"{days[-1]['mispricing_mean']:+.4f}",
              f"- market variance, first and last day: "
              f"{days[0]['market_variance']} to {days[-1]['market_variance']}",
              f"- universe stress, max: "
              f"{max((d['universe_stress'] or 0) for d in days)}",
              f"- truth rows recorded: {run['truth_rows']}"]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--year", choices=list(realdata.YEARS), required=True)
    p.add_argument("--preset", default=None)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--sigma", type=float, default=1e-3)
    p.add_argument("--days", type=int, default=0, help="limit, for a smoke run")
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)
    run = shadow(args)
    with open(os.path.join(args.out, "shadow.json"), "w", encoding="utf-8") as f:
        json.dump(run, f)
    text = render(run)
    with open(os.path.join(args.out, "shadow.md"), "w", encoding="utf-8") as f:
        f.write(text)
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
