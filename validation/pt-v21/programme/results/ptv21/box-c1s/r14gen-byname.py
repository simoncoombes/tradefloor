"""Free histories for the r14 screen: r13gen.py's recording (the leak repros'
input, same keys, same loop) plus the per-name state the realism rows read.

pt-v20 with each arm's dials on the certified roster Universe.random(40,
seed=111, bonds=True), one session per day (open_market, 390 ticks,
close_market). After each close:

  OUT/ARM/SEED-free.npz   r13gen.py's keys (pre/post index, rate indices,
                          published and true phase, published macro, true
                          economy, meetings), plus
                          e_gdp e_cpi e_market_pe e_earnings_cycle  economy
                          base          the nominal output base
  OUT/ARM/names-SEED.npz  per equity name, float32 (days x 40):
                          open    the opening print (prices after open_market)
                          post    the close (after the close's re-mark)
                          pre     the last print before the close
                          vol     the session's volume
                          earn    1 on a name's earnings reaction session
                          paid    cash dividend the name went ex for at the open
                          amount  the declared quarterly amount (dividend state)
                          v       fair_value_offset (the valuation level)
                          lbs     accrued log share-count reduction (buyback_accrual)
                          and eps0/book0/rg0 (fundamentals at day 0), payout (last
                          dividend payout), shares, sectors, tickers, m_* (the
                          published policy rate, 10-year, VIX), e_*, ant, base.

The names file is gen_impl.py's (earnings-gaps) and gen_dv.py's (dividends)
recording in one pass, so common_eg.measure and dv_rows.py read it unchanged.

usage: r14gen.py OUTDIR ARMS_FILE SEEDS DAYS WORKERS

SEEDS is the job's GEN_SEEDS. It must pass seedplan.py's registered-grade rule as the protocol SEED_PROTOCOL
(default truephase): held out, or exactly the registered exam set with REGISTERED_GRADE set; the old exam seeds
(101-190, 401-430, 701-730) are refused always."""
import sys, os, time
from concurrent.futures import ProcessPoolExecutor, as_completed
import numpy as np

CYC = ("expansion", "peak", "contraction", "trough", "recovery")
FIELDS = ("vix", "federal_funds_rate", "corporate_bond_yield", "inflation_rate", "gdp_growth",
          "unemployment_rate", "oil_price", "treasury_yield_2y", "treasury_yield_10y",
          "fear_greed_index")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seedplan  # noqa: E402


def one(spec):
    out, name, dials, seed, days = spec
    path = f"{out}/{name}/{seed}-free.npz"
    npath = f"{out}/{name}/names-{seed}.npz"
    if os.path.exists(path) and os.path.exists(npath):
        return name, seed, 0.0, "cached"
    import tradefloor as tf
    u = tf.Universe.random(40, seed=111, bonds=True)
    m = tf.ModelParams.from_preset('pt-v21', **dials)
    e = tf.Engine(seed=seed, universe=u, model=m)
    specs = [x["ticker"] for x in tf.rate_specs()]
    tick = list(e.tickers)
    ri = [tick.index(t) for t in specs]
    eq = [i for i, t in enumerate(tick) if t not in specs]
    byt = {x.ticker: x for x in u}
    sh = np.array([float(byt[tick[i]].shares_outstanding) for i in eq])
    n = len(eq)
    f64 = lambda b: np.frombuffer(b, dtype="<f8").copy()
    scal = ("pre", "post", "ffr", "gdp", "unemp", "infl", "corp", "t10", "vix", "ant", "ec", "vfb",
            "e_gdp", "e_cpi", "e_market_pe", "e_earnings_cycle")
    rec = {k: np.zeros(days) for k in scal}
    rec["cyc"] = np.zeros(days, np.int8); rec["true"] = np.zeros(days, np.int8); rec["meet"] = np.zeros(days, np.int8)
    rec["rpre"] = np.zeros((days, 3)); rec["rpost"] = np.zeros((days, 3)); rec["yld"] = np.zeros((days, 3))
    for f in FIELDS:
        rec["m_" + f] = np.zeros(days)
    NM = {k: np.zeros((days, n), np.float32) for k in ("open", "post", "pre", "vol", "paid", "amount", "v", "lbs")}
    earn = np.zeros((days, n), bool)
    payout = np.zeros(n)
    fund = e.fundamentals()
    eps0, book0, rg0 = (np.array(x, float)[eq] if len(x) == len(tick) else np.array(x, float)[:n] for x in fund)
    m0 = e.macro_fields
    rec["yld0"] = np.array([m0["treasury_yield_2y"], m0["treasury_yield_10y"], m0["corporate_bond_yield"]])
    rec["rp0"] = f64(e.prices())[ri]
    rec["p0"] = f64(e.prices())[eq]
    last_meet = e.state_snapshot()["central_bank"]["last_meeting_date"]
    PI = []
    base = np.nan
    t0 = time.time()
    for d in range(days):
        e.open_market()
        NM["open"][d] = f64(e.prices())[eq]
        try:
            x = f64(e.earnings_surprises())
            earn[d] = (x[eq] if len(x) == len(tick) else x[:n]) != 0.0
        except Exception:
            pass
        e.run_session(9, 30, 3, 390)
        p = f64(e.prices()); rec["pre"][d] = (p[eq] * sh).sum(); rec["rpre"][d] = p[ri]; NM["pre"][d] = p[eq]
        e.close_market()
        p = f64(e.prices()); rec["post"][d] = (p[eq] * sh).sum(); rec["rpost"][d] = p[ri]; NM["post"][d] = p[eq]
        NM["vol"][d] = f64(e.column("volume"))[eq]
        dv = np.array(e.dividends_today(), float)
        NM["paid"][d] = dv[eq] if len(dv) == len(tick) else dv[:n]
        s = e.state_snapshot(); ec = s["economy"]; cb = s["central_bank"]
        if "dividend" in s:
            st = f64(s["dividend"]).reshape(-1, 7)
            st = st[eq] if st.shape[0] == len(tick) else st[:n]
            NM["amount"][d] = np.nan_to_num(st[:, 3]); payout = np.nan_to_num(st[:, 0])
        if "fair_value_offset" in s:
            v = f64(s["fair_value_offset"]); NM["v"][d] = v[eq] if len(v) == len(tick) else v[:n]
        if "buyback_log_shares" in s:
            v = f64(s["buyback_log_shares"]); NM["lbs"][d] = v[eq] if len(v) == len(tick) else v[:n]
        base = float(s.get("nominal_output_base", np.nan))
        mf = e.macro_fields
        rec["cyc"][d] = CYC.index(mf["cycle"]); rec["true"][d] = CYC.index(ec["cycle_phase"])
        for f in FIELDS:
            rec["m_" + f][d] = float(mf[f])
        rec["yld"][d] = [mf["treasury_yield_2y"], mf["treasury_yield_10y"], mf["corporate_bond_yield"]]
        rec["ffr"][d] = ec["federal_funds_rate"]; rec["gdp"][d] = ec["gdp_growth"]
        rec["unemp"][d] = ec["unemployment_rate"]; rec["infl"][d] = ec["inflation_rate"]
        rec["corp"][d] = ec["corporate_bond_yield"]; rec["t10"][d] = ec["treasury_yield_10y"]
        rec["vix"][d] = ec["vix"]; rec["ant"][d] = e.earnings_anticipation
        rec["ec"][d] = ec.get("earnings_cycle", 0.0); rec["vfb"][d] = ec.get("vix_feedback", 0.0)
        for k in ("gdp", "cpi", "market_pe", "earnings_cycle"):
            rec["e_" + k][d] = float(ec.get(k, np.nan))
        rec["meet"][d] = 1 if cb["last_meeting_date"] != last_meet else 0
        last_meet = cb["last_meeting_date"]
        if "cycle_nowcast" in ec:
            PI.append(list(ec["cycle_nowcast"]) if not isinstance(ec["cycle_nowcast"], dict) else
                      [ec["cycle_nowcast"].get(c, np.nan) for c in CYC])
    rec["level"] = rec["post"]
    rec["spread"] = rec["corp"] - rec["t10"]
    rec["base"] = np.array(base)
    if PI:
        try:
            rec["pi"] = np.array(PI, dtype=float)
        except (TypeError, ValueError):
            pass
    rec["hash"] = np.array(e.state_hash())
    os.makedirs(f"{out}/{name}", exist_ok=True)
    tmp = f"{out}/{name}/.{seed}.tmp.npz"
    np.savez_compressed(tmp, **rec)
    os.replace(tmp, path)
    tmp = f"{out}/{name}/.names-{seed}.tmp.npz"
    np.savez_compressed(tmp, earn=earn, payout=payout, eps0=eps0, book0=book0, rg0=rg0, shares=sh,
                        sectors=np.array([byt[tick[i]].sector for i in eq]), tickers=np.array([tick[i] for i in eq]),
                        dials=np.array(repr(sorted(dials.items()))), preset=np.array('pt-v21'),
                        base=np.array(base), ant=rec["ant"],
                        **{"e_" + k: rec["e_" + k] for k in ("gdp", "cpi", "market_pe", "earnings_cycle")},
                        **{"m_" + k: rec["m_" + k] for k in ("federal_funds_rate", "treasury_yield_10y", "vix")},
                        **NM)
    os.replace(tmp, npath)
    return name, seed, time.time() - t0, m.fingerprint


def arms_of(path):
    arms = []
    for line in open(path):
        line = line.split("#")[0].strip()
        if not line:
            continue
        head, _, body = line.partition(":")
        name = head.split("@")[0]
        arms.append((name, {k: float(v) for k, v in (x.split("=") for x in body.split(",") if x)}))
    return arms


def seeds_of(text):
    return seedplan.parse(text)          # checked against the launched GEN_SEEDS by cert_reg18_box.py


def main():
    out, arms_file, seeds, days, workers = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5])
    arms = arms_of(arms_file)
    specs = [(out, n, d, s, days) for s in seeds_of(seeds) for n, d in arms]
    t0 = time.time(); done = 0
    with ProcessPoolExecutor(workers) as ex:
        futs = [ex.submit(one, sp) for sp in specs]
        for f in as_completed(futs):
            try:
                name, seed, secs, fp = f.result()
                done += 1
                print(f"{name} {seed} {secs:.0f}s {fp} [{done}/{len(specs)}, {time.time()-t0:.0f}s]", flush=True)
            except Exception as exc:  # keep the rest of the grid
                print("FAILED", repr(exc), flush=True)


if __name__ == "__main__":
    main()
