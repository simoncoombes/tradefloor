"""The calibration search: a penalised band-distance fit, as a citable run.

CALIBRATION.md §6 (the loss), §7 (the optimiser), §8 (overfitting control)
and §9 (citability), built on the phase-1 runtime seam and the phase-2
instrument. Where `falsify.py` asks "can this model class reach these
statistics at all", this tool asks the calibration question: **what is the
smallest move from the shipped preset that brings the live targets into
band without pushing a constraint out** — and it answers with a named
candidate preset, a certificate, and the deviation/realism frontier that
prices the answer.

Four things make it a different tool from `falsify.py` rather than a flag
on it, and each is a decision the report has to defend:

**The objective is the shipped one, regularised.** `L_real` comes from
`pretium.loss.band_distance_loss` — the library's own function, with the
library's own `LIVE_TARGETS`/`CONSTRAINTS` membership and the library's
own `SEED_SD` — so the number the search minimises is the number a reader
can recompute from the wheel. On top of it sits §6.3's penalty,
`lambda * sum_j dev_j^2`, with `dev_j` in log units for scale parameters
and raw units for bounded shares and multiples. A falsification wants
`lambda = 0` (best case for the model class); a calibration wants the
penalty load-bearing, because an unregularised fit forks the model
everywhere at once and nobody can say what any move bought.

**The box is §6.3's, not the falsifier's.** `instrumentlib.default_box`
opens a bounded share to its whole natural range, which is right for an
emptiness certificate and wrong here: §6.3 asks for ~[1/4x, 4x] per
parameter "so the model's fixed literals retain their meaning".
`calibration_box` applies that rule to every class. One consequence is
worth naming in advance rather than discovering later: phase 2's
degenerate corner (`garch_alpha` 0.424, twenty-one times the shipped
0.02, lag-5 memory at -0.001) is outside this box by construction. That
is the intended behaviour of a calibration box and NOT the reason the
corner is rejected — the lag-5 band prices it now, and
`--degeneracy-probe` re-measures it here so the certificate carries the
check rather than citing one.

**The seed protocol is two-tier.** §8 fixes the training set at seeds
101-130. Ranking a thousand candidates on thirty seeds each is thirty
thousand panel runs; ranking them on a systematic ten-seed subset and
confirming on all thirty is a fifth of that for the same answer, because
common random numbers make small-seed *comparisons* meaningful in a way
they are not in a noisy simulator. The subset's offsets against the
thirty-seed baseline are measured before the search and recorded in the
certificate, so the reader can see what the shortcut cost.

**The frontier is free.** Every evaluated vector carries its `L_real` and
its squared deviation, so the (deviation, realism) Pareto frontier over
the whole run is computable from the cache without one extra panel. §6.3
calls that frontier the deliverable; here it costs nothing, and the
operating point is chosen on it in the open.

Usage:

    .venv/bin/python tools/calibration/calibrate.py \
        --params spectrum8 --lambda 10 \
        --out results/calibrate-pt-v2-$(date +%F).json
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time

import numpy as np

import instrumentlib as lib
from falsify import cma_es, compass_polish

#: The searched set, taken from phase 2's MEASURED identifiability
#: spectrum (CALIBRATION-RESULTS.md §5) rather than from §3.9's
#: argued-parameter-by-parameter list, which that measurement corrected in
#: both directions. Column norms are in seed-sds per deviation unit at
#: pt-v1, from `results/identifiability-pt-v1-2026-08-22.json`.
#:
#: In, because they are the strongly identified directions:
#:   garch_alpha 31.6, market_vol_alpha 29.0, garch_beta 23.3,
#:   market_vol_beta 22.1, market_vol_vix_coupling 20.7,
#:   momentum_theta 18.3, garch_gamma 11.2, idio_sigma_scale 10.2.
#:
#: Out, and each for a stated reason:
#:   price_breaker_fraction (8.3) and mispricing_cap (4.6) are GUARDS —
#:     §5.2's warning, confirmed with a rate by the SVD: they are visible,
#:     so a loss allowed to touch them will spend them, buying kurtosis by
#:     widening a circuit breaker rather than by making the market
#:     heavier-tailed. Excluded because they are hazards, not because they
#:     are inert.
#:   market_factor_sigma (4.1), market_vol_vix_anchor (3.8),
#:     garch_floor_multiple (2.5), crowd_momentum_gain (2.0),
#:     garch_omega (1.6) are weak-but-real. With the panel constraining
#:     about five effective directions, adding a sixth-to-tenth weak
#:     column buys resolution the data does not have; finding 14 also put
#:     market_factor_sigma's trade on record (correlation bought with
#:     kurtosis at a fixed rate) and correlation is in band with room on
#:     both sides, so there is nothing for it to buy.
#:   garch_ceiling_multiple (0.41) and sector_factor_sigma (0.28) are the
#:     two §3.9 listed as searchable and the SVD found effectively
#:     unidentified at pt-v1 — the last singular direction of the
#:     searched-9 submatrix, 0.079, is these two against each other.
#:   the nine exactly-zero columns (informed_flow_fraction,
#:     crowd_lean_cap, news_market_weight, news_sector_weight,
#:     order_flow_coefficient, price_hard_cap, market_vol_floor_multiple,
#:     crisis_blend_ramp, crisis_blend_cap) are invisible to this panel as
#:     a theorem about the run, not an estimate: bit-identical panels
#:     across all thirty seeds under perturbation.
SPECTRUM_8 = (
    "garch_alpha",
    "market_vol_alpha",
    "garch_beta",
    "market_vol_beta",
    "market_vol_vix_coupling",
    "momentum_theta",
    "garch_gamma",
    "idio_sigma_scale",
)

#: The measured column norms, carried so the certificate states the
#: spectrum it took its search space from rather than citing it.
COLUMN_NORMS = {
    "garch_alpha": 31.6, "market_vol_alpha": 29.0, "garch_beta": 23.3,
    "market_vol_beta": 22.1, "market_vol_vix_coupling": 20.7,
    "momentum_theta": 18.3, "garch_gamma": 11.2, "idio_sigma_scale": 10.2,
    "price_breaker_fraction": 8.3, "mispricing_cap": 4.6,
    "market_factor_sigma": 4.1, "market_vol_vix_anchor": 3.8,
    "garch_floor_multiple": 2.5, "crowd_momentum_gain": 2.0,
    "garch_omega": 1.6, "garch_ceiling_multiple": 0.41,
    "sector_factor_sigma": 0.28,
}

#: Every third training seed. A systematic sample of §8's training set,
#: not a hand-picked one; its per-statistic offsets against the full
#: thirty are measured before the search runs and recorded.
SEARCH_SEEDS = tuple(range(101, 131, 3))

PENALTY_SCALE = 1e3

#: How far inside each band the SEARCH aims, in units of that statistic's
#: own seed sd. `0.0` reproduces phase 3 exactly.
#:
#: Why a margin at all, and why it belongs in the search rather than in
#: the loss. `pretium.loss.band_distance_loss` is a published surface
#: whose meaning is "distance outside the band", and it is flat inside the
#: band on purpose — a statistic that is in band contributes nothing, and
#: there is no gradient rewarding one that is comfortably in over one that
#: is barely in. §6.3's regulariser then pulls every parameter back toward
#: the shipped preset until the pull stops paying, and the pull stops
#: paying exactly when the target it was fighting reaches its band EDGE.
#: The composition of the two therefore parks every trained-to statistic
#: on the least robust point of the feasible set. Phase 3 measured that
#: happening: its optimum put `return_acf1` 0.0002 inside a band top, and
#: the statistic fell out of band on all three validation axes.
#:
#: The fix is to move the target the search aims at, not the ruler the
#: verdict is read against. `MARGIN_SD` shrinks each band by k · s_k on
#: each side for SCORING CANDIDATES ONLY. Every reported number — every
#: `loss_real`, every panel row, every band verdict, §8's overfitting
#: test — is computed by the shipped function against the true bands, and
#: the certificate carries both under names that cannot be confused.
DEFAULT_MARGIN_SD = 0.5


class BudgetExhausted(Exception):
    """Raised when the panel-run budget is spent; stages stop, cleanly."""


def margined_bands(bands: dict, seed_sd: dict, keys: list[str],
                   k: float, per_key: dict[str, float] | None = None) -> dict:
    """Each band shrunk by `k` seed-sds on each side, for the search only.

    `per_key` overrides `k` for named statistics, because one margin does
    not fit every band. The case it was added for: `abs_return_acf20`'s
    band spans 2.57 seed-sds, and at the global 0.5 the searched interval
    is (-0.017, +0.057). Real markets read +0.020 there and this model
    reads -0.004 -- NO CLUSTERING LEFT AT ALL -- and both sit inside, so
    the loss is identical for a market with the right tail and one with
    none. A search cannot chase what the objective cannot see, and the
    pt-v4 run proved it: given a two-component variance and a reason to
    use it, it set the slow weight to 0.035 and left it there.

    At 1.0 the searched interval becomes (+0.007, +0.033): the model's
    no-tail value falls outside it and the real-market median falls
    inside. That is the gradient the statistic was promoted to provide
    and did not.

    A statistic whose band is narrower than 2·k·s_k cannot be given k of
    room on both sides. Rather than emit an empty interval — which would
    make the target unreachable and hand the optimiser an arbitrary
    direction — the shrink degenerates to that band's MIDPOINT, which is
    the most robust point the band contains and is what a margin is
    trying to reach in the first place. The certificate records, per
    statistic, the room the shrink actually bought and whether the
    degenerate case fired, because "the band was too narrow for the
    margin asked for" is a fact about the panel that a reader should not
    have to derive.
    """
    out = {}
    per_key = per_key or {}
    for key in keys:
        lo, hi = bands[key]
        s = seed_sd[key]
        k = per_key.get(key, k)
        room = k * s
        if hi - lo <= 2.0 * room:
            mid = 0.5 * (lo + hi)
            out[key] = {"band": (mid, mid), "degenerate": True,
                        "true_band": (lo, hi), "seed_sd": s,
                        "requested_sd": k,
                        "band_width_sd": (hi - lo) / s,
                        "achieved_sd": 0.5 * (hi - lo) / s}
        else:
            out[key] = {"band": (lo + room, hi - room), "degenerate": False,
                        "true_band": (lo, hi), "seed_sd": s,
                        "requested_sd": k,
                        "band_width_sd": (hi - lo) / s,
                        "achieved_sd": k}
    return out


def search_loss(panels: list[dict], margin: dict,
                room_target: float = 0.0, room_weight: float = 0.0) -> float:
    """`L_real`'s arithmetic on the shrunk bands: the SEARCH objective.

    Deliberately not a call into `pretium.loss.band_distance_loss` with
    different bands, because that function does not take bands and must
    not learn to: its published meaning is distance outside the REAL
    band, and a reader who sees its name must never have to ask which
    ruler produced the number. This is the same formula — median across
    seeds, two-sided distance through the library's own `band_distance`,
    scaled by the same `SEED_SD`, squared and summed — evaluated against
    the margined interval, and it is named for what it is.

    With `k = 0` it is `band_distance_loss(panels)["loss"]` to the bit,
    which the certificate asserts rather than assumes.
    """
    from pretium.facts import band_distance

    total = 0.0
    for key, spec in margin.items():
        lo, hi = spec["band"]
        values = [p[key] for p in panels if p.get(key) is not None]
        median = statistics.median(values)
        total += (band_distance(median, lo, hi) / spec["seed_sd"]) ** 2
        if room_weight > 0.0:
            # ROOM: how far inside its band a statistic sits, in its own
            # seed noise.
            #
            # The band loss is FLAT inside the band by design -- there is no
            # credit for being deeper in. That is right for a published
            # verdict and wrong for SELECTION, because it cannot rank
            # candidates that are all inside, and "cannot rank" means the
            # winner among them is chosen arbitrarily.
            #
            # It also has a measured consequence. On the shipped preset,
            # seven of the ten statistics have their p10-p90 range across
            # seeds crossing a band edge: the medians are in band and a
            # large minority of individual seeds are not. A statistic
            # sitting half a seed-sd inside its band is not safely in band,
            # it is in band on average. This term prices that.
            #
            # One-sided and bounded: a statistic with at least `room_target`
            # of room contributes NOTHING, so there is no reward for
            # burrowing toward a band's centre and no fight with the primary
            # term. Only edge-hugging is penalised.
            room = min(median - lo, hi - median) / spec["seed_sd"]
            if room < room_target:
                total += room_weight * (room_target - room) ** 2
    return total


def persistence_cap(half_life_days: float) -> float:
    """The AR(1) persistence whose half-life is `half_life_days`.

    The market factor's variance persistence is `market_vol_alpha +
    market_vol_beta`, and phase 3's search drove it to 0.9964 — a 192-day
    half-life measured through a 252-day window. A memory that long is
    not identified by that window: the panel cannot separate it from a
    random walk, so the loss is compatible with the value rather than
    evidence for it. Capping the half-life at a quarter of the window
    means the window spans four half-lives, so any excursion decays to a
    sixteenth inside it and the statistics are reading the process's RATE
    of mean reversion rather than only its level.
    """
    return 0.5 ** (1.0 / half_life_days)


def calibration_box(name: str, ship: float) -> tuple[float, float]:
    """§6.3's box: ~[1/4x, 4x] of the shipped value, in raw units.

    Intersected with the parameter's hard range where it has one. This is
    deliberately tighter than `instrumentlib.default_box`, which opens a
    bounded share to (0, 0.999) because an emptiness certificate has to
    search the whole model class. A calibration is asking a different
    question — how far must this constant move — and a box that lets a
    constant move twenty-fold is not asking it.
    """
    spec = lib.PARAM_SPECS[name]
    hard = spec.get("hard_range", (0.0, 0.999))
    if ship == 0.0:
        # A multiplicative box around zero is the single point zero, which
        # would silently pin the parameter rather than search it. Every
        # parameter this applies to was introduced OFF by construction --
        # the pt-v4 slow-variance trio -- so "~[1/4x, 4x] of the shipped
        # value" has no meaning for them and the declared hard range is the
        # honest box. The §6.3 penalty still prices any move away from
        # zero, so the regulariser, not the box, is what keeps the move
        # small.
        return hard
    lo, hi = ship / 4.0, ship * 4.0
    if spec["kind"] == "abs":
        lo, hi = max(lo, hard[0]), min(hi, hard[1])
    return lo, hi


def deviation(name: str, value: float, ship: float) -> float:
    """§6.3's deviation: log for scale parameters, raw for bounded ones."""
    if lib.PARAM_SPECS[name]["kind"] == "log":
        return math.log(value / ship)
    return value - ship


class DevSpace:
    """Search coordinates: §6.3 deviations from pt-v1, box-normalised.

    The same shape as `falsify.DevSpace` and deliberately a separate class
    rather than a flag on it: that one is a committed part of three
    published certificates, its box rule is the one an emptiness claim
    needs, and re-pointing it at a different box to save thirty lines
    would silently re-scope certificates already in the record.

    One change beyond the box, and it is not cosmetic. §6.3's deviation
    units are comparable *as a penalty* — that is what the log/raw split
    buys — but they are not comparable as a SEARCH geometry: at pt-v1 the
    box is ±0.06 deviation units wide for `garch_alpha` and ±1.4 for
    `idio_sigma_scale`, twenty-three fold. A CMA-ES with one scalar step
    size and an identity initial covariance over those coordinates spends
    its whole budget clipped against the narrow faces of the box. So the
    search coordinate is the deviation divided by the half-width of that
    parameter's own box: x = 0 is still pt-v1 exactly, one unit of x is
    the same fraction of the searchable range in every direction, and the
    penalty and the reported deviations are computed from the raw values
    as §6.3 defines them, untouched by the rescaling.
    """

    def __init__(self, params: list[str], ship: dict[str, float],
                 factor_persistence_cap: float | None = None,
                 fixed: dict[str, float] | None = None) -> None:
        self.params = params
        self.ship = ship
        #: Parameters set by construction: applied to every vector, never
        #: searched, never penalised. See `to_raw`.
        self.fixed = dict(fixed or {})
        #: An identifiability bound on `market_vol_alpha +
        #: market_vol_beta`, or None for §6.3's stationarity bound alone.
        #: It lives here rather than in `instrumentlib.feasibility_
        #: violation` on purpose: that function states the constraints
        #: the LIBRARY's stationarity analytics cover, three committed
        #: falsification certificates were measured against it, and an
        #: identifiability cap is a property of what this panel can
        #: resolve rather than of what the model can run.
        self.factor_persistence_cap = factor_persistence_cap
        self.center = np.array([ship[name] for name in params])
        self.kinds = [lib.PARAM_SPECS[name]["kind"] for name in params]
        self.box = {name: calibration_box(name, ship[name]) for name in params}
        lo, hi = [], []
        for name, value in zip(params, self.center):
            box_lo, box_hi = self.box[name]
            if lib.PARAM_SPECS[name]["kind"] == "log":
                lo.append(math.log(box_lo / value))
                hi.append(math.log(box_hi / value))
            else:
                lo.append(box_lo - value)
                hi.append(box_hi - value)
        self.dev_lo = np.array(lo)
        self.dev_hi = np.array(hi)
        #: half-width of each parameter's box, in its own deviation units
        self.scale = (self.dev_hi - self.dev_lo) / 2.0
        self.lo = self.dev_lo / self.scale
        self.hi = self.dev_hi / self.scale

    def to_raw(self, u: np.ndarray) -> dict[str, float]:
        out = {}
        for name, kind, center, scale, x in zip(self.params, self.kinds,
                                                self.center, self.scale, u):
            du = float(x) * scale
            out[name] = float(center * math.exp(du) if kind == "log"
                              else center + du)
        # Fixed overrides ride on every vector the search prices. They are
        # NOT searched and NOT penalised: a parameter set by construction
        # is a decision taken outside the objective, usually because the
        # objective cannot see what it buys. `volume_variance_gain` is the
        # case this was added for -- it lands `volume_change_acf1` in band,
        # and `volume_change_acf1` is STRUCTURAL, so the loss is blind to
        # it by design and a search would leave the parameter at zero.
        out.update(self.fixed)
        return out

    def from_raw(self, raw: dict[str, float]) -> np.ndarray:
        """The inverse of `to_raw`: raw values -> search coordinates.

        What a warm start needs. A parameter the mapping does not carry
        sits at its shipped value, which is x = 0 — the same convention
        `squared_deviation` uses for a missing key.
        """
        return np.array([
            deviation(name, raw.get(name, self.ship[name]), self.ship[name])
            / scale
            for name, scale in zip(self.params, self.scale)])

    def repair(self, u: np.ndarray) -> tuple[np.ndarray, float]:
        """Clip into the box and onto §6.3's stationarity set.

        Returns (repaired coordinates, squared repair distance). The
        distance is the search penalty, so leaning on the repair costs
        something and the interior is preferred.
        """
        v = np.clip(u, self.lo, self.hi)
        raw = self.to_raw(v)

        def get(name: str) -> float:
            return raw.get(name, self.ship[name])

        def put(name: str, value: float) -> None:
            if name in raw:
                raw[name] = value

        a, b, g = get("garch_alpha"), get("garch_beta"), get("garch_gamma")
        s = a + b + g / 2.0
        if s >= 0.999:
            f = 0.998 / s
            put("garch_alpha", a * f)
            put("garch_beta", b * f)
            put("garch_gamma", g * f)
        ma, mb = get("market_vol_alpha"), get("market_vol_beta")
        # The stationarity bound is STRICT — the library's analytics cover
        # alpha + beta < 1 — so it repairs to 0.998, just inside. The
        # identifiability cap is INCLUSIVE: a half-life of exactly the
        # cap is the longest memory the panel can resolve and is a legal
        # place to sit, so it repairs to the cap itself. Either way alpha
        # and beta scale together, which preserves the variance process's
        # SHAPE — the split between innovation and carry — and moves only
        # its memory, so the repair changes the quantity the bound is
        # about and nothing else.
        if self.factor_persistence_cap is not None:
            if ma + mb > self.factor_persistence_cap:
                f = self.factor_persistence_cap / (ma + mb)
                put("market_vol_alpha", ma * f)
                put("market_vol_beta", mb * f)
        elif ma + mb >= 0.999:
            f = 0.998 / (ma + mb)
            put("market_vol_alpha", ma * f)
            put("market_vol_beta", mb * f)
        for ceiling, floor in (("garch_ceiling_multiple",
                                "garch_floor_multiple"),
                               ("market_vol_ceiling_multiple",
                                "market_vol_floor_multiple")):
            if get(ceiling) <= get(floor):
                put(floor, get(ceiling) * 0.5)

        repaired = np.array([
            (math.log(raw[name] / center) if kind == "log"
             else raw[name] - center) / scale
            for name, kind, center, scale in zip(self.params, self.kinds,
                                                 self.center, self.scale)])
        bad = lib.feasibility_violation(raw, self.ship)
        if bad:
            raise AssertionError(f"repair failed to reach feasibility: {bad}")
        if self.factor_persistence_cap is not None:
            total = get("market_vol_alpha") + get("market_vol_beta")
            if total > self.factor_persistence_cap + 1e-12:
                raise AssertionError(
                    f"repair failed to reach the identifiability cap: "
                    f"factor variance persistence {total:.6f} > "
                    f"{self.factor_persistence_cap:.6f}")
        return repaired, float(np.sum((repaired - u) ** 2))

    def squared_deviation(self, raw: dict[str, float]) -> float:
        """§6.3's penalty term for a vector expressed as overrides on pt-v1.

        A missing key means "not overridden", which is deviation zero —
        and it is not a hypothetical: the baseline vector is the empty
        dict, it sits in the same cache as every candidate, and it is
        the leftmost point of the frontier this function computes.
        """
        return sum(deviation(name, raw.get(name, self.ship[name]),
                             self.ship[name]) ** 2
                   for name in self.params)


class Evaluator:
    """Candidate -> penalised loss on a named seed set, memoised and capped.

    The cache is keyed by (vector, seed set), so the ten-seed ranking pass
    and the thirty-seed confirmation pass are different objectives with
    different caches and neither can silently answer for the other. The
    budget counts PANEL RUNS — one (vector, seed) pair — because that is
    what costs wall clock; §7.2's unit is a six-seed vector, so the cap is
    expressed in both and the certificate reports both.
    """

    def __init__(self, workers: int, budget_panel_runs: int,
                 margin: dict,
                 days: int = lib.PANEL_DAYS,
                 days_far: int | None = None,
                 margin_far: dict | None = None,
                 room_target: float = 0.0,
                 room_weight: float = 0.0,
                 universe_n: int = lib.PANEL_UNIVERSE_N,
                 universe_seed: int = lib.PANEL_UNIVERSE_SEED) -> None:
        self.workers = workers
        #: `margined_bands(...)`: what the SEARCH scores against. Never
        #: what anything reports against.
        self.margin = margin
        self.hard_budget = budget_panel_runs
        self.budget = budget_panel_runs
        self.reserved = 0
        self.days = days
        #: The SECOND horizon, or None for the single-horizon objective.
        #: Three consecutive searches bought 252-day realism by spending
        #: 504-day realism, because the objective read one horizon and the
        #: validation read the other, so the trade was free to the optimiser
        #: and only visible afterwards. When this is set, every candidate is
        #: priced at BOTH horizons and the search loss is their sum.
        self.days_far = days_far
        self.margin_far = margin_far
        #: The edge-proximity tie-breaker. Zero weight reproduces the
        #: previous objective exactly, so this changes nothing unasked.
        self.room_target = room_target
        self.room_weight = room_weight
        self.universe_n = universe_n
        self.universe_seed = universe_seed
        self.cache: dict[tuple[str, str], dict] = {}
        self.panel_runs = 0
        self.vector_evaluations = 0
        self.draws_reference: dict[int, int] = {}
        self.economy_reference: dict[int, int] = {}
        self.crn_deviations: list[dict] = []
        self.history: list[dict] = []

    def remaining(self) -> int:
        return self.budget - self.panel_runs

    def reserve(self, runs: int) -> None:
        """Hold `runs` back from the stages that come before the reserve.

        The exploratory stages are the greedy ones — a compass polish runs
        each step until no probe improves, and it has no idea a
        confirmation pass on thirty seeds is queued behind it. Without a
        reserve the last stages can be starved by the first ones, which is
        the worst possible way to spend a budget: everything measured on
        the cheap proxy and nothing on the objective the verdict is read
        on. `release()` hands the reserve back when they start.
        """
        self.reserved = runs
        self.budget = self.hard_budget - runs

    def release(self) -> None:
        self.reserved = 0
        self.budget = self.hard_budget

    def progress(self, stage: str, extra: str = "") -> str:
        return (f"[{self.panel_runs:>5}/{self.budget} runs, "
                f"{self.vector_evaluations:>4} vectors] {stage}{extra}")

    def batch(self, raws: list[dict[str, float]], seeds: tuple[int, ...],
              stage: str) -> list[dict]:
        tag = ",".join(str(s) for s in seeds)
        pending, seen = [], set()
        for raw in raws:
            key = (lib.vector_key(raw), tag)
            if key not in self.cache and key not in seen:
                seen.add(key)
                pending.append((raw, key))
        horizons = [self.days] if self.days_far is None else [self.days, self.days_far]
        if pending:
            need = len(pending) * len(seeds) * len(horizons)
            if self.panel_runs + need > self.budget:
                affordable = max(0, (self.remaining()) // len(seeds))
                if affordable == 0:
                    raise BudgetExhausted(
                        f"{self.panel_runs} panel runs spent of "
                        f"{self.budget}; {len(pending)} candidates unpriced")
                pending = pending[:affordable]
            jobs, labels = [], []
            for raw, key in pending:
                for days in horizons:
                    for seed in seeds:
                        jobs.append((raw, seed, days, self.universe_n,
                                     self.universe_seed))
                        labels.append((key, days))
            results = lib.run_pool(jobs, self.workers)
            self.panel_runs += len(jobs)
            self.vector_evaluations += len(pending)
            by_key: dict[tuple, list[dict]] = {}
            for row, key in zip(results, labels):
                by_key.setdefault(key, []).append(row)
                # The CRN reference is per (seed, HORIZON): a 504-day run
                # draws more than a 252-day one by construction, so a
                # reference keyed on the seed alone would fire on the first
                # dual-horizon batch and call a longer window a violation.
                crn_key = key[1]
                # The CRN guard, on the stream it rests on. Phase 3
                # recorded 16 vectors moving `draws_consumed` and called
                # it a §5.2 violation; `draw_schedule.py` traced all 58
                # (vector, seed) pairs and found the MARKET stream
                # invariant on every one, with the economy stream —
                # whose count varies with macro state by design, and
                # whose macro state every parameter moves through
                # realised volatility — accounting for the whole
                # deviation. So the market count is asserted and the
                # economy count is recorded: one is the property that
                # makes a panel difference a parameter effect, the other
                # is a real branch in the macro chain that a reader
                # should be able to see.
                streams = row["draws_by_stream"]
                ref = (row["seed"], crn_key)
                self.draws_reference.setdefault(ref, streams[lib.CRN_STREAM])
                if streams[lib.CRN_STREAM] != self.draws_reference[ref]:
                    raise AssertionError(
                        f"seed {row['seed']} at {crn_key}d: the "
                        f"{lib.CRN_STREAM} stream "
                        f"moved from {self.draws_reference[ref]} to "
                        f"{streams[lib.CRN_STREAM]} under "
                        f"{row['overrides']} — common random numbers no "
                        "longer hold and every secant here is noise")
                self.economy_reference.setdefault(ref, streams["economy"])
                if streams["economy"] != self.economy_reference[ref]:
                    self.crn_deviations.append(
                        {"stage": stage, "seed": row["seed"],
                         "stream": "economy",
                         "expected": self.economy_reference[ref],
                         "observed": streams["economy"],
                         "overrides": row["overrides"]})
            grouped: dict[tuple[str, str], dict[int, list[dict]]] = {}
            for (key, days), rows in by_key.items():
                grouped.setdefault(key, {})[days] = rows
            for key, by_days in grouped.items():
                near = [r["panel"] for r in by_days[self.days]]
                far = ([r["panel"] for r in by_days[self.days_far]]
                       if self.days_far is not None else None)
                overrides = by_days[self.days][0]["overrides"]
                self.cache[key] = self._score(near, overrides, seeds, stage,
                                              far_panels=far)
        out = []
        for raw in raws:
            key = (lib.vector_key(raw), tag)
            if key not in self.cache:
                raise BudgetExhausted("budget spent mid-batch")
            out.append(self.cache[key])
        return out

    def _score(self, panels: list[dict], overrides: dict[str, float],
               seeds: tuple[int, ...], stage: str,
               far_panels: list[dict] | None = None) -> dict:
        from pretium.loss import band_distance_loss

        # Two numbers, two rulers, and the names say which is which.
        # `loss_real` is the shipped function against the TRUE bands: the
        # published meaning, what every verdict in the certificate is read
        # from, and what §8's overfitting test uses. `loss_search` is the
        # same arithmetic against the margined bands and exists only to
        # rank candidates. At margin 0 they are equal, which the caller
        # asserts once at the baseline rather than trusting.
        breakdown = band_distance_loss(panels)
        medians = {key: statistics.median(p[key] for p in panels)
                   for key in panels[0]}
        margined = search_loss(panels, self.margin,
                               self.room_target, self.room_weight)
        far_margined = None
        if far_panels is not None and self.margin_far is not None:
            # The second horizon, scored against ITS OWN margined bands and
            # its own noise scale. Summed unweighted: there is no principled
            # exchange rate between a band exit at one horizon and the other,
            # and an unstated weighting buried in a scalar would be worse
            # than an equal one stated out loud.
            far_margined = search_loss(far_panels, self.margin_far,
                                       self.room_target, self.room_weight)
            margined = margined + far_margined
        row = {
            "overrides": overrides,
            "loss_real": breakdown["loss"],
            "loss_search": margined,
            "loss_search_far": far_margined,
            "far_panels": far_panels,
            "statistics": {k: {kk: vv for kk, vv in v.items()
                               if kk != "band"}
                           for k, v in breakdown["statistics"].items()},
            "medians": medians,
            "panels": panels,
            "seeds": list(seeds),
            "stage": stage,
        }
        self.history.append({"stage": stage, "seeds": len(seeds),
                             "overrides": overrides,
                             "loss_real": breakdown["loss"],
                             "loss_search": margined})
        return row


def penalised(row: dict, space: DevSpace, lam: float,
              key: str = "loss_search") -> float:
    """§6.3's penalised objective on one of the two rulers.

    `key="loss_search"` is what the optimiser minimises — the margined
    bands, so the regulariser's pull stops paying k seed-sds inside each
    edge instead of exactly on it. `key="loss_real"` is what the frontier
    and every reported figure price, because §6.3's deliverable is
    deviation against REALISM and realism means the published bands.
    """
    return row[key] + lam * space.squared_deviation(row["overrides"])


def pareto_frontier(rows: list[dict], space: DevSpace) -> list[dict]:
    """The (deviation, realism) frontier over every vector ever evaluated.

    §6.3 calls this the deliverable: "deviation from the reference against
    realism achieved, with the parameter movements listed at each point".
    It costs nothing here — both coordinates are already on every cached
    row — and the operating point is then chosen on a curve rather than
    asserted.
    """
    points = sorted(
        ({"squared_deviation": space.squared_deviation(r["overrides"]),
          "loss_real": r["loss_real"], "loss_search": r["loss_search"],
          "overrides": r["overrides"]}
         for r in rows),
        key=lambda p: (p["squared_deviation"], p["loss_real"]))
    out: list[dict] = []
    best = float("inf")
    for point in points:
        if point["loss_real"] < best - 1e-12:
            best = point["loss_real"]
            out.append(point)
    return out


def panel_rows(row: dict) -> list[str]:
    lines = []
    for key, stat in row["statistics"].items():
        scaled = stat["scaled"]
        lines.append(
            f"  {key:<24} {stat['measured']:+10.4f}  {stat['role']:<12}"
            f" d={stat['distance']:.4f}"
            + (f"  {scaled:.3f} sd" if scaled is not None else ""))
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--params", default="spectrum8")
    parser.add_argument("--lambda", dest="lam", type=float, default=10.0)
    parser.add_argument("--lambda-frontier", default="0,1,3,10,30,100")
    parser.add_argument("--search-seeds", default=None)
    parser.add_argument("--train-seeds", default=None)
    parser.add_argument("--holdout-seeds", default="1,2,3,4,5,6")
    parser.add_argument("--holdout-universe", default="60:222")
    parser.add_argument("--holdout-days", type=int, default=504)
    # Defaults to the training seeds so the horizon axis varies the horizon
    # and nothing else. The old three-seed default confounded the two: on
    # `abs_return_acf5` the 504-day excess is +0.0435 from the horizon and
    # +0.1287 from the choice of those three seeds, so the axis reported a
    # seed effect three times larger than the one it is named for.
    parser.add_argument("--holdout-days-seeds",
                        default=",".join(str(s) for s in lib.TRAIN_SEEDS))
    parser.add_argument("--screen", type=int, default=48)
    parser.add_argument("--population", type=int, default=12)
    parser.add_argument("--generations", type=int, default=26)
    parser.add_argument("--cma-seed", type=int, default=20260822)
    parser.add_argument("--sigma0", type=float, default=0.35,
                        help="in box-half-width units (see DevSpace)")
    parser.add_argument("--compass-steps", default="0.2,0.1,0.05")
    parser.add_argument("--confirm-steps", default="0.05,0.025")
    parser.add_argument("--shrink-lambda", dest="shrink_lam", type=float,
                        default=100.0,
                        help="the penalty for the shrink stage — large "
                             "enough that a parameter keeps a move only "
                             "if the statistics paid for it")
    parser.add_argument("--shrink-steps", default="0.1,0.05,0.025")
    parser.add_argument("--budget-panel-runs", type=int, default=6600,
                        help="hard cap on SEARCH; 6600 = 1100 six-seed "
                             "vectors in §7.2's unit, leaving the "
                             "validation axes inside a 1200 total")
    parser.add_argument("--reserve-panel-runs", type=int, default=2600,
                        help="held back from the exploratory stages for "
                             "the thirty-seed confirmation and shrink")
    parser.add_argument("--margin-sd", type=float, default=DEFAULT_MARGIN_SD,
                        help="how far inside each band the SEARCH aims, in "
                             "seed-sds; reporting always uses the true "
                             "bands. 0 reproduces phase 3.")
    parser.add_argument("--margin-sd-for", action="append", default=[],
                        metavar="NAME:SD",
                        help="override --margin-sd for one statistic. One "
                             "margin does not fit every band: a wide band "
                             "at a small margin cannot distinguish the "
                             "model from reality. Repeatable.")
    parser.add_argument("--fix", action="append", default=[],
                        metavar="NAME:VALUE",
                        help="set a parameter by construction: applied to "
                             "every vector, not searched, not penalised. "
                             "Repeatable.")
    parser.add_argument("--factor-persistence-half-life", type=float,
                        default=None, metavar="DAYS",
                        help="cap market_vol_alpha + market_vol_beta at the "
                             "persistence with this half-life, so the search "
                             "cannot buy a variance memory the 252-day panel "
                             "cannot resolve. Unset leaves §6.3's "
                             "stationarity bound alone.")
    parser.add_argument("--start-from", default=None,
                        help="a certificate whose best_vector seeds the "
                             "search, or 'pt-v1'. Warm-starting replaces the "
                             "Latin-hypercube screen, which phase 3 measured "
                             "as its worst-value stage: 48 random points in "
                             "the box and not one beat pt-v1.")
    parser.add_argument("--degeneracy-probe", action="store_true",
                        help="re-measure phase 2's zero-memory corner here")
    parser.add_argument("--dual-horizon", type=int, default=None,
                        metavar="DAYS",
                        help="price every candidate at 252 days AND at this "
                             "horizon, summing the two margined losses. The "
                             "second horizon is scored against its OWN bands "
                             "(facts.REAL_MARKETS_504) and its own noise "
                             "scale (facts.SEED_SD_504) -- pairing one "
                             "horizon's measurement with the other's ruler "
                             "is the error this flag exists to avoid. Costs "
                             "roughly three times the panel runs per "
                             "candidate, because a 504-day panel is twice "
                             "the work of a 252-day one.")
    parser.add_argument("--room-target", type=float, default=0.0,
                        metavar="SD",
                        help="prefer candidates whose statistics sit at "
                             "least this many seed-sds inside their bands. "
                             "The band loss is flat inside a band, so it "
                             "cannot rank candidates that are all inside; "
                             "this breaks that tie toward the one a single "
                             "seed is least likely to knock out of band. "
                             "0 disables it.")
    parser.add_argument("--room-weight", type=float, default=0.0,
                        help="weight on the room term. Small by intent: it "
                             "is a tie-breaker, not a second objective.")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    import pretium.facts as facts
    import pretium.loss as loss_mod

    ship = lib.shipped_values()
    if args.params == "spectrum8":
        params = list(SPECTRUM_8)
    elif args.params == "searched9":
        params = list(lib.SEARCHED_9)
    else:
        params = [p for p in args.params.split(",") if p]
    search_seeds = (tuple(int(s) for s in args.search_seeds.split(","))
                    if args.search_seeds else SEARCH_SEEDS)
    train_seeds = (tuple(int(s) for s in args.train_seeds.split(","))
                   if args.train_seeds else lib.TRAIN_SEEDS)
    holdout_seeds = tuple(int(s) for s in args.holdout_seeds.split(","))
    hu_n, hu_seed = (int(x) for x in args.holdout_universe.split(":"))

    cap = (persistence_cap(args.factor_persistence_half_life)
           if args.factor_persistence_half_life else None)
    fixed = {}
    for item in args.fix:
        name, _, value = item.partition(":")
        name = name.strip()
        if not name or not value:
            raise SystemExit(f"--fix wants NAME:VALUE, got {item!r}")
        if name in params:
            raise SystemExit(
                f"--fix {name} is also in --params: a parameter is either "
                "searched or set by construction, not both")
        fixed[name] = float(value)
    space = DevSpace(params, ship, factor_persistence_cap=cap, fixed=fixed)

    in_loss = list(loss_mod.LIVE_TARGETS) + list(loss_mod.CONSTRAINTS)
    per_key_margin = {}
    for item in args.margin_sd_for:
        name, _, value = item.partition(":")
        name = name.strip()
        if not name or not value:
            raise SystemExit(f"--margin-sd-for wants NAME:SD, got {item!r}")
        if name not in in_loss:
            raise SystemExit(
                f"--margin-sd-for {name} is not in the loss; a margin on a "
                "statistic the objective does not read buys nothing")
        per_key_margin[name] = float(value)
    margin = margined_bands(facts.REAL_MARKETS, facts.SEED_SD, in_loss,
                            args.margin_sd, per_key_margin)
    margin_far = None
    if args.dual_horizon:
        if args.dual_horizon != 504:
            raise SystemExit(
                f"--dual-horizon is only calibrated for 504 (got "
                f"{args.dual_horizon}); facts.REAL_MARKETS_504 and "
                "facts.SEED_SD_504 are the only horizon-matched ruler this "
                "library ships, and scoring another horizon against them "
                "would be the wrong-ruler error in a new dress")
        margin_far = margined_bands(facts.REAL_MARKETS_504, facts.SEED_SD_504,
                                    in_loss, args.margin_sd, per_key_margin)
    ev = Evaluator(args.workers, args.budget_panel_runs, margin,
                   days_far=args.dual_horizon, margin_far=margin_far,
                   room_target=args.room_target, room_weight=args.room_weight)
    started = time.perf_counter()
    trace: list[dict] = []

    def say(text: str) -> None:
        print(f"[{time.perf_counter() - started:7.0f}s "
              f"{ev.panel_runs:>5}/{ev.budget}] {text}", flush=True)

    # ── Stage 0: the baseline, on both seed tiers ────────────────────────
    base_search = ev.batch([{}], search_seeds, "baseline")[0]
    base_train = ev.batch([{}], train_seeds, "baseline")[0]
    subset_offsets = {
        key: base_search["medians"][key] - base_train["medians"][key]
        for key in facts.REAL_MARKETS
    }
    say(f"pt-v1 L_real: {base_train['loss_real']:.4f} on {len(train_seeds)} "
        f"train seeds, {base_search['loss_real']:.4f} on the "
        f"{len(search_seeds)}-seed search subset")
    for line in panel_rows(base_train):
        print(line)

    # The margin, stated before anything is scored against it, and its
    # zero case checked rather than assumed: at k = 0 the search
    # objective must BE the shipped loss, or the two rulers have already
    # drifted and no later comparison means anything.
    zero = margined_bands(facts.REAL_MARKETS, facts.SEED_SD, in_loss, 0.0)
    check = search_loss(base_train["panels"], zero)
    if abs(check - base_train["loss_real"]) > 1e-12:
        raise SystemExit(
            f"the margined loss at k = 0 reads {check!r} where "
            f"band_distance_loss reads {base_train['loss_real']!r}; the "
            "search ruler and the published one disagree at the point they "
            "must agree")
    print(f"\n=== the search aims {args.margin_sd} seed-sds inside each "
          f"band; every REPORTED verdict uses the true band ===")
    for key, spec in margin.items():
        lo, hi = spec["true_band"]
        mlo, mhi = spec["band"]
        print(f"  {key:<24} true [{lo:+8.4f}, {hi:+8.4f}] "
              f"({spec['band_width_sd']:5.2f} sd wide) -> searched "
              f"[{mlo:+8.4f}, {mhi:+8.4f}]"
              + ("   DEGENERATE: narrower than 2k, aimed at the midpoint"
                 if spec["degenerate"] else ""))

    # The confirmation and shrink stages run on thirty seeds and cost three
    # times what a search-subset sweep costs; they are also the only stages
    # whose output the verdict is read from. They get their budget first.
    ev.reserve(args.reserve_panel_runs)

    # ── Stage 1: Latin-hypercube screening ───────────────────────────────
    rng = np.random.default_rng(args.cma_seed)
    n = len(params)
    lhs = np.empty((args.screen, n))
    for j in range(n):
        perm = rng.permutation(args.screen)
        lhs[:, j] = (space.lo[j] + (perm + rng.random(args.screen))
                     / args.screen * (space.hi[j] - space.lo[j]))

    # The best FEASIBLE point seen anywhere, tracked outside the
    # optimisers. Two reasons, both learned the hard way in the smoke
    # run: the imported CMA-ES seeds its incumbent at +inf and so can
    # report a "best" worse than the point it started from, and a stage
    # that hits the budget guard mid-sweep would otherwise throw away
    # every improvement it had made. The tracker is per (lambda, seed
    # set), because a loss measured on ten seeds and one measured on
    # thirty are different objectives and must not be compared.
    best_seen: dict[tuple, tuple[float, np.ndarray]] = {}

    def evaluate_batch(us: list[np.ndarray], lam: float, stage: str,
                       seeds: tuple[int, ...]) -> list[float]:
        repaired, penalties, raws = [], [], []
        for u in us:
            r, dist2 = space.repair(np.asarray(u, dtype=float))
            repaired.append(r)
            penalties.append(PENALTY_SCALE * dist2)
            raws.append(space.to_raw(r))
        rows = ev.batch(raws, seeds, stage)
        out = [penalised(row, space, lam) + pen
               for row, pen in zip(rows, penalties)]
        key = (lam, seeds)
        for value, r in zip(out, repaired):
            if value < best_seen.get(key, (float("inf"), None))[0]:
                best_seen[key] = (float(value), r.copy())
        if ev.panel_runs % 200 < len(rows) * len(seeds):
            say(ev.progress(stage, f" best {best_seen[key][0]:.4f}"))
        return out

    def run_stage(fn, lam: float, seeds: tuple[int, ...], label: str):
        """Run one optimiser stage, surviving the budget guard.

        A stage that runs out of budget mid-sweep has still done work;
        the tracker is what it hands back, so the run degrades to "the
        best point found within the budget" rather than to an exception.
        """
        try:
            fn()
        except BudgetExhausted as exc:
            say(f"{label} stopped on budget: {exc}")
        best = best_seen.get((lam, seeds))
        if best is None:
            raise SystemExit(f"{label}: no feasible point evaluated")
        return best[1], best[0]

    # The screen's job is to find a starting point, and phase 3 measured
    # what 48 random points in this box buy: nothing — not one of them
    # beat pt-v1, for 8% of the budget. That is the expected shape of a
    # box drawn around an already-calibrated model, and it means the
    # information is better spent on polish. A warm start says so
    # explicitly: the point to start from is a vector some earlier
    # measurement already argued for, and the screen shrinks to the
    # comparison that matters — is the warm start better than pt-v1 on
    # THIS objective, which is not the objective that produced it.
    screen_points = [np.zeros(n)]
    screen_labels = ["pt-v1"]
    if args.start_from and args.start_from != "pt-v1":
        # Either shape of certificate: `calibrate.py` writes one vector
        # under `best_vector`, `evaluate_axes.py` writes several under
        # `vectors`, and the vector this phase most wants to start from —
        # pt-v2 itself — lives in the second kind, because it is a named
        # variant of a search optimum rather than a search's own answer.
        path, _, vector_name = args.start_from.partition("#")
        with open(path, encoding="utf-8") as handle:
            doc = json.load(handle)
        if vector_name:
            warm = doc["vectors"][vector_name]["vector"]
        else:
            warm = doc["best_vector"]
        w, _ = space.repair(space.from_raw(warm))
        screen_points.append(w)
        screen_labels.append(args.start_from)
    screen_points += [lhs[i] for i in range(args.screen)]
    screen_labels += [f"lhs{i}" for i in range(args.screen)]
    try:
        screen_losses = evaluate_batch(screen_points, args.lam, "screen",
                                       search_seeds)
    except BudgetExhausted as exc:
        raise SystemExit(f"budget too small to screen: {exc}")
    best_i = int(np.argmin(screen_losses))
    x0 = screen_points[best_i]
    trace.append({"stage": "screen", "samples": args.screen,
                  "warm_start": args.start_from,
                  "candidates": [{"label": lab, "penalised_loss": float(v)}
                                 for lab, v in zip(screen_labels,
                                                   screen_losses)],
                  "base_loss": float(screen_losses[0]),
                  "best_label": screen_labels[best_i],
                  "best_loss": float(screen_losses[best_i])})
    say(f"screen: pt-v1 {screen_losses[0]:.4f} -> best "
        f"{screen_losses[best_i]:.4f} ({screen_labels[best_i]})")

    # ── Stage 2: CMA-ES ──────────────────────────────────────────────────
    cma_trace: list[dict] = []
    best_u, best_loss = run_stage(
        lambda: cma_es(
            lambda us: evaluate_batch(us, args.lam, "cma", search_seeds),
            space, x0, args.sigma0, args.population, args.generations, rng,
            cma_trace),
        args.lam, search_seeds, "cma")
    trace.append({"stage": "cma", "generations": cma_trace})
    say(f"cma: best penalised loss {best_loss:.4f}")

    # ── Stage 3: compass polish on the search subset ─────────────────────
    polish_trace: list[dict] = []
    steps = [float(s) for s in args.compass_steps.split(",") if s]
    best_u, best_loss = run_stage(
        lambda: compass_polish(
            lambda us: evaluate_batch(us, args.lam, "compass", search_seeds),
            best_u, best_loss, space, steps, polish_trace),
        args.lam, search_seeds, "compass")
    trace.append({"stage": "compass", "steps": steps, "moves": polish_trace})
    say(f"compass: best penalised loss {best_loss:.4f}")

    # ── Stage 4: confirmation polish on the FULL training set ────────────
    # §8's training objective is thirty seeds. The subset above is a
    # ranking device; the last steps are taken against the objective the
    # verdict is read on, so the reported optimum is an optimum OF that
    # objective and not of its cheaper proxy.
    ev.release()
    confirm_trace: list[dict] = []
    best_u, _ = space.repair(best_u)
    confirm_steps = [float(s) for s in args.confirm_steps.split(",") if s]
    start_u = best_u.copy()
    try:
        start_loss = evaluate_batch([start_u], args.lam, "confirm",
                                    train_seeds)[0]
        best_u, confirm_loss = run_stage(
            lambda: compass_polish(
                lambda us: evaluate_batch(us, args.lam, "confirm",
                                          train_seeds),
                start_u, start_loss, space, confirm_steps, confirm_trace),
            args.lam, train_seeds, "confirm")
    except BudgetExhausted:
        say("confirmation skipped: budget spent before it could start")
        confirm_loss = float("nan")
    trace.append({"stage": "confirm", "steps": confirm_steps,
                  "moves": confirm_trace})
    best_u, _ = space.repair(best_u)
    say(f"confirm: penalised loss {confirm_loss:.4f} on {len(train_seeds)} "
        f"seeds")

    # ── Stage 5: shrink — the same polish at a lambda that bites ─────────
    # §6.3 asks for the frontier to be walked from lambda = infinity DOWN,
    # "rather than starting from the unregularised fit and hoping". The
    # stages above are the hoping half: they find where the feasible set
    # is, at a lambda chosen so the realism term leads. This stage starts
    # from that point and re-polishes under a penalty large enough to pay
    # for itself, which pulls every parameter back toward pt-v1 as far as
    # the statistics allow. What survives the pull is what the panel
    # actually forced, and that — not the deepest point of a flat basin —
    # is the vector worth shipping a name to.
    shrink_trace: list[dict] = []
    shrink_steps = [float(s) for s in args.shrink_steps.split(",") if s]
    shrink_start = best_u.copy()
    try:
        shrink_start_loss = evaluate_batch([shrink_start], args.shrink_lam,
                                           "shrink", train_seeds)[0]
        best_u, shrink_loss = run_stage(
            lambda: compass_polish(
                lambda us: evaluate_batch(us, args.shrink_lam, "shrink",
                                          train_seeds),
                shrink_start, shrink_start_loss, space, shrink_steps,
                shrink_trace),
            args.shrink_lam, train_seeds, "shrink")
    except BudgetExhausted:
        say("shrink skipped: budget spent before it could start")
        shrink_loss = float("nan")
    trace.append({"stage": "shrink", "lambda": args.shrink_lam,
                  "steps": shrink_steps, "moves": shrink_trace})
    best_u, _ = space.repair(best_u)
    best_raw = space.to_raw(best_u)
    say(f"shrink: penalised loss {shrink_loss:.4f} at lambda "
        f"{args.shrink_lam}, squared deviation "
        f"{space.squared_deviation(best_raw):.4f}")

    # ── Stage 5: the frontier, from the cache, for free ──────────────────
    train_rows = [r for k, r in ev.cache.items()
                  if k[1] == ",".join(str(s) for s in train_seeds)]
    search_rows = [r for k, r in ev.cache.items()
                   if k[1] == ",".join(str(s) for s in search_seeds)]
    frontier = pareto_frontier(search_rows + train_rows, space)
    # The frontier is §6.3's deliverable and its realism axis is the
    # PUBLISHED one: what a reader gets for a given deviation from the
    # shipped preset, priced against the true bands. The margined loss
    # rides along on each point so the two can be compared, but it is not
    # what the picks are chosen on.
    lambda_picks = {}
    for lam_text in args.lambda_frontier.split(","):
        lam = float(lam_text)
        pick = min(search_rows + train_rows,
                   key=lambda r: penalised(r, space, lam, "loss_real"))
        lambda_picks[lam_text] = {
            "overrides": pick["overrides"],
            "loss_real": pick["loss_real"],
            "loss_search": pick["loss_search"],
            "squared_deviation": space.squared_deviation(pick["overrides"]),
            "seeds": len(pick["seeds"]),
        }

    # ── Stage 6: the held-out axes (§8) ──────────────────────────────────
    def measure(overrides: dict[str, float], seeds: tuple[int, ...],
                universe_n: int, universe_seed: int, days: int) -> dict:
        jobs = [(overrides, seed, days, universe_n, universe_seed)
                for seed in seeds]
        results = lib.run_pool(jobs, args.workers)
        ev.panel_runs += len(jobs)
        panels = [r["panel"] for r in results]
        lib.crn_streams(results)
        # An axis is scored against the ruler cut for ITS OWN horizon.
        #
        # This used to score every axis against `facts.REAL_MARKETS`,
        # including the 504-day one, and that made every horizon-axis figure
        # this tool has ever produced a wrong-ruler measurement -- the 1.910
        # that rejected `jointmix` and the 0.0000 that made `ptv4` look
        # perfect among them. The two differ by a factor of five on the same
        # panels: 0.4028 against the 252-day bands, 2.0164 against the
        # matched ones. The `--dual-horizon` flag taught the SEARCH to use
        # the right bands and left the certificate behind.
        far = days == 504
        bands = facts.REAL_MARKETS_504 if far else None
        scales = facts.SEED_SD_504 if far else None
        breakdown = loss_mod.band_distance_loss(panels, bands=bands,
                                                seed_sd=scales)
        stats = {k: {kk: vv for kk, vv in v.items()}
                 for k, v in breakdown["statistics"].items()}
        # `room_sd` is the quantity this whole phase is about: how far
        # inside its TRUE band a statistic sits, in its own seed noise,
        # signed so that negative means out. The band loss cannot see it
        # — that is §6.1's flatness — and it is what decides whether a
        # statistic survives a change of seeds, universe or horizon.
        for key, row in stats.items():
            lo, hi = row["band"]
            # The same horizon's noise scale, for the same reason.
            sd = (facts.SEED_SD_504 if far else facts.SEED_SD).get(key)
            m = row["measured"]
            row["room_sd"] = (None if m is None or not sd
                              else min(m - lo, hi - m) / sd)
            if key in margin:
                mlo, mhi = margin[key]["band"]
                row["margined_band"] = [mlo, mhi]
                row["inside_margined_band"] = (
                    m is not None and mlo <= m <= mhi)
        return {
            "seeds": list(seeds), "days": days,
            "universe": f"Universe.random({universe_n}, seed={universe_seed})",
            "loss_real": breakdown["loss"],
            "loss_search": search_loss(panels, margin),
            "bands_used_for_every_verdict_here": "the TRUE bands "
                                                 "(facts.REAL_MARKETS)",
            "bootstrap_spread": bootstrap_spread(panels),
            "statistics": stats,
            "panels": panels,
        }

    def bootstrap_spread(panels: list[dict], draws: int = 2000) -> float:
        """§8's yardstick: how much L_real moves on a re-draw of the seeds.

        The overfitting rule prices "validation worse than training"
        against this, and without it the rule is a bare 2x on a number
        whose own sampling spread nobody measured. Resampling is over
        SEEDS — the unit that is exchangeable here — and the loss is
        recomputed from each resample's medians by the same shipped
        function the search minimised. Seeded, so the certificate's
        number is reproducible like everything else in it.
        """
        if len(panels) < 2:
            return float("nan")
        boot = np.random.default_rng(args.cma_seed)
        losses = []
        for _ in range(draws):
            idx = boot.integers(0, len(panels), len(panels))
            losses.append(loss_mod.band_distance_loss(
                [panels[i] for i in idx])["loss"])
        return float(statistics.stdev(losses))

    axes = {}
    for label, vector in (("pt-v1", {}), ("candidate", best_raw)):
        axes[label] = {
            "train_seeds": measure(vector, train_seeds,
                                   lib.PANEL_UNIVERSE_N,
                                   lib.PANEL_UNIVERSE_SEED, lib.PANEL_DAYS),
            "holdout_seeds": measure(vector, holdout_seeds,
                                     lib.PANEL_UNIVERSE_N,
                                     lib.PANEL_UNIVERSE_SEED, lib.PANEL_DAYS),
            "holdout_universe": measure(vector, train_seeds, hu_n, hu_seed,
                                        lib.PANEL_DAYS),
            "holdout_horizon": measure(
                vector,
                tuple(int(s) for s in args.holdout_days_seeds.split(",")),
                lib.PANEL_UNIVERSE_N, lib.PANEL_UNIVERSE_SEED,
                args.holdout_days),
        }
        say(f"{label}: " + ", ".join(
            f"{axis} {row['loss_real']:.3f}"
            for axis, row in axes[label].items()))

    # ── §8's verdict, computed rather than eyeballed ─────────────────────
    train_loss = axes["candidate"]["train_seeds"]["loss_real"]
    spread = axes["candidate"]["train_seeds"]["bootstrap_spread"]
    overfitting: dict = {
        "rule": "§8: any trained-to statistic in band on train and out of "
                "band on a validation axis; or validation L_real exceeding "
                "train L_real by more than 2x the bootstrap spread of train "
                "L_real across its seeds",
        "train_loss_real": train_loss,
        "train_bootstrap_spread": spread,
        "threshold": train_loss + 2.0 * spread,
        "axes": {},
        "statistics_in_band_on_train_out_on_validation": [],
    }
    train_stats = axes["candidate"]["train_seeds"]["statistics"]
    for axis in ("holdout_seeds", "holdout_universe", "holdout_horizon"):
        row = axes["candidate"][axis]
        overfitting["axes"][axis] = {
            "loss_real": row["loss_real"],
            "exceeds_threshold": row["loss_real"] > train_loss + 2.0 * spread,
        }
        for key in list(loss_mod.LIVE_TARGETS) + list(loss_mod.CONSTRAINTS):
            if (train_stats[key]["distance"] == 0
                    and row["statistics"][key]["distance"] > 0):
                overfitting[
                    "statistics_in_band_on_train_out_on_validation"].append(
                        {"statistic": key, "axis": axis,
                         "measured": row["statistics"][key]["measured"],
                         "band": row["statistics"][key]["band"]})
    overfitting["verdict"] = (
        "rejected by §8" if (
            overfitting["statistics_in_band_on_train_out_on_validation"]
            or any(a["exceeds_threshold"]
                   for a in overfitting["axes"].values()))
        else "passes §8 on every axis")
    say(f"overfitting: {overfitting['verdict']}")

    degeneracy = None
    if args.degeneracy_probe:
        corner = {"market_factor_sigma": 0.0084, "idio_sigma_scale": 0.86,
                  "garch_alpha": 0.424, "garch_beta": 0.096,
                  "garch_ceiling_multiple": 19.4, "momentum_theta": 0.216}
        degeneracy = {
            "vector": corner,
            "source": "CALIBRATION-RESULTS.md §6.3 — the zero-memory corner",
            "in_calibration_box": all(
                calibration_box(k, ship[k])[0] <= v
                <= calibration_box(k, ship[k])[1]
                for k, v in corner.items() if k in lib.PARAM_SPECS),
            "measured": measure(corner, train_seeds, lib.PANEL_UNIVERSE_N,
                                lib.PANEL_UNIVERSE_SEED, lib.PANEL_DAYS),
        }
        say(f"degeneracy probe: L_real "
            f"{degeneracy['measured']['loss_real']:.3f}")

    wall = time.perf_counter() - started

    moves = []
    for name in params:
        before, after = ship[name], best_raw[name]
        moves.append({
            "parameter": name, "pt_v1": before, "candidate": after,
            "deviation": deviation(name, after, before),
            "deviation_class": lib.PARAM_SPECS[name]["kind"],
            "ratio": after / before if before else None,
            "box": space.box[name],
            "column_norm_seed_sds_per_dev_unit": COLUMN_NORMS.get(name),
        })

    # The certificate is written BEFORE the human-readable summary, and
    # the ordering is load-bearing rather than stylistic. It used to be
    # the other way round, and on 2026-08-23 a formatting bug in the
    # summary -- a parameter that ships at ZERO has no ratio, and None
    # does not take a float format -- raised after every axis had been
    # measured and before anything was saved. Ninety minutes of search
    # died in a print statement. A run that has computed its answer must
    # persist it before it does anything else with it.
    lib.write_json(args.out, {
        "provenance": lib.provenance(),
        "claim": {
            "kind": "calibration",
            "model_class": "pt-v1 (the shipped preset) with the searched "
                           "parameters free",
            "targets": list(loss_mod.LIVE_TARGETS),
            "constraints": list(loss_mod.CONSTRAINTS),
            "structural_excluded": list(loss_mod.STRUCTURAL),
            "searched_parameters": params,
            "search_space_source": "CALIBRATION-RESULTS.md §5, the measured "
                                   "identifiability spectrum (not §3.9's "
                                   "argued list)",
            "column_norms": {p: COLUMN_NORMS.get(p) for p in params},
            "excluded": {
                "guards_visible_but_off_limits": {
                    "price_breaker_fraction": 8.3, "mispricing_cap": 4.6},
                "weak_but_real": {
                    "market_factor_sigma": 4.1, "market_vol_vix_anchor": 3.8,
                    "garch_floor_multiple": 2.5, "crowd_momentum_gain": 2.0,
                    "garch_omega": 1.6},
                "effectively_unidentified": {
                    "garch_ceiling_multiple": 0.41,
                    "sector_factor_sigma": 0.28},
            },
        },
        "method": {
            "loss": "pretium.loss.band_distance_loss (the shipped "
                    "objective) + lambda * sum_j dev_j^2 (§6.3)",
            "search_margin": {
                "margin_sd": args.margin_sd,
                "margin_sd_overrides": per_key_margin,
                "what_it_changes": "the SEARCH objective only. Candidates "
                                   "are ranked on `loss_search`, the same "
                                   "arithmetic as band_distance_loss "
                                   "evaluated against bands shrunk by "
                                   "margin_sd * SEED_SD on each side. "
                                   "`loss_real` and EVERY band verdict, "
                                   "panel row and §8 result in this "
                                   "certificate are the shipped function "
                                   "against the TRUE bands "
                                   "(facts.REAL_MARKETS), unshrunk.",
                "why": "CALIBRATION.md §6.1's band loss is flat inside the "
                       "band and §6.3's regulariser pulls back until the "
                       "pull stops paying, which is exactly at a band "
                       "edge; the composition parks every trained-to "
                       "statistic on the least robust point of the "
                       "feasible set (phase 3, CALIBRATION-PTV2.md §5.1). "
                       "The margin moves what the search aims at without "
                       "touching what the verdict is read against.",
                "zero_case_checked": "at margin_sd = 0 the search loss is "
                                     "asserted equal to "
                                     "band_distance_loss(...)['loss'] on "
                                     "the baseline panels before the "
                                     "search starts",
                "bands": {k: {"true": list(v["true_band"]),
                              "searched": list(v["band"]),
                              "seed_sd": v["seed_sd"],
                              "band_width_sd": v["band_width_sd"],
                              "achieved_margin_sd": v["achieved_sd"],
                              "degenerate": v["degenerate"]}
                          for k, v in margin.items()},
            },
            "factor_persistence_cap": (
                None if cap is None else {
                    "half_life_days": args.factor_persistence_half_life,
                    "persistence": cap,
                    "window_days": lib.PANEL_DAYS,
                    "half_lives_in_window": (
                        lib.PANEL_DAYS / args.factor_persistence_half_life),
                    "applies_to": "market_vol_alpha + market_vol_beta",
                    "why": "a variance memory longer than the window it is "
                           "measured through is not identified by that "
                           "window; the loss can be compatible with it "
                           "without being evidence for it. Enforced in the "
                           "search's own repair, not in "
                           "instrumentlib.feasibility_violation, which "
                           "states what the library's stationarity "
                           "analytics cover and backs three committed "
                           "falsification certificates.",
                }),
            "lambda": args.lam,
            "seed_sd_source": "pretium.facts.SEED_SD",
            "seed_sd": dict(facts.SEED_SD),
            "seed_sd_provenance": dict(facts.SEED_SD_PROVENANCE),
            "bands": {k: list(v) for k, v in facts.REAL_MARKETS.items()},
            "search_seeds": list(search_seeds),
            "train_seeds": list(train_seeds),
            "subset_offsets_at_pt_v1": subset_offsets,
            "days": lib.PANEL_DAYS,
            "universe": f"Universe.random({lib.PANEL_UNIVERSE_N}, "
                        f"seed={lib.PANEL_UNIVERSE_SEED})",
            "box_rule": "§6.3's ~[1/4x, 4x] of the shipped value, "
                        "intersected with each parameter's hard range",
            "box": {name: list(space.box[name]) for name in params},
            "optimiser": {
                "stages": ["latin-hypercube screen", "CMA-ES",
                           "compass polish on the search subset",
                           "compass polish on the full training set",
                           "shrink: compass polish at the shrink lambda"],
                "shrink_lambda": args.shrink_lam,
                "shrink_steps": args.shrink_steps,
                "implementation": "falsify.py's (mu/mu_w, lambda) CMA-ES "
                                  "and compass polish, unmodified",
                "screen": args.screen,
                "population": args.population,
                "generations": args.generations,
                "sigma0": args.sigma0,
                "seed": args.cma_seed,
                "compass_steps": args.compass_steps,
                "confirm_steps": args.confirm_steps,
                "warm_start": args.start_from,
            },
            "workers": args.workers,
        },
        "baseline": {"search_seeds": base_search, "train_seeds": base_train},
        "best_vector": best_raw,
        "moves": moves,
        "axes": axes,
        "lambda_frontier": {"picks": lambda_picks, "pareto": frontier},
        "overfitting": overfitting,
        "degeneracy_probe": degeneracy,
        "crn_guard": {
            "asserted_stream": lib.CRN_STREAM,
            "note": "the market stream's draw count is a pure function of "
                    "(market status, active roster, sector count) and is "
                    "ASSERTED equal across every vector on every seed — a "
                    "move there would make these secants re-alignment "
                    "noise. The economy stream's count varies with macro "
                    "state by design and every parameter reaches macro "
                    "state through realised volatility, so its deviations "
                    "are RECORDED, not failures. "
                    "results/draw-schedule-2026-08-22.json traces phase "
                    "3's 16 'violations' and finds the market stream "
                    "invariant on all 58 (vector, seed) pairs.",
            "economy_deviations": ev.crn_deviations,
        },
        "trace": trace,
        "history": ev.history,
        "vector_evaluations": ev.vector_evaluations,
        "panel_runs": ev.panel_runs,
        "budget_panel_runs": args.budget_panel_runs,
        "reserve_panel_runs": args.reserve_panel_runs,
        "six_seed_vector_equivalents": ev.panel_runs / 6.0,
        "wall_seconds": wall,
    })

    print("\n=== candidate, as overrides on pt-v1 ===")
    for move in sorted(moves, key=lambda m: -abs(m["deviation"])):
        # `ratio` is None for a parameter that SHIPS AT ZERO, because
        # there is no multiple of zero. The pt-v4 slow-variance trio is
        # the first such parameter this project has had, and formatting
        # that None as a float crashed a 90-minute run after its axes
        # were measured and before its certificate was written.
        ratio = ("      n/a" if move["ratio"] is None
                 else f"x{move['ratio']:.3f}")
        print(f"  {move['parameter']:<28} {move['pt_v1']:>10.5g} -> "
              f"{move['candidate']:>10.5g}   dev {move['deviation']:+.4f}"
              f"   {ratio}")
    print("\n=== panel: pt-v1 -> candidate on the training seeds ===")
    print("    (bands and every verdict below are the TRUE bands; "
          f"the search aimed {args.margin_sd} sd inside them)")
    for key in facts.REAL_MARKETS:
        before = axes["pt-v1"]["train_seeds"]["statistics"][key]
        after = axes["candidate"]["train_seeds"]["statistics"][key]
        room = after.get("room_sd")
        print(f"  {key:<24} {before['measured']:+10.4f} -> "
              f"{after['measured']:+10.4f}  band {before['band']}"
              f"  {after['role']:<12}"
              f"  d {before['distance']:.4f} -> {after['distance']:.4f}"
              + (f"  room {room:+6.2f} sd" if room is not None else ""))
    print(f"\nevaluations: {ev.vector_evaluations} vectors, "
          f"{ev.panel_runs} panel runs "
          f"({ev.panel_runs / 6.0:.0f} six-seed-vector equivalents against a "
          f"budget of {args.budget_panel_runs / 6.0:.0f}), wall {wall:.0f}s")



if __name__ == "__main__":
    main()
