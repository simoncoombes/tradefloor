"""Read the §64 factor-memory survey: which vectors give correlation its
memory without spending the other thirteen statistics.

Selection is the pt-v7 protocol. A vector qualifies when every statistic
except the structural volume_change_acf1 is in band at both horizons (the
survey's six-seed medians, so a qualifying vector is a candidate, not a
result) and corr_persistence_acf1 at 504 days is inside the real band.
Qualifiers are ranked by the crisis volatility lever, with sector excess and
kurtosis in the held VIX 45 state printed beside them so the crisis-state
costs §62 found are visible at selection time. Sensitivities are printed for
the persistence column and for the levels the memory is expected to move.

Usage: python tools/calibration/read_factor_memory_survey.py atlas-survey.json [--top 12]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "python"))
from pretium import envelope, facts  # noqa: E402

STRUCTURAL_MISS = {"volume_change_acf1"}
PERSIST = "corr_persistence_acf1"


def panel_at(outputs: dict, days: int) -> dict:
    return {k: outputs[f"{k}_{days}"] for k in facts.REAL_MARKETS if f"{k}_{days}" in outputs}


def misses(outputs: dict, days: int) -> list[str]:
    sc = envelope.score(panel_at(outputs, days), horizon_days=days)["statistics"]
    return sorted(k for k, v in sc.items() if not v.get("in_band", True))


def corr(a, b):
    n = len(a)
    if n < 3:
        return float("nan")
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    va = sum((x - ma) ** 2 for x in a) ** 0.5
    vb = sum((y - mb) ** 2 for y in b) ** 0.5
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (va * vb) if va and vb else float("nan")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("survey")
    ap.add_argument("--top", type=int, default=12)
    args = ap.parse_args()
    doc = json.loads(Path(args.survey).read_text())
    recs = doc["rows"]
    axes = [a["name"] if isinstance(a, dict) else a for a in doc["axes"]]
    rows = []
    for r in recs:
        r = dict(r); r["vector"] = r["parameters"]
        o = r.get("outputs") or {}
        if not o or f"{PERSIST}_504" not in o:
            continue
        m252, m504 = misses(o, 252), misses(o, 504)
        rows.append({"index": r.get("index"), "v": r["vector"], "o": o,
                     "m252": m252, "m504": m504,
                     "ok": set(m252) <= STRUCTURAL_MISS and set(m504) <= STRUCTURAL_MISS})
    print(f"{len(rows)} measured vectors; axes {axes}")
    lo, hi = facts.REAL_MARKETS_504[PERSIST]
    base = [r for r in rows if r["ok"]]
    good = [r for r in base if lo <= r["o"][f"{PERSIST}_504"] <= hi]
    print(f"{len(base)} hold thirteen at both horizons; {len(good)} of those have "
          f"persistence_504 in ({lo}, {hi})")
    vals = [r["o"][f"{PERSIST}_504"] for r in rows]
    print(f"persistence_504 over the survey: min {min(vals):+.2f} median "
          f"{statistics.median(vals):+.2f} max {max(vals):+.2f}\n")

    print("sensitivities (correlation of output with axis, all measured vectors):")
    for col in (f"{PERSIST}_504", f"{PERSIST}_252", "cross_sectional_corr_504",
                "excess_kurtosis_504", "annualised_vol_pct_504", "sector_excess_corr_252",
                "vol_lever", "sector_ex_45", "kurt_45"):
        if col not in rows[0]["o"]:
            continue
        ys = [r["o"][col] for r in rows]
        s = sorted(((corr([r["v"][a] for r in rows], ys), a) for a in axes),
                   key=lambda t: -abs(t[0]))[:3]
        print(f"  {col:28s} " + "  ".join(f"{a} {c:+.2f}" for c, a in s))

    def show(title, group):
        print(f"\n{title}")
        hdr = f"{'idx':>5s} {'pers504':>8s} {'lever':>6s} {'sx45':>7s} {'k45':>5s} {'xs504':>6s} {'kurt504':>7s} {'vol504':>6s} " + " ".join(f"{a[:14]:>14s}" for a in axes)
        print(hdr)
        for r in group:
            o = r["o"]
            print(f"{r['index']:>5} {o[f'{PERSIST}_504']:>+8.3f} {o.get('vol_lever', float('nan')):>6.2f} "
                  f"{o.get('sector_ex_45', float('nan')):>+7.3f} {o.get('kurt_45', float('nan')):>5.2f} "
                  f"{o['cross_sectional_corr_504']:>6.3f} {o['excess_kurtosis_504']:>7.2f} {o['annualised_vol_pct_504']:>6.1f} "
                  + " ".join(f"{r['v'][a]:>14.5g}" for a in axes))
    show("qualifiers ranked by crisis lever:", sorted(good, key=lambda r: -r["o"].get("vol_lever", 0))[:args.top])
    show("highest persistence_504 among vectors holding thirteen (band or not):",
         sorted(base, key=lambda r: -r["o"][f"{PERSIST}_504"])[:args.top])
    near = sorted(rows, key=lambda r: (len(set(r["m252"]) - STRUCTURAL_MISS) + len(set(r["m504"]) - STRUCTURAL_MISS), -r["o"][f"{PERSIST}_504"]))[:args.top]
    print("\nnearest misses (fewest non-structural misses, then persistence):")
    for r in near:
        print(f"  {r['index']:>5} pers504 {r['o'][f'{PERSIST}_504']:+.3f}  misses 252 {r['m252']}  504 {r['m504']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
