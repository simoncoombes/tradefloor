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
    betas: dict[str, float] = {}
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
        betas[t] = beta
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
    return out, betas


def real_idio_sd(d: dict, betas: dict, first: int, last: int) -> dict:
    """Each name's daily idiosyncratic sd over the year: the sd of its log
    return less beta times the index's, the real twin of what one unit of
    the model's day innovation is asked to produce."""
    idx = np.diff(np.log(np.array(d["index"][first - 1:last], dtype=float)))
    out = {}
    for t in realdata.TICKERS:
        r = np.diff(np.log(np.array(d["closes"][t][first - 1:last], dtype=float)))
        out[t] = float(np.std(r - betas[t] * idx, ddof=1))
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

    def commit(self, x: np.ndarray, jumps: list) -> dict:
        """Install the day's draws on the engine and walk it to the close.

        The jump patches go in before the previous close and the day's
        market patches after it, with the overlay emptied in between at
        the closed boundary: a snapshot with no overlay is restored onto
        the engine, which is the one place a restore is safe (the market
        is closed and the day's accumulators are settled). Without this
        the overlay kept every day's hundred thousand patches since the
        start and every draw paid a lookup over all of them; the run
        slowed from fifty to a hundred and ten seconds a day over a year.

        Returns the checkpoint: the stripped snapshot at the boundary and
        the day's market patches, from which ``resume`` rebuilds this
        exact state on another engine.
        """
        if jumps:
            noise.patch_draws(self.engine, jumps)
        if self.day > 0:
            self.engine.close_market()
        snapshot = self.engine.state_snapshot()
        snapshot["draw_overlay"] = []
        self.engine.restore_state(snapshot)
        market = self.layout.patches(x)
        noise.patch_draws(self.engine, market)
        self.engine.open_market()
        self.engine.run_session(*CLOCK, TICKS)
        self.engine.record(self.day)
        return {"day": self.day, "snapshot": snapshot,
                "patches": [(p.address.stream, p.address.kind,
                             p.address.index, p.value) for p in market]}


def encode(value):
    """A snapshot as JSON can carry it: byte buffers as base64 under one
    key, tuples as lists, everything else as it is."""
    import base64
    if isinstance(value, bytes):
        return {"__bytes__": base64.b64encode(value).decode("ascii")}
    if isinstance(value, dict):
        return {k: encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode(v) for v in value]
    return value


def decode(value):
    """The inverse of ``encode``."""
    import base64
    if isinstance(value, dict):
        if set(value) == {"__bytes__"}:
            return base64.b64decode(value["__bytes__"])
        return {k: decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode(v) for v in value]
    return value


def resume(engine, checkpoint: dict) -> None:
    """Rebuild the state ``commit`` left from its checkpoint.

    The snapshot is the closed boundary before the checkpoint day with
    an empty overlay; the patches are that day's market patches. Restore,
    patch, open, run, record: the engine is then where the run was after
    that day's commit, bit for bit, and the next day solves from it.
    """
    engine.restore_state(decode(checkpoint["snapshot"]))
    noise.patch_draws(engine, [
        noise.Patch(noise.DrawAddress(s, k, i), v)
        for s, k, i, v in checkpoint["patches"]])
    engine.open_market()
    engine.run_session(*CLOCK, TICKS)
    engine.record(checkpoint["day"])


# -- the solver ----------------------------------------------------------

#: Solver settings, one place: the finite-difference step in z units, the
#: number of accepted steps between fresh Jacobians (Broyden in between),
#: the iterations per round and the rounds a solve may restart when the
#: closes are not reached. ``lab()`` measures alternatives on synthetic days.
#: Measured by the lab (run shadowlab1, 2026-09-02, four synthetic days each
#: on six and forty names, sigma 1e-3): step 0.25 with Broyden updates only
#: reached 0 of 4 forty-name days (median residual 1.7e-2); step 0.5 or 1.0
#: with a fresh Jacobian every three steps reached 4 of 4 at the grid floor
#: (median 1.8e-3 and 2.1e-3, max 2.7e-3 and 2.2e-3, 502 and 399 evaluations).
SOLVER = {"step": 1.0, "refresh": 3, "max_iter": 12, "restarts": 2}


def solve(forward: Forward, r_obs: np.ndarray, jumps: list, x0: np.ndarray,
          extra: int = 0, *, sigma: float, step: float | None = None,
          max_iter: int | None = None, tol: float = 1e-4,
          J0: np.ndarray | None = None, restarts: int | None = None,
          refresh: int | None = None) -> dict:
    """Levenberg-Marquardt for the MAP day aggregates.

    Minimises ``|r(x) - r_obs|^2 / (2 sigma^2) + |x|^2 / 2``. ``extra``
    trailing entries of ``x`` are jump normals: they enter the forward map
    through ``jumps`` (a function of ``x[-extra:]``) and carry the same
    prior. ``J0`` is a Jacobian from an earlier solve whose leading columns
    are reused; only the columns it lacks are taken by finite difference,
    which is what keeps a jump candidate at a handful of evaluations
    rather than a whole Jacobian. When the closes are not reached at
    convergence the Jacobian is retaken at the solution and the search
    continues, up to ``restarts`` times, so a Broyden drift is not
    mistaken for a clamp. Returns the solution, the residual, the
    Jacobian and whether the optimiser converged.
    """
    m = len(x0)
    step = SOLVER["step"] if step is None else step
    max_iter = SOLVER["max_iter"] if max_iter is None else max_iter
    restarts = SOLVER["restarts"] if restarts is None else restarts
    refresh = SOLVER["refresh"] if refresh is None else refresh

    def jump_list(x):
        return jumps(x[m - extra:]) if callable(jumps) else jumps

    def resid(x):
        return forward.returns(x, jump_list(x)) - r_obs

    x = x0.copy()
    r = resid(x)

    def jacobian(x, r, keep=None):
        J = np.zeros((len(r), m))
        start = 0
        if keep is not None and keep.shape[0] == len(r) and keep.shape[1] <= m:
            J[:, :keep.shape[1]] = keep
            start = keep.shape[1]
        for k in range(start, m):
            xk = x.copy()
            xk[k] += step
            J[:, k] = (resid(xk) - r) / step
        return J

    J = jacobian(x, r, J0)
    lam = 1e-3
    cost = (r @ r) / (2 * sigma ** 2) + (x @ x) / 2
    converged = False
    rounds = 0
    since_fresh = 0
    for _ in range(max_iter * (1 + restarts)):
        if converged:
            if np.max(np.abs(r)) < 5 * sigma or rounds >= restarts:
                break
            # not reached: a fresh Jacobian at the solution, then on
            rounds += 1
            J = jacobian(x, r)
            since_fresh = 0
            lam = 1e-3
            converged = False
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
                improvement = (cost - cost_new) / max(cost, 1e-12)
                x, r, cost = x_new, r_new, cost_new
                since_fresh += 1
                if refresh and since_fresh >= refresh:
                    # the map is piecewise flat at the cent grid, and a
                    # secant update over a flat step corrupts the column;
                    # a fresh difference every few steps keeps it honest
                    J = jacobian(x, r)
                    since_fresh = 0
                else:
                    J += np.outer(dr - J @ delta, delta) / (delta @ delta)
                lam = max(lam / 3, 1e-6)
                accepted = True
                # converged: a near Gauss-Newton step (small damping) moved
                # the posterior by less than tol, which is the optimiser's
                # claim; a small step taken under heavy damping is not one.
                # Whether the closes were reached is a separate question the
                # residual answers.
                converged = improvement < tol and lam <= 1e-3
                break
            lam *= 10
        if not accepted:
            converged = True
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
                      extra=1, sigma=sigma, J0=base["jacobian"][:, :m0])
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
            trial = solve(forward, r_obs, jumps_i, x0, extra=extra, sigma=sigma,
                          J0=best["jacobian"][:, :len(x0) - 1])
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
    universe, betas = build_universe(d, first)
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
    start_k = 0
    checkpoint = None
    if args.resume:
        with open(args.resume, encoding="utf-8") as f:
            saved = json.load(f)
        if saved["year"] != year or saved["args"]["preset"] != args.preset \
                or saved["args"]["seed"] != args.seed:
            raise SystemExit("the checkpoint is from another year, preset "
                             "or seed; a resume continues one run")
        days = saved["days"]
        checkpoint = saved["checkpoint"]
        resume(engine, checkpoint)
        start_k = checkpoint["day"] + 1
        print(f"  resumed after day {checkpoint['day']} "
              f"({len(days)} days carried)", flush=True)
    for k, session in enumerate(range(first, last)):
        if k < start_k:
            continue
        r_obs = np.log(real[k + 1] / real[k])
        fwd = Forward(engine, k, n)
        result = solve_day(fwd, r_obs, intensities, sigma=args.sigma)
        checkpoint = fwd.commit(result["x"], result["jumps"])
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
            "sensitivity": result["jacobian_idio_norm"],
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
            # A partial record every ten days, so a run a dead-man timer
            # or a spot reclaim ends still leaves what it solved.
            partial = {"args": vars(args), "year": year, "partial": True,
                       "sessions": [first, session + 1], "days": days,
                       "checkpoint": encode(checkpoint),
                       "intensities": intensities, "tickers": tickers,
                       "betas": betas, "truth_rows": 0, "truth": [],
                       "provenance": dict(d["provenance"], **build_provenance(engine)),
                       "real_idio_sd": real_idio_sd(d, betas, first, last),
                       "seconds": time.time() - started}
            with open(os.path.join(args.out, "shadow-partial.json"), "w",
                      encoding="utf-8") as f:
                json.dump(partial, f)
            with open(os.path.join(args.out, "shadow-partial.md"), "w",
                      encoding="utf-8") as f:
                f.write(render(partial))
    truth = []
    try:
        import pyarrow as pa
        truth = pa.table(engine.truth()).to_pylist()
    except ImportError:
        pass
    return {"args": vars(args), "year": year, "sessions": [first, last],
            "provenance": dict(d["provenance"], **build_provenance(engine)),
            "intensities": intensities, "days": days, "truth_rows": len(truth),
            "truth": truth, "seconds": time.time() - started,
            "tickers": tickers, "betas": betas,
            "real_idio_sd": real_idio_sd(d, betas, first, last)}


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
    lines = [f"# Shadow run: {run['year']} ({run['args']['year']})"
             + (" PARTIAL, the run was still going" if run.get("partial") else ""),
             "", "## Where the solve fails", ""]
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
    sens = np.array([d["sensitivity"] for d in days if "sensitivity" in d])
    res = np.array([d["max_abs_residual"] for d in days])
    if len(sens):
        real_sd = np.array([run["real_idio_sd"][t] for t in run["tickers"]])
        med = np.median(sens, axis=0)
        lines += ["", "## How far one unit of innovation moves a close", "",
                  "The Jacobian's column for each name's day innovation, in log "
                  "return per unit z (median over the days), beside the name's "
                  "real daily idiosyncratic sd over the year. A ratio well "
                  "below one says the model needs several units of innovation "
                  "for one ordinary real day, which the prior resists; that is "
                  "where a residual comes from when no clamp binds.", "",
                  f"- median sensitivity across names {np.median(med):.4f}; "
                  f"real idiosyncratic sd, median {np.median(real_sd):.4f}; "
                  f"ratio {np.median(med) / np.median(real_sd):.2f}",
                  f"- max abs residual per day: median {np.median(res):.2e}, "
                  f"90th percentile {np.percentile(res, 90):.2e}, max "
                  f"{res.max():.2e}; days reached within five sigma "
                  f"{sum(1 for d in days if d.get('reached', False))} of {N}",
                  "", "| name | sensitivity | real idio sd | ratio |",
                  "|---|---|---|---|"]
        order = np.argsort(med / real_sd)
        for i in list(order[:5]) + list(order[-3:]):
            t = run["tickers"][i]
            lines.append(f"| {t} | {med[i]:.4f} | {real_sd[i]:.4f} | "
                         f"{med[i] / real_sd[i]:.2f} |")
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


def lab(args) -> str:
    """Solver settings against days whose draws are known.

    Two rosters (six names, and the certified forty), several synthetic
    days each: a random aggregate vector makes the closes, and each
    setting solves for them from zero. What is reported is the max
    absolute residual and the evaluations, per setting, so the defaults
    in ``SOLVER`` are a measured choice rather than a guess. The residual
    floor is the cent grid: a close on a fifty-dollar name moves in steps
    of two basis points, and no setting reaches below that.
    """
    rows = []
    settings = [
        {"step": 0.25, "refresh": 0, "max_iter": 8, "restarts": 1},
        {"step": 0.25, "refresh": 3, "max_iter": 12, "restarts": 2},
        {"step": 0.5, "refresh": 3, "max_iter": 12, "restarts": 2},
        {"step": 1.0, "refresh": 3, "max_iter": 12, "restarts": 2},
        {"step": 0.5, "refresh": 1, "max_iter": 12, "restarts": 2},
        {"step": 0.5, "refresh": 3, "max_iter": 20, "restarts": 3},
    ]
    global TICKS
    for names, seed, ticks in ((6, 3, 40), (40, 111, 390)):
        TICKS = ticks
        universe = tf.Universe.random(names, seed=seed)
        for day_seed in range(args.days or 4):
            engine = tf.Engine(seed=11 + day_seed, universe=universe)
            fwd = Forward(engine, 0, names)
            rng = np.random.default_rng(100 + day_seed)
            x_true = rng.normal(size=fwd.layout.size)
            r_obs = fwd.returns(x_true, [])
            floor = float(np.max(0.01 / prices(engine)))
            for s in settings:
                fwd.evals = 0
                out = solve(fwd, r_obs, [], np.zeros(fwd.layout.size),
                            sigma=args.sigma, **s)
                rows.append({"names": names, "day": day_seed, **s,
                             "residual": float(np.max(np.abs(out["residual"]))),
                             "floor": floor, "evals": fwd.evals,
                             "converged": out["converged"],
                             "reached": bool(np.max(np.abs(out["residual"])) < 5 * args.sigma)})
            print(f"  lab {names} names day {day_seed} done", flush=True)
    lines = ["# Solver lab", "",
             "| names | step | refresh | iters | restarts | days reached | "
             "median residual | max residual | grid floor | mean evals |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for names in (6, 40):
        for s in settings:
            part = [r for r in rows if r["names"] == names
                    and all(r[k] == v for k, v in s.items())]
            if not part:
                continue
            res = np.array([r["residual"] for r in part])
            lines.append(f"| {names} | {s['step']} | {s['refresh']} | "
                         f"{s['max_iter']} | {s['restarts']} | "
                         f"{sum(r['reached'] for r in part)}/{len(part)} | "
                         f"{np.median(res):.2e} | {res.max():.2e} | "
                         f"{np.mean([r['floor'] for r in part]):.1e} | "
                         f"{np.mean([r['evals'] for r in part]):.0f} |")
    text = "\n".join(lines) + "\n"
    with open(os.path.join(args.out, "lab.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=1)
    return text


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--year", choices=list(realdata.YEARS), required=True)
    p.add_argument("--lab", action="store_true",
                   help="run the solver lab on synthetic days instead of the year")
    p.add_argument("--preset", default=None)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--sigma", type=float, default=1e-3)
    p.add_argument("--days", type=int, default=0, help="limit, for a smoke run")
    p.add_argument("--resume", default=None,
                   help="a shadow-partial.json from an earlier run of the "
                        "same year, preset and seed; continues after its "
                        "checkpoint day")
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)
    if args.lab:
        text = lab(args)
        with open(os.path.join(args.out, "shadow.md"), "w", encoding="utf-8") as f:
            f.write(text)
        sys.stdout.write(text)
        return 0
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
