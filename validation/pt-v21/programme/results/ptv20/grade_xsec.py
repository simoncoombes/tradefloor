"""The registered rows C4a and C5-C8 for arms, by the protocol registered in
programme/ptv20-registration.md and the bands in bands.json (both committed
before any box read them).

    python grade_xsec.py OUT.json --arm NAME[@BASE]:dial=v,... [--arm ...]
        [--seeds 101-130] [--days 2660] [--workers N]

Per arm:
  - 30 untraded histories of 2660 sessions (the certified forty's length) on
    the certified roster Universe.random(40, seed=111), seeds 101-130: C4a on
    each history's first 252 sessions, C5, C6 (whole history), C7 and C8 on
    the whole history; the row is the median over the histories;
  - C6's opening window: the value IC over days 0-60 on the 30 histories and
    on the twenty suite markets (seeds 92001-92020, Universe.random(20,
    seed=93001-93020), untraded, 81 sessions), the mean over the fifty.
"""
import sys, json, argparse, pathlib, time
import numpy as np
from multiprocessing import Pool
HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))
import desk, xsec

def parse_arm(text, default_base='pt-v19'):
    head, _, rest = text.partition(':')
    name, _, base = head.strip().partition('@')
    dials = {}
    for part in filter(None, rest.split(',')):
        k, _, v = part.partition('=')
        dials[k.strip()] = float(v)
    return name.strip(), (base.strip() or default_base), dials

def opening(job):
    base, name, dials, seed, useed = job
    import tradefloor as tf
    u = tf.Universe.random(20, seed=useed)
    e = tf.Engine(seed=seed, universe=u, model=desk.model_for(base, dials))
    carries = tf.ModelParams.from_preset(base, **dials).to_dict()
    carries = carries['fair_value_news_share'] or carries['opening_mispricing_sigma'] or carries['opening_market_sigma']
    D = 491; close = np.zeros((D, 20)); val = np.zeros((D, 20))
    for d in range(D):
        e.open_market(); e.run_session(9, 30, 3, 390)
        close[d] = desk.f64(e.prices())
        s = desk.f64(e.column('mispricing_s'))
        v = desk.f64(e.state_snapshot()['fair_value_offset']) if carries else 0.0
        val[d] = -(s + v)
        e.close_market()
    ic = xsec.ic_series(val[:81], np.log(close[:81]), 0, 5)
    sh = np.array([i.shares_outstanding for i in u])
    li = np.log((close * sh).sum(1))
    return {'arm': name, 'seed': seed, 'value_ic_0_60': float(np.nanmean(ic)),
            'b9_first60': float(li[60] - li[0]),
            'b9_steady60': [float(li[a + 60] - li[a]) for a in (250, 310, 370, 430)]}


def b9a(longrun_dir, arm):
    """Annual index returns of the long run's free histories: non-overlapping
    252-session blocks 1-20 (years 2-21) of the cap-weighted roster index,
    log returns, pooled over seeds; their sd in per cent."""
    import glob
    out = []
    for f in sorted(glob.glob(str(pathlib.Path(longrun_dir) / arm / '*-free.npz'))):
        lv = np.log(np.load(f)['level'])
        out += [float(lv[252 * (k + 1)] - lv[252 * k]) for k in range(1, len(lv) // 252 - 1)]
    return (float(np.std(out, ddof=1) * 100), len(out)) if out else (None, 0)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('out'); ap.add_argument('--arm', action='append', required=True)
    ap.add_argument('--seeds', default='101-130'); ap.add_argument('--days', type=int, default=2660)
    ap.add_argument('--workers', type=int, default=3)
    ap.add_argument('--longrun', help="the long run's output directory (ARM/SEED-free.npz), for B9's annual spread")
    a = ap.parse_args()
    arms = [parse_arm(t) for t in a.arm]
    seeds = []
    for part in a.seeds.split(','):
        lo, _, hi = part.partition('-')
        seeds += list(range(int(lo), int(hi or lo) + 1))
    jobs = [(b, n, d, s, a.days, 40, 111) for n, b, d in arms for s in seeds]
    ojobs = [(b, n, d, 92000 + i, 93000 + i) for n, b, d in arms for i in range(1, 21)]
    t0 = time.time()
    with Pool(a.workers) as p:
        hist = p.map(desk.run, jobs)
        opn = p.map(opening, ojobs)
    bands = json.load(open(HERE / 'bands.json'))
    out = {'bands': bands, 'seeds': a.seeds, 'days': a.days, 'arms': {}, 'histories': hist, 'opening': opn}
    for n, b, d in arms:
        H = [h for h in hist if h['arm'] == n]
        med = lambda k: float(np.median([h[k] for h in H]))
        o = {'base': b, 'dials': d,
             'C4a_acf1_65m': med('c4a_acf1_65m'), 'C4a_roll_over_quoted': med('c4a_roll_over_quoted'),
             'C5_vr60': med('vr60'), 'C6_value_ic': med('value_ic'),
             'C6_value_ic_0_60': float(np.mean([h['value_ic_0_60'] for h in H] + [x['value_ic_0_60'] for x in opn if x['arm'] == n])),
             'C7_mom12_1': med('mom12_1'), 'C7_mom6_1': med('mom6_1'), 'C8_lm1_bps': med('lm1_bps'),
             'reported': {k: med(k) for k in ('vr20', 'vr120', 'vr250', 'idio_acf1_daily', 'acf1_daily',
                                             's_xsd_stationary', 'share_var_dv', 'share_var_ds', 'lm1_mid_bps',
                                             'close_gap_sd_bps', 'herfindahl_end', 'ig_yield_dy_sd_bp',
                                             'sixty_forty_vol_pct') if k in H[0]}}
        # B9: the spread of annual returns (long run) and the start-up drift
        first = [h['b9_first60'] for h in H] + [x['b9_first60'] for x in opn if x['arm'] == n]
        steady = [r for h in H for r in h['b9_steady60']] + [r for x in opn if x['arm'] == n for r in x['b9_steady60']]
        o['B9_first60_sd_pct'] = float(np.std(first, ddof=1) * 100)
        o['B9_steady60_sd_pct'] = float(np.std(steady, ddof=1) * 100)
        o['B9_startup_ratio'] = o['B9_first60_sd_pct'] / o['B9_steady60_sd_pct']
        o['B9_first60_mean_pct'] = float(np.mean(first) * 100)
        o['B9_annual_sd_pct'], o['B9_annual_n'] = b9a(a.longrun, n) if a.longrun else (None, 0)
        # each seed set's figure beside the pooled one (third registration)
        sets = {}
        for h in H:
            key = '%d-%d' % (h['seed'] // 100 * 100 + 1, h['seed'] // 100 * 100 + 30)
            sets.setdefault(key, []).append(h)
        o['by_set'] = {k: {'C5_vr60': float(np.median([h['vr60'] for h in hs])),
                           'C6_value_ic': float(np.median([h['value_ic'] for h in hs])),
                           'C7_mom12_1': float(np.median([h['mom12_1'] for h in hs])),
                           'C8_lm1_bps': float(np.median([h['lm1_bps'] for h in hs])),
                           'R3_corr_stock_tsy': float(np.median([h['r3_corr_stock_tsy'] for h in hs]))}
                       for k, hs in sorted(sets.items())}
        v = {}
        B = bands['rows']
        inb = lambda x, r: B[r]['band'][0] <= x <= B[r]['band'][1]
        v['C4a'] = bool(o['C4a_acf1_65m'] >= B['C4a']['acf1_floor'] or o['C4a_roll_over_quoted'] <= B['C4a']['roll_ceiling'])
        v['C5'] = inb(o['C5_vr60'], 'C5')
        v['C6'] = inb(o['C6_value_ic'], 'C6') and inb(o['C6_value_ic_0_60'], 'C6')
        v['C7'] = inb(o['C7_mom12_1'], 'C7_12_1') and inb(o['C7_mom6_1'], 'C7_6_1')
        v['C8'] = inb(o['C8_lm1_bps'], 'C8')
        for k in ('r1_dy2_sd_bp', 'r2_dy10_sd_bp', 'r3_corr_stock_tsy', 'r4_corr_stock_corp'):
            o[k.upper()[:2] + '_' + k[3:]] = med(k)
        allf = [f for h in H for f in h.get('e1_earnings_falls', [])]
        o['E1_earnings_fall_median'] = float(np.median(allf)) if allf else 0.0
        o['E1_contractions'] = len(allf)
        v['E1'] = inb(o['E1_earnings_fall_median'], 'E1')
        v['R1'] = inb(o['R1_dy2_sd_bp'], 'R1')
        v['R2'] = inb(o['R2_dy10_sd_bp'], 'R2')
        v['R3'] = inb(o['R3_corr_stock_tsy'], 'R3')
        v['R4'] = inb(o['R4_corr_stock_corp'], 'R4')
        v['B9'] = (None if o['B9_annual_sd_pct'] is None else
                   bool(inb(o['B9_annual_sd_pct'], 'B9_annual') and inb(o['B9_startup_ratio'], 'B9_startup')))
        o['verdict'] = v
        out['arms'][n] = o
        print(f"{n:10s} " + "  ".join(f"{k} {'pass' if x else 'FAIL'}" for k, x in v.items()))
        print('           ' + json.dumps({k: (round(x, 4) if isinstance(x, float) else x) for k, x in o.items() if k[:2] in ('C4', 'C5', 'C6', 'C7', 'C8', 'B9', 'R1', 'R2', 'R3', 'R4', 'E1')}))
    json.dump(out, open(a.out, 'w'), indent=1)
    print(f'{time.time() - t0:.0f}s')

if __name__ == '__main__':
    main()
