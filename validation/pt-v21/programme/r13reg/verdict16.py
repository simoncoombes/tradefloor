"""verdict16.py DESK OUTJSON ARM: the sixteenth registration's grade verdict for one arm (pt-v21, R21D1).

results/ptv20/box-g15/verdict_g15.py (registration/fifteenth) with one addition: the eleven pt-v21 rows
(programme/ptv21-registration-16.md, "The pt-v21 rows"), read from DESK/ptv21-ARM.json, which ptv21.py grade
(ptv21/criteria 3fdb518c) writes. Every row's pass is the registered grader's: grade_all.py, criteria.py and
certgrade for the 148 rows, exploits_summary for the exploit gate, ptv21.py for the eleven. The grade passes when
all 159 pass. Without DESK/expl/ARM (a screen) the exploit gate is listed as deferred and not counted."""
import json, math, os, sys

R13 = os.environ.get("TF_R13", os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, R13)
import grade_all as G            # noqa: E402
import exploits_summary as XS    # noqa: E402

D = os.path.abspath(sys.argv[1]); OUT = sys.argv[2]; ARM = sys.argv[3]
r = json.load(open(f"{D}/ga-{ARM}.json"))[ARM]
rows = []


def add(group, rid, value, band, ok, use, note=None):
    rows.append({"group": group, "id": rid, "value": value, "band": band, "pass": bool(ok),
                 "usage": None if use is None or not math.isfinite(use) else round(float(use), 3),
                 **({"note": note} if note else {})})


# 1. the forty registered rows (criteria.py; R4 restated on the held close by grade_all)
for rid, v in r["existing"].items():
    if not isinstance(v, dict):
        continue
    add("registered", rid, v.get("model_text"), v.get("bands") or v.get("real_text"), v.get("pass"), v.get("usage"),
        v.get("edge_note") or (("last print %+.3f, reported" % v["last_print"]) if rid == "R4" and v.get("last_print") is not None else None))

# 2. proposed rows registered as gated (grade_all.ROWS, gated)
for rid, (stat, lo, hi, real, src, gated) in G.ROWS.items():
    if not gated:
        continue
    key = "C10e_cal" if rid == "C10e-cal" else rid
    v = r.get(key)
    if not isinstance(v, (int, float)):
        add("proposed", rid, None, [lo, hi], False, None, "not read"); continue
    ok = G.within(float(v), lo, hi); use = G.usage(float(v), lo, hi); note = None
    if rid == "C10e-cal":
        a = r.get("C10e_cal_ahead", 1); ok = v <= 1.0 and a <= 2 / 3
        use = max(G.usage(v, -math.inf, 1.0) if v > 0 else 0, a / (2 / 3)); note = f"ahead {a:.3f}"
    if rid == "AO2":
        ok = ok and r.get("AO2_pos", 0) >= 28; note = f"positive on {r.get('AO2_pos')}/30"
    if rid == "G-rt":
        use = None if v < 0 else math.inf; note = r.get("G-rt_which")
    if rid == "PH5":
        note = (f"vol clause worst year {r.get('PH5_vol_worst_year')}: gap {r.get('PH5_vol_worst_gap'):.3f} against "
                f"{r.get('PH5_vol_worst_bound'):.3f}; return d {r.get('PH5_diff'):+.4f} se {r.get('PH5_se'):.4f}"
                if r.get("PH5_n") else f"not read: {r.get('PH5_pool_note')}")
        use = r.get("PH5_vol_use")
    if rid == "AO5":
        note = str(r.get("AO5_checks"))
    if rid == "AO1":
        note = f"keys {r.get('AO1_keys')}, se {r.get('AO1_se')}"; use = None
    if rid in ("AO3", "AO4"):
        note = f"intercept {r.get(rid + '_intercept')} se {r.get(rid + '_se')} n {r.get(rid + '_n')} {r.get(rid + '_note', '')}"; use = None
    add("proposed", rid, v, [lo if math.isfinite(lo) else None, hi if math.isfinite(hi) else None], ok, use, note)

# 3. leak rows (the registration's table; aggregate.py's tests)
def one(rid, key, lo, hi):
    v = r.get(key)
    ok = isinstance(v, (int, float)) and G.within(float(v), lo, hi)
    add("leak", rid, v, [lo if math.isfinite(lo) else None, hi if math.isfinite(hi) else None], ok,
        G.usage(float(v), lo, hi) if isinstance(v, (int, float)) else None)

inf = math.inf
one("T1 median bp", "T1_median_bp", -inf, 50); one("T1 share over 1.7%", "T1_share", -inf, 0.10)
one("T2 sd bp", "T2_sd_bp", 1.5, 6.2); one("T2 days a decade over 50 bp", "T2_over50_dec", -inf, 1.0)
one("T3 bp", "T3_bp", -inf, 25)
v = r.get("C10c_breach"); add("leak", "C10c", v, "0 of 384 (270 pooled histories)", v == 0, None,
                              f"{r.get('C10c_histories')} histories; worst {r.get('C10c_worst')}; 90 alone {r.get('C10c_breach_90')} ({r.get('C10c_worst_90')}); {r.get('C10c_pool_note')}")
v = r.get("C10d_fails"); add("leak", "C10d", v, "0 cells", v == 0, None, str(r.get("C10d_bad")) if r.get("C10d_bad") else None)
one("C10e", "C10e", -13.5, 15.7)
v = r.get("bt:I-rate max err bp"); add("leak", "I-rate", v, "< 0.01", v is not None and v < 0.01, None)
one("R3 held", "bt:R3 held", -0.36, 0.03); one("R3m", "bt:R3m", -0.40, 0.10); one("R4m", "bt:R4m", 0.20, 0.65)
one("R4-lag", "bt:R4t lag1", -0.10, 0.10)
for k in sorted(k for k in r if k.startswith("L-rate:") and "-long" not in k):
    m, n_ahead, n = r[k]; ok = m <= 3 and n_ahead / n <= 2 / 3
    add("leak", k, [m, f"{n_ahead}/{n}"], "mean <= +3, ahead <= 2/3", ok, max(m / 3 if m > 0 else 0, (n_ahead / n) / (2 / 3)))
one("F-stress cut (rate > 0.25)", "bt:P(cut42|VIX 40+, rate>0.25)", 0.5, 1.0)
one("F-stress hike", "bt:P(hike42|VIX 30+)", -inf, 0.10)
one("F-bear", "bt:bear median dff (pts)", -inf, -0.5)
v = r.get("SF1"); add("leak", "SF1", v, ">= 0.75", isinstance(v, float) and v >= 0.75, None, "sf.json (12 seeds), as grade_all reads it")
for k in sorted(r):
    v = r[k]
    if k.startswith("SF2:"):
        m, a, n = v; add("leak", k, [m, f"{a}/{n}"], "<= +8, <= 2/3", m <= 8 and a / n <= 2 / 3, max(m / 8 if m > 0 else 0, (a / n) / (2 / 3)))
    if k.startswith("SF3:"):
        add("leak", k, v, ">= -13", v >= -13, v / -13 if v < 0 else 0, "min %.1f, reported" % r.get("SF3min:" + k[4:], float("nan")))
    if k.startswith("SF4:"):
        add("leak", k, v, "0", v == 0, None)
    if k.startswith("SF5:"):
        add("leak", k, v, [3, 13], 3 <= v <= 13, G.usage(v, 3, 13))
v = r.get("H1-100y"); add("leak", "H1-100y", v, "every decade in", isinstance(v, str) and "FAIL" not in v, None,
                          None)
add("leak", "R7a-pre", [r.get("R7a_pre_hike"), r.get("R7a_pre_cut")], "within 5 bp or 2 se", r.get("R7a_pre_pass") is True, None)
# PH5 (proposed row above) reads the 270 pooled histories; its 90-history reading is reported in the note
for x in rows:
    if x["id"] == "PH5":
        x["note"] = (x.get("note") or "") + f"; {r.get('PH5_n')} histories (90 alone: PH5 {r.get('PH5_90')}, vol use {r.get('PH5_90_vol_use')})"

# 4. the exploit gate (exploits_summary.summarise over expl/ARM), or deferred
if not os.path.isdir(f"{D}/expl/{ARM}"):
    rows.append({"group": "exploit", "id": "exploit gate", "value": None, "band": "0 exploits", "pass": None,
                 "usage": None, "note": "deferred: edgeaudit and regr run with the grade"})
    xs = None
else:
    xs = XS.summarise(f"{D}/expl/{ARM}")
if xs is None:
    pass
else:
  bad = [f"{x['name']} {x['exploits']}" + (f" ({x['fail']})" if x["fail"] else "") + (": " + " | ".join(str(h).strip() for h in x["hits"]) if x["hits"] else "") for x in xs if x["exploits"]]
  add("exploit", "exploit gate", {"outputs": len(xs), "rules_gated": sum(x["n"] for x in xs),
                                  "exploits": sum(x["exploits"] for x in xs)}, "0 exploits", not bad, None, "; ".join(bad) or None)

# 5. the eleven pt-v21 rows (ptv21.py grade --json DESK/ptv21-ARM.json)
PTV21 = ("ON1", "U1", "U2", "U3", "U4", "O1", "O2", "O3", "O4", "SK1", "SK2")
pj = f"{D}/ptv21-{ARM}.json"
pt = json.load(open(pj))[ARM]["rows"] if os.path.exists(pj) else {}
for rid in PTV21:
    x = pt.get(rid)
    if not x or not isinstance(x.get("value"), (int, float)):
        add("pt-v21", rid, None, None, False, None, "not read"); continue
    lo, hi = x.get("low"), x.get("high")
    add("pt-v21", rid, x["value"], [lo, hi], bool(x.get("pass")), G.usage(float(x["value"]), lo, hi))

deferred = [x for x in rows if x["pass"] is None]
rows = [x for x in rows if x["pass"] is not None]
failed = [x for x in rows if not x["pass"]]
thin = sorted((x for x in rows if x["pass"] and x["usage"] is not None), key=lambda x: -x["usage"])[:12]
reg = [x for x in rows if x["group"] in ("registered", "pt-v21")]
V = {"grade": "pt-v21, sixteenth registration", "arm": ARM, "desk": D,
     "deferred": deferred,
     "verdict": "pass" if not failed else "fail", "passed": len(rows) - len(failed), "of": len(rows),
     "registered_passed": sum(x["pass"] for x in reg), "registered_of": len(reg),
     "failed": failed, "thinnest": thin, "rows": rows}
json.dump(V, open(OUT, "w"), indent=1, default=lambda o: float(o) if hasattr(o, "__float__") else str(o))
print(f"verdict {V['verdict']}: {V['passed']}/{V['of']} gated rows; registered {V['registered_passed']}/{V['registered_of']}")
for x in failed:
    print("  FAIL", x["group"], x["id"], x["value"], "band", x["band"], x.get("note", ""))
print("thinnest passing:")
for x in thin:
    print("  ", x["group"], x["id"], x["value"], "band", x["band"], "usage", x["usage"], x.get("note", ""))
