"""Fear-gap era, screen one (round 127): close the realized-vol loop and
shape the spike, measured on the era's own targets.

Targets (fear-gap-targets.json, ^VIX/^GSPC 2004-2025, sub-period ranges
in brackets): rv21-VIX tracking +0.868 [+0.60,+0.92], spike asymmetry
1.203 [1.12,1.28], AR(1) 0.976 [0.92,0.976], P(VIX>30) 0.082
[0.004,0.263], same-day corr -0.813 [-0.84,-0.78] (already real at
shipped; guarded, not chased). Panel guard: 4-seed p252 medians per
cell, because the u63 lesson says a livelier VIX breaks the calibrated
panel and the breakage size decides the re-levelling budget."""
import argparse, json, math, statistics, sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

FEAR_SEEDS = list(range(1, 9))
FEAR_DAYS = 1260
PANEL_SEEDS = list(range(1, 5))

def _grid(name):
    combos = []
    if name == "base":
        combos = [(w, dr, mr) for w in (0.0, 0.2, 0.4, 0.6)
                  for dr in (1.0, 0.6, 0.4) for mr in (0.12, 0.06)]
    elif name == "ext":
        # The bracket-completion grid, launched in parallel with base
        # (Simon: max out AWS): stronger feedback, slower decay, and the
        # mr axis widened both ways.
        combos = ([(w, dr, mr) for w in (0.8, 1.0)
                   for dr in (1.0, 0.6, 0.4) for mr in (0.12, 0.06)]
                  + [(w, dr, mr) for w in (0.2, 0.4, 0.6)
                     for dr in (0.3, 0.2) for mr in (0.12, 0.06)]
                  + [(w, dr, mr) for w in (0.4, 0.6)
                     for dr in (0.6, 0.4) for mr in (0.18, 0.09)])
    else:
        raise SystemExit(f"unknown grid {name}")
    cells = {}
    for w, dr, mr in combos:
        label = f"w{int(w*10)}d{int(dr*10)}m{int(mr*100)}"
        over = {}
        if w: over["vix_realised_vol_weight"] = w
        if dr != 1.0: over["vix_decay_ratio"] = dr
        if mr != 0.12: over["vix_mean_reversion"] = mr
        cells[label] = over
    return cells


CELLS = {}


def corr(x, y):
    n = len(x); mx = sum(x) / n; my = sum(y) / n
    sx = math.sqrt(sum((a - mx) ** 2 for a in x))
    sy = math.sqrt(sum((a - my) ** 2 for a in y))
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (sx * sy) if sx * sy else 0.0


def fear_one(job):
    label, seed = job
    import tradefloor as pt
    import pyarrow as pa, pyarrow.compute as pc
    m = pt.ModelParams.from_preset("pt-v16", **CELLS[label])
    u = pt.Universe.random(40, seed=111)
    e = pt.Engine(seed=seed, universe=u, model=m)
    vixs = []
    for d in range(FEAR_DAYS):
        e.run_days(1, first_day=d)
        mf = e.macro_fields
        if callable(mf): mf = mf()
        vixs.append(mf["vix"] if isinstance(mf, dict) else mf.vix)
    b = pa.table(e.bars(grain="day"))
    rets = {}
    for iid in range(40):
        close = pc.filter(b, pc.equal(b["instrument_id"], iid))["close"].to_pylist()
        rets[iid] = [(close[i] / close[i - 1] - 1) * 100 for i in range(1, len(close))]
    mret = [statistics.mean(rets[i][d] for i in range(40)) for d in range(FEAR_DAYS - 1)]
    dv = [vixs[i] - vixs[i - 1] for i in range(1, len(vixs))]
    vl = vixs[1:]
    rv, vv = [], []
    for j in range(21, len(mret)):
        rv.append(statistics.pstdev(mret[j - 21:j]) * math.sqrt(252)); vv.append(vl[j])
    up = [i for i in range(len(dv)) if dv[i] > 0]
    dn = [i for i in range(len(dv)) if dv[i] < 0]
    return {"kind": "fear", "label": label, "seed": seed,
            "same_day_corr": corr(mret, dv),
            "rv21_vix_corr": corr(rv, vv),
            "p_vix_gt_30": sum(1 for x in vl if x > 30) / len(vl),
            "vix_median": statistics.median(vl),
            "spike_asym": (statistics.mean(dv[i] for i in up)
                           / -statistics.mean(dv[i] for i in dn)) if dn and up else float("nan"),
            "ar1": corr(vl[1:], vl[:-1])}


def panel_one(job):
    label, seed = job
    import tradefloor as pt
    m = pt.ModelParams.from_preset("pt-v16", **CELLS[label])
    f = pt.facts.measure(seed=seed, universe=pt.Universe.random(40, seed=111),
                         days=252, model=m)
    fd = f if isinstance(f, dict) else f.to_dict()
    keep = ("annualised_vol_pct", "excess_kurtosis", "cross_sectional_corr",
            "volume_abs_return_corr", "abs_return_acf1", "corr_asymmetry",
            "sector_excess_corr", "volume_change_acf1")
    return {"kind": "panel", "label": label, "seed": seed,
            **{k: fd[k] for k in keep}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--grid", default="base")
    a = ap.parse_args()
    CELLS.update(_grid(a.grid))
    rows = []
    failed = 0
    # Fear jobs hold a 1260-day engine each; at 94 workers on 192 GB the
    # pool OOMs (BrokenProcessPool, first launch of this screen). The
    # fear pool is capped; the light 252-day panel jobs keep full width.
    for kind, fn, seeds, cap in (("fear", fear_one, FEAR_SEEDS, 24),
                                 ("panel", panel_one, PANEL_SEEDS, a.workers)):
        jobs = [(l, s) for l in CELLS for s in seeds]
        with ProcessPoolExecutor(max_workers=min(cap, a.workers)) as ex:
            futs = {ex.submit(fn, j): j for j in jobs}
            for i, f in enumerate(futs):
                try:
                    rows.append(f.result())
                except Exception as e:
                    failed += 1
                    print(f"FAILED {kind} {futs[f]}: {e}", flush=True)
                if (i + 1) % 50 == 0:
                    print(f"{kind} {i+1}/{len(jobs)}", flush=True)
    print(f"failed jobs: {failed}", flush=True)
    summary = {}
    for l in CELLS:
        fr = [r for r in rows if r["kind"] == "fear" and r["label"] == l]
        pr = [r for r in rows if r["kind"] == "panel" and r["label"] == l]
        summary[l] = {"overrides": CELLS[l]}
        for k in ("same_day_corr", "rv21_vix_corr", "p_vix_gt_30",
                  "vix_median", "spike_asym", "ar1"):
            summary[l][k] = statistics.median(r[k] for r in fr)
        for k in pr[0]:
            if k not in ("kind", "label", "seed"):
                summary[l]["panel_" + k] = statistics.median(r[k] for r in pr)
    json.dump({"rows": rows, "summary": summary}, open(a.out, "w"), indent=1)
    print(json.dumps({l: {k: round(v, 4) for k, v in s.items() if k != "overrides"}
                      for l, s in summary.items()}, indent=1))


if __name__ == "__main__":
    main()
