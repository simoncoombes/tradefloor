"""Measure the model's physical skew: the skewness of the index's forward
21-session log return, conditional on the VIX at the window's start, and
the line `skew = a + b * ln(VIX / 20)` through it.

    python tools/calibration/physical_skew.py simulate --arm pt-v21 \\
        --seeds 201-260 --years 21 --workers 4 --out pt-v21.npz
    python tools/calibration/physical_skew.py fit pt-v21.npz [MORE.npz ...] [--json OUT]
    python tools/calibration/physical_skew.py tape --index GSPC.csv.gz \\
        --vix VIX_History.csv [--skew SKEW_History.csv] [--json OUT]

The index surface (`derivatives/surface.rs`, `fit_smile`) solves its rho so
the 21-session strip's risk-neutral skewness, the Cboe SKEW formula's `S`
(the standardised third central moment of the log return to expiry), is
`surface_skew_physical + surface_skew_physical_slope * ln(VIX / 20) +
surface_skew_premium`. The first two are derived dials: `a` and `b` here,
the same moment of the same return under the model's own law, conditional
on the published VIX the surface is fitted to.

`simulate` runs free histories of an arm on `Universe.random(40,
seed=111)` and keeps, per close, the published VIX and the index level (the
engine's price index under `index_level_listed`; otherwise the closes
times the roster's shares, which is the same level while no name lists,
delists or fails). `fit` reads windows that start at every close after
`--burn` and end 21 closes later:

- the windows are binned by the VIX at their start, and each bin's
  skewness is the population moment `mean((r - m)^3) / sd^3` over every
  window in it, pooled across histories;
- `a` and `b` are the weighted least-squares line through the bins'
  skewness at their mean `ln(VIX / 20)`, each bin weighted by its windows.
  Not by its inverse bootstrap variance (`--weights se`): a sample
  skewness's spread rises with the crash windows it happened to draw, so
  that weighting leans the line away from the bins that drew a crash. Bins
  under `--min-windows` windows or `--min-units` histories, or above
  `--fit-max`, are shown and not fitted;
- the windows overlap, so every standard error resamples whole histories
  (independent by construction) and refits, the weights held; nothing
  treats overlapping windows as independent;
- the line is checked by the bins' standardised residuals, their
  chi-squared on K - 2 degrees of freedom, and a quadratic term's
  bootstrap z.

`tape` applies the same estimator to a real index and VIX (the S&P 500's
`^GSPC` daily close and the Cboe VIX since 1990), the bootstrap
resampling blocks of `--block` consecutive window starts in place of
histories. With `--skew` it adds each bin's mean Cboe SKEW read as a
risk-neutral skewness, `(100 - SKEW) / 10`, beside the physical one. The
series are public: Yahoo's chart API for `^GSPC` (a CSV with `date` and
`close`) and https://cdn.cboe.com/api/global/us_indices/daily_prices/
VIX_History.csv (and SKEW_History.csv).
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import math
import pathlib
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np

HORIZON = 21
YEAR = 252
VIX_CENTRE = 20.0
ROSTER = (40, 111)
BURN = 252
EDGES = (12.0, 14.0, 16.0, 18.0, 20.0, 22.5, 25.0, 30.0, 35.0, 45.0)
BOOT_B = 2000
BOOT_SEED = 20261013

_TF: dict = {}


# ===================================================================== arms

def parse_arm(text: str) -> tuple[str, str, dict[str, float]]:
    """`pt-v21`, `NAME@BASE:dial=v,...` or `@FILE` holding one of those.
    A dial written twice takes its last value."""
    text = text.strip()
    if text.startswith("@"):
        text = pathlib.Path(text[1:]).read_text().strip()
    head, _, tail = text.partition(":")
    name, _, base = head.partition("@")
    dials = {k.strip(): float(v) for k, v in (kv.split("=") for kv in tail.split(",") if kv.strip())}
    return name, base or name, dials


def _engine(path):
    if "tf" not in _TF:
        if path:
            p = str(pathlib.Path(path) / "python")
            if p not in sys.path and any(pathlib.Path(p, "tradefloor").glob("_core*.so")):
                sys.path.insert(0, p)
        import tradefloor as tf

        _TF["tf"] = tf
    return _TF["tf"]


def _params(tf, base, dials):
    return tf.ModelParams.from_preset(base, **dials) if dials else tf.ModelParams.from_preset(base)


def history(job):
    """One free history: the published VIX and the index level at every close."""
    engine_path, base, dials, seed, sessions, roster = job
    tf = _engine(engine_path)
    u = list(tf.Universe.random(roster[0], seed=roster[1]))
    shares = np.array([float(i.shares_outstanding) for i in u])
    e = tf.Engine(seed=seed, universe=u, model=_params(tf, base, dials))
    vix = np.empty(sessions)
    level = np.empty(sessions)
    listed = e.index_level is not None
    for d in range(sessions):
        e.run_days(1, record=False)
        vix[d] = float(e.macro_fields["vix"])
        level[d] = (e.index_level["level"] if listed
                    else float((np.frombuffer(e.prices(), "<f8") * shares).sum()))
    return seed, vix, level, listed


def _provenance(tf, base, dials):
    root = pathlib.Path(tf.__file__).resolve().parents[2]
    try:
        rev = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
                             text=True, timeout=10).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        rev = None
    return {"tradefloor": getattr(tf, "__version__", None), "engine_rev": rev,
            "fingerprint": _params(tf, base, dials).fingerprint}


def parse_seeds(text: str) -> list[int]:
    out = []
    for part in text.split(","):
        a, _, b = part.partition("-")
        out.extend(range(int(a), int(b or a) + 1))
    return out


def simulate(args) -> int:
    name, base, dials = parse_arm(args.arm)
    for kv in args.set or []:
        k, _, v = kv.partition("=")
        dials[k] = float(v)
    tf = _engine(args.engine)
    meta = {"arm": name, "base": base, "dials": dials, "roster": list(ROSTER),
            "years": args.years, **_provenance(tf, base, dials)}
    seeds = parse_seeds(args.seeds)
    jobs = [(args.engine, base, dials, s, args.years * YEAR, ROSTER) for s in seeds]
    t0 = time.time()
    out = []
    with ProcessPoolExecutor(args.workers) as pool:
        for k, r in enumerate(pool.map(history, jobs), 1):
            out.append(r)
            if k % max(1, len(jobs) // 10) == 0:
                print(f"{name}: {k}/{len(jobs)} histories, {time.time() - t0:.0f}s", file=sys.stderr, flush=True)
    meta["index"] = "index_level" if all(r[3] for r in out) else "roster_cap"
    meta["seconds"] = round(time.time() - t0, 1)
    np.savez_compressed(args.out, seeds=np.array([r[0] for r in out]),
                        vix=np.array([r[1] for r in out]), level=np.array([r[2] for r in out]),
                        meta=json.dumps(meta))
    print(f"{name}: {len(out)} histories x {args.years} years in {meta['seconds']}s -> {args.out}")
    return 0


# ================================================================ estimator

def windows(vix, level, burn, horizon=HORIZON):
    """Per window start after `burn`: ln(VIX / 20) and the forward log return."""
    vix = np.asarray(vix, float)
    lv = np.log(np.asarray(level, float))
    t = np.arange(burn, len(lv) - horizon)
    return np.log(vix[t] / VIX_CENTRE), lv[t + horizon] - lv[t]


def unit_moments(units, edges):
    """Per unit (a history, or a block of the tape) and bin: the count and
    the sums of x, r, r^2 and r^3, so a resample is a sum over units."""
    k = len(edges) + 1
    cuts = np.log(np.asarray(edges) / VIX_CENTRE)
    out = np.zeros((len(units), k, 5))
    for i, (x, r) in enumerate(units):
        b = np.digitize(x, cuts)
        for j, col in enumerate((np.ones_like(r), x, r, r * r, r ** 3)):
            out[i, :, j] = np.bincount(b, weights=col, minlength=k)
    return out


def skew_of(m):
    """Bin by bin from summed moments `[n, sx, s1, s2, s3]`: the mean x and
    the population skewness of r."""
    n = m[..., 0]
    with np.errstate(invalid="ignore", divide="ignore"):
        xbar = m[..., 1] / n
        mu = m[..., 2] / n
        v = m[..., 3] / n - mu * mu
        c3 = m[..., 4] / n - 3 * mu * m[..., 3] / n + 2 * mu ** 3
        return xbar, c3 / v ** 1.5


def wls(x, y, w, degree=1):
    """Weighted polynomial least squares over the finite points; the
    coefficients from the constant up."""
    ok = np.isfinite(x) & np.isfinite(y) & (w > 0)
    if ok.sum() <= degree:
        return np.full(degree + 1, np.nan)
    X = np.vander(x[ok], degree + 1, increasing=True)
    sw = np.sqrt(w[ok])
    return np.linalg.lstsq(X * sw[:, None], y[ok] * sw, rcond=None)[0]


def chi2_sf(q, k):
    """P(chi-squared with k degrees of freedom > q), by the regularised
    upper incomplete gamma (series below a + 1, continued fraction above)."""
    if k <= 0 or not math.isfinite(q):
        return float("nan")
    a, x = k / 2.0, q / 2.0
    if x <= 0:
        return 1.0
    lg = math.lgamma(a)
    if x < a + 1:
        term = total = 1.0 / a
        n = a
        for _ in range(500):
            n += 1
            term *= x / n
            total += term
            if term < total * 1e-15:
                break
        return 1.0 - total * math.exp(-x + a * math.log(x) - lg)
    b, c, d = x + 1 - a, 1e300, 1.0 / (x + 1 - a)
    h = d
    for i in range(1, 500):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = 1e-300 if abs(d) < 1e-300 else d
        c = b + an / c
        c = 1e-300 if abs(c) < 1e-300 else c
        d = 1.0 / d
        h *= d * c
        if abs(d * c - 1) < 1e-15:
            break
    return h * math.exp(-x + a * math.log(x) - lg)


def estimate(units, edges=EDGES, min_windows=300, min_units=5, boot=BOOT_B, seed=BOOT_SEED, weights="windows",
             fit_max=None):
    """The binned skewness, the line through it and their bootstrap
    standard errors, resampling `units` (each a pair of arrays x, r)."""
    m = unit_moments(units, edges)
    total = m.sum(0)
    xbar, sk = skew_of(total)
    present = (m[:, :, 0] > 0).sum(0)
    lo = (-np.inf,) + tuple(edges)
    hi = tuple(edges) + (np.inf,)
    fitted = (total[:, 0] >= min_windows) & (present >= min_units)
    if fit_max is not None:
        fitted &= np.array(hi) <= fit_max
    rng = np.random.default_rng(seed)
    n = len(units)
    draws = rng.integers(0, n, (boot, n))
    bx, bs = np.empty((boot, len(sk))), np.empty((boot, len(sk)))
    allm = m.sum(1)
    ball = np.empty(boot)
    for i, d in enumerate(draws):
        bx[i], bs[i] = skew_of(m[d].sum(0))
        ball[i] = skew_of(allm[d].sum(0))[1]
    se = np.nanstd(bs, axis=0, ddof=1)
    if weights == "se":
        w = np.where(fitted & (se > 0), 1.0 / se ** 2, 0.0)
    else:
        w = np.where(fitted, total[:, 0], 0.0)
    line = wls(xbar, sk, w)
    quad = wls(xbar, sk, w, degree=2)
    bline = np.array([wls(bx[i], bs[i], w) for i in range(boot)])
    bquad = np.array([wls(bx[i], bs[i], w, degree=2) for i in range(boot)])
    cov = np.cov(bline[np.isfinite(bline).all(1)].T)
    resid = (sk - (line[0] + line[1] * xbar)) / se
    chi2 = float(np.nansum(resid[fitted] ** 2))
    dof = int(fitted.sum()) - 2
    _, overall = skew_of(allm.sum(0))
    bins = []
    for k in range(len(sk)):
        n_k = int(total[k, 0])
        mu = total[k, 2] / n_k if n_k else float("nan")
        sd = math.sqrt(max(total[k, 3] / n_k - mu * mu, 0.0)) if n_k else float("nan")
        bins.append({"vix_low": lo[k], "vix_high": hi[k], "windows": n_k, "units": int(present[k]),
                     "mean_vix": VIX_CENTRE * math.exp(xbar[k]) if n_k else float("nan"),
                     "mean_ln": float(xbar[k]), "skew": float(sk[k]), "se": float(se[k]),
                     "line": float(line[0] + line[1] * xbar[k]) if n_k else float("nan"),
                     "z": float(resid[k]), "fitted": bool(fitted[k]),
                     "mean_ret": float(mu), "sd_ret": float(sd)})
    return {
        "units": n, "windows": int(total[:, 0].sum()), "boot": boot, "boot_seed": seed, "weights": weights,
        "min_windows": min_windows, "min_units": min_units, "fit_max": fit_max,
        "overall_skew": float(overall), "overall_se": float(np.nanstd(ball, ddof=1)),
        "a": float(line[0]), "se_a": float(math.sqrt(cov[0, 0])),
        "b": float(line[1]), "se_b": float(math.sqrt(cov[1, 1])),
        "corr_ab": float(cov[0, 1] / math.sqrt(cov[0, 0] * cov[1, 1])),
        "chi2": chi2, "dof": dof, "chi2_p": chi2_sf(chi2, dof),
        "quad_c": float(quad[2]), "se_quad_c": float(np.nanstd(bquad[:, 2], ddof=1)),
        "bins": bins,
    }


def report(title, est, extra=None):
    lines = [title,
             f"  {est['units']} units, {est['windows']} windows; all windows' skewness "
             f"{est['overall_skew']:+.3f} (se {est['overall_se']:.3f})",
             f"  skew = a + b ln(VIX/20): a {est['a']:+.3f} (se {est['se_a']:.3f}), "
             f"b {est['b']:+.3f} (se {est['se_b']:.3f}), corr(a, b) {est['corr_ab']:+.2f}; "
             f"bins weighted by {'windows' if est['weights'] == 'windows' else 'inverse variance'}",
             f"  line check: chi2 {est['chi2']:.1f} on {est['dof']} dof (p {est['chi2_p']:.3f}); "
             f"quadratic term {est['quad_c']:+.3f} (se {est['se_quad_c']:.3f})",
             "  VIX bin      windows units  mean VIX  ret sd   skew     se     line   z"
             + ("   SKEW S" if extra else "")]
    for k, b in enumerate(est["bins"]):
        lab = (f"<{b['vix_high']:g}" if b["vix_low"] == -np.inf else
               f">={b['vix_low']:g}" if b["vix_high"] == np.inf else f"{b['vix_low']:g}-{b['vix_high']:g}")
        if not b["windows"]:
            lines.append(f"  {lab:11s} {0:8d}")
            continue
        mark = " " if b["fitted"] else "*"
        lines.append(f"  {lab:11s} {b['windows']:8d} {b['units']:5d} {b['mean_vix']:9.2f} "
                     f"{100 * b['sd_ret']:6.2f}% {b['skew']:+7.3f} {b['se']:6.3f} {b['line']:+7.3f} "
                     f"{b['z']:+5.1f}{mark}" + (f" {extra[k]:+8.3f}" if extra else ""))
    lines.append("  * shown, not fitted (under the minimum windows or units"
                 + (f", or above VIX {est['fit_max']:g})" if est.get("fit_max") else ")"))
    return "\n".join(lines)


def _dump(path, obj):
    def clean(v):
        if isinstance(v, float) and not math.isfinite(v):
            return None
        if isinstance(v, dict):
            return {k: clean(x) for k, x in v.items()}
        if isinstance(v, list):
            return [clean(x) for x in v]
        return v
    pathlib.Path(path).write_text(json.dumps(clean(obj), indent=1) + "\n")


def edges_arg(text):
    return tuple(float(x) for x in text.split(",")) if text else EDGES


def load(paths):
    """Histories from one or more `simulate` files of the same arm: the same
    fingerprint, years, roster and index, no seed twice."""
    metas, seeds, vix, level = [], [], [], []
    for path in paths:
        z = np.load(path)
        metas.append(json.loads(str(z["meta"])))
        seeds += [int(s) for s in z["seeds"]]
        vix += list(z["vix"])
        level += list(z["level"])
    keys = {(m["fingerprint"], m["years"], tuple(m["roster"]), m["index"]) for m in metas}
    if len(keys) > 1:
        raise SystemExit(f"the files are not one arm (fingerprint, years, roster, index): {sorted(keys)}")
    if len(set(seeds)) < len(seeds):
        raise SystemExit("a seed appears in more than one file")
    meta = dict(metas[0], seeds=seeds, files=[str(p) for p in paths],
                engine_rev=sorted({m.get("engine_rev") or "?" for m in metas}))
    return meta, vix, level


def fit(args) -> int:
    meta, vix, level = load(args.npz)
    units = [windows(v, lv, args.burn) for v, lv in zip(vix, level, strict=True)]
    est = estimate(units, edges_arg(args.edges), args.min_windows, args.min_units, args.boot, weights=args.weights,
                   fit_max=args.fit_max)
    est.update(meta=meta, burn=args.burn, horizon=HORIZON, resample="history")
    revs = ", ".join(r[:8] for r in meta["engine_rev"])
    print(report(f"{meta['arm']} on {meta['base']} ({meta['index']}; engine {revs}; "
                 f"burn {args.burn}, {meta['years']} years a history)", est))
    if args.json:
        _dump(args.json, est)
    return 0


# ===================================================================== tape

def _open(path):
    p = pathlib.Path(path)
    raw = p.read_bytes()
    if p.suffix == ".gz":
        raw = gzip.decompress(raw)
    return csv.DictReader(io.StringIO(raw.decode()))


def _iso(d):
    if "/" in d:
        m, day, y = d.split("/")
        return f"{y}-{int(m):02d}-{int(day):02d}"
    return d[:10]


def read_series(path, column):
    out = {}
    for row in _open(path):
        row = {k.strip().lower(): v for k, v in row.items()}
        v = row.get(column, "")
        try:
            x = float(v)
        except ValueError:
            continue
        if math.isfinite(x) and x > 0:
            out[_iso(row["date"])] = x
    return out


def tape(args) -> int:
    idx = read_series(args.index, "close")
    vix = read_series(args.vix, "close")
    dates = sorted(d for d in idx if d in vix and args.start <= d <= (args.end or "9999"))
    level = np.array([idx[d] for d in dates])
    v = np.array([vix[d] for d in dates])
    x, r = windows(v, level, 0)
    starts = dates[:len(x)]
    blocks = [(x[i:i + args.block], r[i:i + args.block]) for i in range(0, len(x), args.block)]
    est = estimate(blocks, edges_arg(args.edges), args.min_windows, args.min_units, args.boot, weights=args.weights,
                   fit_max=args.fit_max)
    est.update(index=str(args.index), vix_file=str(args.vix), first=starts[0], last_start=starts[-1],
               last_close=dates[-1], horizon=HORIZON, resample=f"blocks of {args.block} window starts")
    extra = None
    if args.skew:
        sk = read_series(args.skew, "skew")
        cuts = np.log(np.asarray(edges_arg(args.edges)) / VIX_CENTRE)
        s = np.array([(100.0 - sk[d]) / 10.0 if d in sk else np.nan for d in starts])
        b = np.digitize(x, cuts)
        extra = [float(np.nanmean(s[b == k])) if np.isfinite(s[b == k]).any() else float("nan")
                 for k in range(len(est["bins"]))]
        for k, e in enumerate(extra):
            est["bins"][k]["skew_index_s"] = e
    print(report(f"tape: {pathlib.Path(args.index).name} and {pathlib.Path(args.vix).name}, "
                 f"window starts {starts[0]} to {starts[-1]}", est, extra))
    if args.json:
        _dump(args.json, est)
    return 0


# ===================================================================== main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("simulate", help="run free histories and keep the VIX and the index")
    s.add_argument("--arm", required=True, help="a preset, NAME@BASE:dial=v,... or @FILE")
    s.add_argument("--set", action="append", help="dial=v on top of the arm (repeatable)")
    s.add_argument("--seeds", default="201-240", help="e.g. 201-260 or 201,205")
    s.add_argument("--years", type=int, default=21)
    s.add_argument("--workers", type=int, default=4)
    s.add_argument("--engine", help="an engine checkout whose python/ holds a built _core")
    s.add_argument("--out", required=True)
    for name, helptext in (("fit", "fit the line on simulated histories"),
                           ("tape", "the same estimator on a real index and VIX")):
        p = sub.add_parser(name, help=helptext)
        if name == "fit":
            p.add_argument("npz", nargs="+", help="simulate files of one arm, pooled")
            p.add_argument("--burn", type=int, default=BURN)
        else:
            p.add_argument("--index", required=True, help="CSV with date and close (e.g. ^GSPC)")
            p.add_argument("--vix", required=True, help="CSV with date and close (Cboe VIX_History)")
            p.add_argument("--skew", help="Cboe SKEW_History CSV, shown beside each bin")
            p.add_argument("--start", default="1990-01-02")
            p.add_argument("--end")
            p.add_argument("--block", type=int, default=YEAR, help="window starts a bootstrap block")
        p.add_argument("--edges", help="VIX bin edges, comma-separated (default %s)"
                       % ",".join(f"{e:g}" for e in EDGES))
        p.add_argument("--min-windows", type=int, default=300)
        p.add_argument("--min-units", type=int, default=5)
        p.add_argument("--boot", type=int, default=BOOT_B)
        p.add_argument("--fit-max", type=float, help="fit only bins whose upper edge is at or below this VIX")
        p.add_argument("--weights", choices=("windows", "se"), default="windows",
                       help="a bin's weight in the line: its windows, or its inverse bootstrap variance")
        p.add_argument("--json")
    a = ap.parse_args(argv)
    return {"simulate": simulate, "fit": fit, "tape": tape}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
