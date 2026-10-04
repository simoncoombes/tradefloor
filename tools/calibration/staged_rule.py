"""When a short screen may stop: the kill and settle rules for staged screens.

A staged screen measures a candidate on a few seeds first (stage A) and on
the full block only if it survives (stage B). Stage B's seeds include stage
A's, so a stage-A reading is the first part of the stage-B reading. The
question at stage A is whether the full block could still pass, and the
rule must not throw away a candidate stage B would have passed more often
than a stated rate.

The rule for a row read as a mean or median over seeds
----------------------------------------------------------

After n seeds a row reads X_n with standard error se_n. Stage B reads X_N
over N seeds that include those n. The row passes stage B when X_N lies in
its band [lo, hi] (either side may be open). Stage A kills the candidate on
the row when

    X_n > hi + c * se_n    or    X_n < lo - c * se_n

with

    c = sqrt(1 - n/N) * t_inv(1 - gamma / (K * L), n - 1)

where t_inv is the Student t quantile with n - 1 degrees of freedom, K is
the number of rows that can kill at this stage, L the number of looks
(stage-A extensions) at which a kill is allowed, and gamma the stated rate.

Why this bounds the rate. With per-seed readings normal with sd s and the
stage-B seeds nested, X_n given X_N = x is normal with mean x and variance
s^2 (1/n - 1/N), independent of the stage-A sample sd. A candidate that
passes stage B has X_N <= hi, and the chance that X_n still lands c * se_n
above hi is largest when X_N sits on hi, where it is

    P(t_{n-1} > c / sqrt(1 - n/N)) = gamma / (K * L).

So for every candidate, whatever its true value,

    P(stage A kills on a row | stage B passes that row) <= gamma / (K * L),

and over K rows and L looks, by the union bound,

    P(stage A kills | stage B passes every row) <= gamma.

The default gamma is 0.05: stage A wrongly kills at most one candidate in
twenty that stage B would have passed. `false_kill_bound` computes the
per-row figure for any c, and the tests check it by simulation.

Settling early
--------------

The same argument, with the band shrunk by the margin stage B demands,
lets a row stop gathering seeds once it is far enough inside:

    lo' + c_s * se_n <= X_n <= hi' - c_s * se_n,
    lo' = lo + m * se_N,  hi' = hi - m * se_N,  se_N = se_n * sqrt(n / N),

with c_s built like c from its own rate gamma_s. A settled row fails its
full-block requirement with probability at most gamma_s / (K * L). Seeds
are drawn per protocol, so a protocol stops when every row it feeds has
settled or the candidate is dead, and otherwise extends by one block.

Rows that are not means
-----------------------

* `count` rows (k of N seeds or rules may be bad, k <= max_bad): stage A
  kills only when the bad count already exceeds max_bad, which the nested
  stage B cannot undo. False-kill rate zero. A count row settles when even
  every remaining seed going bad would stay within max_bad.
* `allseeds` rows (every seed or rule must pass): one failure kills, for
  the same reason. False-kill rate zero. They never settle before N.
* `deterministic` rows (no seed noise): decided on first reading.

Readings that are not normal (medians of skewed rows, pooled rates) are
judged on a bootstrap standard error. The normal argument is then an
approximation, and the tests measure the realised rate on skewed readings.

Standard library only, so the desk and the box can both import it.
"""
from __future__ import annotations

import dataclasses
import math
import random
import statistics
from typing import Callable, Iterable, Sequence

GAMMA = 0.05          # stated rate: P(stage A kills | stage B passes) <= GAMMA
GAMMA_SETTLE = 0.05   # P(a settled row fails its full-block requirement) <= GAMMA_SETTLE


# -- the Student t distribution, from the regularised incomplete beta ------

def _betacf(a: float, b: float, x: float) -> float:
    tiny, eps = 1e-300, 1e-15
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 400):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def _betainc(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(lbeta + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_sf(t: float, df: float) -> float:
    """P(T > t) for Student's t with df degrees of freedom."""
    if math.isinf(df):
        return 0.5 * math.erfc(t / math.sqrt(2.0))
    tail = 0.5 * _betainc(df / 2.0, 0.5, df / (df + t * t))
    return tail if t >= 0 else 1.0 - tail


def t_inv(p: float, df: float) -> float:
    """The p quantile of Student's t, by bisection on `t_sf`."""
    if not 0.0 < p < 1.0:
        raise ValueError(p)
    if p < 0.5:
        return -t_inv(1.0 - p, df)
    lo, hi = 0.0, 1.0
    while t_sf(hi, df) > 1.0 - p:
        hi *= 2.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if t_sf(mid, df) > 1.0 - p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# -- thresholds --------------------------------------------------------------

def threshold(n: int, N: int, rate: float) -> float:
    """The c of the module docstring for one row and one look at a given rate."""
    if n >= N:
        return 0.0
    if n < 2:
        return math.inf          # one seed has no standard error to judge by
    return math.sqrt(1.0 - n / N) * t_inv(1.0 - rate, n - 1)


def kill_threshold(n: int, N: int, *, rows: int, looks: int = 1,
                   gamma: float = GAMMA) -> float:
    return threshold(n, N, gamma / (max(rows, 1) * max(looks, 1)))


def settle_threshold(n: int, N: int, *, rows: int, looks: int = 1,
                     gamma: float = GAMMA_SETTLE) -> float:
    return threshold(n, N, gamma / (max(rows, 1) * max(looks, 1)))


def false_kill_bound(c: float, n: int, N: int) -> float:
    """sup over true values of P(kill at n | pass at N), one row, one look."""
    if n >= N:
        return 0.0
    return t_sf(c / math.sqrt(1.0 - n / N), n - 1)


# -- readings ----------------------------------------------------------------

def mean_se(values: Sequence[float]) -> tuple[float, float]:
    v = [float(x) for x in values]
    if len(v) < 2:
        return (v[0] if v else math.nan), math.inf
    return statistics.fmean(v), statistics.stdev(v) / math.sqrt(len(v))


def bootstrap_se(values: Sequence, estimator: Callable[[Sequence], float], *,
                 draws: int = 400, seed: int = 20261004) -> float:
    """The sd of `estimator` over seed resamples, with a fixed generator."""
    vals = list(values)
    if len(vals) < 2:
        return math.inf
    rng = random.Random(seed)
    out = []
    for _ in range(draws):
        x = estimator([vals[rng.randrange(len(vals))] for _ in vals])
        if x is not None and math.isfinite(x):
            out.append(x)
    return statistics.stdev(out) if len(out) > 1 else math.inf


@dataclasses.dataclass(frozen=True)
class Row:
    """A graded row as the rule needs it.

    kind: `mean` (value and se over seeds), `count` (bad of n against
    max_bad over N), `allseeds` (failures seen), or `deterministic`.
    """
    id: str
    kind: str = "mean"
    lo: float | None = None
    hi: float | None = None
    margin_se: float = 0.0
    max_bad_share: float | None = None    # count rows: bad / N must stay <= this


@dataclasses.dataclass(frozen=True)
class Decision:
    row: str
    state: str          # "out", "in" or "open"
    n: int
    N: int
    value: float | None
    se: float | None
    why: str


def decide(row: Row, *, n: int, N: int, value: float | None = None,
           se: float | None = None, bad: int | None = None,
           c_kill: float, c_settle: float) -> Decision:
    """One row's state after n of N seeds."""
    if row.kind == "deterministic":
        ok = value is not None and (row.lo is None or value >= row.lo) \
            and (row.hi is None or value <= row.hi)
        return Decision(row.id, "in" if ok else "out", n, N, value, None,
                        "no seed noise: the first reading is the reading")
    if row.kind == "allseeds":
        if bad:
            return Decision(row.id, "out", n, N, value, None,
                            f"{bad} failure(s) already; stage B keeps these seeds")
        return Decision(row.id, "in" if n >= N else "open", n, N, value, None,
                        "no failure yet")
    if row.kind == "count":
        if bad is None or row.max_bad_share is None:
            raise ValueError(f"{row.id}: a count row needs bad and max_bad_share")
        max_bad = math.floor(row.max_bad_share * N + 1e-9)
        if bad > max_bad:
            return Decision(row.id, "out", n, N, None, None,
                            f"{bad} bad already, more than the {max_bad} of {N} allowed")
        if bad + (N - n) <= max_bad:
            return Decision(row.id, "in", n, N, None, None,
                            f"{bad} bad; even {N - n} more stays within {max_bad}")
        return Decision(row.id, "open", n, N, None, None,
                        f"{bad} bad of {n}; {max_bad} of {N} allowed")
    # mean rows
    if value is None or not math.isfinite(value):
        return Decision(row.id, "open", n, N, value, se, "unreadable so far")
    se_ = se if (se is not None and se > 0) else 0.0
    lo, hi = row.lo, row.hi
    if n >= N:
        # The full block: its reading is the verdict, with the margin it demands.
        lo_m = None if lo is None else lo + row.margin_se * se_
        hi_m = None if hi is None else hi - row.margin_se * se_
        ok = (lo_m is None or value >= lo_m) and (hi_m is None or value <= hi_m)
        return Decision(row.id, "in" if ok else "out", n, N, value, se, "full block")
    if math.isinf(se_):
        return Decision(row.id, "open", n, N, value, se, "no standard error yet")
    if (hi is not None and value > hi + c_kill * se_) or \
       (lo is not None and value < lo - c_kill * se_):
        edge = hi if (hi is not None and value > hi) else lo
        return Decision(row.id, "out", n, N, value, se,
                        f"{abs(value - edge) / se_:.2f} se outside, kill line {c_kill:.2f}")
    se_N = se_ * math.sqrt(n / N)
    lo_m = None if lo is None else lo + row.margin_se * se_N
    hi_m = None if hi is None else hi - row.margin_se * se_N
    if (lo_m is None or value >= lo_m + c_settle * se_) and \
       (hi_m is None or value <= hi_m - c_settle * se_):
        room = min([d for d in ((value - lo_m) if lo_m is not None else None,
                                (hi_m - value) if hi_m is not None else None)
                    if d is not None] or [math.inf])
        return Decision(row.id, "in", n, N, value, se,
                        f"{room / se_:.2f} se inside the margin, settle line {c_settle:.2f}")
    return Decision(row.id, "open", n, N, value, se, "near a limit")


def candidate_state(decisions: Iterable[Decision]) -> str:
    """`dead` if any row is out, `settled` if every row is in, else `open`."""
    ds = list(decisions)
    if any(d.state == "out" for d in ds):
        return "dead"
    if all(d.state == "in" for d in ds):
        return "settled"
    return "open"


def next_seed_count(decisions_by_protocol: dict[str, list[Decision]], *, n: int,
                    N: int, block: int) -> dict[str, int]:
    """Seeds each protocol should have after this look.

    A protocol whose rows are all in stops at n. One with an open row
    extends by `block`, up to N. A candidate with any row out stops
    everywhere, because it is dead.
    """
    every = [d for ds in decisions_by_protocol.values() for d in ds]
    if candidate_state(every) == "dead":
        return {p: n for p in decisions_by_protocol}
    return {p: (n if all(d.state == "in" for d in ds) else min(N, n + block))
            for p, ds in decisions_by_protocol.items()}


def simulate_false_kill(c: float, n: int, N: int, *, theta: float, sd: float = 1.0,
                        hi: float = 0.0, reps: int = 20000, seed: int = 7,
                        draw: Callable[[random.Random], float] | None = None) -> tuple[float, float]:
    """Monte Carlo of (P(kill), P(kill | pass)) for one upper-edge row.

    `draw` replaces the normal per-seed reading, to measure the rule on
    skewed readings.
    """
    rng = random.Random(seed)
    kills = passes = kill_and_pass = 0
    for _ in range(reps):
        xs = [theta + (draw(rng) if draw else rng.gauss(0.0, sd)) for _ in range(N)]
        m_n, se_n = mean_se(xs[:n])
        kill = m_n > hi + c * se_n
        ok = statistics.fmean(xs) <= hi
        kills += kill
        passes += ok
        kill_and_pass += kill and ok
    return kills / reps, (kill_and_pass / passes if passes else 0.0)
