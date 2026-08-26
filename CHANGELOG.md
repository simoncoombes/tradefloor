# Changelog

## Unreleased

### The default is pt-v10: an era boundary

Every seeded trajectory changed. A run recorded before this release does not
reproduce after it unless it names its preset, and every earlier preset from
`pt-v1` through `pt-v9` stays selectable and reproduces bit for bit forever.
The known-answer baseline is regenerated at v10 and the cross-platform gate
runs against it.

`pt.Engine(seed=..., universe=...)` now runs pt-v10, which holds **all
fourteen realism statistics in band at the certified 252-day horizon**, on
the calibration seeds and on a 60-name universe it never saw. pt-v3 held
twelve. The envelope certifies pt-v10; `envelope.CERTIFIED` and the 504-day
table are re-derived on it, and `envelope.check()` no longer names a
statistic as out of band at 252 days, because none is.

Two defects the flip exposed, both invisible while the default carried
neither mechanism:

- **The truth table did not carry jumps.** `apply_jumps` moves `mispricing_s`
  after the tick loop, so the seven attribution components did not
  reconstruct a day on which a jump fired, on any preset from pt-v4 onward.
  `truth()` gains a `jump` column, `Engine.FACTORS` is eight, and the column
  lands on the row where the move is observed. The identity now holds to
  1e-16 on the default.
- **Snapshots did not carry the volume state.** The common log-volume AR(1)
  was omitted from `state_snapshot()`, which was harmless while it was
  effectively off and became a divergence the day pt-v10 turned it on: a
  restored engine traded different volume and printed different prices.

Two behavioural changes worth knowing, both consequences of a market that
responds to its own moves: a VIX pin now acts on the first day rather than
the second, because the sector draw's sigma reads the VIX inside the tick;
and flow large enough to move the index now costs about as much through
everyone else's volatility as through its own book, which
`examples/07-research-workflow.py` measures and states.

Calibration record §68 to §75.

### pt-v10: every statistic in band

The engine carries a common log-volume state, an AR(1) that has shipped
switched off since pt-v1. Turning it on used to spend a passing statistic:
`volume_change_acf1` reaches its band and `volume_abs_return_corr` leaves
its floor, because a market-wide volume multiplier adds volume variance
unrelated to any name's own moves. That trade was priced on the pt-v3 era
base, and the envelope has called the statistic unreachable ever since.

On the pt-v9 base both bands are reachable together, and the window is
narrow: at innovation sigma 0.20 the change autocorrelation is still 0.005
past its edge, and at 0.23 the correlation has left its floor. `pt-v10` sets
persistence 0.70 and innovation sigma 0.21 and holds **all fourteen
statistics in band at 252 days** on thirty training seeds, and fourteen of
fourteen again on a held-out 60-name universe. At 504 days it holds twelve.
§8 passes on every axis. Nothing measured pays for it: the crisis lever, the
correlation blend and the shock ratio are all within noise of pt-v9's.
Calibration record §73.

### pt-v9: a market that frightens itself

The VIX's fear channel was reading the wrong number. `market_return_pct`,
the return it reacts to, is built from `previous_tick_price`: the
cap-weighted move over the final MINUTE of the session, in percent, then
clamped downstream to a fraction. Measured, on the shipped presets: an index
day of -7.87% moves the VIX +0.15 points, half the worst days of a year move
it down, and with the gain raised to 5000 and the clamp opened the day's
return still correlates -0.065 with the next day's VIX change. The market
could not frighten itself, which is why its endogenous VIX never once crossed
its own crisis threshold in a year against a real 12.5% of days.

Six parameters now describe that channel, all shipped at the literals they
replace and bit-identical there: `vix_return_source`, `vix_return_gain`,
`vix_return_gain_up`, `vix_return_clamp`, `vix_target_shock_cap` and
`vix_cycle_amplitude`, plus `vix_realised_vol_weight` for the feedback from
the market's own volatility. `pt-v9` sets them, along with a second halving of the momentum term, and is the first preset
to hold **thirteen of fourteen statistics in band at 252 days and at 504**,
on training seeds, a held-out universe and the §8 overfitting control alike.
Its endogenous VIX reaches the crisis threshold on 6.7% of days. It is not
the default. Calibration record §68 to §71.

### Fixed: a checkpoint could resume a different market

`Checkpoint.of` decided whether to record the model by comparing the
engine's fingerprint against `ModelParams.from_preset()`, whose no-arg form
returned pt-v1 while `Engine` defaults to pt-v3. A pt-v1 run therefore
checkpointed with no model and resumed as pt-v3, replaying a market it had
not frozen. Runs under every other preset were unaffected, because their
fingerprints differed from pt-v1 and the model was carried.

The cause is fixed rather than the symptom: `ModelParams.from_preset()` with
no name now returns the engine's default preset, the same one `Engine(...)`
runs and `model_preset()` already reported. Passing a name explicitly is
unchanged, so `from_preset("pt-v1")` still returns pt-v1. Callers relying on
the no-arg form to mean pt-v1 will now get pt-v3, and the fingerprint says
so. `tests/test_checkpoint_model.py` round-trips every shipped preset and
pins the two defaults together.

`inflation_floor` (-1.0) is a dial, the third and last of the inflation
clamps. Measured over twelve seeds and three years: dropping it to -3.0 moves
the mean by 0.01 and the sd by 0.02, so it is not a lever. The reversion is.

What a wider inflation regime costs the equity panel, measured on pt-v8 at
thirty seeds with `inflation_reversion` 0.20 and `inflation_ceiling` 10: the
252-day panel gains lag-5 clustering and reads 13 of 14, the 504-day panel
loses the return autocorrelation and that same statistic and reads 11 of 14,
and the crisis state does not move. No preset takes the dials. Calibration
record §65.

## 0.1.4

Two new presets, four new panel statistics, five parameters that used to be
literals, and the measurements behind each. Nothing that already shipped
moves: the known-answer digest is unchanged, pt-v1 through pt-v6 reproduce
bit for bit, and pt-v3 remains the default and the certified preset.

### Presets

Both are selectable by name, neither is the default, and both were measured
on thirty training seeds at both horizons.

| preset | base | coefficients moved | measured |
|---|---|---|---|
| `pt-v7` | pt-v6 | `sector_factor_sigma` 0.002 to 0.012, `crisis_blend_source` 0 to 1, `sector_vix_coupling` 0 to 0.25, `idio_sigma_scale` down ten percent, `crisis_blend_cap` 0.8 to 0.98, `market_vol_ceiling_multiple` 8 to 16 | 12 of 13 in band at 252 and at 504 days; sector excess 0.128 and 0.118 against a band of 0.11 to 0.23; crisis volatility lever 3.31x |
| `pt-v8` | pt-v7 | market factor GARCH alpha 0.468 to 0.298 and beta 0.521 to 0.665, `market_factor_sigma` 0.0159 to 0.0088, `idio_sigma_scale` 0.733 to 0.653, market jumps and sector sigma at surveyed values | 13 of 14 at 504 days and 12 of 14 at 252; correlation persistence +0.315 against a band of 0.19 to 0.49; crisis volatility lever 4.34x |

pt-v7 is the first preset in which same-sector pairs co-move more than
cross-sector pairs, in calm markets and under a held crisis. pt-v8 is the
first whose correlation has a memory: in this model window correlation is
the market factor's variance (r = 0.92 within a run), and that variance had
almost none, because the shipped GARCH ran alpha 0.47.

Costs, measured rather than estimated. pt-v8 misses lag-5 clustering at 252
days by a quarter of a seed sd, and its crisis-state sector excess is +0.053
against pt-v7's +0.079 and a real +0.10. Real markets read 6.16x on the
crisis lever, so both presets remain short of it.

Gates both passed: thirty-seed panels at 252 and 504 days, a held VIX 45
state, six held-out seeds, a held-out 60-name universe, and the §8
overfitting control against a base whose own control passes. Derivation in
the calibration record, §58 to §64.

### Four new panel statistics

Banded on the same forty-name reference roster and by the same rule as the
original ten.

| statistic | measures | band at 252 days | pt-v3 |
|---|---|---|---|
| `corr_asymmetry` | correlation on days the market fell more than one sd, minus days it rose more | -0.25 to +0.45 | +0.004, in band |
| `corr_asymmetry_lagged` | the same, on the previous day's market return | -0.20 to +0.55 | -0.033, in band |
| `sector_excess_corr` | same-sector pairs minus cross-sector pairs | +0.11 to +0.23 | +0.004, 15 seed sd out |
| `corr_persistence_acf1` | lag-1 autocorrelation of correlation over 21-day windows | -0.19 to +0.54 | +0.04, in band |

`corr_persistence_acf1` also carries a 504-day band of 0.19 to 0.49, which is
the one that can judge: twelve windows in a year cannot, and the 252-day band
is wide because the real windows themselves are. Its seed noise is the
largest of any correlation-type statistic, so it sits outside the objective.

pt-v3 now reads twelve of fourteen in band at 252 days and seven of fourteen
at 504. Nothing in the engine moved; the misses were always there.

`sector_excess_corr` is recorded as gap 7, `sector-structure`, which
`envelope.check()` fires for any question naming it, and whose `forbids`
field names sector rotation, sector-neutral construction and industry
diversification. pt-v7 closes it.

### Five parameters that used to be literals

Each shipped at the value every preset already ran on, bit-identical by
branch. No preset uses any of them.

| parameter | ships at | decides |
|---|---|---|
| `crisis_blend_source` | 0.0 | whether the crisis blend takes its injection from the sector draw or the market component |
| `sector_vix_coupling` | 0.0 | how much of the sector draw's variance follows VIX |
| `fair_value_book_floor` | 0.0 | whether the book floor applies to profitable companies too |
| `inflation_reversion` | 0.55 | how fast endogenous inflation reverts to its 2% target each month |
| `inflation_ceiling` | 6.0 | the hard clamp on endogenous inflation |

`fair_value_book_floor` exists for a discontinuity: fair value jumps up as
earnings fall through zero, because profitable companies are valued on
earnings and loss makers at book times 1.2. On a book of 20.00, a company
earning 1.00 is worth 14.90 and one losing 19.49 is worth 24.00. The floor
makes fair value continuous and non-decreasing in earnings, and switching it
on re-values 42.8% of instruments from `Universe.random`, so adopting it is a
recalibration rather than a fix.

The inflation dials were measured after the release and the result is in the
next section of this file: they buy range and cost the long-horizon panel.

### New envelope gap: the macro state cannot reach its own crisis regimes

Endogenous inflation peaks at 4.06% to 4.11% over five seeds and five years
against a clamp of 6.0% and a US CPI that reached 9.1% in June 2022. The
cause is dispersion, sd 1.23 around a mean of 1.99%, not persistence: the
monthly series has AR(1) +0.936 against +0.894 for real CPI across 2020 and
2021.

The central bank's crisis cadence depends on it. That path pulls the next
meeting in to 21 to 30 days when a decision leaves it more than 2pp behind an
inflation rate above 4%, fires in 22.0% of the 11,898 central-bank cases in
the parity corpus, and a default run cannot reach it. It also fires in
stagflation rather than in high inflation as such.

So an inflation regime has to be driven through a scenario. The gap says how,
and the recipe was run before it was published, on real 2022 data over six
seeds against a real S&P of -20.0%:

| scenario | index, median of six seeds |
|---|---|
| no scenario | -12.6% |
| the real seven-hike path only | -13.1% |
| the published CPI path | -23.3% |

Inflation is the lever because it steers the central bank's own reaction into
the corporate bond yield; a pinned policy rate does not. `corporate_bond_yield`
must be left free, since pinning it severs that channel. Without the control
row the hike-path run reads as a 13% bear market.

`envelope.check()` gains a `macro_regime` flag so the gap is reachable, and
`tests/test_envelope_reachability.py` now requires every gap to be reachable
and every refusal to carry a figure. It caught one: the `scenario-magnitude`
refusal quoted no number and now gives 3.07x against a real 6.16x.

### What each macro field transmits, and when

`docs/scenarios.md` gains a table, measured by introducing each shock on day
5 and reading day 25.

| field | median price move by day 25 | fair value |
|---|---|---|
| `vix` 15 to 60 | 39.2% | unchanged |
| `qe_pe_boost` 0 to -0.30 | 38.3% | -30.0% |
| `corporate_bond_yield` 5.5% to 11.4% | 28.3% | -9.9% |
| `federal_funds_rate` 1.6% to 10% | 0.00% | unchanged |
| `inflation_rate` 2% to 9% | 0.00% | unchanged |
| `fear_greed_index` 50 to 0 | 0.00% | unchanged |

Three act the day you move them; two wait for a central bank meeting, because
both work only through the corporate bond yield; `fear_greed_index` is
settable and validated and no pricing code reads it.
`tests/test_macro_transmission.py` pins the map, including the meeting
boundary. The scenario recipes page previously headed this "nothing transmits
before day 45", which was true only of a policy-rate path and is now
qualified.

### Two envelope claims replaced by measurements

"The direction of response is right" now carries numbers. Driving the real
2020 to 2021 macro path through the model over 504 sessions, beside the same
correlations on real AAPL:

| channel | simulated | real AAPL |
|---|---|---|
| return against change in VIX | -0.423 | -0.622 |
| return against change in credit yield | -0.496 | -0.592 |
| return against change in valuation | +0.573 | +0.803 |
| absolute return against VIX level | +0.512 | +0.489 |

All four signs are right and the three directional channels run at seventy to
eighty five percent of the real response, which is the scenario-magnitude gap
from another angle.

pt-v6's scenario cost is now stated in the horizon gap: the volatility lever
from VIX 5 to 65 runs 3.07x at pt-v3, 2.67x at pt-v4, 2.69x at pt-v5 and
2.68x at pt-v6 against a real 6.16x, so a study that turns on crisis
magnitude should prefer pt-v3 even over multi-year windows. The cost was
spent when jumps arrived at pt-v4.

### Calibration tooling

- `atlas_survey.py plan --out` writes `meta.json`, so `run` refuses a
  mismatched plan. `--samples` bound nothing before, and one survey was
  killed by its own dead-man switch at 63.9% because the forecast said 48,000
  tasks and the run did 192,000.
- `atlas_survey.py --base <preset> --only a,b,c` surveys a few axes around a
  preset other than pt-v3, pinning the rest at the base. `EXPLICIT_RANGES`
  applies to the transformed axes, and every range must contain the base
  value.
- The survey records `corr_persistence_acf1` at both horizons and the held
  VIX 45 state's sector excess and kurtosis, which is how a candidate's
  crisis-state cost is visible at selection time.
- `gate_pick.py` runs every gate a candidate preset must pass in one command.
  `read_factor_memory_survey.py` and `read_scenario_frontier.py` read the
  survey. pt-v8 was registered through that path.
- `scenario_response.py` no longer prints "shock response retained", a ratio
  of two small excesses that turns 0.024 into an eleven point headline.
- The AWS survey launcher streams rows with `cp` rather than `sync`, which
  needs a `s3:ListBucket` the instance role does not have, and aborts if a
  one-byte write to the bucket fails.
- `read_decay_survey.py` gained an argument parser; entry points go from 21
  to 22.

### Worked example

`examples/09-a-pandemic-shaped-market.ipynb` gains a section testing whether
its effects follow its causes rather than only whether its path resembles the
real one. It carries the channel table above and an event study over the five
sessions after each of six dated events, agreeing on sign five times out of
six. The exception is the Fed's intermeeting cut of 3 March 2020: the model
has the textbook channel by which a cut helps equities and no representation
of an emergency cut reading as a panic signal.

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

It is kept for a smaller effect it does have: at 504 days it improves room
on kurtosis and on annualised volatility.

### Also

The realism envelope now records which selectable preset closes a gap, so a
reader who hits "tails are too thin over multi-year windows" can learn that
three presets do not have it. Naming a preset there is not a certification;
the default does not move.

`docs/envelope.json` carries the same field, so the citable artifact answers
the question without anyone reading the source.

### A worked example that drives real market data

`examples/09-a-pandemic-shaped-market.ipynb` takes the 2020 to 2021 period,
feeds the real VIX, the real FOMC target path and a credit path converted from
real high yield fund prices into a 505 day run, and scores the result against
what Apple's shares actually did. The daily series it uses are committed
alongside it, so it runs offline and gives the same answer every time.

The first attempt puts a drawdown of roughly the right depth two months away
from the real one, and the notebook sweeps each macro field to find out why.
Only `qe_pe_boost` moves a valuation, and it was pinned at zero through the
crash, so nothing could re-rate. Supplying the market's own valuation path
moves the trough onto the correct day and brings the drawdown within a
fraction of a percent, at the cost of overshooting volatility.

Two findings from that sweep hold outside the notebook.
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
