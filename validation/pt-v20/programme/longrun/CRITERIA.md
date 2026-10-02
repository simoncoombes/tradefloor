# Pass criteria for a preset, framed on what a user would notice -- ADOPTED by the owner, 2026-09-23

Seventeen criteria: A1-A3, B1-B8, C1-C3, C4a, C4b, D1. Fifteen were adopted
on 2026-09-23 (counted as sixteen when first proposed and corrected the same
day, with no verdict changes). C4a and C4b were added on 2026-09-24, after the
mean-reversion investigation (`programme/meanrev-edge-ptv19-2026-09-24.md`);
section C4 below says why and what pt-v19 reads on them.

Three different counts sit near this one and are not it:

- **17 long-run criteria**: this page, graded by `criteria.py`, carried by a
  preset record as `long_run`.
- **19 graded rows** of the one-year realism table: 14 shape statistics, the
  index drift (level), 3 crisis rows (the fear gauge after 1% and 3% falls,
  and how often the index falls 3%) and the crisis sector dispersion. D1 is
  "all 19 in, on all four cells".
- **18 statistics** `tf.facts.measure()` reads against `facts.REAL_MARKETS`:
  the 19 above less the crisis sector dispersion, which
  `facts.crisis_statistics` reads.

Replaces the first proposal in README.md ("no row beyond 3 tape SE; the real
value inside the model's 10th-90th band"), which the owner's ruling makes too
strict: "we're looking for realism to a degree but we'll never mimic exactly".
The test of a gap here is whether it would mislead a user: a bot learning an
edge that does not exist, crashes half as violent as real ones, a market stuck
in fear. Tolerances are stated as ratios a person can read, not standard
errors. Everything else the instrument measures is REPORTED beside the verdict
and does not gate.

Scored from existing boxes: pt-v19 and the candidate LMN-Q25A375 from
`calm-regime/box-calm4` (engine fed06b3, 30 seeds x 21 years and both replays),
certification from `calm-regime/certgrade-calm4.txt`, news edge from
`news-speed/`.

## A. Crashes feel real, given the fear (2008 and 2020, the real VIX imposed)

    criterion                                        real        pt-v19            candidate
    A1 worst month's volatility within 30%           84 / 95     46 / 38  FAIL     81 / 67  pass (2020 at -29%)
    A2 maximum drawdown within 30%                   0.57/0.34   0.28/0.25 FAIL    0.41/0.34 pass (2008 at -29%)
    A3 peak stock correlation within 0.15            0.75/0.87   0.66/0.63 FAIL    0.75/0.73 pass (2020 at -0.14)

## B. The long run looks like a real market (20 years, free running)

    criterion                                        real        pt-v19            candidate
    B1 time with VIX above 30, within 1/2x to 2x     8.2%        19.4%  FAIL       8.1%   pass
    B2 fear-spell length above 30, 1/2x to 2x        22          42     pass       25     pass
    B3 20% bear markets per decade, 1/2x to 2x       1.12        2.18   pass (1.95x) 1.35  pass
    B4 10% corrections per decade, 1/2x to 2x        3.65        7.15   pass (1.96x) 6.75  pass (1.85x)
    B5 sessions under -5% per decade, 1/2x to 2x     6.2         1.8    FAIL       4.0    pass
    B6 time with VIX under 15, 1/2x to 2x            32.6%       17.3%  pass (0.53x) 28.1% pass
    B7 index volatility within 20%                   18.1        17.7   pass       16.6   pass
    B8 long-run index return within 2 points of      6.25        3.8    FAIL       6.3    pass
       the honest target (earnings growth, not P/E)

## C. No illusions a bot could learn

    criterion                                        target      pt-v19            candidate
    C1 crash rate steady: years 3-21 within          1.0x        0.86x  pass       1.02x  pass
       2/3x to 1.5x of years 1-2
    C2 VIX ceiling hits, at most 1 of 30 histories   0           2      FAIL       0      pass
    C3 edge from reading a headline 5 ticks late,    ~0          +136bp FAIL       ~+10bp pass
       under 20 bp
    C4a 65-minute lag-1 autocorrelation of prints    ~0          --                -0.187 FAIL
       >= -0.05, or Roll spread <= 2x quoted                                        (5.0x)
    C4b price-only rules on the published suite:     ~0          --                +13.6 (18/20) FAIL
       each <= +5 points over buy-and-hold at the
       median and <= 14 of 20 markets beaten

The C4 column is the shipped pt-v19 (the candidate column became it at 0.8.0),
measured after the engine applies an agent's fills once (engine branch
`fix/agent-flow-once`). The pt-v19 column's composition was retired before C4
existed and was not measured.

## D. The one-year realism table

    D1 every ruled band in, on all four cells        --          pass              pass

## Result

    pt-v19      fails 8 of 15: A1, A2, A3, B1, B5, B8, C2, C3 (C4 not measured)
    candidate   passes 15 of 15; nearest the edge: A1 2020 (-29%), A2 2008
                (-29%, accepted by the owner), A3 2020 (-0.14), B4 (1.85x)

With C4 (2026-09-24), the shipped pt-v19 (the candidate above) fails 2 of 17:
C4a and C4b. Both failures are the market's, not the harness's: C4b's worst
rule is the one-step reversal, which feeds on the tape C4a measures, and the
other failing rule is five-day momentum at daily cadence, which feeds on the
suite opening every name far from fair value. Both are pt-v20's to fix. Until
it ships, C4 is recorded on the preset and reported by the server, and pt-v19
stays the default on the fifteen it was adopted under
(`programme/results/c4/RESULT.md`).

## Reported, not gated

The certification's VIX persistence rows (the candidate's 504 row is refused
below by two seeds; pt-v19's held-out row is refused above), the mechanism
certificate (candidate 10 and 9 of 10; pt-v19 9 and 9), the VIX half-life by
level, same-day fear on small falls (inside the tape's own range across
decades), and every other row of the long-run report with its z score. These
stay on the record and a regression in them is investigated; they do not by
themselves stop a preset.

## Why these tolerances

- 30 per cent on crash size and 0.15 on correlation: the owner accepted a 2008
  drawdown at 71 per cent of the real one; 30 per cent is that line, applied
  the same way to every crash measure.
- 1/2x to 2x on frequencies and shares: the tape's own rates move by that much
  between decades (measured on the tape, by calendar decade 1990-2025:
  small-fall fear 0.77 to 1.20; share of sessions with the VIX above 30 from
  4.3 to 14.6 per cent; below 15 from 18.5 to 45.9 per cent), so a model
  inside that range is inside what the real market itself has done.
- 20 per cent on volatility and 2 points on return: a user sizing positions or
  judging a long backtest would notice more than that.
- The C rows are hard lines because each is an illusion: an edge, a runaway, a
  start-up artefact.

## C4: no price-only edge (added 2026-09-24)

A spec mean-reversion rule beat buy-and-hold on 20 of 20 markets of the
published suite, by a median 42 points in 60 days. None of the fifteen saw
it: every one is a statistic of an untraded market, none looks below daily
frequency, and C3 asks only what a headline is worth. The investigation found
two causes, one in the harness and one in the market, and C4 has a part for
each.

**C4a, the tape.** On the certified roster, `Universe.random(40,
seed=111)`, 252 sessions untraded, eight seeds from 101: the lag-1
autocorrelation of 65-minute print returns (the price at the end of each of
the six 65-tick steps of a session, as `tf.evaluate` samples it), the median
over names, must be at or above -0.05. Or, equivalently for the reader, the
spread those returns imply (Roll 1984, 2 sqrt(-cov)) must be at most twice
the median quoted spread.

- Anchor: where quotes track the efficient price, trade-price noise is about
  the half-spread (Roll 1984). For a large cap with a 1 bp half-spread and 50
  to 60 bp of hourly volatility that gives an hourly autocorrelation of about
  0; for a 20 bp small cap about -0.03. Derived, not measured: the design repo
  holds no intraday tape, and a year of one-minute bars for the certified
  forty would replace the derivation with a measured band.
- pt-v19: -0.187 (seed se 0.003), Roll 56.3 bp against a quoted 11.2 bp,
  5.0x. **FAIL.** The print trails the model price by a gap that overshoots;
  quoting the maker's book around the model price takes it to about -0.02 in
  the investigation's scratch build. pt-v20's job.

**C4b, the user's view.** The simple price-only rules, run through
`tf.evaluate` on tf-suite-2026.1 (20 markets of 60 days, 20 companies,
pt-v19, six 65-tick steps a day, $1M, leverage 2): mean reversion and
momentum at a one-step lookback, a one-day lookback at step cadence, and a
five-day lookback at daily cadence. Against buy-and-hold in the same market,
each rule's median excess must be at most +5 points and it must beat
buy-and-hold in at most 14 of the 20.

- Anchor: real price-only edges at daily cadence are within about a point per
  60 days. The certified forty give -0.3 bp a day for the spec's shape and
  -1.7 +/- 2.3 for Lo-MacKinlay, and the modern short-term reversal factor
  earns 0.15% a month before costs. +5 points is about 2.5 standard errors of
  the median over 20 markets (the seed sd of a rule's excess is 7 to 8
  points); a fair coin beats buy-and-hold in 15 or more of 20 with p = 0.02.
- It runs through the harness on purpose. C4a cannot see an agent's own
  flow; C4b can, and that is what it caught.

pt-v19, median points over buy-and-hold (markets beaten of 20), on the
engine as shipped at 0.8.1 and with an agent's fills applied once:

    rule                               0.8.1            fills once
    mean reversion, 1 step            +94.6 (20)  FAIL  +13.6 (18)  FAIL
    mean reversion, 1 day             +42.1 (20)  FAIL   +0.5 (10)  pass
    mean reversion, 5 days, daily     -14.1  (1)  pass  -15.3  (1)  pass
    momentum, 1 step                  -54.6  (0)  pass  -70.7  (0)  pass
    momentum, 1 day                   -25.3  (1)  pass  -42.4  (0)  pass
    momentum, 5 days, daily           +10.9 (19)  FAIL   +7.1 (17)  FAIL
    (oracle, reported)                +35.1 (20)        +26.7 (20)

The one-day reversal, the suite's spec, was the harness and nothing else. The
one-step reversal is the tape (C4a). Five-day momentum is the start-up
transient: the suite's markets open every name's mispricing at about four
times its stationary spread, so for the first months every name drifts toward
fair value in a direction a five-day trend can read.

**Scored** by `criteria.py --c4` from `c4.py`'s report (C4a in about ten
seconds and C4b in about thirty on eight workers, so no box is needed).
