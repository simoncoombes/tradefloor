# Changelog

## Unreleased

**Every `Universe.random` roster re-rolls**; pin 0.6.2.

**A new preset, pt-v18, returns five first moments the model injected and
grows fair value with nominal output**, taking the equal-weight index from
about -16 per cent a year to roughly flat. The default preset is where it
was.

**The index drift and two fear rows are graded**, and the certified set
splits into shape, level and crisis.

**A run can commit to the state it held at the end of every day**, and a
sampled check verifies k days for that cost.

**The per-name volume states follow the roster**, moving the volume-idio
generator of any run that lists or delists.

**Every print decomposes into the shock that arrived, the depth that
absorbed it and the breaker's share**, with a counterfactual against
unbounded depth on request.

**Every draw has an address**, a substitution table replaces one, a
surgery installs a window of them, and a second `run_days` call numbers
days from the engine's own counter.

**Each draw's effect on a target is measured**, and a shadow run solves a
real year for the draws behind its closes.

**The browser build compiles again**; 0.6.2 shipped it broken by a crate
rename its build script did not follow.

**A mechanism is a specification the engine's Rust is generated from**,
checked for its draw effect and proven inert.

**A day's move decomposes to the draws that seeded it**, and every node
replays from the state the day started in.

<!-- release-note-ends -->

### The graded level row and the split of the certified set

The panel's first moment, `index_drift_pct`, was measured and reported
and deliberately not graded, because no band for it had been derived.
Its band is now derived from real series through `tools/shadow/data.py`,
which keeps the unadjusted close beside the adjusted one and accepts
sessions before 1970: `^GSPC` from 1950 gives the cap-weighted price
return, +7.75 a year as the mean of 75 calendar-year log returns with a
standard error of 1.87; `RSP` on its unadjusted close against `^GSPC`
gives the equal-weight premium in price terms, -0.38 over 22 calendar
years with a standard error of 1.24, and `^SPXEW` cross-checks it at
-0.85. The centre is 7.37 with a standard error of 2.25, and the band is
the centre plus or minus the larger of two standard errors and the
model's own resolution at thirty seeds, so 2.9 to 11.9. The derivation
is `tools/calibration/index_band.py` and the provenance in
`facts.REAL_MARKETS_PROVENANCE` is three URLs and a fetch date. A band
chosen so the current model passes was refused.

The certified set splits along `facts.SHAPE`, `facts.LEVEL` and
`facts.CRISIS`. The fourteen shape rows fill `envelope.CERTIFIED` and a
green panel means what it meant. The level row is read as a thirty-seed
mean (`facts.AGGREGATE`), because its seed standard deviation of about
6.5 points a year is a large fraction of its band, and its certified
value goes in `envelope.CERTIFIED_LEVEL`, held red until the model earns
it; `envelope.score` reports the counts per group, `envelope.certified`
and `describe_simulator` carry the groups and name a row whose certified
value is not yet measured, and `report` prints the level in its own
section. The rows stay structural in the loss until `facts.SEED_SD`
carries their seed sds on the pinned protocol, at which point they join
the live targets so that a search charges for the level it moves.

The level row's protocol varies the roster with the seed,
`facts.LEVEL_PROTOCOL`: a level is a property of the roster as much as
of the model, the held roster opens 0.78 of a population standard
deviation above fair value, and its first-year handicap exceeds the
band's half-width, so thirty market seeds on one roster would certify
that roster. The certified value is the mean over seeds 101 to 130 of a
run on `Universe.random(40, seed=s)` with market seed `s`.

### The fear gauge rows

`fear_gauge_dn1` and `fear_gauge_dn3` read the median change in the
volatility index on sessions whose cap-weighted close-to-close return is
at or below -1 and -3 percent. Two rows because the defect is a channel
that saturates on the down side: a graded row on the -3 percent bucket
alone would have scored it as mildly out of band for three eras, and
mildly out of band is the verdict that gets tuned at rather than fixed;
the -1 percent row reads inside its band on the same model. The -3
percent row is pooled across the certification seeds, because about a
third of 252-day runs hold no such session, and it reports its session
count beside its value. `measure` records each day before the close, so
the macro row for day d holds the gauge the session opened with and the
change answering session d is row d+1 minus row d; `fear_statistics`
pairs them that way and a test pins the convention with two correlations
that swap under the wrong pairing. The bands come from `^VIX` against
`^GSPC` since 1990 through `tools/calibration/fear_band.py`: the -1
percent row by the panel's shared rule over its ten windows, 0.70 to
4.03; the -3 percent row across every window since 1990 holding at
least five such sessions, 2.60 to 9.58, with the pooled median of +5.73
agreeing with the +6.03 the engine cites from FRED. A -5 percent bucket
and the up-side response are reported without bands. Every free-run
figure is held to its free-run value and never to a solved one.

### A generated roster opens at its own fair value

`Universe.random` drew a price, then drew a multiple around the sector
anchor and set earnings to the price divided by it. The multiple's scatter
ran uniformly from 0.55 to 1.6 of the anchor, which is symmetric in the
ratio and biased in the log of it: its mean in log is +0.029, so the
implied earnings came out systematically low and the valuation built on
them came out below the price it was built from. Book value had the same
shape against the loss-maker path. A drawn roster therefore opened above
fair value, at a cross-sectional mean log deviation of +0.050 over two
hundred rosters of forty names, with 58 per cent of names above fair
value, and the market spent a year unwinding that on a 60-day half-life.

Both ratios are now drawn log-uniformly between reciprocal endpoints, so
their mean in log is zero and a name is as likely to open cheap as dear.
The widths are preserved to within two per cent, so the cross-section is
as dispersed as it was and no longer tilted. Over two hundred rosters the
day-zero mean falls from +0.050 to +0.013 and the fraction above fair
value from 58 to 51 per cent. What remains is the valuation model's
neutral discount rate sitting 56 basis points below where the economy
opens, which compresses every profitable name's multiple by about one per
cent. That is a property of the opening macro state rather than of the
generator, and correcting it here would bake an economy constant into
universe generation.

What is centred is the draw, which is the thing that carried the bias. A
roster of forty names taken from it still scatters, and that scatter is
sampling error rather than bias: the day-zero deviation has a
cross-sectional sd of about 0.32, so a forty-name mean carries a standard
error of about 0.05 and any roster lands that far either side by chance.
The panel roster is one that draws high, reading +0.048 against the draw's
+0.013, and a different seed is as likely to read low.

Removing that too would mean correcting each roster to its own realised
mean, which is deliberately not done. The correction depends on the roster
size, so it is a second pass whose result depends on `n`, and it breaks the
invariant that a larger universe extends a smaller one. A generator that
silently re-centred every roster would also report a cross-section tighter
than the one it drew.

Measured on the panel roster over thirty seeds at 252 days on pt-v16, the
annualised index drift improves from a median of -22.155 to -18.344 per cent
a year, in the log convention. Every one of the thirty improves, by between
3.715 and 4.039 points, because the starting condition is a property of the
roster and not of the market seed. The drift is still negative on all
thirty: the remaining terms are a down-tilt in the market factor, a rising
rate path and an unexplained residual, none of which this touches.

**What this breaks.** The same name and seed no longer give the same
universe. Every generated name's earnings and book value move, and with
them every price path, so any published result citing `Universe.random`
re-rolls; `Universe.random(40, seed=111)` now fingerprints
9be68b9bc37e7978. Everything else about a generated name is bit-identical,
because both reconciled fields take exactly one draw each as they did
before and the stream position is unchanged. The determinism digest does
not move: the known-answer run builds its instruments by hand and never
calls the generator.

The certified panel does not move either, which is the same finding the
new drift row exists for. Over seeds 101 to 110 the fourteen graded
medians shift by at most 0.26 of their own across-seed noise and all
fourteen stay in band. Their recorded values, `SEED_SD` and the preset
records were nonetheless all measured on the roster the old generator
produced, and none has been re-measured. They are marked stale where they
are recorded rather than quietly restated.

### The pt-v18 era and the downside transmission tilt

The equal-weight index drifted -22.155 per cent a year at pt-v16 in the log
convention, measured over thirty seeds on the panel roster at 252 days,
against a real large-cap index's +8 to +10. It was negative on every seed.
Almost none of that was chosen. It is the sum of mechanisms that each moved
the first moment as a by-product of shaping something else, under a panel of
fourteen shape statistics that cannot see a first moment and read fourteen
for fourteen throughout.

pt-v18 is the era that gives those first moments back at their source.
pt-v17 is reserved by the open recomposition era, and a preset name is the
identity every published result cites, so this era takes the next free
number and the gap is deliberate.

The first of them is the downside transmission tilt. It scales one side of
a zero-mean draw, which moves that draw's mean, and a mean in a price
process is a drift: for a normal factor, the expectation of the down half
is minus sigma over the square root of two pi, so the tilt adds a negative
amount to every name on every tick whether the market is calm or not.
Nobody chose that. It is the by-product of a correlation mechanism that is
otherwise doing exactly what it was built to do.

`market_beta_down_asym_recentre` gives it back. At 0.0, which is every
preset before pt-v18, the branch is not taken and the trajectory is
bit-identical. At 1.0 the whole injected mean is returned.

Three things about the form are worth stating, because each was a choice.
The sigma is the CONDITIONAL sigma of the tick's own draw and not the
baseline constant, so the correction tracks the factor's variance process
instead of assuming it away, and it cannot be defeated by a VIX coupling or
a pinned scenario: a hotter tick injects more and gives back more. Beta is
per name, because the injection into a name IS its beta times the form, so
using one instead would leave a residual proportional to beta minus one,
which is a cross-sectional bias as well as a mean one. And the offset is
applied after the crash amplifier, because an offset added before it would
itself be amplified.

What is deliberately NOT corrected is the amplifier's own effect on the
tilt. The true injected mean is the closed form times a ratio that has no
closed form, measured between 1.00 and 1.38 across conditional sigmas and
sitting near 1.01 at the ones that occur. Correcting it would need either a
new bit-pinned transcendental or a fitted constant, and a fitted constant
is tuning rather than fixing. What it leaves is measured below rather than
argued.

Measured over thirty seeds in the log convention, the annualised index drift
improves from -18.344 to -11.347 per cent a year, and every one of the
thirty improves. The paired difference is +7.879 by median and +8.251 by
mean. Set that against the investigation's finding that switching the tilt
OFF entirely is worth 7.940: recentring recovers essentially all of the
drift the tilt costs while keeping the correlation asymmetry it exists to
produce.

The mechanism reading agrees with the drift reading. The tilt's own
contribution to the noise attribution is -0.389 per name per year at
pt-v16; with the tilt switched off it is -0.049, and with the tilt on and
recentred it is -0.052. Recentring lands within 0.007 of switching the
mechanism off, on a term worth 0.342, and that gap is the amplifier
residual named above. The -0.049 both arms share is not this correction
falling short; it is what the noise channel carries with no tilt at all.

The certified panel does not move. Over ten seeds the fourteen graded
medians shift by at most 0.55 of their own across-seed noise and all
fourteen stay in band, including the correlation asymmetry the tilt was
built for.

Adding a settable dial changes the SHAPE of every preset's coefficient
vector, because the digest is taken over the whole settable surface. Both
committed preset records therefore gained the new name at 0.0 and a new
digest, through a `--coefficients` mode that rewrites those two fields and
refuses to run if any existing coefficient moved. Every measured block in
those records is byte for byte as it was, and no preset was re-measured.

### The supply term behind the one-way rate path

The corporate bond yield rose on 24 of 30 seeds over a year and was
non-decreasing on most of them. The yield itself is recomputed at every
meeting as the ten-year plus a spread, so it carries no memory of its own.
It rose because the central bank hiked, the bank hiked because inflation
rose, and inflation rose because the oil price did.

The oil price rose because a supply term was left at zero. Oil demand draws
inventory down by `gdp_growth * 0.15` every day and `oil_supply_factor` is
the literal `0.0`, so inventory falls monotonically from its opening 50
whatever the world does. It reaches its floor around day 120 of a 252-day
run and stays there. The inventory pressure term is `(40 - inventory) *
0.08`, so at the floor it saturates at a standing push of `+3.2` a day on
the price, which oil's own mean reversion of 0.03 a day cannot hold. Oil
pins at its 150 clamp, and everything downstream follows.

So the one-way rate path lives in a supply term at zero, four steps
upstream. The yield is the last link in that chain.

`oil_supply_response` answers demand with supply. At 0.0, which is every
preset before pt-v18 and what the reference implementation does, the branch
is not taken and the trajectory is bit-identical. At 1.0 supply matches
demand in expectation, `inventory_change` is the noise term alone, and
inventory is a driftless random walk. That value is derived rather than
chosen: it is the stationarity condition of the process, read off the
process, using the coefficient already there. The pressure term is already
two-sided, pushing up below 40 and down above 60, so a driftless inventory
gives a two-sided oil price and a two-sided rate path without any of them
being made two-sided by hand.

Measured over five seeds, the corporate bond yield is monotone on 1 of 5
where it was monotone on 4 of 5, and its rise over the year falls from
+0.0200 to +0.0133. Over thirty seeds the annualised index drift, in the log
convention, improves from -11.347 to -9.865, a paired +1.178 by median and
+1.294 by mean, and every one of the thirty improves.

**That is a third of what the rate path is worth, and the rest is still
there.** The investigation prices the whole rate path at 3.805 points. Oil
still reaches 145.8 by day 251 and inflation still rises monotonically on
every seed, so answering demand with supply removed one driver and not the
family. What remains has been localised rather than guessed at: the
inventory walk is now driftless but wide enough to spend real time against
its floor, and the inflation process itself carries several one-sided terms
of its own, a wage-pressure floor at zero and a Phillips term that only
pushes one way while unemployment falls to 2.5 per cent. Naming which of
those dominates is the next measurement rather than this one's conclusion.

**And it costs one row.** `leverage_effect` reads +0.0006 against a ceiling
of 0.00, so the panel is 13 of 14 rather than 14 of 14. That row is the
stated side channel of the downside tilt, and the investigation found it
leaving the same band when the tilt was switched off entirely. The
exceedance is 0.008 of the row's own across-seed noise, so it is a hair.
The era is uncertified until the five steps are done, and the panel that
counts is the one measured then.

### The symmetrised OPEC rule

The rule reacts to the oil price against an 80 target. Below it by more
than 10 it cuts production with probability 0.6 and magnitude 3 to 6; above
it by more than 10 it raises production with probability 0.5 and magnitude
2 to 5. Expected `+2.700` against `-1.750`, so the cut is 1.54 times the
increase and the rule pushes oil up on net. The two branches read as a pair
that should mirror, and nothing in the code says the difference was
intended.

`oil_opec_symmetry` at 1.0 gives both sides one probability and one
magnitude range, each the mean of the two the rule already carries:
probability 0.55, magnitude 2.5 to 5.5. That is the one symmetric rule that
preserves the total intervention the rule performs, so it removes the
direction without choosing a side and without inventing a number. Expected
impact is then equal and opposite, and zero on net. At 0.0, every preset
before pt-v18, the original arithmetic runs in the original order and the
same draws are consumed in the same places.

**It is worth nothing on the drift.** It is corrected because it is wrong.
The rule fires every 90 days, so its bias is about +0.95 of oil price per
firing and under +3 across a year against a price that moves by 70. Over
thirty seeds the index drift, in the log convention, moves from -9.865 to
-9.871, a paired median of exactly 0.000. This is a correctness fix on a
rule that was wrong. It has its own section so a reader can hold it apart
from the terms that move the number.

### The compensated market jump

`jump_mean_market` is negative so that crashes are larger than rallies.
That is a real property of index returns and a legitimate thing to want.
But a jump arriving with probability `lambda` and mean `m` contributes
`lambda * m` to the expected return every day whether it fires or not, so
the skew came with a drift: -0.11769 per name per year at the day-zero
intensity, and 2.6 percentage points of annual index level. The value was
set once in the pt-v4 era by a search whose objective could not read a
first moment, and inherited unchanged through eleven presets. The drift was
never chosen, because nothing the search could read would have shown it.

The obvious repair is to solve for a smaller mean that buys skew without
the drift. No such value exists. For a compound Poisson jump the drift and
the skew are both linear in the mean, so trading one against the other is a
matter of degree and any answer would be a fitted constant.

`jump_mean_compensated` subtracts `lambda * m` instead, which is the
standard compensated-Poisson construction and makes the jump term a
martingale. Because the compensator is a DETERMINISTIC offset it moves the
first moment and leaves every central moment alone, so the skew and the fat
tail survive at the mean the calibration chose. **The mean does not move at
all.** The defect was a missing compensator, and the value stands.

`lambda` is the conditional intensity, already scaled by the VIX coupling,
so the compensator tracks the arrival rate. The investigation measured the
realised drift at 1.084 times the day-zero closed form for exactly that
reason, and a compensator on the day-zero rate would have left that 8 per
cent behind. It shows: over thirty seeds the annualised index drift improves
in the log convention from -9.871 to -7.145, a paired +2.799 by median,
where switching the jump mean off entirely was measured at 2.621.

The central moments hold, measured rather than asserted. Over six seeds the
paired moves are -0.045 of skew, +0.066 of excess kurtosis and -0.170 of
annualised volatility, the last two being 0.06 and 0.03 of their own
across-seed noise. Every one of the thirty seeds improves and two of them
now finish the year positive, which no arm of this model has done before.

The panel returns to 14 of 14. The leverage effect, which sat 0.008 of its
own seed noise outside its ceiling after the oil step, is back in band.

**One thing this measurement says that the mechanism did not.** The pooled
skew of this market is POSITIVE, at +0.12, both with the compensator and
without it. The market jump's mean is the mechanism the model has for
negative skew, and at the panel level the skew is the other way. That is
recorded rather than acted on: it says the jump mean was not buying what it
was described as buying, which is a question about the mechanism rather
than about its drift.

### The matched stop ladders

Forced flow from resting stop orders runs both ways: stop-losses under
longs on the way down, buy-stops over shorts on the way up. The two ladders
expressing it did not match. The downside fired at a 2 per cent move and
the upside at 3. The downside had four tiers and the upside three. At every
matched size the downside was the larger: 0.008 against 0.006, 0.005
against 0.004, 0.003 against 0.002, with a fourth downside tier of 0.001
that had no partner. Every one of those was a bare literal with no
parameter, so nothing could reach them and nothing recorded a reason for
the difference. Over a symmetric distribution of daily returns a ladder
that subtracts more than it adds is a drift.

`cascade_symmetry` matches the threshold and the tier magnitudes at the
mean of the two ladders: threshold 0.025, tiers 0.007, 0.0045, 0.0025 and
0.0005. That is the construction the OPEC rule uses. It chooses neither
side and preserves the pair's total intervention exactly, 0.029 across both
ladders before and after.

The GATES are untouched. A stop-loss sits under every long, so the downside
carries no condition beyond its threshold; a buy-stop needs shorts to
exist, so the upside keeps its short-interest gate. Those are finance
rather than an accident, and matching them would have removed a real
mechanism instead of an unchosen asymmetry.

Over thirty seeds the annualised index drift, in the log convention,
improves from -7.145 to -6.832, a paired +0.370 by median, and every one of
the thirty improves. The investigation prices the whole squeeze-and-cascade
block at 0.574, measured by switching it off; matching the ladders while
keeping the gates recovers two thirds of that and leaves the mechanism in
place.

**This one is matched rather than derived, and the difference matters.**
The tilt, the jump and the oil supply term each had a closed form or a
stationarity condition behind them, so their values were read off the
process. Odd symmetry in the return is the structural claim here. The mean
of the two ladders is a rule for choosing numbers under that claim, and the
process itself dictates none. Whether these literals should be parameters
at all stays open for the era's owner to settle.

The panel reads 13 of 14. The leverage effect is +0.0004 against a ceiling
of 0.00, which is 0.005 of that row's own across-seed noise. It is the
downside tilt's stated side channel and it has now crossed and re-crossed
that ceiling twice within this era.

### The valuation's growth term

Price is `fair_value * exp(s)`, the mispricing is stationary around zero
and `eps` is fixed when an instrument is built, so the only time variation
in fair value was the discount rate. The expected log change of the index
over any horizon was therefore zero in a stationary economy and negative
in one whose yields rise. Returning the five unchosen first moments above
leaves the index near zero, and a real equal-weight price index returns
eight to nine per cent a year nominal.

A premium placed in the mispricing process settles at a level. Under a
constant drift per step the stationary mean solves `m = phi * m + c`, so
the premium reaches `c / (1 - phi)` on the sixty-day half-life and grows
no further. Simulated at three, six and nine per cent a year it gave
levels of +0.010, +0.021 and +0.031 with third-year growth of zero.

`earnings_nominal_growth` puts the term in fair value instead, from a
quantity the economy already integrates. The macro chain compounds `gdp`
and `cpi` on every day it advances, so `N = gdp * cpi` is nominal output,
and at 1.0 the valuation reads `eps` and `book_value_per_share` multiplied
by `N_t / N_0`, with `N_0` read at construction. Both fundamentals move,
so the valuation stays homogeneous of degree one in nominal terms on the
earnings path and on the book path alike. The ratio is exactly 1.0 on day
zero, so the opening valuation and the initial mispricing taken from it
hold still. At 0.0, which is every preset before pt-v18, the branch is not
taken and the trajectory is bit-identical.

The term states no rate of its own. It reads the level the economy
reached, so the rate falls with growth and inflation wherever the cycle
takes them, and it goes below zero when the two turn negative together.
Measured over 1008 days on seed 1, where the cycle leaves expansion and
the run ends in a trough, nominal output reaches a ratio of 1.0685, which
is 1.67 per cent a year against the 4.342 a certified year delivers. So
4.34 is a property of the opening expansion rather than of the model, and
anyone quoting the term has to say over what window.

The two figures bracket the model's own long-run number, and the cycle
clock decides where inside them it falls. The business-cycle hazard draws
a monthly scale once a day, so a phase change arrives about thirty times
too often: 63 per cent of certified years opening in expansion see one
against 3 per cent on the per-month reading. The 1.67 is therefore
measured on a model that leaves expansion far more often than it should,
and the true figure sits between the two and nearer the upper one. That
makes the cycle correction worth more than its own row suggested, since
it decides what this term delivers over any window longer than a year.

The clock is the part of this that is easy to get wrong. The economy
compounds on a 365-day year and advances once per market day, while the
market trades 252 days and annualises by 252, so a certified year carries
252/365 of every annual rate. Measured on the panel roster at 252 days
over six seeds, nominal output grows a median of +4.353 per cent per
trading year, with mean growth of 3.353 and mean inflation of 2.931 over
the run, and `(3.353 + 2.931) * 252 / 365` is 4.339 against a measured
mean of 4.340. A test states that as an identity against the rates a run
held rather than as a value.

Measured over thirty seeds on the panel roster at 252 days, in the log
convention, which is the one every section above uses.

| quantity | median | mean | min | max | sd |
|---|---|---|---|---|---|
| index drift before | -6.832 | -8.208 | -23.706 | +1.410 | 6.418 |
| index drift after | -2.304 | -3.816 | -19.213 | +5.628 | 6.335 |
| recovered, paired per seed | +4.383 | +4.392 | +3.996 | +5.165 | 0.200 |
| nominal output integrated | +4.342 | +4.339 | +4.019 | +4.540 | 0.115 |
| recovery less nominal | +0.013 | +0.053 | -0.166 | +0.911 | 0.189 |

Every one of the thirty improves, and nine finish the year positive
against two before. The last row is the claim: the term delivers the
level the economy integrated and 0.013 of a point besides, with fifteen
seeds either side of zero, so it reads the economy rather than adding
anything of its own. In this row's own reporting convention, the
daily-rebalanced portfolio, the same thirty seeds read a median of -0.340
with thirteen above zero, against pt-v16's -16.150 with none.

The trailing multiple stays far from the cycle's gate inside the window
everything here is measured on. Over the same thirty seeds it reads a
median of 21.547 at day 251, a maximum of 24.220, and crosses 28 on none
of them.

The certified panel does not move. Thirteen of fourteen rows are in band
before and after, the largest paired median shift is 0.18 of that row's
own across-seed noise, and the row outside is the leverage effect at
+0.0035 against a ceiling of exactly 0.00. Its paired median moves -0.0004
under this step, which is toward the band, and its level sits at a
twentieth of its own across-seed standard deviation of 0.0760 above a
boundary drawn at zero. Seven of the ten panel seeds are above that
boundary and three below. The reading is about a band placed at exactly
zero rather than about this step.

The earnings share of nominal output is held constant, and the model
carries no quantity for that share, so 1.0 is the only setting read off
the process rather than chosen. A real price index also earns a return
above nominal output growth, through buybacks and the drift of that share,
worth three to four points a year. This model has nothing to derive that
from, so the gap is named here and left.

The trailing market multiple reads the same restated earnings. The daily
macro step computes a market-cap-weighted price over earnings, and the
expansion hazard adds `min(0.1, (pe - 28) * 0.005)` a day above a multiple
of 28, so a day-zero earnings figure divided into a price that grew with
nominal output would report a multiple rising because the mechanism ran.

What that is worth was measured on one trajectory rather than argued,
since the restatement is a single factor across names and the multiple a
day-zero denominator would give is the engine's own times the ratio on the
same day. Over 1008 days on seed 1 the restated multiple ends at 30.214
and crosses 28 on 32 days, for a summed hazard of 0.2536; the day-zero
denominator gives 32.284, 42 days and 0.6344. Inside a certified year
neither reaches the gate, because the multiple at day 251 is 21.671 at a
ratio of 1.0448. So this part of the change is worth nothing over the
window everything here is measured on and a third of the expansion hazard
over four years. The second number is what it is in the change for.

`N_0` is engine state and rides in the snapshot, so the state hash covers
one more field and a ledger leaf taken on an earlier build of this release
verifies against nothing taken now. Both ship in one release and no such
leaf exists outside this repository. Adding a settable dial also changes
the shape of every preset's coefficient vector, so both committed records
gained the new name at its inert default and a new digest, through the
mode that rewrites those two fields and refuses any moved coefficient. The
determinism digest is unmoved and the default preset is untouched.

### The oil seasonality dial

The daily oil update multiplied the whole new price by
`1 + 0.03 * sin(2 pi (day_of_year - 90) / 365)`. A shape applied to a level
every day compounds, so what a window sees is the product of its factors:
5.119 over the 252 game-days a certified year passes, and 0.921 over a full
365. The shape is near neutral over its own period and the horizon slices
it, taking 162 days of the up leg against 90 of the down. Summing the
term's own contribution to each day's change over year one gives +365.80 of
oil price against a net change of +72.10, so it pushed about five times
harder than the price moved and the mean reversion of 0.03 a day absorbed
the rest. Oil had no fixed point under it. It was a forced limit cycle with
a 365-day period that sat on its 150.0 clamp from day 180 on every seed,
and the inflation term, the meeting rule and the discount rate followed it
there.

`oil_seasonality_target` moves the shape onto the price the process reverts
toward. At 1.0 the whole amplitude multiplies the target and the level's
own factor is exactly 1.0, so the shape modulates where the price is pulled
toward by plus or minus 3 per cent and integrates to +0.672 per cent of oil
over a certified year. Between the ends the amplitude is split, `1 + g*a`
on the target and `1 + (1-g)*a` on the level, so its total is conserved and
only the point of application moves. The 0.03 is the amplitude the term
already carried, and this dial is a share of it. At 0.0, which is every
preset before pt-v18, the original arithmetic runs in its original order.

What it is worth, over five seeds at 252 days on the panel roster, paired
on the seed so the roster's own opening level cancels. The index gains
+1.303 points a year at the median in the log convention and +1.293 as a
portfolio, on 5 of 5 seeds, in a range of +1.105 to +1.914. The convention
gap moves -0.009, so both conventions carry the same gain and the move is
in the closes. All of it arrives through the discount rate, which is the
route the mechanism predicts: the fair value channel carries +1.263 on
every seed and the mispricing channel +0.021 with the seeds split.
Inflation at day 251 falls 1.055 points and the corporate bond yield 0.795,
each on 5 of 5.

Oil stops reaching its clamp. Days spent at the 150.0 ceiling fall from a
median of 69 to zero on every seed, and the highest price any seed reaches
is 108.6 where the arm without the dial reaches 150.0. That is the plainest
statement of the defect being gone. The day-251 median is 93.1, against the
86.7 the seasonal term's own arithmetic predicts on a target near 85, and
the difference is the inventory pressure, the dollar drag and the OPEC
rule, which the compounding factor used to dominate.

The certified panel moves, and the row that moves it cannot be decided
here. Over seeds 101 to 110 the arm without the dial reads 13 of 14 and
the arm with it 14 of 14, while pt-v18 as it ships, carrying the cycle
dial as well, reads 13 of 14 again. One row decides all three: the
leverage effect, at +0.0035, -0.0005 and +0.0038 against a ceiling of
exactly 0.00, on an across-seed noise of 0.0769 and a per-seed spread of
0.029 at ten seeds, with between five and seven of the ten seeds above
the ceiling in every arm. Those three arms span 0.0043, a twentieth of
the row's own noise, so ten seeds cannot say which side of a boundary at
zero this model belongs on.

The ceiling is the larger term and it describes the band's derivation.
`REAL_MARKETS_PROVENANCE` records that this band's top was derived at
+0.05 and clamped INWARD to 0.00, because every retrieved source agrees
the effect's sign is negative and a top above zero would certify a
reversed leverage effect as real-market behaviour. The nine non-crisis
reference windows run -0.113 to +0.014, and the single positive reading
is the 2020-21 meme-stock year. So the clamp moves the boundary by 0.05,
eleven times the 0.0043 these arms span, and the clamp decides the
verdict while the model's own level stays far inside it.

The derivation record states that the two inward clamps it needed decide
no current verdict. On this era's arms the leverage clamp decides every
one of them, and each verdict this section reports for that row, in
either direction, is a reading of the clamp.

The thirty-seed distribution and the four-year arm are measured elsewhere,
because neither runs on one machine.

### The cycle hazard's monthly scale

`weibull_hazard` returns `(shape / scale) * pow(months / scale, shape - 1)`,
and every scale in `cycle_hazard_params` is in months: 36 for an expansion,
6 for a peak, 12 for a contraction. A hazard whose scale is in months is a
rate per month, and `check_cycle_transition` compared it against a uniform
once a day, so the cycle ran about thirty times too fast. A full cycle took
2.6 trading years where the same constants read per month give 9.7, and a
252-day run opening at the start of an expansion left it 63 per cent of the
time against 3.

`cycle_hazard_per_month` reads the scale on the clock it was written in.
`months_in_current_phase` advances by 1/30 a day, so the month this model
keeps is 30 days and the divisor is read off the engine's own clock. At
0.0, which is every preset before pt-v18 and what the reference
implementation does, the branch is not taken. At 1.0 the daily probability
is the monthly rate divided by 30 to the last bit.

The conversion is applied after the condition ladder and after the clamp,
because every operand before it is a rate per month: the hazard's own cap
of 0.8, the ladder's additions of 0.1 and 0.15 for inflation, policy and an
inverted curve, and the clamp at 0.3. Converting earlier would leave the
ladder as a daily probability against a base hazard near 0.0004 a day, so
an inverted curve would raise the transition rate by 250 times where it now
triples it. The 9.7 years above assumes this placement. Dividing before the
clamp gives 9.59 instead, and the whole of that difference sits in the two
short phases.

So the 0.3 clamp becomes a cap on a monthly rate and the largest daily
probability is 0.01. On the hazard alone it binds for a peak past month
5.40 and a trough past month 2.57 and nowhere else, since an expansion
reaches it at month 338, a recovery at month 358, and a contraction's
hazard falls with duration. It shapes the mean peak from 183 days to 171
and the mean trough from 159 to 138.

Counted over thirty seeds at 1008 days, the clamp binds on 527 of 2082
peak rolls and 153 of 203 trough rolls on the hazard alone, against none
of either drawn per day, and on none of 18352 expansion or 1495
contraction rolls under either reading. A certified year reaches no trough
at all and records 0 of 2121 rolls clamped.

With the ladder a trough differs, and it differed before this dial
existed. A trough adds 0.1 for a policy rate under 3.0 and 0.05 for
unemployment over 8.0 against a hazard of 0.265 at its two-month minimum,
so the clamp binds on its first eligible roll under either reading, at 203
of 203 read per month and 97 of 97 drawn per day. The clamp was already
doing work in a trough, and what changed is that phases now last long
enough to reach one.

Inside one year the correction is worth nothing. Over thirty seeds at 252
days the paired median is +0.000 with a mean of +0.205, fourteen seeds up,
five down and eleven bit-identical, and the eleven are exactly the eleven
that never left an expansion under the fast clock. The two sets are equal
seed for seed. What moves is the phase: seeds leaving
expansion by day 251 fall from 19 of 30 to 1 of 30, and the mean days in
expansion rise from 226.2 to 250.7.

Beyond a year the clock decides how much of a study sits outside an
expansion, and four years show it. The spread between the best and worst
year's median drift falls from 12.252 points to 5.142 over thirty seeds at
1008 days. What remains is the run's own shape: year one carries the
roster's opening level decaying against the growth term, years two and
three run at +3.6 and +3.3, and year four falls back to +0.6 as the first
contractions arrive.

The phase lengths quoted above are the hazard alone. The condition ladder
adds to it, so each bounds a phase's length from above. The ratio of
thirty is unaffected, since both readings exclude the ladder equally.

**A claim in this section was wrong and is corrected here.** It said a
contraction's four ladder conditions shorten a deep one to 7.7 months
against a mild one's 23. Every contraction condition adds to the hazard,
so the ladder can only shorten a phase, but that arithmetic assumed all
four fire from the fourth month, and the four-year arms measure them
firing late: growth under -2.0 on day 1, the policy rate under 1.0 on day
190 at the median, unemployment over 10.0 on day 239. A spell's count of
fired conditions therefore records how long it has already run, so the
162, 226 and 335 days at one, two and three conditions sort spells by
duration rather than by depth. A deep contraction ended early by its own
ladder was looked for and measured absent. The wrong claim is quoted
rather than deleted, so a reader who met it can find out it was wrong.

Two entry points changed shape. `check_cycle_transition` and
`get_cycle_transition_probability` each take the dial, so a caller states
which reading it wants instead of inheriting one. Every caller outside the
engine passes 0.0, which is the reference implementation's reading, and the
economy parity vectors reproduce bit for bit against it.

### The valuation's neutral rate

`compute_target_pe` compresses a multiple by
`(discount - neutral) * RATE_PE_SENSITIVITY * duration`, so a name sits on
its sector anchor exactly when the discount rate equals the neutral point.
That point was the constant 0.04. The economy opens at a corporate bond
yield of 4.56 per cent and settles at 4.82, and it never visits 4.00, so
every profitable name opened about one per cent below the price the
generator drew for it and the market spent the year unwinding that on a
60-day half-life.

`neutral_discount_rate` is that point, settable, at 0.04 for every preset
before pt-v18 and 0.0456 for pt-v18. The value is read off the process
rather than chosen: the rate that zeroes the day-zero term is the yield
the economy opens at.

The generator is untouched. The multiple
this rate anchors is the same sector anchor the generator draws its
multiples around, so the two stay consistent under any neutral rate and a
roster opens at fair value whenever the engine's discount rate equals it.
The constant is read by no line of the generator's code. Its reconciliation
test now runs at three neutral points and asserts the day-zero mean is
bit-identical across them, which is the claim itself: at a discount rate
equal to the neutral point the rate adjustment is exactly 1.0 whatever that
point is.

The name was promoted from the carried read-only surface rather than added
beside it. `to_pairs` merges that surface with the settable one and sorts,
so a name moving between them leaves every preset's pairs, fingerprint and
coefficient digest untouched wherever its value has not moved. Both
committed preset records are therefore byte for byte as they were, and the
record test passes without re-recording. A second name would have put two
entries for one quantity in every record, with `neutral_discount_rate`
reading 0.04 about an engine using 0.0456.

`tradefloor.fair_value` takes the rate as an optional keyword, absent
meaning the old constant. A caller recomputing a run's fair value passes
that run's own value, because a helper holding the old constant while the
engine moved would disagree with the market it describes and the
disagreement would read as a print residual.

What it is worth, over five seeds at 252 days on the panel roster, paired
on the seed. The index gains +0.965 points a year at the median in the log
convention and +0.965 as a portfolio, on 5 of 5 seeds, in a range of +0.937
to +1.015. The whole of it is the mispricing channel at +0.965, with the
fair value channel at -0.005 and the seeds split. What moves is the level
the market opens at, and the discount path it travels afterwards stays
where it was. The oil price, the phase and the macro path are
bit-identical on every seed.

The day-zero level falls by 0.00968 on all five seeds, identically, because
it is a property of the roster and the valuation and not of the market
seed. A prediction registered beforehand said 0.0100 to 0.0115 and this
misses it low. The reason is structural rather than noise: 0.0107 is the
figure for a name valued on its earnings, and 3 of the 40 names on this
roster are valued on their book, where the multiple and this rate reach
nothing. Scaling by the 92.5 per cent that are on the earnings path
predicts 0.00990, and the remaining 0.00022 is the duration multiplier
varying from name to name.

The certified panel holds at 13 of 14 over seeds 101 to 110, with the
leverage effect the only row out at +0.0014 against a ceiling of exactly
0.00, where the arm without the dial reads +0.0038. That row straddles its
ceiling across every arm in this era and ten seeds cannot settle it. The
panel's own drift row, a portfolio return, reads +2.259 against +1.303.

### The economy's opening state

The economy opened at unemployment 4.00, inflation 2.00 and a corporate
bond yield of 4.56, and its own dynamics reach 2.50, 2.74 and 4.82. So
every certified year was measured on a window in which nothing had
settled, and the travel compressed the multiple one way.

`macro_burn_in_days` advances the economy alone before day zero, at 0 for
every preset before pt-v18 and 755 for pt-v18. The length is measured
rather than round: 755 is the day the last field the valuation reads
enters one stationary standard deviation of its mean and stays there.
Unemployment takes 119 days, inflation 419 and the ten-year 705.

Three things it does deliberately. The draws come from the economy's own
substream, so the market's day-zero draws sit where they did, and a
burn-in consumes economy draws by running the economy. The phase is held
for the burn-in's length, by restoring both the phase and its age whenever
a transition fires; restoring the phase alone leaves the age at zero,
which fires the phase-change shock the next day and lifts growth half a
point above its target for the rest of the run. The clock is reset at the
end, so the year still opens at the start of a phase.

The growth term's base is re-read at the end. `gdp` and `cpi` compound on
all 755 days, so a base left at its pre-burn-in value would open every
valuation about nine per cent above its own earnings.

What it is worth, five seeds at 252 days paired on the seed, against the
neutral rate alone. The fair value channel gains +0.942, on 5 of 5 seeds,
which is the economy sitting still while the year is measured. The whole
arm gains +0.363 points a year at the median.

The two rows do not compose, and the reason is oil. The neutral rate is
exact for a year that opens at 4.56, because every seed opens there. With
the burn-in the economy opens at a corporate yield of 5.236 at the median
over twelve seeds, with a spread of 4.828 to 5.960, because oil still has
no resting point: it opens between its own 35.0 floor and 120.8, at a
standard deviation of 22.7. So no single neutral rate zeroes the day-zero
term on every seed. 0.0482 is the corner an arm with oil pinned at 75.0
reaches, and this engine rests half a point above it.

The residual is named instead of fitted. At 0.0482 the mispricing channel
reads -0.351 on 5 of 5 seeds, which is that unclosed gap measured. The
value that would zero it at the median seed is near 0.0510 and is worth
about 0.7 points more. That value is the median of a distribution, so it
would be a fitted constant, and oil's resting point is what would make it
derivable.

### The buyback yield

Rows so far give back means the model injected and grow the valuation with
the economy's own output. What they leave is the return a real price index
earns above nominal output growth, and the model had no quantity to derive
it from.

`buyback_payout_share` is the share of earnings a company returns as net
buybacks, at 0.0 for every preset before pt-v18 and a third for pt-v18.
Retiring stock makes earnings and book per share grow faster than the
company does, by the buyback yield `payout_share * eps / price`. That yield
is earnings over price, so the term pays more where the multiple is low and
less where it is high.

This is the one CHOSEN constant in the era and it is marked as such. Every
other dial here is derived: a stationarity condition, a symmetric mean, a
unit, a measured transient, the yield the economy opens at. This one states
how US large-cap companies behave, from the filing record where total
shareholder return runs near half of earnings split between dividends and
net buybacks. A reader can check it against that source, which a value
fitted to this engine's own distribution would not allow.

The yield it implies is a check on the share, in that order. The model's
median annual earnings yield is 0.0555, so a third of it is a buyback
yield of 1.85 per cent, and US large-cap net buybacks over 2000 to 2025 run
near 1.5 to 2.0 per cent of market value.

The yield is evaluated at today's price and held over the elapsed time. The
exact factor is the exponential of the payout share times the integral of
the earnings yield, which needs a per-name accumulator and a nineteenth
column in the state hash. Measured against that integral on real price
paths: over 252 days on 200 name-seeds the difference is +0.026 points of
annual index return at the median, and over 1008 days on 120 name-seeds it
is -0.015. The multiple mean-reverts, so today's yield estimates the
period's average at both horizons, and the residual is named here rather
than removed.

A loss-maker neither retires stock nor issues it. A negative earnings yield
would grow the share count, and a company cannot return earnings it does
not have. That clamp carries none of the drift hazard this era keeps
finding in one-sided terms, because `eps` is fixed for a name at
construction, so it selects a fixed share of the roster once rather than
branching on a zero-mean quantity every day. On the panel roster it is 3
names of 40, and their measured lift is -0.05 points against +1.77 for the
index.

What it is worth, five seeds at 252 days paired on the seed: +1.765 points
a year at the median in the log convention and +1.738 as a portfolio, on 5
of 5 seeds, in a range of +1.672 to +2.011. The whole of it is the fair
value channel at +1.771, with the mispricing channel at -0.019. The oil
price, the phase and the macro path are bit-identical on every seed.

The shape is measured as well as the size. Across 185 name-seeds with
positive earnings the rank correlation between a name's lift and its
opening earnings yield is +0.773, and the cheapest quartile by yield gains
+2.543 points against +1.154 for the dearest. The term pays an earnings
yield, which is the property that separates it from a flat premium.

The certified panel reads 14 of 14 over seeds 101 to 110, and its own drift
row, a portfolio return, reads +4.193.

### The sequential base and two misattributed figures

Each step in this era was measured against the branch as it stood when that
step landed. That is the right thing for a changelog, which describes commits,
and the wrong thing for a decomposition, which describes mechanisms: a step
measured against a moving base carries whatever else moved with it.

Two figures above are affected. The burn-in's +0.363 was measured against the
neutral rate as it then stood, at 0.0456, while the burn-in's own commit moved
that rate to 0.0482. Isolated, with the rate held on both sides over thirty
seeds, the burn-in reads -0.028 and the rate step carries the figure. And the
oil rows are worth MORE than they are credited here, because the sequential
measurement charged part of each to whatever landed next.

The burn-in still does what its own falsifier said. The economy after
construction opens at the corner its dynamics reach, at unemployment 2.50
against 4.00. What it does not do is move the index.

### The one-year horizon of every figure above

Six of the era's terms have now been measured at 1008 days as well as 252,
and they do not behave alike. A term working through the mispricing is a
ONE-OFF LEVEL SHIFT, because `s` is a stationary process and a persistent
injection settles at an offset rather than drifting; removing it buys that
offset once. The tilt keeps 0.28 of its one-year worth when annualised over
four and the oil seasonality keeps 0.11.

A term working through fair value compounds: the growth term keeps 0.88. A
term changing the cycle clock is worth exactly nothing inside a certified year
and +1.824 a year over four, which makes it the second largest term at that
horizon having been the smallest at this one.

The buyback yield is not among the six. It is measured at 252 days only, and
a term paying an earnings yield out of a mean-reverting multiple has no reason
to behave like either group without being measured at the longer horizon.

So the era is worth about +18 at a certified year and about +10 annualised
over four, with a different ranking. A reader applying this section to a
multi-year study gets both the size and the order wrong.

### The commit the measurements name

Every figure in the section above was measured on a build of a commit
that is no longer reachable from this branch. The two commits that carry
the growth term and the convention change were pushed, the second was
then amended to rewrap five paragraphs, and the branch was force-pushed,
which orphaned the commit the sweep had already stamped into its output
files.

The tree is unaffected. The amended commit differs from the orphaned one
in CHANGELOG.md prose alone, so the build the numbers came from is the
build this branch carries. What was lost is the ability to resolve the
SHA those files name, and resolving it is the whole use of a citation.

So the SHA to cite for every figure here is this branch's second commit,
and the orphaned one should be read as an earlier spelling of the same
tree. The rule that produced this is worth keeping: a branch whose
commits are stamped into measurement output is a branch that is never
rewritten, and prose is not an exception to that.

### The index convention

`index_drift_pct` reported the mean across names of the daily log return.
An equal-weight index is a portfolio rebalanced to equal weights every
day, so its daily return is the mean of the SIMPLE returns, and the row
now reports that. The two are different quantities and the row is graded,
when a band for it exists, against a published index return, which is a
portfolio return.

The decompositions keep the log convention, and they have to. Log returns
are additive across time and across the terms of an identity, so a
contribution table that sums to a move can be written in them and cannot be
written in a portfolio return. Every attribution this engine reports is in
log returns, and so is every figure in the sections above.

The two differ by half the cross-sectional variance of the daily returns,
which is positive wherever the names disperse at all. On the panel roster
at 252 days it was measured at 1.910 points a year with a standard
deviation of 0.190 over eight seeds, so a figure quoted without its
convention is wrong by about two points of annual index level. A test
rebuilds the portfolio from the bars, compounding a notional level day by
day, and asserts the row is its annualised log growth; a second assertion
puts the gap against half the cross-sectional variance, so the difference
is the term it should be rather than an error in either.

Both conventions are named wherever a figure appears: in the row's own
docstring, in this file's sections above, and in the reason
`facts.REPORTING_ONLY` carries and `facts.report` prints beside the
number.

### The protocol behind every figure

Two seeds decide a run, and every figure in the sections above was
measured with one of them held. The market seed drives every draw the
engine takes; the universe seed decides the roster it takes them against.
Each arm above varied the market seed over 1 to 30 on `Universe.random(40,
seed=111)` held fixed.

That roster opens 0.78 of a population standard deviation above fair
value, so its LEVEL carries a draw as well as a model: one build reads
-8.603 on it against +3.989 and +7.050 on rosters 204 and 209. Those three
across-roster figures come from the era's roster sweep rather than from
any arm here, and the design note carries them with the roster's +0.038
mean log deviation, the population mean of -0.002 and the across-roster
standard deviation of 0.052 that the 0.78 is computed from.

A paired difference between two arms on the held roster is a property of
the model, because the roster's own draw is common to both and cancels. A
level is a property of that roster, and a level that has to describe the
model is measured by varying the universe seed instead.

How much that costs is measured. A roster sweep puts roster 111 at 1.12
standard deviations dearer than the population at the open, and index
drift moves at about minus a hundred points per unit of opening
mispricing, so this roster carries a handicap near six points a year.
Across a hundred rosters the preset this branch started from reads a
portfolio median of +1.55 a year with 65 of 101 rosters above zero, where
roster 111 reads about minus five. So every level in this section is that
roster's and reads about six points worse than the model does, while
every paired recovery is the model's. The design note holds an earlier and
smaller sample that puts the same roster at 0.78 standard deviations, and
the two have not been reconciled.

The paragraph is in the row's docstring as well, since that is where a
reader meets the number.

### The index drift row

`facts.panel_statistics` reports `index_drift_pct`, the annualised drift
of the equal-weight index over the measured window. It is the panel's only
first moment. The other fourteen statistics are shape
measurements, and a market can hold every one of them in band while
losing a fifth of its value in a year: nine are exactly invariant to a
constant drift added to every name because they centre their arguments
before measuring them, and the five built on an absolute return move by
less than a tenth of their own across-seed noise. A test states that,
adding a known drift to a recorded run and asserting the new row moves by
exactly the amount added while no graded row moves by a quarter of its
noise.

It carries no real-market band, so it earns no verdict, cannot pass,
cannot fail, and `envelope.score` refuses it as an unknown statistic. That
is a gap held open on purpose. The fourteen graded bands were each derived
from a real reference panel at this module's own estimators; a first-moment
band would have to be derived from a real index over a matched window, and
that work has not been done here. Shipping a plausible-looking band instead
would grade every future preset against a number nobody measured, which is
the unprovenanced-band defect this module already corrected once. The
reason is carried as data in `facts.REPORTING_ONLY` and printed by
`facts.report` beside the number, rather than retyped into prose that can
drift from it.

Two method choices are worth naming, because each is the difference
between this row and a flattering one. Every name counts, including one
too short-lived for the shape rows, since dropping it is survivorship bias
on exactly the quantity being measured. And a gap in a name's bars is
spanned rather than dropped, so the sum over the window is exact.

### The explanation tree

`Engine.explain(ticker, day)` returns a tree. The root is the day's log
move in the printed price. Its eleven children are the contributions that
make the move up: the nine `truth()` columns, which sum to the day's
change in `mispricing_s`, plus the valuation's own move and the change in
the log distance from the model price to the print. All eleven are
measured rather than left over, so their sum against the move is an
identity the engine can fail.

Under each contribution is the Rust function that computed it, the dials
that function reads, the state it read at the open and the addresses of
the draws it consumed. A contribution that takes no draw of its own has
no draw leaf and the caveats name it.

Every node replays. A fork of the engine as it stood before the day
opened takes the node's logged draw values at their addresses, the day's
own inputs are replayed from the run log, and the contribution is
measured again on that run. `check()` does it for every node and reports
what did not come back, over three claims at once: the eleven sum to the
move, each node reproduces the contribution it sits under, and where the
run recorded the day, the replay reproduces the tape. No formula is
re-implemented in Python, because a replay is the engine running the same
day from the same state under the same draws.

`Engine.keep_explanations(from_day, to_day)` turns the two records on:
the draw log on every stream, and a copy of the engine at each of those
days' opens. The log starts one day early, because the jump a day's
`truth()` table carries was drawn at the close before it. Both records
read and neither writes, so a market with a window open is the market it
would have been without one, and the known-answer digest is the same
digest either way. The cost is one engine copy per kept day, taken
without the recorded tape.

`run_days` and `World.run` open a day through one path, so both fill the
store, and a day an agent traded replays with the flow the agent sent.
The MCP server gains an `explain` tool, which returns the tree and its
caveats and exposes no replay.

### The day ledger

**A run can commit to the state it held at the end of every day.** A
`DayLedger` takes a canonical hash of the engine's state at every close,
and a `RunManifest` written with one carries the Merkle root over those
hashes. `tradefloor.manifest.verify` recomputes k random days from their
committed predecessors and checks each leaf against the root, so checking
k days costs k days of simulation whatever the length of the run.
`Engine.state_hash` covers every field a state snapshot carries, including
the macro chain and the generator positions the market digest leaves out,
and `tradefloor.manifest.state_hash` computes the same digest in Python
from `state_snapshot()`. Nothing about a trajectory moves: the manifest
field is additive under the schema it already had, and `reproduce()`
behaves the same with the field and without it.

### Draw surgery in a world

Four surgeries sit on a world beside its interventions. `point` replaces
one draw, `unfire` stops a day's market jump, `window` re-randomises one
stream over a range of days under a generator derived from the seed, the
stream and a surgery seed, and `transplant` copies another world's draws
of one stream address for address. Each is checked after its day runs:
the draw log must show the patched address drawn on that day, at the site
it was aimed at, with the value that was installed, and a schedule that
moved in between raises rather than reporting a surgery that landed
somewhere else. A delisting moves the schedule as a listing does, because
the jumps stream takes one uniform per active company.

`unfire` says what it did. `Engine.market_jump_intensity` reports the
threshold `apply_jumps` compares its market uniform against, so the
record carries whether the jump could fire and whether the surgery could
stop it. At an intensity of zero the jump cannot fire and the surgery is
a no-op; above one the value the surgery installs is itself under the
threshold, so the jump fires in both arms and the record says the surgery
could not stop it.

### The draw log's range

A second trace widens the log's range in both directions and keeps what
was already recorded, so a caller tracing a window on top of an earlier
trace loses nothing.

### Draw addressing and the patching layer

A draw's address is its stream, whether it was a uniform or a normal, and
how many draws of that kind the stream had taken. An overlay substitutes
a value at an address without skipping the generator step, so every
address after it keeps its value and the draw counts are identical with
and without one. A log records what each stream delivered, with the day
and the call site, and `Engine.market_day_layout` maps a day and a company
to the market normals it drew.

The day a draw carries is stamped at the open it belongs to. Opening a day
pushes its mark and takes its endogenous news draws, so a day stamped
after the open left one run carrying two numbers: a three-day run numbered
from 100 logged five streams on 100, 101 and 102 and the news stream and
the marks on 0, 1 and 2, and the layout of day 100 resolved to nothing.

`run_days` numbers from the engine's own day counter unless told
otherwise, so a second call continues the first instead of repeating its
numbers. A caller that wants the old behaviour passes `first_day=0`. This
moves the day column of a recorded second run, from 0 and 1 to 2 and 3.

The state hash covers the two snapshot fields this adds, the per-stream
draw counts and the table of substitutions, in the order the snapshot
carries them. Both decide what the engine draws next, so two states
alike in every column and differing by one installed substitution
diverge from that point, and a commitment that skipped the table would
call them one state. A ledger written before this change carries leaves
computed without them, and a leaf written after it hashes to a different
value for the same market.

The market schedule follows the active roster. At ten names and eight
ticks a day the stream takes 584 draws, 73 a tick, and 536 the day after
a delisting, with the settlement taking four uniforms per active name per
tick throughout.

A restore drops the day marks, which described the run it replaced. The
log costs 32 bytes a record, pinned beside the type: at 60 names and 390
ticks the market stream takes 145,470 draws a day, 4.7 MB a day and 1.17
GB over a year, and its time cost did not resolve above the noise of a
shared machine.

### The measured cost

On `Universe.random(8, seed=99)`, seed 42, twelve days at 30 ticks a day,
verifying k days runs k days of ticks and no more, for k of 1, 3 and 12.
The whole run is 360 ticks, so a four-day verification is a third of what
replaying it costs. A ledger written without the states reaches day d by
running to it, so a sample costs the sum of d plus one over the days it
drew, which the verify seed decides. `Verification` reports the days, the
day-runs and which of the two costs was paid.

### The ledger size

The states sit in the ledger, beside the manifest rather than inside it.
On `Universe.random(40, seed=7)`, seed 42, 252 days at 30 ticks a day with
`record=False`, the ledger writes 4.88 MB with the states and 16.9 kB
without them, next to a 61.8 kB manifest. A manifest is meant to be read,
so it carries the root, the count and the hash version.

### The per-name volume array and a changed roster

`tradefloor.manifest.state_hash` hashes each per-slot array at the width
the snapshot carries and refuses a snapshot whose arrays disagree with the
roster. `volume_idio` was sized at construction and was the one per-slot
array `add_company` and `remove_company` left alone, so a snapshot taken
after a listing or a delisting was refused and a run whose roster changed
could not be checked by the Python side. The resize landed in this same
release, so such a snapshot is accepted now, the Python side checks the
run, and its hash agrees with `Engine.state_hash` in Rust.

No price depends on the width. Every shipped preset holds
`volume_idio_sigma` and `volume_idio_persistence` at 0.0, so every value
in the array is exactly 0.0. The test here reads the widths off the
snapshot rather than pinning them, so it passed through the resize
unchanged.

### The session flag at a close

`run_session(close_at_end=True)` leaves the binding's session flag set
where `close_market()` clears it, so the two spellings of one close
produce different leaves for the same market. The columns, the macro
chain, the generators and the draw count are identical across them, which
is why nothing saw the flag until a leaf covered it. The flag is left
where it is: clearing it would change the trajectory of a run that calls
`run_session` twice without opening a market between them.

### The per-name volume states

**The per-name volume states follow the roster.** `volume_idio` holds one
state per company and is positional against the roster, alongside the four
per-slot arrays `add_company` and `remove_company` already carried. It was
in neither, so its width stayed at whatever the engine was constructed
with. A listing left the new company without a slot of its own, a delisting
left every survivor reading its old neighbour's state, and the per-day draw
count from the volume-idio stream followed the stale width.

This reaches the volume-idio generator position, and the state snapshot, of
any run that lists or delists an instrument. Such a run draws once per
company per day from the mutation onward, so its position on every later
day differs from the position the same run reached in 0.6.2, and its
snapshot records the array at the roster's width.

The change reaches nothing else. All sixteen shipped presets hold
`volume_idio_sigma` and `volume_idio_persistence` at 0.0, so every state
stays exactly 0.0 and no shipped price path moves. The known-answer digest
holds at KAT 13. A run with a fixed roster agrees with 0.6.2 on its prices,
its snapshot and all seven generator positions.

**A checkpoint of a roster-changing run saved before this will not
restore.** It carries the array at the construction width, which its own
roster disagrees with. `restore_state` refuses it, and the error names both
widths and issue #148. Reproduce such a run from its seed and its order log
to get a snapshot at the roster's width.

A run whose roster changes can now be checked day by day. The ledger's
per-day hash reads each per-slot array at the width the snapshot carries,
so while `volume_idio` kept its construction width the Python twin refused
every snapshot taken after a listing or a delisting. Measured on `c85ee28`,
which carries the ledger without this fix: the twin raises on the first
snapshot after a listing, saying the column carries 64 bytes where the
roster needs 72. On this branch the twin agrees with the engine through a
listing and a delisting, and the sampled verifier checks a nine-day run
across both.

### The measured comparison

Two builds ran the same three runs on `Universe.random(8, seed=99)` at seed
42 under pt-v16, one at `f47c149` and one with the fix. Digests here are
`tradefloor.manifest.market_digest`, which the package ships, so a reader
can recompute them. The fixed-roster run of three days held every field,
including its market digest of
`f12c3ff678c769867bec02bbd44882f8fdf5c3273fde7dd41f80d8d6266be0e3`, its
whole state snapshot and all seven generator positions.

The roster-changing run listed one instrument after day one and delisted
index 0 after day two. Its market digest held at
`37b5a9e719764f4b6df2b8d85291d0ce6f326146d838bd28a8d5a9b6734cdb78`, and its
`draws_consumed` count, which totals the market, economy and external
streams and does not reach the volume-idio stream, held at 73,739. Four
fields moved: the volume-idio generator position, the snapshot digest that
carries it, the array width after the listing, which went from 8 to 9, and
the survivors' states after the delisting under a model with
`volume_idio_sigma` at 0.4.

That last arm is one no shipped preset reaches. At sigma 0.0 every slot
holds exactly 0.0, so a survivor reading its neighbour's slot reads the
same number and the defect has no visible consequence. The test that states
the claim builds its own `ModelParams` in the test, so this work adds no
preset and no dial.

### The draw schedule

`update_volume_idio` draws once per slot at every close, before the check
that skips the write at zero coefficients. The count is therefore the
array's width, which from this release is the roster's width. A run that
never mutates its roster takes the count it always took, because the
constructor already sized the array to the roster.

The volume-idio stream is derived from the root seed alone, so its position
after a run is a function of the seed and the draw count. The tests rest on
that. Eight names for three days, twelve for two and twenty-four for one
all reach one position, and the roster-changing run reaches the position of
a twenty-five-name run of a single day. The pre-fix count appears in the
tests as a fixed-roster run of the construction width, so no digest is
copied into an assertion.

### The stale checkpoints

`set_volume_idio` refused a width its roster disagreed with before this
change, and that refusal is kept. A pad or a truncation would attach each
state to whichever company now sits at that index, and the restored market
would continue plausibly under states belonging to other names. The error
now names the width the snapshot carries, the width the roster holds and
issue #148, so a reader meets the explanation where the failure happens.

That guard is the boundary of the restore. Everything `restore_state`
writes before it takes the snapshot's value, and everything it writes after
keeps the engine's own, because the error propagates out of the guard and
the later writes are attempted and never reached. An engine that has caught
this error holds one run's market beside another run's macro state, so drop
it rather than running it on.

### Noise attribution

`tradefloor.noise.attribute` forks one arm per draw from a common state,
installs one patch, runs the same days with the same agent, and reports
the difference. Event streams are attributed one logged draw at a time
and the market stream by day aggregate, where a common shift of delta
over the square root of the tick count moves a day's sum by delta
standard deviations of that sum. Every arm's draw positions are compared
against the control's on all seven streams, and the result is a caveat
either way.

The rows are single-draw differences through a market with feedback, so
they do not decompose the target. One more arm installs every changing
patch at once and the result carries the joint move and the residual
between it and the sum of the rows, with a caveat that claims no total.

An event lands at its day's close and is first seen at the next open, so
the default horizon reaches one day past the window whenever an event
stream is attributed, and a stream whose rows are all zero says which of
the target's day or the horizon stopped the measurement short. A column
target is read at the day it names rather than wherever the arms stopped.

### The amplification report

`tools/calibration/amplification.py` compares two presets draw for draw
and names the gain per site. The verdict names the gain first and adds a
mechanism reading only where the correlation supports one, with the row
count, the contributing count and a standard error beside it, and the
three cut-offs printed as the conventions they are.

### Implied draws and the shadow run

For each real trading day, `tools/shadow/shadow.py` finds the day
aggregates under which the engine reproduces the day's observed closes,
feeds them in and walks the engine along the real path. The estimate is
the maximum a posteriori vector per day, by Levenberg-Marquardt on the
forward map, and the report says so: the series is shrunk by the prior on
an under-determined system, so the run measures the estimator's own null
on days the engine generated from a standard normal and compares against
that rather than against the prior's.

The sensitivity column is a finite difference taken fresh at the accepted
solution, because the optimiser's carried Jacobian is updated by secant
between refreshes and differed from a fresh one by a factor of nine on one
day of four. The binding clamp verdict reads the same fresh column.

The greedy jump step recovers a downward market jump and not an upward
one: the jump's mean is negative, so an upward jump needs a normal whose
prior the likelihood cannot repay. Measured on planted jumps, every
downward jump from -85 to -208 basis points was recovered and no upward
jump at any size to +112, and the two fired-count rows carry that
envelope.

A run writes its per-day solutions beside its report, so `--render`
regenerates the report on corrected code without solving again, and a
resumed run carries its whole record through the checkpoint rather than
only the prices and the stream positions. A render that recomputes a
column leaves the recomputed run beside the report too, so a reader who
wants the column rather than its median does not pay the 38 minutes
again. The window a fetch asks for is midnight UTC on the dates it names,
so the url is a function of the date and not of the machine's zone: a box
in UTC and a machine four hours west asked for windows four hours apart
and one close of 25,914 came back 1.13e-06 apart.

### The mechanism language

`tools/mechanism` holds a declarative specification of one mechanism: its
dials with defaults, the state it reads and writes, the stream it draws
from, and a body. A checker types every expression by its draw effect and
refuses a count that depends on a dial or on state, requires equal effect
on both branches of a conditional and proposes draw hoisting where they
differ, and proves the body inert at its defaults by evaluating it with
uniforms as symbols. Anything it cannot decide is a failure to prove; it
assumes only that a decided zero absorbs a multiply, because adding zero
is the identity everywhere except on a field holding negative zero.

An emitter turns a checked mechanism into Rust that uses only the `mathx`
surface, keeps source order, parenthesises every compound operand, never
emits `mul_add` and pins recorded constants to their bits, and refuses a
mechanism the prover rejected. The market and company jump mechanism is
re-expressed and the body of `Engine::apply_jumps` is generated from it,
with the known-answer digest as the proof that the emitted Rust is the
shipped mechanism. The specification's digest covers what the emitter
reads and leaves out prose it does not, and every declared dose is the
value a build has.

Preset records gain a mechanism set beside the untouched fingerprint: the
specification digest, the stream and the doses the preset runs.

### The prints table

**Every print says what it was made of.** `Engine.prints()` is a table
beside `bars` and `truth`, one row per instrument per tick: the print, the
model price behind it, and the two log distances between them. `shock` is
the distance from the last print to the model price, `absorbed` is the
distance from there to the tape, and the two sum to the print's own move.

**A depth counterfactual measures liquidity's share.**
`Engine.settle_depth_counterfactual(True)` settles every open tick a second
time against every resting level, under the same four uniforms and from the
same book state, and adds `unbounded_print` and `liquidity_share` to that
table. It takes no draw and its fills reach no company field, so the market
and the known-answer digest are the same with it on. A run takes three to
four times as long with it on, so it stays off until a caller asks for it.

### The depth bound

`settle_price_through_book` quotes only the levels a tick's flow can reach:
`min(BOOK_LEVELS, max(2, ceil(tick_volume / level_size) + 1))`, computed
before any draw. Quoting all ten was measured at roughly four times the
settlement cost for depth ordinary flow never touches. `SettleOptions`
gains `depth_multiplier`, read at that one site and nowhere else. The four
settlement uniforms, `fair_value` and `buy_fraction` are all decided
without it, so the same tick settled twice at two multipliers is a
controlled comparison rather than two markets.

At the shipped `1.0` the bound is returned untouched rather than multiplied
by one, so the change is a no-op a reader can check by looking. The
counterfactual runs at `f64::INFINITY`, which lifts the bound to every
level the maker quotes.

The second settlement runs inside the tick, on its own book, between the
real settlement and the maker-inventory carry, so it sees the state the
real one saw. It is served the four uniforms the real settlement was
served, rewound, so the stream position and `draws_by_stream` hold at the
values they had. It is skipped on the recorded-stream replay path, where
settlement draws from the caller's source and there is no buffer to rewind.

### The absorption column and the share

`absorbed` is measured against the printed tape, so it holds the second
circuit breaker as well as the book. On a halted name the breaker is what
absorbed the shock, and booking it to the book would be the more flattering
of two wrong answers.

`liquidity_share` is `log(print / unbounded_print)` over the print's own log
move. It comes out NEGATIVE on most rows that carry one. The depth bound
truncates a walk: an order that exhausts a shallow book stops there, while
against every resting level it keeps filling and prints further from where
it started. So the real print sits between the last print and the unbounded
print. The unbounded book's move is `1 - share` times the printed move, so
a share of -1 says the deeper book would have moved the price twice as far,
and nothing bounds the ratio by one. Measured over three days of twelve
names, 1,409 of the 1,516 rows where the bound moved a print are negative.

The share is exactly zero where the two prints coincide, which covers every
tick that did not settle, and NaN where the print did not move and the
deeper book would have moved it. Dividing anyway would put an infinity in
the column, and one infinity turns every mean taken over it into an
infinity too; that case was measured at 7 rows in 14,040.

The counterfactual prints one tick from the real state. It cannot say what
a deeper book would have done to the next tick, because the maker inventory
it would have left is discarded and the tick after this one is the one that
actually ran.

### The breaker's own column

`absorbed` is measured to the printed price, so it carries the second
circuit breaker as well as the book. `clamp` is the breaker's own part and
`absorbed - clamp` is the book's.

The two cancel, so one column could not carry both. Measured over three
stressed runs of eight names, the book and the breaker pull opposite
ways on every clamped print without exception, and on 90 of 146, 22 of 41
and 22 of 37 they cancel to the last bit. `absorbed` then reads exactly 0.0
on a name the breaker had just moved 513 basis points, which is the same
value it takes on a tick that never settled at all. Where it is non-zero the
clamp is a median 54% to 111% of it, and up to 26.8 times it.

`shock + absorbed` is still the print's own log move, so nothing that read
the two-term split has to change. The three-term form is
`shock + (absorbed - clamp) + clamp`, measured to a worst error of 2.91e-16
over 9,504 rows.

### One metadata key on the prints schema

The table carries a single `caveat` entry, computed from the state the
caller is in. `arrow::Schema::metadata` is a `HashMap` and the IPC writer
serialises it in that map's iteration order, which `RandomState` reseeds per
map, so three keys produced two different digests for one table inside a
single process. The field's type belongs to `arrow`, so nothing on this side
can supply a hasher; one entry has no order to vary. The two keys that went
were both derivable from the schema, and a consumer asking whether the depth
counterfactual ran should read the column list, which is the more reliable
answer.

A day whose sessions disagreed about the counterfactual gets a third caveat
of its own. Its columns are shorter than the day, so both are dropped rather
than served with a gap, and the caveat names that case instead of telling
the caller to set an option they already set.

### The reading in the liquidity-crisis example

`examples/experiments/liquidity-crisis` runs both arms with the
counterfactual on and reports what the book paid for. `market.liquidity` at
40% is a claim about depth, and the column reads it back off the tape. On
the last day of the post-fork window, flow reached the end of the quoted
depth on 0.76% of control prints and 2.04% of crisis prints, at the same
median share of -1.002 in both, and the mean distance from the model price
to the print was 23.3 and 34.7 basis points. The crisis changes how often
the book runs out rather than how far a print goes when it does. The arms
are the same arms, and their exposure numbers are unchanged.

### The browser build

`tools/wasm/build.sh` and `check.mjs` still built and checked against
`pretium.wasm` and `pretium.js` after 27223f2 renamed the crate to
`tradefloor`, so the wasm32 build failed outright: `wasm-bindgen` had
nothing at the path it was told to read. Fixed to `tradefloor.wasm`
and `tradefloor.js` in both files.

`check.mjs`'s one check, comparing the browser build's
`fixed_simulation_digest` against the native one, is otherwise
unchanged, and passes again now that the build reaches it.


## 0.6.2

**A scenario reaches the agent.** `market.liquidity` is the one scenario
lever that touches execution, and the figure an agent read was built once
and never re-read. A depth shock thinned the book while the volume the
agent saw, and the order cap it was clipped against, stayed at their
pre-crisis values. `World.run` and `evaluate` now re-read the column each
day a scenario fires.

**A bad answer no longer ends a run.** `World(on_refusal="skip")` records
an unusable agent response, trades nothing that step, and carries on,
counting it apart from the market-side `refused`. A recording that cannot
answer raises `ReplayMiss` and still stops the run.

**A dead live run keeps what it paid for.** Every adapter takes `prior=`, a
recording consulted before the provider, with a mandate guard and a
replayed-versus-called count.

**The agent's own noise floor is measurable.** `resample()` asks one
recorded decision N times per arm and reports the within-arm spread beside
the between-arm gap, refusing when the two arms' inputs differ beyond the
intervention.

**An exact roster reaches EDGAR, and a large universe reaches an agent.**
`fetch(ciks=)` returns those filers and no others, accounting for every
request. `observe(detail=)` renders a chosen few in full and the rest as
compact rows.

**Saved files are the same bytes everywhere.** `Snapshot.save`,
`Transcript.save` and `Survey.save` write bytes rather than text mode,
which emitted CRLF on Windows.

<!-- release-note-ends -->

### The file digest and the content digest

`Snapshot.hash` is computed over canonical JSON and was always portable.
That is the right definition and it does not change here. It is also not
the thing a reader reaches for when checking that two people hold the same
file: that is `sha256` over the bytes, and the library was making it a
property of the operating system.

The failure it caused, in full. An experiment saved a snapshot, recorded
`sha256` of the file in its published results, and shipped a validator that
compared the two. Everything passed. A clone of the same commit into a
clean directory produced a different digest:

```
edgar-2026-08-31.json   0b9f6bf946d8663d...   Windows working tree
                        959783efb512c335...   fresh clone, same commit
```

`.gitattributes` sets `* text=auto eol=lf`, so git normalised the 5,450
CRLF pairs on the way in and never put them back. The working tree and
every clone of it held different bytes, the published digest described the
working tree, and the validator compared the file against itself.

### Reading

A recording costs API calls to make, and every recording a Windows user
already holds carries CRLF. `Snapshot.load`, `Transcript.load` and
`Survey.load` are unchanged and still read them, so this fixes a
portability bug without creating a data-loss one.

### The guard

`tests/test_portable_writes.py` exercises each writer and then scans the
package source for a text-mode write anywhere, because four savers checked
one at a time leaves the fifth unguarded, and the fifth is where this came
from. On Linux every one of them passes trivially, which is how the
existing suite stayed green through the bug.

### The exact roster

`limit` with `rank_by` answers "the largest N filers", and an experiment
whose universe was decided elsewhere asks a different question. The two have
different failure modes: a ranked fetch returning 98 of 100 is still a ranked
universe, and an exact fetch returning 498 of 500 has lost two members, which
is the finding. So `ciks=` accounts for every request. A filer with no
diluted EPS, no share count, no submissions record, no listed ticker or no
sector mapping lands in `Snapshot.excluded` under its own reason, and
`notes["requested_ciks"]` carries the request so membership is checkable from
the file alone.

`limit` and `rank_by` grew a `None` default so that passing one beside a
roster could be refused. Resolving to the same 100 and `"equity"` as before,
so every released call returns the snapshot it returned.

### The large universe

Nine lines an asset reads well for four names and buries the question at four
hundred. The split is a rendering decision and not a narrowing: the allowlist
gains no field, the sector summary is arithmetic over the asset rows, and the
sealed-engine proxy and the hidden-value scan in `tests/test_finrobot.py` now
run over the large rendering as well as the small one.

The panel belongs to the experiment, so it travels in `state()` and the fork
agreement reads it. Two arms shown different names would be answering
different questions, and the price history, the last decision and the cadence
would all match while that went unrecorded.

### The dependency ceilings

`pyautogen<0.11` resolves to the AutoGen 0.4 rewrite, which installs
`autogen-agentchat` and `autogen-core` and provides no `autogen` module, so
`finrobot.functional.rag` fails on `No module named 'autogen'` before
`SingleAssistant` is reached. The rewrite starts at 0.3, so that is the
ceiling. `anthropic<2.0` admitted the 1.0 SDK, which removed
`anthropic.types.Completion` that autogen 0.2.35's client imports.

One consequence worth stating, because it decides which model a study
can use. `pyautogen` 0.2.35 sends `temperature` on every request: unlike
`top_k`, `top_p` and `stop_sequences`, which it drops when unset, the
parameter is non-nullable and defaults to 1.0. Claude Opus 5 and Sonnet 5
have deprecated it, and answer

```
400 invalid_request_error: `temperature` is deprecated for this model.
```

to any explicit value. They run through FinRobot at the default of 1.0.
A reproducible study wants temperature 0, and temperature 0 is the one
setting those models refuse, so every recorded run in this repository uses
`claude-sonnet-4-5-20250929`. Measured 2026-09-01 against the live API.

### The recording check

`tests/test_integrations.py` parsed every response in every committed
recording and read a failure as a corrupted file. `parse` is strict on
purpose, so a long enough recording of a real model contains output it
refuses, and the check forbade committing an honest one. It now compares the
number of refused responses against a count the recording declares in its
meta. An absent count means zero, so every fixture committed before this is
unchanged.

### The cost

`liquidity_crisis` takes quoted depth to 40%. Measured on
`Universe.random(6, seed=11)`, forked, with the scenario on one arm:

```
engine avg_volume column   543,983  ->  217,593     exactly x0.40
what the agent was shown   543,983  ->  543,983     unchanged
participation cap           27,199  ->   27,199     unchanged
order actually permitted    27,199  ->   27,199     unchanged
```

The agent asked for 60,000 shares in both arms and was allowed the same
27,199 in a book holding 40% of the ladder. The clip exists to stop an order
the market cannot absorb. It was sized against a market that no longer
existed. The agent never learned the order was unrealistic, because the only
signal was a worse fill.

`Observation.book()` on the same object DID show the thinned ladder, so the
observation disagreed with itself: one accessor in the crisis, one in the
market before it.

`liquidity_crisis.yml` says an evaluation reading only the price series will
score an agent as though it traded for free. The harness was doing a version
of that.

### The re-read

`World.run` re-reads `avg_volume` at the top of each day, straight after
`Scenario.apply`. That is the earliest point at which the day's value is
known, and once a day is sufficient because nothing in the engine writes the
column. `evaluate` does the same, guarded on a scenario being present: with
none there is nothing to move it, and a daily column copy through every
ordinary run would be work nobody asked for.

The engine's column starts equal to the instrument list, so with no scenario
in play the re-read returns exactly the value the old code held.
`tests/test_observed_depth.py` pins that, alongside the four cases that fail
without the fix.

### The two sites

Both `World.run` and `harness.evaluate`, three lines apart in different
files. `_run_untraded` builds no Observation and needed nothing.

### The case for the library

`compare` reports one trajectory per arm, which is the whole answer for a
deterministic policy and half of one for a language model. The agent is the
only stochastic component left in an otherwise bit-identical experiment, and
a single pair of trajectories cannot separate "the agent responded to the
intervention" from "the agent answered the same question two ways". Every
study running an LLM agent has to hand-roll the same paired resampling, and
none of them did.

A small N suffices here only because of the determinism, so the library
that provides the determinism should provide the measurement that cashes it
in.
Everything except the agent has already been eliminated by construction:
`agree()` verifies the whole engine state at the fork, and the two arms'
inputs at the first post-fork decision differ only in the intervened fields.
Outside this environment the same question would need to separate agent
variance from market noise, path dependence and different starting states,
with no counterfactual available at all.

### The measurement it exists to prevent

Live, both arms at temperature 0. At the first post-fork decision the two
prompts differed in 2 lines of 376, `federal_funds_rate` and
`corporate_bond_yield`, with every price, position and return byte-identical.
The recorded trajectories then diverged, readably:

- control: "Deploying excess cash into quality positions: buying IBM's dip"
- +200bps: "Reducing exposure to IBM after its sharp 4.2% single-day
  decline ... to manage downside risk"

Resampling those two exact prompts eight times each:

| arm | distinct answers in 8 | modal share | net (buys - sells) |
|---|---:|---:|---:|
| control | 4 | 62% | 0.62 +/- 0.99 |
| +200bps | 1 | 100% | 0.00 +/- 0.00 |

The between-arm gap of 0.62 sits inside control's own within-arm standard
deviation of 0.99. The recorded split was one of control's four available
answers. The study around it passed 71 publication checks.

The same numbers carry a second reading the feature reports on purpose. One
distinct answer in eight calls against four is a difference in decision
STABILITY rather than in direction. `compare` cannot see it. It is
observable at all only because the input was byte-identical eight times.

### The refusals

Inputs that differ beyond the intervention. A controlled resample needs
two arms answering one question, so a difference the intervention cannot
account for is refused and the error names the fields that moved. This is for the FIRST post-fork decision; by the second
the market has already answered the intervention and every line differs.

A zero-variance arm reports a standard deviation of 0 and a separation of
`None`. A ratio over zero is undefined rather than large, and `inf` printed
in a published table reads as an overwhelming result.

Refusals are counted, never dropped. An agent that returns unusable output
on three of twenty calls is a finding, and sampling until twenty parse would
hide it. Exactly N calls are made per arm.

No p-value. The gap is reported in units of the noise floor and the reader
judges; a significance test would imply an inference model nobody has
argued for here.

### The re-ask hook and the record

`FrameworkAdapter.reask(entry)` performs one live interaction from a record
entry and changes no adapter state: no price appended to the history, no row
added to the record, no write to the recorder. A resample happens after the
run, and the adapter's state belongs to the run, so all five adapters
implement it under a contract check that asserts the state is untouched.

`agent.record` entries gain `payload`. The record's own docstring says it
carries the whole chain, "observation to input to response to validated
action to order", and it began at the rendered input -- so nothing joined a
decision back to what the agent was shown, and an adapter that builds its
framework input from the payload rather than from text had nothing to
re-ask.


### The refusal policy

`World.run` called the agent with no guard, so a `DecisionError` from
`act` went out past the trace, past the checkpoint and past every artifact
the caller was about to write. The adapter layer documented the opposite:
"the caller decides whether that ends the run or costs the agent a step".
The caller is `World.run`, and the choice did not exist.

Measured on a live pilot: 24 names, 60 planned decisions, and on call 36 a
per-action `rationale` field. `parse` is right to refuse it -- dropping an
unknown field executes a trade the agent conditioned on something it never
got -- and 35 recorded interactions, 20 simulated days of shared history
and both arms of a fork went with it, at a malformed rate of 1 in 35. A run
long enough to be interesting is a run long enough to hit that.

An agent that returns unusable output is an agent behaving badly, and
behaving badly is a measurement. It belongs beside `refused` in the trace
rather than in a traceback.

`refused` and `unusable_responses` are two columns and are never summed. A
market that rejected an order and an agent that could not format an answer
are different failures with different remedies, and one column covering
both would make an unusable agent read as an illiquid market.
`Comparison.ROWS` carries both, because two arms with different refusal
counts are not comparable on turnover without the reader being told.

What this does not do: retry. A second attempt at the same question is a
second agent, and the experiment would then be measuring the retry policy.
`parse` is unchanged and stays strict.

### The exempt refusal

`replay_response` raises `DecisionError` when a recording has no answer for
an input, so a blanket skip turns a transcript covering nothing into an
agent that refused everything -- and the run then completes, writes its
artifacts and publishes that. Measured while building a study against this:
two arms replayed against a transcript covering neither, reported twenty
refusals each, and produced an empty series two hundred lines later.

So a missing entry and a recorded null both raise `ReplayMiss`, and
`World._ask` re-raises it while skipping everything else. A model that
answered badly is a fact about the agent; a recording that does not cover
the question is a fact about the experiment, and the two have opposite
remedies.

`ReplayMiss` subclasses `DecisionError`, so every caller written to catch
one and charge the agent a step keeps catching it. Only the skip policy
treats it differently.

### Resuming a recording

A refusal policy does not survive a rate limit, a dropped connection or a
keyboard interrupt. The recorder is written by the caller at the end, so a
run that died had paid for N calls and kept none of them in a form the next
run could use, and the second attempt re-asked every question it already
had an answer to.

`prior=` is consulted before the provider, on the same key the replay path
uses. The market is deterministic, so a resumed run reaches the same
prompts and computes the same digests, and every recorded answer is still
an answer to the question being asked.

Two guards. A `prior` whose `instructions_digest` differs from the current
one is refused at construction: instructions do not travel in the input the
key is computed over, so every recorded key would still match and the run
would complete, answering the instructions you have now with decisions
taken under the ones you had then. And the resulting recording carries
`replayed_from_prior` and `called_live`, so a file stitched from two
sessions cannot claim to be one.

Both counts are derived from the two transcripts rather than accumulated as
the run goes. A fork shares one recorder and gives each arm its own
adapter, so a counter living on an adapter would split across the arms and
each half would understate the file it describes.

`World.fork` now carries `on_refusal` to both arms, for the same reason it
carries every other setting: an arm that reverted to the default would die
on output its sibling counted and continued past.

## 0.6.1

**Four agent frameworks reach the same market.** `tradefloor.integrations`
gains adapters for the OpenAI Agents SDK, PydanticAI and LangGraph, alongside
a generic adapter over any plain Python function. Each hands its framework an
allowlisted observation and validates the decision that comes back, so two
frameworks can be compared on one seed through one harness. FinRobot, which
came first, keeps its own integration and extra.

```
pip install "tradefloor[openai-agents]"   # or [pydantic-ai], or [langgraph]
```

**Recorded decisions replay exactly.** A `Transcript` keys each exchange by a
digest of the exact input the framework was sent, so a run that cost money
replays for free, with the framework uninstalled and no network reached. The
key comes from the input, so an edited experiment stops instead of answering
the new question with the answer given to the old one.

**The examples run offline.** `examples/integrations/` carries a folder per
framework, each holding a script and a notebook that drive the real framework,
with a deterministic function where a model would sit. No API key, no provider
account, a few seconds each.

**Two fixes.** FinRobot's rendered observation named the participation cap and
not the funding cap, so an agent sizing to the only limit it saw could be
refused by one it never saw. It now states both. The Claude example printed a
scorecard for a run where every decision failed.

The market is unchanged: same digest, same preset.

Below the marker: the shared layer, the decision contract, version floors
and CI lanes.

<!-- release-note-ends -->

### Detail

**The shared layer.** `tradefloor.integrations.common` holds what every
adapter needs and no framework owns: the observation allowlist, the decision
schema and the Pydantic model derived from it, two-stage validation,
transcripts and replay, credential-screened adapter metadata, and the
`FrameworkAdapter` base an adapter completes by implementing one method.
`ReplayMixin` supplies the record-and-replay branch, so a new adapter states
`prepare()` and `call()` and gets the rest. The layer derives from the
FinRobot integration, which came first and settled those questions; the only
change to FinRobot itself is that its `DecisionError` now descends from the
shared one, so a caller can catch either.

**What a decision may contain.** The engine takes signed share quantities
through `Portfolio.execute`, so a decision names a symbol, a side and a
quantity. `parse_decision` refuses an unknown key at the top level or on an
action, naming it, because a silently dropped `stop_loss` would leave an
agent believing it has protection this market cannot give. An envelope
carrying no `actions` key is refused on the same reasoning, since unwrapped
framework state would otherwise score as a considered hold.

**The extras.** `openai-agents>=0.22`, `pydantic-ai-slim>=2.36` and
`langgraph>=1.2`. Each floor is the version its adapter was written and
tested against, since a floor at the major admits releases the adapter has
never met. `pydantic-ai-slim` supplies the `pydantic_ai` module without the
six provider SDKs the umbrella package adds. There is no aggregate extra: the
frameworks are alternatives, and installing three dependency trees to use one
would mostly install conflict surface.

**Reproducibility.** Tradefloor reproduces a run from its configuration and
the sequence of agent actions. A live model call sits outside that guarantee,
which is the gap the transcript closes: a recorded exchange replays exactly,
and `adapter.provenance()` records the framework, its version, the provider,
the model, the generation parameters and the decision cadence that a market
replay cannot reconstruct.

**The CI lanes.** The batch job in `suite.yml` installs the three frameworks
by name and asserts each one imports before pytest starts, on the reasoning
that file already carries for MCP and Gymnasium: a lane that skips is a lane
that reports green by not running. `finrobot` stays out of that install,
since its tests replay a recorded run and one of them can only execute while
the package is absent.

## 0.6.0

**`pt-v16` is the default.** The market itself is different, so a run that
took the default under 0.5.0 does not replay here. A run that names its
preset replays exactly, and every preset from `pt-v1` on stays selectable:

```python
eng = tf.Engine(seed=42, universe=u, model="pt-v14")
```

pt-v16 holds the complete card over twenty-six seed blocks at one hundred
seeds each, thirteen of them never touched by any search: 25 of 26 on the
union full house, 26 of 26 on both crisis instruments, and a driven noise
ratio of 1.12 against the candidate's 1.30. One row sits out of band, a
`corr_asymmetry` median at one block, 0.0025 past a floor whose own
derivation noise is 0.038.

**The VIX learns fear.** It tracked its own market's realized volatility at
0.16 correlation against a real 0.87, and decayed as fast as it rose.
Measured on ^VIX and ^GSPC over 2004-2025: tracking 0.16 to 0.57, spike
asymmetry 0.95 to 1.28 against a real 1.20, day persistence 0.90 to 0.985.

**A policy rate reaches equities before the first meeting.** The daily credit
floor touches the spread every day, so a policy-only ramp transmits inside
the window that used to hold it: 0.00% under pt-v14 against -3.34% on the
shipped default at 40 days. Pin `pt-v14` to keep the sharp boundary.

Below the marker: scenarios as a file you can hand to somebody else, forking
as a true copy of the engine, a stress-activated forced-flow segment shipped
inert, and manifest lineage.

<!-- release-note-ends -->

### Detail

**`pt-v16` is the default.** The market itself is different, so a run that
took the default under 0.5.0 does not replay under this release. A run that
names its preset replays exactly, and every preset from `pt-v1` on stays
selectable and bit-reproducing:

```python
eng = tf.Engine(seed=42, universe=u, model="pt-v14")
```

pt-v16 is the first preset to hold the complete card at the deepest standard
this programme runs, over twenty-six seed blocks at one hundred seeds each,
thirteen of the blocks never touched by any search:

| | `pt-v16` as shipped | the pre-trim candidate |
|---|---|---|
| union full house, both panels | **25 of 26** | 24 of 26 |
| crisis co-movement in range | 26 of 26 | 26 of 26 |
| crisis lever in tolerance | 26 of 26 | 26 of 26 |
| driven noise ratio | **1.12** | 1.30 |
| out-of-band rows anywhere | one, stated below | `corr_asymmetry` twice |

Three ideas compose it. `qe_pe_gain` 0.0 silences a channel whose driven input
is a proxy anticorrelated with measured Fed purchases at -0.485.
`vix_cycle_amplitude` 0.85, `sector_loading_beta_slope` 0.7 and
`market_beta_down_asym` 0.025 are the correlation-asymmetry composition, in
which down ticks of the factor transmit harder, funded by sector-loading
dispersion and seasoned by pulling the business-cycle share of the VIX in. The
six noise sources then scale together by 0.86, a trim that has to be joint,
since any single source alone re-balances the market and idiosyncratic split
and collapses correlations instead of re-levelling. `volume_move_response`
rises from 0.6 to 1.0.

**And the VIX learns fear.** Left as it was, the engine's VIX tracked its
own market's realized volatility at 0.16 correlation against a real 0.87
and decayed as fast as it rose. pt-v16 closes the loop: a third of the VIX
target is the variance process's own inverse (`vix_realised_vol_weight`
0.3), fear decays at six tenths of the rate it arrives (`vix_decay_ratio`
0.6, a new dial, bit-inert at 1.0), and the reversion slows to match
(`vix_mean_reversion` 0.06). Measured on ^VIX and ^GSPC over 2004-2025:
realized-vol tracking 0.16 to 0.57, spike asymmetry 0.95 to 1.28 against a
real 1.20, day persistence 0.90 to 0.985. The fold's price is the one
out-of-band row in the table: a `corr_asymmetry` median at one block,
0.0025 past a floor whose own derivation noise is 0.038. Crisis frequency
stays below real -- every mechanism that raised P(VIX>30) broke certified
statistics, three families measured to their deaths in the design record,
and a market afraid of itself at the right frequency belongs to a later
era's recalibration. The fear-event dials (`vix_jump_intensity`,
`vix_jump_scale`) ship inert, taking no draws at zero.

**A policy rate now reaches equities before the first meeting.** Equities
discount off the corporate bond yield, which the central bank recomputes from
the 10Y at meetings, the first of them 45 days out. A policy-only ramp
therefore moved nothing inside that window, and both the scenario
documentation and `tests/test_scenario.py` stated it as a fact: 0.00% at 40
days. `daily_credit_floor_gain` re-asserts both credit floors on every daily
step, so from pt-v15 onward the spread is touched daily and the ramp
transmits inside the window. Measured on `Universe.random(20, seed=4)` at sim
seed 5, read at 40 days: pt-v12 and pt-v14 give 0.00% and the shipped default
gives -3.34% on the median instrument. The 0.4.2 entry named this consequence
in advance and placed it at a preset boundary, which is where it arrived. Pin
`pt-v14` to keep the sharp boundary.

**What is still short, stated.** The `corr_asymmetry` median of -0.022 is
band-complete and sits below every real reference window, and the driven
window at 1.12 is the closest this model has come to the real 1.00 without
reaching it. Both gaps are named in the record.

**Scenarios: a named collection of interventions, in YAML or Python.**
`tf.Scenario` names targets from a registry of twelve fields the engine reads,
applies `set`, `add` or `multiply` on one of four shapes, and fingerprints the
resolved experiment. Every target carries a measured note saying what it is
worth: four of the twelve are too small to see over a hundred days, and
`macro.fear_greed` measures 0.00% everywhere. Six scenarios ship in the wheel,
`tradefloor scenario list|validate|show|diff|targets` reads a file without
running a market, `World.apply(scenario)` drives a counterfactual arm from a
document, MCP gains `list_scenarios`, and `RunManifest` records the resolved
scenario. A scenario built only from pins serialises as before, schema 1, byte
for byte. `Engine.pin_macro` gains `gdp_growth`, `unemployment_rate`,
`tariff_rate` and `oil_price`, with `Engine.macro_fields` as the read side,
and `Engine.set_avg_volume` writes the column the market maker quotes off.

**`tradefloor.counterfactual`: one agent in two worlds that differ by one
variable.** A `World` is the run loop of market, agent, portfolio and macro
path, with `checkpoint()`, `fork()` and `intervene()` between days. `agree()`
verifies that two arms started identical across nine checks, and `compare()`
finds the first step at which the macro, the decision, the orders, the prices
and the portfolios came apart. The agent is a parameter throughout, so
`tradefloor.integrations.finrobot` runs the same experiment with a real
FinRobot agent swapped in, behind an observation allowlist that keeps fair
value, the attribution and the macro path ahead on the Tradefloor side.
`examples/rate-shock/` and `examples/finrobot/` are the two studies.

**Forking is a copy of the engine.** `tf.branch` rebuilt a fork from a
hand-maintained field list that was incomplete in four ways, the serious one
being that a mid-day fork lost the day's endogenous news and priced
differently from its parent, live on `pt-v14` and every preset from `pt-v11`.
`Engine.fork` copies the engine and `tf.branch` calls it, so forks can now be
checkpointed, forked again and written to a manifest, with `universe` and
`seed` optional, and no trajectory moves as a result.

**Lineage and state.** `Checkpoint.fingerprint` digests the canonical
serialisation, `RunManifest.of(..., derived_from=checkpoint)` records it, and
`verify_lineage(checkpoint)` checks the claim on identity before history.
`state_snapshot` gains the day's endogenous news, the universe's remembered
stress and the per-name volume states.

**Flow composition: crashes get their sellers.** The engine gains a
stress-activated forced-flow segment -- a common sell lean in the price
path above a fear threshold, weighted by beta (leveraged names get sold
hardest) and drawing down a finite, checkpointed budget, because
deleveraging completes and stops (an instrument that held VIX 65 for sixty
days caught the infinite-sellers version grinding prices into their
clamps). Five dials, all shipped at zero and bit-inert there:
`forced_flow_gain`, `forced_flow_threshold`, `forced_flow_beta_exponent`,
`forced_flow_reservoir`, `forced_flow_replenish`. At the measured
configuration (gain 0.003, threshold 50, beta exponent 2, reservoir 400,
replenish 0.05) the replayed 2020 crash coheres at 0.78 against a real
0.78 where the preset alone reads 0.52 on a good seed and 0.22 on a bad
one, and crash dispersion improves -- at the price of a crash about eight
percent hotter than the real one. Funding that price inside the certified
preset was measured and failed, so the presets do not carry it: the
segment is for embedders who want a market that crashes like a market,
switched on with five documented numbers.

**Smaller fixes.** `truth(day=N)` and `bars(day=N)` select a day, having
previously discarded the argument. A failed `reproduce()` separates the three
cases the evidence distinguishes, `Checkpoint.from_json` names what is missing
on a truncated payload, and `market_vol_gamma` joins the reparameterised set.
Six environment knobs accept `TRADEFLOOR_` alongside `PRETIUM_`.

**Packaging.** `.gitignore` still named `python/pretium/` after the rename, so
a development build wrote an extension and a Windows debug database into the
source tree, both were committed, and maturin packaged the debug database into
every wheel built from such a tree, including the published 0.5.0. The ignore
rules now name the directory that exists.


## 0.5.0

**The library is now `tradefloor`**, formerly `pretium`, which published
through 0.4.3 and stays on PyPI and crates.io permanently, because published
results cite those versions. Install `tradefloor`, `import tradefloor`, crate
`tradefloor`, MCP server `tradefloor-mcp`, documentation at
https://tradefloor.dev. The rename changes no behaviour and reproduces the
same known-answer digest as 0.4.3. Preset names stay under the old prefix, as
does the manifest `pretium_version` key.

**pt-v15 registered: the slow-variance mixture, the credit floor and sector
dispersion.** pt-v14 plus six numbers, including the two-timescale variance
mixture carried inert since the pt-v4 era. Over thirteen thirty-seed blocks
the 504-day panel ties with pt-v14, crisis co-movement goes 12/13 to 13/13,
and the range across blocks falls from 0.0774 to 0.0464 inside a 0.0630 band
width, making it the first preset to hold both crisis instruments on all
thirteen. Selectable by name, with `pt-v14` the default.

<!-- release-note-ends -->


## 0.4.3

**0.4.2 changed `pt-v13` and `pt-v14`, and it should not have.** If you pinned
either preset, this release puts them back exactly as they were in 0.4.0 and
0.4.1, bit-identical on all fourteen certified statistics.

The 0.4.2 fix pointed the dollar's safe-haven gate at `crisis_vix_threshold`,
which both presets override to 30.88, so their dollar gate moved from 25.5 and
their trajectories moved with it, a breaking change shipped in a patch
release. All fourteen statistics moved and all fourteen stayed in band, the
largest displacement being 4.48% of a band width, so no result computed under
0.4.2 is wrong, though a run recorded under `pt-v14` before 0.4.2 does not
replay under it.

**The dollar gate is its own dial**, `usd_crisis_vix_threshold`, defaulted to
the same 25.5. A preset that wants both gates to move together sets both.

<!-- release-note-ends -->

## 0.4.2

Three reported defects, none of which changes a trajectory. Every preset runs
exactly as it did in 0.4.1.

- **A crisis threshold reached one gate and not the other.** Moving
  `crisis_vix_threshold` gated the gold crisis premium and left the dollar's
  safe-haven drift at the default 25.5, though the two describe one regime.
- **A meeting reports what it decided.** `advance_day` computed the central
  bank's `Decision` and its announcement variant and discarded both. Both are
  carried on `DayAdvanceOutcome` now, and `Decision` gains `as_str` and
  `Hash`.
- **A new dial, off everywhere, for a spread that can invert.** Between
  meetings the corporate bond yield goes stale while the 10y treasury keeps
  moving, so the credit spread drifts to a measured 0.42 against a floor of
  0.8. `daily_credit_floor_gain` re-asserts both floors daily, shipping at
  0.0.

<!-- release-note-ends -->

## 0.4.1

`pt-v13` and `pt-v14` reported a mispricing half-life they did not run. Both
said 68.26 days and both decayed at 60. The engine reads `mispricing_phi`,
which was always the 60-day value, so no trajectory moves. What was wrong is a
published fact, since `pt.model_preset()` reported 68.26 and a manifest
records it. Both presets now report 60, and results pinned to either preset in
0.4.0 are unaffected.

<!-- release-note-ends -->

## 0.4.0

`pt-v14` is the default, beating the preset before it by a wider margin than
any previous change on all 13 seed blocks measured.

| over 13 seed blocks | `pt-v12` | `pt-v14` |
|---|---|---|
| **two-year panel, blocks fully in band** | 3 of 13 | **11 of 13** |
| crisis correlation outside its real range | 4 of 13 | **2 of 13** |
| how far the crisis volatility jump misses real markets | 3.7% | **2.0%** |
| roster shapes in band | 131 of 138 | **137 of 138** |
| rosters it does better on, out of six it never saw | | **all six** |

Better on eight blocks, level on five, worse on none. Your numbers change if
you did not name a preset:

```python
eng = pt.Engine(seed=42, universe=u, model="pt-v12")
```

**What changed in the model.** Industry-level volatility carries more of the
market's shared movement and the market's own volatility memory was retuned to
pay for it, so stocks in different industries stop moving together quite so
uniformly in a crisis. Volume and volatility arrive together less tightly as a
result, 0.56 to 0.52 in a band running 0.46 to 0.66.

**Two new dials ship switched off**, `crisis_blend_variance_damp` and
`qe_pe_gain`. The `abs_return_acf5` band was re-derived from eight reference
windows instead of three, moving it to 0.01 to 0.12 with no change to any
preset's score.

<!-- release-note-ends -->

## 0.3.0

`pt-v12` is the default, and the first preset that looks like a real market
over two years as well as one.

| in band | `pt-v10` | `pt-v12` |
|---|---|---|
| one year | 14 of 14 | 14 of 14 |
| **two years** | 13 of 14 | **14 of 14** |
| a roster it never saw | 14 of 14 | 14 of 14 |

Thirty-seed medians. Only the two-year row moved. Your numbers change if you
did not name a preset.

**The fix was one number.** Volume stopped responding to a move at 4 percent,
so a stock down 12 traded like a stock down 4. That cap had been in the engine
since the first version and nobody chose it; the figure is now 12 percent.
Daily swings under a real macro path run 1.57x as wide as the real stock they
are compared against, against 1.555x before, recorded as the
`scenario-magnitude` gap.

**`pt-v11` also ships and is not selected by default.** It is the base
`pt-v12` is built on, and the first preset whose crises behave like real ones.

| in a crisis | `pt-v10` | `pt-v11` | real |
|---|---|---|---|
| volatility, calm to panic | 5.0x | 6.0x | 6.2x |
| how tightly names move together | 0.67 | 0.70 | 0.66 to 0.73 |
| how much industries hold together | +0.04 | +0.11 | +0.10 |

Two mechanisms did that. `crisis_blend_gain` was a fixed number already at its
maximum in any real crisis, so an earlier preset could only intensify a crisis
through company-specific movement, which pulled names apart. Companies now
also generate their own news, which reaches their sector peers.

**Seventeen new settings**, each at the value the engine already used, so no
preset from `pt-v1` to `pt-v10` moves. `ModelParams` goes from 70 coefficients
to 87, listed by `tf.ModelParams.settable()`.

**The envelope covers more than it did.** Six gaps become five, since `pt-v12`
holds volume change in band at both horizons, and two-year and five-year runs
are measured rather than assumed. The certified horizon stays 252 days. A
minute-at-a-time run and a batched tick loop disagreed on company news, and
both paths now print the same prices, a defect that affected `pt-v11` alone.

## 0.2.0

The default moved from `pt-v3` to `pt-v10`, so the market itself is different.
A run that names its preset replays exactly; a run that took the default does
not. `pt-v10` is the first preset to hold all fourteen statistics in band at
one year and thirteen of fourteen at two years, against twelve and seven for
the default before it, on training seeds, new seeds and a 60-name universe it
never saw.

| | `pt-v3` | `pt-v10` | real |
|---|---|---|---|
| days above its own crisis threshold | 0% | 10.2% | 12.5% |
| volatility, calm to panic | 3.07x | 5.05x | 6.16x |
| same-sector co-movement | 0.004 | 0.135 | 0.11 to 0.23 |

Correlation now has a memory and volume behaves at one year, leaving volume
change at two years as the one row of fourteen that still misses.

**Two ground-truth defects fixed.** A jump moved the mispricing state after
the tick loop, so on a day a jump fired the components did not sum to the
move, and a halted day booked its clamped price to nobody. `truth()` gains a
`jump` column and a `circuit_breaker` column, `Engine.FACTORS` is nine, and
the identity holds through a crisis to 1e-16.

**Two behaviour changes.** A VIX pin acts on the first day, and index-moving
order flow now costs about as much through other names' volatility as through
its own book. Snapshots also carry the log-volume state.

## 0.1.4

Two presets, four statistics, five settings. `pt-v3` is still the default and
`pt-v1` through `pt-v6` reproduce bit for bit. `pt-v7` is the first preset in
which same-sector names co-move more than cross-sector names, and `pt-v8` is
the first whose correlation has a memory. Both hold 13 of 14 in band at 504
days.

Four statistics join the panel: `corr_asymmetry`, `corr_asymmetry_lagged`,
`sector_excess_corr` and `corr_persistence_acf1`, so `pt-v3` now reads 12 of
14 at 252 days and 7 of 14 at 504 with the engine unchanged. Five settings
that were literals become dials at the value every preset already ran on:
`crisis_blend_source`, `sector_vix_coupling`, `fair_value_book_floor`,
`inflation_reversion` and `inflation_ceiling`.

**New gap: the economy cannot reach its own crisis regimes.** Endogenous
inflation peaks near 4.1 percent against a real CPI that reached 9.1 percent,
so an inflation regime needs a scenario to drive it. On real 2022 data over
six seeds, against a real S&P of -20.0 percent, the index reads -12.6% with no
scenario, -13.1% on the seven-hike path, and -23.3% on the published CPI path.

**What each macro field transmits**, measured by a shock on day 5 read on day
25:

| field | price move by day 25 |
|---|---|
| `vix` 15 to 60 | 39.2% |
| `qe_pe_boost` 0 to -0.30 | 38.3% |
| `corporate_bond_yield` 5.5% to 11.4% | 28.3% |
| `federal_funds_rate` 1.6% to 10% | 0.00% |
| `inflation_rate` 2% to 9% | 0.00% |
| `fear_greed_index` 50 to 0 | 0.00% |

Three fields act the day you move them and two wait for a central bank
meeting, because both work through the corporate bond yield. Inflation is the
lever for an inflation regime, since it steers the central bank into that
yield. `fear_greed_index` is settable and read by no pricing code.

## 0.1.3

Two presets. `pt-v3` is still the default, so nothing changes unless you ask
for a preset by name.

`pt-v6` holds 9 of 10 statistics at 252 days and 8 of 10 at 504, against 9 and
5 for `pt-v3`. The two tie at one year, so what `pt-v6` buys is tail room,
from 0.80 to 7.20 seed standard deviations. Use it for multi-year work where
the tail matters.

**A trade-off that was a wiring accident.** `pt-v4` reached the two-year tail
and lost `return_acf1` at one year, which came from one line: a jump moved the
mispricing state after the momentum roll had read it. `jump_momentum_share`
separates the two, so `pt-v5` is `pt-v4` with that dial at zero, and `pt-v6`
also halves the herding term. `garch_beta_dispersion` ships at zero, kept for
a small gain on 504-day kurtosis.

## 0.1.2

Fixes the PyPI project page, leaving the engine unchanged. The README linked
to examples and licences by relative path, which works on GitHub and 404s on
PyPI, so eleven links were dead. A test now fails on any relative README link,
and a second on an absolute link naming a file the repository does not hold.

## 0.1.1

A packaging fix. The engine did not move, so a result from 0.1.0 reproduces
here exactly. 0.1.0 published five wheels and no source distribution, because
PyPI refused the sdist for declaring its licence files at the package root
when the build had put them under `rust/`. This release ships the sdist with
the paths declared, and the v0.1.0 tag stays where it is, since moving it
would break the link between that tag and the wheels published from it.

## 0.1.0

First release. pretium simulates an equity market you can run a strategy
against: prices, a limit order book with price-time priority and partial
fills, and an economy that advances each day. Orders match against real depth,
so your trades move the price. On top of that sit agent evaluation, ranking
across seeds with paired sign tests, transaction cost analysis, sweeps and
replay, plus checkpoints with branching, a Gymnasium environment, five Arrow
tables and an SEC EDGAR loader.

Two things are less usual. The simulator computed every price, so it can say
why one moved, as seven factor contributions per instrument per tick that sum
to the move. The same seed also gives the same market, so you can run it twice
and price every fill against the market where you never traded.

**Determinism.** Five targets build a wheel, run one fixed simulation inside
it and compare digests on linux-x86_64, linux-aarch64, macos-arm64,
macos-x86_64 and windows-x86_64, all reproducing

```
5bd011be292f823ce1c360d1a12bf46de3362deee058a37283c74ab47069d0c1
```

A WebAssembly build gives the same numbers, so the engine can run in a browser
without becoming a second model.

**Realism.** Ten statistics are measured against real-market bands. At 252
days `pt-v3` holds nine of ten, on seeds and a roster the calibration never
saw. The tenth is the autocorrelation of volume changes, which misses by 13.7
seed standard deviations and sits outside the calibration objective
deliberately. Six further gaps say what each one stops you concluding.

**Versioning.** Anything that changes the simulated trajectory is a breaking
change here, however small, because a market that runs differently from the
same seed invalidates every published result that cited it. Coefficient
changes arrive as a new preset, and old presets keep running as they did.
