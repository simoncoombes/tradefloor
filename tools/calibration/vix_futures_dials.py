"""Derive the VIX futures' intraday loading, the three derived dials.

    python tools/calibration/vix_futures_dials.py derive
    python tools/calibration/vix_futures_dials.py derive --law '{"vix_fear_uptake": 0.25, "vix_fear_half_life": 6.0}'

Within a session a VIX future `m` sessions from expiry (counted from
tonight's close) moves by `lambda(m)` times the live VIX's surprise: the
live VIX less the forecast's expectation of tonight's published VIX. The
right `lambda(m)` is what tonight's close will do to the contract's expected
settlement given that surprise, so it is fitted as the projection of a
close's revision of the expected published VIX `m - 1` closes on, on the
close's own surprise:

    revision(s, m) = E_{s+1}[VIX after s + m] - E_s[VIX after s + m]
    surprise(s)    = VIX_{s+1} - E_s[VIX_{s+1}]
    lambda(m)      = sum revision * surprise / sum surprise^2

over every close after the first year of every history, horizon by horizon
(m = 2 to 127; lambda(1) is 1, the contract settling on tonight's VIX). The
form `w 0.5^((m - 1) / H1) + (1 - w) 0.5^((m - 1) / H2)` is then fitted to
those slopes by least squares: for each pair of half-lives on a grid the
share `w` is the linear least-squares one, clipped to [0, 1].
`futures_vix_live_fast_share` is `w`, `futures_vix_live_fast_half_life`
`H1` and `futures_vix_live_slow_half_life` `H2`.

The histories are pt-v21 (or `--preset`) with the forecast at 130 sessions
and its derived dials as `forecast_dials.py derive` fits them on pt-v21,
on `Universe.random(40, seed=111)`, with `--law`'s dials on top: the loading
is the forecast's own response, so a candidate law that changes the VIX's
gets its own. The VIX futures themselves are not listed: nothing here reads
them.
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor

#: The forecast the loading is the response of, as forecast_dials.py derive
#: fits its dials on pt-v21.
SWITCHES = dict(index_level_listed=1.0, forecast_horizon_sessions=130.0,
                forecast_vix_dispersion=0.3, forecast_vix_dispersion_half_life=31.5,
                forecast_vix_dispersion_skew=0.81, forecast_policy_shadow_discount=0.36,
                forecast_policy_persistence=0.6, forecast_policy_reversion=0.05,
                forecast_policy_neutral=1.48)
#: The horizons fitted: m = 2 to 127, the furthest a listed contract reads.
HORIZONS = range(2, 128)
BURN = 252


def history(args):
    """One seed's published VIX and forecast path at every close."""
    import numpy as np
    import tradefloor as tf

    seed, sessions, law, preset = args
    engine = tf.Engine(seed=seed, universe=tf.Universe.random(40, seed=111),
                       model=tf.ModelParams.from_preset(preset, **{**SWITCHES, **law}))
    pub, path = [], []
    for _ in range(sessions):
        engine.run_days(1)
        pub.append(engine.macro_fields["vix"])
        path.append(engine.forecast()["vix"])
    return np.asarray(pub, float), np.asarray(path, float)


def slopes(histories):
    """lambda(m) for m in HORIZONS: the pooled projection of revisions on
    surprises."""
    import numpy as np

    num = {m: 0.0 for m in HORIZONS}
    den = {m: 0.0 for m in HORIZONS}
    for pub, path in histories:
        surprise = pub[BURN + 1:] - path[BURN:-1, 0]
        for m in HORIZONS:
            # E_{s+1} of the VIX after s + m closes is path[s+1][m - 2];
            # E_s of the same, path[s][m - 1].
            revision = path[BURN + 1:, m - 2] - path[BURN:-1, m - 1]
            num[m] += float((surprise * revision).sum())
            den[m] += float((surprise * surprise).sum())
    return {m: num[m] / den[m] for m in HORIZONS}


def fit(lam):
    """The two-part form's share and half-lives, by least squares."""
    import numpy as np

    m = np.array(list(lam), float)
    y = np.array(list(lam.values()), float)
    best = None
    grid1 = np.round(np.arange(0.05, 10.0, 0.01), 2)
    grid2 = np.round(np.arange(5.0, 200.0, 0.1), 1)
    for h1 in grid1[::5]:
        a = 0.5 ** ((m - 1) / h1)
        for h2 in grid2[::5]:
            b = 0.5 ** ((m - 1) / h2)
            d = a - b
            w = float(np.clip(((y - b) * d).sum() / (d * d).sum(), 0.0, 1.0)) if (d * d).sum() > 0 else 0.0
            sse = float(((w * a + (1 - w) * b - y) ** 2).sum())
            if best is None or sse < best[0]:
                best = (sse, w, h1, h2)
    # Refine on the full grid around the coarse best.
    _, _, c1, c2 = best
    for h1 in grid1[(grid1 >= c1 - 0.06) & (grid1 <= c1 + 0.06)]:
        a = 0.5 ** ((m - 1) / h1)
        for h2 in grid2[(grid2 >= c2 - 0.6) & (grid2 <= c2 + 0.6)]:
            b = 0.5 ** ((m - 1) / h2)
            d = a - b
            w = float(np.clip(((y - b) * d).sum() / (d * d).sum(), 0.0, 1.0)) if (d * d).sum() > 0 else 0.0
            sse = float(((w * a + (1 - w) * b - y) ** 2).sum())
            if sse < best[0]:
                best = (sse, w, h1, h2)
    sse, w, h1, h2 = best
    worst = float(np.abs(w * 0.5 ** ((m - 1) / h1) + (1 - w) * 0.5 ** ((m - 1) / h2) - y).max())
    return round(w, 3), float(h1), float(h2), worst


def main(argv=None):
    parser = argparse.ArgumentParser(description="Derive the VIX futures' intraday loading on held-out histories.")
    parser.add_argument("mode", choices=("derive",))
    parser.add_argument("--seeds", default="3001,3040", help="first,last seed (inclusive), default 3001,3040")
    parser.add_argument("--sessions", type=int, default=2000)
    parser.add_argument("--preset", default="pt-v21")
    parser.add_argument("--law", default="{}",
                        help="a JSON object of model dials set on the preset, for a law no preset ships")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args(argv)
    lo, hi = (int(x) for x in args.seeds.split(","))
    law = json.loads(args.law)
    with ProcessPoolExecutor(args.workers) as pool:
        histories = list(pool.map(history, [(s, args.sessions, law, args.preset) for s in range(lo, hi + 1)]))
    lam = slopes(histories)
    w, h1, h2, worst = fit(lam)
    print("lambda(m):", ", ".join(f"m{m} {lam[m]:.3f}" for m in (2, 3, 6, 12, 22, 43, 64, 106, 127)))
    print(f"fit's largest miss on the slopes: {worst:.3f}")
    print(json.dumps(dict(futures_vix_live_fast_share=w, futures_vix_live_fast_half_life=h1,
                          futures_vix_live_slow_half_life=h2)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
