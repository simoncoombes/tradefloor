"""criteria -- the adopted pass bar for a preset (CRITERIA.md), computed by code.

    python criteria.py --longrun REPORT.json --certgrade CERTGRADE.json \\
        --edge EDGE.json [--edge EDGE2.json ...] --c4 C4.json [--c4 ...] \\
        [--xsec XSEC.json] \\
        --arm NAME[,cert=NAME][,edge=NAME][,c4=NAME][,xsec=NAME] [--arm ...] \\
        [--out FILE.txt] [--json FILE.json] [--expect NAME=A1,B5,...]

    # measure the headline edge where no file has it (engine python needed):
    ENGINE/.venv/bin/python criteria.py ... --edge-run --edge-out EDGE.json \\
        [--edge-seeds 10] [--edge-days 300] [--edge-workers 3]

Inputs, each the output of the project's own instrument:
  --longrun    longrun.py report's JSON (the .json beside longrun-report.txt):
               30 seeds x 21 years free running and the 2008 / 2020 replays;
  --certgrade  certgrade_box.py --json: the one-year certification cells;
  --edge       results/news-speed/edge.py's JSON: what a headline read at
               tick k is worth, per arm. --edge-run measures it here instead,
               with edge.measure() on the arm's base and dials as the long-run
               report records them (the same method: certified roster, seeds
               from 101, gross, market-adjusted, held to the close).
  --c4         c4.py's JSON: the 65-minute tape (C4a) and the price-only
               rules on the published suite (C4b), per arm. Measured on the
               engine the arm will ship on: C4b runs through tf.evaluate, so
               it reads the harness as well as the market.

  --xsec       results/ptv20/grade_xsec.py's JSON: the cross-section rows
               C5-C8 registered in programme/ptv20-registration.md (bands in
               results/ptv20/bands.json). Without it the verdict grades the
               seventeen; with it, twenty-one.
  --driven, --driven2022, --c10, --impact: D2, F1, L1; R5, R6; C10; C9.
  --r7         r7_event.py's JSON (R7a) and r7_eval.py's JSON (R7b), the flag
               once for each: no drift after a published policy-rate
               decision (twelfth registration).
  --recession  recession_rows.py's JSON: S1a, S1b and S2, the packaged
               recession recovers (twelfth registration).
  --v1         v1.py's JSON: V1, the long-horizon variance ratio (twelfth
               registration).

An arm is named as the long-run report names it; `cert=`, `edge=`, `c4=` and `xsec=`
give its name in the other inputs when they differ (older boxes call pt-v19
SHIPPED in the certification cells).

THE SEVENTEEN CRITERIA, with the tolerances CRITERIA.md states (inclusive at
the edge). Fifteen were adopted on 2026-09-23 (first written as sixteen and
corrected at e1820518); C4a and C4b were added on 2026-09-24:
  A1  2008 and 2020 replays: worst month's volatility within 30% of real
  A2  2008 and 2020 replays: maximum drawdown within 30% of real
  A3  2008 and 2020 replays: peak stock correlation within 0.15 of real
      (the model's value is the median over seeds, as the report prints it)
  B1  share of sessions with VIX > 30, 1/2x to 2x the tape
  B2  mean fear-spell length above 30, 1/2x to 2x
  B3  20% bear markets per decade, 1/2x to 2x
  B4  10% corrections per decade, 1/2x to 2x
  B5  sessions under -5% per decade, 1/2x to 2x
  B6  share of sessions with VIX < 15, 1/2x to 2x
  B7  index annual volatility within 20% of the tape
  B8  long-run index return within 2 points of the honest target, 6.25%
      (earnings growth, not P/E: the midpoint of candidate-status.md's 6-6.5)
  C1  crash rate steady: the mean share of sessions under -3% over years
      3-21 within 2/3x to 1.5x of the mean over years 1-2
  C2  VIX ceiling hits in at most 1 of the 30 histories
  C3  the edge from a headline read 5 ticks late under 20 bp (|mean|: an
      edge from fading the headline is the same illusion)
  C4a the tape: 65-minute lag-1 autocorrelation of print returns, median
      over the certified roster's names, at or above -0.05, OR the Roll
      spread those returns imply at most 2x the median quoted spread
  C4b the user's view: mean reversion and momentum at a one-step, a
      one-day and a five-day (daily cadence) lookback, through tf.evaluate
      on tf-suite-2026.1's 20 markets of 60 days: each beats buy-and-hold by
      at most +5 points at the median and in at most 14 of the 20
  B9  the index's annual returns spread (sd, years 2-21) within 20% of the
      tape's 17.4, and the first 60 sessions' spread across markets within
      2/3x to 1.5x of the same markets' later 60-session spread (registered
      2026-09-24 at the main session's request, ptv20-registration.md)
  C5  the stock-level variance ratio at 60 sessions, median name, in
      [0.80, 1.05] (real 0.924; registered 2026-09-24, ptv20-registration.md)
  C6  the value signal's rank IC against the next 20 sessions, whole history
      and first 60 sessions, both in [-0.03, +0.05] (real +0.009)
  C7  12-1 and 6-1 month momentum rank ICs in [-0.04, +0.095] and
      [-0.02, +0.10] (real +0.027 and +0.041)
  C8  the daily loser-minus-winner book in [-6.4, +2.9] bp (real -1.74)
  R1-R4 the yield curve: the 2- and 10-year's daily change sd and the
      stock-Treasury and stock-IG correlations, each within 2 tape se
      (registered 2026-09-24, ptv20-registration.md section R)
  R7a the first 65-minute bar after a published policy-rate change, less
      all days' first bar, hikes and cuts apart: each within 5 bp or 2 se
  R7b the audit's rate-news agent: mean annual excess over holding at most
      0, ahead in at most 20 of 30 (R7, twelfth registration)
  S1a the packaged recession: the share of the paired log fall won back 252
      sessions after its lowest point, mean over seeds, in 45% to 100%
  S1b every seed out of contraction and trough within 24 months of onset
  S2  the index's own rise in the 252 sessions after its low, in +25% to +80%
      (S1 and S2, twelfth registration; seeds 301-330, pt-v19 and pt-v20)
  D1  every ruled band in on all four certification cells (panel_252,
      panel_504, heldout_seeds, heldout_universe), and the level rows in band;
      crisis_sector_dispersion on at least 10 readings per cell, extended
      with seeds from 1001 where the cell's own give fewer (2026-09-26,
      certgrade_box.py and crisisext_box.py)

`usage` is the share of the tolerance a value spends (0 on target, 1 at the
edge; on the 1/2x-2x rows it is |log ratio| / log 2) and names what sits
nearest the edge. Everything else the instruments measure is REPORTED under
the table and does not gate, as CRITERIA.md rules.

--expect NAME=A1,B5 (or NAME= for none) makes the run a check: exit 1 unless
NAME fails exactly those criteria. That is how the table in CRITERIA.md is
reproduced.

--verdict FILE --verdict-arm NAME [--box RUN] [--date D] writes one arm's
verdict in the shape a preset record carries as its `long_run` block
(`tools/presets/record.py --long-run FILE` in the engine writes it): the
criteria, pass or fail, the count, one row per criterion (id, words, the
model's value, the real value, the rule in words, pass) and what was measured
(engine commit, seeds, years, box, date, and the fingerprint the long run ran
under, which record.py checks against the record's preset name). A verdict
with any criterion unscored is refused rather than written.

--definitions reg18 --reg18 FILE grades R4 and D1 as the eighteenth registration
defines them (DEFINITIONS below), from FILE (results/ptv21/cert_reg18.py's
output): R4 on the held close (bt:R4 held over the true-phase histories, the
thirteenth registration on), and D1's index_tail_dn3_pct and index_drift_pct as
pooled readings over at least 360 seeds (owner decisions 11 and 12, the
fourteenth registration on). The default, reg12, grades them as the twelfth
registration did (R4 at the last print, the level rows on the 30 cert seeds),
unchanged. Under reg18 the twelfth-definition readings stay in the rows and the
text as reported values, and the verdict records which definitions graded it,
why, and where they are registered.
"""
import argparse
import json
import math
import pathlib
import statistics as st
import sys

HERE = pathlib.Path(__file__).resolve().parent

#: The definition sets --definitions chooses between. reg12 is the default and
#: changes nothing. reg18 is the eighteenth registration's reading of R4 and D1
#: (registered for pt-v21, grade 18); every other row is the same under both.
#: `cite` names the registration by its path in the public validation folder.
DEFINITIONS = {
    "reg12": {
        "version": "reg12",
        "rows": {},
        "why": "the twelfth registration's definitions, as box ptv20g6 graded pt-v20",
        "cite": ["validation/pt-v20/programme/ptv20-registration.md"],
    },
    "reg18": {
        "version": "reg18",
        "rows": {
            "R4": "graded once, on the held close: bt:R4 held, the mean over the true-phase histories of the "
                  "daily correlation of the index with minus the IG yield's change at the held close, band "
                  "+0.15 to +0.39 (real +0.272, SPY against LQD 2015-2025); the reading at the last print is "
                  "reported (owner 2026-09-28 (4), the thirteenth registration on)",
            "D1": "index_tail_dn3_pct as a pooled rate and index_drift_pct as the mean, each over at least 360 "
                  "seeds of the certification's varying-roster protocol (d1pool), the certification's bands; the "
                  "30-seed cert readings are reported (owner decisions 11 and 12, the fourteenth registration on)",
        },
        "why": "pt-v21 was registered and graded (grade 18) on these definitions; the twelfth registration's R4 "
               "and D1 level rows were superseded before pt-v21's programme began",
        "cite": ["validation/pt-v21/programme/ptv21-registration-18.md (\"The forty registered rows\")"],
    },
}
REG18_POOL_MIN = 360

HONEST_RETURN = 6.25        # CRITERIA.md B8; candidate-status.md "6-6.5 honest"
EPISODES = (("gfc", "2008"), ("covid", "2020"))
CERT_CELLS = ("panel_252", "panel_504", "heldout_seeds", "heldout_universe")
EDGE_TICK = "5"

ORDER = ["A1", "A2", "A3", "B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B9", "C1", "C2", "C3",
         "C4a", "C4b", "C5", "C6", "C7", "C8", "C9", "R1", "R2", "R3", "R4", "E1", "D2", "F1", "L1", "R5", "R6", "C10",
         "R7a", "R7b", "S1a", "S1b", "S2", "V1", "D1"]
#: The cross-section rows are graded only when --xsec is given, so a
#: seventeen-row verdict written before they were registered still reads as
#: it did.
XSEC_ROWS = ("B9", "C5", "C6", "C7", "C8", "C9", "R1", "R2", "R3", "R4", "E1", "D2", "F1", "L1", "R5", "R6", "C10",
             "R7a", "R7b", "S1a", "S1b", "S2", "V1")
#: V1, the long-horizon variance ratio (the audit's major 5; twelfth
#: registration): v1.py's V1a = VR(504)/VR(252) and V1b = VR(1260)/VR(252)
#: on the free histories, scored from xsec["_v1"] (the --v1 file's arm block).
V1_REGISTERED = True
V1_BANDS = {"V1a": (0.75, 1.15), "V1b": (0.55, 1.20)}
LABEL = {
    "A1": "A1 worst month's volatility within 30%",
    "A2": "A2 maximum drawdown within 30%",
    "A3": "A3 peak stock correlation within 0.15",
    "B1": "B1 time with VIX above 30, within 1/2x to 2x",
    "B2": "B2 fear-spell length above 30, 1/2x to 2x",
    "B3": "B3 20% bear markets per decade, 1/2x to 2x",
    "B4": "B4 10% corrections per decade, 1/2x to 2x",
    "B5": "B5 sessions under -5% per decade, 1/2x to 2x",
    "B6": "B6 time with VIX under 15, 1/2x to 2x",
    "B7": "B7 index volatility within 20%",
    "B8": "B8 long-run index return within 2 points",
    "C1": "C1 crash rate steady, years 3-21 / 1-2",
    "C2": "C2 VIX ceiling hits, at most 1 of 30",
    "C3": "C3 headline edge 5 ticks late, under 20 bp",
    "C4a": "C4a 65-min return ACF1 >= -0.05, or Roll <= 2x",
    "C4b": "C4b price-only rules: <= +5 pts, <= 14 of 20",
    "B9": "B9 annual return spread within 20%; start-up 2/3x-1.5x",
    "C5": "C5 stock-level variance ratio, 60 sessions",
    "C6": "C6 value signal rank IC, whole / first 60",
    "C7": "C7 momentum rank IC, 12-1 / 6-1",
    "C8": "C8 daily loser-minus-winner, bp",
    "C9": "C9 cost of size: exponent / average coefficient",
    "D2": "D2 driven 2020-21: drawdown / sessions back to the high",
    "F1": "F1 driven 2020 fast crash: drop / sessions",
    "L1": "L1 look-through: price trough leads earnings, sessions",
    "R5": "R5 driven 2022: index drawdown",
    "R6": "R6 driven 2022: P/E per 100 bp of the corporate yield, %",
    "C10": "C10 no public macro signal: worst rule pts/yr / drifts",
    "R7a": "R7a first bar after a rate decision less all days, bp",
    "R7b": "R7b rate-news agent: pts/yr over holding, ahead of 30",
    "S1a": "S1a recession: share of the fall won back in 252",
    "V1": "V1 long-horizon variance ratio, 2y/1y and 5y/1y",
    "S1b": "S1b recession: seeds out of contraction in 24 months",
    "S2": "S2 recession: the index's rise 252 after its low, %",
    "E1": "E1 aggregate earnings fall in a contraction",
    "R1": "R1 2-year yield daily sd, bp",
    "R2": "R2 10-year yield daily sd, bp",
    "R3": "R3 stock-Treasury daily correlation",
    "R4": "R4 stock-IG corporate daily correlation",
    "D1": "D1 every ruled band in, all four cells",
}


# ------------------------------------------------------------ the tests

def within_rel(model, real, tol):
    r = model / real
    return abs(r - 1.0) <= tol + 1e-12, abs(r - 1.0) / tol, r


def within_abs(model, real, tol):
    d = model - real
    return abs(d) <= tol + 1e-12, abs(d) / tol, d


def within_ratio(model, real, lo, hi):
    r = model / real
    ok = lo - 1e-12 <= r <= hi + 1e-12
    use = (math.log(r) / math.log(hi)) if r >= 1 else (math.log(r) / math.log(lo)) if r > 0 else float("inf")
    return ok, use, r


# ------------------------------------------------------------ one arm

def score_arm(name, lr, tape, cg, edge, c4=None, xsec=None, bands=None):
    """Every criterion for one arm. lr: the report's arm block; tape: the
    report's tape block; cg: certgrade's arm block (or None); edge: edge.py's
    arm block (or None); c4: c4.py's arm block (or None). Returns
    {criterion: {...}}."""
    P, T, real = lr["point"], tape["point"], tape["replay"]
    R = lr.get("replay_summary") or {}
    out = {}

    # A: the replays, both windows must pass
    for cid, key, kind, tol, fmt in (("A1", "peak_rv21", "rel", 0.30, "%.0f"),
                                     ("A2", "max_drawdown", "rel", 0.30, "%.2f"),
                                     ("A3", "corr_peak", "abs", 0.15, "%.2f")):
        subs, ok_all, use_max, worst = [], True, 0.0, None
        for ep, year in EPISODES:
            m = R.get(ep, {}).get(key, {}).get("median")
            if m is None:
                subs.append({"episode": year, "model": None}); ok_all = None; continue
            ok, use, dev = (within_rel if kind == "rel" else within_abs)(m, real[ep][key], tol)
            note = ("%+.0f%%" % (100 * (dev - 1))) if kind == "rel" else ("%+.2f" % dev)
            subs.append({"episode": year, "model": m, "real": real[ep][key], "pass": ok, "usage": use,
                         "note": note})
            ok_all = ok_all and ok if ok_all is not None else None
            if use >= use_max:
                use_max, worst = use, f"{year} at {note}"
        out[cid] = {"pass": ok_all, "usage": use_max, "detail": subs, "edge_note": worst,
                    "real_text": " / ".join(fmt % s["real"] for s in subs if s.get("real") is not None),
                    "model_text": " / ".join("-" if s["model"] is None else fmt % s["model"] for s in subs)}

    # B: the long run against the tape's point values
    for cid, key, fmt, scale in (("B1", "vix_share_above_30", "%.1f%%", 100),
                                 ("B2", "spell30_mean_len", "%.0f", 1),
                                 ("B3", "dd20_per_decade", "%.2f", 1),
                                 ("B4", "dd10_per_decade", "%.2f", 1),
                                 ("B5", "days_below_-5_per_decade", "%.1f", 1),
                                 ("B6", "lvl_0", "%.1f%%", 100)):
        m, t = P.get(key), T.get(key)
        ok, use, r = within_ratio(m, t, 0.5, 2.0)
        out[cid] = {"pass": ok, "usage": use, "model": m, "real": t, "ratio": r, "key": key,
                    "edge_note": "%.2fx" % r, "real_text": fmt % (t * scale), "model_text": fmt % (m * scale)}
    m, t = P["ann_vol_pct"], T["ann_vol_pct"]
    ok, use, r = within_rel(m, t, 0.20)
    out["B7"] = {"pass": ok, "usage": use, "model": m, "real": t, "ratio": r, "key": "ann_vol_pct",
                 "edge_note": "%+.0f%%" % (100 * (r - 1)), "real_text": "%.1f" % t, "model_text": "%.1f" % m}
    m = P["ann_return_pct"]
    ok, use, d = within_abs(m, HONEST_RETURN, 2.0)
    out["B8"] = {"pass": ok, "usage": use, "model": m, "real": HONEST_RETURN, "diff": d,
                 "key": "ann_return_pct", "edge_note": "%+.1f points" % d,
                 "real_text": "%.2f" % HONEST_RETURN, "model_text": "%.1f" % m,
                 "tape_point": T.get("ann_return_pct")}

    # C: illusions a bot could learn
    years = sorted(int(k.split("_")[1]) for k in P if k.startswith("ycrash_"))
    early = [P[f"ycrash_{y}"] for y in years if y <= 2]
    late = [P[f"ycrash_{y}"] for y in years if y >= 3]
    if early and late and st.fmean(early) > 0:
        r = st.fmean(late) / st.fmean(early)
        ok, use, _ = within_ratio(r, 1.0, 2.0 / 3.0, 1.5)
        out["C1"] = {"pass": ok, "usage": use, "ratio": r, "years": [years[0], years[-1]],
                     "edge_note": "%.2fx" % r, "real_text": "1.0x", "model_text": "%.2fx" % r}
    else:
        out["C1"] = {"pass": None, "usage": None, "real_text": "1.0x", "model_text": "-"}
    hits, n = P.get("ceiling_hits"), lr.get("n")
    # At most one history in thirty: a rate, so a pooled set of 90 allows 3
    # (ptv20-registration.md, third registration). 30 histories allow 1, as
    # adopted.
    allowed = max(1, (n or 30) // 30)
    out["C2"] = {"pass": hits is not None and hits <= allowed, "usage": (hits or 0) / allowed, "hits": hits,
                 "histories": n, "edge_note": f"{hits} of {n}", "real_text": "0",
                 "model_text": "-" if hits is None else f"{hits} of {n}"}
    if edge is not None:
        c = edge["captured"][EDGE_TICK]
        e, se = c["mean_bp"], c["se_bp"]
        out["C3"] = {"pass": abs(e) < 20.0, "usage": abs(e) / 20.0, "edge_bp": e, "se_bp": se,
                     "events": edge.get("events"), "edge_note": "%+.1f bp" % e, "real_text": "~0",
                     "model_text": "%+.0fbp" % e}
    else:
        out["C3"] = {"pass": None, "usage": None, "real_text": "~0", "model_text": "not measured"}

    # C4: no price-only edge. The tape (a) and the user's view (b).
    a = (c4 or {}).get("c4a")
    if a is not None:
        acf, ratio = a["acf1"], a["roll_to_quoted"]
        ok = acf >= -0.05 - 1e-12 or ratio <= 2.0 + 1e-12
        use = min(max(0.0, -acf) / 0.05, ratio / 2.0)
        out["C4a"] = {"pass": ok, "usage": use, "acf1": acf, "acf1_se": a.get("acf1_se"),
                      "roll_to_quoted": ratio, "roll_bps": a.get("roll_bps"),
                      "quoted_bps": a.get("quoted_bps"),
                      "edge_note": "%+.3f, Roll %.1fx" % (acf, ratio),
                      "real_text": "~0 (Roll)", "model_text": "%+.3f / %.1fx" % (acf, ratio)}
    else:
        out["C4a"] = {"pass": None, "usage": None, "real_text": "~0 (Roll)", "model_text": "not measured"}
    b = (c4 or {}).get("c4b")
    if b is not None:
        graded = {k: v for k, v in b["specs"].items() if v.get("graded", True)}
        worst = max(graded, key=lambda k: max(graded[k]["median_excess"] / 5.0,
                                              graded[k]["wins"] / 14.0))
        w = graded[worst]
        ok = all(v["median_excess"] <= 5.0 + 1e-12 and v["wins"] <= 14 for v in graded.values())
        failing = [k for k, v in graded.items() if not (v["median_excess"] <= 5.0 + 1e-12 and v["wins"] <= 14)]
        out["C4b"] = {"pass": ok, "usage": max(w["median_excess"] / 5.0, w["wins"] / 14.0),
                      "worst": worst, "failing": failing,
                      "specs": {k: {"median_excess": v["median_excess"], "wins": v["wins"],
                                    "beats_oracle": v.get("beats_oracle")} for k, v in graded.items()},
                      "edge_note": "%s %+.1f pts, %d of 20" % (worst, w["median_excess"], w["wins"]),
                      "real_text": "~0",
                      "model_text": "%+.1f pts, %d/20" % (w["median_excess"], w["wins"])}
    else:
        out["C4b"] = {"pass": None, "usage": None, "real_text": "~0", "model_text": "not measured"}

    # C5-C8: the cross-section a strategy researcher tests (ptv20-registration.md)
    if xsec is not None and bands is not None:
        B = bands["rows"]
        def band_row(cid, vals, keys, fmt):
            ok = all(B[k]["band"][0] - 1e-12 <= v <= B[k]["band"][1] + 1e-12 for v, k in zip(vals, keys))
            use = max((abs(v - (B[k]["band"][0] + B[k]["band"][1]) / 2) / ((B[k]["band"][1] - B[k]["band"][0]) / 2))
                      for v, k in zip(vals, keys))
            return {"pass": ok, "usage": use, "values": vals, "bands": [B[k]["band"] for k in keys],
                    "real": [B[k]["real"] for k in keys], "edge_note": " / ".join(fmt % v for v in vals),
                    "real_text": " / ".join(fmt % B[k]["real"] for k in keys),
                    "model_text": " / ".join(fmt % v for v in vals)}
        out["C5"] = band_row("C5", [xsec["C5_vr60"]], ["C5"], "%.3f")
        out["C6"] = band_row("C6", [xsec["C6_value_ic"], xsec["C6_value_ic_0_60"]], ["C6", "C6"], "%+.3f")
        out["C6"]["real_text"] = "%+.3f" % B["C6"]["real"]
        out["C7"] = band_row("C7", [xsec["C7_mom12_1"], xsec["C7_mom6_1"]], ["C7_12_1", "C7_6_1"], "%+.3f")
        out["C8"] = band_row("C8", [xsec["C8_lm1_bps"]], ["C8"], "%+.1f")
        if xsec.get("E1_earnings_fall_median") is not None and "E1" in B:
            out["E1"] = band_row("E1", [xsec["E1_earnings_fall_median"]], ["E1"], "%+.3f")
        else:
            out["E1"] = {"pass": None, "usage": None, "real_text": "-", "model_text": "not measured"}
        drv = xsec.get("_driven")
        if drv is not None and "D2a" in B:
            out["D2"] = band_row("D2", [drv[0], drv[1]], ["D2a", "D2b"], "%.3f")
            out["D2"]["model_text"] = "%.3f / %s" % (drv[0], drv[1])
            out["D2"]["real_text"] = "0.339 / 126"
        else:
            out["D2"] = {"pass": None, "usage": None, "real_text": "0.339 / 126", "model_text": "not measured"}
        fl = xsec.get("_fastlead")
        if fl is not None and fl[0] is not None and "F1a" in B:
            out["F1"] = band_row("F1", [fl[0], fl[1]], ["F1a", "F1b"], "%.3f")
            out["F1"]["model_text"] = "%.3f / %s" % (fl[0], fl[1]); out["F1"]["real_text"] = "0.339 / 23"
            out["L1"] = band_row("L1", [fl[2]], ["L1"], "%+.0f")
        else:
            out["F1"] = {"pass": None, "usage": None, "real_text": "0.339 / 23", "model_text": "not measured"}
            out["L1"] = {"pass": None, "usage": None, "real_text": "+68", "model_text": "not measured"}
        c10 = xsec.get("_c10")
        if c10 is not None and c10.get("histories"):
            ok = bool(c10["pass"])
            use = max(c10["worst_median_excess"] / 1.0 if c10["worst_median_excess"] > 0 else 0.0,
                      c10["worst_share_ahead"] / (2 / 3),
                      c10["drift_63"]["contraction"] / -5.9 if c10["drift_63"]["contraction"] < 0 else 0.0,
                      c10["drift_63"]["recovery"] / 4.3 if c10["drift_63"]["recovery"] > 0 else 0.0)
            txt = "%+.2f, %.2f / %+.1f, %+.1f" % (c10["worst_median_excess"], c10["worst_share_ahead"],
                                                  c10["drift_63"]["contraction"], c10["drift_63"]["recovery"])
            out["C10"] = {"pass": ok, "usage": use, "model_text": txt, "real_text": "-2.0 / -5.9, +4.3",
                          "edge_note": "worst rule %s" % c10["worst"],
                          "values": [c10["worst_median_excess"], c10["worst_share_ahead"],
                                     c10["drift_63"]["contraction"], c10["drift_63"]["recovery"]],
                          "real": [-2.0, round(11 / 34, 3), -5.9, 4.3], "worst": c10["worst"]}
        else:
            out["C10"] = {"pass": None, "usage": None, "real_text": "-2.0 / -5.9, +4.3", "model_text": "not measured"}
        d22 = xsec.get("_driven2022")
        if d22 is not None and "R5" in B:
            out["R5"] = band_row("R5", [d22[0]], ["R5"], "%.3f")
            out["R6"] = band_row("R6", [d22[1]], ["R6"], "%+.2f")
        else:
            out["R5"] = {"pass": None, "usage": None, "real_text": "0.254", "model_text": "not measured"}
            out["R6"] = {"pass": None, "usage": None, "real_text": "-5.2", "model_text": "not measured"}
        score_twelfth(out, xsec)
        imp = xsec.get("_impact")
        if imp is not None and "C9" in B:
            ex, co = imp["exponent"], imp["coefficient"]
            be, bc = B["C9"]["band_exponent"], B["C9"]["band_coefficient"]
            ok = be[0] <= ex <= be[1] and bc[0] <= co <= bc[1]
            use = max(abs(ex - sum(be) / 2) / ((be[1] - be[0]) / 2), abs(co - sum(bc) / 2) / ((bc[1] - bc[0]) / 2))
            out["C9"] = {"pass": ok, "usage": use, "values": [ex, co], "real": [0.5, 0.5],
                         "edge_note": "%.3f / %.3f" % (ex, co), "real_text": "0.5 / 0.5",
                         "model_text": "%.3f / %.3f" % (ex, co)}
        else:
            out["C9"] = {"pass": None, "usage": None, "real_text": "0.5 / 0.5", "model_text": "not measured"}
        for cid, key, fmt in (("R1", "R1_dy2_sd_bp", "%.2f"), ("R2", "R2_dy10_sd_bp", "%.2f"),
                              ("R3", "R3_corr_stock_tsy", "%+.3f"), ("R4", "R4_corr_stock_corp", "%+.3f")):
            if xsec.get(key) is not None and cid in B:
                out[cid] = band_row(cid, [xsec[key]], [cid], fmt)
            else:
                out[cid] = {"pass": None, "usage": None, "real_text": "-", "model_text": "not measured"}
        if xsec.get("B9_annual_sd_pct") is not None:
            out["B9"] = band_row("B9", [xsec["B9_annual_sd_pct"], xsec["B9_startup_ratio"]],
                                 ["B9_annual", "B9_startup"], "%.2f")
        else:
            out["B9"] = {"pass": None, "usage": None, "real_text": "17.4 / 1.0", "model_text": "not measured"}
    else:
        for cid in XSEC_ROWS:
            out[cid] = {"pass": None, "usage": None, "real_text": "-", "model_text": "not measured"}

    # D: the one-year table
    if cg is None:
        out["D1"] = {"pass": None, "usage": None, "real_text": "--", "model_text": "not graded"}
    else:
        bands = cg.get("bands", {})
        missing = [c for c in CERT_CELLS if not bands.get(c)]
        misses = {c: bands[c]["misses"] for c in CERT_CELLS if bands.get(c) and bands[c]["misses"]}
        level_out = [r for r, v in (cg.get("level") or {}).items() if v.get("in_band") is False]
        # D1's crisis sector dispersion needs at least ten readings (ptv20-registration.md,
        # 2026-09-26): certgrade_box.py grades the row on the cell's own seeds and the
        # extension; a cell short of ten with no extension is `unresolved`, which
        # cannot pass D1 (a miss elsewhere still fails it). A certgrade written before
        # the rule carries no `crisis_dispersion` block and is read as it was graded.
        unresolved = {c: bands[c]["unresolved"] for c in CERT_CELLS if bands.get(c) and bands[c].get("unresolved")}
        crisis = cg.get("crisis_dispersion")
        ok = None if missing else (False if misses or level_out else (None if unresolved else True))
        out["D1"] = {"pass": ok, "usage": 0.0 if ok else (None if ok is None else 1.0 + len(misses)),
                     "cells_missing": missing, "misses": misses, "level_out": level_out,
                     "unresolved": unresolved, "crisis_dispersion": crisis,
                     "in_band": {c: bands[c]["in_band"] for c in CERT_CELLS if bands.get(c)},
                     "unreadable": {c: bands[c]["unreadable"] for c in CERT_CELLS
                                    if bands.get(c) and bands[c]["unreadable"]},
                     "level_rows": len(cg.get("level") or {}),
                     "real_text": "--",
                     "model_text": ("cells missing: " + ",".join(missing)) if missing else
                                   ("in" if ok else ("crisis row unresolved in %d cell(s)" % len(unresolved) if ok is None else
                                    "misses " + json.dumps(misses) + (" level OUT " + ",".join(level_out) if level_out else "")))}
    return out


def score_twelfth(out, xsec):
    """R7a, R7b, S1a, S1b and S2 (twelfth registration). Their bounds are the
    registration's; the instruments write their own verdicts, and these are
    re-derived here from the numbers, not read from them."""
    ev = xsec.get("_r7event")
    if ev is not None and ev.get("hike", {}).get("n") and ev.get("cut", {}).get("n"):
        h, c = ev["hike"], ev["cut"]
        ok = all(abs(x["excess_bp"]) <= x["bound_bp"] + 1e-12 for x in (h, c))
        use = max(abs(x["excess_bp"]) / x["bound_bp"] for x in (h, c))
        out["R7a"] = {"pass": ok, "usage": use, "values": [h["excess_bp"], c["excess_bp"]], "real": [0.0, 0.0],
                      "bounds": [h["bound_bp"], c["bound_bp"]],
                      "model_text": "%+.1f / %+.1f" % (h["excess_bp"], c["excess_bp"]), "real_text": "0 / 0",
                      "edge_note": "hike %+.1f of %.1f, cut %+.1f of %.1f" % (h["excess_bp"], h["bound_bp"],
                                                                            c["excess_bp"], c["bound_bp"])}
    else:
        out["R7a"] = {"pass": None, "usage": None, "real_text": "0 / 0", "model_text": "not measured"}
    rv = xsec.get("_r7eval")
    if rv is not None and rv.get("n"):
        n, m, k = rv["n"], rv["mean_excess_pts_yr"], rv["ahead"]
        cap = 20 * n / 30.0
        ok = m <= 0.0 + 1e-12 and k <= cap + 1e-12
        use = max(k / cap, 1.0 + m if m > 0 else 0.0)
        out["R7b"] = {"pass": ok, "usage": use, "values": [m, k], "real": [0.0, 15],
                      "model_text": "%+.2f, %d/%d" % (m, k, n), "real_text": "0, <=20/30",
                      "edge_note": "%+.2f pts, %d of %d" % (m, k, n)}
    else:
        out["R7b"] = {"pass": None, "usage": None, "real_text": "0, <=20/30", "model_text": "not measured"}
    rc = xsec.get("_recession")
    if rc is not None and rc.get("n"):
        def band(cid, v, lo, hi, real, fmt, real_text):
            ok = v is not None and math.isfinite(v) and lo - 1e-12 <= v <= hi + 1e-12
            use = abs(v - (lo + hi) / 2) / ((hi - lo) / 2) if v is not None and math.isfinite(v) else None
            out[cid] = {"pass": ok, "usage": use, "values": [v], "real": [real], "bands": [[lo, hi]],
                        "model_text": fmt % v, "real_text": real_text, "edge_note": fmt % v}
        band("S1a", rc["S1a_regain_mean"], 0.45, 1.00, 0.62, "%.2f", "0.62 (2009)")
        band("S2", rc["S2_rise_mean_pct"], 25.0, 80.0, 69.0, "%+.1f", "+69 (2009)")
        k, n = rc["S1b_out_within_24m"], rc["n"]
        out["S1b"] = {"pass": k == n, "usage": 0.0 if k == n else 1.0 + (n - k), "values": [k], "real": [n],
                      "model_text": "%d/%d" % (k, n), "real_text": "all seeds",
                      "edge_note": "%d of %d" % (k, n)}
    else:
        for cid, rt in (("S1a", "0.62 (2009)"), ("S1b", "all seeds"), ("S2", "+69 (2009)")):
            out[cid] = {"pass": None, "usage": None, "real_text": rt, "model_text": "not measured"}
    v1 = xsec.get("_v1")
    if v1 is not None and "V1a" in v1 and "V1b" in v1:
        vals = [v1["V1a"]["value"], v1["V1b"]["value"]]
        bands = [V1_BANDS["V1a"], V1_BANDS["V1b"]]
        ok = all(lo - 1e-12 <= v <= hi + 1e-12 for v, (lo, hi) in zip(vals, bands))
        use = max(abs(v - (lo + hi) / 2) / ((hi - lo) / 2) for v, (lo, hi) in zip(vals, bands))
        out["V1"] = {"pass": ok, "usage": use, "values": vals, "real": [0.93, 0.87], "bands": [list(b) for b in bands],
                     "model_text": "%.2f / %.2f" % tuple(vals), "real_text": "0.93 / 0.87",
                     "edge_note": "%.2f / %.2f" % tuple(vals)}
    else:
        out["V1"] = {"pass": None, "usage": None, "real_text": "0.93 / 0.87", "model_text": "not measured"}


# ------------------------------------------------------------ the eighteenth registration's R4 and D1

def apply_reg18(out, r18, cg, bands):
    """Regrade R4 and D1 in `out` (score_arm's rows for one arm) on the eighteenth registration's definitions
    (DEFINITIONS["reg18"]) from r18, cert_reg18.py's block for the arm. The twelfth-definition rows are kept in
    out["_reg12"] and reported. An arm with no reg18 block, or a block short of the rule, is unscored on both rows
    (a verdict for it is refused)."""
    out["_reg12"] = {"R4": dict(out.get("R4") or {}), "D1": dict(out.get("D1") or {})}
    lvl12 = {k: dict(v) for k, v in ((cg or {}).get("level") or {}).items()}
    out["_reg12"]["D1"]["cert30"] = lvl12
    if not r18:
        for cid in ("R4", "D1"):
            out[cid] = {"pass": None, "usage": None, "real_text": out[cid].get("real_text", "-"),
                        "model_text": "not measured (reg18)"}
        return
    # R4 on the held close, the band and real of the row
    held = (r18.get("R4_held") or {}).get("value")
    b = (bands or {}).get("R4") or {}
    lo, hi = (b.get("band") or [0.15, 0.39])
    real = b.get("real", 0.272)
    ok = held is not None and math.isfinite(held) and lo - 1e-12 <= held <= hi + 1e-12
    old = out["_reg12"]["R4"]
    out["R4"] = {"pass": bool(ok) if held is not None else None,
                 "usage": None if held is None else abs(held - (lo + hi) / 2) / ((hi - lo) / 2),
                 "values": [held], "bands": [[lo, hi]], "real": [real],
                 "edge_note": "held close" if held is None else "%+.3f held close" % held,
                 "real_text": "%+.3f" % real,
                 "model_text": "--" if held is None else "%+.3f held (%s last print)" % (
                     held, old.get("model_text", "-")),
                 "n": (r18.get("R4_held") or {}).get("n"), "last_print": (old.get("values") or [None])[0]}
    # D1: the level rows pooled over at least 360 seeds; the cells' ruled bands as before
    d1 = dict(out["D1"])
    if d1.get("pass") is None and not cg:
        out["D1"] = d1
        return
    pooled = r18.get("level") or {}
    level_out = []
    for row, v in lvl12.items():
        pv = pooled.get(row)
        if row in ("index_tail_dn3_pct", "index_drift_pct"):
            n = (pv or {}).get("n") or 0
            val = (pv or {}).get("value")
            band = v.get("band")
            inb = val is not None and n >= REG18_POOL_MIN and band is not None and band[0] <= val <= band[1]
            if not inb:
                level_out.append(row)
        elif v.get("in_band") is False:
            level_out.append(row)
    bands_ = (cg or {}).get("bands", {})
    missing = d1.get("cells_missing") or []
    misses = d1.get("misses") or {}
    unresolved = d1.get("unresolved") or {}
    ok = None if missing else (False if misses or level_out else (None if unresolved else True))
    d1.update({"pass": ok, "usage": 0.0 if ok else (None if ok is None else 1.0 + len(misses)),
               "level_out": level_out, "level_pooled": pooled,
               "model_text": ("cells missing: " + ",".join(missing)) if missing else
                             ("in" if ok else ("crisis row unresolved in %d cell(s)" % len(unresolved) if ok is None else
                              "misses " + json.dumps(misses) + (" level OUT " + ",".join(level_out) if level_out else "")))})
    out["D1"] = d1


# ------------------------------------------------------------ the verdict block

# The words, rules and rounding of the block the trading server reads
# (tradefloor.serve.core.long_run_failures prints "<id> <words>: <value>
# against <real> real (<rule>)" for each failed row), as first written into
# the fourth composition's record on engine integration/next at 209cbd1.
WORDS = {
    "A1": "worst month's volatility in the 2008 and 2020 replays within 30% of real",
    "A2": "maximum drawdown in the 2008 and 2020 replays within 30% of real",
    "A3": "peak stock correlation in the 2008 and 2020 replays within 0.15 of real",
    "B1": "share of sessions with the VIX above 30",
    "B2": "mean length of a fear spell above VIX 30, sessions",
    "B3": "20% bear markets per decade",
    "B4": "10% corrections per decade",
    "B5": "sessions down more than 5% per decade",
    "B6": "share of sessions with the VIX under 15",
    "B7": "index annual volatility, %",
    "B8": "long-run index return, % a year",
    "C1": "crash rate in years 3-21 against years 1-2",
    "C2": "histories touching the VIX ceiling, of 30",
    "C3": "edge from reading a headline 5 ticks late, bp",
    "C4a": "65-minute lag-1 autocorrelation of print returns, median name; Roll spread over quoted",
    "C4b": "the price-only rule furthest over its line on tf-suite-2026.1 (named in `worst`): median points over buy-and-hold, markets beaten of 20",
    "B9": "sd of annual index log returns, years 2-21, per cent; the start-up drift: sd of the first 60 sessions' index return over the steady state's, across 50 markets",
    "C5": "stock-level (idiosyncratic) variance ratio at 60 sessions, median name, 30 histories of 2,660 sessions",
    "C6": "rank IC of the value signal on published fundamentals against the next 20 sessions: whole history, first 60 sessions",
    "C7": "rank IC of 12-1 and 6-1 month momentum against the next 20 sessions",
    "C8": "Lo-MacKinlay one-day loser-minus-winner book, bp a day",
    "C9": "the cost of size in the agent-facing book: exponent and coefficient of the average cost against Q/V, in sigma units (impact_curve.py)",
    "D2": "the driven 2020-21 market: the index's maximum drawdown and the sessions from the pre-crash high back to it, medians over histories (driven2020.py)",
    "F1": "the driven 2020 path's fast crash: the high to the lowest close within 60 sessions, and the sessions it took (driven2020.py)",
    "L1": "look-through: sessions from the index's trough to the aggregate earnings level's trough on the driven 2020 path (driven2020.py)",
    "R5": "the driven 2022 market: the index's maximum drawdown (driven2022.py)",
    "R6": "the driven 2022 market: the market P/E's log change per 100 bp of the corporate yield, monthly averages (driven2022.py)",
    "C10": "timing rules on published macro data against buy-and-hold, and the drift after a published turn (c10.py)",
    "R7a": "the equal-weight index's mean log move from the first price readable after a changed policy rate to the end of the first 65-minute bar, less the mean first bar of all days, bp: after a hike, after a cut (r7_event.py)",
    "R7b": "the audit's rate-news agent through tf.evaluate: mean annual excess over holding in points, histories ahead (r7_eval.py)",
    "S1a": "the packaged recession: share of the index's paired log fall, at its lowest, won back 252 sessions later, mean over seeds (recession_rows.py)",
    "V1": "the index's variance ratio at two and five years relative to one, years 2-21 of the pooled free histories (v1.py)",
    "S1b": "the packaged recession: seeds whose cycle has left contraction and trough within 24 months of the onset (recession_rows.py)",
    "S2": "the packaged recession: the index's own rise in the 252 sessions after its low, per cent, mean over seeds (recession_rows.py)",
    "E1": "the aggregate earnings fall around a contraction, median over the 30 histories' contractions (Shiller 1953-2020)",
    "R1": "sd of the 2-year Treasury yield's daily change, bp (FRED DGS2 2015-2025)",
    "R2": "sd of the 10-year Treasury yield's daily change, bp (FRED DGS10)",
    "R3": "daily correlation of the roster index with a Treasury bond's return (minus the 10-year's change; SPY against IEF)",
    "R4": "daily correlation of the roster index with an IG bond's return (minus the corporate yield's change; SPY against LQD)",
    "D1": "ruled bands of the one-year realism table in, on all four cells",
}
RULE = {
    "A1": "within 30%", "A2": "within 30%", "A3": "within 0.15",
    "B1": "1/2x to 2x", "B2": "1/2x to 2x", "B3": "1/2x to 2x", "B4": "1/2x to 2x",
    "B5": "1/2x to 2x", "B6": "1/2x to 2x",
    "B7": "within 20%",
    "B8": "within 2 points of the honest target",
    "C1": "2/3x to 1.5x",
    "C2": "at most 1",
    "C3": "under 20 bp",
    "C4a": "at or above -0.05, or Roll at most 2x quoted",
    "C4b": "every rule at most +5 points and 14 of 20",
    "B9": "annual within 20% of 17.4; start-up 2/3x to 1.5x",
    "C5": "in 0.80 to 1.05",
    "C6": "both in -0.03 to +0.05",
    "C7": "12-1 in -0.04 to +0.095, 6-1 in -0.02 to +0.10",
    "C8": "in -6.4 to +2.9",
    "C9": "exponent in 0.4 to 0.7 and coefficient in 0.33 to 0.67",
    "D2": "drawdown in 0.237 to 0.441 and sessions in 63 to 252",
    "F1": "drop in 0.237 to 0.441 within 12 to 46 sessions",
    "L1": "lead in 1 to 136 sessions",
    "R5": "drawdown in 0.178 to 0.330",
    "R6": "-10.4 to -2.6 per cent",
    "C10": "every rule at most +1.0 point a year and ahead in at most 2/3; drifts no larger than the S&P's after NBER turns",
    "R7a": "each within 5 bp, or 2 se where wider",
    "R7b": "at most 0 points a year, ahead in at most 20 of 30",
    "S1a": "in 45% to 100%",
    "V1": "2y/1y in 0.75 to 1.15 and 5y/1y in 0.55 to 1.20",
    "S1b": "every seed",
    "S2": "in +25% to +80%",
    "E1": "in -0.40 to -0.046",
    "R1": "in 3.65 to 6.80", "R2": "in 4.54 to 6.27",
    "R3": "in -0.36 to +0.03", "R4": "in +0.15 to +0.39",
    "D1": "every band in",
}
#: Printed precision per row: the record carries what a reader is shown.
PLACES = {"A1": 1, "A2": 3, "A3": 3, "B1": 3, "B2": 0, "B3": 2, "B4": 2, "B5": 1, "B6": 3,
          "B7": 1, "B8": 1, "C1": 2, "C3": 1, "C4a": 3}


def _rnd(cid, v):
    if isinstance(v, list):
        return [_rnd(cid, x) for x in v]
    if v is None or cid not in PLACES:
        return v
    return int(round(v)) if PLACES[cid] == 0 else round(float(v), PLACES[cid])


def _row(cid, c):
    if cid in ("A1", "A2", "A3"):
        value = [d.get("model") for d in c["detail"]]
        real = [d.get("real") for d in c["detail"]]
    elif cid == "B8":
        value, real = c["model"], HONEST_RETURN
    elif cid == "C1":
        value, real = c.get("ratio"), 1.0
    elif cid == "C2":
        value, real = c.get("hits"), 0
    elif cid == "C3":
        value, real = c.get("edge_bp"), 0
    elif cid == "C4a":
        value = [_rnd(cid, c.get("acf1")), round(float(c.get("roll_to_quoted")), 2)]
        return {"id": cid, "words": WORDS[cid], "value": value, "real": [0.0, 1.0],
                "rule": RULE[cid], "pass": c["pass"]}
    elif cid == "C4b":
        spec = c["specs"][c["worst"]]
        # Numbers only in `value`, as every other row: readers format it as
        # numbers. The rule's name goes beside it.
        return {"id": cid, "words": WORDS[cid],
                "value": [round(float(spec["median_excess"]), 1), spec["wins"]],
                "real": [0.0, 10], "rule": RULE[cid], "pass": c["pass"],
                "worst": c["worst"], "failing": c.get("failing", [])}
    elif cid in XSEC_ROWS:
        return {"id": cid, "words": WORDS[cid], "value": [round(float(v), 4) for v in c["values"]],
                "real": c["real"], "rule": RULE[cid], "pass": c["pass"]}
    elif cid == "D1":
        misses = ["%s: %s" % (cell, ", ".join(rows)) for cell, rows in (c.get("misses") or {}).items()]
        misses += ["level: %s" % r for r in c.get("level_out") or []]
        value, real = ("all" if not misses else "; ".join(misses) + " out"), "all"
    else:
        value, real = c["model"], c["real"]
    real = HONEST_RETURN if cid == "B8" else _rnd(cid, real)
    return {"id": cid, "words": WORDS[cid], "value": _rnd(cid, value), "real": real,
            "rule": RULE[cid], "pass": c["pass"]}


def verdict_block(name, scored, lr_arm, edge, edge_src, a):
    sc = scored[name]
    missing = [c for c in ORDER if sc[c]["pass"] is None]
    if missing:
        sys.exit(f"REFUSED: {name} has {len(missing)} criterion/criteria unscored ({', '.join(missing)}); "
                 "a verdict with holes is not written")
    if "D1" in ORDER and sc["D1"].get("crisis_dispersion") is None:
        sys.exit(f"REFUSED: {name}'s certgrade predates D1's ten-reading rule for crisis_sector_dispersion "
                 "(no crisis_dispersion block); regrade the cells with certgrade_box.py")
    rows = [_row(c, sc[c]) for c in ORDER]
    D = DEFINITIONS[a.definitions]
    for r in rows:
        if r["id"] in D["rows"]:
            r["definition"] = D["version"]
            r12 = (sc.get("_reg12") or {}).get(r["id"]) or {}
            if r["id"] == "R4":
                r["reg12_reading"] = {"value": (r12.get("values") or [None])[0], "pass": r12.get("pass"),
                                      "form": "at the last print, reported"}
            elif r["id"] == "D1":
                r["reg12_reading"] = {"pass": r12.get("pass"), "level_cert30": r12.get("cert30"),
                                      "form": "level rows on the 30 cert seeds, reported"}
    passed = sum(1 for r in rows if r["pass"])
    meta = lr_arm["meta"]
    return {
        **({"definitions": {"version": D["version"], "rows": D["rows"], "why": D["why"], "registered_in": D["cite"],
                            **({"inputs": a.reg18} if a.reg18 else {})}} if a.definitions != "reg12" else {}),
        "criteria": "programme/longrun/CRITERIA.md (adopted 2026-09-23; C4a and C4b added 2026-09-24)"
                    + ("; C5-C8 registered 2026-09-24 in programme/ptv20-registration.md" if "C5" in ORDER else "")
                    + ("; C10 the eleventh registration, R7, S1 and S2 the twelfth" if "R7a" in ORDER else ""),
        "verdict": "pass" if passed == len(rows) else "fail",
        "passed": passed,
        "of": len(rows),   # the rows graded: seventeen, or twenty-one with --xsec
        "rows": rows,
        "measured": {
            "engine_commit": (meta.get("build") or {}).get("commit"),
            "seeds": lr_arm["n"], "years": lr_arm["years"],
            "box": a.box, "date": a.date,
            "fingerprint": meta.get("fingerprint"), "base": meta.get("base"),
            "dials": meta.get("dials") or {},
            "longrun_report": a.longrun, "certgrade": a.certgrade,
            "c10": a.c10, "r7": a.r7 or None, "recession": a.recession,
            "edge": {"source": edge_src, **{k: v for k, v in ((edge or {}).get("_design") or {}).items()}},
            "c4": {"source": getattr(a, "_c4_src", {}).get(name),
                   "tradefloor_version": getattr(a, "_c4_version", {}).get(name)},
            "computed_by": "programme/longrun/criteria.py",
            "note": "computed by programme/longrun/criteria.py from the long-run report, the "
                    "certification grade and the edge named here",
        },
    }


# ------------------------------------------------------------ inputs

def parse_arm(text):
    parts = [p.strip() for p in text.split(",") if p.strip()]
    spec = {"name": parts[0], "cert": parts[0], "edge": parts[0], "c4": parts[0], "xsec": parts[0]}
    for p in parts[1:]:
        k, _, v = p.partition("=")
        if k not in ("cert", "edge", "c4", "xsec"):
            sys.exit(f"--arm {text}: unknown key {k!r} (cert=, edge=, c4=, xsec=)")
        spec[k] = v
    return spec


def load_c4(paths):
    """c4.py reports -> {arm: block}, {arm: path}, {arm: engine version}.
    One arm in two files is refused, as for --edge: name the one you mean."""
    arms, src, ver = {}, {}, {}
    for p in paths:
        d = json.load(open(p))
        for name, body in d["arms"].items():
            prev = arms.setdefault(name, {})
            for part in ("c4a", "c4b"):
                if part in body:
                    if part in prev:
                        sys.exit(f"REFUSED: c4 arm {name} carries {part} in {src[name]} and {p}")
                    prev[part] = body[part]
            src[name] = ", ".join(x for x in (src.get(name), p) if x)
            ver[name] = d.get("tradefloor_version") + (
                f" at {d['engine_commit'][:12]}" if d.get("engine_commit") else "")
    return arms, src, ver


def load_edges(paths):
    arms, src = {}, {}
    for p in paths:
        d = json.load(open(p))
        for name, body in d["arms"].items():
            if name in arms and arms[name]["captured"][EDGE_TICK] != body["captured"][EDGE_TICK]:
                sys.exit(f"REFUSED: edge arm {name} is in {src[name]} and {p} with different readings; "
                         "name the one you mean with --arm NAME,edge=OTHER")
            arms[name], src[name] = body, p
            body["_design"] = {k: d.get(k) for k in ("base", "seeds", "first_seed", "days")}
    return arms, src


def fmt_cell(c):
    mark = "pass" if c["pass"] else ("FAIL" if c["pass"] is False else "n/a")
    return f"{c['model_text']}  {mark}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--longrun", required=True)
    ap.add_argument("--certgrade")
    ap.add_argument("--edge", action="append", default=[])
    ap.add_argument("--xsec", help="results/ptv20/grade_xsec.py's report (B9, C5-C8, R1-R4, E1)")
    ap.add_argument("--driven", help="driven2020.py's JSON (D2, F1, L1), every arm in it")
    ap.add_argument("--driven2022", help="driven2022.py's JSON (R5, R6), every arm in it")
    ap.add_argument("--c10", help="c10.py's JSON (C10), every arm in it")
    ap.add_argument("--r7", action="append", default=[],
                    help="r7_event.py's JSON (R7a) and r7_eval.py's JSON (R7b); give the flag once for each")
    ap.add_argument("--recession", help="recession_rows.py's JSON (S1a, S1b, S2), every arm in it")
    ap.add_argument("--v1", help="V1, the long-horizon variance ratio: v1.py's JSON")
    ap.add_argument("--impact", action="append", default=[],
                    help="ARM=FILE: tools/calibration/impact_curve.py's --out for that arm (C9)")
    ap.add_argument("--c4", action="append", default=[],
                    help="c4.py's report (C4a, C4b); may be given twice, one per part")
    ap.add_argument("--arm", action="append", required=True)
    ap.add_argument("--edge-run", action="store_true",
                    help="measure the edge for arms no --edge file carries (engine python)")
    ap.add_argument("--edge-out", help="where --edge-run writes its edge report")
    ap.add_argument("--edge-seeds", type=int, default=10)
    ap.add_argument("--edge-days", type=int, default=300)
    ap.add_argument("--edge-workers", type=int, default=3)
    ap.add_argument("--out"); ap.add_argument("--json")
    ap.add_argument("--expect", action="append", default=[],
                    help="NAME=A1,B5,... : exit 1 unless NAME fails exactly these")
    ap.add_argument("--verdict", help="write --verdict-arm's verdict block (a record's long_run) here")
    ap.add_argument("--verdict-arm")
    ap.add_argument("--box", help="the run that measured the long run (the verdict's measured.box)")
    ap.add_argument("--date", default="2026-09-23")
    ap.add_argument("--definitions", choices=sorted(DEFINITIONS), default="reg12",
                    help="which registration's definitions of R4 and D1 grade (default reg12, unchanged)")
    ap.add_argument("--reg18", help="with --definitions reg18: results/ptv21/cert_reg18.py's JSON")
    a = ap.parse_args()
    global ORDER
    if not a.xsec:
        ORDER = [c for c in ORDER if c not in XSEC_ROWS]
    xs, xbands = {}, None
    if a.xsec:
        xd = json.load(open(a.xsec))
        xs, xbands = xd["arms"], xd["bands"]
        for spec in a.impact:
            arm, _, path = spec.partition("=")
            fit = json.load(open(path))["on"]["fit_avg"]
            xs.setdefault(arm, {})["_impact"] = fit
        if a.driven:
            for arm, row in json.load(open(a.driven))["arms"].items():
                xs.setdefault(arm, {})["_driven"] = (row["D2a_mdd"], row["D2b_recovery_sessions"])
                xs[arm]["_fastlead"] = (row.get("F1a_fast_drop"), row.get("F1b_fast_sessions"),
                                        row.get("L1_earnings_lead"))
        if a.c10:
            for arm, row in json.load(open(a.c10)).items():
                xs.setdefault(arm, {})["_c10"] = row
        if a.driven2022:
            for arm, row in json.load(open(a.driven2022))["arms"].items():
                xs.setdefault(arm, {})["_driven2022"] = (row["R5_mdd"], row["R6_pe_per_100bp"])
        for path in a.r7:
            d = json.load(open(path))
            key = {"ptv20-r7-event": "_r7event", "ptv20-r7-eval": "_r7eval"}.get(d.get("kind"))
            if key is None:
                sys.exit(f"REFUSED: --r7 {path} is kind {d.get('kind')!r}, not r7_event.py's or r7_eval.py's")
            for arm, row in d["arms"].items():
                if key in xs.get(arm, {}):
                    sys.exit(f"REFUSED: --r7 gives {key[1:]} for {arm} twice")
                xs.setdefault(arm, {})[key] = row
        if a.recession:
            d = json.load(open(a.recession))
            if d.get("kind") != "ptv20-recession-rows":
                sys.exit(f"REFUSED: --recession {a.recession} is kind {d.get('kind')!r}, not recession_rows.py's")
            for arm, row in d["arms"].items():
                xs.setdefault(arm, {})["_recession"] = row
    if a.v1:
        d = json.load(open(a.v1))
        if d.get("kind") != "v1":
            sys.exit(f"REFUSED: --v1 {a.v1} is kind {d.get('kind')!r}, not v1.py's")
        if {k: tuple(v) for k, v in d.get("bands", {}).items()} != V1_BANDS:
            sys.exit(f"REFUSED: --v1 {a.v1} carries bands {d.get('bands')}, not the registered {V1_BANDS}")
        for arm, row in d["arms"].items():
            xs.setdefault(arm, {})["_v1"] = row

    r18 = None
    if a.definitions == "reg18":
        if not a.reg18:
            sys.exit("REFUSED: --definitions reg18 needs --reg18 FILE (results/ptv21/cert_reg18.py's output)")
        r18 = json.load(open(a.reg18))
        if r18.get("kind") != "cert-reg18":
            sys.exit(f"REFUSED: --reg18 {a.reg18} is kind {r18.get('kind')!r}, not cert_reg18.py's")
    elif a.reg18:
        sys.exit("REFUSED: --reg18 is read only with --definitions reg18")
    rep = json.load(open(a.longrun))
    tape = rep["tape"]
    cg = json.load(open(a.certgrade))["arms"] if a.certgrade else {}
    arms = [parse_arm(t) for t in a.arm]
    edges, esrc = load_edges(a.edge)
    c4s, c4src, c4ver = load_c4(a.c4)
    a._c4_src = {s["name"]: c4src.get(s["c4"]) for s in arms}
    a._c4_version = {s["name"]: c4ver.get(s["c4"]) for s in arms}

    todo = [s for s in arms if s["edge"] not in edges]
    if todo and a.edge_run:
        sys.path.insert(0, str(HERE.parent / "results" / "news-speed"))
        import edge as EDGE  # noqa: E402
        texts = []
        for s in todo:
            meta = rep["arms"][s["name"]]["meta"]
            body = ",".join(f"{k}={v!r}" for k, v in (meta.get("dials") or {}).items())
            texts.append(f"{s['edge']}@{meta['base']}:{body}")
        print(f"measuring the edge on {len(texts)} arm(s): {[s['edge'] for s in todo]}", file=sys.stderr)
        er = EDGE.measure(texts, base=texts and todo and rep["arms"][todo[0]["name"]]["meta"]["base"],
                          seeds=a.edge_seeds, days=a.edge_days, workers=a.edge_workers)
        if a.edge_out:
            json.dump(er, open(a.edge_out, "w"), indent=1)
        for name, body in er["arms"].items():
            body["_design"] = {k: er.get(k) for k in ("base", "seeds", "first_seed", "days")}
            edges[name], esrc[name] = body, a.edge_out or "(--edge-run)"

    L = []
    say = lambda s="": (print(s), L.append(s))
    scored = {}
    for s in arms:
        if s["name"] not in rep["arms"]:
            sys.exit(f"REFUSED: {s['name']} is not an arm of {a.longrun} ({sorted(rep['arms'])})")
        scored[s["name"]] = score_arm(s["name"], rep["arms"][s["name"]], tape,
                                      cg.get(s["cert"]), edges.get(s["edge"]),
                                      c4s.get(s["c4"]), xs.get(s["xsec"]), xbands)
        if a.definitions == "reg18":
            apply_reg18(scored[s["name"]], (r18 or {}).get("arms", {}).get(s["cert"]), cg.get(s["cert"]), xbands)

    W = 26
    say("PASS CRITERIA (programme/longrun/CRITERIA.md, adopted 2026-09-23, C4 added 2026-09-24"
        + (", C5-C8 registered 2026-09-24" if a.xsec else "") + "), computed by criteria.py")
    D = DEFINITIONS[a.definitions]
    if a.definitions != "reg12":      # the default's text is as it was
        say(f"definitions {D['version']}: {D['why']}; registered in {'; '.join(D['cite'])}"
            + (f"; inputs {a.reg18}" if a.reg18 else ""))
        for cid, words in D["rows"].items():
            say(f"  {cid}: {words}")
    say(f"long run  {a.longrun}")
    for s in arms:
        m = rep["arms"][s["name"]]["meta"]
        say(f"  {s['name']}: base {m.get('base')}, {len(m.get('dials') or {})} dials, "
            f"{rep['arms'][s['name']]['n']} seeds x {rep['arms'][s['name']]['years']} years, "
            f"engine {str(m.get('build', {}).get('commit', '?'))[:12]}, fingerprint {m.get('fingerprint')}")
    say(f"cert      {a.certgrade or '-'}  (arms " + ", ".join(f"{s['name']}={s['cert']}" for s in arms) + ")")
    for s in arms:
        e = edges.get(s["edge"])
        if e is not None:
            d = e["_design"]
            say(f"edge      {s['name']}={s['edge']} from {esrc[s['edge']]}: base {e.get('base', d['base'])}, "
                f"{d['seeds']} seeds from {d['first_seed']} x {d['days']} days, {e.get('events')} events")
        else:
            say(f"edge      {s['name']}: NOT MEASURED")
    for s in arms:
        c = c4s.get(s["c4"])
        if c is not None:
            say(f"c4        {s['name']}={s['c4']} from {c4src[s['c4']]} (tradefloor {c4ver.get(s['c4'])}): "
                + ", ".join(k for k in ("c4a", "c4b") if k in c))
        else:
            say(f"c4        {s['name']}: NOT MEASURED")
    say()
    say("    %-46s %-14s" % ("criterion", "real/target") + "".join("%-*s" % (W, s["name"]) for s in arms))
    for cid in ORDER:
        cells = [scored[s["name"]][cid] for s in arms]
        say("    %-46s %-14s" % (LABEL[cid], cells[0]["real_text"]) + "".join("%-*s" % (W, fmt_cell(c)) for c in cells))
    say()
    say("Result")
    verdicts = {}
    for s in arms:
        sc = scored[s["name"]]
        fails = [c for c in ORDER if sc[c]["pass"] is False]
        unknown = [c for c in ORDER if sc[c]["pass"] is None]
        near = sorted(((sc[c]["usage"], c) for c in ORDER
                       if sc[c]["pass"] and sc[c].get("usage") is not None and sc[c]["usage"] >= 0.8), reverse=True)
        near_txt = ", ".join(f"{c} ({sc[c].get('edge_note')}, {u:.0%} of tolerance)" for u, c in near)
        n = len(ORDER)   # seventeen: A1-A3, B1-B8, C1-C3, C4a, C4b, D1
        if unknown:
            v = f"INCOMPLETE: {len(unknown)} not scored ({', '.join(unknown)}); fails {', '.join(fails) or 'none'}"
        elif fails:
            v = f"fails {len(fails)} of {n}: {', '.join(fails)}"
        else:
            v = f"passes {n} of {n}" + (f"; nearest the edge: {near_txt}" if near else "")
        verdicts[s["name"]] = {"fails": fails, "unknown": unknown, "passes_all": not fails and not unknown,
                               "nearest_edge": [c for _, c in near], "text": v}
        say(f"    {s['name']:14s} {v}")
    say()
    if not V1_REGISTERED:
        say("V1  long-horizon variance ratio: not registered (the audit's major 5, pending); not graded"
            + (f" ({a.v1} read)" if a.v1 else ""))
        say()
    say("Reported, not gated (CRITERIA.md): the certification's VIX rows, the mechanism certificate, the lever")
    for s in arms:
        g = cg.get(s["cert"])
        if not g:
            continue
        vr = "; ".join(f"{k} k {v['k']}/{v['n']} {v['verdict']}" + (f"/{v['side']}" if v.get("side") else "")
                       for k, v in g.get("vix_row", {}).items())
        mech = "; ".join(f"{k} {len(v['shown'])}/10" + (f" (not shown {','.join(v['not_shown'])})" if v["not_shown"] else "")
                         for k, v in g.get("certify", {}).items())
        lev = g.get("lever") or {}
        say(f"    {s['name']:14s} VIX row: {vr}")
        say(f"    {'':14s} mechanisms: {mech}; lever {lev.get('ratio', float('nan')):.2f}x (tape 6.16x)")
    say()
    say("D1's crisis_sector_dispersion, at least 10 readings per cell (own seeds, then blocks of 30 from 1001 to 1270 at most)")
    for s in arms:
        g = cg.get(s["cert"])
        if not g:
            continue
        cd = g.get("crisis_dispersion")
        if cd is None:
            say(f"    {s['name']:14s} reading counts not recorded: this certgrade predates the rule (not extended)")
            continue
        for cell in CERT_CELLS:
            r = cd.get(cell)
            if r is None:
                say(f"    {s['name']:14s} {cell:17s} -")
                continue
            ext = (f"{r['blocks_used']} block(s) {r['extension_seeds'][0]}-{r['extension_seeds'][1]}"
                   if r["blocks_used"] else "not extended")
            med = "-" if r["median"] is None else f"{r['median']:.4f}"
            say(f"    {s['name']:14s} {cell:17s} own {r['own_readings']:2d} of {r['own_seeds']}, {ext}, "
                f"total {r['total']}, median {med}, band {r['band']}: {r['status']}"
                + (f" ({r['note']})" if r.get("note") and r["status"] != "pass" else ""))
    if a.definitions == "reg18":
        say()
        say("The twelfth registration's readings of R4 and D1, reported (they do not grade under reg18)")
        for s in arms:
            r12 = scored[s["name"]].get("_reg12") or {}
            r4 = r12.get("R4") or {}
            d1 = r12.get("D1") or {}
            say(f"    {s['name']:14s} R4 at the last print {r4.get('model_text', '-')} "
                f"({'pass' if r4.get('pass') else 'FAIL' if r4.get('pass') is False else 'n/a'} under reg12); "
                f"D1 level rows on the 30 cert seeds: " + (", ".join(
                    f"{k} {v.get('value') if v.get('value') is None else round(v['value'], 4)} "
                    f"{'in' if v.get('in_band') else 'OUT'}" for k, v in (d1.get("cert30") or {}).items()) or "-")
                + f" (D1 {'pass' if d1.get('pass') else 'FAIL' if d1.get('pass') is False else 'n/a'} under reg12)")
    if a.out:
        pathlib.Path(a.out).write_text("\n".join(L) + "\n")
    if a.json:
        json.dump({"kind": "longrun.criteria", "inputs": {"longrun": a.longrun, "certgrade": a.certgrade,
                                                          "edge": {s["name"]: esrc.get(s["edge"]) for s in arms}},
                   "honest_return": HONEST_RETURN, "arms": scored, "verdicts": verdicts},
                  open(a.json, "w"), indent=1, default=str)

    if a.verdict:
        s = next((x for x in arms if x["name"] == a.verdict_arm), None)
        if s is None:
            sys.exit(f"--verdict-arm {a.verdict_arm!r} is not one of the --arm names")
        v = verdict_block(s["name"], scored, rep["arms"][s["name"]], edges.get(s["edge"]), esrc.get(s["edge"]), a)
        json.dump(v, open(a.verdict, "w"), indent=1, default=str)
        print(f"wrote {a.verdict}: {s['name']} {v['verdict']} {v['passed']} of {v['of']}", file=sys.stderr)

    bad = 0
    for e in a.expect:
        name, _, want = e.partition("=")
        want = sorted(x for x in want.split(",") if x)
        got = sorted(verdicts[name]["fails"] + verdicts[name]["unknown"])
        ok = got == want
        bad += not ok
        print(f"EXPECT {name}: fails {want or 'none'} -> got {got or 'none'}  {'OK' if ok else 'MISMATCH'}",
              file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
