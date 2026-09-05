"""The thirty-seed gate for a survey pick: everything a preset has to pass,
in one run, on the pt-v7 protocol (CALIBRATION-FOLLOWUPS.md §62, §63).

  python tools/calibration/gate_pick.py --base pt-v7 \
      --overrides market_vol_alpha=0.25,market_vol_beta=0.739 [--seeds 30]

Runs, in order: thirty-seed panels at 252 and 504 days on the training
universe (fourteen statistics scored against the horizon-matched bands);
held VIX 45 crisis state (sector excess, cross-sectional correlation,
kurtosis, volatility); held-out seeds and a held-out 60-name universe at
252; then the response instrument against the base. Prints one block per
gate; the record quotes the block. §8 is separate (section8_check.py).
"""
from __future__ import annotations

import argparse
import json
import statistics
import statistics as st
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

# The source tree is APPENDED, never inserted ahead of site-packages.
# Inserting it shadows an installed pretium wheel with a source copy that
# has no compiled `_core`, which is invisible locally (the dev venv is an
# editable install pointing at these same files, extension included) and
# fatal on a fresh box, where the wheel is a real install. That is exactly
# how the first AWS gate batch died: "cannot import name '_core' from
# partially initialized module 'pretium'". atlas_survey.py never had the
# bug because it only ever added its own directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.append(str(Path(__file__).resolve().parent.parent.parent / "python"))
import tradefloor as pt  # noqa: E402
from tradefloor import Scenario, envelope, facts  # noqa: E402

TRAIN = tuple(range(101, 131))

#: Thirty, matching TRAIN, since round 122. It was six, and six was too few
#: for the statistic that reads worst here. `corr_persistence_acf1` has a
#: per-seed sd of 0.274, so a six-seed median carries about 0.11 of noise
#: against a band 0.73 wide. Seeds 1-6 happen to hold a -0.50 and two -0.25s:
#: the ho row read -0.2226 and called the statistic OUT, while the same build
#: at 100 seeds reads +0.1527 and the held-out 909 universe +0.1409, both
#: comfortably in. The pt-v14-out, pt-v15-in, pt-v16-out history an audit
#: found in this row was noise walking across a band edge rather than the
#: model moving.
#:
#: Thirty carries about 0.06, which is 8 percent of that band. It costs
#: nothing net: the rows no longer recompute per block, see `--ho-cache` in
#: gate_batch, so a campaign runs them once rather than twenty-six times.
HELDOUT = tuple(range(1, 31))

# The measurement universe. 111/40 is the search universe every recorded
# figure used; PT_UNIVERSE_SEED/PT_UNIVERSE_N override it for held-out
# universe cards (round 122). ho_universe stays pinned to 909/60 — it is
# the card's own cross-check and must not move with the override.
import os as _os
_U_SEED = int(_os.environ.get("PT_UNIVERSE_SEED", "111"))
_U_N = int(_os.environ.get("PT_UNIVERSE_N", "40"))


def _universe():
    return pt.Universe.random(_U_N, seed=_U_SEED)

_MODEL: dict = {}


def parse_overrides(text: str) -> dict[str, float]:
    return {k: float(v) for k, v in (kv.split("=") for kv in text.split(",") if kv)}


def model(base: str, overrides: dict[str, float]):
    return pt.ModelParams.from_preset(base, **overrides)



# ── The driven-window axis (§81, §100) ────────────────────────────────────
#
# The panel gates are UNDRIVEN, and §81 found a defect they cannot see: run
# through the real 2020-21 macro path, the model's scenario response GAIN is
# right within ten percent on all three driver channels, while its daily
# return sd is 1.51x real AAPL's. The expected response to a scenario is
# calibrated and the dispersion around it is too wide, so one run understates
# how much of its own move was the scenario.
#
# Any preset that changes crisis variance can move that ratio, and nothing in
# the panel would report it. So it is a gate axis rather than a script
# somebody remembers to run.
_COVID = Path(__file__).resolve().parent.parent.parent / "examples" / "data" / "covid-2020-2021.json"


def _covid_inputs():
    import datetime as dt
    raw = json.loads(_COVID.read_text(encoding="utf-8"))
    dates = [dt.date.fromisoformat(d) for d in raw["dates"]]
    first_cut, second_cut = dt.date(2020, 3, 3), dt.date(2020, 3, 15)
    policy = [0.01625 if d < first_cut else (0.01125 if d < second_cut else 0.00125)
              for d in dates]
    hyg0 = raw["hyg"][0]
    credit = [0.0554 + (hyg0 - x) / hyg0 / 3.8 for x in raw["hyg"]]
    spx = raw["spx"]
    k, trend = 2 / 201, [spx[0]]
    for v in spx[1:]:
        trend.append(trend[-1] + k * (v - trend[-1]))
    qe = [max(-0.35, min(0.35, spx[i] / trend[i] - 1)) for i in range(len(spx))]
    return raw, policy, credit, qe


def _sd(xs):
    n = len(xs); mu = sum(xs) / n
    return (sum((x - mu) ** 2 for x in xs) / n) ** 0.5


_QE_MEASURED = Path(__file__).resolve().parent / "data" / "qe-measured-2020-2021.json"


def qe_measured():
    """The MEASURED qe_pe_boost series, aligned to the covid window's dates.

    FRED WSHOSHO + WSHOMCB (securities held outright), trailing four-week
    purchases through the engine's own scale `0.10 * monthly / 120`. This is
    NOT what the certified driven gate runs: that remains the EMA proxy in
    `_covid_inputs`, because every recorded driven figure was measured
    against it and a silent swap would orphan them all. The measured series
    correlates -0.485 with the proxy -- QE is countercyclical, the proxy is
    procyclical -- so treat any conclusion transferred between them as
    unsupported until re-measured.
    """
    raw = json.loads(_COVID.read_text(encoding="utf-8"))
    meas = json.loads(_QE_MEASURED.read_text(encoding="utf-8"))
    assert meas["dates"] == raw["dates"], "the measured series must align by date"
    return meas["qe_pe_boost"]


_QE_ASSETS = Path(__file__).resolve().parent / "data" / "qe-assets-2020-2021.json"


def qe_assets_measured():
    """The measured holdings RATIO, daily, aligned to the covid window.

    FRED securities held outright over the window's first day, for the
    stock channel (`qe_pe_stock_gain`). This is the input the redesigned
    channel is calibrated against; the flow proxy stays what the certified
    gate runs.
    """
    raw = json.loads(_COVID.read_text(encoding="utf-8"))
    meas = json.loads(_QE_ASSETS.read_text(encoding="utf-8"))
    assert meas["dates"] == raw["dates"], "the assets series must align by date"
    return meas["qe_assets_ratio"]


def driven_window(m, seed: int, qe_series=None, qe_assets=None, freeze=()) -> dict:
    """Daily return sd and the VIX-channel gain, against real AAPL's own.

    `qe_series` replaces the proxy `qe_pe_boost` input day for day when
    given (see `qe_measured`); None runs the shipped proxy, bit for bit.
    `qe_assets` pins the holdings ratio day for day (see
    `qe_assets_measured`) for models whose stock channel is on; None
    leaves the ratio neutral.
    """
    import pyarrow as pa
    import pyarrow.compute as pc
    raw, policy, credit, qe = _covid_inputs()
    if qe_series is not None:
        assert len(qe_series) == len(qe), "qe_series must cover the window"
        qe = list(qe_series)
    n = len(raw["dates"])
    path = [{"day": i, "vix": raw["vix"][i], "federal_funds_rate": policy[i],
             "corporate_bond_yield": credit[i], "qe_pe_boost": qe[i]}
            for i in range(n)]
    if qe_assets is not None:
        assert len(qe_assets) == n, "qe_assets must cover the window"
        for i in range(n):
            path[i]["qe_assets_ratio"] = qe_assets[i]
    if freeze:
        # Channel attribution, round 76's method: a frozen channel holds its
        # day-zero value for the whole window, so the difference against the
        # full run is that channel's contribution. "vix" freezes the fear
        # path; "policy" the funds rate; "credit" the bond yield.
        keys = {"vix": "vix", "policy": "federal_funds_rate",
                "credit": "corporate_bond_yield"}
        for ch in freeze:
            k = keys[ch]
            v0 = path[0][k]
            for i in range(n):
                path[i][k] = v0
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

    def rets(x):
        return [x[i] / x[i - 1] - 1 for i in range(1, len(x))]

    r_sim, r_real = rets(close), rets(raw["aapl"])
    d_vix = [raw["vix"][i] - raw["vix"][i - 1] for i in range(1, n)]

    def beta(y, x):
        k = len(y); mx, my = sum(x) / k, sum(y) / k
        sxx = sum((v - mx) ** 2 for v in x)
        return sum((x[i] - mx) * (y[i] - my) for i in range(k)) / sxx

    return {"ret_sd": _sd(r_sim), "real_ret_sd": _sd(r_real),
            "noise_ratio": _sd(r_sim) / _sd(r_real),
            "vix_beta": beta(r_sim, d_vix), "real_vix_beta": beta(r_real, d_vix)}


# ── The multi-name driven axis (MULTINAME-DRIVEN.md, item 6) ──────────────
#
# The certified driven gate rides one name. This instrument runs the SAME
# 2020-2021 scenario path into a universe built from the reference-panel
# roster's real 2020 openings and asks whether the CROSS-SECTION behaves
# like the real one did. REPORTED, not banded: bands wait until seed and
# block noise are characterized (the deepseed lesson, applied in advance).
#
# Fundamentals are approximations and say so: eps values each name AT its
# sector anchor (zero initial mispricing — this instrument measures
# dynamics, not valuation), caps are order-correct 2020-02 figures, betas
# are sector volatilities. Real per name: sector, initial price, Jan-2020
# average volume.

_ROSTER_SECTOR = {
    "AAPL": "technology", "MSFT": "technology", "NVDA": "technology",
    "GOOGL": "telecommunications", "META": "telecommunications",
    "DIS": "telecommunications", "CMCSA": "telecommunications",
    "T": "telecommunications", "VZ": "telecommunications",
    "AMZN": "consumer_discretionary", "HD": "consumer_discretionary",
    "MCD": "consumer_discretionary", "NKE": "consumer_discretionary",
    "JPM": "financial_services", "BAC": "financial_services",
    "WFC": "financial_services", "GS": "financial_services",
    "C": "financial_services", "V": "financial_services",
    "MA": "financial_services",
    "XOM": "energy", "CVX": "energy", "COP": "energy",
    "JNJ": "healthcare", "PFE": "healthcare", "MRK": "healthcare",
    "UNH": "healthcare", "LLY": "healthcare", "ABT": "healthcare",
    "PG": "consumer_staples", "KO": "consumer_staples",
    "PEP": "consumer_staples", "WMT": "consumer_staples",
    "COST": "consumer_staples",
    "BA": "industrials", "CAT": "industrials", "GE": "industrials",
    "HON": "industrials",
    "UPS": "transportation", "UNP": "transportation",
}

# Approximate 2020-02 market caps, billions USD, order-correct from memory
# and recorded as approximations (they set cap weights, nothing else).
_ROSTER_CAP_B = {
    "AAPL": 1400, "MSFT": 1400, "GOOGL": 1000, "AMZN": 1000, "META": 600,
    "NVDA": 160, "JPM": 430, "BAC": 300, "WFC": 200, "GS": 80, "C": 160,
    "V": 420, "MA": 320, "XOM": 260, "CVX": 210, "COP": 60, "JNJ": 390,
    "PFE": 200, "MRK": 210, "UNH": 280, "LLY": 130, "ABT": 150, "PG": 310,
    "KO": 250, "PEP": 190, "WMT": 330, "COST": 130, "HD": 250, "MCD": 160,
    "NKE": 140, "DIS": 250, "CMCSA": 200, "T": 270, "VZ": 240, "BA": 190,
    "CAT": 75, "GE": 95, "HON": 120, "UPS": 90, "UNP": 120,
}

_SECTOR_PE = {"technology": 32.0, "financial_services": 12.0,
              "healthcare": 24.0, "energy": 10.0,
              "consumer_discretionary": 20.0, "consumer_staples": 20.0,
              "industrials": 17.0, "materials": 14.0, "real_estate": 35.0,
              "utilities": 16.0, "telecommunications": 14.0,
              "transportation": 15.0}
_SECTOR_BETA = {"technology": 1.2, "financial_services": 1.1,
                "healthcare": 0.9, "energy": 1.3,
                "consumer_discretionary": 1.0, "consumer_staples": 0.7,
                "industrials": 1.0, "materials": 1.2, "real_estate": 0.9,
                "utilities": 0.6, "telecommunications": 0.8,
                "transportation": 1.1}


def _roster_inputs(roster_path):
    raw = json.loads(Path(roster_path).read_text())
    dates = raw["dates"]
    closes = raw["closes"]
    vols = raw["avg_volume_2020_01"]
    # crash window: 2020-02-19 peak to 2020-03-23 trough, by calendar date
    crash = [i for i, d in enumerate(dates) if "2020-02-19" <= d <= "2020-03-23"]
    return dates, closes, vols, (crash[0], crash[-1])


def driven_basket(m, seed: int, roster_path, freeze=()) -> dict:
    """Four cross-sectional statistics, sim vs real, along the real path.

    Basket noise ratio (the driven ratio, de-AAPLed), dispersion path
    ratio at the crash trough and at window end, crash co-movement (mean
    pairwise correlation over the drawdown sessions), and the IQR of the
    per-name noise ratios.
    """
    import pyarrow as pa
    import pyarrow.compute as pc
    raw, policy, credit, qe = _covid_inputs()
    dates, closes, avg_vols, (c0, c1) = _roster_inputs(roster_path)
    assert dates == raw["dates"], "roster must align to the covid window"
    n = len(dates)
    path = [{"day": i, "vix": raw["vix"][i], "federal_funds_rate": policy[i],
             "corporate_bond_yield": credit[i], "qe_pe_boost": qe[i]}
            for i in range(n)]
    if freeze:
        # Round 76's channel attribution, on the basket: a frozen channel
        # holds its day-zero value for the whole window.
        keys = {"vix": "vix", "policy": "federal_funds_rate",
                "credit": "corporate_bond_yield"}
        for ch in freeze:
            k = keys[ch]
            v0 = path[0][k]
            for i in range(n):
                path[i][k] = v0
    scen = pt.Scenario.from_json(json.dumps(
        {"schema": 1, "label": "covid-basket", "days": n, "path": path}))
    names = sorted(closes)
    instruments = []
    for tk in names:
        sec = _ROSTER_SECTOR[tk]
        p0 = closes[tk][0]
        shares = _ROSTER_CAP_B[tk] * 1e9 / p0
        eps = p0 / _SECTOR_PE[sec]
        instruments.append(pt.Instrument(
            tk, sec, initial_price=p0, shares_outstanding=shares,
            eps=eps, book_value_per_share=eps * 4.0, revenue_growth=0.05,
            avg_volume=avg_vols[tk], beta=_SECTOR_BETA[sec],
            short_interest=shares * 0.01))
    u = pt.Universe(instruments)
    e = pt.Engine(seed=seed, universe=u, model=m)
    for i in range(n):
        scen.apply(e, i)
        e.run_days(1, first_day=i)
    b = pa.table(e.bars(grain="day"))

    def rets(x):
        return [x[i] / x[i - 1] - 1 for i in range(1, len(x))]

    sim_r, real_r = {}, {}
    for iid, tk in enumerate(names):
        close = pc.filter(b, pc.equal(b["instrument_id"], iid))["close"].to_pylist()
        sim_r[tk] = rets(close)
        real_r[tk] = rets(closes[tk])

    ratios = sorted(_sd(sim_r[tk]) / _sd(real_r[tk]) for tk in names)
    k = len(ratios)
    basket_ratio = statistics.median(ratios)
    iqr = ratios[(3 * k) // 4] - ratios[k // 4]

    def cum(r, upto):
        c = 1.0
        for x in r[:upto]:
            c *= 1 + x
        return c - 1.0

    def disp(rmap, upto):
        vals = [cum(rmap[tk], upto) for tk in names]
        return _sd(vals)

    disp_trough = disp(sim_r, c1) / disp(real_r, c1)
    disp_end = disp(sim_r, n - 1) / disp(real_r, n - 1)

    def mean_pairwise(rmap, i0, i1):
        # mean pairwise correlation via the standardized-sum identity
        seg = {tk: rmap[tk][i0:i1] for tk in names}
        zs = []
        for tk in names:
            s = seg[tk]
            mu = statistics.mean(s)
            sd = _sd(s)
            zs.append([(x - mu) / sd for x in s] if sd > 0 else [0.0] * len(s))
        m_ = len(zs)
        t_ = len(zs[0])
        tot = [sum(z[j] for z in zs) for j in range(t_)]
        var_tot = sum(x * x for x in tot) / t_
        # var(sum of standardized) = m + m(m-1)*rbar
        return (var_tot - m_) / (m_ * (m_ - 1.0)) if m_ > 1 else 0.0

    co_sim = mean_pairwise(sim_r, c0, c1)
    co_real = mean_pairwise(real_r, c0, c1)

    return {"basket_noise_ratio": basket_ratio,
            "noise_ratio_iqr": iqr,
            "dispersion_trough_ratio": disp_trough,
            "dispersion_end_ratio": disp_end,
            "crash_comovement_sim": co_sim,
            "crash_comovement_real": co_real}


def one(job):
    base, overrides, kind, seed = job
    m = model(base, overrides)
    if kind == "p252":
        f = facts.measure(seed=seed, universe=_universe(), days=252, model=m)
    elif kind == "p504":
        f = facts.measure(seed=seed, universe=_universe(), days=504, model=m)
    elif kind in ("vix5", "vix45", "vix65"):
        # The two ENDS as well as the middle: the crisis volatility lever is
        # vol(VIX 65) / vol(VIX 5), the headline number for any
        # crisis preset. A gate that reported only the middle sent every
        # candidate to a separate laptop run to find its lever (§93).
        held = {"vix5": 5.0, "vix45": 45.0, "vix65": 65.0}[kind]
        f = facts.measure(seed=seed, universe=_universe(), days=252,
                          model=m, scenario=Scenario().hold(vix=held))
    elif kind == "driven":
        return kind, driven_window(m, seed)
    elif kind == "ho_seeds":
        f = facts.measure(seed=seed, universe=_universe(), days=252, model=m)
    elif kind == "ho_universe":
        f = facts.measure(seed=seed, universe=pt.Universe.random(60, seed=909), days=252, model=m)
    else:
        raise ValueError(kind)
    return kind, {k: f.get(k) for k in list(facts.REAL_MARKETS)}


def summarise(kind: str, rows: list[dict]) -> str:
    # POOLED rows are skipped, not aggregated. Their graded value is the
    # median of every seed's samples together and these rows carry scalars
    # only, so there is nothing here to pool; `facts.aggregate_value`
    # refuses them rather than returning the median of the reporting seeds'
    # medians, which is a different statistic. This loop walks every key a
    # row carries, so it met that refusal the moment it was added -- and
    # before it was added, it was quietly computing the wrong number for
    # `fear_gauge_dn3` and feeding it to `envelope.score` below, where only
    # the shape and level rows are printed. Nothing wrong reached a reader;
    # nothing stopped it either.
    med = {k: facts.aggregate_value(k, [r[k] for r in rows if r.get(k) is not None])
           for k in rows[0]
           if facts.AGGREGATE.get(k) != "pooled"
           and any(r.get(k) is not None for r in rows)}
    if kind == "driven":
        return (f"  driven 2020-21 ({len(rows)} seeds): return sd "
                f"{med['ret_sd']:.4f} vs real AAPL {med['real_ret_sd']:.4f} "
                f"= {med['noise_ratio']:.2f}x; VIX gain {med['vix_beta']:.5f} "
                f"vs real {med['real_vix_beta']:.5f}")
    if kind in ("vix5", "vix65"):
        return (f"  held VIX {kind[3:]:<3s} ({len(rows)} seeds): vol "
                f"{med['annualised_vol_pct']:.1f} xs {med['cross_sectional_corr']:.3f}")
    if kind == "vix45":
        return (f"  held VIX 45 ({len(rows)} seeds): sector_ex {med['sector_excess_corr']:+.4f} "
                f"xs {med['cross_sectional_corr']:.3f} kurt {med['excess_kurtosis']:.2f} "
                f"vol {med['annualised_vol_pct']:.1f}")
    days = 504 if kind == "p504" else 252
    scored = envelope.score(med, horizon_days=days)
    sc = scored["statistics"]
    out = [k for k, v in sc.items() if not v.get("in_band", True) and k in facts.SHAPE]
    level = [f"{k} {med[k]:+.2f}" for k in facts.LEVEL if k in med]
    n = len(facts.SHAPE)
    return (f"  {kind:12s} {days}d ({len(rows)} seeds): {n - len(out)}/{n} shape in band; "
            f"level {', '.join(level) or 'n/a'}; vol "
            f"{med['annualised_vol_pct']:.1f} kurt {med['excess_kurtosis']:.2f} xs "
            f"{med['cross_sectional_corr']:.3f} sector_ex {med['sector_excess_corr']:+.4f} "
            f"persist {med['corr_persistence_acf1']:+.3f} acf1/5/20 {med['abs_return_acf1']:.3f}/"
            f"{med['abs_return_acf5']:.4f}/{med['abs_return_acf20']:.4f}  out: {', '.join(out) or '(none)'}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="pt-v7")
    ap.add_argument("--overrides", default="")
    ap.add_argument("--seeds", type=int, default=30, help="training seeds to use (30 is the gate)")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--no-response", action="store_true")
    args = ap.parse_args()
    ov = parse_overrides(args.overrides)
    m = model(args.base, ov)
    print(f"gate: {args.base} + {ov}  fingerprint {m.fingerprint}", flush=True)
    train = TRAIN[:args.seeds]
    jobs = ([(args.base, ov, "p252", s) for s in train] + [(args.base, ov, "p504", s) for s in train]
            + [(args.base, ov, "vix45", s) for s in train]
            + [(args.base, ov, "ho_seeds", s) for s in HELDOUT]
            + [(args.base, ov, "ho_universe", s) for s in HELDOUT])
    out: dict[str, list] = {}
    with ProcessPoolExecutor(args.workers) as ex:
        for kind, row in ex.map(one, jobs):
            out.setdefault(kind, []).append(row)
    for kind in ("p252", "p504", "vix45", "ho_seeds", "ho_universe"):
        print(summarise(kind, out[kind]), flush=True)
    if args.no_response:
        return 0
    # The response instrument opens its own pool: after ours has closed.
    import scenario_response as sr
    seeds = sr.DEFAULT_SEEDS if args.seeds >= 30 else train
    print("\nresponse instrument (vector vs base):", flush=True)
    v = sr.measure_vector(m, seeds, args.workers)
    r = sr.measure_vector(model(args.base, {}), seeds, args.workers)
    for key in ("vol_lever", "corr_blend", "shock_ratio_median"):
        print(f"  {key:20s} {v[key]:>8.3f}  base {r[key]:>8.3f}")
    for vix in sr.HELD_VIX:
        k = str(vix)
        print(f"  VIX {vix:<5.0f} vol {v['held_vix'][k]['annualised_vol_pct']:>6.1f}  base "
              f"{r['held_vix'][k]['annualised_vol_pct']:>6.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
