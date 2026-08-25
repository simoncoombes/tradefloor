"""Measure the endogenous VIX distribution of the INSTALLED pretium wheel.

The crisis-gated behaviours — the correlation blend in `tick.rs` and the
gold/USD crisis terms in `economy/daily.rs` — trigger on VIX levels that
were inherited from the reference as inline literals (40 and 30). Whether
those levels are reachable is a property of the endogenous VIX process,
so re-siting the triggers starts with measuring where that process
actually goes. This script is that measurement.

Same discipline as `measure_panel.py`: the published-method universe
(`Universe.random(40, seed=111)`), full 390-tick sessions so the
return->VIX feedback is live, and the macro chain endogenous — no pins,
and no shocks, which the Python surface never supplies
(`python_engine.rs`: `active_shocks: &[]`).

Two envelopes, because the engine always starts in expansion and the
slow Weibull cycle phases (expansion scale 36 months) mean a 252-day
window barely samples contraction:

  --mode short   48 seeds x 252 days: what the published realism-method
                 window sees.
  --mode long    12 seeds x 2520 days: ten years, so contraction and
                 trough — where the VIX target sits at 25 and 22 — carry
                 their steady-state weight.

  --mode pinned-corr
                 cross-sectional correlation under pinned VIX at levels
                 tracing the crisis blend's weight curve
                 w = min(0.8, (vix - 40) / 30): the finding-7 /
                 `measure_panel.py` method (`facts.measure`, re-pinning
                 before every open, 120 days, six seeds). Controls at
                 25/30/39 sit above calm but below the trigger, so any
                 movement there is a non-blend VIX effect.

Each mode writes one JSON carrying its method and the trajectory
fingerprint of the wheel it measured (see `measure_panel.py` for why the
fingerprint, not the git rev, identifies the engine).
"""

from __future__ import annotations

import argparse
import json
import sys


def fingerprint() -> str:
    sys.path.insert(0, __file__.rsplit("/", 1)[0])
    from measure_panel import trajectory_fingerprint

    return trajectory_fingerprint()


def endogenous(seeds: list[int], days: int) -> dict[str, list[tuple[float, str]]]:
    import pretium

    universe = pretium.Universe.random(40, seed=111)
    out: dict[str, list[tuple[float, str]]] = {}
    for seed in seeds:
        engine = pretium.Engine(seed=seed, universe=universe)
        rows = []
        for _ in range(days):
            engine.open_market()
            engine.run_session(9, 30, 3, 390)
            engine.close_market()
            macro = engine.macro_state
            rows.append((macro.vix, macro.cycle))
        out[str(seed)] = rows
        print(f"seed {seed} done ({days} days)", file=sys.stderr, flush=True)
    return out


class PinVix:
    def __init__(self, vix: float) -> None:
        self.vix = vix

    def apply(self, engine, day: int) -> None:
        engine.pin_macro(vix=self.vix)


def pinned_corr(pins: list[float], seeds: list[int], days: int) -> dict:
    import pretium
    from pretium.facts import measure

    universe = pretium.Universe.random(40, seed=111)
    out: dict[str, dict[str, float]] = {}
    for vix in pins:
        rows = {}
        for seed in seeds:
            facts = measure(seed=seed, universe=universe, days=days, scenario=PinVix(vix))
            rows[str(seed)] = facts["cross_sectional_corr"]
        out[str(vix)] = rows
        print(f"pin {vix} done", file=sys.stderr, flush=True)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--mode", choices=["short", "long", "pinned-corr"], required=True)
    parser.add_argument("--seeds", default=None, help="comma-separated; mode default otherwise")
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    if args.mode == "short":
        seeds = [int(s) for s in args.seeds.split(",")] if args.seeds else list(range(1, 49))
        days = args.days or 252
        payload = {"daily_vix_and_phase": endogenous(seeds, days)}
    elif args.mode == "long":
        seeds = [int(s) for s in args.seeds.split(",")] if args.seeds else list(range(1, 13))
        days = args.days or 2520
        payload = {"daily_vix_and_phase": endogenous(seeds, days)}
    else:
        seeds = [int(s) for s in args.seeds.split(",")] if args.seeds else [1, 2, 3, 4, 5, 6]
        days = args.days or 120
        pins = [15.0, 25.0, 30.0, 39.0, 41.5, 43.0, 46.0, 50.0, 65.0]
        payload = {"cross_sectional_corr": pinned_corr(pins, seeds, days), "pins": pins}

    result = {
        "method": {
            "mode": args.mode,
            "universe": "Universe.random(40, seed=111)",
            "seeds": seeds,
            "days": days,
            "macro": "endogenous (no pins, no shocks)" if args.mode != "pinned-corr"
            else "VIX re-pinned before every open (finding-7 method)",
        },
        "trajectory_fingerprint": fingerprint(),
        **payload,
    }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(result, handle)
        handle.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
