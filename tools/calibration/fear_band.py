"""Real-market bands for the fear gauge rows, derived from ^VIX against ^GSPC.

The two rows read the median change in the volatility index on sessions
whose index return is at or below a threshold: -1 percent for the first
row and -3 percent for the second. Real side: the change for session d is
the ^VIX close on d minus the close on d-1, paired with d's own close-to-
close return of ^GSPC.

Two derivations, because the buckets differ by an order of magnitude in
how often they fill:

- the panel's shared rule, applied to the -1 percent row: the statistic
  per 252-session window over the reference panel's ten windows, 2015-07
  to 2025-07, the window holding the COVID crash excluded and reported as
  the crisis reading, band = [min - s, max + s] with s the across-window sd
  with the single most extreme window dropped;
- the same rule fails for the -3 percent row, because a calm year holds no
  such session (2017 held none), so its band is taken across every 252-
  session window since 1990 that holds at least five such sessions, with
  the same rule, and the pooled median over the whole series is reported
  beside it, against the +6.03 the engine's own docstring cites from FRED.

Usage:  python tools/calibration/fear_band.py
"""
import math
import os
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shadow"))
import data  # noqa: E402

END = "2026-09-03"
THRESHOLDS = {"dn1": -1.0, "dn3": -3.0, "dn5": -5.0}
PANEL_START = "2015-07-01"
PANEL_WINDOWS = 10
COVID_WINDOW_HOLDS = "2020-03-16"


def series():
    vix = data.fetch("^VIX", "1990-01-01", END)
    spx = data.fetch("^GSPC", "1950-01-01", END)
    v = {r[0]: r[1] for r in vix["rows"]}
    s = {r[0]: (r[3] if len(r) > 3 and r[3] else r[1]) for r in spx["rows"]}
    dates = sorted(set(v) & set(s))
    rows = []
    for prev, cur in zip(dates, dates[1:]):
        ret = (s[cur] / s[prev] - 1.0) * 100.0
        rows.append((cur, ret, v[cur] - v[prev]))
    return rows, vix, spx


def bucket_median(rows, threshold):
    changes = [c for _, r, c in rows if r <= threshold]
    return (statistics.median(changes) if changes else None), len(changes)


def windows_from(rows, start, count, length=252):
    i = next(k for k, (d, _, _) in enumerate(rows) if d >= start)
    out = []
    while len(out) < count and i + length <= len(rows):
        out.append(rows[i:i + length])
        i += length
    return out


def shared_rule(values):
    """[min - s, max + s], s the sd with the most extreme window dropped."""
    med = statistics.median(values)
    trimmed = sorted(values, key=lambda x: abs(x - med))[:-1]
    s = statistics.stdev(trimmed) if len(trimmed) > 1 else 0.0
    return (min(values) - s, max(values) + s, s)


def main():
    rows, vix, spx = series()
    print("sessions %d, %s to %s; ^VIX fetched %s; ^GSPC fetched %s"
          % (len(rows), rows[0][0], rows[-1][0], vix["fetched"], spx["fetched"]))
    print("  %s" % vix["url"])
    print()
    print("pooled over the whole series, median VIX change on sessions at or below the threshold:")
    for name, th in THRESHOLDS.items():
        med, n = bucket_median(rows, th)
        print("  %s (%+.0f pct): median %+.3f over %d sessions" % (name, th, med, n))
    print()
    wins = windows_from(rows, PANEL_START, PANEL_WINDOWS)
    print("the panel's ten 252-session windows from %s:" % PANEL_START)
    per = {k: [] for k in THRESHOLDS}
    crisis = {}
    for w in wins:
        holds_covid = any(d == COVID_WINDOW_HOLDS for d, _, _ in w)
        line = "  %s to %s%s:" % (w[0][0], w[-1][0], "  (crisis window, excluded)" if holds_covid else "")
        for name, th in THRESHOLDS.items():
            med, n = bucket_median(w, th)
            line += "  %s %s over %2d" % (name, ("%+.2f" % med) if med is not None else "  n/a", n)
            if holds_covid:
                crisis[name] = (med, n)
            elif med is not None:
                per[name].append(med)
        print(line)
    print()
    for name in ("dn1", "dn3"):
        vals = per[name]
        if len(vals) >= 3:
            lo, hi, s = shared_rule(vals)
            print("%s by the shared rule over %d non-crisis windows: min %+.2f max %+.2f trimmed sd %.2f -> band (%+.2f, %+.2f); crisis window %s"
                  % (name, len(vals), min(vals), max(vals), s, lo, hi, crisis.get(name)))
        else:
            print("%s: only %d non-crisis windows define it; the shared rule cannot be applied" % (name, len(vals)))
    print()
    # the -3 row across every window since 1990 that holds at least five such sessions
    allw = windows_from(rows, rows[0][0], 10_000)
    meds = []
    for w in allw:
        med, n = bucket_median(w, -3.0)
        if n >= 5:
            meds.append((w[0][0], med, n))
    vals = [m for _, m, _ in meds]
    lo, hi, s = shared_rule(vals)
    print("dn3 across the %d windows since 1990 holding at least five sessions at -3 or worse:" % len(meds))
    for d, m, n in meds:
        print("  from %s: median %+.2f over %d" % (d, m, n))
    print("  min %+.2f max %+.2f trimmed sd %.2f -> band (%+.2f, %+.2f)" % (min(vals), max(vals), s, lo, hi))
    print()
    print("the engine's own citation for the -3 row: +6.03, FRED VIXCLS against SP500, 2,511 common days to 2026-08 (rust/src/economy/state.rs)")


if __name__ == "__main__":
    main()
