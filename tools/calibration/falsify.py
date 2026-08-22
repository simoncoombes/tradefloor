"""Structural falsification as a first-class output: the emptiness certificate.

CALIBRATION.md §4.4, built. The claim this tool earns or refuses is the
strong negative one: *no parameter vector in the named box reaches the
named targets jointly* — with the residual saying by how far, the box
and budget saying what was searched, and every number re-runnable from
the committed JSON (§7.3: the search is itself a citable run).

The workflow is the design's, verbatim:

1. choose a target subset and a searched parameter set;
2. minimise the band-distance loss over the box with lambda -> 0 (no
   regularisation — best case for the model class);
3. a strictly positive minimum, reproduced on held-out seeds and a
   held-out universe, is the rejection;
4. name the minimal structural change predicted to alter the verdict
   (--flips-verdict), so the certificate is falsifiable rather than
   final.

The optimiser is deliberately boring and fully seeded: Latin-hypercube
screening, a small CMA-ES (the §7.1 recommendation, implemented from
the standard (mu/mu_w, lambda) equations with numpy — rank-based, so
indifferent to the kinks the h-sweep measured), and a deterministic
compass polish. Infeasible candidates are repaired onto the §6.3
constraint set and carry a penalty so the search prefers the interior;
every vector actually evaluated is feasible. The objective is
deterministic given the fixed search-seed list (the CRN consequence:
the randomness is frozen into S), so re-running the tool with the same
arguments reproduces the certificate to the bit.

At the found minimum the tool can re-measure the target rows of the
secant Jacobian (--jacobian-at-best) and report the first-order box
bound: the largest movement each target could achieve within the box if
its response were linear — a local corroboration that the residual is
structural rather than a search failure. §4.5's honesty carries: the
certificate is a numerical minimum over a bounded box under a named
budget, not a proof.

Grid mode (--grid name=v1,v2,...) replaces the search with an explicit
1-D grid — the finding-14 shape — and certifies over the grid instead.

Usage (the leverage certificate):

    .venv/bin/python tools/calibration/falsify.py \
        --base legacy --params searched9 --targets leverage_effect \
        --flips-verdict "a GJR asymmetry term (garch_gamma > 0)" \
        --out results/falsify-leverage-legacy-$(date +%F).json
"""

from __future__ import annotations

import argparse
import math
import statistics
import time

import numpy as np

import instrumentlib as lib

PENALTY_SCALE = 1e3


def parse_seed_list(text: str) -> list[int]:
    return [int(s) for s in text.split(",") if s]


class DevSpace:
    """Parameter vectors as §6.3 deviation coordinates over a box.

    Coordinates are deviations from the BASE vector's values (log units
    for scale parameters, raw units for bounded ones), with the search
    box mapped from `default_box` intersected with feasibility repair.
    """

    def __init__(self, params: list[str], base: dict[str, float],
                 ship: dict[str, float]) -> None:
        self.params = params
        self.base = base
        self.ship = ship
        self.center = np.array([
            base.get(name, ship[name]) for name in params])
        self.kinds = [lib.PARAM_SPECS[name]["kind"] for name in params]
        lo, hi = [], []
        for name, value in zip(params, self.center):
            box_lo, box_hi = lib.default_box(name, ship[name])
            if lib.PARAM_SPECS[name]["kind"] == "log":
                # guard against a base at zero being asked for log devs
                if value <= 0:
                    raise SystemExit(
                        f"{name}: base value {value} is not searchable in "
                        "log space")
                lo.append(math.log(max(box_lo, 1e-300) / value))
                hi.append(math.log(box_hi / value))
            else:
                lo.append(box_lo - value)
                hi.append(box_hi - value)
        self.lo = np.array(lo)
        self.hi = np.array(hi)

    def to_raw(self, u: np.ndarray) -> dict[str, float]:
        out = {}
        for name, kind, center, du in zip(self.params, self.kinds,
                                          self.center, u):
            out[name] = float(center * math.exp(du) if kind == "log"
                              else center + du)
        return out

    def repair(self, u: np.ndarray) -> tuple[np.ndarray, float]:
        """Clip into the box and onto the stationarity set.

        Returns (repaired u, squared repair distance in dev units) —
        the distance is the CMA penalty, so the search prefers the
        interior to leaning on the repair.
        """
        v = np.clip(u, self.lo, self.hi)
        raw = self.to_raw(v)
        full = {**self.base, **raw}

        def get(name: str) -> float:
            return full.get(name, self.ship[name])

        def put(name: str, value: float) -> None:
            if name in raw:
                raw[name] = value
                full[name] = value

        a, b, g = get("garch_alpha"), get("garch_beta"), get("garch_gamma")
        s = a + b + g / 2.0
        if s >= 0.999:
            f = 0.998 / s
            for name, val in (("garch_alpha", a), ("garch_beta", b),
                              ("garch_gamma", g)):
                put(name, val * f)
        ma, mb = get("market_vol_alpha"), get("market_vol_beta")
        s = ma + mb
        if s >= 0.999:
            f = 0.998 / s
            put("market_vol_alpha", ma * f)
            put("market_vol_beta", mb * f)
        for pair in (("garch_ceiling_multiple", "garch_floor_multiple"),
                     ("market_vol_ceiling_multiple",
                      "market_vol_floor_multiple")):
            ceil, floor = get(pair[0]), get(pair[1])
            if ceil <= floor:
                put(pair[1], ceil * 0.5)

        repaired = np.array([
            math.log(raw[name] / center) if kind == "log"
            else raw[name] - center
            for name, kind, center in zip(self.params, self.kinds,
                                          self.center)])
        distance2 = float(np.sum((repaired - u) ** 2))
        bad = lib.feasibility_violation({**self.base, **raw}, self.ship)
        if bad:
            raise AssertionError(f"repair failed to reach feasibility: {bad}")
        return repaired, distance2


def scaled_band_loss(medians: dict[str, float], targets: list[str],
                     constraints: list[str], seed_sd: dict[str, float],
                     bands: dict[str, tuple[float, float]]) -> dict:
    from pretium.facts import band_distance

    rows = {}
    total = 0.0
    for key in targets + constraints:
        low, high = bands[key]
        d = band_distance(medians[key], low, high)
        scaled = d / seed_sd[key]
        rows[key] = {"measured": medians[key], "band": (low, high),
                     "distance": d, "scaled": scaled,
                     "role": "target" if key in targets else "constraint"}
        total += scaled ** 2
    return {"loss": total, "rows": rows}


class Evaluator:
    """Vector -> loss on the fixed search-seed list, memoised, CRN-checked."""

    def __init__(self, base: dict[str, float], targets: list[str],
                 constraints: list[str], seeds: list[int], workers: int,
                 seed_sd: dict[str, float],
                 bands: dict[str, tuple[float, float]],
                 days: int = lib.PANEL_DAYS,
                 universe_n: int = lib.PANEL_UNIVERSE_N,
                 universe_seed: int = lib.PANEL_UNIVERSE_SEED) -> None:
        self.base = base
        self.targets = targets
        self.constraints = constraints
        self.seeds = seeds
        self.workers = workers
        self.seed_sd = seed_sd
        self.bands = bands
        self.days = days
        self.universe_n = universe_n
        self.universe_seed = universe_seed
        self.cache: dict[str, dict] = {}
        self.evaluations = 0
        self.draws_reference: dict[int, int] | None = None
        self.trace: list[dict] = []

    def batch(self, raw_vectors: list[dict[str, float]]) -> list[dict]:
        """Evaluate a batch of override-diffs (relative to base), cached."""
        pending = []
        for raw in raw_vectors:
            key = lib.vector_key({**self.base, **raw})
            if key not in self.cache:
                pending.append(({**self.base, **raw}, key))
        jobs, labels = [], []
        seen = set()
        for overrides, key in pending:
            if key in seen:
                continue
            seen.add(key)
            for seed in self.seeds:
                jobs.append((overrides, seed, self.days, self.universe_n,
                             self.universe_seed))
                labels.append(key)
        if jobs:
            results = lib.run_pool(jobs, self.workers)
            self.evaluations += len(jobs)
            # CRN bookkeeping: the guard is a per-run record here rather
            # than a hard assert — a candidate that moved the draw
            # schedule would invalidate secant comparisons against it,
            # not its own panel levels, and a level is all the
            # certificate uses. None was observed in any committed run.
            for row in results:
                ref = self.draws_reference
                if ref is None:
                    self.draws_reference = {}
                    ref = self.draws_reference
                ref.setdefault(row["seed"], row["draws_consumed"])
            by_key: dict[str, list[dict]] = {}
            for row, key in zip(results, labels):
                by_key.setdefault(key, []).append(row)
            for key, rows in by_key.items():
                medians = {
                    stat: statistics.median(r["panel"][stat] for r in rows)
                    for stat in lib.PANEL_STATS
                }
                crn_ok = all(
                    r["draws_consumed"] == self.draws_reference[r["seed"]]
                    for r in rows)
                self.cache[key] = {
                    "medians": medians,
                    "crn_schedule_matches_base": crn_ok,
                    **scaled_band_loss(medians, self.targets,
                                       self.constraints, self.seed_sd,
                                       self.bands),
                }
        return [self.cache[lib.vector_key({**self.base, **raw})]
                for raw in raw_vectors]


def cma_es(evaluate_batch, space: DevSpace, x0: np.ndarray, sigma0: float,
           popsize: int, generations: int, rng: np.random.Generator,
           trace: list) -> tuple[np.ndarray, float]:
    """A standard (mu/mu_w, lambda) CMA-ES over dev coordinates.

    `evaluate_batch(list of u) -> list of loss` must already include the
    repair penalty. Returns (best u seen, its loss). Implemented from
    the standard equations (Hansen's tutorial defaults) rather than
    imported, so the tools directory keeps numpy as its only dependency;
    the certificate names this implementation and its seed, and the
    run's determinism makes 'the same tool, the same config, the same
    certificate' checkable.
    """
    n = len(x0)
    lam = popsize
    mu = lam // 2
    weights = np.log(mu + 0.5) - np.log(np.arange(1, mu + 1))
    weights /= weights.sum()
    mueff = 1.0 / np.sum(weights ** 2)
    cc = (4 + mueff / n) / (n + 4 + 2 * mueff / n)
    cs = (mueff + 2) / (n + mueff + 5)
    c1 = 2 / ((n + 1.3) ** 2 + mueff)
    cmu = min(1 - c1, 2 * (mueff - 2 + 1 / mueff) / ((n + 2) ** 2 + mueff))
    damps = 1 + 2 * max(0.0, math.sqrt((mueff - 1) / (n + 1)) - 1) + cs
    chi_n = math.sqrt(n) * (1 - 1 / (4 * n) + 1 / (21 * n * n))

    mean = x0.copy()
    sigma = sigma0
    pc = np.zeros(n)
    ps = np.zeros(n)
    cov = np.eye(n)
    best_u, best_loss = x0.copy(), float("inf")

    for gen in range(generations):
        vals, vecs = np.linalg.eigh(cov)
        vals = np.maximum(vals, 1e-20)
        sqrt_c = vecs @ np.diag(np.sqrt(vals)) @ vecs.T
        inv_sqrt_c = vecs @ np.diag(1.0 / np.sqrt(vals)) @ vecs.T
        z = rng.standard_normal((lam, n))
        xs = mean + sigma * z @ sqrt_c.T
        losses = np.array(evaluate_batch([xs[i] for i in range(lam)]))
        order = np.argsort(losses)
        if losses[order[0]] < best_loss:
            best_loss = float(losses[order[0]])
            best_u = xs[order[0]].copy()
        trace.append({"generation": gen, "best_loss": float(losses[order[0]]),
                      "mean_loss": float(losses.mean()),
                      "sigma": float(sigma)})
        selected = xs[order[:mu]]
        new_mean = weights @ selected
        y = (new_mean - mean) / sigma
        ps = (1 - cs) * ps + math.sqrt(cs * (2 - cs) * mueff) * (inv_sqrt_c @ y)
        hsig = (np.linalg.norm(ps)
                / math.sqrt(1 - (1 - cs) ** (2 * (gen + 1))) / chi_n
                < 1.4 + 2 / (n + 1))
        pc = (1 - cc) * pc + hsig * math.sqrt(cc * (2 - cc) * mueff) * y
        artmp = (selected - mean) / sigma
        cov = ((1 - c1 - cmu) * cov
               + c1 * (np.outer(pc, pc)
                       + (0 if hsig else 1) * cc * (2 - cc) * cov)
               + cmu * artmp.T @ np.diag(weights) @ artmp)
        mean = new_mean
        sigma *= math.exp((cs / damps)
                          * (np.linalg.norm(ps) / chi_n - 1))
    return best_u, best_loss


def compass_polish(evaluate_batch, u: np.ndarray, loss: float,
                   space: DevSpace, steps: list[float],
                   trace: list) -> tuple[np.ndarray, float]:
    """Deterministic pattern search: probe ±step along each axis."""
    n = len(u)
    for step in steps:
        improved = True
        while improved:
            improved = False
            probes = []
            for j in range(n):
                for sign in (1.0, -1.0):
                    probe = u.copy()
                    probe[j] += sign * step
                    probes.append(probe)
            losses = evaluate_batch(probes)
            best = int(np.argmin(losses))
            if losses[best] < loss - 1e-12:
                u, loss = probes[best], float(losses[best])
                improved = True
                trace.append({"compass_step": step, "loss": loss})
    return u, loss


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base", default="legacy", choices=["pt-v1", "legacy"])
    parser.add_argument("--params", default="searched9")
    parser.add_argument("--targets", required=True,
                        help="comma list of panel statistics to reach")
    parser.add_argument("--constraints", default="",
                        help="comma list held in-band alongside the targets")
    parser.add_argument("--search-seeds", default="101,102,103")
    parser.add_argument("--verify-seeds", default="1,2,3,4,5,6")
    parser.add_argument("--holdout-universe", default="60:222",
                        help="N:seed for the held-out universe check")
    parser.add_argument("--population", type=int, default=8)
    parser.add_argument("--generations", type=int, default=20)
    parser.add_argument("--cma-seed", type=int, default=7)
    parser.add_argument("--sigma0", type=float, default=0.35)
    parser.add_argument("--screen", type=int, default=32,
                        help="Latin-hypercube screening samples")
    parser.add_argument("--compass-steps", default="0.1,0.05,0.025")
    parser.add_argument("--grid", default=None,
                        help="'name=v1,v2,...' replaces the search entirely")
    parser.add_argument("--jacobian-at-best", action="store_true")
    parser.add_argument("--flips-verdict", default=None,
                        help="the minimal structural change predicted to "
                             "alter the verdict (recorded in the "
                             "certificate)")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    import pretium.facts as facts

    base = dict(lib.LEGACY_OVERRIDES) if args.base == "legacy" else {}
    ship = lib.shipped_values()
    targets = [t for t in args.targets.split(",") if t]
    constraints = [c for c in args.constraints.split(",") if c]
    for key in targets + constraints:
        if key not in facts.REAL_MARKETS:
            raise SystemExit(f"{key} has no real-market band")
    if args.params == "searched9":
        params = list(lib.SEARCHED_9)
    elif args.params == "all":
        params = list(lib.PARAM_SPECS)
    else:
        params = [p for p in args.params.split(",") if p]
    search_seeds = parse_seed_list(args.search_seeds)
    seed_sd = dict(facts.SEED_SD)
    bands = {k: tuple(v) for k, v in facts.REAL_MARKETS.items()}

    evaluator = Evaluator(base, targets, constraints, search_seeds,
                          args.workers, seed_sd, bands)
    wall_started = time.perf_counter()
    trace: list[dict] = []

    if args.grid:
        name, values_text = args.grid.split("=", 1)
        grid_values = [float(v) for v in values_text.split(",")]
        raws = [{name: v} for v in grid_values]
        rows = evaluator.batch(raws)
        best_i = int(np.argmin([r["loss"] for r in rows]))
        best_raw = raws[best_i]
        best_loss = rows[best_i]["loss"]
        search_spec = {"mode": "grid", "parameter": name,
                       "values": grid_values}
        grid_report = [
            {"value": v, "loss": r["loss"], "rows": r["rows"],
             "medians": {k: r["medians"][k] for k in lib.PANEL_STATS}}
            for v, r in zip(grid_values, rows)
        ]
    else:
        space = DevSpace(params, base, ship)
        rng = np.random.default_rng(args.cma_seed)

        def evaluate_batch(us: list[np.ndarray]) -> list[float]:
            repaired, penalties, raws = [], [], []
            for u in us:
                r, dist2 = space.repair(np.asarray(u, dtype=float))
                repaired.append(r)
                penalties.append(PENALTY_SCALE * dist2)
                raws.append(space.to_raw(r))
            rows = evaluator.batch(raws)
            return [row["loss"] + pen for row, pen in zip(rows, penalties)]

        # Latin-hypercube screening over the box, plus the base itself.
        n = len(params)
        lhs = np.empty((args.screen, n))
        for j in range(n):
            perm = rng.permutation(args.screen)
            lhs[:, j] = (space.lo[j]
                         + (perm + rng.random(args.screen)) / args.screen
                         * (space.hi[j] - space.lo[j]))
        screen_points = [np.zeros(n)] + [lhs[i] for i in range(args.screen)]
        screen_losses = evaluate_batch(screen_points)
        best_i = int(np.argmin(screen_losses))
        x0 = screen_points[best_i]
        trace.append({"screening_best_loss": float(screen_losses[best_i]),
                      "screening_base_loss": float(screen_losses[0])})

        best_u, best_loss = cma_es(evaluate_batch, space, x0, args.sigma0,
                                   args.population, args.generations, rng,
                                   trace)
        steps = [float(s) for s in args.compass_steps.split(",") if s]
        best_u, best_loss = compass_polish(evaluate_batch, best_u, best_loss,
                                           space, steps, trace)
        best_u, _ = space.repair(best_u)
        best_raw = space.to_raw(best_u)
        search_spec = {
            "mode": "lhs+cma-es+compass",
            "cma": {"population": args.population,
                    "generations": args.generations,
                    "sigma0": args.sigma0, "seed": args.cma_seed,
                    "implementation": "standard (mu/mu_w, lambda) "
                                      "equations, this file"},
            "screening": args.screen,
            "compass_steps": steps,
            "box": {name: lib.default_box(name, ship[name])
                    for name in params},
        }
        grid_report = None

    search_row = evaluator.batch([best_raw])[0]
    search_wall = time.perf_counter() - wall_started

    # ── Verification on the held-out axes (§8) ───────────────────────────
    verify_seeds = parse_seed_list(args.verify_seeds)
    hu_n, hu_seed = (int(x) for x in args.holdout_universe.split(":"))

    def verify(seeds: list[int], universe_n: int, universe_seed: int) -> dict:
        jobs = [({**base, **best_raw}, seed, lib.PANEL_DAYS, universe_n,
                 universe_seed) for seed in seeds]
        results = lib.run_pool(jobs, args.workers)
        evaluator.evaluations += len(jobs)
        medians = {stat: statistics.median(r["panel"][stat] for r in results)
                   for stat in lib.PANEL_STATS}
        return {"seeds": seeds,
                "universe": f"Universe.random({universe_n}, "
                            f"seed={universe_seed})",
                "medians": medians,
                **scaled_band_loss(medians, targets, constraints, seed_sd,
                                   bands)}

    verification = {
        "published_seeds": verify(verify_seeds, lib.PANEL_UNIVERSE_N,
                                  lib.PANEL_UNIVERSE_SEED),
        "holdout_universe": verify(parse_seed_list(args.search_seeds),
                                   hu_n, hu_seed),
    }

    jac_at_best = None
    if args.jacobian_at_best and not args.grid:
        jac_at_best = {}
        h = 0.05
        for name in params:
            value = {**base, **best_raw}.get(name, ship[name])
            lo, hi, dev = lib.bracket(name, value, h)
            lo_vec = {**best_raw, name: lo}
            hi_vec = {**best_raw, name: hi}
            if lib.feasibility_violation({**base, **hi_vec}, ship):
                hi_vec, dev = dict(best_raw), dev / 2.0
            elif lib.feasibility_violation({**base, **lo_vec}, ship):
                lo_vec, dev = dict(best_raw), dev / 2.0
            lo_row, hi_row = evaluator.batch([lo_vec, hi_vec])
            secants = {t: (hi_row["medians"][t] - lo_row["medians"][t]) / dev
                       for t in targets}
            box_lo, box_hi = lib.default_box(name, ship[name])
            if lib.PARAM_SPECS[name]["kind"] == "log":
                radius = max(abs(math.log(box_hi / value)),
                             abs(math.log(max(box_lo, 1e-300) / value)))
            else:
                radius = max(abs(box_hi - value), abs(box_lo - value))
            jac_at_best[name] = {"secants_per_dev_unit": secants,
                                 "box_radius_dev_units": radius}
        first_order_bound = {
            t: sum(abs(col["secants_per_dev_unit"][t])
                   * col["box_radius_dev_units"]
                   for col in jac_at_best.values())
            for t in targets
        }
    else:
        first_order_bound = None

    wall = time.perf_counter() - wall_started

    residual = search_row["loss"]
    holdout_losses = {axis: v["loss"] for axis, v in verification.items()}
    if residual > 0 and all(l > 0 for l in holdout_losses.values()):
        verdict = ("infeasible within the searched box and budget: the "
                   f"minimum scaled loss found is {residual:.3f} > 0 on the "
                   "search seeds, and remains positive on every held-out "
                   "axis")
    elif residual == 0 and all(l == 0 for l in holdout_losses.values()):
        verdict = ("reachable: a vector with zero band-distance loss was "
                   "found on the search seeds and reproduces zero on every "
                   "held-out axis")
    else:
        verdict = (f"inconclusive: search minimum {residual:.3f} against "
                   f"held-out losses {holdout_losses} — where the search is "
                   "the lower side, treat as overfitting of the search "
                   "seeds, per section 8")

    print(f"\nverdict: {verdict}")
    print(f"best vector (as overrides on {args.base}): {best_raw}")
    for key, row in search_row["rows"].items():
        print(f"  {key:<26} measured {row['measured']:+.4f}  band "
              f"{row['band']}  distance {row['distance']:.4f} "
              f"({row['scaled']:.2f} seed-sds)")
    print(f"evaluations: {evaluator.evaluations} "
          f"({evaluator.evaluations / wall * 3600.0:,.0f}/hour), "
          f"wall {wall:.0f}s")

    lib.write_json(args.out, {
        "provenance": lib.provenance(),
        "claim": {
            "model_class": args.base,
            "base_overrides": base,
            "targets": targets,
            "constraints": constraints,
            "searched_parameters": params,
            "verdict": verdict,
            "min_scaled_loss": residual,
            "flips_verdict": args.flips_verdict,
        },
        "method": {
            "search_seeds": search_seeds,
            "days": lib.PANEL_DAYS,
            "universe": f"Universe.random({lib.PANEL_UNIVERSE_N}, "
                        f"seed={lib.PANEL_UNIVERSE_SEED})",
            "loss": "sum over targets+constraints of (band_distance / "
                    "seed_sd)^2, medians over search seeds, lambda = 0",
            "seed_sd_source": "pretium.facts.SEED_SD (shipped)",
            "search": search_spec,
            "workers": args.workers,
        },
        "best_vector": best_raw,
        "at_best": search_row,
        "verification": verification,
        "jacobian_at_best": jac_at_best,
        "first_order_box_bound": first_order_bound,
        "grid": grid_report,
        "trace": trace,
        "evaluations": evaluator.evaluations,
        "wall_seconds": wall,
        "search_wall_seconds": search_wall,
    })


if __name__ == "__main__":
    main()
