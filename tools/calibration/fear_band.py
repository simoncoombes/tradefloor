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

And the -3 percent row's TAPE ERROR, which `facts.REAL_MARKETS_PROVENANCE`
records as `centre_se` and which this tool is the named route to. The
scoring rule's centre for the row is the pooled median over every session
since 1990 at or below -3 percent (107 of them), and its error is a
window-block bootstrap of that median: the 252-session windows are the
blocks, as many are drawn with replacement as there are blocks, their
qualifying sessions are pooled, the median taken, 2,000 draws at seed
20260905 (the repository's bootstrap convention). Two block definitions
are run and both printed:

- ALL windows since 1990 holding at least one qualifying session -- the
  form on the record. Its blocks hold every one of the centre's 107
  sessions, so the bootstrap resamples the centre's own sample and its
  mean sits within a fifth of an sd of the centre.
- the ten windows holding at least five, the BAND's blocks. Those hold 92
  of the 107 and centre the bootstrap 0.40 below the recorded centre: an
  estimator of a slightly different quantity, which is the wrong-ruler
  pattern the row's own provenance forbids. Printed beside the other so
  the choice stays visible, and not recorded.

Until 2026-09-09 the provenance said this tool could run that bootstrap
and it could not: `main()` computed bands and pooled medians and nothing
else. The row was blind in every score for four days on the strength of a
sentence that pointed at code that did not exist.

Usage:  python tools/calibration/fear_band.py
"""
import math
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "shadow"))
import data  # noqa: E402

# The panel's band rule, from the one place that defines it. This tool once
# carried its own copy, median-centred, while the test helper trimmed around
# the mean; the two agreed on every fear band and disagreed elsewhere.
from tradefloor.facts import shared_rule  # noqa: E402

END = "2026-09-03"
THRESHOLDS = {"dn1": -1.0, "dn3": -3.0, "dn5": -5.0}
PANEL_START = "2015-07-01"
PANEL_WINDOWS = 10
COVID_WINDOW_HOLDS = "2020-03-16"
#: The repository's bootstrap convention (`tools/calibration/calibrate.py`,
#: `loss.scoring_rule`): 2,000 draws at seed 20260905.
BOOTSTRAP_DRAWS = 2000
BOOTSTRAP_SEED = 20260905
#: The band's block condition for the -3 row: a window carries a median of
#: its own only with at least this many qualifying sessions. The bootstrap
#: of the POOLED median needs no such floor, and the recorded error uses
#: none (`DN3_ERROR_MIN_SESSIONS`).
DN3_BAND_MIN_SESSIONS = 5
DN3_ERROR_MIN_SESSIONS = 1


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


def dn3_blocks(rows, threshold=-3.0, min_sessions=DN3_ERROR_MIN_SESSIONS,
               length=252):
    """The consecutive `length`-session windows since 1990, each reduced to
    the VIX changes on its sessions at or below `threshold`, keeping the
    windows with at least `min_sessions` of them. `[(start_date, changes)]`
    in tape order; the trailing partial window is dropped by `windows_from`.
    """
    out = []
    for w in windows_from(rows, rows[0][0], 10_000, length):
        changes = [c for _, r, c in w if r <= threshold]
        if len(changes) >= min_sessions:
            out.append((w[0][0], changes))
    return out


def block_bootstrap(blocks, draws=BOOTSTRAP_DRAWS, seed=BOOTSTRAP_SEED):
    """The bootstrap distribution of the pooled median under resampling BLOCKS.

    `blocks` is a list of session lists. Each draw picks `len(blocks)` of
    them with replacement, pools their sessions and takes the median; the
    returned list holds one median per draw, so its sd is the error and its
    mean says where the estimator centres against the recorded centre. The
    block, not the session, is the unit of replication: sessions at -3 per
    cent arrive in clusters (2008, 2020) and a session bootstrap would price
    the error of a median over independent days that the tape does not
    have.
    """
    rng = random.Random(seed)
    k = len(blocks)
    medians = []
    for _ in range(draws):
        pooled = []
        for _ in range(k):
            pooled.extend(rng.choice(blocks))
        medians.append(statistics.median(pooled))
    return medians


def dn3_error(rows, min_sessions=DN3_ERROR_MIN_SESSIONS,
              draws=BOOTSTRAP_DRAWS, seed=BOOTSTRAP_SEED):
    """`fear_gauge_dn3`'s tape error by the window-block bootstrap.

    Returns a dict with the block count, the sessions the blocks hold, the
    pooled median over those sessions, the bootstrap sd (the `centre_se`
    the provenance records), its mean, its 2.5 and 97.5 percentiles, and
    `df` = blocks - 1.
    """
    blocks = [changes for _, changes in dn3_blocks(rows, min_sessions=min_sessions)]
    medians = sorted(block_bootstrap(blocks, draws, seed))
    return {
        "blocks": len(blocks),
        "sessions": sum(len(b) for b in blocks),
        "pooled_median": statistics.median([c for b in blocks for c in b]),
        "se": statistics.stdev(medians),
        "mean": statistics.fmean(medians),
        "p2_5": medians[int(0.025 * len(medians))],
        "p97_5": medians[int(0.975 * len(medians))],
        "df": len(blocks) - 1,
        "draws": draws,
        "seed": seed,
    }


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
    # the -3 row's TAPE ERROR: the window-block bootstrap of the pooled median
    med3, n3 = bucket_median(rows, -3.0)
    print("dn3 tape error, window-block bootstrap of the pooled median (%+.4f over %d sessions), %d draws, seed %d:"
          % (med3, n3, BOOTSTRAP_DRAWS, BOOTSTRAP_SEED))
    for label, floor in (("RECORDED: every 252-session window holding >= %d qualifying session" % DN3_ERROR_MIN_SESSIONS, DN3_ERROR_MIN_SESSIONS),
                         ("for comparison: the band's %d windows holding >= %d" % (len(meds), DN3_BAND_MIN_SESSIONS), DN3_BAND_MIN_SESSIONS)):
        e = dn3_error(rows, min_sessions=floor)
        print("  %s:" % label)
        print("    %d blocks holding %d of the %d sessions, pooled median over them %+.4f"
              % (e["blocks"], e["sessions"], n3, e["pooled_median"]))
        print("    se %.4f  df %d  bootstrap mean %+.4f (%+.2f from the centre)  P2.5 %+.3f  P97.5 %+.3f"
              % (e["se"], e["df"], e["mean"], e["mean"] - med3, e["p2_5"], e["p97_5"]))
    print()
    print("the engine's own citation for the -3 row: +6.03, FRED VIXCLS against SP500, 2,511 common days to 2026-08 (rust/src/economy/state.rs)")


if __name__ == "__main__":
    main()
