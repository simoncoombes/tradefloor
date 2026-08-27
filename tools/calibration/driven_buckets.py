"""Where in the VIX range the driven window's excess volatility actually is.

The `scenario-magnitude` gap is one number: over the real 2020-21 path the
model's daily return sd is 1.565x real AAPL's. One number cannot say WHERE,
and three rounds have now been spent on hypotheses about where.

Round 15 killed the standing one. Closing 83% of the calm-market floor made
the ratio WORSE, 1.610 to 1.630, so the excess is not in the calm days.

A held-VIX sweep (`vix_transfer.py`) then suggested the excess sits at VIX 25
to 40. **That comparison is not clean and this script exists because it is
not.** A held VIX 37.5 is a sustained regime the model settles into; real
days at VIX 37.5 are mostly transient spikes that mean-revert within weeks.
Sustained-versus-transient would produce a mid-range bump on its own, with
no model defect at all, and `real_vix_lever.py` flags exactly that asymmetry
in its own docstring.

So bucket BOTH SIDES the same way, on the same days. Drive the model with
the real 2020-21 path -- which is what `gate_pick.driven_window` already
does, and which spans VIX 12 to 82 -- then bucket the model's daily returns
and real AAPL's daily returns by the SAME session's VIX close, and take the
ratio within each bucket. Same dates, same VIX, same estimator, no held-run
construction anywhere.

If the ratio is flat across buckets, the model's excess is a uniform gain
error and the transfer curve's bump was an artefact of the held comparison.
If it concentrates at VIX 25-40, the bump is real and the gap has a
location. Either answer is worth the run; they are different defects and
they have different fixes.

    python tools/calibration/driven_buckets.py --candidates c.json \\
        --seeds 30 --workers 190 --out buckets.json
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor

import pretium as pt

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import gate_pick  # noqa: E402

#: `real_vix_lever.py`'s buckets, so the two measurements are directly
#: comparable. Open at both ends; the 2020-21 window reaches 82.
EDGES = [0, 12, 16, 20, 25, 30, 45, 999]


def _bucket(v: float) -> str:
    for lo, hi in zip(EDGES, EDGES[1:]):
        if lo <= v < hi:
            return f"{lo}-{hi}"
    return f"{EDGES[-2]}-{EDGES[-1]}"


def _job(spec):
    """One seed's model AAPL return series over the real path, plus the VIX."""
    label, base, ov, seed = spec
    import pyarrow as pa
    import pyarrow.compute as pc
    m = gate_pick.model(base, ov)
    raw, policy, credit, qe = gate_pick._covid_inputs()
    n = len(raw["dates"])
    path = [{"day": i, "vix": raw["vix"][i], "federal_funds_rate": policy[i],
             "corporate_bond_yield": credit[i], "qe_pe_boost": qe[i]}
            for i in range(n)]
    scen = pt.Scenario.from_json(json.dumps(
        {"schema": 1, "label": "covid", "days": n, "path": path}))
    aapl = pt.Instrument("AAPL", "technology", initial_price=raw["aapl"][0],
                         shares_outstanding=17.77e9, eps=2.97,
                         book_value_per_share=5.09, revenue_growth=-0.02,
                         avg_volume=140e6, beta=1.2, short_interest=124e6)
    u = pt.Universe([aapl]); u.extend(pt.Universe.random(39, seed=2020))
    e = pt.Engine(seed=seed, universe=u, model=m)
    for i in range(n):
        scen.apply(e, i)
        e.run_days(1, first_day=i)
    b = pa.table(e.bars(grain="day"))
    close = pc.filter(b, pc.equal(b["instrument_id"], 0))["close"].to_pylist()
    rets = [close[i] / close[i - 1] - 1 for i in range(1, len(close))]
    return label, rets


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--seeds", type=int, default=30)
    ap.add_argument("--seed-start", type=int, default=None)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    cands = json.loads(pathlib.Path(args.candidates).read_text(encoding="utf-8"))
    if args.seed_start is None:
        seeds = list(gate_pick.TRAIN[: args.seeds])
    else:
        seeds = list(range(args.seed_start, args.seed_start + args.seeds))
        if set(seeds) & set(gate_pick.TRAIN):
            sys.exit("the confirmation block overlaps the calibration seeds")

    raw, *_ = gate_pick._covid_inputs()
    vix = raw["vix"]
    real = [raw["aapl"][i] / raw["aapl"][i - 1] - 1 for i in range(1, len(raw["aapl"]))]
    # Bucket by the session the return is measured OVER, which is the close
    # that opens it -- the same convention on both sides, which is the only
    # thing that has to be true for the ratio to mean anything.
    buckets = [_bucket(vix[i]) for i in range(len(real))]
    order = [b for b in (f"{lo}-{hi}" for lo, hi in zip(EDGES, EDGES[1:]))
             if buckets.count(b) >= 10]

    def sd_by_bucket(rs):
        out = {}
        for b in order:
            xs = [rs[i] for i in range(len(rs)) if buckets[i] == b]
            if len(xs) >= 10:
                out[b] = statistics.pstdev(xs) * math.sqrt(252) * 100
        return out

    real_sd = sd_by_bucket(real)
    jobs = [(c["label"], c["base"], c["overrides"], s) for c in cands for s in seeds]
    print(f"{len(cands)} candidates x {len(seeds)} seeds over {len(real)} real "
          f"sessions, {len(order)} buckets, {args.workers} workers", flush=True)

    acc: dict[str, list] = {}
    with ProcessPoolExecutor(args.workers) as ex:
        for n, (label, rets) in enumerate(ex.map(_job, jobs), 1):
            acc.setdefault(label, []).append(sd_by_bucket(rets))
            if n % 25 == 0:
                print(f"  ... {n}/{len(jobs)}", flush=True)

    out = {"buckets": order, "sessions": len(real),
           "days_per_bucket": {b: buckets.count(b) for b in order},
           "real_annualised_vol_pct": real_sd, "candidates": {}}
    hdr = "".join(f"{b:>10}" for b in order)
    print(f"\n{'':<12}{hdr}      all")
    print(f"{'REAL AAPL':<12}" + "".join(f"{real_sd[b]:>10.1f}" for b in order)
          + f"{statistics.pstdev(real)*math.sqrt(252)*100:>9.1f}")
    for c in cands:
        rows = acc[c["label"]]
        med = {b: statistics.median([r[b] for r in rows if b in r]) for b in order}
        out["candidates"][c["label"]] = {
            "overrides": c["overrides"],
            "annualised_vol_pct": med,
            "ratio_to_real": {b: med[b] / real_sd[b] for b in order},
        }
        print(f"{c['label']:<12}" + "".join(f"{med[b]:>10.1f}" for b in order))
    print(f"\n{'ratio to real':<12}")
    for c in cands:
        r = out["candidates"][c["label"]]["ratio_to_real"]
        print(f"{c['label']:<12}" + "".join(f"{r[b]:>10.2f}" for b in order))
    print(f"\ndays per bucket: {out['days_per_bucket']}")

    pathlib.Path(args.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"wrote {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
