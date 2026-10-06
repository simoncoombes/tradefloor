# pt-v20: the rows it is graded on, registered before any box ran (2026-09-24)

pt-v20 is pt-v19 with the market-behaviour faults of
`programme/meanrev-edge-ptv19-2026-09-24.md` fixed: both rounds, the tape
and the structural cross-section, in one preset. This note fixes what it is
graded on and the bands, before the grading box runs. The bands are also in
`results/ptv20/bands.json`, which the grader reads.

The owner widened pt-v20 on 2026-09-24 to include a deeper order book (E4)
and bonds (E5). Their rows (impact against size, stock-bond correlation,
bond volatility) will be registered in a second note before the box that
grades them. Nothing below changes when they arrive.

## What is graded

1. The fifteen adopted criteria (`longrun/CRITERIA.md`, A1-D1), unchanged.
2. C4a and C4b, as E2 added them (`longrun/CRITERIA.md` C4, `c4.py`,
   `criteria.py --c4`, branch `criteria/c4-price-only-edge` at e16b8773).
3. Four new rows, C5 to C8, below, and B9, added the same day at the main
   session's request (section B9), before any box ran. The C family ("no illusions a bot could
   learn") is where they belong: each is an edge a strategy researcher would
   test, and the model gave the wrong answer on every one.

## The new rows

The real panel is the certified forty (`corpus/civ/real_closes.json`, Yahoo
adjusted closes, 2015-01-02 to 2025-07-31, 2,660 sessions). One estimator for
both sides: `results/ptv20/xsec.py`. Real values: `real_rows.py` and
`real_value_ic.py`, outputs beside them.

**C5, the stock-level variance ratio.** Idiosyncratic daily log returns
(each name's return less the equal-weight cross-sectional mean), summed over
non-overlapping 60-session windows; per name, the variance of the sums over
60 times the daily variance; the median name. Real **0.924**, calendar-year
block bootstrap se 0.064 (B = 200). **Band 0.80 to 1.05** (real plus or
minus 2 se, rounded outward). pt-v19 reads about 0.59 on this protocol
(desk, below): a stock-specific move is two-fifths gone in 60 sessions.

**C6, the value signal.** At every fifth session, the rank correlation
across names of log(fair value from published fundamentals and rates /
price) with the next 20 sessions' log return; the mean over dates.
- Real: the model's own valuation function (`tradefloor.fair_value`,
  pt-v19's neutral rate 0.0482) applied to the forty's point-in-time
  reported fundamentals: trailing four-quarter diluted EPS, book value per
  share and annual revenue growth, each the first-reported value of its
  period usable from its filing date, per-share figures in today's share
  units, from SEC XBRL (companyfacts, frozen in
  `results/ptv20/edgar/facts.json.gz`), with Moody's Baa and the effective
  fed funds rate from FRED as of each date, against Yahoo's split-adjusted,
  not dividend-adjusted, closes. 39 names (Visa reports no diluted EPS in
  the tagged form). **+0.009** every fifth session, +0.014 monthly
  (bootstrap se 0.016, iid 0.018). Cross-checks on the same data: earnings
  yield -0.029, book-to-price -0.045 (value lost among these names over
  these years).
- Published long-run value ICs at monthly horizons are small, of order 0.02
  to 0.05. That is recalled, not measured, as the investigation recorded it.
- **Band -0.03 to +0.05** (centre +0.01, half-width 0.04, about 2.3 se; the
  upper end is the top of the published range).
- Two readings, both must be in: the whole-history mean, and the mean over
  a market's first 60 sessions (the hosted suites run 60-day markets).
- pt-v19: +0.40 and +0.73.

**C7, 6-12 month momentum.** Monthly, the rank correlation of the 12-1
return (252 sessions skipping the last 21) and of the 6-1 return (126
skipping 21) with the next 20 sessions' return; the mean over months.
- Real 12-1 **+0.027** (bootstrap se 0.034, iid 0.029), 6-1 **+0.041**
  (bootstrap 0.021, iid 0.031).
- **Bands: 12-1 -0.04 to +0.095; 6-1 -0.02 to +0.10** (real plus or minus 2
  of the larger se, rounded outward). Both must be in.
- pt-v19: -0.17 and -0.16.

**C8, the daily loser-minus-winner spread.** Lo-MacKinlay one-day contrarian
book: weights minus each name's return less the cross-sectional mean,
scaled to $1 a side, earning the next session; the mean, bps a day.
- Real **-1.74** (iid se 2.34, bootstrap 2.17).
- **Band -6.4 to +2.9** (real plus or minus 2 se).
- pt-v19 reads about +10 on this protocol: two errors that do not cancel
  here. The model price has a daily momentum (the stop ladders and herding)
  and the stale tape and the last-minute print add a larger reversal.

**The model's protocol**, one for all four:
- 30 untraded histories of 2,660 sessions, the real panel's length, so the
  small-sample bias of a 60-session variance ratio is the same on both sides.
- The certified roster, `Universe.random(40, seed=111)`, seeds 101-130. The
  close is `prices()` after each session: the last print, or under a closing
  cross the cross.
- The row is the median over histories. C6's first-60-session reading is the
  mean over those 30 histories and the 20 suite markets (seeds 92001-92020,
  `Universe.random(20, seed=93001-93020)`, untraded, the first 81 of 491
  sessions).
- The model's value signal is -(s + v): the log of fair value on the
  published fundamentals over the model price. Under a closing cross that is
  the close; pt-v19's close differs from it by its tape gap, which does not
  change the ranking.
- Graded by `results/ptv20/grade_xsec.py`.

Reported beside, not graded: the variance ratio at 20, 120 and 250 sessions
(real 0.94, 0.80, 0.73), the daily idiosyncratic autocorrelation, the
stationary spread of s, the share of idiosyncratic variance in fair value,
the same rows on the roster's names of at least $10B, and the 65-minute
rows on every name.

## B9, the index's returns: their spread and the start-up drift

Added at the main session's request after W17's crash replays and a
reviewer's reading of a calm market drifting +13 per cent in 85 days (a
recession cell +39 per cent). B8 already grades the long-run mean. Nothing
graded the spread of annual returns or a drift at the start.

- **Annual spread.** Non-overlapping 252-session blocks, years 2-21 of the
  long run's 30 free histories (the cap-weighted certified roster index, as
  B7 reads it), log returns pooled over seeds; their sd. Tape: the S&P 500's
  calendar-year log returns, 1990-2024, sd **17.4** (35 years, bootstrap se
  2.8; median +12.0, 10th and 90th percentiles -12.7 and +24.7).
  **Within 20 per cent: 13.9 to 20.9**, the tolerance B7 gives daily
  volatility.
- **Start-up drift.** Across 50 markets (the 30 certified-roster histories
  of C5-C8 and the 20 suite markets, untraded, 491 sessions), the sd of the
  index's log return over the first 60 sessions, over the sd of the same
  markets' 60-session returns in sessions 250-490 (four windows each).
  **2/3x to 1.5x**, as C1 grades the crash rate's steadiness: the first
  months must look like any other months of the same markets.
- Both must pass. Graded by `grade_xsec.py --longrun` and `criteria.py`
  (row B9, after B8).

What the desk already shows, so it is registered and not discovered: on 6
seeds pt-v19's annual spread is **11.4** at a daily volatility of 19.1. The
index reverts at the one-year horizon (a variance ratio near 0.36 against
the S&P's 0.92), because every market shock sits in the mispricing and is
pulled back on its 60-day half-life. The same pull is why a 20 per cent bear
market recovers in 158 sessions against the tape's 670, and why a replayed
crisis leaves the index about a third as far down as the real one at the
window's end (W17). pt-v20 as composed keeps that pull. So:

P6. pt-v20 (by name) FAILS B9's annual spread (desk 12.9 on 6 seeds) and
passes its start-up drift (the market-level opening is now a stationary draw:
suite universe 93010 moves -3.7 per cent in 85 sessions against pt-v19's
+28.3). pt-v19 fails both.

**The owner's decision this box informs.** A market-wide permanent share,
`fair_value_market_share` (the name's loading on the market draw, market
news and the market jump move fair value for good), is built and inert, and
the box carries it as two arms. Measured, not assumed:
- `pt-v20-m05` is 0.5, with `opening_market_sigma` 0.046;
- `pt-v20-m1` is 1.0, with 0.001, the stationary spread at that share
  (desk seeds 201-203: 0.099, 0.046, 0.001 at 0, 0.5 and 1).

It is NOT part of pt-v20 as registered. It rewrites the index's variance
structure that the B rows and the VIX loop were calibrated on (B4 sat at
1.85x of its 2x edge). Whether pt-v20 takes it is the owner's call, on these
arms' rows, and if it does, pt-v20 is recomposed and graded by name again.

## Why the whole roster, and not its large names only

The real panel is forty mega caps; the certified roster runs from $0.3B to
$1.3T. On the desk, before the closing cross, the roster's small names
showed a daily reversal the large ones did not (Lo-MacKinlay +30 against +1,
closes on seeds 201-203), from the last minute's print sitting 45 bp off
the model price. That is a close that a real exchange's closing auction does
not have, and pt-v20 fixes it (`closing_auction`), after which the two
agree within noise. So the rows are graded on the whole roster. The large
names are reported beside them. This choice was made after those desk runs
and is stated as such.

## What pt-v20 is, as registered

pt-v19 plus seven dials. Every one is 0.0 or 1.0 on the presets before it,
so the known-answer digests do not move (sim 1e683b96, checked):

- `quote_model_weight` 1.0: the maker's book is centred on the model price.
- `closing_auction` 1.0: the 15:59 tick prints at the model price.
- `fair_value_news_share` 1.0: every stock- and sector-specific shock moves
  fair value for good. That covers the idiosyncratic and sector noise
  draws, the company's own, peer and sector news, and its own jump; only
  market-wide shocks stay in the mispricing. There is an Ito term. The
  published fundamentals do not move.
- `opening_mispricing_sigma` 0.016: the opening s is the roster's
  cap-weighted day-zero premium plus a stationary-sized draw. 0.016 is the
  measured stationary cross-sectional sd of s under this vector (desk seeds
  201-206, 0.016 at cascade 0.2 to 0.4).
- `opening_market_sigma` 0.10: the index's opening mispricing is a draw
  from its own stationary spread (0.099, desk seeds 201-203), not the
  roster's cap-weighted premium. That premium is +0.11 on the certified
  roster and -0.33 to +0.30 on the suite's 20-name rosters, which is where a
  calm market's +13 to +39 per cent in 85 days came from.
- `cascade_gain` 0.1: the stop and squeeze ladders at a tenth. Measured on
  seeds 201-206: the daily Lo-MacKinlay reading moves about -8.5 bp per unit
  of gain from about -0.7 at zero. -1.74 puts it at 0.12, and 0.1 is within
  that estimate's se of about 0.1.
- Herding unchanged. With the share at 1.0 it acts on the market-wide
  mispricing only. Switching it off moves no cross-sectional row beyond noise
  (seeds 207-212: C8 -0.56 against +0.06, C5 0.95 against 0.96), so pt-v19's
  values stay rather than become a sixth moved dial.

Merged into the branch: E2's fix (fills applied once, 04190ba), without
which C4b grades the harness and not the market.

## Predictions, from the desk (seeds 201-212, never the grade's)

    row                        real      pt-v19 (desk)   pt-v20 (desk)
    C4a 65-min ACF1            ~0        -0.155          -0.03 to -0.04
    C5  VR60                   0.924     0.59            0.93 to 0.95
    C6  value IC, whole        +0.009    +0.40           0.00
    C6  value IC, first 60     --        +0.73           0.00 to +0.015
    C7  12-1 / 6-1             +.03/+.04 -.17/-.17       -.01/-.01
    C8  Lo-MacKinlay bp/day    -1.74     +10             -0.6 to -3

P1. pt-v20 passes C4a, C5, C6, C7 and C8. The rows most at risk are C4a
(-0.038 on seeds 207-212, inside by 0.012) and C7's 6-1 (-0.008, inside by
0.012).

P2. pt-v19 fails C5, C6, C7 and C8, and C4a and C4b as E2 graded it.

P3. The fifteen are NOT predicted to hold untouched. The structural change
makes every stock-specific move permanent. The cap-weighted index then
carries its names' idiosyncratic moves for good, where before they reverted,
and its concentration drifts (Herfindahl 0.187 to about 0.21 over 10.5
years, desk). The rows at risk are B3 and B4 (bear markets and corrections
per decade; B4 sat at 1.85x, near its 2x edge), B7, C1 and the replay rows
A1 and A2, which the stop ladders' tenth may soften. The box carries two
ablation arms so a miss can be attributed rather than argued, beside the two
market-share arms of section B9:
- `pt-v20-c1`: `cascade_gain` 1.0, the ladders at full size;
- `pt-v20-tape`: `fair_value_news_share` 0 and both opening spreads 0, the
  tape fixes and the ladders only.

A local preview on 6 seeds (101-106, the grade's first six, read before
this section was written) put pt-v20 against pt-v19:
- B3 at 2.08 against 1.50 per decade;
- A2 2008 at 0.38 against 0.41;
- B8 at 4.8 against 6.3.
It also put pt-v19 itself outside A1 and C1, which 30 seeds pass (81 and
1.02x on the fifth box). Six seeds cannot grade these rows, and the box
does.

## What would stage it

If the structural part (the fair-value share) costs one of the fifteen, and
no value of it both holds that row and keeps C5-C7 in band, the structural
part moves to a pt-v21 and pt-v20 ships the tape fixes. That is the "strong
reason to stage it" the brief asked me to state early, and it is a
measurement, not a preference.

## Second registration, 2026-09-24 (before the final box): R1-R4, C9, E1

Registered after box ptv20g1 graded the market-side preset and before the
final box. Nothing above changes. The owner widened pt-v20 to carry the
agent-facing book (E4), the bonds (E5), a fixed yield curve and an
endogenous earnings cycle, and ruled that B9 must pass at launch. Bands are in
`results/ptv20/bands.json`.

**R, the yield curve.** Real: FRED DGS2 and DGS10 daily changes, and SPY
daily returns against IEF (7-10 year Treasuries) and LQD (IG corporates),
2015-01-02 to 2025-07-31 (`results/ptv20/real_rates.py`). Each band is the
real value plus or minus 2 calendar-year bootstrap se (B = 500).

    row  what                                              real     band
    R1   sd of the 2-year's daily change, bp               5.23     3.65 to 6.80
    R2   sd of the 10-year's daily change, bp              5.41     4.54 to 6.27
    R3   corr(index return, minus the 10-year's change)    -0.16    -0.36 to +0.03
    R4   corr(index return, minus the IG yield's change)   +0.27    +0.15 to +0.39

- The model side: the same 30 histories as C5-C8. The yields at each close
  come from the economy state; the index is the cap-weighted certified roster.
  The row is the median over histories.
- Reported beside, not graded: a daily-rebalanced 60/40 book's volatility
  (real 10.9 per cent) and the IG yield's daily change sd.
- A limit, stated plainly: the model's inflation almost never leaves the
  under-3-per-cent regime, so it is always in flight to quality. The real
  -0.16 pools 2015-21 (-0.35 to -0.5) with 2022-24 (positive). The model
  matches the pooled figure, not the regime switch.

**C9, the cost of size** (E4's agent-facing book). Measured by
`tools/calibration/impact_curve.py --base pt-v20` (3 seeds x 40 names, each
name's sigma realised over 60 sessions). It fits the median average cost of
an immediate order of Q = f V against f, from 1 per cent of daily volume up,
in sigma units.
- Exponent band 0.4 to 0.7: the square-root law's 0.5 with the range the
  literature reports (Toth et al. 2011; Almgren et al. 2005's 0.6).
- Coefficient band 0.33 to 0.67: Toth et al.'s peak Y in [0.5, 1], two
  thirds of it for the average cost of reaching that peak under a latent book
  linear in distance, as E4's tool states it.
- Desk, before this registration: 0.495 and 0.469.
- C4b is re-graded with the book on, since traded results change.

**E1, the aggregate earnings fall in a contraction** (the earnings cycle).
Per contraction in the 30 histories: exp of the cycle's lowest in the 504
sessions after the contraction starts over its highest in the 252 before,
less one; the row is the median.
- Real: S&P earnings around the eleven NBER recessions 1953-2020 (Shiller's
  reported earnings, 2008-09 capped at the operating series' -40 per cent):
  median -0.17, range -0.016 to -0.54.
- Band -0.40 to -0.046: that range without the most and the least severe.
- In the model the cycle is relative to nominal output, which already grows
  earnings with it.
- Source: Robert J. Shiller, "Stock Market Data Used in Irrational
  Exuberance", ie_data.xls, http://www.econ.yale.edu/~shiller/data.htm,
  retrieved 2026-09-24. The file states no licence, only a disclaimer of
  accuracy, and is posted for public download. The two columns used are
  frozen in `results/ptv20/shiller/` for reproduction only.

**The earnings cycle's calibration.** A calibration box (ptv20e1) runs on
seeds 401-430, never the grade's. Its grid covers depth 0.2 to 0.4 and the
market mispricing's half-life at 40, 60 and 90. The pick is the arm that puts
B9's annual spread nearest 17.4 while B3, B4, B8, C1 and A2 stay inside
their edges. If none does, I stop and report the trade-off with numbers, as
the owner asked.

## Third registration, 2026-09-24: 90 pooled histories, and the co-tune grid

Written after the calibration boxes (ptv20e1, e2, e3) and before any box
grades on the pooled set.

**Why the seeds are pooled.** The same build and the same preset read very
differently on three sets of 30 histories. On pt-v20 with no earnings cycle:

    set        B3     B5     C1     A1 2020   B9 annual
    101-130   1.95    5.4   1.17     67        11.5
    401-430   2.15   15.2   0.93     66        12.5
    701-730   1.78    9.7   0.62     77        11.2

pt-v19 sits the same way: B5 4.0 on 101-130 against 7.1 on 701-730, C1 1.02
against 0.51. So a 30-history tail row can pass on one set and fail on the
next. The owner ruled that the long-run rows are graded on the three sets
POOLED, 90 histories:
- A1-A3, B1-B9 and C1 come from the pooled long run and the replays;
- C2 is read as a rate, at most one history in 30, so at most 3 of 90;
- C5-C8, R1-R4 and E1 pool the same 90 seeds (2,660 sessions each);
- each set's figure is reported beside the pooled one;
- no band moves.

The tuned values are chosen on the pooled 90. The one-year certification
cells (D1), C3 and C4 keep their registered protocols.

**The co-tune grid** (`results/ptv20/arms-e4.txt`, box ptv20e4). pt-v20 plus
the earnings cycle, crossed with the levers the owner named:
- the market factor's transient shock size (`market_factor_sigma` x0.85,
  x0.70);
- the stock-level permanent share (`fair_value_news_share` 0.9);
- the stop ladders (`cascade_gain` 0 and 0.3);
- the market jump rate (x0.5);
- the VIX regime level (`vix_level_sigma` x0.7).
The arm chosen is one that passes every long-run row on the pooled 90. It is
then graded by name on every registered row.

**The grid's result and the arm chosen** (written after box ptv20e4, before
the final grade). `results/ptv20/grid-e4.txt`. Five arms of 17 pass every
long-run row and B9's annual spread on the pooled 90:

    arm          B3     B9 annual   B1     widest band use
    D30m85       2.09   14.81       6.0%   0.90 (B3)
    D30m85c0     2.12   14.81       6.0%   0.91 (B3)
    D30m85p90    2.17   14.64       6.0%   0.95 (B3)
    D35m85       2.14   15.89       6.0%   0.93 (B3)
    D35m85j50    2.07   16.78       5.8%   0.88 (B3)

Without the 0.85 cut every depth fails B3 (2.43 to 2.49). The 0.70 cut
fails B1 (2.6 per cent). With no cycle, B9 reads 11.70.

pt-v20 takes D35m85j50: earnings depth 0.35, upside 0.09 (derived: 9
months of contraction and trough in the US table's 108-month cycle,
9/99), `market_factor_sigma` 0.006454071 (0.85 of pt-v19's) and
`jump_intensity_market` 0.02828766685 (half). It has the most room on B3
and on B9. Both cuts shrink transient market shocks, and the cycle adds
lasting ones.

The final grade is box ptv20g2 (`results/ptv20/arms-g2.txt`,
`cert-arms-g2.txt`). It covers pt-v19 and pt-v20, by name, on every
registered row, with the long run, C5-C8, R1-R4 and E1 on the pooled 90. Its
long-run rows repeat e4's arm bit for bit, because the preset carries the
same values. What the box adds is every other row with the cycle in.

The rate-to-P/E proposal (`ptv20-scenario-size.md` section 5) is not in this
grade. RATE_PE_SENSITIVITY is still a constant, and making it a dial and
re-grading takes another box. It stays a separate proposal.

## Fourth registration, 2026-09-24: after box ptv20g2

Written after the final grade box ptv20g2 and before the box that replaces it.
No band moves. On the pooled 90, pt-v20 passed 25 of the 28 rows it was scored
on. Three things stopped it: an instrument error, a row the grid did not
measure, and a crash.

**R3 and R4: the yields were read a day late.** `results/ptv20/desk.py` read
the economy's yields after the session and BEFORE `close_market`. The close's
macro step is what sets them, and it reads the session's return for the flight
to quality. So each yield change was paired with the next session's index
return. The registered row is the same-day correlation (FRED's close against
the index's close), and the desk's own curve read (`rates.py`, before this
registration) took the yields after the close. Box g2's R3 reading, +0.076,
is on the misaligned instrument; R1 and R2 (sds) are unaffected. desk.py now
reads the yields after `close_market`. The next box grades R3 and R4 with the
fix, and g2's figures stay in `results/ptv20/box-g2/` beside it.

**D1: the volume-to-return correlation.** The co-tune grid measured the long
run only. On g2's certification cells, pt-v20 misses one band: the 504-session
panel's `volume_abs_return_corr`, at 0.639 against a ceiling of 0.63. A desk
screen (`results/ptv20/d1screen.py`, the same protocol) reproduces 0.6392.
- The earnings cycle does not move it: 0.6392 without the cycle.
- The two transient cuts do: 0.630 without them, 0.637 with the 0.85 cut
  alone.
- Less market noise leaves the common volume multiplier a smaller share of a
  name's volume, so its volume tracks its own move more tightly.

`volume_move_response` is the only term that ties a name's volume to its own
move on the same day. The next box ships the value the desk screen picks. That
value must put the 504 panel back inside with room, and the box grades it on
every row.

**C4b: a suite market refused to run.** The published suite's market 92019
(recession) refused under pt-v20. The packaged recession adds -3 points of
growth, and on that seed the economy was already contracting at 2.3 per cent,
under the rates' -5 per cent floor. The curve's new 2-year draw moved the
economy stream, so the macro path differs from pt-v19's. pt-v19 refuses on 3
of 30 other seeds (`ptv20-scenario-size.md`). The owner approved a growth-only
floor of -10 per cent (US real GDP -7.4 per cent year on year to 2020Q2,
-10.0 annualised in 1958Q1, FRED GDPC1). It moves no run that ran before, and
all twenty suite markets now run on pt-v19 and pt-v20. C4b is scored on the
next box.

**The volume value, and the box that grades it** (before box ptv20g3). The
desk screen of the 504-session panel's `volume_abs_return_corr`, with
`volume_move_response` at 0.85, 0.80 and 0.75 on pt-v20, read 0.625, 0.618
and 0.612. At 0.80, the 252-session panel, the held-out seeds and the
held-out universe all stay at 15 of 15, and the level rows are unchanged
(index drift 1.1446, which g2 also read). pt-v20 ships 0.80
(`results/ptv20/d1screen.py`).

A desk read of R3 and R4 with same-day yields (six histories of 1008
sessions) gives -0.13 and +0.30 on pt-v20. Box ptv20g3 grades pt-v19 and
pt-v20, by name, on every registered row (`arms-g3.txt`, `cert-arms-g3.txt`).
It uses the same protocol as g2, with desk.py's yields read after the close
and the growth floor in the engine.

## Fifth registration, 2026-09-24: the driven 2020-21 market (D2)

Written before any grade of it. Notebook 09 drives pt-v20 through the real
2020-21 macro path. On the release build its index fell 58.3 per cent and never
recovered, where the S&P 500 fell 33.9 per cent and regained its February high
by August 2020.

**The cause.** The path pins the VIX, the policy rate, a credit yield and a
QE ramp, but not the cycle phase. So the economy's own cycle ran free:
- on the notebook's seed it sat in expansion and peak through 2020;
- it turned to contraction in April 2021 and reached trough in November;
- the earnings cycle took aggregate earnings 30 per cent down through 2021, a
  recession the real economy did not have.

pt-v19 has no earnings cycle, so a wrong phase cost it nothing. The fix is to
the path: it now carries the NBER phases.
- peak to February 2020;
- contraction March-April 2020 (NBER: peak February, trough April 2020);
- recovery to April 2021 (the four quarters after the trough, as the US phase
  table labels them);
- expansion after.

With the phases in, the notebook's seed reads an index drawdown of 28.5 per
cent, back at its February high in June 2020 (desk, before this
registration).

**The row, D2.** `results/ptv20/driven2020.py`:
- the path above, pinned each morning from the engine's own opening;
- the certified roster, `Universe.random(40, seed=111)`, cap-weighted at fixed
  shares;
- the pooled 90 histories;
- each reading is the median over histories.

    row   what                                                  real    band
    D2a   the index's maximum drawdown over the path            0.339   0.237 to 0.441 (within 30%, as A2)
    D2b   sessions from the pre-crash high back to it           126     63 to 252 (1/2x to 2x, as the B rows)

The pre-crash high is the highest close of the first 40 sessions. A history
that never gets back counts as never. Real data: the S&P 500 closes in
`results/ptv20/data/covid-2020-2021.json`, the notebook's file. The row is
graded on pt-v19 and pt-v20 in the same regrade box as the other 28.

## Sixth registration, 2026-09-24: the regrade after the core fixes (box ptv20g4)

Written before box ptv20g4 runs. Since g3, the engine branch (fix/ptv20-core
6462721, on release/0.8.5) has changed:
- **A pinned VIX charges the corporate yield no VIX term**, and a pinned
  corporate yield holds through the close on pt-v20. These fixed the ratchet
  that also depressed the crisis lever's low-VIX cell: the lever reads 5.29x
  on the desk (pt-v19 5.22x, real 6.16x), where g3 read 3.60x.
- **The corporate yield's daily move is capped at 0.50 points.** FRED DBAA's
  largest in 1986-2026 is 0.48.
- **truth() gains `fair_value_shift`**, which moves no price.
- **The Oracle trades pt-v20's hidden state.** It is reported, not graded.
- **pt-v20's `volume_move_response` is 0.6.** At 0.8 the volume-return
  correlation crossed 0.63 from 1,260 sessions; at 2,520 it reads 0.633 at
  0.8, 0.621 at 0.7 and 0.609 at 0.6.

A desk re-check after the pin fixes, before the cap and the volume change:
- the replays: A1 87/72, A2 0.46/0.35, A3 0.78/0.77;
- C4b's worst rule: +0.55 pts, 11 of 20.

ptv20g4 grades pt-v19 and pt-v20, by name, on all 28 rows plus D2. It uses the
g3 protocol on the pooled 90 (`arms-g4.txt`, `cert-arms-g4.txt`). No band
moves.

Not changed, and said plainly: the volatility memory. The index's |return|
lag-1 autocorrelation over the pooled 90 falls from 0.2196 to 0.1935 with the
market factor's 0.85 cut (grid e4). Market jumps at half rate give back
0.006. A name's own |return| lag-1 over 2,520 sessions reads 0.128 against
pt-v19's 0.194. Undoing the cut fails B3 (2.43-2.49 against a ceiling of 2.24
on the grid), so pt-v20 keeps it. No GARCH or VIX persistence dial was tested
in time.

**Two regrade boxes, not one.** The volume change moves every trajectory of
the default preset, so the release's five LLM fixtures, which were
re-recorded on pt-v20, would need recording again. It therefore moves to its
own branch, `tune/ptv20-volume` (6462721). fix/ptv20-core goes back to
40f3f39, with the volume response at 0.8. Both are graded on the same rows,
before either verdict is read:
- ptv20g4: tune/ptv20-volume at 6462721, volume response 0.6;
- ptv20g4b: fix/ptv20-core at 40f3f39, volume response 0.8.

The choice between them is the owner's. The 10-year volume-return gap
(reported, not graded) against the cost of re-recording the fixtures.

## Seventh registration, 2026-09-25: volatility memory on top of volume 0.6 (grid ptv20e5)

The owner ruled that pt-v20 takes `volume_move_response` 0.6. A volatility-memory
fix may be added if it holds all 29 rows on the pooled 90. Otherwise it is left
out and reported.

**Desk screen** (certified roster, 9 histories of 1,260 sessions): |return| lag-1
autocorrelation, index / median name.
- pt-v19: 0.183 / 0.098.
- pt-v20: 0.181 / 0.091.
- `garch_beta` 0.85: 0.194 / 0.101.
- `garch_beta` 0.88: 0.193 / 0.108.
- `market_vol_beta` 0.93 and 0.95 collapse the index's volatility to 10-11 per
  cent.
- `vix_mean_reversion` 0.20 lowers the memory.

So the per-name GJR's persistence is the lever. Grid ptv20e5 (`arms-e5.txt`):
pt-v20 (volume 0.6), `garch_beta` 0.85 and 0.88, on the long run over the
pooled 90 and on the four certification cells.
- The pick is the arm that passes every long-run row and D1 with the most room.
- It is then regraded by name on all 29 rows.
- If neither arm passes, pt-v20 keeps `garch_beta` 0.7905.

**The grid's result and the final dials** (before the regrade box ptv20g5).
Grid ptv20e5 (`grid-e5.txt`, `certgrade-e5.txt`). Every long-run row and all
four certification cells pass on every arm.

    arm     index |r| lag-1   name |r| lag-1, 252 / 504   B3 (use)      B9 annual   GJR persistence
    v20     0.204             0.034 / 0.042               2.07 (0.88)   16.62       0.942
    gb85    0.201             0.044 / 0.053               2.11 (0.91)   16.24       1.001
    gb88    0.203             0.052 / 0.060               2.07 (0.88)   16.39       1.031

pt-v20 takes `garch_beta` 0.85: the smallest move that clears the decade
floor of 0.04. 0.88 would put the GJR's first-moment persistence at 1.03,
held only by its variance floor and ceiling. The index-level memory does
not move with either. Final dials:
- `volume_move_response` 0.6;
- `garch_beta` 0.85;
- the rest as at g4b.

Engine fix/ptv20-core 99969c7. Box ptv20g5 regrades pt-v19 and pt-v20, by
name, on all 29 rows (`arms-g5.txt`, `cert-arms-g5.txt`).

**Box ptv20g5: garch_beta 0.85 fails D1, and is withdrawn.** On g5, pt-v20
passes 28 of 29 rows. D1 misses the one-year level row: index_drift_pct
reads 1.047 against the band's floor of 1.1. The grid had shown the same
reading (gb85 1.0467, out of band; gb88 1.117; v20 1.143), and I read only the
cells' in-band counts. pt-v20 goes back to `garch_beta` 0.7905, on
fix/ptv20-core. With volume 0.6 it has passed all 29 rows (g4, e5).
Volatility memory is carried into the next grid, beside the valuation work,
which moves the index drift directly.

## Eighth registration, 2026-09-25: anticipation, the rate sensitivity, and five rows

The owner asked for two mechanisms, and for rows registered before any grade.

**Forward-looking valuation** (`earnings_anticipation_half_life`, engine
fix/ptv20-core). Fair value reads the earnings cycle's expected path averaged
under a discount of half-life H sessions, not today's level alone:
`A = c e + g_phase`. Here c = rho / (rho + kappa), and g solves the cycle's
five linear equations from the engine's own hazards and pull
(`Engine::earnings_anticipation_terms`).
- A turn of phase moves prices at once.
- A trough, from which a recovery is near, reads above a contraction, so the
  price trough leads the earnings trough.
- Probe on pt-v20 at an earnings level of 0.0315 (anticipated level by
  phase):

      H      expansion  peak    contraction  trough   recovery
      63     +0.028    -0.031   -0.117      -0.056   +0.031
      126    +0.023    -0.056   -0.116      -0.045   +0.028
      252    +0.016    -0.057   -0.088      -0.028   +0.022

**The rate sensitivity** becomes a dial, `rate_pe_sensitivity`. It was the
constant 1.5, which is every preset's value and moves no digest.

**Rows, each median over the pooled 90** (`bands.json`):

    row   what                                                          real     band
    F1    driven 2020: high to the lowest close within 60 sessions     0.339    0.237 to 0.441 (within 30%)
          and the sessions it took                                      23       12 to 46 (1/2x to 2x)
    L1    driven 2020: sessions from the index trough to the            68       1 to 136 (the price leads,
          earnings level's trough                                                 by at most 2x)
    R5    driven 2022: the index's maximum drawdown                     0.254    0.178 to 0.330 (within 30%)
    R6    driven 2022: market P/E log change per 100 bp of the          -5.2%    -10.4 to -2.6 (1/2x to 2x)
          corporate yield, monthly averages January-October

The paths:
- **F1 and L1** use D2's registered path (`driven2020.py`).
- **L1's real lead** runs from the S&P trough on 23 March 2020 to the end of
  Q2 2020, the trough quarter of S&P operating earnings.
- **R5 and R6** use `driven2022.py`, the real 2022 path. It pins the VIX,
  fed funds (DFF), Baa (DBAA), 2- and 10-year yields (DGS2, DGS10), and the
  expansion phase, since NBER dates no 2022 recession.
- **R5's real value** is the S&P 500 from 3 January to 12 October 2022, from
  the tape.
- **R6's real value** is the 2022 trailing P/E against Baa (section 5 of
  `ptv20-scenario-size.md`).

The packaged scenarios are re-measured on the final dials and reported
(liquidity_crisis at 21 sessions against March 2020's -28.8 per cent), not
graded.

**The grid (ptv20e6)** crosses H in {0, 63, 126, 252} with
`rate_pe_sensitivity` in {1.5, 3.0, 4.0}, and `garch_beta` 0.88 on the
leading arms (volatility memory, seventh registration).
- The pick is the arm that passes every row, the 29 and these five, with the
  most room.
- It is regraded by name on all 34.
- If no arm passes every row, the mechanism stays off and the numbers are
  reported.

## Ninth registration, 2026-09-25: two corrections to the 2020 path, before the grid

Written after a nine-history desk look at the new rows (below) and before any
grade of F1 or L1, and before D2 is graded again. D2's band does not move.
Two errors in the path D2 was registered on:

1. **The corporate yield.** The path took notebook 09's credit yield, a
   high-yield proxy from HYG: 5.5 per cent at the start and 11 per cent at
   the peak. The model reads the corporate yield as investment grade: it is
   the yield fair value discounts at, and the one the IG bond index prices
   off. The path now takes Moody's Baa (FRED DBAA): 3.8 per cent, and 4.9 at
   the peak, 20 March 2020. With the rate sensitivity at 4, the proxy
   discounted every name at an 11 per cent yield.
2. **The phase labels.** The path folded April 2020, the NBER trough month,
   into contraction. The model's own table splits a recession into
   contraction and trough (5.5 and 3.5 months). The path now reads
   contraction in March and trough in April, then recovery to April 2021,
   then expansion.

Desk, nine histories on the corrected path (D2a / D2b / F1 / L1):
- v20: 0.370 / 196 / 0.282 in 51 / -7.
- H126, rate 4: 0.309 / 108 / 0.234 in 40 / +4.
- H63, rate 4: 0.329 / 117 / 0.263 in 40 / +4.
- rate 4 alone: 0.360 / 190 / 0.285 in 50 / -9.

Driven 2022, R5 / R6:
- rate 1.5: 0.198 / -1.17 per cent.
- rate 4: 0.238 / -4.69 per cent.

**Grid ptv20e6**, two boxes, every registered row (the 29 and F1, L1, R5,
R6):
- e6a: rate 4 alone; H42 rate 4; H63 rate 4; H63 rate 3.
- e6b: H126 rate 4; H126 rate 3; H63 rate 4 with `garch_beta` 0.88; H252
  rate 4.

The pick follows the eighth registration's rule.

## Tenth registration, 2026-09-25: grid ptv20e6's result, and grid ptv20e7

Grid e6 (`criteria-e6a.txt`, `criteria-e6b.txt`): no arm passes every row.

    arm        fails                one-year index drift (D1 level row, band 1.1-10.3)
    r4         B3, F1, L1, D1       -0.14
    H42r4      B3, C4b, D1          -1.35
    H63r4      B3, C4b, D1          -1.34
    H63r3      C4b, D1              -0.84
    H126r4     B3, C4b, D1          -0.83
    H126r3     C4b, D1              -0.34
    H63r4g88   B3, C4b, D1          -1.35
    H252r4     C4b, D1               0.07

Every arm passes F1, L1, R5 and R6 except rate 4 alone (F1, L1). The worst
C4b rule is 5-day mean reversion winning 15 of 20 against buy-and-hold (the
ceiling is 14), where buy-and-hold loses 5 to 7 per cent at the median. So
two things break: the one-year drift, and C4b.
- **The rate sensitivity** costs about 0.8 points a year of drift at 3, and
  1.3 at 4.
- **The anticipation** costs 0.7 more. In an expansion the anticipated
  level sits below today's, so prices grow more slowly than earnings while
  the next recession is priced.
- **Everything else** that grades a year or more holds.

**The lever.** `buyback_payout_share` has no measurement behind it
(provenance: undetermined, "a third is not distinguishable from a quarter").
It adds drift, and the one-year drift reads (desk, 30 rosters):

    arm          drift
    r3 bb 0.6    1.73
    H126r3 bb 0.6   1.04
    H252r3 bb 0.6   1.94
    H126r3 bb 0.75  1.79
    H252r4 bb 0.75  2.21

It also raises buy-and-hold's return in the suite markets, which is what C4b
measures against. B8 (the long-run return, 6.25 plus or minus 2) bounds it.

**Grid ptv20e7**, two boxes, every row:
- e7a: H252 rate 3 bb 0.6; H252 rate 4 bb 0.6; H252 rate 4 bb 0.75; H126
  rate 3 bb 0.75.
- e7b: H252 rate 3.5 bb 0.6; H504 rate 4 bb 0.6; rate 3 bb 0.6; H252 rate 3
  bb 0.6 with `garch_beta` 0.88.

The same rule applies: the pick passes every row with the most room. If none
does, the mechanisms stay off and this is reported.

## Eleventh registration, 2026-09-25: C10, no public macro signal predicts returns; grid e7's result; grid e8

**Grid e7** (`criteria-e7a.txt`, `criteria-e7b.txt`), on the 33 rows (the 29
and F1, L1, R5, R6):
- H126, rate 3, buybacks 0.75 passes all 33. Its tightest row is C4b, at 14
  of 20, the ceiling itself.
- Every other arm fails one to four rows. The failing rows are C4b (15 of
  20), F1 at H252 and longer (drops of 0.233-0.236), B9 at H504 and with
  `garch_beta` 0.88 (13.5-13.6), and L1 without the anticipation.
- The one-year drift is in band on every arm (1.45-2.23).

**C10** (the independent audit, at d154e47). Timing rules on published macro
data beat buy-and-hold on pt-v20:
- out while the published phase is contraction or trough wins 30 of 30
  histories by a median 4.1 points a year;
- after a published turn to contraction the index falls 8.8 per cent over 21
  sessions and 22 over 63.

The row, `results/ptv20/c10.py`, on the long run's 90 free histories:
- The long run now records the published macro fields at every close.
- The rules: out while the phase is contraction and trough; out in peak and
  contraction; in only in trough and recovery; out in contraction. Then, for
  each of the ten published macro fields, out while its 21-session change is
  up, and out while it is down.
- Timing: decided from what was published by the close before, and a switch
  costs 5 bp.

    row    what                                                        bound
    C10a   every rule: median annualised excess over buy-and-hold       at most +1.0 point
           share of histories where it is ahead                        at most 2/3
    C10b   index log return over 63 sessions after the published
           entry into a contraction, less the unconditional 63-session mean   at least -5.9 points
           after the entry into a recovery                              at most +4.3 points

The real references:
- **The unemployment rule** (out while the latest published unemployment rate
  is above its level of a month before), S&P 500 1990-2024 with FRED UNRATE
  read from the 7th of the following month: median annual excess -2.0
  points, ahead in 11 of 34 years.
- **NBER turns** 1990-2020, dated with hindsight: the S&P 500's 63-session
  return after a recession's start less its unconditional 63-session mean is
  -5.9 points (starts: -16.5, +5.4, -7.1 and +3.0 per cent, less +2.05). After
  a recession's end it is +4.3 points (-0.2, +0.6, +14.3 and +10.9, less
  2.05).
- Hindsight dating is the most a phase could be worth. The NBER publishes a
  peak about a year after it, so a real-time phase would be worth less.
- The bands are those magnitudes, and C4b's two thirds.

A local preview (6 histories of 12 years) before this registration:
- pt-v20 as it ships: +2.94 points a year, ahead in 6 of 6; drifts -21 / +20.
- H252, rate 3, buybacks 0.6: worst rule -0.31; drifts -11.1 / +7.2.
- The same with the phase read 252 sessions late: -0.50; +2.7 / +1.4.

The last case bears on whether the phase should be published in real time,
which an API change would decide. It is reported beside the grade with
`--lag 252` and not graded.

**Grid ptv20e8**, two boxes, every row and C10:
- e8a: H126 rate 3 bb 0.75; H126 rate 4 bb 0.75; H168 rate 3 bb 0.75; H126
  rate 3 bb 0.85.
- e8b: H84 rate 3 bb 0.75; H126 rate 3.5 bb 0.75; H189 rate 3 bb 0.8; H126
  rate 3 bb 0.75 with `garch_beta` 0.84.

The pick passes all 34 rows with the most room. If no arm passes C10, the
report gives the numbers with and without the phase lag.

**Grid e8's result** (`criteria-e8a.txt`, `criteria-e8b.txt`, `c10.log`,
`c10-lag252.log`):
- No arm passes C10 on the phase as the API publishes it today. The worst
  rule is out in peak and contraction, +0.2 to +1.3 points a year; the
  drifts after a turn are -9.6 to -13.4 and +12.8 to +16.8.
- With the phase read 252 sessions late, every arm passes C10.
- The leading arm (H126, rate 3, buybacks 0.75) passes the other 33 rows.
- The owner ruled that the phase is published about 12 months late, as the
  NBER dates recessions. C10 is graded on the published phase. The engine
  change is the next step (`ptv20-status-2026-09-25.md`).

## Twelfth registration, 2026-09-26 (registered before any grade-seed run of the arm below): the audit's majors 3-5, and the macro fields that gave the phase away

**The phase lag alone does not pass C10.** On grid e8's leading arm (H126,
rate 3, buybacks 0.75), with the phase read 252 sessions late:
- `gdp_growth` steps about -3 points at the first close after a contraction
  begins (a fixed shock at each turn). Out for 63 sessions after that drop:
  +0.85 points a year, ahead in 88 of 90 histories.
- The largest rises in the published unemployment rate all come 3-22
  sessions after a contraction begins (105 of 105). Out for 63 sessions after
  one: +0.30 points a year, ahead in 77 per cent.
- C10 as registered in the eleventh registration did not test these. Its
  21-session change rules dilute a one-session step.

**Owner decision (2026-09-25): GDP growth is published as the BEA publishes
it**, a quarterly figure (the quarter's mean) released 21 sessions after the
quarter ends, about the advance estimate's month. Dial on fix/ptv20-core,
inert at its default, set on pt-v20. Simulated offline on grid e8, it takes
the GDP rules to ahead in at most 36 per cent.

**Unemployment responds gradually** (`unemployment_adjustment_half_life`,
84 sessions on pt-v20). Each monthly release closed its whole gap to Okun's
law on the true growth, so the first release after a turn rose about 1.2
points against a 0.27 spread. A partial adjustment with an 84-session
half-life matches 2001 (UNRATE 4.3 to 5.5) and 2008 (5.0 to 9.5): the first
release rises 0.16, and 15 per cent of the rise comes in the first 63
sessions. On the desk seeds (201-212) the largest-rise rule goes from 7 of 12
ahead to 0 of 12.

**Fear/greed reads what is published** (a switch on pt-v20). Its target read
the true phase and growth directly: a 5-session fall of 34.6 points entering
a contraction, 7.9 times a normal spread. Out for 63 sessions after a
5-session fall of more than 20 points: ahead in 84 of 90 grid e8 histories.
The real index is built from market data and knows no recession dating. With
the switch, the target reads the published phase and GDP.

**C10, the event rules (added to C10a).** For each published field, out for
21, 63 or 126 sessions after one of its largest moves over one session or
five, up or down: above the 95th, 99th or 99.9th percentile of the history's
nonzero moves over that span (a whole-history threshold, a look-ahead that
can only help the rule). 360 event rules beside the 24 before, the same
bounds as C10a (median excess at most +1.0 point, ahead at most 2/3).
`results/ptv20/c10.py`.
- The family catches all three leaks on grid e8's leading arm with the phase
  252 late: GDP (out 63 after a drop, ahead 0.97), fear/greed (out 63 after
  a five-session fall above the 99.9th percentile, 0.93) and unemployment
  (out 21 after a rise above the 95th, 0.89). 32 rules fail there.
- Chance: a rule that is a coin flip is ahead in more than 60 of 90 with
  probability 0.05 per cent; over 384 rules, as independent coin flips, about
  a 17 per cent chance that one fails. But a rule that sits out gives up the
  market's drift, so a rule without an edge is ahead far less than half the
  time: on grid e8 the 266 rules on the fields that do not leak have a median
  share ahead of 0.06.
- The exception on grid e8: out 21 or 63 sessions after a corporate yield
  rise above the 99.9th percentile is ahead in 60 of 90 (+0.15 and +0.19
  points), at the bound. Credit spreads widen as a contraction begins. In
  real data a wide default spread is followed by higher equity returns, not
  lower (Fama and French 1989).
  `macro_publication_repricing` prices each yield move when it is published,
  which should take the edge; the grade decides.

**R7, no drift after a published policy-rate decision** (audit major 3).
- The mechanism: `macro_publication_repricing` (fix/ptv20-ratenews, e388145),
  inert at 0, 1 on pt-v20. The close's macro step is priced in the moment it
  is published: each name moves by its change in fair value, its mispricing
  untouched.
- The statistic: the equal-weight index's mean log move from the first price
  readable after a changed policy rate to the end of the first 65-minute bar,
  for hikes and cuts apart, less the mean first bar of all days. 30 histories
  of 21 years, the certified roster.
- The companion: the audit's rate-news agent (sell at the open after a hike,
  double after a cut, revert next step) through `tf.evaluate` at six steps a
  day, 30 histories of 10 years, on the audit's roster
  (`Universe.random(40, seed)`), seeds 101-130.

      row   what                                                   bound
      R7a   first-bar move after a hike, less all days             within ±5 bp (or ±2 se if wider)
            the same after a cut                                   within ±5 bp (or ±2 se if wider)
      R7b   the rate-news agent: mean annual excess over holding   at most 0
            histories where it beats holding                       at most 20 of 30

- Real reference: the S&P 500's response to an FOMC decision lies within
  about 30 minutes of the statement (Gürkaynak, Sack and Swanson 2005;
  Bernanke and Kuttner 2005). The drift comes before the announcement, not
  after it (Lucca and Moench 2015).
- Before, on the leading arm (box ratenews1): -62.2 bp after a hike, +162.9
  after a cut; the agent +1.90 points a year, ahead 30 of 30. After, with the
  dial: +0.6 and +3.9 bp; -0.28 points a year, ahead 7 of 30. Those were
  seeds 601-630; the grade reads 101-130.

**S1 and S2, the packaged recession recovers** (audit major 4). The file
`recession.yml` held contraction for good; every seed ended 600 sessions
still in contraction. The fix is in the file (fix/ptv20-recession, then
fix/ptv20-recession2 for the more permanent market V1 needs: recovery on the
NBER trough date, day 428; earnings held at x0.65 to September 2009, 0.92
of the peak over 2010 as the S&P's were).
- The statistic: recession.yml as the graded engine ships it, on the certified roster, the cap-weighted
  index against the same seed with no scenario, seeds 101-130. The file was
  tuned on seeds 301-330 (42 variants, boxes recgrid1-4; recession2's 28
  variants, boxes rec2grid1-2) and checked on 201-230, so the grade reads
  others.

      row   what                                                   bound
      S1a   share of the log fall (at its lowest) won back 252
            sessions later, mean over seeds                         45% to 100%
      S1b   seeds out of contraction and trough within 24 months
            of onset, on the true phase (the published one is a
            year late by design; its exit is recorded beside it)    30 of 30
      S2    the index's own rise in the 252 sessions after its low  +25% to +80%

- Real references: from the S&P's low, 12 months on, share of the fall won
  back: 2009 62%, 2002-03 43%, 1990-91 114%, 2020-21 135%. The rise: 2009
  +69%, 2020 +75%, 2002 +34%, 1990 +29%. The longest post-war NBER
  contraction is 18 months.
- Graded on pt-v20, which 0.8.5 makes the default, and on pt-v19.

**V1, long-horizon variance ratio** (audit major 5). The audit found the
index mean-reverting far faster than the S&P: on the leading arm the 5-year
variance ratio read 0.32 (95% CI 0.29-0.35). Every market-wide shock sat in
the mispricing, which reverts with about a 40-session half-life, and the
earnings cycle made fair value itself cyclical.

- The statistic: the cap-weighted certified-roster index (as B7 and B9
  read it), years 2-21 of the pooled 90 free histories. Overlapping daily
  log-return sums with the pooled mean removed; VR(h) is their variance
  over h times the one-day variance; bootstrap over histories. Scaled to
  one year so the short-horizon level stays out of the row
  (`results/ptv20/v1.py`).

      row   what                                   band
      V1a   VR(504) / VR(252)                      0.75 to 1.15
      V1b   VR(1260) / VR(252)                     0.55 to 1.20

- Real reference, from the Shiller monthly S&P file
  (`results/ptv20/real_ratio.py`): 2y/1y 1.01 (se 0.07) and 5y/1y 0.87 (se
  0.13-0.16) over 1871-2023; 0.93 (se 0.08-0.09) and 0.82 (se 0.16-0.22)
  over 1946-2023; the daily tape 1990-2025 reads 1.07 and 1.02 (se 0.3-0.4).
  V1a's band is the post-war value plus or minus 2 se; V1b's is the
  1871-2023 value plus or minus 2 se. The other samples sit inside both.
- Caveats: post-war data alone cannot tell a 5y/1y ratio from 0.4, so V1b's
  power comes from the long sample and V1a is the part with real power. The
  Shiller series is monthly averages, which inflates the ratios by about 1
  per cent; the real estimates use their own sample mean, which biases them
  down by 1 to 4 per cent at these lengths (Richardson and Stock 1989).
- References: Poterba and Summers 1988; Fama and French 1988; Lo and
  MacKinlay 1988; Kim, Nelson and Startz 1991 (mean reversion is a pre-war
  feature); Spierdijk, Bikker and van den Hoek 2012 (faster in turbulent
  periods); Pastor and Stambaugh 2012.
- Before: the leading arm 0.70 / 0.42 and pt-v19 0.53 / 0.24, both failing.
  The bands are the real values plus or minus 2 se, a rule set by the
  data and not by any arm; arms' readings (0.79 to 0.83 and 0.63 to 0.71
  on the candidates) had been seen when the rule was written.

**D1's crisis sector dispersion needs at least ten readings** (owner,
2026-09-26). The row reads a 504-session window only when the VIX is above
30.88 for at least 30 of its sessions (`facts.CRISIS_DISPERSION_MIN_SESSIONS`),
and a cell grades the median over the seeds that read it, with no minimum.
On pt-v20's arms 1 or 2 of a cell's 30 seeds read it (pt-v19 reads 9), so
the verdict was the median of one or two numbers: the leading arm 1.53 and
1.73 (pass), the control on held-out seeds 1.47 and 2.04 (fail), the
W100k15d20 arm 1.44 and 1.88 (fail by 0.001). The band (1.03 to 1.66) is
unchanged. What changes is how many readings stand behind the value:

- A cell whose seeds give fewer than 10 readings of
  `crisis_sector_dispersion` is extended, for this row only, with further
  seeds in blocks of 30 from 1001 upward (1001-1030, 1031-1060, ... up to
  1241-1270: nine blocks, 270 seeds, 300 with the cell's own 30). It stops
  at the first block that brings the count to 10 or more. No seed from 1001 to 1270 has been run on any arm.
- The graded value is the median over every reading, from the cell's own
  seeds and the extension.
- A cell with fewer than 10 readings after all 300 seeds fails the row: the
  model then gives a crisis as long as the tape's 7 readable windows in
  under 1 in 30 two-year histories, which is itself a miss against the tape.
- The cell's other rows, and every other cell's rows, are read as before on
  their own seeds.
- Ten is set before any extended reading is taken. The tape's band was
  drawn from 7 windows; ten readings put the model's median on at least as
  many as the band's own.

**The graded arm.** The grade is of pt-v20 as 0.8.5 ships it, with these
values set in the preset (engine fix/ptv20-vr-feedback 8fccc49 merged onto
fix/ptv20-core; every dial below inert at its default on every other
preset):

    cycle_publication_lag 252, gdp_publication_lag 21,
    unemployment_adjustment_half_life 84, fear_greed_published_inputs 1,
    macro_publication_repricing 1,
    earnings_anticipation_half_life 126, rate_pe_sensitivity 3,
    buyback_payout_share 0.75,
    fair_value_market_linear 1, fair_value_market_share 1,
    opening_market_sigma 0.001, fair_value_market_vol_cap 1.5,
    earnings_cycle_depth 0.2, buyback_yield_cap 0.15,
    treasury_10y_noise 0.038,
    fair_value_vix_discount 0.35, fair_value_vix_half_life 5,
    fair_value_vix_knee 40

- The mechanisms added for V1 and F1: the market's plain factor shocks are
  made permanent (`fair_value_market_share`, `fair_value_market_linear`)
  up to a volatility ceiling (`fair_value_market_vol_cap`), so fear-regime
  excess reverts (Poterba and Summers 1988; Kim, Nelson and Startz 1991;
  Spierdijk, Bikker and van den Hoek 2012); and a transient
  volatility-feedback discount above a VIX of 40, smoothed over 5 sessions
  (French, Schwert and Stambaugh 1987; Campbell and Hentschel 1992).
  `buyback_yield_cap` removes a divergence the grid found (a name near the
  price floor compounding a buyback yield in the hundreds; the index rose
  86-fold in one close on 1 of 90 held-out histories, with or without the
  feedback). `treasury_10y_noise` 0.038 takes R2 from 4.25 to 5.12.
- Chosen on held-out seeds (201-230, 501-530, 801-830; box ptv20vr9), where
  it passes all 40 rows; nearest the edge S1a 0.47 (94 per cent of
  tolerance), B3 1.90x (92), B5 1.86x (89). V1 reads 0.80 and 0.66. The
  grade seeds (101-130, 401-430, 701-730) have not been run on it or on any
  arm with these mechanisms.
- The earlier leading arm (without the V1 and F1 mechanisms) failed R2, F1
  and D1 on the same held-out seeds while passing them on the grade seeds it
  was tuned on; that is why the choice was made on held-out seeds.
- If pt-v20 fails any registered row on the grade, 0.8.5 does not ship on
  this grade. No other arm is graded in its place: a change goes through a
  new registration and a new held-out check first.
