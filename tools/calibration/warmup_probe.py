"""Which engine states are still relaxing at session 252?

The brief for the market-side warm-up (`level-sigma-horizon.md`) names six
candidates -- the factor's fast and slow variance components, the per-name
GARCH variances, the sector variance state, the jump excitation, the
smoothed VIX and the volume states -- and asks which of them are actually
still moving a year into a run rather than warming everything by reflex.

This records every one of them, once per session, across seeds, and reports
each state's cross-seed mean and dispersion by 63-session block.  A state
that is stationary from session one reads flat in both columns; a state
still relaxing reads a trend.  The control arm at
`market_vol_level_sigma` 0.0 separates "relaxing because of the level" from
"relaxing anyway".

Usage:

    python tools/calibration/warmup_probe.py --seeds 32 --days 504 \
        --sigma 0.085 --sigma 0.0 --workers 4 --out probe.json

Output is JSON so the reader can be re-run without the engine.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np

BLOCK = 63

# The states recorded per session.  `market_variance` is the six-tuple
# `(variance, day_factor, fast, slow, prev_day_factor, smoothed_vix)` that
# `MarketVarianceState::snapshot` returns; the rest are top-level snapshot
# keys or per-name byte columns reduced to one number.
FIELDS = [
    "log_factor_var",     # log of the MIXTURE the tick draws against
    "log_fast_var",       # the fast component's own level
    "log_slow_var",       # the slow component's own level
    "market_vol_log_level",
    "vix",
    "smoothed_vix",
    "log_garch_mean",     # mean over names of the per-name GARCH variance
    "log_sector_var_mean",
    "jump_excitation_mean",
    "volume_state",
    "volume_idio_rms",
    "universe_stress",
]


def _f8(buf: bytes) -> np.ndarray:
    return np.frombuffer(buf, dtype="<f8")


def _row(engine) -> list[float]:
    s = engine.state_snapshot()
    mv = s["market_variance"]
    garch = _f8(s["columns"]["garch_variance"])
    sect = _f8(s["sector_variance"])
    excite = _f8(s["jump_excitation"])
    vidio = _f8(s["volume_idio"])
    smoothed = mv[5]
    return [
        math.log(mv[0]) if mv[0] > 0 else float("nan"),
        math.log(mv[2]) if mv[2] > 0 else float("nan"),
        math.log(mv[3]) if mv[3] > 0 else float("nan"),
        s["market_vol_log_level"],
        s["economy"]["vix"],
        smoothed if smoothed >= 0.0 else float("nan"),
        float(np.log(np.maximum(garch, 1e-300)).mean()),
        float(np.log(np.maximum(sect, 1e-300)).mean()) if sect.size else float("nan"),
        float(excite.mean()) if excite.size else float("nan"),
        s["volume_state"],
        float(np.sqrt((vidio ** 2).mean())) if vidio.size else float("nan"),
        s["universe_stress"],
    ]


def _one(job) -> tuple[float, int, list[list[float]]]:
    sigma, seed, days, names, preset, burn = job
    from tradefloor import _core

    universe = _core.random_instruments(names, seed=seed)
    model = _core.ModelParams.from_preset(
        preset, market_vol_level_sigma=sigma, market_burn_in_sessions=burn)
    engine = _core.Engine(seed=seed, universe=universe, model=model)
    out = []
    for _ in range(days):
        engine.run_days(1)
        out.append(_row(engine))
    return sigma, seed, out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=32)
    ap.add_argument("--seed0", type=int, default=1000)
    ap.add_argument("--days", type=int, default=504)
    ap.add_argument("--names", type=int, default=60)
    ap.add_argument("--preset", default="pt-v19")
    ap.add_argument("--sigma", type=float, action="append", default=None)
    ap.add_argument("--burn", type=float, default=0.0,
                    help="market_burn_in_sessions for every arm")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", default="warmup_probe.json")
    args = ap.parse_args(argv)
    sigmas = args.sigma if args.sigma else [0.085, 0.0]

    jobs = [
        (sigma, args.seed0 + i, args.days, args.names, args.preset, args.burn)
        for sigma in sigmas
        for i in range(args.seeds)
    ]
    panels: dict[str, list[list[list[float]]]] = {f"{s:g}": [] for s in sigmas}
    workers = max(1, min(args.workers, 4))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for sigma, seed, rows in pool.map(_one, jobs):
            panels[f"{sigma:g}"].append(rows)
            print(f"  done sigma={sigma:g} seed={seed}", file=sys.stderr, flush=True)

    payload = {
        "fields": FIELDS,
        "days": args.days,
        "names": args.names,
        "preset": args.preset,
        "seeds": args.seeds,
        "seed0": args.seed0,
        "burn": args.burn,
        "panels": panels,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    print(f"wrote {args.out}", file=sys.stderr)
    report(payload)
    return 0


def report(payload: dict) -> None:
    fields = payload["fields"]
    days = payload["days"]
    nblocks = days // BLOCK
    for sigma, panel in payload["panels"].items():
        arr = np.array(panel, dtype=float)  # (seed, day, field)
        print()
        print(f"=== market_vol_level_sigma {sigma}, {arr.shape[0]} seeds, "
              f"{arr.shape[1]} sessions ===")
        head = "  ".join(f"b{b + 1}" for b in range(nblocks))
        print(f"{'field':<22} {'stat':<5} {head}")
        for fi, name in enumerate(fields):
            col = arr[:, :, fi]
            if not np.isfinite(col).any():
                continue
            means, sds = [], []
            for b in range(nblocks):
                blk = col[:, b * BLOCK:(b + 1) * BLOCK]
                # one number per seed per block, then across seeds
                per_seed = np.nanmean(blk, axis=1)
                means.append(np.nanmean(per_seed))
                sds.append(np.nanstd(per_seed, ddof=1))
            fm = "  ".join(f"{m:+.4f}" for m in means)
            fs = "  ".join(f"{s:.4f}" for s in sds)
            print(f"{name:<22} {'mean':<5} {fm}")
            print(f"{'':<22} {'sd':<5} {fs}")


def derive_recovery(reps: int = 40000, seed: int = 7) -> None:
    """How much of the stationary window statistic each start recovers.

    DERIVED, not measured: this runs the LINEARISED recursion, not the
    engine.  `log v` of a variance component tracks
    `(1 - p) sum_k p^k log L_{t-k}` to first order, so the three starts --
    the infinite past, the cold `log v = log base`, and this warm-up's
    conditional mean `((1 - p) / (1 - p phi)) log L_1` -- differ only in
    their initial condition and the rest is one linear-Gaussian
    calculation.

    What it answers is the question the design turns on: a conditional mean
    cannot carry the conditional SPREAD of the state given the opening
    level, so is the mean enough?  The window statistic is a 252-session
    AVERAGE, which damps a decaying initial-condition error and does not
    damp a persistent one, and the answer is that it is.

        python tools/calibration/warmup_probe.py derive
    """
    import numpy as np

    rng = np.random.default_rng(seed)
    phi, sig = 0.9977, 0.085
    s2 = sig * sig / (1.0 - phi * phi)
    p_fast, p_slow, weight = 0.9790, 0.9913, 0.35
    horizon, burn = 504, 6000

    z = rng.standard_normal((reps, burn + horizon + 1))
    level = np.empty_like(z)
    level[:, 0] = np.sqrt(s2) * z[:, 0]
    for t in range(1, burn + horizon + 1):
        level[:, t] = phi * level[:, t - 1] + sig * z[:, t]

    def forward(p, x0, start, steps):
        out = np.empty((reps, steps))
        x = x0.copy()
        for t in range(steps):
            x = (1.0 - p) * level[:, start + t] + p * x
            out[:, t] = x
        return out

    stationary = {p: forward(p, np.zeros(reps), 0, burn + 1)[:, -1]
                  for p in (p_fast, p_slow)}
    opening = level[:, burn + 1]
    arms = {
        "stationary": (stationary[p_fast], stationary[p_slow]),
        "cold": (np.zeros(reps), np.zeros(reps)),
        "conditional mean": (
            (1 - p_fast) / (1 - p_fast * phi) * opening,
            (1 - p_slow) / (1 - p_slow * phi) * opening,
        ),
    }
    print(f"phi={phi} sigma={sig} sd(log L)={np.sqrt(s2):.4f}  "
          f"{reps} replications")
    print(f"{'start':<18} {'var h1':>9} {'var h2':>9} {'h2/h1':>7} "
          f"{'h1 / stationary h1':>20}")
    ref = None
    for name, (x0f, x0s) in arms.items():
        mix = ((1 - weight) * forward(p_fast, x0f, burn + 1, horizon)
               + weight * forward(p_slow, x0s, burn + 1, horizon))
        h1 = mix[:, :horizon // 2].mean(1).var()
        h2 = mix[:, horizon // 2:].mean(1).var()
        if ref is None:
            ref = h1
        print(f"{name:<18} {h1:9.4f} {h2:9.4f} {h2 / h1:7.3f} "
              f"{h1 / ref:20.3f}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "derive":
        derive_recovery()
        raise SystemExit(0)
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        with open(sys.argv[2], encoding="utf-8") as fh:
            report(json.load(fh))
        raise SystemExit(0)
    raise SystemExit(main())
