"""The real-market band for the panel's index return row, derived from data.

The row `index_drift_pct` in `tradefloor.facts` reports the annualised
return of a daily-rebalanced equal-weight portfolio of the roster, in
percent a year, a price return. A band for it needs the long-run price
return of a real equal-weight large-cap index, and no single series gives
that with a small error: an annual return has a standard deviation near 17
points, so even 75 years of it puts the mean inside about two points.

Two series through `tools/shadow/data.py`, cached beside their URL and
fetch date:

- `^GSPC` from 1950, the cap-weighted S&P 500 price index, 75 years. Its
  adjusted and unadjusted closes are identical, since an index pays no
  dividend. This gives the cap-weighted long-run price return.
- `RSP` from 2003 and `^SPXEW` from 2006, the equal-weight S&P 500 fund
  and the index it tracks, read on the UNADJUSTED close so that no
  dividend enters, against `^GSPC` over the same sessions. The difference
  of two series that move together has a much smaller error than either,
  and it is the equal-weight premium. The fund carries an expense ratio
  of about 0.20 percent a year that the index does not, and both
  rebalance quarterly where the panel's row rebalances daily, so the
  premium read off them understates the daily-rebalanced one by a small
  amount that is stated rather than corrected.

The centre is the long-run cap-weighted return plus the premium, each
with a standard error from calendar-year returns, and the band is the
centre plus or minus the larger of two standard errors of the centre and
the model's own resolution at thirty seeds, two standard errors of a
thirty-seed mean at the row's measured seed standard deviation.

Usage:  python tools/calibration/index_band.py [--model-sd 6.5]
"""
import argparse
import math
import os
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shadow"))
import data  # noqa: E402

END = "2026-09-03"
SERIES = {"^GSPC": "1950-01-01", "RSP": "2003-04-01", "^SPXEW": "1990-01-01"}


def closes(symbol, start):
    d = data.fetch(symbol, start, END)
    rows = [r for r in d["rows"] if len(r) > 3 and r[3] is not None and r[3] > 0]
    return {r[0]: r[3] for r in rows}, d["url"], d["fetched"]


def calendar_year_log_returns(series, dates):
    """log(last close of year / last close of the previous year), by year."""
    by_year = {}
    for day in dates:
        by_year[day[:4]] = day
    years = sorted(by_year)
    out = {}
    for prev, cur in zip(years, years[1:]):
        out[cur] = math.log(series[by_year[cur]] / series[by_year[prev]])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-sd", type=float, default=6.5,
                    help="the row's seed sd in points a year, for the resolution term")
    ap.add_argument("--seeds", type=int, default=30)
    args = ap.parse_args()

    gspc, gspc_url, gspc_when = closes("^GSPC", SERIES["^GSPC"])
    dates = sorted(gspc)
    years = (len(dates) - 1) / 252.0
    total = math.log(gspc[dates[-1]] / gspc[dates[0]])
    cw = 100.0 * total / ((int(dates[-1][:4]) - int(dates[0][:4])) + (int(dates[-1][5:7]) - int(dates[0][5:7])) / 12.0)
    yearly = calendar_year_log_returns(gspc, dates)
    full_years = [y for y in yearly if y != dates[-1][:4]]
    vals = [100.0 * yearly[y] for y in full_years]
    cw_mean = statistics.fmean(vals)
    cw_sd = statistics.stdev(vals)
    cw_se = cw_sd / math.sqrt(len(vals))
    print("cap-weighted price return, ^GSPC unadjusted close")
    print("  %s to %s, %s -> %s" % (dates[0], dates[-1], gspc[dates[0]], gspc[dates[-1]]))
    print("  geometric, whole span: %+.2f percent a year" % cw)
    print("  mean of %d calendar-year log returns %s to %s: %+.2f, sd %.2f, se %.2f"
          % (len(vals), full_years[0], full_years[-1], cw_mean, cw_sd, cw_se))
    print("  url %s  fetched %s" % (gspc_url, gspc_when))

    premiums = {}
    for sym in ("RSP", "^SPXEW"):
        ew, url, when = closes(sym, SERIES[sym])
        common = sorted(set(ew) & set(gspc))
        y_ew = calendar_year_log_returns(ew, common)
        y_cw = calendar_year_log_returns(gspc, common)
        yrs = [y for y in y_ew if y in y_cw and y != common[-1][:4] and y != common[0][:4]]
        diffs = [100.0 * (y_ew[y] - y_cw[y]) for y in yrs]
        span_ew = 100.0 * math.log(ew[common[-1]] / ew[common[0]]) / (len(common) - 1) * 252
        span_cw = 100.0 * math.log(gspc[common[-1]] / gspc[common[0]]) / (len(common) - 1) * 252
        prem_mean = statistics.fmean(diffs)
        prem_se = statistics.stdev(diffs) / math.sqrt(len(diffs))
        premiums[sym] = (prem_mean, prem_se, len(diffs))
        print("equal-weight premium, %s unadjusted close against ^GSPC on %d common sessions" % (sym, len(common)))
        print("  %s to %s: %s %+.2f a year, ^GSPC %+.2f, difference %+.2f over the span"
              % (common[0], common[-1], sym, span_ew, span_cw, span_ew - span_cw))
        print("  mean of %d full calendar-year differences %s to %s: %+.2f, sd %.2f, se %.2f"
              % (len(diffs), yrs[0], yrs[-1], prem_mean, statistics.stdev(diffs), prem_se))
        print("  url %s  fetched %s" % (url, when))

    prem, prem_se, n = premiums["RSP"]
    centre = cw_mean + prem
    centre_se = math.sqrt(cw_se ** 2 + prem_se ** 2)
    resolution = 2.0 * args.model_sd / math.sqrt(args.seeds)
    half = max(2.0 * centre_se, resolution)
    print()
    print("centre: cap-weighted %+.2f plus the RSP premium %+.2f = %+.2f, se %.2f" % (cw_mean, prem, centre, centre_se))
    print("width: the larger of two centre standard errors %.2f and the model's resolution %.2f "
          "(two standard errors of a %d-seed mean at a seed sd of %.1f)"
          % (2.0 * centre_se, resolution, args.seeds, args.model_sd))
    print("band: (%.1f, %.1f), percent a year, daily-rebalanced equal-weight price return" % (centre - half, centre + half))
    print("cross-check with ^SPXEW: premium %+.2f se %.2f over %d years, centre %+.2f"
          % (premiums["^SPXEW"][0], premiums["^SPXEW"][1], premiums["^SPXEW"][2], cw_mean + premiums["^SPXEW"][0]))


if __name__ == "__main__":
    main()
