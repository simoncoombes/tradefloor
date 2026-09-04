"""Real daily bars for the shadow run, from the fear-gap source.

The same source the fear-gap targets and the realism reference panel were
built from: the Yahoo Finance v8 chart API, daily bars, adjusted close.
Fetched on demand into ``tools/shadow/data/`` (ignored by git; vendor data
is not committed) and read from there afterwards, with the fetch time and
the URL template recorded beside the bars so a report can say where its
numbers came from.

The roster is the reference panel's forty large names with the sector map
that panel states from general knowledge, mapped onto the engine's own
sector names. The index is ^GSPC and the fear index ^VIX.
"""
from __future__ import annotations

import calendar
import datetime
import json
import os
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "data")
URL = ("https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
       "?period1={p1}&period2={p2}&interval=1d&events=split")
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36"}

TICKERS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "JPM", "BAC", "WFC",
    "GS", "C", "V", "MA", "XOM", "CVX", "COP", "JNJ", "PFE", "MRK", "UNH",
    "LLY", "ABT", "PG", "KO", "PEP", "WMT", "COST", "HD", "MCD", "NKE",
    "DIS", "CMCSA", "T", "VZ", "BA", "CAT", "GE", "HON", "UPS", "UNP",
]
#: The reference panel's labels, then the engine's sector for each.
PANEL_SECTOR = {
    "AAPL": "tech", "MSFT": "tech", "GOOGL": "comm", "AMZN": "cons_disc",
    "META": "comm", "NVDA": "tech", "JPM": "fin", "BAC": "fin", "WFC": "fin",
    "GS": "fin", "C": "fin", "V": "fin", "MA": "fin", "XOM": "energy",
    "CVX": "energy", "COP": "energy", "JNJ": "health", "PFE": "health",
    "MRK": "health", "UNH": "health", "LLY": "health", "ABT": "health",
    "PG": "staples", "KO": "staples", "PEP": "staples", "WMT": "staples",
    "COST": "staples", "HD": "cons_disc", "MCD": "cons_disc",
    "NKE": "cons_disc", "DIS": "comm", "CMCSA": "comm", "T": "comm",
    "VZ": "comm", "BA": "indus", "CAT": "indus", "GE": "indus",
    "HON": "indus", "UPS": "indus", "UNP": "indus",
}
ENGINE_SECTOR = {
    "tech": "technology", "comm": "telecommunications",
    "cons_disc": "consumer_discretionary", "fin": "financial_services",
    "energy": "energy", "health": "healthcare", "staples": "consumer_staples",
    "indus": "industrials",
}
INDEX, VIX = "^GSPC", "^VIX"
#: The two years the brief names: one calm, one crisis. Each window starts
#: fifteen months earlier, so the beta estimate has 252 prior sessions and
#: the starting price is the close before the first session.
YEARS = {"calm": ("2015-10-01", "2018-01-01"),
         "crisis": ("2018-10-01", "2021-01-01")}


def _utc_date(t: int) -> str:
    """The UTC calendar date of a Unix timestamp, for any sign of ``t``."""
    return (datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=int(t))).strftime("%Y-%m-%d")


def epoch(day: str) -> int:
    """Midnight UTC on ``day``, as a Unix timestamp.

    In UTC, because the value goes into the url a report prints as its
    provenance, and `time.mktime` reads midnight in the machine's own
    zone. A box in UTC and a machine four hours west of it asked for
    windows four hours apart and Yahoo answered with the same sessions at
    a different float rounding: one Visa close came back 67.53675 on one
    and 67.53683 on the other, 1.13e-06 relative, which reaches the
    recompute through the universe it builds and the observed returns it
    differences.
    """
    return calendar.timegm(time.strptime(day, "%Y-%m-%d"))


def fetch(symbol: str, start: str, end: str) -> dict:
    """Daily bars for ``symbol`` between two dates, cached on disk.

    Returns ``{"symbol", "url", "fetched", "rows": [[date, close, volume,
    unadjusted]]}`` with the adjusted close second, which Yahoo adjusts for
    dividends as well as splits, and the unadjusted close fourth, which is
    the price series a price index band is stated in. Rows with a missing
    or non-positive close are dropped, and volume is kept where reported
    (an index reports none). A cache written before the fourth column
    existed carries three-element rows, so a reader of the unadjusted
    close checks the row length rather than assuming it.
    """
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f"{symbol.strip('^')}_{start}_{end}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    url = URL.format(symbol=symbol, p1=epoch(start), p2=epoch(end))
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.load(r)
    res = d["chart"]["result"][0]
    ts = res["timestamp"]
    quote = res["indicators"]["quote"][0]
    adj = res["indicators"]["adjclose"][0]["adjclose"]
    vol = quote.get("volume") or [None] * len(ts)
    raw = quote.get("close") or [None] * len(ts)
    rows = []
    for t, a, v, c in zip(ts, adj, vol, raw):
        if a is None or a <= 0:
            continue
        # Not time.gmtime: on Windows it refuses a negative timestamp, and
        # a session before 1970 is one, so a long index history could not be
        # fetched there. The arithmetic form gives the same UTC date.
        rows.append([_utc_date(t), float(a),
                     None if v is None else float(v),
                     None if c is None else float(c)])
    rows.sort()
    out = {"symbol": symbol, "url": url,
           "fetched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "rows": rows}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f)
    return out


def load(which: str) -> dict:
    """Everything the shadow run needs for one of :data:`YEARS`.

    Bars for the forty names, the index and the VIX over the two-year
    window, aligned on the sessions every name reports. Returns the dates
    in order, ``closes[ticker][i]``, ``volumes[ticker][i]``, the index
    closes, the VIX closes and the provenance of each series.
    """
    start, end = YEARS[which]
    series = {t: fetch(t, start, end) for t in TICKERS}
    index = fetch(INDEX, start, end)
    vix = fetch(VIX, start, end)
    common = set.intersection(*(set(r[0] for r in s["rows"])
                                for s in series.values()))
    common &= set(r[0] for r in index["rows"])
    common &= set(r[0] for r in vix["rows"])
    dates = sorted(common)
    where = {d: i for i, d in enumerate(dates)}

    def column(rows, k):
        out = [None] * len(dates)
        for r in rows:
            i = where.get(r[0])
            if i is not None:
                out[i] = r[k]
        return out

    return {
        "which": which, "start": start, "end": end, "dates": dates,
        "closes": {t: column(s["rows"], 1) for t, s in series.items()},
        "volumes": {t: column(s["rows"], 2) for t, s in series.items()},
        "index": column(index["rows"], 1),
        "vix": column(vix["rows"], 1),
        "provenance": {
            "source": "Yahoo Finance v8 chart API, daily bars, adjusted close",
            "url_template": URL,
            "fetched": {s["symbol"]: s["fetched"]
                        for s in [*series.values(), index, vix]},
            "sessions": len(dates),
        },
    }


def year_slice(data: dict, year: str) -> tuple[int, int]:
    """The index range ``[first, last)`` of sessions in ``year``."""
    dates = data["dates"]
    first = next(i for i, d in enumerate(dates) if d.startswith(year))
    last = next((i for i, d in enumerate(dates)
                 if d > f"{year}-12-31"), len(dates))
    return first, last
