# Changelog

## Unreleased

The documentation is rebuilt as a learning path. Twenty-five pages take you
from what a limit order book is to the API reference. Every page carries one
chart drawn from the repository's own reference data, and the caveats sit in
collapsible blocks beside the thing they qualify rather than in every
paragraph.

Nothing in the package changed. Old documentation URLs still work: twelve
redirect to the page that replaced them, and four pages were retired.

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
