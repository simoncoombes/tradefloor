# pt-v21: the new checks, registered before any mechanism is tuned

Phase 0a of `programme/PLAN-pt-v21.md`, written 2026-10-04 on branch
`ptv21/criteria` (from `preset/pt-v20`). Every row below has a precise
statistic, an estimator that runs on simulator output, real data with its
source and dates, a band from a stated rule, and a minimum seed count. No
dial was moved to write it and nothing ran on AWS. The instrument is
`programme/longrun/ptv21.py`; its real inputs are frozen in
`programme/longrun/data/ptv21/` (README there: URLs and download date).

    python ptv21.py real                     # every real value and band -> data/ptv21/real.json
    python ptv21.py measure --arm pt-v20 --seeds 201-230 --parts open --out DIR
    python ptv21.py measure --arm pt-v20 --seeds 201-203 --years 6 --parts free,mo,xn,ac --out DIR
    python ptv21.py grade DIR/pt-v20

## Read this first: three of the plan's "new" checks already exist

The plan's table calls all seven checks new. Three of them are already
registered rows, with bands, from the thirteenth to fifteenth pt-v20
registrations (`programme/ptv20-registration-13.md`, branch
`registration/thirteenth`; the same rows graded in
`ptv20-registration-15.md`):

| plan check | rows that already read it | R20M in the fifteenth registration's table |
|---|---|---|
| meta-order cost | Q1 to Q9, `tools/calibration/metaorder_curve.py` on sim/r21 | Q6 0.132 / 0.128, all nine in band |
| overnight gaps | G1, G1c, G7, G7b, G8 and the earnings rows G2 to G10 | G1 0.399 / 0.403 |
| skew | CV3, the skew of 21-session index returns | -1.121 / -0.739 |

And the mechanisms behind three of the plan's rows are already built.
R20M, the arm the plan's item 1 starts pt-v21 from, carries:

- `impact_memory_*`, a transient impact kernel of its own (half-lives 12
  and 780 sessions): mechanism 2;
- `market_prehistory_sessions` 504, a pre-history run before day 0, so each
  seed opens from its own state (R20M's VIX before the first session runs
  from 10.0 to 36.5 over seeds 201-230, against 17.66 to 17.77 on pt-v20):
  mechanism 3;
- `overnight_market_share`, `overnight_idio_share`, `overnight_idio_df`,
  an overnight gap: mechanism 4.

Those dials are on `sim/r21` (caaa4c6e) and are NOT on `origin/dev`
(0.9.1): `git grep` finds none of `impact_memory_coefficient`,
`market_prehistory_sessions`, `overnight_market_share`,
`market_vol_leverage` or `book_depth_nesting` in dev's `rust/src`. The
plan's "current readings" (0.04 of a daily sd, every run opening at 17.66,
a first tick of 0.67% against 2.0%) describe pt-v20 as shipped in 0.8.5 to
0.9.1, not R20M.

**Decision needed before phase 1 goes further:** mechanisms 2, 3 and 4
built fresh on `origin/dev` would rebuild what sim/r21 already has. Either
sim/r21 is merged into the pt-v21 base first, or each phase-1 branch has to
say why its switch replaces the r16 to r20 one. The rows below grade either
choice the same way.

What this registration does with the existing rows: it adopts them
unchanged (same statistic, same band, same estimator), and adds rows only
where the plan's statistic is not yet read. The Q rows' instrument is
vendored here as `metaorder_curve.py` (the engine's
`tools/calibration/metaorder_curve.py` at sim/r21 caaa4c6e, last changed
in a982f3a6, sha256 ef06b1b1a9cb85d4f906e82a9c90d41b21d555c2b55eb90b1ae797d9f36a0e59),
so this runner can call it on any build, including 0.9.1, which does not
ship it.

## The rows

pt-v20 is the 0.9.1 wheel from PyPI, measured on the desk on 2026-10-04.
R20M is the fifteenth registration's arm line
(`r13reg/arms/arm-R20M.txt`, fingerprint custom-d4197920) on the sim/r21
build at caaa4c6e (`~/Dev/tf-wt-r21`, built 2026-10-01). Both are LOCAL
smoke readings on held-out screening seeds: 201-230 for OS, 201-203 for
everything else (free histories of 6 years, 12 names for the Q rows, 4
names for AC). They show the estimators compute and roughly where each arm
sits. They are not grades: the minimum seeds column is what a grade needs.

Both arms read the same seeds. The "judges" column is the plan's mechanism
number; a guard is a row the mechanism must not break.

| row | statistic | band | real | rule | min seeds | pt-v20 | R20M | judges |
|---|---|---|---|---|---|---|---|---|
| MO1 | exponent of the day TWAP's IS in f, 1-30% ADV | 0.400 to 0.700 | 0.47-0.51 | lit. | 30 x 12 names | 0.432 | 0.424 | 2 |
| Q1 | print peak exponent in f, half-day (adopted) | 0.400 to 0.700 | 0.47-0.51 | lit. | 30 x 12 | 1.022 FAIL | 0.614 | 2 |
| Q2 | print peak at 10% / sqrt(0.1) (adopted) | 0.300 to 1.000 | about 0.46 | lit. | 30 x 12 | 0.125 FAIL | 0.334 | 2 |
| Q3 | share of final displacement at halfway, day TWAP 10% (adopted) | 0.620 to 0.820 | 0.71 | lit. | 30 x 12 | 0.502 FAIL | 0.709 | 2 |
| Q4 | close / peak, half-day, f >= 3% (adopted) | 0.550 to 0.800 | 0.66 | lit. | 30 x 12 | 0.991 FAIL | 0.662 | 2 |
| Q5 | next close / peak (adopted) | 0.400 to 0.700 | about 0.55 | lit. | 30 x 12 | 0.974 FAIL | 0.564 | 2 |
| Q6 | IS of a day TWAP at 10% ADV, daily sigma (adopted) | 0.100 to 0.210 | 0.105-0.21 | lit. | 30 x 12 | 0.084 FAIL | 0.115 | 2 |
| Q7 | IS day TWAP / one-hour TWAP at 10% (adopted) | 0.500 to 0.900 | about 0.63 | lit. | 30 x 12 | 0.838 | 0.781 | 2 |
| Q8 | IS day TWAP / block at 10% (adopted) | 0.500 to 0.800 | 0.55-0.64 | lit. | 30 x 12 | 0.515 | 0.641 | 2 |
| Q9 | IS day TWAP / block at 3% (adopted) | 0.500 to 0.800 | 0.55-0.64 | lit. | 30 x 12 | 0.527 | 0.656 | 2 |
| OS1 | median VIX at a history's first close | 14.42 to 21.44 | 17.93 | 2 se | 200 | 19.19 | 17.64 | 3 |
| OS2 | sd of log VIX at the first close, across seeds | 0.259 to 0.382 | 0.321 | 2 se | 200 | 0.077 FAIL | 0.381 | 3 |
| ON1 | overnight share of name variance, year one, median name | 0.245 to 0.415 | 0.330 | t(9) | 30 x 252 | 0.112 FAIL | 0.476 FAIL | 4 |
| G1 | the same to 2660 sessions (adopted) | 0.300 to 0.480 | 0.391 | reg. 13 | 90 x 2660 | 0.141 FAIL | 0.469 | 4 |
| G1c | overnight share of the EW index variance (adopted) | 0.300 to 0.600 | 0.461 | reg. 13 | 90 x 2660 | 0.017 FAIL | 0.513 | 4 |
| U1 | unemployment: share of months within 0.2 pt of the 5-year low | at most 0.287 | 0.133 | t(15) | 30 x 21 y | 0.433 FAIL | 0.700 FAIL | 5 |
| U2 | unemployment: 5-year range, points | at most 6.012 | 2.90 | t(15) | 30 x 21 y | 5.384 | 5.471 | 5 |
| U3 | unemployment: lag-12 autocorrelation of the monthly level | -0.147 to 0.682 | 0.268 | t(15) | 30 x 21 y | 0.033 | 0.036 | 5 |
| U4 | unemployment: sd of the 12-month change, points | at most 2.222 | 0.86 | t(15) | 30 x 21 y | 2.569 FAIL | 1.528 | 5 |
| O1 | oil: 5-year log range | 0.469 to 1.781 | 1.125 | t(10) | 30 x 21 y | 1.070 | 0.624 | 5 |
| O2 | oil: share of months within 2% of the 5-year high or low | at most 0.137 | 0.067 | t(10) | 30 x 21 y | 0.067 | 0.033 | 5 |
| O3 | oil: lag-12 autocorrelation of the monthly log price | -0.578 to 0.751 | 0.086 | t(10) | 30 x 21 y | -0.088 | -0.008 | 5 |
| O4 | oil: sd of the 12-month log change | at most 0.557 | 0.272 | t(10) | 30 x 21 y | 0.505 | 0.183 | 5 |
| SK1 | median one-year skew of daily index log returns | -0.287 to -0.014 | -0.150 | 2 se | 30 x 21 y | -0.361 FAIL | -0.092 | 6 |
| SK2 | index sessions under -3% over over +3% (log) | 0.998 to 1.502 | 1.25 | 2 se | 30 x 21 y | 1.123 | 0.927 FAIL | 6 |
| XN1 | flow path: slope of log impact/sigma on log dollar volume at 10% ADV | -0.333 to 0.333 | 0 | lit. | 10 x 40 | nan FAIL | -0.852 FAIL | 7 |
| XN3 | flow path: median impact at 10% ADV, daily sigma | 0.095 to 0.320 | about 0.15 | lit. | 10 x 40 | 0.027 FAIL | 0.026 FAIL | 7 |
| XN2 | book path: the same slope, day TWAP IS at 10% | -0.333 to 0.333 | 0 | lit. | 30 x 12 | -0.120 | -0.074 | 7 (guard) |
| AC1 | per-share IS of a 12-day program over a 1-day one, 10% ADV a day | 1.800 to 5.000 | none measured | lit. models | 30 x 4 | 3.096 | 2.303 | 8 |
| AC2 | co-impact: IS split over 4 labels / one label | 0.800 to 1.250 | 1 | lit. | 30 x 4 | 1.000 | 1.000 | 8, 9 (guard) |

Every row in the table gates. Printed beside them and not gated, as
CRITERIA.md treats the rest of the long-run report: OS3 (the opening
against the model's own year starts), the share of sessions at the
unemployment floor and near the oil clamps, the pooled skew, AC1's IS(3)
and IS(5) and its displacement per day, and XN's least-over-most-liquid
quartile ratio. CV3 and the other G rows (G2 to G10) are adopted unchanged
and graded by their own instrument (`r13reg/grade_all.py`), so they are
not repeated in this table.

## The three band rules

1. **The fixed rule (facts.py).** For a statistic read per real window:
   the median of the windows plus or minus t(n) times their trimmed sd (the
   sd with the window farthest from the median dropped). t(n) is solved so a
   fresh window from the same law falls outside 0.06486 of the time, on
   `facts.band_rule_fixed_false_alarm`'s own null (n + 1 iid normals,
   seed 20260905, 200,000 draws). `ptv21.solve_t` replays those draws in
   the same order and reproduces the engine's table to six digits:
   t(9) 2.982334, t(16) 2.417688. It adds t(10) = 2.830202 and
   t(15) = 2.466699. Used by ON1, U1 to U4 and O1 to O4. The model's value
   is the median over its windows, so this is the lenient reading the
   facts note warns about (a one-window prediction band judging a
   many-window median); it is the house rule for D1 and is kept for the
   same reason.
2. **Real plus or minus 2 bootstrap se.** For a statistic of the whole
   real distribution: B = 2000 resamples of the real units (years or
   windows), seed 20261004, as R1 to R4 and V2 do. Used by OS1, OS2, SK1
   and SK2, whose model side pools many years and is far more precise than
   one real window.
3. **Derived from the literature.** MO1, XN, AC: each derivation is in its
   section, with the numbers taken and where from.

## 1. Meta-order cost (mechanism 2: transient impact on its own kernel)

**Adopted unchanged: Q1 to Q9** and their derivations, which are in
`metaorder_curve.py`'s module note: the certified roster, 60 untraded
sessions to read each name's close-to-close daily sigma, then from tick 15
of the next session an order of f of daily volume (0.3% to 30%) executed
as a block, over an hour (12 slices), half a day (18), a day (36 slices
ten ticks apart) or a day one tick apart, each forked against a twin that
does not trade. Q6, the implementation shortfall of the day TWAP at 10%,
is exactly the plan's "0.04 sd for 10% of ADV in 36 slices"; its band
[0.10, 0.21] is Toth et al.'s Y of 0.5 to 1 times sqrt(0.1) times the
two-thirds of the peak a square-root execution pays on average, and
Almgren et al.'s formula reads 0.152 to 0.165 inside it.

**New: MO1, the exponent of the day TWAP's shortfall in f** (OLS of log
median IS on log f over 1% to 30%). Q1 reads the exponent of the PRINT
PEAK on the half-day schedule; nothing reads how the COST of a day-long
order scales with its size, which is what a schedule optimiser trades off.
Band [0.4, 0.7], the same as Q1's: under the square-root law the average
shortfall is a fixed share (about 2/3, Zarinelli et al. 2015, abstract:
"the impact relaxes to approximately 2/3 of the peak") of a peak that goes
as f^delta, so it inherits delta. delta is 0.47 over all ANcerno metaorders
and 0.51 on large caps (Zarinelli, Treccani, Farmer and Lillo, "Beyond the
square root: evidence for logarithmic dependence of market impact on size
and participation rate", Market Microstructure and Liquidity 1(2), 2015,
arXiv 1412.2152; Table 4 as the Q rows' note quotes it), about 0.5 (Toth, Lemperiere, Deremble, de Lataillade, Kockelkoren
and Bouchaud, "Anomalous price impact and the critical nature of liquidity
in financial markets", Physical Review X 1, 021006, 2011), and 0.6 on the
temporary term (Almgren, Thum, Hauptmann and Li, "Direct estimation of
equity market impact", Risk 18(7) 58-62, 2005: temporary impact
eta sigma (X/VT)^(3/5) with eta 0.142, permanent gamma sigma (X/V)
(Theta/V)^(1/4) with gamma 0.314). Also cited for the decay rows: Bucci,
Benzaquen, Lillo and Bouchaud, "Crossover from linear to square-root market
impact", Physical Review Letters 122, 108302, 2019, and "Slow decay of
impact in equity markets: insights from the ANcerno database", arXiv
1901.05332, 2019, published in Market Microstructure and Liquidity
(abstract: impact
at the end of the day is about 2/3 of the peak, and decays over about 50
days to about 1/2 of the end-of-day value).

- Minimum: 30 seeds x 12 names (the Q rows' own protocol, screening seeds
  2501-2534).
- pt-v20 reads Q6 0.084 on seeds 201-203, 12 names. The README's 0.04 is
  not reproduced on these seeds; it does not say which it used, and both
  are under the floor. Its failures are the decay rows: Q4 0.991 and Q5 0.974 against
  ceilings of 0.80 and 0.70, so impact does not decay, and Q1 1.022 (the
  print peak is linear in f). R20M passes all nine and MO1 (0.424) on the
  same seeds, as it did at grade 15.

## 2. Opening state (mechanism 3: each seed opens from a draw of the stationary state)

**OS1, the median VIX at the first close of a history, and OS2, the sd of
its log, across seeds** (the published VIX the engine reports after the
first session, `macro_fields["vix"]`, on the certified roster).

- Real: the CBOE VIX close on the first trading day of each calendar year,
  1990 to 2026, 37 values (`VIX_History.csv`, CBOE, downloaded 2026-10-04).
  A one-year run is a year that starts at an arbitrary point of history,
  and a year's first day is such a point; at a year's spacing the VIX's
  memory (half-lives of weeks to months) leaves the 37 nearly independent.
- OS1: real median 17.93, se 1.75, band [14.42, 21.44]. OS2: real sd of
  log 0.321, se 0.031, band [0.259, 0.382]. (All days 1990-2025 read p10
  12.1, median 17.6, p90 28.6, sd of log 0.34, so the year starts are
  representative.)
- OS3, reported: the two-sample Kolmogorov-Smirnov distance between the
  model's openings and its own VIX at the first close of years 2 to 21.
  A stationary draw makes them the same law, so p >= 0.01 is the
  mechanism's construction check. It is reported and not gated, because
  OS1 and OS2 already grade the outcome.
- Minimum: 200 seeds for OS1 and OS2. The opening costs one session, so
  seeds are cheap, and the model's sd needs about 200 to carry under half
  the real se (relative se of an sd 1/sqrt(2(n-1))).
- pt-v20: OS1 19.19 (in), OS2 0.077 (FAIL) on 30 seeds. The state before
  the first session is 17.66 to 17.77 on every seed; the first close
  spreads it to 16.9 to 24.1, a quarter of the real spread. R20M, whose
  pre-history draws the opening, reads OS1 17.64 and OS2 0.381 (just in),
  and OS3's distance to its own year starts is small (p 0.88 on three
  histories).

## 3. Overnight gaps (mechanism 4)

**ON1, the overnight share of name variance in year one, median name.**
Night g = log(first print / previous close), session i = log(close / first
print), share = var(g) / (var(g) + var(i)) per name over the first 252
sessions from the opening, median over the forty, median over seeds. The
first print is the price after the session's first tick: the earliest
price an order sent before the bell can fill at, which is what a stop
behind an overnight position meets. (`open_market` itself moves nothing on
pt-v20, so the engine's session open is the last close.) The free history
runs each day as `open_market`, one tick, 389 ticks with the clock
advanced, `close_market`; `ptv21.selfcheck` confirms this reproduces
`run_days(1)` to the bit, and `meta.json` records it per arm.

- Real: `programme/overnight-target.json` (the engine's
  `tools/calibration/overnight_band.py`): the reference panel's forty names
  of `facts.REAL_MARKETS_WINDOWS`, ten 253-bar windows 2015-07 to 2025-07,
  Yahoo daily bars, adjusted. Nine non-crisis window medians, 0.263 to
  0.405, median 0.330, trimmed sd 0.0284; the crisis window 2019-07 to
  2020-07 reads 0.503 and is excluded, as on the panel.
- Band, the fixed rule at t(9): [0.245, 0.415].
- **Adopted unchanged: G1** (the same share over up to 2660 sessions,
  median name, band [0.30, 0.48], real 0.391) and **G1c** (the share on the
  equal-weighted index, band [0.30, 0.60], real 0.461), both from the
  thirteenth registration, read on the forty with EDGAR 8-K item 2.02
  dates (`r13reg/deps/common_eg.py`; ON1 uses the same definitions).
- G1c is the row that needs a common overnight factor. A noisy first
  print can lift the per-name share without any gap the index sees; the
  index share cannot be met that way.
- Minimum: 30 seeds x 252 sessions for ON1; G1 and G1c as registered (90
  histories x 2660 sessions).
- pt-v20: ON1 0.112, G1 0.141, G1c 0.017, all FAIL. The first tick's sd is
  0.85% of a day's 2.28% (median name), in line with the README's 0.67%
  against 2.0%. R20M reads G1 0.469 and G1c 0.513 (in) but ON1 0.476,
  above the one-year ceiling: its first year is gappier than its later
  ones. Grade 15 read G1 0.399 on 90 histories, so three seeds say
  little here.

## 4. Unemployment and oil (mechanism 5: #172 anchor, #170 and #171 oil)

Every statistic is read per 60-month window. The model's daily series is
averaged over 21-session months after the burn year, and a 21-year history
gives four windows. Windows are anchored at the latest month and walk back
(`facts.INDEX_TAIL_WINDOWS`' rule).

**Unemployment** (FRED UNRATE, monthly, seasonally adjusted, 1948-01 to
2026-09; fifteen windows from 1951-10).

| row | statistic | real median | trimmed sd | windows range | band, t(15) 2.4667 |
|---|---|---|---|---|---|
| U1 | share of months within 0.2 points of the window's own low | 0.133 | 0.062 | 0.033 to 0.300 | at most 0.287 |
| U2 | the window's range, points | 2.90 | 1.26 | 1.3 to 11.3 | at most 6.01 |
| U3 | lag-12 autocorrelation of the monthly level | 0.268 | 0.168 | -0.32 to 0.45 | -0.147 to 0.682 |
| U4 | sd of the 12-month change, points | 0.86 | 0.55 | 0.32 to 3.54 | at most 2.22 |

The rule's lower edge is below zero on U1, U2 and U4, so those rows have a
ceiling only. U1 is the row #172 is about: a level that runs to its floor
and stays there reads a large share. U3 catches the other failure, a level
that never moves (a window held at a clamp reads 1.0, by definition in
`acf_level`). The 2020 spike (to 14.8) sits in the 2016-2021 window and is
kept: recessions are part of the real distribution, and the trimmed sd
drops the most extreme window anyway.

Reported, not gated: the share of all sessions at the model's 2.5 floor.

**Oil** (FRED WTISPLC, monthly average WTI, 1946 to 2026-08; ten windows
from 1976-09, which leaves out the administered prices before 1974).

| row | statistic | real median | trimmed sd | windows range | band, t(10) 2.8302 |
|---|---|---|---|---|---|
| O1 | log range of the window | 1.125 | 0.232 | 0.49 to 1.48 | 0.469 to 1.781 |
| O2 | share of months within 2% (log) of the window's own high or low | 0.067 | 0.025 | 0.033 to 0.250 | at most 0.137 |
| O3 | lag-12 autocorrelation of the monthly log price | 0.086 | 0.235 | -0.38 to 0.43 | -0.578 to 0.751 |
| O4 | sd of the 12-month log change | 0.272 | 0.101 | 0.15 to 0.48 | at most 0.557 |

The rule's lower edge is below zero on O2 and O4 as well, so those two
have a ceiling only. O2 is #170's row: a price that ends at a clamp sits within 2% of its own
high or low for the rest of the window. Reported, not gated: the share of
sessions within 0.5% of the clamps (150 and 36.8). #171 (the asymmetric
pass-through) has no row of its own here: it acts through inflation, and
it is graded by what it does to the index drift and the rates rows that
already exist (B8, R1 to R4, H rows).

- Minimum: 30 histories x 21 years (120 windows a series).
- pt-v20, three 6-year histories (one partial window each, so a smoke
  reading only): U1 0.433 FAIL, U4 2.569 FAIL; 53% of sessions sit at
  the 2.5 floor. Oil passes all four (O2 0.067); 8% of sessions are within
  0.5% of a clamp. #170 was filed on pt-v18's oil before `oil_supply_response`;
  a 21-year read decides whether it is still live. R20M: U1 0.700 FAIL
  (62% of sessions at the floor), the other seven in. #172 is open on both.

## 5. Return skew (mechanism 6, #174)

**SK1, the median one-year skew of daily index log returns** (moment
skew of each 252-return year of the cap-weighted roster index, years 2 to
21 of each history, median over all of them). **SK2, sessions under -3%
over sessions over +3%** (log returns, pooled over the same years).

- Real: the tape, ^GSPC 1990-01-02 to 2025-07-31 (`tape.json.gz`).
  SK1: 35 non-overlapping 252-return years anchored at the last return,
  median -0.150, se of the median 0.068 (bootstrap over years), band
  [-0.287, -0.014]. SK2: 115 down against 92 up, 1.25, se 0.126 (bootstrap
  over 36 calendar years), band [0.998, 1.502]. The pooled daily log-return skew (-0.36)
  and the 21-session skew (CV3, adopted unchanged, band [-1.30, -0.35])
  sit beside them.
- Why a band on the median and not a one-year band: the one-year row
  `index_skew` was refused in `results/new-rows.md` because the tape's own
  years spread from -0.68 to +0.26 and a thirty-seed one-year median could
  not tell the sign. Twenty years of 30 histories give 600 model years.
  If the model's years spread as the tape's do (sd 0.39 over the 35), the
  median of 600 is known to about 0.02.
- Both rows are signed: SK1 excludes zero, so a symmetric or right-skewed
  index fails, which is #174's complaint. SK2 includes 1.0 at its floor.
- Minimum: 30 histories x 21 years.
- pt-v20 reads SK1 -0.361 (FAIL, too left-skewed on these 12 years, two of
  three histories at 35% volatility) and SK2 1.123 (in). R20M reads SK1
  -0.092 (in) and SK2 0.927 (FAIL: more big up days than down). #174's
  +0.12 was measured on pt-v18. Twelve model years is noise on SK1, and
  SK2's 51 against 55 has a se near 0.18, so R20M sits under half a se
  below the floor.

## 6. Cost across names at equal participation (mechanism 7: #182, #166)

**XN1, the flow path.** On the certified roster after 60 warm sessions, a
standing buy of 10% of each name's daily volume over one session
(`flow_per_tick` of avg_volume x 0.1 / 390 a tick), forked against a twin
without it; impact = log(close / twin's close) in the name's daily sigma.
The row is the OLS slope of log impact on log dollar volume (avg_volume x
initial price) across the forty, median over seeds. It also fails when
more than a tenth of the names read an impact at or below zero (#166's
cent grid), since under every cited law 10% of a day's volume moves every
name by a tenth of a sigma or more.

**XN3, the flow path's level:** the median over names of the same impact.
Band [0.095, 0.32]: Q2's registered band for the peak at 10% (0.3 to 1.0
times sqrt(0.1)), since a program that runs to the close peaks at the
close.

**XN2, the book path:** the same slope for the day TWAP's shortfall at 10%
across the Q rows' 12 names (read from the same run). It guards that the
agent-facing book, which #182 does not touch, stays in band while the flow
law changes.

Band for both slopes: [-1/3, +1/3]. Kyle and Obizhaeva, "Market
microstructure invariance: empirical hypotheses", Econometrica 84(4)
1345-1404, 2016, equations (17) and (18): cost in units of sigma at a
fixed fraction Q/V of volume is kappa_0 W^(-1/3) (the spread) plus either
kappa_I W^(1/3) Q/V (the linear model) or kappa_I (Q/V)^(1/2) (the
square-root model), W = sigma P V the trading activity. "The square root
is the only function for which invariance leads to the empirical
prediction that impact costs (measured in units of returns volatility)
depend only on bet size as a fraction of bet volume" (p. 1359). They find
both models explain their 400,000 portfolio-transition orders. So every
term of either invariant cost function has an elasticity in W between
-1/3 and +1/3 at equal participation, and so does any sum of them. Across
the certified forty, dollar volume and W rank the same (sigma varies far
less than P V). Toth et al. 2011 and Bucci et al. 2019 also report the
square-root prefactor Y roughly the same across stocks.

- Minimum: 10 seeds x 40 names (XN1, XN3); XN2 as the Q rows.
- pt-v20: XN1 FAILS on the zero rule (15 of 120 names at or below zero);
  over the positive ones the slope is -0.917, #182's -0.92 again.
  XN3 0.027 FAIL: the median name barely moves while the thinnest
  move 2 to 4 sigma. XN2 -0.120 (in): the book path is near
  depth-neutral already. R20M is the same on the flow path (XN1 -0.852,
  XN3 0.026, both FAIL) and in on the book path (XN2 -0.074): the r16 to
  r20 work never touched `flow_per_tick`.

## 7. Adaptive counterparties (mechanisms 8 and 9)

### AC1, escalation of sustained one-way flow (tier 1), gated

**Statistic.** One program buys 10% of daily volume every day for 12 days,
a day TWAP of 36 slices each day (the Q rows' `day` schedule), forked
against a twin that does not trade, on four names spread by market cap.
IS(n) is the per-share shortfall of the first n days against the twin's
mid when each slice is sent, in the name's daily sigma. AC1 = IS(12) /
IS(1), median over (seed, name). IS(3) and IS(5) are reported.

**What the literature supports, and what it does not.** The square-root
law applies to the TOTAL size of a metaorder (Toth et al. 2011; Kyle and
Obizhaeva 2016, eq. 18), and Bucci et al. (2019, arXiv 1901.05332) measure
how impact decays between days. Together they predict that the per-share
COST of a longer program at the same daily rate rises, and that the price
displacement PER DAY falls (concavity). No study measures IS(12)/IS(1)
directly. So the band is derived from three models that each fit those
facts, with the decay kernel G(d) = 1/3 + (0.55 - 1/3) d^(-beta) of the
peak d closes after an order's day (0.55 at the next close, Q5's centre;
1/3 of the peak in the long run, Bucci's 2/3 times 1/2), beta 0.3 to 1:

| model | IS(3)/IS(1) | IS(5)/IS(1) | IS(12)/IS(1) |
|---|---|---|---|
| square root of the decay-weighted cumulative volume (most concave) | 1.27 to 1.28 | 1.42 to 1.48 | 1.78 to 1.95 |
| one metaorder, square root, no decay | 1.73 | 2.24 | 3.46 |
| linear superposition of daily impacts with the kernel (no concavity) | 1.77 to 1.80 | 2.42 to 2.55 | 4.43 to 5.01 |

Band [1.8, 5.0]: the most concave and the least concave of the three.
Co-impact (Bucci et al. 2020, below) says the market reacts to net flow,
concavely, so the truth sits inside, nearer the lower edge.

**Falsifiable for tier 1:** the inferring maker must keep AC1 inside
[1.8, 5.0] while Q4 and Q5 come into band. The design note's premise
needs correcting before tier 1 is built: its table measured the price
displacement per day (341 to 430 bp, 1.26x), and under the square-root law
that number should FALL with the program's length, not rise. A maker tuned
to make it rise would push the market away from the literature. The
displacement-per-day ratio is printed beside AC1 for that reason.

- Minimum: 30 seeds x 4 names.
- pt-v20 reads AC1 3.096 (in; IS(3) 1.822, IS(5) 2.246), spread 1.5 to 5.7 over names.
  It escalates because its impact barely decays (Q4 0.99), not because
  anything adapts. Displacement per day at 12 days over day one: 0.909.
  R20M, whose impact decays (Q4 0.662), reads AC1 2.303 (IS(3) 1.49, IS(5)
  1.79) and a displacement per day of 0.268 at 12 days: concave, the
  literature's direction, and with no adaptive maker at all.

### AC2, co-impact of identical flow, gated

**Statistic.** The first day of the same program split across four agent
labels sending a quarter of each slice together (in label order), against
one label sending the whole: the four's mean per-share IS over the single
label's, median over (seed, name). Band [0.8, 1.25].

- Derivation: Bucci, Mastromatteo, Eisler, Lillo, Bouchaud and Lehalle,
  "Co-impact: crowding effects in institutional trading activity",
  Quantitative Finance 20(2) 193-205, 2020: "the market chiefly reacts to
  the net order flow of ongoing metaorders, without individually
  distinguishing them". So the ratio is 1. The tolerance, a quarter in
  either direction (log-symmetric), is a chosen one, of the size
  CRITERIA.md uses for crash depth (30%): a user would notice more.
- It guards tier 1 and tier 2: a maker that infers toxicity per label, or a
  population that reacts to one label's pattern, must not make four
  labels with the same net flow cheaper or dearer than one.
- Minimum: 30 seeds x 4 names.
- pt-v20 and R20M both read 1.000 (one book, one queue).

### AC3, edge decay as capital trades it (tier 2): directional, no number

No band exists. McLean and Pontiff, "Does academic research destroy stock
return predictability?", Journal of Finance 71(1) 5-32, 2016: across 97
predictors, portfolio returns are 26% lower out of sample and 58% lower
after publication, so about 32% attributable to informed trading after
publication. That is a scale, not a band: "publication" is not a
population of k agents with capital C, and no mapping between them is
measured. So the criterion is directional, on populated mode:

- (a) k = 1, 2, 4, 8 identical agents trading one price-only rule (C4b's
  one-day reversal and five-day momentum), 30 paired seeds: the per-agent
  net excess over buy-and-hold falls with k (OLS slope on k below zero,
  one-sided p < 0.05);
- (b) the same rule with the tier-2 population present against isolated
  mode, same seeds: lower in populated mode when the population holds an
  archetype that trades the same signal (the trend follower for momentum,
  the mean reverter for reversal), one-sided paired p < 0.05;
- falsifier: if neither moves, the population does not crowd and the
  mechanism ships off under the plan's drop rule.

### AC4, front-running of persistent flow (tier 2): directional, no number

No transferable band exists. van Kervel and Menkveld, "High-frequency
trading around large institutional orders", Journal of Finance 74(3)
1091-1137, 2019: HFTs lean against an institutional order at first and
trade with it once it persists, for the most informed orders;
institutional cost is 46% lower when HFTs lean against by one sd and 169%
higher when they trade with it. Those are in units of HFT positions, not
of anything the engine has. Criterion, populated mode, 30 paired seeds, a
program of 10% of daily volume a day for 5 days:

- (a) predictable schedule (same slice times and sizes every day): IS
  populated minus IS isolated > 0, one-sided paired p < 0.05;
- (b) that excess is larger than for the same daily quantity on a
  randomised schedule (slice times and sizes from the agent's own stream),
  one-sided paired p < 0.05;
- (c) the flow detector's mean P&L on the predictable program is positive;
- reported: the excess as a share of isolated IS, beside the 169%.

AC3 and AC4 have no runner yet: they need tier 2's `Population`. Their
estimators are the paired comparisons above and need nothing new beyond
`tf.evaluate` in both modes.

## Reproducing the local readings

The two columns came from these commands (desk, 2026-10-04, 3 workers;
pt-v20 about 7 CPU minutes, R20M about 75, mostly its 504-session
pre-history and the metaorder forks). Their summaries are in
`results/ptv21-local/` (grade and meta JSON per arm; the raw histories
stay out of the repo).

    # pt-v20: the 0.9.1 wheel from PyPI in a fresh venv
    python ptv21.py measure --arm pt-v20 --seeds 201-230 --parts open --out DIR
    python ptv21.py measure --arm pt-v20 --seeds 201-203 --years 6 --parts xn,ac,free,mo --out DIR
    python ptv21.py grade DIR/pt-v20 --json results/ptv21-local/grade-pt-v20.json
    # R20M: the sim/r21 build at caaa4c6e
    ~/Dev/tf-wt-r21/.venv/bin/python ptv21.py measure --arm-file ARM-R20M --seeds 201-230 --parts open --out DIR
    ~/Dev/tf-wt-r21/.venv/bin/python ptv21.py measure --arm-file ARM-R20M --seeds 201-203 --years 6 --parts xn,ac,free,mo --out DIR
    ~/Dev/tf-wt-r21/.venv/bin/python ptv21.py grade DIR/R20M --json results/ptv21-local/grade-R20M.json

ARM-R20M is `programme/r13reg/arms/arm-R20M.txt` on branch
`registration/fifteenth`.

## Seeds for the grade

Every reading above used held-out screening seeds (201-230 and 201-203,
the fifteenth registration's held-out set). A pt-v21 grade needs fresh
exam seeds laid out by `r13reg/freshseeds/` as before; none is named here.

## Open risks

- Rows that judge weakly: U2, U4, O4 (ceiling only), U3 and O3 (wide), and
  O1 to O4 may all pass a pinned oil price on a 6-year read. The macro
  rows need the 21-year histories to decide #170.
- ON1 can be met by first-print noise; G1c is the row that cannot.
- SK1 at 600 model years is precise, but its real se (0.068) rests on 35
  years, and the band is only 0.27 wide.
- AC1's band is model-derived, not measured. It is two-sided on purpose:
  the shipped engine already reads inside it for the wrong reason (no
  decay), so tier 1 is judged on keeping AC1 in band while Q4 and Q5 come
  in, not on raising AC1.
- XN3's band borrows Q2's (the book path's peak) for the flow path; the
  two paths are separate mechanisms in the engine and the literature
  measures neither one apart from the other.
