"""Read the envgaps-pt-v20 and envgaps-pt-v20-final box artefacts into the figures envelope.py quotes."""
import json, random, statistics as st, sys
from tradefloor import facts, envelope

D = sys.argv[1]

print("== decay")
d = json.load(open(f"{D}/decay.json"))
for p, r in d["results"].items():
    ps = r["per_seed"]; keys = sorted(ps)
    rng = random.Random(20260923)
    boots = {k: [] for k in d["lags"]}
    for _ in range(2000):
        pick = [rng.choice(keys) for _ in keys]
        for k in d["lags"]:
            boots[k].append(st.median([ps[s][str(k)] for s in pick]))
    se = {k: st.pstdev(v) for k, v in boots.items()}
    print(p, "slope", round(r["slope_1_20"], 4), "sd", r["slope_boot_sd"] and round(r["slope_boot_sd"], 4),
          "undefined", r["slope_undefined_share"])
    for k in d["lags"]:
        c = r["curve"][str(k)] if str(k) in r["curve"] else r["curve"][k]
        pos = sum(ps[s][str(k)] > 0 for s in keys)
        real = envelope.REAL_DECAY.get(k)
        print(f"  lag {k:3d} {c:+.4f} se {se[k]:.4f} z {c / se[k]:+.2f} pos {pos}/30"
              + (f" real {real} ratio {c / real:.2f}" if real else ""))

print("== driven")
dr = json.load(open(f"{D}/driven.json"))
real = dr["real"]
for p in dr:
    if p == "real":
        continue
    rows = dr[p]; seeds = sorted(rows)
    for k in ("vix", "credit", "valuation"):
        v = [rows[s]["beta"][k] for s in seeds]
        sign_ok = sum((x < 0) == (real["beta"][k] < 0) for x in v)
        print(f"  {p} beta {k:9s} {st.median(v):+.5f} ratio {st.median(v) / real['beta'][k]:.2f} sign-right {sign_ok}/{len(v)}")
    for k in ("vix", "credit", "valuation"):
        v = [rows[s]["corr"][k] for s in seeds]
        print(f"  {p} corr {k:9s} {st.median(v):+.3f}")
    v = [rows[s]["sd_ratio"] for s in seeds]
    print(f"  {p} sd ratio {st.median(v):.3f} ({min(v):.2f} to {max(v):.2f})")
    v = [rows[s]["abs_vs_vix"] for s in seeds]
    print(f"  {p} |r| vs VIX {st.median(v):+.3f}")
    if "events" not in rows["2020"]: continue
    ev = rows["2020"]["events"]
    print(f"  {p} seed 2020 events agree {ev['agree']}/6")
    for r in ev["rows"]:
        print(f"     {r['date']} {r['event']:28s} {r['sim']:+.1%} vs {r['real']:+.1%} {'yes' if r['agree'] else 'NO'}")
    print(f"  {p} agree by seed", {s: rows[s]["events"]["agree"] for s in seeds})

print("== macro")
m = json.load(open(f"{D}/macro.json"))
for p, c in m["candidates"].items():
    print(" ", p, {k: (round(v, 4) if isinstance(v, float) else v) for k, v in c.items() if k != "overrides"})

print("== long horizon")
lh = json.load(open(f"{D}/long-horizon.json"))
for h in ("252", "504", "756", "1260", "2520"):
    med = lh["median"][h]
    ruled = facts.REAL_MARKETS_RULED_504
    graded = [k for k in facts.SHAPE if ruled.get(k) is not None and med.get(k) is not None]
    out_r = [k for k in graded if not (ruled[k][0] <= med[k] <= ruled[k][1])]
    dec = envelope.BANDS_504
    out_d = [k for k in facts.SHAPE if not (k in med and dec[k][0] <= med[k] <= dec[k][1])]
    print(f"  {h}d ruled {len(graded) - len(out_r)}/{len(graded)} out {[(k, round(med[k], 4), ruled[k]) for k in out_r]}"
          f" | decade {14 - len(out_d)}/14 out {[(k, round(med[k], 4), dec[k]) for k in out_d]}")
