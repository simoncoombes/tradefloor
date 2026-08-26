"""The thirty-seed gate for a survey pick: everything a preset has to pass,
in one run, on the pt-v7 protocol (CALIBRATION-FOLLOWUPS.md §62, §63).

  python tools/calibration/gate_pick.py --base pt-v7 \
      --overrides market_vol_alpha=0.25,market_vol_beta=0.739 [--seeds 30]

Runs, in order: thirty-seed panels at 252 and 504 days on the training
universe (fourteen statistics scored against the horizon-matched bands);
held VIX 45 crisis state (sector excess, cross-sectional correlation,
kurtosis, volatility); held-out seeds and a held-out 60-name universe at
252; then the response instrument against the base. Prints one block per
gate; the record quotes the block. §8 is separate (section8_check.py).
"""
from __future__ import annotations

import argparse
import statistics as st
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

# The source tree is APPENDED, never inserted ahead of site-packages.
# Inserting it shadows an installed pretium wheel with a source copy that
# has no compiled `_core`, which is invisible locally (the dev venv is an
# editable install pointing at these same files, extension included) and
# fatal on a fresh box, where the wheel is a real install. That is exactly
# how the first AWS gate batch died: "cannot import name '_core' from
# partially initialized module 'pretium'". atlas_survey.py never had the
# bug because it only ever added its own directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.append(str(Path(__file__).resolve().parent.parent.parent / "python"))
import pretium as pt  # noqa: E402
from pretium import Scenario, envelope, facts  # noqa: E402

TRAIN = tuple(range(101, 131))
HELDOUT = (1, 2, 3, 4, 5, 6)
_MODEL: dict = {}


def parse_overrides(text: str) -> dict[str, float]:
    return {k: float(v) for k, v in (kv.split("=") for kv in text.split(",") if kv)}


def model(base: str, overrides: dict[str, float]):
    return pt.ModelParams.from_preset(base, **overrides)


def one(job):
    base, overrides, kind, seed = job
    m = model(base, overrides)
    if kind == "p252":
        f = facts.measure(seed=seed, universe=pt.Universe.random(40, seed=111), days=252, model=m)
    elif kind == "p504":
        f = facts.measure(seed=seed, universe=pt.Universe.random(40, seed=111), days=504, model=m)
    elif kind == "vix45":
        f = facts.measure(seed=seed, universe=pt.Universe.random(40, seed=111), days=252,
                          model=m, scenario=Scenario().hold(vix=45.0))
    elif kind == "ho_seeds":
        f = facts.measure(seed=seed, universe=pt.Universe.random(40, seed=111), days=252, model=m)
    elif kind == "ho_universe":
        f = facts.measure(seed=seed, universe=pt.Universe.random(60, seed=909), days=252, model=m)
    else:
        raise ValueError(kind)
    return kind, {k: f.get(k) for k in list(facts.REAL_MARKETS)}


def summarise(kind: str, rows: list[dict]) -> str:
    med = {k: st.median([r[k] for r in rows if r.get(k) is not None]) for k in rows[0]}
    if kind == "vix45":
        return (f"  held VIX 45 ({len(rows)} seeds): sector_ex {med['sector_excess_corr']:+.4f} "
                f"xs {med['cross_sectional_corr']:.3f} kurt {med['excess_kurtosis']:.2f} "
                f"vol {med['annualised_vol_pct']:.1f}")
    days = 504 if kind == "p504" else 252
    sc = envelope.score(med, horizon_days=days)["statistics"]
    out = [k for k, v in sc.items() if not v.get("in_band", True)]
    n = len(facts.REAL_MARKETS)
    return (f"  {kind:12s} {days}d ({len(rows)} seeds): {n - len(out)}/{n} in band; vol "
            f"{med['annualised_vol_pct']:.1f} kurt {med['excess_kurtosis']:.2f} xs "
            f"{med['cross_sectional_corr']:.3f} sector_ex {med['sector_excess_corr']:+.4f} "
            f"persist {med['corr_persistence_acf1']:+.3f} acf1/5/20 {med['abs_return_acf1']:.3f}/"
            f"{med['abs_return_acf5']:.4f}/{med['abs_return_acf20']:.4f}  out: {', '.join(out) or '(none)'}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="pt-v7")
    ap.add_argument("--overrides", default="")
    ap.add_argument("--seeds", type=int, default=30, help="training seeds to use (30 is the gate)")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--no-response", action="store_true")
    args = ap.parse_args()
    ov = parse_overrides(args.overrides)
    m = model(args.base, ov)
    print(f"gate: {args.base} + {ov}  fingerprint {m.fingerprint}", flush=True)
    train = TRAIN[:args.seeds]
    jobs = ([(args.base, ov, "p252", s) for s in train] + [(args.base, ov, "p504", s) for s in train]
            + [(args.base, ov, "vix45", s) for s in train]
            + [(args.base, ov, "ho_seeds", s) for s in HELDOUT]
            + [(args.base, ov, "ho_universe", s) for s in HELDOUT])
    out: dict[str, list] = {}
    with ProcessPoolExecutor(args.workers) as ex:
        for kind, row in ex.map(one, jobs):
            out.setdefault(kind, []).append(row)
    for kind in ("p252", "p504", "vix45", "ho_seeds", "ho_universe"):
        print(summarise(kind, out[kind]), flush=True)
    if args.no_response:
        return 0
    # The response instrument opens its own pool: after ours has closed.
    import scenario_response as sr
    seeds = sr.DEFAULT_SEEDS if args.seeds >= 30 else train
    print("\nresponse instrument (vector vs base):", flush=True)
    v = sr.measure_vector(m, seeds, args.workers)
    r = sr.measure_vector(model(args.base, {}), seeds, args.workers)
    for key in ("vol_lever", "corr_blend", "shock_ratio_median"):
        print(f"  {key:20s} {v[key]:>8.3f}  base {r[key]:>8.3f}")
    for vix in sr.HELD_VIX:
        k = str(vix)
        print(f"  VIX {vix:<5.0f} vol {v['held_vix'][k]['annualised_vol_pct']:>6.1f}  base "
              f"{r['held_vix'][k]['annualised_vol_pct']:>6.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
