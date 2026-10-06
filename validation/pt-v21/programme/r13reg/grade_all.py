"""grade_all.py: every row of the thirteenth registration (draft), per arm, on held-out seeds.

Copied from the r15 screen (scratchpad r15screen/grade_all.py, sha256 in MANIFEST-sources.txt) and changed to the
owner's decisions of 2026-09-28 (programme/OWNER-DECISIONS-2026-09-28.md):
  (1) SF3 is the median over seeds of each seed's worst paired session; the minimum is printed as SF3min.
  (2) PH5's volatility clause: for each year y in 1..7, |mean vol y - mean vol 0| <= 2 sqrt(se_0^2 + se_y^2),
      se_y the standard error across seeds of year y's mean annualised volatility (was a fixed 0.02).
  (3) F-stress P(cut within 42 | VIX 40+) counts only sessions with a policy rate above 0.25 per cent.
  (4) R4 is graded once, on the held close (bt:R4 held, band [0.15, 0.39]); criteria.py's last-print R4 is
      reported beside it and no longer graded.
  (5) is in exploits_summary.py (levered rules against the exposure-matched position; rule_cut on the long run).
The statistics are pure functions below (sf3, ph5, p_cut_given, restate_r4) so tests/ can check them on fixtures.
Owner decisions of 2026-09-29: (13) R7a and R7a-pre read r7-event.json and r7pre.json, which the job now runs on the
three held-out blocks (R7_SEEDS, R7PRE_SEEDS; 90 seeds); (14) C10c and (15) PH5 are graded on 270 pooled long-run
histories: longrun/ARM's 90 and longrun-pool/ARM's 180 (LONGRUN_POOL_SEED_LIST, protocol longrunpool), read by
load_pool, which returns nothing unless the pool is exactly three 90-history blocks of 270 distinct seeds. Without
the pool both rows are not read (a fail); their 90-history readings are reported as PH5_90 and C10c_breach_90.
The owner's decision after grade 17 (2026-10-05, the eighteenth registration): every row read on the long run's 90
that can be computed from all 270 is read on the 270 (lr270.py): criteria.py's long-run rows (A1-A3, B1-B8, C1, C2,
V1 and B9's annual spread) from BOX/lr270's report, V1 and xsec files, and CV1/CV3, V2/V3, H1-H5, VC4a-f, PH4/PH4b,
C10d and the `lever:` rows here on load_pool's 270. Bands and rules are unchanged.


  python grade_all.py BOXDIR [BOXDIR ...] [--arms A,B,...] [--out FILE] [--json FILE] [--skip-criteria]

Each BOXDIR is a collected r14 screen box (r14-grade-jobs.sh). For every arm found:

 1. EXISTING ROWS. The registered grade as the r13 screen ran it (certgrade_box_hr.py with the D1 rule,
    v1.py for V1, criteria.py on every box output), with C9 read from the arm's own impact curve
    (c9/ARM.json: impact_curve.py with the arm's dials). Every row criteria.py prints, value, band,
    real reference, pass.
 2. NEW ROWS proposed by the designs and fixers (journals wf_fb5dba9b-8d4, wf_7196311c-9b6), each with
    statistic, band, real reference and source, computed from the box's recordings:
      r13gen/ARM/SEED-free.npz + names-SEED.npz   r14gen.py, 90 held-out histories x 5292 sessions
      longrun/ARM/SEED-free.npz                   the registered long run (the index B7 reads, the VIX
                                                  state, the published macro fields)
      mc-report-ARM.json, trips/, mark/           metaorder_curve.py (Q rows, round trips, mark)
      ao/, ph/                                    arrival order, pre-history
      recession-I.json, r7pre.json, sf*.json, probe.json, stab100/   leak rows
 3. The r13 screen's leak evaluation (screen_eval.py) verbatim, for the record.
Every model reading is on held-out seeds (201-230, 501-530, 801-830, 2001+), unless the box ran the registered exam
seeds (r13reg/grade-seeds.json) and REGISTERED_GRADE is set to the registration commit: box/seedplan.py's rule, which
refuses the old exam seeds (101-190, 401-430, 701-730) always."""
import sys, os, glob, json, math, re, subprocess, argparse
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DESIGN = os.environ.get("TF_PROGRAMME", os.path.dirname(HERE))          # the design repo's programme/
PY = os.environ.get("TF_PY", "/Users/simoncoombes/Dev/tf-wt-r15/.venv/bin/python")   # the engine's python
ENG = os.environ.get("TF_ENGINE", "/Users/simoncoombes/Dev/tf-wt-r15")              # the engine checkout
BURN = 252

sys.path.insert(0, f"{HERE}/box")
import seedplan  # noqa: E402  the registered-grade rule (box/seedplan.py)
sys.path.insert(0, f"{HERE}/deps")
from common_eg import measure as eg_measure  # noqa: E402

# ------------------------------------------------------------------ helpers


def seed_of(f):
    b = os.path.basename(f)
    return int(re.findall(r"(\d+)", b)[0])


def check_seeds(seeds, protocol, exact=True):
    """The registered-grade rule (seedplan.py): a recording is graded when its seeds are held out, or exactly the
    registered exam set for the protocol with REGISTERED_GRADE set to the registration commit. The old exam seeds
    (101-190, 401-430, 701-730) are refused always."""
    return seedplan.guard(sorted(set(seeds)), protocol, exact=exact)


def check_box_plan(box):
    """A box that recorded its seed plan (r15-grade-jobs.sh writes seedplan.json): every protocol it ran passes the
    rule, and under REGISTERED_GRADE the box ran this grade-seeds.json and the same commit."""
    f = f"{box}/seedplan.json"
    if not os.path.exists(f):
        return None
    rec = json.load(open(f))
    why = seedplan.verdict({p: seedplan.parse(v) for p, v in rec["plan"].items()})
    sha = os.environ.get("REGISTERED_GRADE", "").strip()
    if sha or rec.get("registered_grade"):
        path = seedplan.grade_seeds_path()
        if (rec.get("registered_grade") or "") != sha:
            why.append(f"the box ran REGISTERED_GRADE={rec.get('registered_grade')}, the desk has {sha or '(unset)'}")
        if not path or rec.get("grade_seeds_sha256") != seedplan.sha256(path):
            why.append("the box's grade-seeds.json is not the desk's")
    if why:
        raise seedplan.Refused("REFUSED: box seed plan: " + "; ".join(why))
    return rec


def load_free(box, arm, sub):
    out = []
    fs = sorted(glob.glob(f"{box}/{sub}/{arm}/*-free.npz"), key=seed_of)
    check_seeds([seed_of(f) for f in fs], "longrun" if sub == "longrun" else "truephase")
    for f in fs:
        z = np.load(f, allow_pickle=True)
        h = {k: z[k] for k in z.files}
        h["seed"] = seed_of(f)
        out.append(h)
    return out


POOL_N = 270          # owner decisions 14 and 15: C10c and PH5 on three 90-history blocks


def load_pool(box, arm, need=POOL_N):
    """The 270 long-run histories C10c and PH5 are graded on (owner decisions 14 and 15): longrun/ARM (the long
    run's 90, protocol longrun) and longrun-pool/ARM (two more 90-history blocks, protocol longrunpool), each set
    passing the registered-grade rule. Returns (histories, note); the histories are [] unless there are exactly
    `need` distinct seeds in blocks of 90 (the long run's own and the pool's two), so a partial pool is not graded."""
    base = load_free(box, arm, "longrun")
    fs = sorted(glob.glob(f"{box}/longrun-pool/{arm}/*-free.npz"), key=seed_of)
    pseeds = [seed_of(f) for f in fs]
    check_seeds(pseeds, "longrunpool")
    seeds = [h["seed"] for h in base] + pseeds
    if len(base) != 90 or len(pseeds) != need - 90 or len(set(seeds)) != need:
        return [], (f"not graded: {len(base)} long-run and {len(pseeds)} pooled histories "
                    f"({len(set(seeds))} distinct seeds; the row needs {need} in three blocks of 90)")
    out = list(base)
    for f in fs:
        z = np.load(f, allow_pickle=True)
        h = {k: z[k] for k in z.files}
        h["seed"] = seed_of(f)
        out.append(h)
    return out, f"{need} histories: longrun {seedplan.fmt([h['seed'] for h in base])} + pool {seedplan.fmt(pseeds)}"


def names_files(box, arm):
    fs = sorted(glob.glob(f"{box}/r13gen/{arm}/names-*.npz"), key=seed_of)
    check_seeds([seed_of(f) for f in fs], "truephase")
    return fs


def within(v, lo, hi):
    return v is not None and np.isfinite(v) and lo <= v <= hi


def usage(v, lo, hi):
    if v is None or not np.isfinite(v):
        return float("nan")
    if lo == -math.inf:
        return v / hi if hi else float("nan")
    if hi == math.inf:
        return lo / v if v else float("nan")
    if hi == lo:                                   # a point band (AO5: 0 checks fail)
        return 0.0 if v == lo else math.inf
    mid, half = (lo + hi) / 2, (hi - lo) / 2
    return abs(v - mid) / half


# ------------------------------------------------------------------ the new rows' definitions
AO_COIN_KEYS = 1_000_000          # owner decision 10: AO1 over at least 10^6 (seed, step) keys
AO_POOL_N = 400                   # AO3 and AO4 over at least 400 held-out-style seeds
# id: (statistic, band lo, band hi, real reference, source, gated)
ROWS = {
    # dividends
    "DV1": ("premium over cash: index total return less the policy rate, log %/yr, years 2-21, median over histories", 4.6, 8.6, "6.59", "Ken French Mkt-RF 1926-2025 (log); Damodaran 1928-2025 6.23", True),
    "DV2": ("premium over the 10-year constant-maturity bond, log %/yr", -math.inf, math.inf, "5.11", "Damodaran 1928-2025 (reported only)", False),
    "DV3": ("index dividend yield, cap-weighted declared annual rate over price, %", 1.3, 2.5, "1.81", "Damodaran 2001-2025; Shiller 1990-2023 2.02", True),
    "DV4": ("delivered buyback yield, cap-weighted log share-count reduction a year, %", 1.8, 3.4, "2.58", "Damodaran B/P 2001-2025 (gross)", True),
    "DV5": ("total payout (D+B)/E on the valuation's per-share earnings", 0.56, 1.04, "0.80", "Damodaran 2001-2025", True),
    "DV6": ("D/P-quintile total-return spread, top minus bottom, pts/yr, mean over histories", -2.1, 3.9, "+0.89 (se 1.48)", "Ken French D/P portfolios, value-weighted, 1927-2025", True),
    "DV7sd": ("sd of annual aggregate dividend growth, %", 3.6, 14.2, "7.10", "Shiller 1990-2023 (1946-2023 6.25)", True),
    "DV7cut": ("share of years with an aggregate dividend cut, pooled", 0.045, 0.18, "0.088 (3 of 34)", "Shiller 1990-2023", True),
    "DV8": ("ex-date drop per unit of dividend (prior close less open over D), median over events", 0.80, 1.05, "0.88 (se 0.04)", "40-name tape 2015-2025, 1121 events", True),
    "DV9": ("rank IC of D/P against the next 20 sessions' total return, every 5 sessions", -math.inf, math.inf, "+0.005 (se 0.008)", "Ken French D/P portfolios read as an IC (reported only)", False),
    # crash-vol-state
    "CV1": ("leverage sum: sum_k=1..20 corr(r_t, |r_t+k|), index daily log returns, years 2-21, median over histories", -1.75, -0.80, "-1.35", "S&P 500 1990-2025 (tape); CRSP 1926-2025 -1.25", True),
    "CV3": ("skew of non-overlapping 21-session index log returns, median over histories", -1.30, -0.35, "-0.80", "S&P 500 1990-2025; CRSP 20-year windows median -0.85", True),
    "CV2": ("VIX memory of a fall: k20/k0 slope ratio (reported)", -math.inf, math.inf, "0.57", "S&P 500 / VIX 1990-2025", False),
    # vix-peaks
    "V2": ("median published VIX / RV21 over sessions with RV21 >= 40, pooled, years 2-21", 0.74, 0.92, "0.831 (se 0.045)", "S&P 500 and ^VIX tape 1990-01-02 to 2025-07-31", True),
    "V3": ("share of sessions with the published VIX above 50, %", 0.42, 1.67, "0.84 (1/2x to 2x)", "same tape (2008-09, 2020, 2025)", True),
    # bear-dynamics
    "B10": ("index sd on TRUE contraction/trough sessions over sd on the rest, pooled, years 2-21", 1.45, 2.30, "1.87", "S&P 500 daily 1928-2025 by NBER USREC month (1.66 for 1950-2025)", True),
    "B11": ("median VIX (state) on TRUE contraction/trough sessions over the median on the rest", 1.25, 2.00, "1.62 (27.5 vs 17.0)", "FRED VIXCLS 1990-2025 by NBER USREC", True),
    "B12": ("share of B3's 20% bears with a TRUE contraction/trough between peak and trough+63", 0.45, 0.85, "0.64 (7 of 11)", "S&P 500 bears 1950-2025 against NBER recessions", True),
    # bond-hedge-fed
    "H1": ("mean policy-rate change over 63 sessions after a close with VIX >= 30, rate >= 0.5, CPI < 4 (published)", -0.69, -0.13, "-0.411 (se 0.139)", "FRED DFF + VIX tape 1990-2025", True),
    "H2": ("share of policy-rate changes at VIX >= 30 (previous close) and CPI < 4 that are hikes", -math.inf, 0.20, "0 of 9", "FRED DFEDTAR/DFEDTARU 1990-2025", True),
    "H3": ("mean 10-year change (pp) over 63-session windows with the index down >10%, CPI<4 at start", -0.84, -0.40, "-0.624 (se 0.108)", "FRED DGS10 + S&P 500 1990-2025", True),
    "H4": ("same windows: mean 10-year constant-maturity bond return over mean index return", -0.94, -0.24, "-0.473 (se 0.064)", "FRED DGS10 + S&P 500 1990-2025", True),
    "H5": ("monthly corr(index log return, minus the 10-year's change), blocks starting with CPI < 3", -0.35, -0.02, "-0.188 (se 0.082)", "FRED DGS10/CPI + S&P 500 1990-2025", True),
    "Hf": ("policy-rate changes a year (reported guard)", -math.inf, math.inf, "3.0", "FOMC target changes 1990-2025", False),
    "Hz": ("share of sessions with the policy rate <= 0.25 (reported)", -math.inf, math.inf, "0.255", "FRED DFF 1990-2025", False),
    # vol-clustering
    "VC1": ("idiosyncratic |e| lag-1 ACF (residual on leave-one-out EW roster), 252-return windows, median names/windows/histories", 0.065, 0.110, "0.088 (se 0.010)", "real forty 2015-2025", True),
    "VC2": ("aftershock: mean |e| after a top-2.5% |e| session over mean |e|", 1.16, 1.42, "1.29 (se 0.062)", "real forty 2015-2025", True),
    "VC3": ("certification abs_return_acf1, panel_252 cell median (graded in D1)", 0.039, 0.17, "0.1025 median of REAL_MARKETS_WINDOWS", "facts.REAL_MARKETS_WINDOWS 2015-07 to 2025-07 (lowest 0.039)", True),
    "VC4a": ("cap-weighted index |r| ACF lag 1", 0.158, 0.305, "p10-p90 of 20-year windows", "CRSP 1926-2026", True),
    "VC4b": ("index |r| ACF lag 5", 0.152, 0.346, "", "CRSP 1926-2026", True),
    "VC4c": ("index |r| ACF lag 20", 0.077, 0.245, "", "CRSP 1926-2026", True),
    "VC4d": ("index |r| ACF lag 100", 0.025, 0.126, "", "CRSP 1926-2026", True),
    "VC4e": ("power-law slope of the |r| ACF, lags 1-100", -0.687, -0.130, "", "CRSP 1926-2026", True),
    "VC4f": ("ACF1 of log monthly realised vol", 0.489, 0.740, "", "CRSP 1926-2026", True),
    # earnings-gaps
    "G1": ("overnight share of name variance, median name", 0.30, 0.48, "0.391 (0.326-0.441)", "the forty 2015-2025 with EDGAR 8-K 2.02 dates", True),
    "G1c": ("overnight share of the EQUAL-weighted 40-name index variance", 0.30, 0.60, "0.461 (0.332-0.558)", "same", True),
    "G2": ("earnings reaction-day idiosyncratic variance ratio", 6.5, 14.0, "10.2 (7.3-12.6)", "same", True),
    "G2b": ("reaction days' share of idiosyncratic variance", 0.09, 0.20, "0.144 (0.108-0.173)", "same", True),
    "G3": ("night's share of reaction-day variance", 0.65, 0.85, "0.759 (0.731-0.779)", "same", True),
    "G3b": ("day-after idiosyncratic variance ratio", 1.2, 2.4, "1.71 (1.34-2.14)", "same", True),
    "G4": ("idiosyncratic excess kurtosis, median name", 4.7, 14.1, "9.4 (6.1-9.7)", "same", True),
    "G7": ("median |gap|, bp", 26, 50, "37.5 (33.1-43.7)", "same", True),
    "G7b": ("nights beyond 8x the name's median |gap|, %", 0.8, 4.0, "1.96", "same", True),
    "G8": ("nights opening through a stop 2 median daily |r| below the close, %", 2.0, 5.0, "3.28", "same", True),
    "G9": ("reaction-day volume ratio", 1.6, 3.0, "2.16 (2.06-2.25)", "same", True),
    "G10": ("gap continuation: slope of the EW session return (open to close print) on the EW night return", -0.04, 0.09, "+0.022 (se ~0.03)", "the forty 2015-2025", True),
    "C10e-cal": ("calendar rule, out through each report: median annualised excess pts/yr (<= +1.0) and share ahead (<= 2/3)", -math.inf, 1.0, "-1.48, ahead 11 of 39", "the forty 2015-2025 with EDGAR report dates", True),
    # prehistory
    "PH1": ("state hash after prehistory(N)+T equals an untraded N+T run; forks identical; baseline equal (15 cells)", 1, 1, "construction", "harness identity", True),
    "PH3": ("with history_days=252, SMA200 and TSMOM252 active on every scored day (fraction)", 1.0, 1.0, "construction (cold: 0.21 and 0)", "harness identity", True),
    "PH4": ("within-seed corr of log RV20 before/after seams every 40 sessions", 0.575, 0.743, "0.659 (se 0.042)", "S&P 500 1990-2025 (Yahoo ^GSPC)", True),
    "PH4b": ("same, RV60 (companion)", 0.586, 0.838, "0.712 (se 0.063)", "S&P 500 1990-2025", True),
    "PH5": ("paired index log return, year 1 minus year 0: |mean| < 2 se; year-0 vol within 2 sqrt(se_0^2 + se_y^2) of each year y = 1..7 (se of the yearly means across seeds; owner 2026-09-28), over 270 pooled long-run histories (owner decision 15)", 1, 1, "construction", "stationarity", True),
    # sqrt-impact: metaorder_curve.py, 30 held-out seeds (2501-2530) x 12 names, each order forked against a no-trade twin
    "Q1": ("exponent of the print peak in f, half-day schedule, 1-30% ADV", 0.4, 0.7, "0.47-0.51", "Zarinelli et al. 2015; Toth et al. 2011; Almgren et al. 2005 (0.6)", True),
    "Q2": ("print peak at 10% ADV, half-day, over sqrt(0.1), in daily sigma", 0.3, 1.0, "0.46 (Bucci 0.4 range units x 1.14)", "Bucci et al. PRL 122 108302 (2019); Toth et al. 2011", True),
    "Q3": ("share of the final displacement reached halfway through a day TWAP at 10%", 0.62, 0.82, "0.71 (square root)", "square-root / propagator shape", True),
    "Q4": ("close over peak of the displacement, half-day orders, f >= 3%", 0.55, 0.80, "0.66 +- 0.04", "Bucci et al. arXiv 1901.05332 Fig. 1", True),
    "Q5": ("next close over peak, same orders", 0.40, 0.70, "~0.55 (0.44 with zeta)", "Bucci et al. arXiv 1901.05332 Fig. 2", True),
    "Q6": ("IS of a day TWAP at 10% ADV, in daily sigma", 0.10, 0.21, "0.105-0.21", "Toth Y 0.5-1 x 2/3; Almgren et al. with turnover factor 0.152-0.165", True),
    "Q7": ("IS of a day TWAP over IS of a one-hour TWAP at 10%", 0.5, 0.9, "~0.63", "Bacry et al. 2015 (T^-0.25); Almgren temporary T^-0.6", True),
    "Q8": ("IS of a day TWAP over IS of a block at 10%", 0.5, 0.8, "0.55-0.64", "Almgren et al. 2005 over a sixth of a day; Bacry et al. 2015", True),
    "Q9": ("IS of a day TWAP over IS of a block at 3%", 0.5, 0.8, "0.55-0.64", "same", True),
    # sqrt-impact (Q rows from metaorder_curve.py's own bands)
    "G-rt": ("the most profitable round trip (pump, two-name, block/slice, next-day, alternating, wash) against the twin, bp", -math.inf, 0.0, "every round trip loses", "construction", True),
    "MARK": ("mark-the-close gain over cost, 10% ADV holder buying 0.01% two ticks before the close (reported)", -math.inf, math.inf, "illegal in practice (marking the close)", "", False),
    # arrival order
    # owner decision 10 (2026-09-28): AO1, AO3 and AO4 test fairness; the 30-seed counts are reported (AO1n30, AO3n30,
    # AO4n30) and exempt from the one-standard-error screening margin
    "AO1": (f"the book coin: P(a first) over >= {AO_COIN_KEYS:,} (seed, day, step) keys (ao_coin.py, 400 seeds x 420 days x 6 steps)", 0.498, 0.502, "0.5 (no identity priority)", "Nasdaq Rule 4757; NYSE Pillar 7.36-7.37; owner decision 10", True),
    "AO2": ("second arrival's VWAP premium over the first, mean bp (and positive on >= 28/30)", 10, 40, "~24 bp", "square-root law (Toth et al. 2011)", True),
    "AO3": (f"label effect with arrival luck removed: intercept / se of a's lead (bp) regressed on its a-first share - 0.5, two identical momentum agents, >= {AO_POOL_N} seeds", -3.0, 3.0, "0", "identical agents of equal speed; owner decision 10", True),
    "AO4": (f"label effect with arrival luck removed: intercept / se of Spearman(label rank, cost) regressed on Spearman(label rank, mean arrival position), four agents, >= {AO_POOL_N} seeds", -3.0, 3.0, "0", "random activation (Axtell 2001); owner decision 10", True),
    "AO1n30": ("seeds (of 30) where the later label pays the higher VWAP, two identical 10%-ADV buys (reported)", -math.inf, math.inf, "15", "the thirteenth registration's AO1", False),
    "AO3n30": ("seeds (of 30) where label a ends ahead (reported)", -math.inf, math.inf, "15", "the thirteenth registration's AO3", False),
    "AO4n30": ("mean Spearman(label rank, execution cost) over 30 seeds (reported)", -math.inf, math.inf, "0", "the thirteenth registration's AO4", False),
    "AO5": ("checks failed of 5: the 40 registered rows unchanged by the arrival shuffle switch (ao5.py: engine, readers, scripts, paired, live)", 0, 0, "0", "construction", True),
    # guards on the published quote (B rows read the VIX state in the registered long run)
    "B1q": ("time with the PUBLISHED VIX above 30, % (B1 reads the state)", 4.1, 16.4, "8.2", "tape 1990-2025", False),
    "B6q": ("time with the PUBLISHED VIX under 15, %", 16.3, 65.2, "32.6", "tape 1990-2025", False),
}

# ------------------------------------------------------------------ per-topic estimators


def dv_one(f):
    z = np.load(f, allow_pickle=True); h = {k: z[k] for k in z.files}
    P = h["post"].astype(float); O = h["open"].astype(float); Dp = h["paid"].astype(float); A = h["amount"].astype(float)
    days, n = P.shape
    sh = h["shares"]; eps0 = h["eps0"]; v = h["v"].astype(float); po = h["payout"]
    N = h["e_gdp"] * h["e_cpi"] / float(h["base"])
    cyc = h["e_earnings_cycle"]
    lnF = h["lbs"].astype(float)          # the engine's accrued log share-count reduction (buyback_accrual)
    E = eps0[None, :] * (N * np.exp(cyc))[:, None] * np.exp(v) * np.exp(lnF)
    cap = P * sh; idx = cap.sum(1); w = cap / idx[:, None]
    pr = np.diff(np.log(idx))[BURN:]
    trd = np.log(((P[1:] + Dp[1:]) * sh).sum(1) / idx[:-1])[BURN:]
    yrs = len(pr) / 252
    ff = h["m_federal_funds_rate"][BURN:]; y10 = h["m_treasury_yield_10y"][BURN:]
    # published macro fields are fractions (0.05 is 5%), as dv_rows.py reads them
    dy = np.diff(y10); br = y10[:-1] / 252 - 8.5 * dy + 0.5 * 84.0 * dy ** 2
    cash = np.log1p(ff[:-1] / 252).sum() / yrs; bond = np.log1p(br).sum() / yrs
    tr = trd.sum() / yrs
    dp_name = 4 * A / P
    dv3 = float(((w * dp_name).sum(1))[BURN:].mean())
    dlnF = np.diff(lnF, axis=0) * 252
    dv4 = float((w[1:] * dlnF).sum(1)[BURN:].mean())
    Epos = np.maximum(E, 0)
    Bps = np.vstack([dlnF[:1], dlnF]) * P
    pay = ((4 * A + Bps) * sh).sum(1) / (Epos * sh).sum(1)
    dv5 = float(np.nanmean(pay[BURN:]))
    ok = (P[BURN] > 0.05) & (P[-1] > 0.05)
    trn = (np.log(P[-1] / P[BURN]) + np.log1p(Dp[BURN + 1:] / P[BURN:-1]).sum(0)) / yrs
    dpm = dp_name[BURN:].mean(0)
    idx_ok = np.where(ok)[0]
    order = idx_ok[np.argsort(dpm[idx_ok], kind="stable")]
    q = max(1, len(order) // 5)
    dv6 = float(trn[order[-q:]].mean() - trn[order[:q]].mean())
    agg = (Dp * sh).sum(1)
    ann = np.array([agg[252 * y:252 * (y + 1)].sum() for y in range(1, days // 252)])
    g = np.diff(np.log(np.maximum(ann, 1e-12)))
    ev = []
    for d in range(1, days):
        k = Dp[d] > 0.004 * P[d - 1]
        if k.any():
            ev.extend(((P[d - 1, k] - O[d, k]) / Dp[d, k]).tolist())
    ics = []
    for d in range(0, days - 21, 5):
        fwd = np.log((P[d + 20] + Dp[d + 1:d + 21].sum(0)) / P[d])
        a, b = dp_name[d], fwd
        m = np.isfinite(a) & np.isfinite(b)
        if m.sum() >= 5 and a[m].std() > 0:
            ra = np.argsort(np.argsort(a[m])); rb = np.argsort(np.argsort(b[m]))
            ics.append(np.corrcoef(ra, rb)[0, 1])
    return dict(eq=pr.sum() / yrs * 100, tr=tr * 100, DV1=(tr - cash) * 100, DV2=(tr - bond) * 100, DV3=dv3 * 100,
                DV4=dv4 * 100, DV5=dv5, DV6=dv6 * 100, DV7sd=float(np.std(g, ddof=1) * 100) if len(g) > 2 else np.nan,
                DV7cut=float((g < 0).mean()) if len(g) else np.nan, DV8=float(np.median(ev)) if ev else np.nan,
                DV9=float(np.nanmean(ics)) if ics else np.nan, ev=len(ev))


def dv_rows(box, arm):
    fs = names_files(box, arm)
    if not fs:
        return {}
    R = [dv_one(f) for f in fs]
    col = lambda k: np.array([r[k] for r in R], float)
    if np.nansum(col("ev")) == 0:
        return {"DV3": 0.0, "DV1": float(np.nanmedian(col("DV1"))), "DV2": float(np.nanmedian(col("DV2"))),
                "DV4": float(np.nanmedian(col("DV4"))), "note": "no dividends on this arm"}
    return dict(DV1=float(np.nanmedian(col("DV1"))), DV2=float(np.nanmedian(col("DV2"))), DV3=float(np.nanmedian(col("DV3"))),
                DV4=float(np.nanmedian(col("DV4"))), DV5=float(np.nanmedian(col("DV5"))), DV6=float(np.nanmean(col("DV6"))),
                DV6_se=float(np.nanstd(col("DV6"), ddof=1) / np.sqrt(len(R))),
                DV7sd=float(np.nanmedian(col("DV7sd"))), DV7cut=float(np.nanmean(col("DV7cut"))), DV8=float(np.nanmedian(col("DV8"))),
                DV9=float(np.nanmean(col("DV9"))), price_return=float(np.nanmedian(col("eq"))), total_return=float(np.nanmedian(col("tr"))))


def lev_sum(r, K=20):
    a = np.abs(r)
    return float(sum(np.corrcoef(r[:-k], a[k:])[0, 1] for k in range(1, K + 1)))


def skew(x):
    x = np.asarray(x, float); m = x.mean(); s = x.std()
    return float(((x - m) ** 3).mean() / s ** 3)


def agg(r, h):
    n = len(r) // h
    return r[:n * h].reshape(n, h).sum(1)


def vix_irf_ratio(r, v, k=20):
    # slope of VIX_{t+k} - VIX_{t-1} on r_t over the same-day slope (crash-vol-state grade.py's CV2 form)
    out = []
    for kk in (0, k):
        y = v[1 + kk:] if kk else v[1:]
        n = min(len(r) - 1 - kk, len(y))
        x = r[1:1 + n]; dv = v[1 + kk:1 + kk + n] - v[:n]
        out.append(np.polyfit(x, dv, 1)[0])
    return float(out[1] / out[0])


def cv_rows(LR):
    lev, skm = [], []
    for h in LR:
        r = np.diff(np.log(h["level"][BURN:].astype(float)))
        lev.append(lev_sum(r)); skm.append(skew(agg(r, 21)))
    return dict(CV1=float(np.median(lev)), CV3=float(np.median(skm)),
                CV1_p10=float(np.percentile(lev, 10)), CV3_p90=float(np.percentile(skm, 90)))


def trail_rv(level, w=21):
    r = np.diff(np.log(level)); n = len(level)
    out = np.full(n, np.nan)
    c = np.concatenate([[0.0], np.cumsum(r * r)])
    t = np.arange(w, n)
    out[t] = np.sqrt(252.0 * (c[t] - c[t - w]) / w) * 100.0
    return out


def spells(v, thr=30, merge=10):
    # programme/longrun/estimators.spells: spells above thr merged within `merge` sessions, mean length
    sp = []; t = 0; n = len(v)
    while t < n:
        if v[t] > thr:
            s = t
            e = t
            while True:
                while e + 1 < n and v[e + 1] > thr:
                    e += 1
                nxt = next((k for k in range(e + 1, min(n, e + 1 + merge)) if v[k] > thr), None)
                if nxt is None:
                    break
                e = nxt
            sp.append(e - s + 1); t = e + 1
        else:
            t += 1
    return sp


def vix_rows(LR):
    ratio_v, ratio_r, gt50, gt30, lt15, n, spl = [], [], 0, 0, 0, 0, []
    for h in LR:
        lv = h["level"].astype(float); q = h["m_vix"].astype(float)
        tr = trail_rv(lv)[BURN:]; qq = q[BURN:]
        m = np.isfinite(tr) & (tr >= 40)
        ratio_v.append(qq[m]); ratio_r.append(tr[m])
        gt50 += int((qq > 50).sum()); gt30 += int((qq > 30).sum()); lt15 += int((qq < 15).sum()); n += len(qq)
        spl += spells(list(qq), 30)
    V = np.concatenate(ratio_v); Rr = np.concatenate(ratio_r)
    return dict(V2=float(np.median(V / Rr)) if len(V) >= 10 else np.nan, V2_n=int(len(V)), V3=100 * gt50 / n,
                B1q=100 * gt30 / n, B6q=100 * lt15 / n, B2q=float(np.mean(spl)) if spl else np.nan,
                vix_max=float(max(h["m_vix"].max() for h in LR)))


def drawdowns(level, threshold):
    out, peak_i, i, n = [], 0, 1, len(level)
    while i < n:
        if level[i] >= level[peak_i]:
            peak_i = i; i += 1; continue
        if level[i] <= level[peak_i] * (1 - threshold):
            trough_i = i; j = i
            while j < n and level[j] < level[peak_i]:
                if level[j] < level[trough_i]:
                    trough_i = j
                j += 1
            rec = j if j < n else None
            out.append((peak_i, trough_i, rec, 1 - level[trough_i] / level[peak_i]))
            if rec is None:
                break
            peak_i, i = rec, rec + 1
            continue
        i += 1
    return out


def bear_rows(G):
    Rc, Rn, Vc, Vn, Qc, Qn, nb, inn, yrs, rec = [], [], [], [], [], [], 0, 0, 0.0, []
    for h in G:
        L = h["post"][BURN:].astype(float); V = h["vix"][BURN:]; Q = h["m_vix"][BURN:]; PH = h["true"][BURN:].astype(int)
        R = np.diff(np.log(L)); c = np.isin(PH[1:], (2, 3))
        Rc.append(R[c]); Rn.append(R[~c])
        cv = np.isin(PH, (2, 3)); Vc.append(V[cv]); Vn.append(V[~cv]); Qc.append(Q[cv]); Qn.append(Q[~cv])
        yrs += len(R) / 252
        for a, t, r, dep in drawdowns(list(L), .2):
            nb += 1; inn += bool(np.any(np.isin(PH[a:t + 64], (2, 3))))
            if r is not None:
                rec.append(r - a)
    Rc, Rn = np.concatenate(Rc), np.concatenate(Rn)
    return dict(B10=float(Rc.std() / Rn.std()), B11=float(np.median(np.concatenate(Vc)) / np.median(np.concatenate(Vn))),
                B11q=float(np.median(np.concatenate(Qc)) / np.median(np.concatenate(Qn))),
                B12=inn / max(nb, 1), bears=nb, bears_outside_per_decade=(nb - inn) / yrs * 10,
                recovery_median=float(np.median(rec)) if rec else np.nan)


def bond_logret(y):
    dy = np.diff(y) / 100
    return np.log1p(y[:-1] / 100 / 252 - 8.5 * dy + 0.5 * 84.0 * dy ** 2)


def bond_rows(LR):
    fp_n = fp_s = 0.0; q = []; X = []; up = dn = 0; changes = 0; zlb = 0; nses = 0; ffmean = []
    for h in LR:
        # bhf.sim_histories' reading: the VIX state the long run records (the put reads the state),
        # the published rates and CPI in per cent
        L = h["level"][BURN:].astype(float); v = h["vix"][BURN:]; ff = 100 * h["m_federal_funds_rate"][BURN:]
        y = 100 * h["m_treasury_yield_10y"][BURN:]; inf = 100 * h["m_inflation_rate"][BURN:]
        n = len(L); idx = np.arange(0, n - 63)
        ok = (ff[idx] >= 0.5) & (inf[idx] < 4.0) & (v[idx] >= 30)
        fp_s += (ff[idx + 63] - ff[idx])[ok].sum(); fp_n += ok.sum()
        bl = np.concatenate([[0], np.cumsum(bond_logret(y))])
        for t in range(0, n - 63, 5):
            r = L[t + 63] / L[t] - 1
            if r < -0.10 and inf[t] < 4:
                q.append((y[t + 63] - y[t], np.exp(bl[t + 63] - bl[t]) - 1, r))
        for i in range((n - 1) // 21):
            a, b = 21 * i, 21 * (i + 1)
            X.append((np.log(L[b] / L[a]), -(y[b] - y[a]), inf[a]))
        d = np.diff(ff)
        for t in range(1, n):
            if abs(d[t - 1]) > 1e-9:
                changes += 1
                if v[t - 1] >= 30 and inf[t - 1] < 4:
                    up += d[t - 1] > 0; dn += d[t - 1] < 0
        zlb += int((ff <= 0.25).sum()); nses += n; ffmean.append(ff.mean())
    q = np.array(q).reshape(-1, 3); X = np.array(X)
    m = X[:, 2] < 3
    return dict(H1=fp_s / max(fp_n, 1), H1_n=int(fp_n), H2=up / max(up + dn, 1), H2_n=int(up + dn),
                H3=float(q[:, 0].mean()) if len(q) else np.nan, H4=float(q[:, 1].mean() / q[:, 2].mean()) if len(q) else np.nan,
                H34_n=len(q), H5=float(np.corrcoef(X[m, 0], X[m, 1])[0, 1]) if m.sum() > 10 else np.nan, H5_n=int(m.sum()),
                H5_cpi3plus=float(np.corrcoef(X[~m, 0], X[~m, 1])[0, 1]) if (~m).sum() > 10 else np.nan,
                Hf=changes / (nses / 252), Hz=zlb / nses, ff_mean=float(np.mean(ffmean)))


def acf(x, lags):
    x = np.asarray(x, float); x = x - x.mean(); v = (x * x).sum()
    return np.array([(x[:-k] * x[k:]).sum() / v if k < len(x) else np.nan for k in lags])


def vc_rows(box, arm, days=2660):
    ia, af = [], []
    for f in names_files(box, arm):
        C = np.load(f)["post"][:days].astype(float)
        R = np.diff(np.log(C), axis=0)
        for s in range(0, len(R) - 252 + 1, 252):
            W = R[s:s + 252]; T, N = W.shape; ew = W.mean(1)
            a1, a2 = [], []
            for i in range(N):
                r = W[:, i]; m = (ew * N - r) / (N - 1)
                X = np.c_[np.ones(T), m]; b = np.linalg.lstsq(X, r, rcond=None)[0]; e = r - X @ b
                a1.append(acf(np.abs(e), [1])[0])
                a = np.abs(e); big = a[:-1] >= np.percentile(a[:-1], 97.5); a2.append(a[1:][big].mean() / a.mean())
            ia.append(np.median(a1)); af.append(np.median(a2))
    return dict(VC1=float(np.median(ia)) if ia else np.nan, VC2=float(np.median(af)) if af else np.nan)


def vc4_rows(LR):
    FULL = list(range(1, 101)); S = []
    for h in LR:
        r = np.diff(np.log(h["level"].astype(float)))[251:]
        a = acf(np.abs(r), FULL)
        L = np.array(FULL); mm = (a > 0)
        slope = float(np.polyfit(np.log(L[mm]), np.log(a[mm]), 1)[0])
        nb = len(r) // 21; rv = np.sqrt((r[:nb * 21].reshape(nb, 21) ** 2).sum(1))
        S.append((a[0], a[4], a[19], a[99], slope, acf(np.log(rv), [1])[0]))
    S = np.median(np.array(S), 0)
    return dict(zip(("VC4a", "VC4b", "VC4c", "VC4d", "VC4e", "VC4f"), map(float, S)))


def eg_rows(box, arm, days=2660):
    fs = names_files(box, arm)
    rows = []; X, Y = [], []; oe = []
    for f in fs:
        z = np.load(f)
        C = z["post"][:days].astype(float); O = z["open"][:days].astype(float); V = z["vol"][:days].astype(float)
        E = z["earn"][:days].astype(bool)
        if E.sum() == 0:
            E = None
        out, x = eg_measure(C, O, V, E)
        g = np.log(O[1:] / C[:-1]); r = np.diff(np.log(C), axis=0)
        mg = np.median(np.abs(g), 0); mr = np.median(np.abs(r), 0)
        with np.errstate(invalid="ignore", divide="ignore"):
            out["G7b"] = float(np.median((np.abs(g) > 8 * mg).mean(0)) * 100)
            zz = g / mr; out["G8"] = float((zz < -2).mean() * 100)
        rows.append(out)
        night = np.log(O[1:] / C[:-1]).mean(1); sess = np.log(C[1:] / O[1:]).mean(1)
        X.append(night); Y.append(sess)
        if E is not None:
            day = np.log(C[1:] / C[:-1]); Ev = E[1:]; yrs = (len(C) - 1) / 252
            for i in range(C.shape[1]):
                ev = Ev[:, i]; k = ev.sum()
                oe.append((-day[ev, i].sum() - 2 * 0.0005 * k) / yrs * 100)
    med = lambda k: float(np.nanmedian([r.get(k, np.nan) for r in rows])) if rows else np.nan
    x = np.concatenate(X); y = np.concatenate(Y); xc, yc = x - x.mean(), y - y.mean()
    slope = float(xc @ yc / (xc @ xc)) if (xc @ xc) > 0 else np.nan
    oe = np.array(oe)
    return dict(G1=med("overnight_share_p50"), G1c=med("mkt_overnight_share"), G2=med("idio_var_ratio_E"),
                G2b=med("E_var_share_of_idio"), G3=med("E_night_share_of_day_var"), G3b=med("idio_var_ratio_Ep1"),
                G4=med("idio_kurt_p50"), G7=med("abs_gap_median_bp"), G7b=med("G7b"), G8=med("G8"),
                G9=med("E_volume_ratio_median"), events_per_name_year=med("events_per_name_year"), G10=slope,
                C10e_cal=float(np.median(oe)) if len(oe) else np.nan, C10e_cal_ahead=float((oe > 0).mean()) if len(oe) else np.nan)


def rv_(L, a, b):
    r = np.diff(np.log(L[a:b + 1]))
    return r.std(ddof=1) * np.sqrt(252)


#: PH5's volatility clause: each year's gap to year 0 within PH5_VOL_Z combined standard errors. Registrations 13-16
#: (owner 2026-09-28 (2)) used 2.0 per year. The seventeenth uses the Bonferroni form over the seven years, 2.69 (the
#: two-sided normal quantile at 0.05 / 7; owner's decision 2026-10-05, programme/ptv21-registration-17.md), as
#: does the eighteenth.
PH5_VOL_Z = 2.69


def ph5(d, v0, v17, z=None):
    """PH5 (owner 2026-09-28 (2)). d: per-seed paired index log return, year 1 minus year 0. v0: per-seed annualised
    volatility in year 0. v17: seeds x 7, the same in years 1..7. Return clause: |mean d| < 2 se(d). Volatility clause:
    for every year y, |mean v_y - mean v_0| <= PH5_VOL_Z sqrt(se_0^2 + se_y^2), se_y = sd over seeds of v_y / sqrt(n).
    The fixed-0.02 form it replaces is returned as PH5_vol_fixed002 (reported); the other form of the clause (2.0 or
    2.69, whichever is not registered) as PH5_vol_alt (reported)."""
    z = PH5_VOL_Z if z is None else z
    d = np.asarray(d, float); v0 = np.asarray(v0, float); v17 = np.asarray(v17, float)
    n = len(d); se = d.std(ddof=1) / np.sqrt(n)
    m0 = v0.mean(); my = v17.mean(0)
    se0 = v0.std(ddof=1) / np.sqrt(len(v0)); sey = v17.std(0, ddof=1) / np.sqrt(v17.shape[0])
    gap = np.abs(my - m0); bound = z * np.sqrt(se0 ** 2 + sey ** 2)
    alt_z = 2.69 if z == 2.0 else 2.0
    alt_ok = bool(np.all(gap <= alt_z * np.sqrt(se0 ** 2 + sey ** 2)))
    w = int(np.argmax(gap / bound))
    ret_ok = bool(abs(d.mean()) < 2 * se); vol_ok = bool(np.all(gap <= bound))
    return {"PH5_diff": float(d.mean()), "PH5_se": float(se), "PH5_vol0": float(m0), "PH5_vol0_se": float(se0),
            "PH5_vol_gap": float(gap.max()), "PH5_vol_worst_year": w + 1, "PH5_vol_worst_gap": float(gap[w]),
            "PH5_vol_worst_bound": float(bound[w]), "PH5_vol_use": float((gap / bound).max()),
            "PH5_vol_fixed002": 1.0 if gap.max() <= 0.02 else 0.0,
            "PH5_vol_z": float(z), "PH5_vol_alt": 1.0 if alt_ok else 0.0, "PH5_vol_alt_z": alt_z,
            "PH5": 1.0 if (ret_ok and vol_ok) else 0.0}


def ph5_of(LR):
    """PH5's inputs from index histories (level): d, v0, v17 as ph5() takes them."""
    d = []; v0 = []; v17 = []
    for h in LR:
        L = h["level"].astype(float)
        y0 = np.log(L[251] / L[0]); y1 = np.log(L[503] / L[251]); d.append(y1 - y0)
        v0.append(np.diff(np.log(L[:252])).std(ddof=1) * np.sqrt(252))
        v17.append([np.diff(np.log(L[252 * y - 1:252 * (y + 1)])).std(ddof=1) * np.sqrt(252) for y in range(1, 8)])
    return d, v0, v17


def ph_rows(LR, POOL=None, pool_note="", LR90=None):
    """PH4 and PH4b on LR (the 270 since the eighteenth registration); PH5 on the 270 pooled histories (owner decision
    15), its reading on the long run's 90 (LR90, or LR) reported as PH5_90. POOL None (a caller without a box) grades
    PH5 on LR as before."""
    out = {}
    for k, key in ((20, "PH4"), (60, "PH4b")):
        Bw, Fw = [], []
        for h in LR:
            L = h["level"].astype(float)
            seams = list(range(k + 1, len(L) - k, 2 * k))
            b = np.log([rv_(L, s - k, s - 1) for s in seams]); f = np.log([rv_(L, s, s + k - 1) for s in seams])
            Bw += list(b - b.mean()); Fw += list(f - f.mean())
        out[key] = float(np.corrcoef(Bw, Fw)[0, 1])
    if POOL is None:
        out.update(ph5(*ph5_of(LR)))
        return out
    L90 = LR if LR90 is None else LR90
    if len(L90) > 1:
        p90 = ph5(*ph5_of(L90))
        out["PH5_90"] = p90["PH5"]; out["PH5_90_vol_use"] = p90["PH5_vol_use"]; out["PH5_90_diff"] = p90["PH5_diff"]
    out["PH5_pool_note"] = pool_note
    if POOL:
        out.update(ph5(*ph5_of(POOL)))
        out["PH5_n"] = len(POOL)
    else:
        out["PH5"] = float("nan")
    return out


def ph_harness(box, arm):
    p1 = []; act = []; cold = []
    for f in glob.glob(f"{box}/ph/ph1-{arm}-*.json"):
        try:
            j = json.loads(open(f).read().strip().splitlines()[-1])
            p1 += [r["forks_identical"] and r["equals_off"] and r["baseline_prices_equal"] for r in j["rows"]]
        except Exception:
            p1.append(False)
    for f in glob.glob(f"{box}/ph/ph3-{arm}-*-252.json"):
        try:
            j = json.loads(open(f).read().strip().splitlines()[-1])
            act += [j["sma200"]["active"], j["tsmom252"]["active"]]
        except Exception:
            pass
    for f in glob.glob(f"{box}/ph/ph3-{arm}-*-0.json"):
        try:
            j = json.loads(open(f).read().strip().splitlines()[-1])
            cold += [j["sma200"]["active"], j["tsmom252"]["active"]]
        except Exception:
            pass
    return dict(PH1=(1.0 if p1 and all(p1) else 0.0), PH1_cells=len(p1), PH3=float(min(act)) if act else np.nan,
                PH3_cold=float(np.mean(cold)) if cold else np.nan, PH3_n=len(act))


def q_rows(box, arm):
    out = {}
    f = f"{box}/mc-report-{arm}.json"
    if os.path.exists(f):
        j = json.load(open(f))
        for k, v in j.get("Q", {}).items():
            out[k] = float(v)
        out["mc_n"] = j.get("n_rows")
        sob = j.get("sliced_over_block", {})
        if sob:
            out["slice_over_block_small"] = max(sob[s]["0.01"] for s in sob)
    best = []
    for tf_ in glob.glob(f"{box}/trips/{arm}_*.txt"):
        for line in open(tf_):
            m = re.search(r"best round trip ([+-]?[\d.]+) bp \((\S+),", line)
            if m:
                best.append((float(m.group(1)), m.group(2), os.path.basename(tf_)))
    if best:
        b = max(best)
        out["G-rt"] = b[0]; out["G-rt_which"] = f"{b[1]} {b[2]}"
    for mf in glob.glob(f"{box}/mark/{arm}_*.json"):
        try:
            j = json.load(open(mf)); vals = []
            def walk(x):
                if isinstance(x, dict):
                    for k, v in x.items():
                        if k == "close_gain_over_cost" and isinstance(v, (int, float)):
                            vals.append(v)
                        walk(v)
            walk(j)
            if vals:
                out["MARK"] = max(vals)
        except Exception:
            pass
    return out


def vc3(box, arm):
    f = f"{box}/cert/cells/cell-{arm}.json"
    if not os.path.exists(f):
        return {}
    d = json.load(open(f))
    check_seeds([int(s_["seed"]) for s_ in d["held"]], "cert")
    return {"VC3": float(np.median([s_["abs_return_acf1"] for s_ in d["held"]]))}


def ao_coin(rec, need=AO_COIN_KEYS):
    """AO1 (owner decision 10): the book coin from ao_coin.py's record. The share of (seed, day, step) keys at which a
    arrives first, its binomial se, and the key count; None for the share when the record holds fewer than `need`
    keys or its per-seed counts do not add up."""
    n = int(rec.get("keys") or 0); k = int(rec.get("a_first") or 0)
    per = rec.get("per_seed") or {}
    ok = n >= need and sum(per.values()) == k and len(per) * rec.get("days", 0) * rec.get("steps", 0) == n
    p = k / n if n else float("nan")
    return {"AO1": p if ok else None, "AO1_keys": n, "AO1_se": math.sqrt(0.25 / n) if n else float("nan"),
            "AO1_z": (p - 0.5) / math.sqrt(0.25 / n) if n else float("nan"), "AO1_seeds": len(per)}


def ao_luck(y, x, center=0.0):
    """AO3 and AO4 (owner decision 10): the label effect with arrival luck removed. OLS of y (a's lead, or the
    Spearman of label and cost) on x - center (the seed's a-first share less 0.5, or the Spearman of label and mean
    arrival position): returns the intercept (the label effect on a seed whose arrivals were fair), its standard error,
    their ratio, the slope and n. Seeds with no luck reading are dropped."""
    pairs = [(float(a), float(b)) for a, b in zip(y, x) if a is not None and b is not None
             and np.isfinite(a) and np.isfinite(b)]
    n = len(pairs)
    if n < 3:
        return {"intercept": float("nan"), "se": float("nan"), "t": float("nan"), "slope": float("nan"), "n": n}
    Y = np.array([p[0] for p in pairs]); Xv = np.array([p[1] for p in pairs]) - center
    X = np.c_[np.ones(n), Xv]
    b = np.linalg.lstsq(X, Y, rcond=None)[0]
    e = Y - X @ b
    s2 = float(e @ e) / (n - 2)
    cov = s2 * np.linalg.pinv(X.T @ X)
    se = float(np.sqrt(cov[0, 0]))
    return {"intercept": float(b[0]), "se": se, "t": float(b[0] / se) if se > 0 else float("nan"),
            "slope": float(b[1]), "n": n}


def ao_pool_rows(box, arm, need=AO_POOL_N):
    """AO1, AO3 and AO4 from the pooled block (ao-pool/: coin-ARM.json from ao_coin.py; START_ARM_q2.json and
    START_ARM_q3.json from e7_arm.py in blocks). The blocks' seeds together must be exactly the aopool protocol's set
    (AOPOOL_FIRST, 400 seeds), with no seed twice; otherwise the rows are not read (and fail)."""
    out = {}
    d = f"{box}/ao-pool"
    cf = f"{d}/coin-{arm}.json"
    if os.path.exists(cf):
        rec = json.load(open(cf))
        check_seeds(seedplan.parse(rec["seeds"]), "aopool")
        out.update(ao_coin(rec))
    sp = f"{box}/seedplan.json"             # the set the box ran (r15-grade-jobs.sh), else this desk's AOPOOL_FIRST
    plan = json.load(open(sp)).get("plan", {}) if os.path.exists(sp) else {}
    want = seedplan.parse(plan["aopool"]) if "aopool" in plan else seedplan.plan()["aopool"]
    for row, key, luck, center, rid in (("q2", "ret", "afirst", 0.5, "AO3"), ("q3", "rho", "lpos", 0.0, "AO4")):
        y, x, seeds = [], [], []
        for f in sorted(glob.glob(f"{d}/*_{arm}_{row}.json"), key=seed_of):
            r = json.load(open(f))
            seeds += list(range(r["start"], r["start"] + len(r[key])))
            y += r[key]; x += r.get(luck) or [None] * len(r[key])
        if not seeds:
            continue
        if sorted(seeds) != sorted(set(seeds)) or set(seeds) != set(want):
            out[f"{rid}_note"] = f"pooled seeds {seedplan.fmt(sorted(set(seeds)))} are not exactly the aopool set {seedplan.fmt(want)}"
            continue
        check_seeds(sorted(seeds), "aopool")
        L = ao_luck(y, x, center)
        out[f"{rid}_intercept"], out[f"{rid}_se"], out[f"{rid}_slope"], out[f"{rid}_n"] = L["intercept"], L["se"], L["slope"], L["n"]
        out[f"{rid}_raw_mean"] = float(np.mean(y))
        if L["n"] >= need:
            out[rid] = L["t"]
        else:
            out[f"{rid}_note"] = f"{L['n']} seeds with an arrival-luck reading, fewer than {need}"
    return out


def ao_rows(box, arm):
    out = {}
    def j(row):
        fs = glob.glob(f"{box}/ao/*_{arm}_{row}.json")      # START_ARM_ROW.json, START = AO_FIRST_SEED
        if len(fs) != 1:
            return None
        start = int(os.path.basename(fs[0]).split("_")[0])
        check_seeds(range(start, start + int(os.environ.get("AO_N", "30"))), "ao")
        return json.load(open(fs[0]))
    a = j("q1")
    if a:
        out["AO1n30"] = sum(x > 0 for x in a["gap"]); out["AO2"] = float(np.mean(a["prem"])); out["AO2_pos"] = sum(x > 0 for x in a["prem"])
    b = j("q2")
    if b:
        out["AO3n30"] = sum(x > 0 for x in b["ret"])
    c = j("q3")
    if c:
        out["AO4n30"] = float(np.mean(c["rho"]))
    out.update(ao_pool_rows(box, arm))
    # AO5: a construction check on the box's engine code and the 40 rows' scripts as the box ran them, with a paired
    # run of their entry points, switch on and off (ao5.py). Not computed -> absent -> the row fails.
    r = subprocess.run([PY, f"{HERE}/ao5.py", box, arm, "--engine", ENG], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        if line.startswith("AO5JSON "):
            rec = json.loads(line[8:])
            out["AO5"] = rec["AO5"]
            out["AO5_checks"] = {k: v["pass"] for k, v in rec["checks"].items()}
    return out


# ------------------------------------------------------------------ leak rows (r13) and exploit repros


def leak_rows(box, arm):
    """T1-T4, C10c/C10d/C10e, the bond rows on held closes, via the r13 screen's functions. C10c reads the 270 pooled
    histories (owner decision 14), its 90-history count reported as C10c_breach_90; C10d and the `lever:` rows read
    the same 270 since the eighteenth registration."""
    import screen_eval as SE
    out = {}
    H = SE.load_gen(box, arm)
    if H:
        T = SE.rows_T(H); out.update({k: float(v) for k, v in T.items()})
        tdr, offs, c10e = SE.E.true_side(H)
        out["C10e"] = float(np.mean(c10e)); o = np.array(offs)
        out["firstcut_vs_onset_median"] = float(np.median(o)) if len(o) else np.nan
        out["firstcut_before_onset_share"] = float(np.mean(o < 0)) if len(o) else np.nan
        B = SE.bt_stats(H); out.update({"bt:" + k: float(v) for k, v in B.items()})
    LR = SE.load_lr(box, arm)
    if LR:
        tu, worst, tm, br, lev, dr = SE.E.grade(LR, None)
        out["C10c_breach_90"] = len(br)
        out["C10c_worst_90"] = "; ".join(f"{k} {tm[k][0]:+.2f}/{tm[k][1]:.2f}" for k in br[:3])
    POOL, note = load_pool(box, arm)
    if POOL:
        # the eighteenth registration: C10d and the `lever:` rows on all 270 long-run histories
        for z in POOL:
            z["pub"] = z["cyc"].astype(int)
        tu, worst, tm, br, lev, dr = SE.E.grade(POOL, None)
        keys = [(p, h) for p in list(range(5)) + ["cut", "hike"] for h in (63, 126)]
        bad = [k for k in keys if not ((-5.9 <= dr[k] <= 4.3) if k[1] == 63 else (-10.0 <= dr[k] <= 8.0))]
        out["C10d_fails"] = len(bad); out["C10d_bad"] = str(bad); out["C10d_histories"] = len(POOL)
        for k, v in lev.items():
            s = SE.summ(v); out[f"lever:{k}"] = f"{s['median']:+.2f}/{s['ahead']:.2f}"
    out["C10c_pool_note"] = note
    if POOL:
        out["C10c_breach"] = len(br); out["C10c_n"] = len(tm); out["C10c_histories"] = len(POOL)
        out["C10c_worst"] = "; ".join(f"{k} {tm[k][0]:+.2f}/{tm[k][1]:.2f}" for k in br[:3])
    return out


def sf3(worst):
    """SF3 (owner 2026-09-28 (1)): the median over seeds of each seed's worst paired session (log %), graded against
    the floor -13; the minimum over seeds, the form registered before, is reported beside it."""
    w = np.asarray(worst, float)
    return float(np.median(w)), float(w.min())


def sf_rows(box, arm):
    out = {}
    for fn in ("sf-rec.json", "sf.json"):
        f = f"{box}/{fn}"
        if not os.path.exists(f):
            continue
        res = json.load(open(f))["per_seed"].get(arm, {})
        sf1 = list(res.get("SF1", {}).values())
        if sf1:
            out["SF1"] = float(np.median(sf1))
        for file, per in res.items():
            if file.startswith("SF1"):
                continue
            rows = list(per.values())
            if not rows or not isinstance(rows[0], dict):
                continue
            vt = [r["vixtimer"] for r in rows]
            out[f"SF2:{file}"] = (float(np.mean(vt)), sum(x > 0 for x in vt), len(vt))
            out[f"SF3:{file}"], out[f"SF3min:{file}"] = sf3([r["worst"] for r in rows])
            out[f"SF5:{file}"] = float(np.median([r["corp_sd"] for r in rows]))
            if "off_phase" in rows[0]:
                out[f"SF4:{file}"] = int(sum(r["off_phase"] + r["one_day_troughs"] for r in rows))
                reg = [r["regain"] for r in rows if math.isfinite(r["regain"])]
                out[f"S1a:{file}"] = float(np.mean(reg)); out[f"S2:{file}"] = float(np.mean([r["rise"] for r in rows]))
                out[f"S1b:{file}"] = f"{sum(r['out_in'] for r in rows)}/{len(rows)}"
    return out


def probe_rows(box, arm):
    out = {}
    f = f"{box}/probe.log"
    if os.path.exists(f):
        for line in open(f):
            m = re.match(rf"{re.escape(arm)}\s+(\S+)\s+mean\s+([+-][\d.]+)%/yr\s+positive (\d+)/(\d+)", line)
            if m:
                out[f"L-rate:{m.group(1)}"] = (float(m.group(2)), int(m.group(3)), int(m.group(4)))
    return out


def h1_rows(box, arm):
    f = f"{box}/stab100-report.txt"
    out = {}
    r = subprocess.run([PY, f"{HERE}/deps/h1.py", f"{box}/stab100"], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        if line.startswith(arm + ":"):
            out["H1-100y"] = line.split(":", 1)[1].strip()
    return out


def rec_I(box, arm):
    f = f"{box}/recession-I.json"
    if not os.path.exists(f):
        return {}
    j = json.load(open(f)); a = j["arms"].get(arm)
    if not a:
        return {}
    return {"recI:S1a": a.get("S1a_regain_mean"), "recI:S1b": f"{a.get('S1b_out_within_24m')}/{a.get('n')}",
            "recI:S2": a.get("S2_rise_mean_pct"), "recI:pass": a.get("pass")}


def r7pre(box, arm):
    f = f"{box}/r7pre.json"
    if not os.path.exists(f):
        return {}
    j = json.load(open(f)); a = j.get("arms", {}).get(arm) or j.get(arm)
    if not a:
        return {}
    out = {"R7a_pre_pass": a.get("pass_pre")}
    for k in ("hike", "cut"):
        if k in a:
            out[f"R7a_pre_{k}"] = (a[k].get("excess_pre_bp"), a[k].get("excess_pre_se_bp"))
    return out


# ------------------------------------------------------------------ existing rows via criteria.py


def criteria(box, tag, arms, outdir):
    cg = f"{outdir}/certgrade-{tag}.json"
    lr = f"{DESIGN}/longrun"
    if not os.path.exists(cg):
        subprocess.run([PY, f"{HERE}/box/certgrade_box_hr.py", box, "--engine", ENG, "--json", cg],
                       cwd=lr, stdout=open(f"{outdir}/certgrade-{tag}.txt", "w"), stderr=subprocess.STDOUT)
    # the eighteenth registration: the long-run rows on all 270 histories (lr270.py), not the box's 90
    import lr270
    notes = lr270.prepare(box, arms)
    json.dump(notes, open(f"{outdir}/lr270-{tag}.json", "w"), indent=1)
    L270 = f"{box}/{lr270.SUB}"
    args = [PY, "criteria.py", "--longrun", f"{L270}/longrun-report.json", "--certgrade", cg, "--edge", f"{box}/edge.json",
            "--c4", f"{box}/c4a.json", "--c4", f"{box}/c4b.json", "--xsec", f"{L270}/xsec.json", "--driven", f"{box}/driven2020.json",
            "--driven2022", f"{box}/driven2022.json", "--c10", f"{box}/c10.json", "--r7", f"{box}/r7-event.json",
            "--r7", f"{box}/r7-eval.json", "--recession", f"{box}/recession.json", "--v1", f"{L270}/v1.json"]
    for a in arms:
        c9 = f"{box}/c9/{a}.json"
        args += ["--arm", a, "--impact", f"{a}={c9 if os.path.exists(c9) else box + '/impact-pt-v20.json'}"]
    args += ["--out", f"{outdir}/criteria-{tag}.txt", "--json", f"{outdir}/criteria-{tag}.json"]
    r = subprocess.run(args, cwd=lr, capture_output=True, text=True)
    if r.returncode != 0:
        print("criteria.py failed:", r.stderr[-2000:])
    return json.load(open(f"{outdir}/criteria-{tag}.json")), open(f"{outdir}/criteria-{tag}.txt").read()


R4_BAND = (0.15, 0.39)


def restate_r4(existing, held):
    """R4 (owner 2026-09-28 (4)): graded once, on the held close (the r13gen histories' bt:R4 held, as the real SPY-LQD
    2015-2025 figure is measured), band [0.15, 0.39], real +0.272. criteria.py's reading at the last print stays in the
    row as last_print, reported only. Returns the new row; an arm with no held reading fails."""
    old = dict(existing or {})
    ok = held is not None and np.isfinite(held) and R4_BAND[0] <= held <= R4_BAND[1]
    return {"pass": bool(ok), "values": [None if held is None else float(held)], "bands": [list(R4_BAND)], "real": [0.272],
            "usage": usage(held, *R4_BAND) if held is not None else float("nan"),
            "real_text": "+0.272", "model_text": "--" if held is None else f"{held:+.3f}",
            "edge_note": "graded on the held close; last print reported",
            "last_print": (old.get("values") or [None])[0], "last_print_pass_old": old.get("pass")}


def certcell(cg, arm, cell, key):
    try:
        j = json.load(open(cg))
        for c in j.get("cells", j) if isinstance(j, dict) else j:
            pass
    except Exception:
        return np.nan
    return np.nan


# ------------------------------------------------------------------ main


def ao_note(r, rid):
    """The pooled AO rows' detail beside the graded number (owner decision 10)."""
    if rid == "AO1" and r.get("AO1_keys"):
        return f" (a first {fmt(r.get('AO1'))} +- {r['AO1_se']:.5f} over {r['AO1_keys']:,} keys, {r.get('AO1_seeds')} seeds)"
    if rid in ("AO3", "AO4") and (f"{rid}_n" in r or f"{rid}_note" in r):
        if f"{rid}_n" not in r:
            return f" ({r[f'{rid}_note']})"
        return (f" (intercept {r[f'{rid}_intercept']:+.4f} se {r[f'{rid}_se']:.4f}, slope {r[f'{rid}_slope']:+.3f}, "
                f"n {r[f'{rid}_n']}, raw mean {r[f'{rid}_raw_mean']:+.4f})" + (f" {r[f'{rid}_note']}" if f"{rid}_note" in r else ""))
    return ""


def fmt(v):
    if isinstance(v, (float, np.floating)):
        return "nan" if not np.isfinite(v) else (f"{v:.3f}" if abs(v) < 100 else f"{v:.1f}")
    return str(v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("boxes", nargs="+")
    ap.add_argument("--arms", default="")
    ap.add_argument("--out", default=os.path.join(os.getcwd(), "grade_all.txt"))
    ap.add_argument("--json", default=os.path.join(os.getcwd(), "grade_all.json"))
    ap.add_argument("--skip-criteria", action="store_true")
    a = ap.parse_args()
    outdir = os.path.dirname(a.out)
    want = [x for x in a.arms.split(",") if x]
    RES = {}; CRIT_TXT = []
    for box in [os.path.abspath(b) for b in a.boxes]:
        arms = sorted(os.path.basename(p) for p in glob.glob(f"{box}/longrun/*") if os.path.isdir(p))
        arms = [x for x in arms if not want or x in want]
        tag = os.path.basename(box.rstrip("/"))
        check_box_plan(box)          # the seed plan the box recorded, before anything on it is graded
        if not a.skip_criteria and os.path.exists(f"{box}/longrun-report.json"):
            cj, ct = criteria(box, tag, arms, outdir)
            CRIT_TXT.append(ct)
        else:
            cj = json.load(open(f"{outdir}/criteria-{tag}.json")) if os.path.exists(f"{outdir}/criteria-{tag}.json") else {"arms": {}}
        for arm in arms:
            r = {"box": tag}
            LR90 = load_free(box, arm, "longrun"); G = load_free(box, arm, "r13gen")
            assert len(LR90) in (0, 90) or os.environ.get("GA_TEST"), (arm, len(LR90))
            POOL, pool_note = load_pool(box, arm)
            LR = POOL               # the eighteenth registration: the long-run rows on all 270
            r["n_longrun"] = len(LR); r["n_longrun_90"] = len(LR90); r["n_gen"] = len(G); r["lr_note"] = pool_note
            r["existing"] = cj["arms"].get(arm, {})
            for fn in (lambda: dv_rows(box, arm), lambda: cv_rows(LR), lambda: vix_rows(LR), lambda: bear_rows(G),
                       lambda: bond_rows(LR), lambda: vc_rows(box, arm), lambda: vc4_rows(LR), lambda: eg_rows(box, arm),
                       lambda: ph_rows(LR, POOL, pool_note, LR90), lambda: ph_harness(box, arm), lambda: q_rows(box, arm), lambda: ao_rows(box, arm), lambda: vc3(box, arm),
                       lambda: leak_rows(box, arm), lambda: sf_rows(box, arm), lambda: probe_rows(box, arm),
                       lambda: h1_rows(box, arm), lambda: rec_I(box, arm), lambda: r7pre(box, arm)):
                try:
                    r.update(fn())
                except Exception as exc:  # keep grading the rest
                    import traceback
                    r.setdefault("errors", []).append(traceback.format_exc()[-600:])
            if r["existing"]:
                r["existing"] = dict(r["existing"]); r["existing"]["R4"] = restate_r4(r["existing"].get("R4"), r.get("bt:R4 held"))
            RES[arm] = r
    # ---- print
    arms = list(RES)
    L = []
    L.append("PT-V21 REGISTRATION GRADE (EIGHTEENTH REGISTRATION; LONG-RUN ROWS ON 270 HISTORIES): EVERY ROW, PER ARM, " + (
        f"THE REGISTERED EXAM SEEDS (REGISTERED_GRADE {os.environ['REGISTERED_GRADE']})\n"
        if os.environ.get("REGISTERED_GRADE") else "HELD-OUT SEEDS ONLY\n"))
    L.append("== 1. EXISTING REGISTERED ROWS (criteria.py, twelfth registration; C9 on the arm's own impact curve)\n")
    L += CRIT_TXT
    L.append("R4 RESTATED (owner 2026-09-28 (4)): graded once on the held close, band [0.15, 0.39], real +0.272; the last-print"
             " reading above is reported only.\n   " + " | ".join(
                 f"{arm} held {fmt(RES[arm]['existing']['R4']['values'][0])} {'pass' if RES[arm]['existing']['R4']['pass'] else 'FAIL'}"
                 f" (last print {fmt(RES[arm]['existing']['R4']['last_print'])}, reported)" for arm in arms if RES[arm].get("existing")))
    L.append("\n== 2. NEW ROWS (statistic | band | real reference, source | per arm value and verdict)\n")
    for rid, (stat, lo, hi, real, src, gated) in ROWS.items():
        cells = []
        for arm in arms:
            v = RES[arm].get(rid.replace("-cal", "_cal") if rid == "C10e-cal" else rid)
            if isinstance(v, (int, float, np.floating, np.integer)) and v is not None:
                v = float(v)
                if rid == "C10e-cal":
                    ok = v <= 1.0 and RES[arm].get("C10e_cal_ahead", 1) <= 2 / 3
                    cells.append(f"{arm} {v:+.2f}/{RES[arm].get('C10e_cal_ahead', np.nan):.2f} {'pass' if ok else 'FAIL'}")
                    continue
                if rid == "AO2":
                    ok = within(v, lo, hi) and RES[arm].get("AO2_pos", 0) >= 28
                else:
                    ok = within(v, lo, hi)
                cells.append(f"{arm} {fmt(v)} {('pass' if ok else 'FAIL') if gated else 'rep'}" + ao_note(RES[arm], rid))
            else:
                cells.append(f"{arm} --" + ao_note(RES[arm], rid))
        band = f"[{lo:g}, {hi:g}]" if gated else "reported"
        L.append(f"{rid:8s} {stat}\n         band {band}; real {real} ({src})\n         " + " | ".join(cells))
    L.append("\n== 3. LEAK ROWS (r13 screen's definitions with the owner's restatements of 2026-09-28; bands in brackets)\n")
    LK = [("T1_median_bp", "T1 turn tell median |re-mark| at a true turn, bp [<= 50]", lambda v: v <= 50),
          ("T1_share", "T1 share of true turns over 1.7% [<= 0.10]", lambda v: v <= 0.10),
          ("T2_sd_bp", "T2 credit spread daily change sd, bp [1.5-6.2]", lambda v: 1.5 <= v <= 6.2),
          ("T2_over50_dec", "T2 days a decade the spread moves > 50 bp [<= 1]", lambda v: v <= 1.0),
          ("T3_bp", "T3 meeting step after a true turn less other meetings, bp [<= 25]", lambda v: v <= 25),
          ("T4", "T4 contraction+trough spread over expansion (reported; real 1.5-2.5)", None),
          ("C10e", "C10e forward 252 excess from 84 sessions into a true contraction [-13.5, +15.7]", lambda v: -13.5 <= v <= 15.7),
          ("C10d_fails", "C10d cells outside [-5.9,+4.3] at 63 / [-10,+8] at 126 [0]", lambda v: v == 0),
          ("C10c_breach", "C10c mirrored/levered C10 rules breaching median<=+1.0 & ahead<=2/3 (of 384), 270 pooled long-run histories (owner decision 14) [0]", lambda v: v == 0),
          ("C10c_breach_90", "C10c on the long run's 90 alone (reported)", None),
          ("bt:I-rate max err bp", "I-rate same-close invariant, max error bp [< 0.01]", lambda v: v < 0.01),
          ("bt:R3 held", "R3 on held closes [-0.36, +0.03]", lambda v: -0.36 <= v <= 0.03),
          ("bt:R4 held", "R4 on held closes (now the registered R4, section 1) [+0.15, +0.39]", None),
          ("bt:R3m", "R3m monthly [-0.40, +0.10]", lambda v: -0.40 <= v <= 0.10),
          ("bt:R4m", "R4m monthly [+0.20, +0.65]", lambda v: 0.20 <= v <= 0.65),
          ("bt:R4t lag1", "R4-lag corr(index_t, IGCORP_t+1) [|.| <= 0.10]", lambda v: abs(v) <= 0.10),
          ("bt:P(cut42|VIX 40+, rate>0.25)", "F-stress P(cut within 42 | VIX 40+, policy rate > 0.25) [0.50-1.00]; real 0.90", lambda v: 0.5 <= v <= 1.0),
          ("bt:P(cut42|VIX 40+)", "F-stress P(cut within 42 | VIX 40+), unconditioned (reported; real 0.40)", None),
          ("bt:P(hike42|VIX 30+)", "F-stress P(hike within 42 | VIX 30+) [<= 0.10]", lambda v: v <= 0.10),
          ("bt:bear median dff (pts)", "F-bear median policy change peak to trough in a 20% bear [<= -0.5]", lambda v: v <= -0.5),
          ("SF1", "SF1 forced VIX priced the day it is published [>= 0.75]", lambda v: v >= 0.75),
          ]
    for key, lab, test in LK:
        cells = []
        for arm in arms:
            v = RES[arm].get(key)
            if v is None:
                cells.append(f"{arm} --"); continue
            cells.append(f"{arm} {fmt(v)} {'' if test is None else ('pass' if test(v) else 'FAIL')}")
        L.append(f"  {lab}\n     " + " | ".join(cells))
    for arm in arms:
        r = RES[arm]
        L.append(f"\n  {arm}: " + "; ".join(f"{k} {fmt(v)}" for k, v in r.items()
                                             if k.startswith(("SF2", "SF3", "SF4", "SF5", "PH5_vol", "S1a", "S1b", "S2", "L-rate", "H1-100y", "lever:", "recI", "R7a_pre", "C10c_worst", "C10c_pool", "PH5_90", "PH5_n", "PH5_pool", "firstcut"))))
        if r.get("errors"):
            L.append(f"  {arm} ERRORS: " + " || ".join(e.replace("\n", " ")[-300:] for e in r["errors"]))
    txt = "\n".join(L)
    open(a.out, "w").write(txt)
    json.dump(RES, open(a.json, "w"), indent=1, default=lambda o: float(o) if isinstance(o, (np.floating, np.integer)) else str(o))
    print(txt)


if __name__ == "__main__":
    main()
