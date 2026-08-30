"""Flow composition, screen one (round 139): the forced-flow ladder on
the crash-cohesion instrument. Pre-registered judgment (round 138):
crash-co median toward the real 0.781 AND the seed-IQR collapsing
(v16 reads a wide 0.22-0.73); basket noise ratio and dispersion-trough
not degraded."""
import argparse, json, statistics, sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import os
CELLS = {"v16": {}}
if os.environ.get("FLOW_GRID", "one") == "trim":
    # Round 145: fund the flow's covid-path variance with the round-111
    # six-sigma joint trim (preserves correlations and ratios).
    CELLS.update({
        "r400p5": {
                "forced_flow_gain": 0.003,
                "forced_flow_threshold": 50.0,
                "forced_flow_beta_exponent": 2.0,
                "forced_flow_reservoir": 400.0,
                "forced_flow_replenish": 0.05
        },
        "rt90": {
                "forced_flow_gain": 0.003,
                "forced_flow_threshold": 50.0,
                "forced_flow_beta_exponent": 2.0,
                "forced_flow_reservoir": 400.0,
                "forced_flow_replenish": 0.05,
                "market_factor_sigma": 0.006833722432130459,
                "idio_sigma_scale": 0.46133837333999994,
                "jump_sigma_idio": 0.06768720557999999,
                "jump_sigma_market": 0.0022137810588347354,
                "endogenous_news_sigma": 0.015759039384,
                "sector_factor_sigma": 0.007724748252600001
        },
        "rt92": {
                "forced_flow_gain": 0.003,
                "forced_flow_threshold": 50.0,
                "forced_flow_beta_exponent": 2.0,
                "forced_flow_reservoir": 400.0,
                "forced_flow_replenish": 0.05,
                "market_factor_sigma": 0.006985582930622247,
                "idio_sigma_scale": 0.471590337192,
                "jump_sigma_idio": 0.069191365704,
                "jump_sigma_market": 0.002262976193475507,
                "endogenous_news_sigma": 0.016109240259200002,
                "sector_factor_sigma": 0.007896409324880001
        },
        "rt94": {
                "forced_flow_gain": 0.003,
                "forced_flow_threshold": 50.0,
                "forced_flow_beta_exponent": 2.0,
                "forced_flow_reservoir": 400.0,
                "forced_flow_replenish": 0.05,
                "market_factor_sigma": 0.0071374434291140345,
                "idio_sigma_scale": 0.48184230104399994,
                "jump_sigma_idio": 0.070695525828,
                "jump_sigma_market": 0.002312171328116279,
                "endogenous_news_sigma": 0.0164594411344,
                "sector_factor_sigma": 0.00806807039716
        }
})
elif os.environ.get("FLOW_GRID", "one") == "res":
    # Round 144: the reservoir ladder on the hot dose.
    CELLS.update({
        "g300inf": {
                "forced_flow_gain": 0.003,
                "forced_flow_threshold": 50.0,
                "forced_flow_beta_exponent": 2.0
        },
        "r200p0": {
                "forced_flow_gain": 0.003,
                "forced_flow_threshold": 50.0,
                "forced_flow_beta_exponent": 2.0,
                "forced_flow_reservoir": 200.0
        },
        "r200p5": {
                "forced_flow_gain": 0.003,
                "forced_flow_threshold": 50.0,
                "forced_flow_beta_exponent": 2.0,
                "forced_flow_reservoir": 200.0,
                "forced_flow_replenish": 0.05
        },
        "r400p0": {
                "forced_flow_gain": 0.003,
                "forced_flow_threshold": 50.0,
                "forced_flow_beta_exponent": 2.0,
                "forced_flow_reservoir": 400.0
        },
        "r400p5": {
                "forced_flow_gain": 0.003,
                "forced_flow_threshold": 50.0,
                "forced_flow_beta_exponent": 2.0,
                "forced_flow_reservoir": 400.0,
                "forced_flow_replenish": 0.05
        },
        "r800p0": {
                "forced_flow_gain": 0.003,
                "forced_flow_threshold": 50.0,
                "forced_flow_beta_exponent": 2.0,
                "forced_flow_reservoir": 800.0
        },
        "r800p5": {
                "forced_flow_gain": 0.003,
                "forced_flow_threshold": 50.0,
                "forced_flow_beta_exponent": 2.0,
                "forced_flow_reservoir": 800.0,
                "forced_flow_replenish": 0.05
        }
})
elif os.environ.get("FLOW_GRID", "one") == "fund":
    # Round 142: funded composites -- forced flow paid for by an
    # endpoint-pinned exponent DROP (T5/T45 fixed, T65 lowered), so
    # crisis variance is recomposed from factor noise into forced flow.
    CELLS.update({
        "g300t50k2": {
                "forced_flow_gain": 0.003,
                "forced_flow_threshold": 50.0,
                "forced_flow_beta_exponent": 2.0
        },
        "f300p17": {
                "forced_flow_gain": 0.003,
                "forced_flow_threshold": 50.0,
                "forced_flow_beta_exponent": 2.0,
                "market_vol_vix_exponent": 1.7,
                "market_vol_vix_coupling": 1.0432946095791136,
                "market_vol_vix_anchor": 13.938395948914303
        },
        "f300p18": {
                "forced_flow_gain": 0.003,
                "forced_flow_threshold": 50.0,
                "forced_flow_beta_exponent": 2.0,
                "market_vol_vix_exponent": 1.8,
                "market_vol_vix_coupling": 1.0065722134144428,
                "market_vol_vix_anchor": 14.621968758459962
        },
        "f200p18": {
                "forced_flow_gain": 0.002,
                "forced_flow_threshold": 50.0,
                "forced_flow_beta_exponent": 2.0,
                "market_vol_vix_exponent": 1.8,
                "market_vol_vix_coupling": 1.0065722134144428,
                "market_vol_vix_anchor": 14.621968758459962
        },
        "f300p19": {
                "forced_flow_gain": 0.003,
                "forced_flow_threshold": 50.0,
                "forced_flow_beta_exponent": 2.0,
                "market_vol_vix_exponent": 1.9,
                "market_vol_vix_coupling": 0.9773477138978486,
                "market_vol_vix_anchor": 15.30542178476031
        }
})
elif os.environ.get("FLOW_GRID", "one") == "four":
    # Screen four (round 141): threshold 50 -- ABOVE the held-45
    # instrument entirely (lever/co clean by construction), hotter gain
    # to compensate for fewer active days in the crash window.
    def c(g, th, k):
        o = {"forced_flow_gain": g, "forced_flow_threshold": th}
        if k: o["forced_flow_beta_exponent"] = k
        return o
    CELLS.update({
        "g200t50k2": c(0.002, 50.0, 2.0),
        "g300t50k2": c(0.003, 50.0, 2.0),
        "g400t50k2": c(0.004, 50.0, 2.0),
        "g300t55k2": c(0.003, 55.0, 2.0),
    })
elif os.environ.get("FLOW_GRID", "one") == "three":
    # Screen three (round 140): the frontier corners — hot dose x strong
    # concentration.
    def c(g, th, k):
        o = {"forced_flow_gain": g, "forced_flow_threshold": th}
        if k: o["forced_flow_beta_exponent"] = k
        return o
    CELLS.update({
        "g150t40k2": c(0.0015, 40.0, 2.0),
        "g200t40k2": c(0.002, 40.0, 2.0),
        "g150t35k2": c(0.0015, 35.0, 2.0),
        "g200t45k2": c(0.002, 45.0, 2.0),
    })
elif os.environ.get("FLOW_GRID", "one") == "two":
    # Screen two (round 139): beta-weighted forced flow. The uniform lean
    # pinned cohesion at a dispersion cost; beta^k concentrates the same
    # pressure on high-beta names.
    def c(g, th, k):
        o = {"forced_flow_gain": g, "forced_flow_threshold": th}
        if k: o["forced_flow_beta_exponent"] = k
        return o
    CELLS.update({
        "g100t35": c(0.001, 35.0, 0.0),
        "g100t35k1": c(0.001, 35.0, 1.0),
        "g100t35k2": c(0.001, 35.0, 2.0),
        "g100t40k1": c(0.001, 40.0, 1.0),
        "g150t40k1": c(0.0015, 40.0, 1.0),
    })
else:
    for g in (0.00025, 0.0005, 0.001):
        for th in (35.0, 40.0, 45.0):
            CELLS[f"g{int(g*100000)}t{int(th)}"] = {
                "forced_flow_gain": g, "forced_flow_threshold": th}
SEEDS = list(range(1, 31))


def one(job):
    label, seed, roster = job
    import gate_pick
    import tradefloor as pt
    m = pt.ModelParams.from_preset("pt-v16", **CELLS[label])
    r = gate_pick.driven_basket(m, seed=seed, roster_path=roster)
    r["label"], r["seed"] = label, seed
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--roster", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    jobs = [(l, s, a.roster) for l in CELLS for s in SEEDS]
    rows = []
    with ProcessPoolExecutor(max_workers=min(a.workers, 24)) as ex:
        futs = {ex.submit(one, j): j for j in jobs}
        for f in futs:
            try:
                rows.append(f.result())
            except Exception as e:
                print(f"FAILED {futs[f]}: {e}", flush=True)
    summary = {}
    for l in CELLS:
        pr = sorted(r["crash_comovement_sim"] for r in rows if r["label"] == l)
        if not pr:
            continue
        n = len(pr)
        summary[l] = {
            "crash_co_median": statistics.median(pr),
            "crash_co_iqr": pr[(3 * n) // 4] - pr[n // 4],
            "crash_co_min": pr[0],
            "basket_noise": statistics.median(
                r["basket_noise_ratio"] for r in rows if r["label"] == l),
            "disp_trough": statistics.median(
                r["dispersion_trough_ratio"] for r in rows if r["label"] == l),
            "n": n}
    json.dump({"rows": rows, "summary": summary}, open(a.out, "w"), indent=1)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
