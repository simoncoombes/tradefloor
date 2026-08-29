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
HELDOUT = (1, 2, 3, 4, 5, 6)
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


def one(job):
    base, overrides, kind, seed = job
    m = model(base, overrides)
    if kind == "p252":
        f = facts.measure(seed=seed, universe=pt.Universe.random(40, seed=111), days=252, model=m)
    elif kind == "p504":
        f = facts.measure(seed=seed, universe=pt.Universe.random(40, seed=111), days=504, model=m)
    elif kind in ("vix5", "vix45", "vix65"):
        # The two ENDS as well as the middle: the crisis volatility lever is
        # vol(VIX 65) / vol(VIX 5), and it is the headline number for any
        # crisis preset. A gate that reported only the middle sent every
        # candidate to a separate laptop run to find its lever (§93).
        held = {"vix5": 5.0, "vix45": 45.0, "vix65": 65.0}[kind]
        f = facts.measure(seed=seed, universe=pt.Universe.random(40, seed=111), days=252,
                          model=m, scenario=Scenario().hold(vix=held))
    elif kind == "driven":
        return kind, driven_window(m, seed)
    elif kind == "ho_seeds":
        f = facts.measure(seed=seed, universe=pt.Universe.random(40, seed=111), days=252, model=m)
    elif kind == "ho_universe":
        f = facts.measure(seed=seed, universe=pt.Universe.random(60, seed=909), days=252, model=m)
    else:
        raise ValueError(kind)
    return kind, {k: f.get(k) for k in list(facts.REAL_MARKETS)}


def summarise(kind: str, rows: list[dict]) -> str:
    med = {k: st.median([r[k] for r in rows if r.get(k) is not None]) for k in rows[0]}
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
    sc = envelope.score(med, horizon_days=days)["statistics"]
    out = [k for k, v in sc.items() if not v.get("in_band", True)]
    n = len(facts.REAL_MARKETS)
    return (f"  {kind:12s} {days}d ({len(rows)} seeds): {n - len(out)}/{n} in band; vol "
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
