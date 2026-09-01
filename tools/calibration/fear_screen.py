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
    elif name == "fine":
        # The dose question (round 130): the full fear cell pushes 1201's
        # 504-day corr_asymmetry 0.0043 past the floor. Does a milder dose
        # keep the fear gates and stay in band?
        combos = [(w, dr, 0.06) for w in (0.3, 0.35, 0.4)
                  for dr in (0.6, 0.7)]
    elif name == "wire":
        # Round 132: P(VIX>30) regressed under the feedback (calm implied
        # pulls the median down). The one mechanism left: the same-day
        # fear wire at the MEASURED real slopes (-1.55 down / -1.10 up,
        # clamp 3) on top of the card-clean dose.
        base = {"vix_realised_vol_weight": 0.3, "vix_decay_ratio": 0.6,
                "vix_mean_reversion": 0.06}
        def wired(gd, gu):
            return {**base, "vix_return_source": 1.0, "vix_return_gain": gd,
                    "vix_return_gain_up": gu, "vix_return_clamp": 3.0}
        return {"w30d6": dict(base), "w30wireH": wired(0.8, 0.55),
                "w30wire1": wired(1.55, 1.10), "w30wire2": wired(2.0, 1.4)}
    elif name == "jump":
        # Round 134: exogenous fear events on the dose base. Jumps supply
        # up-side asymmetry on their own, so decay 0.7 hedges the 1.28
        # asymmetry ceiling the base already touches.
        b6 = {"vix_realised_vol_weight": 0.3, "vix_decay_ratio": 0.6,
              "vix_mean_reversion": 0.06}
        b7 = {**b6, "vix_decay_ratio": 0.7}
        def j(base, i, s):
            return {**base, "vix_jump_intensity": i, "vix_jump_scale": s}
        return {"w30d6": dict(b6),
                "w30j158": j(b6, 1.5, 8.0), "w30j310": j(b6, 3.0, 10.0),
                "w30j312": j(b6, 3.0, 12.0), "w30j610": j(b6, 6.0, 10.0),
                "w30d7j310": j(b7, 3.0, 10.0), "w30d7j312": j(b7, 3.0, 12.0)}
    elif name == "selfex":
        # Round 151: the crash-gated co-jump family's first screen
        # (PT-V17-MECHANISMS.md B1; predictions pre-registered there).
        # pt-v16 base, so the gate rides the shipped fear process. The
        # Poisson family died here at every dose (round 135); the claim
        # under test is that conditioning the arrival on the day's own
        # down-move inverts that death mode.
        def sx(**kw):
            return kw
        return {"v16": {},
                "g03": sx(vix_selfex_gain=0.3),
                "g05": sx(vix_selfex_gain=0.5),
                "g05s8": sx(vix_selfex_gain=0.5, vix_selfex_scale=8.0),
                "g05s8p1": sx(vix_selfex_gain=0.5, vix_selfex_scale=8.0,
                              vix_selfex_phase=1.0),
                "g03e55": sx(vix_selfex_gain=0.3, vix_selfex_excite=0.55),
                "g05r80": sx(vix_selfex_gain=0.5, vix_selfex_relax=0.80)}
    elif name == "selfex2":
        # Round 154: the composite the first two screens dictated (round
        # 153). Size-coupled magnitudes repair the same-day regression;
        # decay_ratio hands the asymmetry budget to the jumps; the comp
        # and lag wires ride where the card screens priced them; the HAR
        # anchor takes its first cells.
        def sx(**kw):
            return kw
        base35 = dict(vix_selfex_gain=0.3, vix_selfex_size_coupling=0.5)
        base55 = dict(vix_selfex_gain=0.5, vix_selfex_size_coupling=0.5)
        return {"v16": {},
                "g03sc05": dict(base35),
                "g03sc10": sx(vix_selfex_gain=0.3, vix_selfex_size_coupling=1.0),
                "g05sc05": dict(base55),
                "g05sc10": sx(vix_selfex_gain=0.5, vix_selfex_size_coupling=1.0),
                "g03sc05d75": sx(**base35, vix_decay_ratio=0.75),
                "g05sc05d80": sx(**base55, vix_decay_ratio=0.8),
                "g03sc05d75c02": sx(**base35, vix_decay_ratio=0.75,
                                    market_beta_up_comp=0.02),
                "g03sc05d75c02lag": sx(**base35, vix_decay_ratio=0.75,
                                       market_beta_up_comp=0.02,
                                       market_beta_down_asym_lag=0.02),
                "h05": sx(vix_har_weight=0.5),
                "g03sc05h05": sx(**base35, vix_har_weight=0.5)}
    else:
        raise SystemExit(f"unknown grid {name}")
    if isinstance(combos, dict):
        return combos
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
