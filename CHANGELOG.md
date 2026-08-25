# Changelog

## 0.1.4

Nothing in the engine moves. The known-answer digest is unchanged, pt-v1
through pt-v6 reproduce exactly, and pt-v3 is still the default, so every
published result still reproduces. This release adds one inert mechanism, and
otherwise turns several claims the documentation was making into measurements
a reader can check.

### The realism panel grows from ten statistics to thirteen

The one cross-sectional statistic the panel had, mean pairwise correlation over
all pairs, is blind to sign, sector and time, and a search cannot preserve what
it cannot see. Three conditional statistics are added, each banded on the same
forty-name reference roster by the same rule as the original ten:

| statistic | what it conditions on | real band at 252 days | pt-v3 |
|---|---|---|---|
| `corr_asymmetry` | days the market fell more than one sd, minus days it rose more | -0.25 to +0.45 | +0.004, in band |
| `corr_asymmetry_lagged` | the same, on the previous day's market return | -0.20 to +0.55 | -0.033, in band |
| `sector_excess_corr` | same-sector pairs minus cross-sector pairs | **+0.11 to +0.23** | **+0.004, 15 seed-sd out** |

The certified count is now **eleven of thirteen** at 252 days, on training
seeds, held-out seeds and a held-out 60-name universe alike, and six of
thirteen at 504. Nothing in the engine moved; the two new misses were always
there and are now measured.

The new miss is a real finding. Every one of ten real windows including the
2020 crisis has same-sector pairs co-moving between 0.10 and 0.20 more than
cross-sector pairs, the tightest band on the panel after volume change, and the
model reads 0.004 because its sector factor is a single scalar at sigma 0.002.
That is recorded as gap 7, `sector-structure`, which `envelope.check()` now
fires for any question naming `sector_excess_corr`, and its `forbids` field
names sector rotation, sector-neutral construction and industry diversification.
The lever, `sector_factor_sigma`, is settable and shipped; the pt-v1 search
reported it as a null direction, which is what a lever looks like when the
objective cannot see it.

The dial that would close gap 7 has been measured and is not shipped. At
`sector_factor_sigma` 0.012 the statistic lands inside its band at 0.155 with
nothing else on the panel leaving its own, but two things the panel cannot see
move the wrong way: the crisis volatility lever falls from 3.07x to 2.78x, and
kurtosis thins by about a third of a seed-sd at two years. And above the crisis
threshold the engine's blend replaces the sector draw with the market draw, so
industries dissolve in a panic whatever the dial is set to. The remedies for
all three are being measured, and any change to the default arrives as a new
preset. The calibration record has the numbers (§59 and §60) and the design
for the blend change.

Where these came from: a review of the correlation structure, adversarially
checked against the calibration record, which rejected five of twelve proposed
mechanisms on the record's own measurements and ranked the survivors behind the
statistics needed to see them. It is in the design repository as
CORRELATION-REVIEW-2026-08-25.md.

### Two parameters that let sector structure survive a crisis, inert at 0.0

Measuring the fix for gap 7 found that the coefficient alone pays twice, on
every base, for one reason: the sector draw is the only variance term on the
tick path that does not scale with VIX, so it dilutes the crisis lever in calm
markets and is thrown away by the crisis blend in stressed ones, where
`sector_excess_corr` reads zero at a held VIX 45 against a real +0.10.

`sector_vix_coupling` lets the sector draw's variance follow VIX on the market
factor's own target shape. `crisis_blend_source` moves the crisis injection off
the sector slot and onto the market component, leaving the sector draw whole.
Both ship at 0.0, bit-identical by branch on every preset; the known-answer
digest is unchanged and every parity suite passes. At six seeds under a held
crisis, moving the blend off the sector slot restores sector structure and the
coupling raises it further; the thirty-seed measurement is in the calibration
record. No preset uses either yet.

### `fair_value_book_floor`, and the discontinuity it exists for

Fair value jumps UP as earnings fall through zero. Profitable companies are
valued on earnings times a multiple and loss makers at book times 1.2, so with
a book value of 20.00 a company earning 1.00 is worth 14.90, one earning 0.50
is worth 7.45, and one losing 19.49 is worth 24.00. A barely profitable company
is worth less than a bankrupt one.

That is close to harmless today, because earnings are fixed when an instrument
is built and nothing walks them through zero. It stops being harmless the
moment anything does: an airline going from 4.30 to a loss across a year would
watch its fair value slide, invert and jump, with the price chasing it.

The new parameter applies the book floor on both sides, which makes fair value
continuous at zero and non decreasing in earnings. **It ships at 0.0, off, and
is bit identical there.** That is not caution for its own sake: 42.8% of
instruments from `Universe.random` have `eps * pe` below `book * 1.2`, some at
a fifth of it, so switching it on re-values a large part of a typical universe
and re-bases every calibrated statistic. Adopting it is an era boundary and a
recalibration rather than a bug fix, and this release only makes it available
to measure.

### A new envelope gap: the macro state cannot reach its own crisis regimes

`macro-range` records something the envelope did not say and users were
entitled to know, namely that left to itself the economy stays in a moderate
band.

Endogenous inflation peaks at 4.06% to 4.11% over five seeds and five years,
against a hard clamp of 6.0% and a US CPI that reached 9.1% in June 2022. The
obvious explanation is wrong, and the gap says so, because the next person will
otherwise start there: `inflation_mean_rev_coeff` is 0.55 a month and looks
like it should pin inflation to its 2% target, but the monthly series has
AR(1) +0.936 against +0.894 for real CPI year on year across 2020 and 2021. The
model's inflation is if anything MORE persistent than the real thing. The
defect is dispersion, sd 1.23 around a mean of 1.99%, which puts 9% at 4.6
sigma.

The consequence reaches the central bank. Its crisis cadence pulls the next
meeting in to 21 to 30 days when a decision leaves it more than 2pp behind an
inflation rate above 4%, and that path is correct and well exercised, firing in
22.0% of the 11,898 central bank cases in the parity corpus. A default run
simply cannot reach it. It also fires in STAGFLATION rather than in high
inflation as such, so pinning inflation high with unemployment low will not
trigger it however high you pin it.

So a 2022 style inflation shock has to be driven through a scenario, and so
does the policy response to it.

### `check()` can now refuse a macro-regime question

The `macro-range` gap above was, for one afternoon, invisible to the interface
users actually call. `envelope.check()` names no route to a gap that names no
statistics, so someone asking whether they could study an inflation regime was
told `inside=True` while the documentation page said otherwise. `check()` gains
a `macro_regime` flag, and the refusal quotes the measurements.

`tests/test_envelope_reachability.py` now requires every gap in `GAPS` to be
reachable through some argument to `check`, and every refusal to carry a
figure. Adding a gap fails that file until it is wired to something a caller
can ask for. It caught one immediately: the `scenario-magnitude` refusal said
only that the size "is not certified", with no number, and now gives the 3.07x
lever against real markets' 6.16x.

### The gap's own advice, tested against 2022

`macro-range` tells a reader to drive an inflation regime through a scenario.
That advice was checked before shipping it, on real 2022 data over six seeds,
because a gap that hands out a recipe should have run the recipe.

It works, and the lever is inflation rather than the policy rate. Against a
real S&P of -20.0%:

| scenario | index, median of 6 seeds |
|---|---|
| no scenario at all | -12.6% |
| the real seven hike path only | -13.1% |
| the published CPI path | **-23.3%** |

The middle row is the one worth having. Driving the real 2022 hiking cycle,
0.125% to 4.375% in seven moves, returns the drift and nothing more. Inflation
works because it steers the central bank's OWN reaction into the corporate bond
yield, and an externally pinned policy rate does not reproduce that. The gap
now says so, including that `corporate_bond_yield` must be left free, since
pinning it severs the channel the inflation path is using.

The control row is the reason any of this is stated. Without it, the hike-path
run reads as a 13% bear market and looks like a result.

### What each macro field transmits, and when

`docs/scenarios.md` gains a per field table, measured by introducing each shock
on day 5 of a run and reading day 25.

| field | median price move by day 25 | fair value |
|---|---|---|
| `vix` 15 to 60 | 39.2% | unchanged |
| `qe_pe_boost` 0 to -0.30 | 38.3% | -30.0% |
| `corporate_bond_yield` 5.5% to 11.4% | 28.3% | -9.9% |
| `federal_funds_rate` 1.6% to 10% | 0.00% | unchanged |
| `inflation_rate` 2% to 9% | 0.00% | unchanged |
| `fear_greed_index` 50 to 0 | 0.00% | unchanged |

Three act on the day you move them, two wait for a central bank meeting because
both work only by steering the corporate bond yield, and one does nothing at
all. `fear_greed_index` is settable, range validated and reported in
`macro_table`, and no pricing code reads it. That is now stated rather than
left to be discovered.

The scenario recipes page previously headed this "nothing transmits before day
45". The sentence beneath it was correctly narrow, a POLICY ONLY rate path, but
the heading is what gets remembered and it is false. It now carries the
qualifier, and `tests/test_macro_transmission.py` pins the whole map: the
meeting boundary at its exact day, the fields that act immediately, the fact
that pinning the yield severs everything upstream of it, and the inertness of
`fear_greed_index`.

### Two claims in the realism envelope are now measurements

**"The direction of response is right"** was asserted with no number behind it.
Driving the real 2020 to 2021 macro path through the model and correlating
daily returns against each driver over 504 sessions, beside the same
correlations computed on real AAPL:

| channel | simulated | real AAPL |
|---|---|---|
| return vs change in VIX | -0.423 | -0.622 |
| return vs change in credit yield | -0.496 | -0.592 |
| return vs change in valuation | +0.573 | +0.803 |
| absolute return vs VIX level | +0.512 | +0.489 |

All four carry the sign theory fixes in advance, and the volatility clustering
channel is close to exact. The three directional channels run at roughly
seventy to eighty five percent of the real response, which is the
scenario magnitude gap from another angle: a shock moves this market the right
way and not far enough.

**pt-v6's scenario cost** is now in the horizon gap, so a reader selecting it on
band counts reads the cost in the same paragraph. The steady state volatility
lever from VIX 5 to VIX 65 runs 3.07x at pt-v3, 2.67x at pt-v4, 2.69x at pt-v5
and 2.68x at pt-v6, against real markets' 6.16x. A sustained crisis is about an
eighth less violent under pt-v6 than under the default, so a study turning on
crisis magnitude should prefer pt-v3 even over multi year windows. The cost was
spent when jumps arrived at pt-v4; every preset since inherits it.

### Calibration tooling

**`atlas_survey.py plan` now binds.** `--samples` is a single top level argument
that `run` re-reads from its own default of 4000, and `plan` wrote nothing to
disk, so `plan --samples 1000` followed by a bare `run` forecast 48,000 tasks
and then ran 192,000 under a different plan fingerprint printed one line later.
The forecast is what an operator sizes a dead man switch against, and one
survey run was killed by its own timeout at 63.9% complete for exactly this
reason. `plan --out` now writes `meta.json`, which lets the refusal `run`
already had actually fire, and `tests/test_survey_plan_guard.py` pins it.

**`scenario_response.py` no longer prints a number that should not be quoted.**
"Shock response retained" divides two small excesses over 1.0, so a difference
of 0.024 in the shock ratio becomes an eleven point headline, and it has been
quoted that way twice in this project's own record. The tool now prints the
excesses it is built from and names the shock ratios to quote instead.

**`read_decay_survey.py` gained an argument parser.** It read `sys.argv[1]`
directly, so `--help` opened a file named `--help` and died in a traceback.
`tests/test_tool_help.py` could not see it, because that test collects scripts
containing `add_argument` and a tool with no parser is not an entry point as
far as it is concerned. Entry points go from 21 to 22.

**The AWS survey launcher can now survive a spot reclaim.** Its row stream
used `aws s3 sync`, which lists the destination to compare and so needs
`s3:ListBucket`, a permission the instance role does not have. Every sync
failed silently while every `cp` in the same loop succeeded, so a reclaimed run
resumed from nothing. The rows are copied with `cp` now, and the launcher also
refuses to start a run at all if a one-byte write to the bucket fails, which is
what a missing instance profile looks like and what cost one full survey.

**`atlas_survey.py` can survey a few axes around a preset other than pt-v3.**
`--base pt-v6 --only a,b,c` restricts the plan to the named axes and pins
every other parameter at the base preset's value inside each vector, so a task
still sets all of them and nothing falls through to the evaluator's pt-v1
base. Multiplicative boxes around a negative base value, which pt-v6's jump
mean is, are ordered rather than refused. `tests/test_survey_only.py` pins the
completion.

**`read_scenario_frontier.py`** is new, and reads the survey for the frontier
between scenario response and horizon realism.

### Worked example

`examples/09-a-pandemic-shaped-market.ipynb`, which shipped in 0.1.3, gains a
section testing whether its effects follow its causes rather than only whether
its path resembles the real one. It carries the channel table above and an
event study over the five sessions after each of six dated events, which agrees
on sign five times out of six. The exception is the Fed's intermeeting cut of
3 March 2020, where the model has the textbook channel by which a cut helps
equities and no representation of an emergency cut reading as a panic signal.
That channel is missing rather than miscalibrated, and the notebook says so.

## 0.1.3

Two new model presets and the mechanism behind them. Nothing existing moves:
the known-answer digest is unchanged, pt-v1 through pt-v4 reproduce exactly,
and pt-v3 remains the default. If you do not ask for a new preset by name,
this release changes nothing about your results.

### pt-v6

The first preset to hold nine of ten statistics in band at 252 days and
eight of ten at 504. pt-v3 manages nine and five; pt-v4 eight and seven.

It is not strictly better than pt-v3, and the band counts hide that. At the
certified horizon the two tie at nine of ten, but pt-v3 sits more
comfortably inside its bands on six of the ten statistics, and pt-v6 buys
one large gain for it: kurtosis room goes from 0.80 to 7.20 seed-standard-
deviations. At two years pt-v6 is clearly ahead, closing kurtosis,
`abs_return_acf1` and `return_acf1`, which pt-v3 misses.

Choose pt-v6 for multi-year work where the tail matters. pt-v3 is still what
the realism envelope certifies, and certification is a different claim from
passing the controls.

### pt-v5, and why the trade it broke was a wiring accident

pt-v4 reached the two-year tail and paid for it at one year, losing
`return_acf1`. That looked like a property of reaching the tail at all.

It was one line. A jump was applied to the mispricing state at the day close,
after the momentum roll had already recorded the pre-jump level, so at the
next close the herding term read the jump as a re-rating and continued it.
Fattening the tail and adding return continuation were the same write to the
same variable, which is why no amount of searching those coefficients
escaped it.

`jump_momentum_share` separates them. pt-v5 is pt-v4 with it at zero: the
jump still decays through the existing process, it is simply not amplified on
the way. pt-v6 is pt-v5 with the herding term halved, which fixes the
two-year reading the same mechanism left behind.

### A parameter that did not work, shipped inert and documented as such

`garch_beta_dispersion` spreads volatility persistence across the
cross-section with a name's size. It was built for the decay-shape gap and it
does not close it: the log-log slope of the volatility autocorrelation reads
-0.944 with it against -0.933 without, where real markets read -0.436, so it
moves away from the target. It ships at zero and its documentation leads with
that measurement rather than with what it was for.

It is kept because it does something real and smaller: at 504 days it
improves room on kurtosis and on annualised volatility. A parameter claiming
a gap it does not close would be worse than no parameter.

### Also

The realism envelope now records which selectable preset closes a gap, so a
reader who hits "tails are too thin over multi-year windows" can learn that
three presets do not have it. Naming a preset there is not a certification:
the reader makes that trade rather than the default moving quietly.

`docs/envelope.json` carries the same field, so the citable artifact answers
the question without anyone reading the source.

### A worked example that drives real market data

`examples/09-a-pandemic-shaped-market.ipynb` takes the 2020 to 2021 period,
feeds the real VIX, the real FOMC target path and a credit path converted from
real high yield fund prices into a 505 day run, and scores the result against
what Apple's shares actually did. The daily series it uses are committed
alongside it, so it runs offline and gives the same answer every time.

It is written around a failure rather than a result. The first attempt puts a
drawdown of roughly the right depth two months away from the real one, and the
notebook then sweeps each macro field on its own to find out why. Only
`qe_pe_boost` moves a valuation, and it had been pinned at zero through the
entire crash, so nothing could re-rate. Supplying the market's own valuation
path moves the trough onto the correct day and brings the drawdown within a
fraction of a percent, at the cost of overshooting volatility, which the
notebook reports rather than smooths away.

Two findings from that sweep are worth having outside the notebook.
`fear_greed_index` is inert with respect to price: pinned anywhere from 0 to
100 it produces bit identical trajectories. `inflation_rate` reaches prices
only through `corporate_bond_yield`, so pinning both is the same as pinning the
yield alone. Neither is a change in behaviour, and both are easy to assume
otherwise.

## 0.1.2

Fixes the PyPI project page. Nothing in the engine moved and the digest is
unchanged.

The README links to the example notebooks, the licences, the contributing
guide and the citation file by relative path. That is correct on GitHub and
dead on PyPI, which renders the same file at pypi.org, so
`examples/01-first-simulation.ipynb` resolved to
`pypi.org/project/pretium/examples/01-first-simulation.ipynb` and returned a
404. Eleven links were affected.

The reason it survived two releases is that the file renders correctly
everywhere an author looks at it. So the rule is now checked rather than
remembered: a test fails if any README link is relative, and a second one
fails if an absolute link into this repository names a file that is not
there, which would leave the project page pointing at a GitHub 404 after a
rename.

## 0.1.1

A packaging fix. Nothing in the engine moved, and the digest is unchanged, so
a result produced under 0.1.0 reproduces here exactly.

0.1.0 published five wheels and no source distribution. PyPI refused the
sdist because it declared its licence files at the package root while the
build had put them under `rust/`, and it checks that the declared path
exists. The wheels were never affected: a wheel carries its licences in
`.dist-info` by a different mechanism, so the fault showed up on one
artefact out of six and the release went out half finished.

This release ships the source distribution, with the licence paths declared
explicitly rather than inferred.

The v0.1.0 tag was left where it is. Moving it to pick up the fix would have
broken the link between that tag and the wheels already published from it,
and that link is the point.

Two smaller things. The publish step now skips files already present, so a
rejection partway through a release can be re-run instead of leaving the
version stranded. And the determinism gate runs on pushes to the main
branch, not only on tags, because the badge could otherwise report a result
from a tree that no longer existed.

## 0.1.0

First release.

pretium simulates an equity market you can run a strategy against. Give it a
seed and a roster of companies and it runs prices, a limit order book, fills
and a macro economy forward. Orders match against real depth, so trading
moves the price.

### What's in it

The engine covers price formation, a limit order book with price-time
priority and partial fills, and an economy that advances daily under a
five-phase business cycle.

On top of that sit the things you need to get an answer out of it: agent
evaluation against reference baselines and an oracle, ranking across seeds
with paired sign tests, transaction cost analysis, parameter sweeps, replay,
and checkpointing with branching. There's a Gymnasium environment for RL
work, five Arrow tables for getting results into polars or pandas, and an
SEC EDGAR loader if you want real fundamentals instead of generated ones.

Two things are less usual. The simulator computed every price, so it can
tell you why one moved: seven factor contributions per instrument per tick
that add up to the move. And because the same seed reproduces the same
market, you can run it twice, once with your orders and once without, and
price every fill against the market where you never traded.

### Determinism

The same seed gives the same market on every platform we ship for. That's
checked on each release rather than asserted: five targets build a wheel,
run one fixed simulation inside it, and compare digests. A disagreement
fails the release.

Verified for this one on linux-x86_64, linux-aarch64, macos-arm64,
macos-x86_64 and windows-x86_64, all reproducing

```
5bd011be292f823ce1c360d1a12bf46de3362deee058a37283c74ab47069d0c1
```

A WebAssembly build (`--features wasm`) produces the same numbers, which
means the engine can run in a browser without becoming a second model that
quietly disagrees with this one.

### How realistic it is

Ten statistics are measured against real-market bands. At the certified
252-day horizon the shipped `pt-v3` preset holds nine of the ten in band,
and holds the same nine on seeds and a roster the calibration never saw.

The tenth is the autocorrelation of volume changes, which misses by 13.7
seed-standard-deviations. It's left out of the calibration objective on
purpose, because pointing an optimiser at a target it can't reach distorts
everything else it touches.

Six further gaps are written down with what each one stops you concluding,
and `envelope.check()` will refuse a question that falls outside them rather
than answering it anyway.

### Presets

`pt-v3` is the default and is what the envelope certifies.

`pt-v4` also ships, and is not the default. It halves the calibration
objective and is the first preset to bring 504-day kurtosis inside its band,
which no earlier one managed. It pays for that at one year, where it holds
eight of ten statistics in band instead of nine. Use `pt-v3` for horizons up
to a year and `pt-v4` for multi-year work.

`pt-v1` and `pt-v2` remain selectable and reproduce exactly what they always
did.

### Driving it from an agent

`pip install "pretium[mcp]"` adds an MCP server, so a coding agent can use
the simulator through eleven read-only tools. Strategies, universes and
scenarios are all composed as data, and there's no way to get from a tool
argument to running code. Results carry their caveats and provenance with
them, because a model summarising a result has the tool output and nothing
else to go on.

### A note on versioning

Anything that changes the simulated trajectory is a breaking change here,
however small it looks. A market that runs differently from the same seed
invalidates every published result that cited it. So coefficient changes
arrive as a new model preset rather than an edit to an existing one, and old
presets keep running exactly as they did.
