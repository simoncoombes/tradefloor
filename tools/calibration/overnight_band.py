"""Real-market target for the overnight variance share, from daily bars.

The engine has no overnight process, so its true overnight return is
identically zero. This derives what a real one reads, on the reference
panel's forty names over the ten 253-bar windows `facts.REAL_MARKETS_WINDOWS`
uses, from the Yahoo daily bars `tools/shadow/data.py` fetches with the
open kept beside the close.

Per name and window, with adjusted prices so a split or a dividend is not
counted as a night,

    g = log(open / previous close)          the night
    i = log(close / open)                   the session
    share = var(g) / (var(g) + var(i))      the graded quantity
    ratio = var(g) / var(i)                 reported, never graded

and four quantities the process has to be built to, beside the share:

- concentration: the fraction of a series' variance carried by its largest
  five percent of observations by magnitude, nights against sessions; an
  iid Gaussian reads 0.2791 in closed form;
- composition: the mean pairwise correlation across names of the nights,
  against the sessions';
- reversal: the slope of the session on the same day's night, pooled over
  every name-day of a window, with the median of the per-name slopes
  beside it;
- the index's own share, ^GSPC open against its previous close.

The window statistic is the median across names, which is how the panel
reads a per-name statistic, and the band is the panel's shared rule over
the nine non-crisis windows: [min - s, max + s] with s the across-window
sd with the single most extreme window dropped. The crisis window is
reported beside it and excluded from the band.

Usage:  python tools/calibration/overnight_band.py [--out FILE.json]
"""
import argparse
import json
import math
import os
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shadow"))
import data  # noqa: E402

START, END = "2015-07-01", "2025-08-01"
#: The reference panel's first window opens on this session (REALISM-BANDS.md).
FIRST_WINDOW = "2015-07-10"
WINDOW_BARS = 253
WINDOWS = 10
CRISIS_INDEX = 4
TOP = 0.05
#: The traded proxy for the index's own night; see the note at its fetch.
INDEX_PROXY = "SPY"


def gaussian_concentration(top=TOP):
    """Share of variance in the largest `top` fraction of an iid Gaussian."""
    # z with P(|Z| > z) = top, by bisection; then 2 (z phi(z) + 1 - Phi(z)).
    lo, hi = 0.0, 10.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if math.erfc(mid / math.sqrt(2.0)) > top:
            lo = mid
        else:
            hi = mid
    z = (lo + hi) / 2.0
    phi = math.exp(-z * z / 2.0) / math.sqrt(2.0 * math.pi)
    return 2.0 * (z * phi + 0.5 * math.erfc(z / math.sqrt(2.0)))


def concentration(values, top=TOP):
    """The fraction of sum of squares carried by the top `top` by magnitude."""
    sq = sorted((v * v for v in values), reverse=True)
    total = sum(sq)
    if total <= 0.0:
        return None
    k = max(1, int(math.ceil(top * len(sq))))
    return sum(sq[:k]) / total


def pearson(xs, ys):
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = math.sqrt(sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys))
    return num / den if den > 0.0 else None


def slope(xs, ys):
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    den = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den if den > 0.0 else None


def mean_pairwise_corr(series):
    names = list(series)
    out = []
    for a in range(len(names)):
        for b in range(a + 1, len(names)):
            r = pearson(series[names[a]], series[names[b]])
            if r is not None:
                out.append(r)
    return statistics.fmean(out) if out else None


def adjusted(rows):
    """{date: (adjusted open, adjusted close)} from rows with the open kept.

    Yahoo's adjusted close carries splits and dividends; the same day's
    factor adjusts the open, so a night across an ex-dividend date or a
    split reads as the price move alone.
    """
    out = {}
    for r in rows:
        if len(r) < 5 or r[3] is None or r[4] is None or r[3] <= 0 or r[4] <= 0:
            continue
        factor = r[1] / r[3]
        out[r[0]] = (r[4] * factor, r[1])
    return out


def night_and_session(bars, dates):
    """(g, i) per date from the second date on, given {date: (open, close)}."""
    g, i = [], []
    for prev, cur in zip(dates, dates[1:]):
        o, c = bars[cur]
        _, pc = bars[prev]
        g.append(math.log(o / pc))
        i.append(math.log(c / o))
    return g, i


def shared_rule(values):
    med = statistics.median(values)
    trimmed = sorted(values, key=lambda x: abs(x - med))[:-1]
    s = statistics.stdev(trimmed) if len(trimmed) > 1 else 0.0
    return (min(values) - s, max(values) + s, s)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None, help="write the per-window table as JSON")
    args = ap.parse_args(argv)

    fetched = {t: data.fetch(t, START, END, ohlc=True) for t in data.TICKERS}
    index = data.fetch(data.INDEX, START, END, ohlc=True)
    # The index's published open is not a traded price: at 09:30 most
    # constituents have not printed and the index open carries their prior
    # closes, so ^GSPC's night reads far below any traded series'. SPY, the
    # fund on the same index, opens on an auction and is the traded proxy.
    proxy = data.fetch(INDEX_PROXY, START, END, ohlc=True)
    bars = {t: adjusted(f["rows"]) for t, f in fetched.items()}
    index_bars = adjusted(index["rows"])
    proxy_bars = adjusted(proxy["rows"])
    common = (set.intersection(*(set(b) for b in bars.values()))
              & set(index_bars) & set(proxy_bars))
    dates = sorted(common)
    first = next(k for k, d in enumerate(dates) if d >= FIRST_WINDOW)
    print("forty names and %s, %d common sessions %s to %s; first fetch %s"
          % (data.INDEX, len(dates), dates[0], dates[-1], min(f["fetched"] for f in fetched.values())))
    print("  %s" % index["url"])
    print("iid Gaussian concentration at the top %.0f percent: %.4f" % (TOP * 100, gaussian_concentration()))
    print()

    header = ("window", "share med", "ratio med", "conc night", "conc sess", "xs night", "xs sess",
              "rev pooled", "rev med", "index share", "SPY share")
    print("  %-24s" % header[0] + "".join("%11s" % h for h in header[1:]))
    table = []
    for w in range(WINDOWS):
        lo, hi = first + w * WINDOW_BARS, first + (w + 1) * WINDOW_BARS
        if hi > len(dates):
            break
        wd = dates[lo:hi]
        shares, ratios, conc_g, conc_i, slopes = [], [], [], [], []
        nights, sessions = {}, {}
        pooled_g, pooled_i = [], []
        for t in data.TICKERS:
            g, i = night_and_session(bars[t], wd)
            vg, vi = statistics.pvariance(g), statistics.pvariance(i)
            shares.append(vg / (vg + vi))
            ratios.append(vg / vi)
            conc_g.append(concentration(g))
            conc_i.append(concentration(i))
            slopes.append(slope(g, i))
            nights[t], sessions[t] = g, i
            pooled_g.extend(g)
            pooled_i.extend(i)
        ig, ii = night_and_session(index_bars, wd)
        ivg, ivi = statistics.pvariance(ig), statistics.pvariance(ii)
        pg, pi_ = night_and_session(proxy_bars, wd)
        pvg, pvi = statistics.pvariance(pg), statistics.pvariance(pi_)
        row = {
            "window": "%s..%s" % (wd[0], wd[-1]),
            "crisis": w == CRISIS_INDEX,
            "share_median": statistics.median(shares),
            "ratio_median": statistics.median(ratios),
            "concentration_night_median": statistics.median(conc_g),
            "concentration_session_median": statistics.median(conc_i),
            "xs_corr_night": mean_pairwise_corr(nights),
            "xs_corr_session": mean_pairwise_corr(sessions),
            "reversal_pooled": slope(pooled_g, pooled_i),
            "reversal_median": statistics.median(slopes),
            "index_share": ivg / (ivg + ivi),
            "index_concentration_night": concentration(ig),
            "proxy_share": pvg / (pvg + pvi),
            "proxy_concentration_night": concentration(pg),
            "names": len(shares),
            "sessions": len(wd),
        }
        table.append(row)
        print("  %-24s" % row["window"]
              + "%11.4f" % row["share_median"] + "%11.4f" % row["ratio_median"]
              + "%11.4f" % row["concentration_night_median"] + "%11.4f" % row["concentration_session_median"]
              + "%11.4f" % row["xs_corr_night"] + "%11.4f" % row["xs_corr_session"]
              + "%11.4f" % row["reversal_pooled"] + "%11.4f" % row["reversal_median"]
              + "%11.4f" % row["index_share"] + "%11.4f" % row["proxy_share"]
              + ("   crisis, excluded" if row["crisis"] else ""))
    print()
    calm = [r for r in table if not r["crisis"]]
    crisis = next((r for r in table if r["crisis"]), None)
    bands = {}
    for key in ("share_median", "concentration_night_median", "xs_corr_night",
                "reversal_pooled", "index_share", "proxy_share"):
        vals = [r[key] for r in calm]
        lo, hi, s = shared_rule(vals)
        bands[key] = {"low": lo, "high": hi, "trimmed_sd": s, "min": min(vals), "max": max(vals),
                      "median": statistics.median(vals), "crisis": crisis[key] if crisis else None}
        print("%-30s over %d non-crisis windows: min %.4f max %.4f median %.4f trimmed sd %.4f -> band (%.4f, %.4f); crisis %.4f"
              % (key, len(vals), min(vals), max(vals), statistics.median(vals), s, lo, hi, crisis[key] if crisis else float("nan")))
    print()
    more_conc = sum(1 for r in calm if r["concentration_night_median"] > r["concentration_session_median"])
    index_above = sum(1 for r in calm if r["index_share"] > r["share_median"])
    proxy_above = sum(1 for r in calm if r["proxy_share"] > r["share_median"])
    xs_ratio = [r["xs_corr_night"] / r["xs_corr_session"] for r in calm]
    neg = sum(1 for r in calm if -0.15 <= r["reversal_pooled"] <= 0.0)
    print("nights more concentrated than sessions in %d of %d non-crisis windows" % (more_conc, len(calm)))
    print("index share above the single-name median in %d of %d; %s share above it in %d of %d"
          % (index_above, len(calm), INDEX_PROXY, proxy_above, len(calm)))
    print("cross-sectional correlation, night over session: min %.3f max %.3f" % (min(xs_ratio), max(xs_ratio)))
    print("pooled reversal slope in [-0.15, 0.0] in %d of %d" % (neg, len(calm)))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({"start": START, "end": END, "first_window": FIRST_WINDOW,
                       "window_bars": WINDOW_BARS, "crisis_index": CRISIS_INDEX,
                       "gaussian_concentration": gaussian_concentration(),
                       "fetched": {t: f_["fetched"] for t, f_ in fetched.items()},
                       "index_fetched": index["fetched"], "index_url": index["url"],
                       "proxy": INDEX_PROXY, "proxy_fetched": proxy["fetched"],
                       "windows": table, "bands": bands}, f, indent=1)
        print("wrote %s" % args.out)


if __name__ == "__main__":
    main()
