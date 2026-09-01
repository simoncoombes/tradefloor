# Changelog

## Unreleased

**An exact roster reaches EDGAR.** `tradefloor.edgar.fetch` takes `ciks=`, a
list of SEC Central Index Keys, and returns those filers and no others. Every
requested CIK comes back as a row or as an exclusion carrying a machine
readable reason, rows keep the requested order, and the snapshot notes record
the request and its digest. `limit` and `rank_by` behave as they always have,
and passing either beside `ciks=` raises.

**A large universe reaches an agent.**
`tradefloor.integrations.finrobot.observe` takes `detail=`, the symbols to
render in full. Every other symbol arrives as a compact row carrying price,
five-day return, position and order cap, and stays a legal action.
`FinRobotAdapter` takes `panel=`, which switches that rendering on and
travels through `state()` and `fork()`, so the fork agreement covers which
names each arm sees. Both default to the output 0.6.1 released, byte for
byte.

**A pin that installed a FinRobot unable to import itself.** The `finrobot`
extra admitted `pyautogen` 0.10 and `anthropic` 1.x, and both ceilings now
exclude the versions the adapter cannot drive.

<!-- release-note-ends -->

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
