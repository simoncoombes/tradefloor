"""Desk arms for pt-v20: untraded histories on the certified roster, read for
C4a (the tape) and C5-C8 (the cross-section), plus the stationary spread of s.

    python desk.py OUT.json ARM=dial=v,dial=v ... [--seeds 201-206] [--days 2660] [--workers 3]

An arm's dials are applied on pt-v19 (--base). Seeds default to 201-206, which
the grade never uses (it runs 101-130 and 1-30)."""
import sys, json, time, argparse, pathlib
import numpy as np, pyarrow as pa, tradefloor as tf
from multiprocessing import Pool
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import xsec

def f64(b):
    return np.frombuffer(b, dtype='<f8').copy()

def model_for(base, dials):
    return tf.ModelParams.from_preset(base, **dials) if dials else tf.ModelParams.from_preset(base)

def run(job):
    base, name, dials, seed, days, n, useed = job[:7]
    model_px = job[7] if len(job) > 7 else False
    u = tf.Universe.random(n, seed=useed)
    e = tf.Engine(seed=seed, universe=u, model=model_for(base, dials))
    pd_ = model_for(base, dials).to_dict()
    carries = pd_['fair_value_news_share'] != 0 or pd_['opening_mispricing_sigma'] != 0 or pd_['opening_market_sigma'] != 0
    close = np.zeros((days, n)); S = np.zeros((days, n)); V = np.zeros((days, n)); MID = np.zeros((days, n))
    YLD = np.zeros((days, 3))   # 2y, 10y, corporate, per cent
    ECY = np.zeros(days); PHASE = []
    steps = []            # 65-minute grid prints, first 252 days
    spread = []           # quoted half-spread over mid at the close, first 252 days
    tickers = e.tickers
    for d in range(days):
        e.open_market(); e.run_session(9, 30, 3, 390)
        if d < 252:
            sp = f64(e.session_prices()).reshape(390, n)
            steps.append(sp[[64, 129, 194, 259, 324, 389]])
            if d % 5 == 0:
                q = []
                for t in tickers:
                    b = e.book(t)
                    q.append((b.best_ask - b.best_bid) / (b.best_ask + b.best_bid))
                spread.append(q)
        close[d] = f64(e.prices())
        ec = e.state_snapshot()['economy']
        ECY[d] = ec.get('earnings_cycle', 0.0); PHASE.append(ec['cycle_phase'])
        if model_px:
            e.record(d)
            tb = pa.RecordBatchReader.from_stream(e.prints(day=d)).read_all()
            MID[d] = tb.column('model_price').to_numpy().reshape(-1, n)[-1]
            e.clear_recording()
        S[d] = f64(e.column('mispricing_s'))
        if carries:
            V[d] = f64(e.state_snapshot()['fair_value_offset'])
        e.close_market()
        # The yields AT this close: the close's macro step sets them, reading
        # the session's return (the flight to quality), so they are read after
        # it, as FRED's close pairs with the index's close. Read before it
        # (boxes through ptv20g2) they were the previous close's, a day behind
        # the index return R3 and R4 correlate them with.
        ec = e.state_snapshot()['economy']
        YLD[d] = [ec['treasury_yield_2y'], ec['treasury_yield_10y'], ec['corporate_bond_yield']]
    o = {'arm': name, 'seed': seed}
    # C4a: 65-minute print returns, per-name lag-1 autocorrelation, Roll
    g = np.log(np.concatenate(steps))                 # [252*6, n]
    r = np.diff(g, axis=0)
    ac = [np.corrcoef(r[:-1, i], r[1:, i])[0, 1] for i in range(n)]
    cov = [np.cov(r[:-1, i], r[1:, i])[0, 1] for i in range(n)]
    roll = [2 * np.sqrt(-c) if c < 0 else 0.0 for c in cov]     # full spread, log units
    qs = np.median(np.array(spread), axis=0) * 2                # full quoted spread / mid
    o['c4a_acf1_65m'] = float(np.median(ac))
    o['c4a_roll_over_quoted'] = float(np.median(np.array(roll) / qs))
    ri = r - r.mean(1, keepdims=True)
    o['c4a_idio_acf1_65m'] = float(np.median([np.corrcoef(ri[:-1, i], ri[1:, i])[0, 1] for i in range(n)]))
    # C5-C8 on the whole history, and the value signal -(s + v)
    value = -(S + V)
    o.update(xsec.rows(close, value=value, value_windows=((0, 60), (250, days - 21))))
    rd = np.diff(np.log(close), axis=0)
    o['idio_acf1_daily'] = float(np.median([np.corrcoef(x[:-1], x[1:])[0, 1] for x in (rd - rd.mean(1, keepdims=True)).T]))
    o['acf1_daily'] = float(np.median([np.corrcoef(x[:-1], x[1:])[0, 1] for x in rd.T]))
    # what carries the idiosyncratic daily variance, and the close's bounce
    ds = np.diff(S, axis=0); dv = np.diff(V, axis=0)
    dsi = ds - ds.mean(1, keepdims=True); dvi = dv - dv.mean(1, keepdims=True); rdi = rd - rd.mean(1, keepdims=True)
    tot = np.median(rdi.var(0))
    o['share_var_dv'] = float(np.median(dvi.var(0)) / tot)
    o['share_var_ds'] = float(np.median(dsi.var(0)) / tot)
    caps = np.array([i.initial_price * i.shares_outstanding for i in u])
    big = caps >= 1e10
    o['lm1_big_bps'] = float(xsec.lo_mackinlay(rd[:, big]).mean() * 1e4)
    o['vr60_big'] = float(xsec.variance_ratio(rd[:, big], 60))
    for lab, sig in (('value_ic_big', value[:, big]),):
        o[lab] = float(np.nanmean(xsec.ic_series(sig, np.log(close[:, big]), 0, 5)))
    o['mom12_1_big'] = float(np.nanmean(xsec.momentum_ic(np.log(close[:, big]), 252)))
    if model_px:
        rm = np.diff(np.log(MID), axis=0)
        o['lm1_model_bps'] = float(xsec.lo_mackinlay(rm).mean() * 1e4)
        o['lm1_model_big_bps'] = float(xsec.lo_mackinlay(rm[:, big]).mean() * 1e4)
        o['close_gap_sd_big_bps'] = float(np.median(np.log(close / MID)[:, big].std(0)) * 1e4)
        o['close_gap_sd_small_bps'] = float(np.median(np.log(close / MID)[:, ~big].std(0)) * 1e4)
    o['s_xsd_stationary'] = float(np.mean((S[250:] - S[250:].mean(1, keepdims=True)).std(1)))
    o['s_xsd_open'] = float((S[0] - S[0].mean()).std())
    o['v_xsd_end'] = float(V[-1].std())
    # index: cap-weighted closes of the roster (shares fixed)
    sh = np.array([i.shares_outstanding for i in u])
    idx = (close * sh).sum(1)
    ir = np.diff(np.log(idx))
    o['index_vol_pct'] = float(ir.std() * np.sqrt(252) * 100)
    li = np.log(idx)
    # R1-R4: the curve and the stock-bond correlation (bp; the roster index)
    dY = np.diff(YLD, axis=0) * 100
    o['r1_dy2_sd_bp'] = float(dY[:, 0].std()); o['r2_dy10_sd_bp'] = float(dY[:, 1].std())
    o['r3_corr_stock_tsy'] = float(np.corrcoef(ir, -dY[:, 1])[0, 1])
    o['r4_corr_stock_corp'] = float(np.corrcoef(ir, -dY[:, 2])[0, 1])
    o['ig_yield_dy_sd_bp'] = float(dY[:, 2].std())
    # E1: the aggregate earnings fall around each contraction: exp(e) from its
    # highest in the 252 sessions before the contraction starts to its lowest
    # in the 504 after (log levels; the nominal-output path is not in e)
    falls = []
    for k in range(1, days):
        if PHASE[k] == 'contraction' and PHASE[k - 1] != 'contraction':
            pre = ECY[max(0, k - 252):k + 1]; post = ECY[k:k + 505]
            if len(post) > 60:
                falls.append(float(np.exp(post.min() - pre.max()) - 1))
    o['e1_earnings_falls'] = falls
    o['earnings_cycle_sd'] = float(ECY.std())
    # a 60/40 book, rebalanced daily: the index and a 10-year bond (duration
    # 8.5, carry the yield), reported against the tape's 10.9 per cent
    bond = YLD[:-1, 1] / 100 / 252 - 8.5 * np.diff(YLD[:, 1]) / 100
    o['sixty_forty_vol_pct'] = float((0.6 * ir + 0.4 * bond).std() * np.sqrt(252) * 100)
    o['b9_first60'] = float(li[60] - li[0])
    o['b9_steady60'] = [float(li[a + 60] - li[a]) for a in (250, 310, 370, 430)] if len(li) > 491 else []
    ws = close[0] * sh
    o['opening_capweighted_s'] = float((ws / ws.sum() * S[0]).sum())
    o['index_ret_pct'] = float(ir.mean() * 252 * 100)
    w = close[-1] * sh; w = w / w.sum(); o['herfindahl_end'] = float((w ** 2).sum())
    w0 = close[0] * sh; w0 = w0 / w0.sum(); o['herfindahl_start'] = float((w0 ** 2).sum())
    o['idio_daily_sd'] = float(np.median((rd - rd.mean(1, keepdims=True)).std(0)))
    return o

def parse_seeds(t):
    a, _, b = t.partition('-')
    return list(range(int(a), int(b or a) + 1))

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('out'); ap.add_argument('arms', nargs='+')
    ap.add_argument('--base', default='pt-v19'); ap.add_argument('--seeds', default='201-206')
    ap.add_argument('--days', type=int, default=2660); ap.add_argument('--n', type=int, default=40)
    ap.add_argument('--useed', type=int, default=111); ap.add_argument('--workers', type=int, default=3)
    ap.add_argument('--model-price', action='store_true')
    a = ap.parse_args()
    arms = []
    for t in a.arms:
        name, _, body = t.partition('=')
        dials = {}
        for kv in filter(None, body.split(',')):
            k, _, v = kv.partition('=')
            dials[k] = float(v)
        arms.append((name, dials))
    jobs = [(a.base, n, d, s, a.days, a.n, a.useed, a.model_price) for n, d in arms for s in parse_seeds(a.seeds)]
    t0 = time.time()
    with Pool(a.workers) as p:
        res = p.map(run, jobs)
    json.dump({'base': a.base, 'arms': {n: d for n, d in arms}, 'days': a.days, 'runs': res}, open(a.out, 'w'), indent=1)
    keys = [k for k in res[0] if k not in ('arm', 'seed') and not k.endswith('_n')]
    print(f'{time.time() - t0:.0f}s')
    print('%-26s' % 'row' + ''.join('%14s' % n for n, _ in arms))
    for k in keys:
        print('%-26s' % k + ''.join('%14.4f' % np.median([r[k] for r in res if r['arm'] == n and r.get(k) is not None and np.isfinite(r[k])] or [np.nan]) for n, _ in arms))
