# Changelog

## Unreleased

### A correction to notebook 09, which shipped in 0.1.3 with a wrong claim

The transmission sweep in that notebook ran its probe over two days and
concluded from it that `federal_funds_rate` and `vix` have no effect on a
valuation. Both statements were wrong, and wrong for the same already
documented reason. The corporate bond yield, which is the rate equities
discount off, is recomputed only at central bank meetings, and the first is
scheduled 45 days out. A two day probe measures the meeting schedule and
reports it as an absence. The boundary is sharp: a policy rate of 0.5% against
10% gives bit identical valuations through day 45 and a 12.8% gap on day 46.
Unpinned, a 10% policy rate is worth about 11% of fundamental value.

`docs/scenario-recipes.md` has led with this trap since before the notebook was
written, states the rule that rate recipes must run at least 90 days, and gives
the measurement. The error was writing a probe without reading it.

### The transmission map, written down where it can be found

That page's heading said "nothing transmits before day 45", which is false and
was believed anyway. The sentence beneath it has always been correctly narrow,
a *policy-only* rate path, but the heading is what gets remembered. It now
carries the qualifier, and Scenarios has gained a per-field table measured by
introducing each shock on day 5 and reading day 25:

| field | median price move by day 25 | fair value |
|---|---|---|
| `vix` 15 to 60 | 39.2% | unchanged |
| `qe_pe_boost` 0 to -0.30 | 38.3% | -30.0% |
| `corporate_bond_yield` 5.5% to 11.4% | 28.3% | -9.9% |
| `federal_funds_rate` 1.6% to 10% | 0.00% | unchanged |
| `inflation_rate` 2% to 9% | 0.00% | unchanged |
| `fear_greed_index` 50 to 0 | 0.00% | unchanged |

Three fields act on the day you move them, two wait for a meeting because both
work only by steering the corporate bond yield, and one does nothing ever. The
split that matters is that `vix` moves prices hard without moving fair value at
all, so "transmits" and "re-rates" are different questions and were being asked
as one.

`tests/test_macro_transmission.py` pins the map: the meeting boundary at its
exact day, the immediate half, the fact that pinning the yield severs
everything upstream of it, and the inertness of `fear_greed_index`.

### pt-v6 responds to a VIX spike LESS than the default does

0.1.3 recommends pt-v6 for multi-year work where the tail matters, on band
counts: 8 of 10 at 504 days against pt-v3's 5, and 9 of 10 at the certified
horizon. That recommendation was incomplete, and the missing part lands on
exactly the long-dated crisis work it is meant for.

Measured over thirty seeds with `tools/calibration/scenario_response.py`, the
steady-state volatility lever from VIX 5 to VIX 65:

| preset | vol lever | note |
|---|---|---|
| real markets | 6.16x | the target nothing is close to |
| pt-v3, the default | 3.07x | |
| pt-v4 | 2.67x | jumps arrive, and the lever is spent here |
| pt-v5 | 2.69x | |
| pt-v6 | 2.68x | inherited |

A sustained crisis is about an eighth less violent under pt-v6 than under the
default, so a scenario study turning on crisis MAGNITUDE should prefer pt-v3
even over multi-year windows, or say that it accepted the weaker response. The
envelope's horizon gap now carries this, so anyone selecting pt-v6 on the band
counts reads the cost in the same paragraph.

**Two corrections to how that was first written here.** The cost is not
pt-v6's to answer for. The chain above puts the drop at pt-v3 to pt-v4, when
jumps arrived; pt-v5 and pt-v6 inherit it and pt-v6 changes it by 0.01x.
Attributing it to the newest preset was wrong.

And the harness's "shock response retained" percentage was quoted, at 27.6%
against 16.7%, which the calibration record says plainly should not be done.
That figure divides two small excesses over 1.0, so shock ratios of 1.062 and
1.038 become an eleven-point headline. The transient difference between the
two presets is real and it is 0.024. Quoting the percentage is a real number
at a resolution it does not have.

### A realism gap: endogenous inflation cannot reach its own crisis regime

The central bank has a crisis cadence that pulls the next meeting in to 21-30
days when a decision leaves it more than 2pp behind an inflation rate above
4%. It was measured as never firing, across five seeds and five years and
again under inflation pinned as high as 9%, and changed on the strength of
that to read the pre-decision rate instead.

**That change was wrong and has been reverted.** The JS oracle rejected it with
1618 mismatches and was right to. The path is not dead: it fires in 22.0% of
the 11,898 central-bank cases in the parity corpus. What the scenarios above
all had in common was low unemployment, where the Taylor rule hikes hard and
genuinely does catch up, so not firing was the correct answer. It fires in
stagflation, which none of them produced: at inflation 4.5% with unemployment
9.0% the bank cuts for the output gap and leaves itself further behind, and the
follow-up is pulled in. The post-decision reading is the better rule, because
the question is whether the decision was sufficient.

The gap the episode did find is real and sits elsewhere. Endogenous inflation
peaks at 4.06% to 4.11% over five seeds and five years, against a hard clamp of
6.0% and a US CPI that reached 9.1% in June 2022. So an entire regime is
implemented, correct and parity-tested, and a default run cannot get to it: a
2022-style inflation shock has to be driven through a scenario. Raising the
inflation process is an era boundary and belongs to a calibrated change, so it
is recorded and tested rather than fixed here.

The obvious explanation for that ceiling is also wrong, and worth writing down
so the next attempt does not start there. `inflation_mean_rev_coeff` is 0.55 a
month, which looks like it should pin inflation to its 2% target and implies an
AR(1) of 0.45. Measured over eight seeds and five years the monthly series has
AR(1) **+0.936**, against **+0.894** for real US CPI year-on-year across
2020-21: the model's inflation is if anything slightly more persistent than the
real thing, because the drivers around the reversion term carry their own
persistence.

So the defect is dispersion, not persistence. Model monthly inflation has sd
1.23 around a mean of 1.99% and spans -0.12% to 4.14%, where real CPI spanned
0.1% to 7.0% in 2020-21 alone, and 9.1% at the 2022 peak. Reaching that from
here is a 4.6 sigma excursion. A calibrated change should go after the driver
and shock magnitudes and leave the mean reversion alone.

The corrected account is more useful than either version. The model has
exactly two valuation channels. `corporate_bond_yield` is the discount rate,
and `federal_funds_rate`, `vix` and `inflation_rate` reach a valuation solely
by moving it, so pinning that yield severs all three at once. `qe_pe_boost` is
a direct multiple that bypasses the yield entirely and moves value one for one.

The notebook's own conclusion survives the correction unchanged. Its credit leg
is pinned to real HYG derived data, which is the right choice and which does
shadow the policy and volatility legs, so `qe_pe_boost` really was the only
lever left able to express a re-rating. What changed is the reason, from "those
fields do nothing" to "those fields were severed by a choice made three cells
earlier". The sweep now runs 120 days and shows both the pinned and unpinned
cases.

`fear_greed_index` is confirmed genuinely inert rather than merely slow: bit
identical prices at every value from 0 to 100 out to 504 days, and no pricing
code in the engine reads it.

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
