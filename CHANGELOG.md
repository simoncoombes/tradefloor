# Changelog

## Unreleased

**pt-v16 registered: the complete card.** pt-v15 re-levelled and
re-coupled: the QE valuation channel silenced (its driven input was a
proxy anticorrelated with measured Fed purchases), the
correlation-asymmetry composition (down ticks transmit harder, funded
by sector-loading dispersion and a quieter VIX cycle), a 0.86x joint
trim of every noise source (preserves correlations and ratios while
bringing the volatility level to real scale), and the same-day volume
coupling raised (volume_move_response 0.6 to 1.0) -- at the shipped
value the 252-day volume-|return| correlation sat below the weakest
real reference window on every block measured. Judged on twenty-six
blocks -- thirteen of them never touched by any search -- at one
hundred seeds per block: BOTH panels in band on every statistic on
all twenty-six blocks, no out-of-band row anywhere, crisis
co-movement and lever 26/26 each, and the driven noise ratio at 1.13
against pt-v15's ~1.46. Selectable by name; not the default, which remains
pt-v14.

**Planned: a shared-book multi-agent arena.** Today `evaluate` and `rank`
give each agent its own copy of the market, which is what makes the
comparison clean. The next step is one book with several agents in it,
competing for the same liquidity and scored under identical conditions.
Not shipped, and the realism envelope does not cover it: certification was
measured on a single agent.

## 0.5.0

**The library is now `tradefloor`**, formerly `pretium`, which published
through 0.4.3 and stays on PyPI and crates.io forever, because published
results cite those versions and reproducibility is the point. Install
`tradefloor`, `import tradefloor`, crate `tradefloor`, MCP server
`tradefloor-mcp`. Docs move to https://tradefloor.dev. The rename changes
no behaviour: this release reproduces the same known-answer digest as
0.4.3 on every platform, which the release gate proves before publishing.
Preset names (`pt-v1` … `pt-v15`) are citation identifiers and are frozen
under the old prefix, as are the run-manifest `pretium_version` key and
the known-answer `kind`, which describe documents written under them.

**pt-v15 registered: the slow-variance mixture, the credit floor, and
sector dispersion.** pt-v14 plus six numbers: the two-timescale variance mixture the model has
carried inert since the pt-v4 era (slow weight 0.35, persistence 0.98,
gain 0.05, VIX damp 0.374) and `daily_credit_floor_gain` 1.0, which
activates the #48 fix as a preset the way the version policy requires.
Against pt-v14 over thirteen thirty-seed blocks the 504-day panel ties on
every block, the crisis co-movement range across blocks falls 0.0774 to
0.0502 -- inside the 0.0630 band width, past pt-v12's 0.0551 -- and the
crisis lever median lands near the real 6.16 with 13/13 in tolerance.
`sector_loading_beta_slope` 0.5 -- cross-sectional dispersion in sector
exposure -- then takes back the one crisis co-movement block the mixture
cannot reach: confirmed over thirteen blocks paired against the
five-override base, co-movement goes 12/13 to 13/13 and the range falls
0.0502 to 0.0464 with the panel a tie on every block, making pt-v15 the
first preset measured to hold both crisis instruments on all thirteen.
Selectable by name; not the default, which remains pt-v14.

<!-- release-note-ends -->


## 0.4.3

**0.4.2 changed `pt-v13` and `pt-v14`, and it should not have.** If you
pinned either preset, this release puts them back exactly as they were in
0.4.0 and 0.4.1. All fourteen certified statistics are bit-identical to the
pre-0.4.2 values again.

The fix in 0.4.2 for a reported defect pointed the dollar's safe-haven gate at
`crisis_vix_threshold`, and both presets override that parameter to 30.88, so
their dollar gate moved from 25.5 and their trajectories moved with it. That
is a breaking change, and it went out in a patch release. The changelog said
there was no behaviour change at the default; that was wrong, and this
corrects it.

**What it cost, measured rather than estimated.** All fourteen statistics
moved and all fourteen stayed in band, the largest displacement being 4.48% of
a band width on `abs_return_acf5`. So no result computed under 0.4.2 is wrong.
But a run recorded under `pt-v14` before 0.4.2 does not replay under it, and
the preset name is what a citation carries, which is the whole reason presets
are frozen.

**The dollar gate is now its own dial**, `usd_crisis_vix_threshold`, defaulted
to the same 25.5 as before. A preset that wants both gates to move together
sets both. That still answers the original report, which was that the dollar
gate was an invisible constant while gold read a parameter: it is a named,
settable, documented parameter now.

**Also in this release**, the package description changes from "Deterministic
market simulation with a real limit order book" to "A reproducible evaluation
environment for financial AI agents", matching what the documentation has said
since the site was rebuilt.

<!-- release-note-ends -->

### the detail, and why nothing caught it

The dollar index is not an output-only series. `economy/daily.rs` reads it for
the inflation effect and for the dollar effect, so a change to the safe-haven
drift propagates through inflation into the whole macro chain and out into
equity fair values. It is a trajectory change by any definition.

Nothing in the suite could see it, and the reason is structural rather than an
oversight. The cross-platform known-answer test starts at VIX 19.5 and never
crosses 25.5, so neither the old gate nor the new one fires in its 250 days.
The two surviving full bit-parity economy trajectories have recorded VIX
ceilings of 25.44 and 16.51, both under the old gate. The three trajectories
that do cross 25.5 are exactly the ones retired under `#[ignore]` at the
2026-08-21 crisis-trigger fork. Every gate that could have caught this had
already been switched off, correctly, for an unrelated reason.

`a_preset_that_moves_the_crisis_threshold_leaves_the_dollar_gate_alone` closes
that hole. It drives the VIX above the gate deliberately, which is the region
none of the surviving gates sample, and asserts that raising
`crisis_vix_threshold` alone moves gold and leaves the dollar where it is. It
was checked against the 0.4.2 expression before being committed, and fails
there naming the regression.

## 0.4.2

Three reported defects, none of which changes a trajectory. Every preset runs
exactly as it did in 0.4.1.

**A crisis threshold reached one gate and not the other.** Moving
`crisis_vix_threshold` gated the gold crisis premium at your chosen level and
left the dollar's safe-haven drift at the default 25.5, silently. The two
describe one regime. Anyone who never moved the parameter saw nothing, which
is what made it worth fixing rather than living with.

**A meeting now reports what it decided.** `advance_day` computed the central
bank's `Decision` and its announcement variant and then discarded both,
leaving an embedder with a rate that moved and no way to say why. Both are
carried on `DayAdvanceOutcome` now. Reconstructing the decision from the rate
delta was never sound: `StagflationHike` and `LaborEmergencyCut` are separated
by the context that selected them, not the size of the move. `Decision` also
gains `as_str` and `Hash`.

**A new dial, off everywhere, for a spread that can invert.** Between central
bank meetings the corporate bond yield goes stale while the 10y treasury keeps
moving, so the credit spread drifts below its floor: measured down to 0.42
against a floor of 0.8, first breaching on day 121. An investment-grade yield
under the risk-free curve is an impossible quote. `daily_credit_floor_gain`
re-asserts both credit floors on every daily step, and ships at 0.0, so
nothing changes until a preset sets it.

<!-- release-note-ends -->

### the detail, and why the third one ships switched off

`update_economy_daily` is preset-independent, so flooring the spread
unconditionally would move the economy trajectory of every preset, `pt-v1`
included. The version policy in `RELEASING.md` is explicit that a change to
the simulated trajectory is breaking however small it looks, and that such
changes arrive as a new preset rather than an edit to an existing one.

Measured unconditionally, the fix retired both remaining full bit-parity
economy trajectories, moved the shipped seed standard deviations, and made a
policy rate transmit before the first meeting -- a boundary the documentation
and one notebook both describe as sharp. All three are the right consequences
of the right fix, and all three belong at a preset boundary rather than in a
patch release. Two tests hold the position: one pins the inversion while the
dial is off, the other proves both floors hold at 1.0.

The audit the threshold report asked for went across all nine parameters that
carry a named constant. One more instance turned up, in a test helper that
took `ModelParams` and then read the constant anyway, so it would have stopped
mirroring the engine the moment a test moved the threshold.

## 0.4.1

`pt-v13` and `pt-v14` reported a mispricing half-life they did not run. Both
said 68.26 days; both decayed at 60. Nothing you ran was wrong, and no
trajectory moves in this release: the number the engine reads is
`mispricing_phi`, which was always the 60-day value, and the cross-platform
known-answer test confirms it by leaving its simulation digest untouched.
What was wrong is a published fact. `pt.model_preset()` reported the 68.26,
a manifest records it, and anyone who set a half-life from that number got a
different market than the preset runs.

Both presets now report 60, which is what they do. If you pinned either one
in 0.4.0, your results are unaffected and need no rerun.

<!-- release-note-ends -->

### the detail, and how it happened

The presets are built by `const fn`. The half-life is an INPUT: assigning it
has to recompute `mispricing_phi` and `s_phi_tick` through `ln` and `exp`,
which const evaluation cannot do. The runtime path does this correctly, and
a test has covered it since 0.1.x. What nothing covered was a preset
CONSTRUCTOR assigning the field, where the value lands with no recompute and
no complaint. Twelve of the fourteen presets were unaffected because they
never set it.

`every_preset_runs_the_half_life_it_reports` now walks every shipped preset
and compares the reported half-life against the decay its `mispricing_phi`
implies. It was checked against the defect before being committed: it fails
naming the preset and the rate it actually runs.

The 68.26 came from the calibration search that produced `pt-v14`, and it is
kept in this record rather than in a field the engine contradicts. Shipping
it for real means writing the recomputed bits literally, under a new preset
name, because changing them under an existing one would move a published
model.

## 0.4.0

`pt-v14` is the default. On the panel this project certifies it is better
than the preset before it by a wider margin than any previous change, and it
is never worse on any of the 13 seed blocks it was measured on. One
statistic loses margin; it is described below rather than left out.

| over 13 seed blocks | `pt-v12` | `pt-v14` |
|---|---|---|
| **two-year panel, blocks fully in band** | 3 of 13 | **11 of 13** |
| crisis correlation outside its real range | 4 of 13 | **2 of 13** |
| how far the crisis volatility jump misses real markets | 3.7% | **2.0%** |
| roster shapes in band | 131 of 138 | **137 of 138** |
| rosters it does better on, out of six it never saw | -- | **all six** |

It is never worse than `pt-v12` on any block measured: better on eight,
level on five, worse on none.

Your numbers change if you did not name a preset. Every earlier preset still
runs exactly as it did:

```python
eng = pt.Engine(seed=42, universe=u, model="pt-v12")
```

**What changed in the model.** Industry-level volatility now carries more of
the market's shared movement, and the market's own volatility memory was
retuned to pay for it. The effect is that stocks in different industries stop
moving together quite so uniformly in a crisis, which is what real markets do
and what the old default got wrong at exactly the wrong moment.

**One thing got slightly worse.** Volume and volatility still arrive
together, but less tightly: the measure falls from 0.56 to 0.52 in a band
that runs 0.46 to 0.66. It never leaves the band at the resolution this
project certifies -- 30 seeds, one year, on any of 13 blocks -- but it sits
nearer the edge than it did.

**Two new dials ship switched off**, so nothing changes unless you set them:
`crisis_blend_variance_damp` and `qe_pe_gain`. Both are measured and
documented; neither is used by any preset.

**The documentation is rebuilt as a learning path.** Twenty-five pages take
you from what a limit order book is to the API reference. Every page carries
one chart drawn from the repository's own reference data, and the caveats sit
in collapsible blocks beside the thing they qualify rather than in every
paragraph. Old documentation URLs still work: twelve redirect to the page
that replaced them, and four pages were retired.

<!-- release-note-ends -->

### the detail, and how it was measured

**A band was too narrow and is now correct.** `abs_return_acf5` ran 0.02 to
0.09. Real markets leave that band on five of eight non-crisis reference
windows, which means it was rejecting reality. Re-derived from eight windows
instead of three, it is 0.01 to 0.12. No preset's score changes.

**The leverage effect sits nearer its edge.** Its median moves from -0.0377
to -0.0222 in a band that runs -0.16 to 0.0, and the count of seeds landing
at or above zero goes from 4 of 30 to 6 of 30. Both presets pass the
statistic at the certified resolution. A sentence in the old documentation
said the sign was negative in six seeds of six; that was a fragile n=6 rather
than a property of the model, and the row asserting it has been retired from
the re-measurement inventory with its history recorded there.

**How the table was measured.** Every row is 13 seed blocks of 30 seeds each,
at block starts 101 through 1401. The panel row counts blocks whose whole
two-year panel sits in band; 11 of 13 against 3 of 13 gives Wilson intervals
of 58 to 96 percent and 8 to 50 percent, which do not overlap. The paired
rows compare the two presets on the same block, so the block itself cancels:
that matters because a block's panel score is 44 percent seed block and 56
percent parameter vector, and an unpaired comparison at this margin reads
mostly as luck. The six rosters are drawn from the universe generator with
seeds the search never saw.

**What the search was.** `pt-v14` differs from `pt-v12` in 15 of 90
parameters. They were found by CMA-ES on a Wilson lower bound of the block
pass rate, seeded from a Bayesian-optimisation front, and confirmed against
held out blocks with zero loss.


## 0.3.0

`pt-v12` is the default. It is the first preset that looks like a real market
over two years as well as one.

| in band | `pt-v10` | `pt-v12` |
|---|---|---|
| one year | 14 of 14 | 14 of 14 |
| **two years** | 13 of 14 | **14 of 14** |
| a roster it never saw | 14 of 14 | 14 of 14 |

Thirty-seed medians. Only the two-year row moved, and it cost nothing on the
other two rows.

Your numbers change if you did not name a preset. Every earlier preset still
runs exactly as it did:

```python
eng = pt.Engine(seed=42, universe=u, model="pt-v10")
```

**The fix was one number.** Volume stopped responding to a move at 4 percent,
so a stock down 12 traded like a stock down 4. That cap had been in the engine
since the first version and nobody chose it. It is now 12 percent.

**One thing got worse.** Under a real macro path, daily swings run 1.57x as
wide as the real stock they are compared against, against 1.555x before. It is
the `scenario-magnitude` gap.

**`pt-v11` also ships, and is not the default.** It is the base `pt-v12` is
built on, and the first preset whose crises behave like real ones.

| in a crisis | `pt-v10` | `pt-v11` | real |
|---|---|---|---|
| volatility, calm to panic | 5.0x | 6.0x | 6.2x |
| how tightly names move together | 0.67 | 0.70 | 0.66 to 0.73 |
| how much industries hold together | +0.04 | +0.11 | +0.10 |

Two mechanisms did that. `crisis_blend_gain` was a fixed number, already at its
maximum in any real crisis. So an earlier preset could only make a crisis more
violent through company-specific movement, which pulled names apart. Companies
also generate their own news now, and that news reaches their sector peers.
Before this, one company's earnings surprise reached nobody.

**Seventeen new settings**, each at the value the engine already used, so no
preset from `pt-v1` to `pt-v10` moves. `ModelParams` goes from 70 coefficients
to 87. `pt.ModelParams.settable()` lists them.

**The envelope covers more than it did.** Six gaps become five. `pt-v12` holds
volume change in band at both horizons, so that gap is gone. Two-year and
five-year runs are now measured rather than assumed. Over ten years, annualised
volatility reads 31.5, 35.6, 30.2, 33.5, 33.0, 33.1, 31.3, 32.4, 32.4 and 31.6
percent, so nothing runs away and nothing drifts. The certified horizon stays
252 days, because the bands themselves were derived at one and two years.
S&P-like, technology-heavy and defensive rosters all hold 14 of 14 at one year.

**A bug fixed on the way.** Driven a minute at a time, the model's own company
news never reached you. A batched tick loop also rolled that news every minute.
Both paths now print the same prices. This only affected `pt-v11`.

**Documentation.** Every published figure was re-measured against the engine.
Three new pages: a glossary, the two loops, and the principles. References to
the reference implementation no longer disclose its paths or its language, and
`tests/test_brand_commitments.py` now fails if they come back.

## 0.2.0

The default moved from `pt-v3` to `pt-v10`, so the market itself is different.
A run that names its preset replays exactly. A run that took the default does
not.

`pt-v10` is the first preset to hold all fourteen statistics in band at one
year, and thirteen of fourteen at two years. The default before it held twelve
and seven. That holds on training seeds, on new seeds, and on a 60-name
universe it never saw.

| | `pt-v3` | `pt-v10` | real |
|---|---|---|---|
| days above its own crisis threshold | 0% | 10.2% | 12.5% |
| volatility, calm to panic | 3.07x | 5.05x | 6.16x |
| same-sector co-movement | 0.004 | 0.135 | 0.11 to 0.23 |

Correlation now has a memory, and volume behaves at one year. Volume change at
two years is the one row of fourteen that still misses.

**Two ground-truth defects fixed.** A jump moved the mispricing state after the
tick loop. So on a day a jump fired, the components did not sum to the move. A
halted day booked its clamped price to nobody. `truth()` gains a `jump` column
and a `circuit_breaker` column, `Engine.FACTORS` is nine, and the identity holds
through a crisis to 1e-16. A decomposition published from 0.1.x is exact except
on those days.

**Snapshots now carry the log-volume state.** A restored `pt-v4` engine used to
trade different volume and print different prices.

**Two behaviour changes.** A VIX pin acts on the first day, not the second. And
order flow large enough to move the index now costs about as much through other
names' volatility as through its own book.

Also: `examples/00-a-year-in-one-market.ipynb`, the entry point the examples
were missing. Two envelope gaps closed, both recorded rather than dropped:
two-year kurtosis moved from 5.23 to 8.26, and sector co-movement from 0.004 to
0.135. Three inflation constants became settings, measured against real CPI and
taken by no preset.

## 0.1.4

Two presets, four statistics, five settings. `pt-v3` is still the default and
`pt-v1` through `pt-v6` reproduce bit for bit.

`pt-v7` is the first preset in which same-sector names co-move more than
cross-sector names. `pt-v8` is the first whose correlation has a memory: in this
window correlation is the market factor's variance, and that variance had almost
none. Both hold 13 of 14 in band at 504 days.

Four statistics join the panel: `corr_asymmetry`, `corr_asymmetry_lagged`,
`sector_excess_corr` and `corr_persistence_acf1`. `pt-v3` now reads 12 of 14 at
252 days and 7 of 14 at 504. The engine did not move. The misses were always
there.

Five settings that were literals: `crisis_blend_source`, `sector_vix_coupling`,
`fair_value_book_floor`, `inflation_reversion` and `inflation_ceiling`. Each
ships at the value every preset already ran on.

**New gap: the economy cannot reach its own crisis regimes.** Endogenous
inflation peaks near 4.1 percent, against a real CPI that reached 9.1 percent.
So an inflation regime needs a scenario to drive it, and the gap says how. On real 2022
data over six seeds, against a real S&P of -20.0 percent:

| scenario | index |
|---|---|
| none | -12.6% |
| the real seven-hike path only | -13.1% |
| the published CPI path | -23.3% |

Inflation is the lever, because it steers the central bank into the corporate
bond yield. A pinned policy rate does not.

**What each macro field transmits.** Measured by a shock on day 5, read on day
25.

| field | price move by day 25 |
|---|---|
| `vix` 15 to 60 | 39.2% |
| `qe_pe_boost` 0 to -0.30 | 38.3% |
| `corporate_bond_yield` 5.5% to 11.4% | 28.3% |
| `federal_funds_rate` 1.6% to 10% | 0.00% |
| `inflation_rate` 2% to 9% | 0.00% |
| `fear_greed_index` 50 to 0 | 0.00% |

Three fields act the day you move them. Two wait for a central bank meeting,
because both work through the corporate bond yield. `fear_greed_index` is
settable, validated, and read by no pricing code.

The real 2020-21 macro path now drives four scenario response channels rather
than a claim. Every sign is right. Each channel runs at 70 to 85 percent of the
size real AAPL shows, which is the scenario-magnitude gap from another angle.

## 0.1.3

Two presets. `pt-v3` is still the default, so nothing changes unless you ask for
a preset by name.

`pt-v6` holds 9 of 10 statistics at 252 days and 8 of 10 at 504, against 9 and 5
for `pt-v3`. It is not strictly better: the two tie at one year, and `pt-v3`
sits more comfortably inside six of the ten bands. What `pt-v6` buys is tail
room, from 0.80 to 7.20 seed standard deviations. Use it for multi-year work
where the tail matters. `pt-v3` is still the preset the envelope certifies.

**A trade-off that was a wiring accident.** `pt-v4` reached the two-year tail
and lost `return_acf1` at one year. That looked like the price of the tail. It was
one line. A jump moved the mispricing state after the momentum roll had read it.
The next close then read the jump as a re-rating and continued it.
`jump_momentum_share` separates the two. `pt-v5` is `pt-v4` with it at zero.
`pt-v6` also halves the herding term.

`garch_beta_dispersion` ships at zero. It was built for the decay-shape gap and moves
away from it. The log-log slope reads -0.944 with it and -0.933 without, against
a real -0.436. It is kept for a smaller gain it does give, on 504-day
kurtosis and annualised volatility. Its documentation leads with the
measurement, not with what it was for.

`examples/09-a-pandemic-shaped-market.ipynb` drives the real 2020-21 VIX, FOMC
path and credit path through a 505-day run, and scores the result against
Apple's shares. The daily series ship with it, so it runs offline. Two findings
hold outside the notebook: only `qe_pe_boost` moves a valuation, and
`fear_greed_index` moves nothing at all.

## 0.1.2

Fixes the PyPI project page. The engine did not move.

The README linked to examples and licences by relative path. That works on
GitHub and 404s on PyPI, which renders the same file. Eleven links were dead. It
survived two releases because the file looks correct everywhere an author reads
it. A test now fails on any relative README link. A second fails if an
absolute link names a file that is not in the repository.

## 0.1.1

A packaging fix. The engine did not move, so a result from 0.1.0 reproduces here
exactly.

0.1.0 published five wheels and no source distribution. PyPI refused the sdist
because it declared its licence files at the package root and the build had put
them under `rust/`. The wheels were never affected, so the fault showed up on
one artefact out of six. This release ships the sdist with the paths declared.

The v0.1.0 tag stays where it is. Moving it would break the link between that
tag and the wheels published from it, and that link is the point.

## 0.1.0

First release. pretium simulates an equity market you can run a strategy
against. It runs prices, a limit order book with price-time priority and partial
fills, and an economy that advances each day. Orders match against real depth,
so your trades move the price.

On top of that sit agent evaluation, ranking across seeds with paired sign
tests, transaction cost analysis, sweeps and replay. There are also checkpoints
with branching, a Gymnasium environment, five Arrow tables, and an SEC EDGAR
loader.

Two things are less usual. The simulator computed every price, so it can tell
you why one moved. That is seven factor contributions per instrument per tick,
and they sum to the move. The same seed also gives the same market. So you can
run it twice, once with your orders and once without, and price every fill
against the market where you never traded.

**Determinism.** Five targets build a wheel, run one fixed simulation inside it,
and compare digests. Verified on linux-x86_64, linux-aarch64, macos-arm64,
macos-x86_64 and windows-x86_64, all reproducing

```
5bd011be292f823ce1c360d1a12bf46de3362deee058a37283c74ab47069d0c1
```

A WebAssembly build gives the same numbers, so the engine can run in a browser
without becoming a second model.

**Realism.** Ten statistics are measured against real-market bands. At 252 days
`pt-v3` holds nine of ten, and holds the same nine on seeds and a roster the
calibration never saw. The tenth is the autocorrelation of volume changes, which
misses by 13.7 seed standard deviations. It is outside the calibration objective
on purpose: an optimiser pointed at a target it cannot reach distorts everything
else. Six further gaps say what each one stops you concluding, and
`envelope.check()` refuses a question that falls outside them.

`pt-v4` also ships and is not the default. It is the first preset to bring
504-day kurtosis inside its band, and it pays at one year, holding eight of ten.

**Versioning.** Anything that changes the simulated trajectory is a breaking
change here, however small. A market that runs differently from the same seed
invalidates every published result that cited it. So coefficient changes arrive
as a new preset, and old presets keep running as they did.
