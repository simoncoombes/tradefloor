"""Loss-makers against profitable growth on the driven 2022 path.

The measurement behind `lossmaker_rate_sensitivity`
(tools/calibration/lossmaker_duration.md). The real 2022 macro path that
row R6 drives (VIX, fed funds, Moody's Baa, the 2- and 10-year Treasury
yields, the cycle held at expansion), pinned each morning on a roster with
enough loss-makers to read a cross-sectional mean. Per seed, the
equal-weight log price change from the engine's opening to the last close
of the names with no earnings, less that of the 30 per cent of profitable
names with the highest P/E at the opening, over the Baa move (5.87 less
3.45, in points), times 100: per cent per 100 bp.

The real figure is computed from tools/calibration/data/ff-ep-2022.json
(Ken French's E/P portfolios, equal weight): -10.9.

    python lossmaker_duration.py real
    python lossmaker_duration.py measure --seeds 9001-9020 --roster 2022 \\
        --grid 0,4,8,11,14 --out OUT.json
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import statistics as st
from concurrent.futures import ProcessPoolExecutor

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
REAL = HERE / "data" / "ff-ep-2022.json"
PATH = ROOT / "validation" / "pt-v21" / "programme" / "results" / "ptv20" / "data" / "driven-2022.json"


def real() -> dict:
    """The 2022 equal- and value-weight figures, per 100 bp of Baa."""
    rows = json.load(open(REAL))["rows"]
    raw = json.load(open(PATH))
    move = raw["baa"][-1] - raw["baa"][0]
    out = {"baa_move_points": move}
    for weight in ("Equal", "Value"):
        annual = rows[f"{weight} Weight Returns -- Annual from January to December"]["2022"]
        log = {k: math.log(1 + annual[k] / 100) for k in ("<= 0", "Lo 30", "Med 40", "Hi 30")}
        out[weight.lower()] = {
            "returns_pct": {k: annual[k] for k in log},
            "per_100bp": {k: 100 * v / move for k, v in log.items()},
            "excess_over_lo30_per_100bp": 100 * (log["<= 0"] - log["Lo 30"]) / move,
        }
    return out


def driven_path():
    raw = json.load(open(PATH))
    return [{"day": i, "vix": raw["vix"][i], "federal_funds_rate": raw["dff"][i] / 100.0,
             "corporate_bond_yield": raw["baa"][i] / 100.0,
             "treasury_yield_10y": raw["dgs10"][i] / 100.0,
             "treasury_yield_2y": raw["dgs2"][i] / 100.0,
             "cycle": "expansion"} for i in range(len(raw["dates"]))], raw


def job(spec):
    preset, dials, seed, roster, names = spec
    import array
    import tradefloor as tf
    p, raw = driven_path()
    u = list(tf.Universe.random(names, seed=roster))
    model = tf.ModelParams.from_preset(preset, **dials)
    e = tf.Engine(seed=seed, universe=u, model=model)
    first = array.array("d"); first.frombytes(e.prices())
    sc = tf.Scenario.from_json(json.dumps({"schema": 1, "label": "driven-2022",
                                           "days": len(p), "path": p}))
    for i in range(len(p)):
        sc.apply(e, i)
        e.run_days(1, record=False, first_day=i)
    last = array.array("d"); last.frombytes(e.prices())
    move = raw["baa"][-1] - raw["baa"][0]
    loss = [i for i, inst in enumerate(u) if inst.eps <= 0]
    prof = sorted((i for i, inst in enumerate(u) if inst.eps > 0),
                  key=lambda i: first[i] / u[i].eps, reverse=True)
    growth = prof[:max(1, round(0.3 * len(prof)))]
    ret = [math.log(last[i] / first[i]) for i in range(len(u))]
    lm = st.fmean(ret[i] for i in loss)
    gm = st.fmean(ret[i] for i in growth)
    pm = st.fmean(ret[i] for i in prof)
    return seed, {"loss": lm, "growth": gm, "profitable": pm,
                  "excess_over_growth_per_100bp": 100 * (lm - gm) / move,
                  "excess_over_profitable_per_100bp": 100 * (lm - pm) / move,
                  "loss_per_100bp": 100 * lm / move, "n_loss": len(loss)}


def seeds_of(text: str) -> list[int]:
    out = []
    for part in text.split(","):
        lo, _, hi = part.partition("-")
        out += list(range(int(lo), int(hi or lo) + 1))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("real", help="the 2022 figures from the E/P portfolios")
    m = sub.add_parser("measure", help="the same construction on the driven 2022 path")
    m.add_argument("--preset", default="pt-v21")
    m.add_argument("--seeds", required=True)
    m.add_argument("--roster", type=int, default=2022, help="Universe.random seed")
    m.add_argument("--names", type=int, default=200)
    m.add_argument("--grid", default="0", help="lossmaker_rate_sensitivity values, comma-separated")
    m.add_argument("--workers", type=int, default=4)
    m.add_argument("--out")
    a = ap.parse_args()
    if a.cmd == "real":
        print(json.dumps(real(), indent=1))
        return
    result = {"preset": a.preset, "seeds": a.seeds, "roster": a.roster, "names": a.names,
              "real": real()["equal"]["excess_over_lo30_per_100bp"], "grid": {}}
    with ProcessPoolExecutor(a.workers) as pool:
        for k in [float(x) for x in a.grid.split(",")]:
            dials = {"lossmaker_rate_sensitivity": k} if k else {}
            specs = [(a.preset, dials, s, a.roster, a.names) for s in seeds_of(a.seeds)]
            per = dict(pool.map(job, specs))
            ex = [v["excess_over_growth_per_100bp"] for v in per.values()]
            row = {"excess_over_growth_per_100bp": st.fmean(ex),
                   "se": st.stdev(ex) / math.sqrt(len(ex)) if len(ex) > 1 else float("nan"),
                   "excess_over_profitable_per_100bp": st.fmean(
                       v["excess_over_profitable_per_100bp"] for v in per.values()),
                   "loss_per_100bp": st.fmean(v["loss_per_100bp"] for v in per.values()),
                   "n_loss": next(iter(per.values()))["n_loss"], "per_seed": per}
            result["grid"][str(k)] = row
            print(f"k={k:5.2f}  excess over growth {row['excess_over_growth_per_100bp']:+.2f} "
                  f"(se {row['se']:.2f})  over profitable {row['excess_over_profitable_per_100bp']:+.2f}  "
                  f"loss-makers {row['loss_per_100bp']:+.2f} per 100 bp  [{row['n_loss']} loss-makers]",
                  flush=True)
    if a.out:
        json.dump(result, open(a.out, "w"), indent=1)


if __name__ == "__main__":
    main()
