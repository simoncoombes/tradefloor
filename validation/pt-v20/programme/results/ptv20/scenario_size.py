"""How big the packaged scenarios' price effects are, paired against the same
seed with no scenario. Certified roster (Universe.random(40, seed=111)), the
index its cap-weighted closes at fixed shares, the scenario applied from day 0
as run_scenario and evaluate apply it (every packaged file fires at day 50).

    python scenario_size.py OUT.json [--model pt-v19 | --dials k=v,...] [--seeds 301-330]
"""
import sys, json, argparse, pathlib, time
import numpy as np, tradefloor as tf
from multiprocessing import Pool
SCEN = ['rate_shock', 'recession', 'liquidity_crisis', 'geopolitical_conflict', 'oil_price_spike', 'policy_regime_shift']
SHOCK = 50
H = (21, 63, 85, 120)

def f64(b):
    return np.frombuffer(b, dtype='<f8').copy()

def path(model, seed, scen, days, n, useed):
    base, dials = model
    u = tf.Universe.random(n, seed=useed)
    e = tf.Engine(seed=seed, universe=u, model=tf.ModelParams.from_preset(base, **dials))
    if scen and scen.endswith('.yml'):
        sc = tf.Scenario.from_yaml(open(scen).read())
    else:
        sc = tf.Scenario.load(scen) if scen else None
    sh = np.array([i.shares_outstanding for i in u])
    idx, vix, cy, ff, px = [], [], [], [], []
    for d in range(days):
        if sc is not None:
            sc.apply(e, d)
        e.open_market(); e.run_session(9, 30, 3, 390); e.close_market()
        p = f64(e.prices()); px.append(p)
        idx.append(float((p * sh).sum()))
        m = e.macro_state
        vix.append(m.vix); cy.append(m.corporate_bond_yield); ff.append(m.federal_funds_rate)
    return np.array(idx), np.array(vix), np.array(cy), np.array(ff), np.array(px)

def job(a):
    model, seed, scen, days, n, useed = a
    b = path(model, seed, None, days, n, useed)
    try:
        s = path(model, seed, scen, days, n, useed)
    except Exception as exc:   # a packaged scenario the endogenous state refuses (e.g. growth out of band)
        return {'seed': seed, 'scenario': scen, 'refused': f'{type(exc).__name__}: {exc}'[:300]}
    o = {'seed': seed, 'scenario': scen}
    for lab, (I, V, C, F, P) in (('base', b), ('scen', s)):
        o[lab] = {}
        for h in H:
            seg = I[SHOCK - 1:SHOCK + h]
            o[lab][f'index_{h}d_pct'] = float((seg[-1] / seg[0] - 1) * 100)
            o[lab][f'maxdd_{h}d_pct'] = float(((seg / np.maximum.accumulate(seg)) - 1).min() * 100)
            o[lab][f'median_name_{h}d_pct'] = float(np.median(P[SHOCK - 1 + h] / P[SHOCK - 1] - 1) * 100)
        r = np.diff(np.log(I[SHOCK - 1:SHOCK + 63]))
        o[lab]['realised_vol_63d_pct'] = float(r.std() * np.sqrt(252) * 100)
        o[lab]['vix_pre'] = float(V[SHOCK - 1]); o[lab]['vix_peak'] = float(V[SHOCK:SHOCK + 120].max())
        o[lab]['corp_yield_change_bp'] = float((C[SHOCK + 62] - C[SHOCK - 1]) * 1e4)
        o[lab]['policy_change_bp'] = float((F[SHOCK + 62] - F[SHOCK - 1]) * 1e4)
        o[lab]['index_pre50_pct'] = float((I[SHOCK - 1] / I[0] - 1) * 100)
    return o

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('out'); ap.add_argument('--model', default='pt-v19'); ap.add_argument('--dials', default='')
    ap.add_argument('--seeds', default='301-330'); ap.add_argument('--workers', type=int, default=3)
    ap.add_argument('--n', type=int, default=40); ap.add_argument('--useed', type=int, default=111)
    ap.add_argument('--scenarios', default='', help='comma list of names or .yml paths (default: the six packaged)')
    a = ap.parse_args()
    scen_list = a.scenarios.split(',') if a.scenarios else SCEN
    dials = {k: float(v) for k, v in (kv.split('=') for kv in a.dials.split(',') if kv)}
    model = (a.model, dials)
    lo, hi = (int(x) for x in a.seeds.split('-'))
    jobs = [(model, s, sc, SHOCK + max(H) + 1, a.n, a.useed) for sc in scen_list for s in range(lo, hi + 1)]
    t0 = time.time()
    with Pool(a.workers) as p:
        res = p.map(job, jobs)
    json.dump({'model': a.model, 'dials': dials, 'n': a.n, 'useed': a.useed, 'runs': res}, open(a.out, 'w'), indent=0)
    print(f'{time.time() - t0:.0f}s')
    for sc in scen_list:
        R = [r for r in res if r['scenario'] == sc and 'refused' not in r]
        nref = sum(1 for r in res if r['scenario'] == sc and 'refused' in r)
        if not R:
            print(f"{sc:22s} every run refused ({nref})"); continue
        def med(f):
            return float(np.median([f(r) for r in R]))
        print(f"{sc:22s} paired index 21/63/85/120d: " + " ".join(
            f"{med(lambda r: r['scen'][f'index_{h}d_pct'] - r['base'][f'index_{h}d_pct']):+6.1f}" for h in H)
              + f" | scen maxdd120 {med(lambda r: r['scen']['maxdd_120d_pct']):+6.1f} (base {med(lambda r: r['base']['maxdd_120d_pct']):+6.1f})"
              + f" | vix {med(lambda r: r['scen']['vix_pre']):.1f}->{med(lambda r: r['scen']['vix_peak']):.1f} (base peak {med(lambda r: r['base']['vix_peak']):.1f})"
              + f" | corp {med(lambda r: r['scen']['corp_yield_change_bp'] - r['base']['corp_yield_change_bp']):+.0f}bp policy {med(lambda r: r['scen']['policy_change_bp'] - r['base']['policy_change_bp']):+.0f}bp"
              + f" | rv63 {med(lambda r: r['scen']['realised_vol_63d_pct']):.1f} vs {med(lambda r: r['base']['realised_vol_63d_pct']):.1f}"
              + (f" | refused {nref}" if nref else ""))
