"""Measure the realism panel of the INSTALLED pretium wheel, as JSON.

This is the measurement half of the `MARKET_FACTOR_SIGMA` calibration. It
runs in a fresh interpreter against whatever `pretium` wheel is installed,
which is the point: the sweep driver rebuilds and reinstalls the wheel
between calls, and a fresh process cannot be holding a stale module.

The panel is `pretium.facts.measure` — the library's own instrument, not a
reimplementation — at the published method: `Universe.random(40, seed=111)`,
252 days, per seed. Two supplements ride along:

  - per-instrument median annualised volatility, because the pooled figure
    `facts.measure` reports is dominated by the high-volatility tail of a
    deliberately dispersed universe, and the real-market 15-35%% band refers
    to individual equities. Both are reported; the calibration argues from
    both.
  - cross-sectional correlation under pinned VIX (15 / 45 / 65 by default),
    because the crisis-correlation blend in `tick.rs` acts above VIX 40 and
    whether it does anything observable is exactly what a market-factor
    recalibration should change. Same method as finding 7 in the design
    findings, via `pin_macro` re-applied before each open.

`--fingerprint-only` prints a hash of a tiny fixed run and exits. The sweep
driver uses it as a stale-wheel guard: every distinct `MARKET_FACTOR_SIGMA`
must produce a distinct trajectory, so two sweep points reporting the same
fingerprint mean the rebuild or reinstall silently failed and the sweep
aborts rather than measuring one constant twice under two labels.

Run it standalone to measure the current build:

    .venv/bin/python tools/calibration/measure_panel.py --seeds 1,2,3,4,5,6
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import struct
import sys


def trajectory_fingerprint() -> str:
    """Hash the closes of a small fixed run, as raw f64 bytes.

    Any change to the price path — a different `MARKET_FACTOR_SIGMA`, a
    different RNG stream, anything — changes this. Raw IEEE-754 bytes rather
    than decimal formatting, for the reason the known-answer test documents:
    repr(float) can differ for reasons that have nothing to do with the
    simulation.
    """
    import pretium

    universe = pretium.Universe.random(8, seed=7)
    engine = pretium.Engine(seed=7, universe=universe)
    buf = bytearray()
    for day in range(2):
        engine.open_market()
        engine.run_session(9, 30, 3, 390)
        engine.record(day)
        engine.close_market()
    import pyarrow as pa

    bars = pa.table(engine.bars(grain="day")).to_pydict()
    for close in bars["close"]:
        buf += struct.pack(">d", close)
    return hashlib.sha256(bytes(buf)).hexdigest()[:16]


class PinVix:
    """A `facts.measure` scenario that pins VIX before every open.

    The endogenous macro chain advances at each close; re-pinning before the
    next open holds the level for the whole run, which is the finding-7
    method for measuring correlation at a chosen VIX.
    """

    def __init__(self, vix: float) -> None:
        self.vix = vix

    def apply(self, engine, day: int) -> None:
        engine.pin_macro(vix=self.vix)


def per_instrument_vol_median(seed: int, universe, days: int) -> float:
    """Median across instruments of annualised daily-return volatility.

    Supplementary to the pooled `annualised_vol_pct` from `facts.measure`:
    the pooled second moment is dominated by the high-volatility names of a
    dispersed universe, and the median is the statistic the era-boundary
    volume fix was reported against.
    """
    import pyarrow as pa

    import pretium
    from pretium.facts import _daily_series, _log_returns

    engine = pretium.Engine(seed=seed, universe=universe)
    for day in range(days):
        engine.open_market()
        engine.run_session(9, 30, 3, 390)
        engine.record(day)
        engine.close_market()
    bars = pa.table(engine.bars(grain="day")).to_pydict()
    vols = []
    for rows in _daily_series(bars).values():
        returns = _log_returns([row[1] for row in rows])
        if len(returns) >= 30:
            vols.append(statistics.pstdev(returns) * math.sqrt(252) * 100.0)
    return statistics.median(vols)


def measure_all(
    seeds: list[int],
    days: int,
    universe_n: int,
    universe_seed: int,
    pin_vix: list[float],
    pin_days: int,
) -> dict:
    import pretium
    from pretium.facts import measure

    universe = pretium.Universe.random(universe_n, seed=universe_seed)

    panels = {}
    for seed in seeds:
        facts = measure(seed=seed, universe=universe, days=days)
        facts["per_instrument_vol_median_pct"] = per_instrument_vol_median(
            seed, universe, days
        )
        panels[str(seed)] = facts

    pinned = {}
    for vix in pin_vix:
        rows = {}
        for seed in seeds:
            facts = measure(
                seed=seed,
                universe=universe,
                days=pin_days,
                scenario=PinVix(vix),
            )
            rows[str(seed)] = {
                "cross_sectional_corr": facts["cross_sectional_corr"],
                "annualised_vol_pct": facts["annualised_vol_pct"],
            }
        pinned[str(vix)] = rows

    return {
        "method": {
            "universe": f"Universe.random({universe_n}, seed={universe_seed})",
            "days": days,
            "seeds": seeds,
            "pin_vix": pin_vix,
            "pin_days": pin_days,
            "panel": "pretium.facts.measure, per seed",
        },
        "trajectory_fingerprint": trajectory_fingerprint(),
        "panels": panels,
        "pinned_vix": pinned,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--seeds", default="1,2,3,4,5,6")
    parser.add_argument("--days", type=int, default=252)
    parser.add_argument("--universe-n", type=int, default=40)
    parser.add_argument("--universe-seed", type=int, default=111)
    parser.add_argument("--pin-vix", default="15,45,65")
    parser.add_argument("--pin-days", type=int, default=120)
    parser.add_argument(
        "--out", default="-", help="write JSON here, '-' for stdout"
    )
    parser.add_argument(
        "--fingerprint-only",
        action="store_true",
        help="print the trajectory fingerprint of the installed wheel, nothing else",
    )
    args = parser.parse_args()

    if args.fingerprint_only:
        print(trajectory_fingerprint())
        return 0

    seeds = [int(s) for s in args.seeds.split(",") if s]
    pin_vix = [float(v) for v in args.pin_vix.split(",") if v]
    result = measure_all(
        seeds, args.days, args.universe_n, args.universe_seed, pin_vix, args.pin_days
    )
    text = json.dumps(result, indent=1, sort_keys=True)
    if args.out == "-":
        print(text)
    else:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
