"""§8 on the exact incumbent, using calibrate.py's own control functions.

calibrate.py cannot validate a fixed vector: the shrink stage moves it, and
the §44 run judged share 0.475 rather than the 0.0 asked for. §36 made the
flip test a pure function precisely so it could be exercised directly, so
the control runs here on the exact vector instead of near it.

A rejection is not a verdict on the candidate until the base has been run
through the same check. MEASURED 2026-08-25: pt-v3 with no override is
REJECTED by the horizon flip test (abs_return_acf5 room -0.76 sd,
excess_kurtosis -0.60 sd at 504 days), so any pt-v3 based candidate fails
here whatever it changes. Run the base as a control and compare the rooms;
the difference between the two is what the candidate did. §60 in
CALIBRATION-FOLLOWUPS.md records the first time this was nearly misread.
"""
import sys, statistics, multiprocessing as mp
import numpy as np
sys.path.insert(0, "tools/calibration")
import calibrate, instrumentlib as lib
import pretium as pt
from pretium import facts, loss

TRAIN = tuple(range(101, 131))
HOLD_SEEDS = (1, 2, 3, 4, 5, 6)
CONFIRM = tuple(range(201, 231))   # disjoint from TRAIN, for the horizon axis
MARGIN = 0.5          # §36's tolerance
# Overridable so a candidate can be checked without editing the file. Flags
# take precedence; the environment variables predate them and still work.
#
# `--help` used to RUN THE CHECK, because there was no parser and argparse
# never saw the flag: a reader asking what the tool does got a multi-minute
# calibration instead of a usage line. tests/test_tool_help.py exists for
# exactly that and could not see this file, because it collects scripts
# containing `add_argument`.
import argparse
import os


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--preset", default=os.environ.get("PRETIUM_S8_PRESET", "pt-v4"),
        help="preset the candidate is built from (env PRETIUM_S8_PRESET, "
             "default: %(default)s)",
    )
    p.add_argument(
        "--overrides",
        default=os.environ.get("PRETIUM_S8_OVERRIDES", "jump_momentum_share=0.0"),
        help="comma separated name=value coefficients applied to the preset "
             "(env PRETIUM_S8_OVERRIDES, default: %(default)s)",
    )
    return p


def incumbent_from(args) -> dict:
    out = dict(preset=args.preset)
    for item in filter(None, args.overrides.split(",")):
        k, _, v = item.partition("=")
        out[k.strip()] = float(v)
    return out


INCUMBENT = incumbent_from(build_parser().parse_known_args()[0])

AXES = {
    "train":            (TRAIN,      40, 111, 252),
    "holdout_seeds":    (HOLD_SEEDS, 40, 111, 252),
    "holdout_universe": (HOLD_SEEDS, 60, 909, 252),
    # CONFIRM, not TRAIN. calibrate.py's own help records that this axis
    # used to default to the training seeds, which held out the horizon and
    # not the paths. I reproduced that defect on the first pass.
    "train_horizon":    (TRAIN,      40, 111, 504),
    "holdout_horizon":  (CONFIRM,    40, 111, 504),
}

def job(a):
    axis, seed = a
    seeds, n, useed, days = AXES[axis]
    m = pt.ModelParams.from_preset(
        INCUMBENT["preset"],
        **{k: v for k, v in INCUMBENT.items() if k != "preset"})
    return axis, facts.measure(seed=seed, universe=pt.Universe.random(n, seed=useed),
                               days=days, model=m)

def median_panel(panels):
    keys = set().union(*(p.keys() for p in panels))
    out = {}
    for k in keys:
        vals = [p[k] for p in panels if isinstance(p.get(k), (int, float))]
        if vals: out[k] = sorted(vals)[len(vals)//2]
    return out

def main():
    jobs = [(ax, s) for ax, (seeds, *_ ) in AXES.items() for s in seeds]
    # 8 is the laptop default. On a 192 vCPU box four of these run side by
    # side, one per candidate, so the pool size is the knob that decides
    # whether the control finishes beside the candidate or an hour after it.
    with mp.Pool(int(os.environ.get("PRETIUM_S8_WORKERS", "8"))) as p:
        res = p.map(job, jobs)
    by = {}
    for ax, pn in res: by.setdefault(ax, []).append(pn)
    # The flip test wants band_distance_loss's per-statistic breakdown.
    # The 504-day axis MUST be scored against its own bands and noise scale:
    # pairing one horizon's measurement with the other's ruler is §32's error.
    stats = {ax: loss.band_distance_loss(v)["statistics"]
             for ax, v in by.items() if not ax.endswith("horizon")}
    for ax in ("train_horizon", "holdout_horizon"):
        stats[ax] = loss.band_distance_loss(
            by[ax], bands=facts.REAL_MARKETS_504,
            seed_sd=facts.SEED_SD_504)["statistics"]

    # band_distance_loss does not emit room_sd; calibrate.py adds it before
    # calling the flip test, and without it the test flags rather than
    # passes. Same formula, same horizon-matched noise scale.
    for ax, table in stats.items():
        far = ax.endswith("horizon")
        scale = facts.SEED_SD_504 if far else facts.SEED_SD
        for key, row in table.items():
            lo, hi = row["band"]
            sd = scale.get(key)
            m = row["measured"]
            row["room_sd"] = (None if m is None or not sd
                              else min(m - lo, hi - m) / sd)

    def bootstrap_spread(panels, bands=None, sd=None, draws=2000, seed=20260822):
        """calibrate.py's yardstick, same shape and same default seed.

        Resamples over SEEDS, the exchangeable unit, and recomputes the
        shipped loss on each resample. Without it the §8 rule is a bare 2x
        on a number whose own sampling spread nobody measured.
        """
        if len(panels) < 2:
            return float("nan")
        boot = np.random.default_rng(seed)
        out = []
        for _ in range(draws):
            idx = boot.integers(0, len(panels), len(panels))
            pick = [panels[i] for i in idx]
            out.append(loss.band_distance_loss(pick, bands=bands, seed_sd=sd)["loss"])
        return float(statistics.stdev(out))

    print("  === §8 loss axes (L_real, true bands) ===")
    base = loss.band_distance_loss(by["train"]) if hasattr(loss, "band_distance_loss") else None
    for ax in ("train", "holdout_seeds", "holdout_universe", "train_horizon", "holdout_horizon"):
        L = (loss.band_distance_loss(by[ax], bands=facts.REAL_MARKETS_504,
                                     seed_sd=facts.SEED_SD_504)
             if ax.endswith("horizon") else loss.band_distance_loss(by[ax]))
        v = L["loss"] if isinstance(L, dict) else L
        print(f"    {ax:20s} {v:.4f}")

    # The thresholds the rule actually uses.
    L = lambda ax, far=False: loss.band_distance_loss(
        by[ax], bands=facts.REAL_MARKETS_504 if far else None,
        seed_sd=facts.SEED_SD_504 if far else None)["loss"]
    tr, trh = L("train"), L("train_horizon", True)
    sp = bootstrap_spread(by["train"])
    sph = bootstrap_spread(by["train_horizon"], facts.REAL_MARKETS_504, facts.SEED_SD_504)
    print(f"\n  === §8 loss thresholds (train + 2 x bootstrap spread) ===")
    print(f"    train  {tr:.4f}  spread {sp:.4f}  -> threshold {tr + 2*sp:.4f}")
    print(f"    horizon{trh:>7.4f}  spread {sph:.4f}  -> threshold {trh + 2*sph:.4f}")
    exceeded = []
    for ax, thr in (("holdout_seeds", tr + 2*sp), ("holdout_universe", tr + 2*sp),
                    ("holdout_horizon", trh + 2*sph)):
        v = L(ax, ax.endswith("horizon"))
        ok = v <= thr
        exceeded.append(not ok)
        print(f"    {ax:20s} {v:.4f} vs {thr:.4f}  {'ok' if ok else 'EXCEEDS'}")

    print("\n  === §8 flip test, margin 0.5 seed-sd ===")
    keys = list(loss.LIVE_TARGETS) + list(loss.CONSTRAINTS)
    any_flip = False
    for ax in ("holdout_seeds", "holdout_universe", "holdout_horizon"):
        flips = calibrate.statistic_flips(stats["train"], stats[ax], keys, MARGIN, ax)
        if flips:
            any_flip = True
            for f in flips:
                print(f"    FLIP {f['statistic']:22s} {ax:18s} "
                      f"measured {f.get('measured')} band {f.get('band')} "
                      f"room_sd {f.get('room_sd'):.3f}")
        else:
            print(f"    {ax:20s} no flips")
    verdict = ("rejected by §8" if (any_flip or any(exceeded))
               else "passes §8 on every axis")
    print(f"\n  VERDICT: {verdict}")

if __name__ == "__main__":
    # Parsed for real here so an unknown flag is an error rather than ignored;
    # the module-level parse above is deliberately permissive so that importing
    # this file for its functions does not depend on argv.
    build_parser().parse_args()
    main()
