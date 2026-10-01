"""The simulated rate indices against real bond markets, 2015-2025.

Two halves. The simulation: `Universe.random(40, seed=111, bonds=True)`
under the default preset for `--days` sessions on each seed, one session a
day, reading at every close the equities' equal-weight index return, the
three rate indices' returns and the yields they were marked at. The real
side: FRED's constant-maturity 2-year and 10-year yields (DGS2, DGS10) and
Yahoo's adjusted daily closes for SPY, SHY, IEF and LQD, the ETFs the three
indices resemble, over 2015-01-01 to 2025-12-31. `--real-data DIR` caches
them there and downloads what is missing.

What it prints: the daily yield-change sd in basis points, each index's
annualised volatility, its daily correlation with the equity index on the
same day and on the day after, and a 60/40 book's volatility, with the real
figure beside each.

    python tools/bonds/realism.py --seeds 1 2 3 4 5 6 7 8 --days 504 \
        --real-data /tmp/bond-data
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import pathlib
import statistics
import struct
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import tradefloor as tf

RATES = ("UST2Y", "UST10Y", "IGCORP")
ETF = {"UST2Y": "SHY", "UST10Y": "IEF", "IGCORP": "LQD"}
SLEEVE = {"UST2Y": 0.10, "UST10Y": 0.20, "IGCORP": 0.10}


def _f64(buf: bytes) -> list[float]:
    return list(struct.unpack("<%dd" % (len(buf) // 8), buf))


def corr(a: list[float], b: list[float]) -> float:
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    return cov / math.sqrt(va * vb) if va > 0 and vb > 0 else float("nan")


def annual(returns: list[float]) -> float:
    return statistics.pstdev(returns) * math.sqrt(252)


def simulate(universe, seed: int, days: int) -> dict:
    engine = tf.Engine(seed=seed, universe=universe)
    n = len(universe) - len(RATES)
    prev = _f64(engine.prices())
    equity, bond, yields = [], {t: [] for t in RATES}, {t: [] for t in RATES}
    for _ in range(days):
        engine.open_market()
        marked = {r["ticker"]: r["yield"] for r in engine.rate_instruments}
        engine.run_session(9, 30, 3, 390)
        engine.close_market()
        now = _f64(engine.prices())
        equity.append(statistics.fmean(now[i] / prev[i] - 1.0 for i in range(n)))
        for j, t in enumerate(RATES):
            bond[t].append(now[n + j] / prev[n + j] - 1.0)
            yields[t].append(marked[t])
        prev = now
    return {"equity": equity, "bond": bond, "yields": yields}


def sim_stats(runs: list[dict]) -> dict:
    out: dict = {t: {} for t in RATES}
    for t in RATES:
        out[t]["dy_bp"] = statistics.median(
            statistics.pstdev([b - a for a, b in zip(r["yields"][t], r["yields"][t][1:])]) * 1e4
            for r in runs)
        out[t]["vol"] = statistics.median(annual(r["bond"][t]) for r in runs)
        out[t]["corr"] = statistics.median(corr(r["equity"], r["bond"][t]) for r in runs)
        out[t]["corr_next"] = statistics.median(
            corr(r["equity"][:-1], r["bond"][t][1:]) for r in runs)
    out["equity_vol"] = statistics.median(annual(r["equity"]) for r in runs)
    out["sixty_forty_vol"] = statistics.median(
        annual([0.6 * e + sum(w * r["bond"][t][k] for t, w in SLEEVE.items())
                for k, e in enumerate(r["equity"])]) for r in runs)
    return out


def _fred(directory: pathlib.Path, series: str) -> dict[str, float]:
    path = directory / f"{series}.csv"
    if not path.exists():
        urllib.request.urlretrieve(
            f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}", path)
    out = {}
    with path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                out[row["observation_date"]] = float(row[series]) / 100.0
            except ValueError:
                continue
    return out


def _yahoo(directory: pathlib.Path, symbol: str) -> dict[str, float]:
    path = directory / f"{symbol}.json"
    if not path.exists():
        start = int(dt.datetime(2014, 12, 1).timestamp())
        end = int(dt.datetime(2026, 1, 5).timestamp())
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
               f"?period1={start}&period2={end}&interval=1d&events=div")
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=60) as response:
            result = json.load(response)["chart"]["result"][0]
        adj = result["indicators"]["adjclose"][0]["adjclose"]
        rows = [(dt.datetime.utcfromtimestamp(t).date().isoformat(), a)
                for t, a in zip(result["timestamp"], adj) if a is not None]
        path.write_text(json.dumps(rows), encoding="utf-8")
    return dict(json.loads(path.read_text(encoding="utf-8")))


def real_stats(directory: pathlib.Path) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    window = lambda d: "2015-01-01" <= d <= "2025-12-31"  # noqa: E731
    out: dict = {t: {} for t in RATES}
    for t, series in (("UST2Y", "DGS2"), ("UST10Y", "DGS10")):
        y = _fred(directory, series)
        days = sorted(d for d in y if window(d))
        out[t]["dy_bp"] = statistics.pstdev(
            [(y[b] - y[a]) * 1e4 for a, b in zip(days, days[1:])])
    closes = {s: _yahoo(directory, s) for s in ("SPY", *ETF.values())}
    days = sorted(set.intersection(*(set(c) for c in closes.values())))
    days = [d for d in days if window(d)]
    ret = {s: [c[b] / c[a] - 1.0 for a, b in zip(days, days[1:])]
           for s, c in closes.items()}
    for t, symbol in ETF.items():
        out[t]["vol"] = annual(ret[symbol])
        out[t]["corr"] = corr(ret["SPY"], ret[symbol])
        out[t]["corr_next"] = corr(ret["SPY"][:-1], ret[symbol][1:])
    out["equity_vol"] = annual(ret["SPY"])
    out["sixty_forty_vol"] = annual(
        [0.6 * e + sum(w * ret[ETF[t]][k] for t, w in SLEEVE.items())
         for k, e in enumerate(ret["SPY"])])
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3, 4])
    parser.add_argument("--days", type=int, default=504)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--real-data", type=pathlib.Path,
                        help="cache directory for FRED and Yahoo data")
    args = parser.parse_args()

    universe = tf.Universe.random(40, seed=111, bonds=True)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        runs = list(pool.map(lambda s: simulate(universe, s, args.days), args.seeds))
    sim = sim_stats(runs)
    real = real_stats(args.real_data) if args.real_data else None

    def pair(key, t=None, scale=1.0, fmt="{:+.3f}"):
        s = (sim[t][key] if t else sim[key]) * scale
        r = None if real is None or (t and key not in real[t]) else (
            real[t][key] if t else real[key]) * scale
        return fmt.format(s), ("-" if r is None else fmt.format(r))

    print(f"model {tf.model_preset()['name']}, Universe.random(40, seed=111, "
          f"bonds=True), {args.days} sessions x seeds {args.seeds} (medians); "
          "real: 2015-2025")
    print(f"{'':34s}{'simulated':>11s}{'real':>11s}")
    for t in RATES:
        rows = [("daily yield change sd, bp", "dy_bp", 1.0, "{:.2f}"),
                ("annualised volatility", "vol", 100.0, "{:.2f}%"),
                ("corr with equities, same day", "corr", 1.0, "{:+.3f}"),
                ("corr with equities, day before", "corr_next", 1.0, "{:+.3f}")]
        for label, key, scale, fmt in rows:
            if key == "dy_bp" and t == "IGCORP" and real is not None:
                s = fmt.format(sim[t][key] * scale)
                print(f"{t + ' ' + label:34s}{s:>11s}{'-':>11s}")
                continue
            s, r = pair(key, t, scale, fmt)
            print(f"{t + ' ' + label:34s}{s:>11s}{r:>11s}")
    for label, key in (("equity index volatility", "equity_vol"),
                       ("60/40 volatility", "sixty_forty_vol")):
        s, r = pair(key, None, 100.0, "{:.2f}%")
        print(f"{label:34s}{s:>11s}{r:>11s}")


if __name__ == "__main__":
    main()
