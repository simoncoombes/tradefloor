"""Derive the margin's tail allowance, and read row MG1.

    python tools/calibration/margin_dials.py derive
    python tools/calibration/margin_dials.py measure --tail 1.3 --seeds 8601,8616

A listed contract's initial margin is its multiplier times `z * t * sigma`
(`margin_scan_coverage`, `margin_scan_tail`): `sigma` its own one-session
sd, an exponentially weighted mean of its squared close-to-close mark
changes, and `z` the normal quantile for the coverage. Row MG1 reads the
share of one-session moves of each root's front contract, times its
multiplier, past the initial margin the previous close set, pooled over
roots, sessions and histories, and asks for 0.5 to 1.5 per cent about the 99
per cent standard.

`derive` runs held-out histories with every phase 1 contract listed, the
coverage at 0.99 and no allowance, and returns the allowance `t` that puts
MG1 at the coverage's complement: the pooled quantile, at the coverage, of
each front move over the `z * sigma` the previous close's margin was set
from. `measure` reads MG1 at a given allowance on fresh seeds, and by root.

The histories are pt-v21 on `Universe.random(40, seed=111)` with the
phase 1 switches and the forecast's derived dials as their own tools fit
them on pt-v21, and `--law`'s dials on top.
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor

BASE = dict(index_level_listed=1.0, vix_intraday_live=1.0, forecast_horizon_sessions=252.0,
            forecast_vix_dispersion=0.3, forecast_vix_dispersion_half_life=31.5,
            forecast_vix_dispersion_skew=0.81, forecast_policy_shadow_discount=0.36,
            forecast_policy_persistence=0.6, forecast_policy_reversion=0.05,
            forecast_policy_neutral=1.48,
            futures_index_listed=1.0, basis_sd=3.753, basis_persistence=0.429,
            futures_vix_listed=1.0, futures_vix_live_fast_share=0.713,
            futures_vix_live_fast_half_life=5.96, futures_vix_live_slow_half_life=71.7,
            futures_rates_listed=1.0, futures_oil_listed=1.0, margin_scan_coverage=0.99)
ROOTS = ("IDX", "VIX", "FF", "TR3", "OIL")
BURN = 252


def history(args):
    """Per root, each front contract's one-session move over the previous
    close's margin, both in dollars a contract."""
    import tradefloor as tf

    seed, sessions, dials, preset = args
    engine = tf.Engine(seed=seed, universe=tf.Universe.random(40, seed=111),
                       model=tf.ModelParams.from_preset(preset, **{**BASE, **dials}))
    prev, out = {}, {r: [] for r in ROOTS}
    for s in range(sessions):
        engine.run_days(1)
        now = {}
        for c in engine.contracts():
            if c["root"] in now:
                continue
            q = engine.quote(c["symbol"])
            now[c["root"]] = (c["symbol"], q["mark"], q["initial_margin"], q["multiplier"])
        if s > BURN:
            for root, (sym, mark, _, mult) in now.items():
                p = prev.get(root)
                if p and p[0] == sym and p[2]:
                    out[root].append((abs(mark - p[1]) * mult, p[2]))
        prev = now
    return out


def run(seeds, sessions, dials, preset, workers):
    with ProcessPoolExecutor(workers) as pool:
        return list(pool.map(history, [(s, sessions, dials, preset) for s in seeds]))


def pooled(histories):
    return {r: [x for h in histories for x in h[r]] for r in ROOTS}


def main(argv=None):
    import numpy as np

    parser = argparse.ArgumentParser(description="Derive the margin's tail allowance, or read MG1.")
    parser.add_argument("mode", choices=("derive", "measure"))
    parser.add_argument("--seeds", default=None, help="first,last seed; derive 8501,8524, measure 8601,8616")
    parser.add_argument("--sessions", type=int, default=1500)
    parser.add_argument("--tail", type=float, default=0.0, help="margin_scan_tail for measure")
    parser.add_argument("--preset", default="pt-v21")
    parser.add_argument("--law", default="{}")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args(argv)
    lo, hi = (int(x) for x in (args.seeds or ("8501,8524" if args.mode == "derive" else "8601,8616")).split(","))
    law = json.loads(args.law)
    dials = dict(law, margin_scan_tail=args.tail if args.mode == "measure" else 0.0)
    moves = pooled(run(range(lo, hi + 1), args.sessions, dials, args.preset, args.workers))
    if args.mode == "derive":
        # With no allowance the margin is z * sigma, so move / margin is the
        # move in units of z * sigma, and the allowance that leaves the
        # coverage's complement past it is that ratio's quantile.
        ratios = np.array([m / b for r in ROOTS for m, b in moves[r]])
        tail = float(np.quantile(ratios, BASE["margin_scan_coverage"]))
        for r in ROOTS:
            x = np.array([m / b for m, b in moves[r]])
            print(f"{r:4s} {len(x):6d} front moves; share past z sigma {np.mean(x > 1):.4f}, "
                  f"past the allowance {np.mean(x > tail):.4f}")
        print(json.dumps(dict(margin_scan_tail=round(tail, 3))))
    else:
        past = {r: np.mean([m > b for m, b in moves[r]]) for r in ROOTS if moves[r]}
        n = sum(len(moves[r]) for r in ROOTS)
        total = sum(sum(m > b for m, b in moves[r]) for r in ROOTS) / n
        print(" ".join(f"{r} {v:.4f}" for r, v in past.items()))
        print(f"MG1 pooled over {n} front moves: {total:.4f} (band 0.005 to 0.015)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
