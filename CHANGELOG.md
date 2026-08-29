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

**Scenarios are now a file you can hand to somebody else.** `tf.Scenario`
gains a second half: a named, inspectable collection of explicit
interventions, written in YAML or in Python, with a fingerprint over the
resolved experiment rather than over the file's bytes.

```yaml
version: 1
scenario:
  name: liquidity_crisis
  shocks:
    - target: market.liquidity
      operation: multiply
      value: 0.40
      at: 50
      duration: 25
```

```python
scenario = tf.Scenario.from_yaml("scenarios/liquidity_crisis.yml")
control, stress = tf.branch(engine, 2)
for day in range(80):
    scenario.apply(stress, day)
    ...
```

There is no `start_war()` and there will not be one. A scenario names
targets from an explicit registry of twelve things the engine actually
reads, applies one of three operations (`set`, `add`, `multiply`) on one of
four shapes (`impulse`, `hold`, `ramp`, `permanent`), and keeps its
EXOGENOUS SHOCKS apart from its ASSUMED TRANSMISSION -- because this library
cannot tell you that a 40% oil shock raises inflation 1.5 points, only run a
market in which somebody assumed it did.

Every registered target carries a MEASURED note saying what it is worth.
Four of the twelve are honest mechanisms with effects too small to see over a
hundred days, and `macro.fear_greed` measures at exactly 0.00% on every
instrument: nothing in the market reads it. Knowing which is which is the
difference between an experiment and a number.

- `Engine.pin_macro` gains `gdp_growth`, `unemployment_rate`, `tariff_rate`
  and `oil_price`, which the economy has always carried and the binding never
  exposed. Fractional like every other rate here; `oil_price` is dollars.
- `Engine.macro_fields` is the read side of `pin_macro`, field for field and
  unit for unit, so a relative intervention cannot be a factor of a hundred
  out.
- `Engine.set_avg_volume` writes the column the market maker quotes off,
  the shape a liquidity shock takes here: measured, quoted depth scales
  exactly with the multiplier and sweeping 50,000 shares costs 6.08bp at
  full depth and 14.59bp at a tenth of it. It is recorded in the order log
  like any other input, so a replay, a checkpoint and a fork all carry it.
- `tradefloor scenario validate|show|diff|targets` reads a scenario file
  without running a market. `python -m tradefloor ...` is the same tree.
- `RunManifest` records the RESOLVED scenario -- every intervention, its
  fingerprint and the source file's name -- so a run replays after the YAML
  is edited or deleted.
- `tf.compare` on an intervention scenario now differences it against the
  same world WITHOUT the interventions, and reports the firing trail.
- YAML is read by `tradefloor.yaml_subset`, which implements the block-style
  subset the schema uses and refuses everything else by name. No dependency,
  no tags, no anchors, no flow style, and nothing a scenario file could use
  to construct a Python object. It agrees with `yaml.safe_load` on every
  document it accepts, checked by a differential fuzz over sixteen thousand
  generated documents; the classes that fuzz found -- `0x1f` is thirty-one,
  `1_000` is a thousand, and `operation: -` is a syntax error rather than the
  string `-` -- are all refused now.
- A `hold` ends when it says it does, on every target. For a macro field
  that needs no help -- the chain recomputes it. Nothing in the engine writes
  `avg_volume` or `tariff_rate`, so on those the scenario puts the level back
  itself, once, when the LAST window on that target closes; the restore is in
  the audit trail as a `release`. Until it was, a twenty-five day liquidity
  crisis quietly lasted for the rest of the run.
- A relative operation cannot write a value its target cannot mean. `check`
  sees the multiplier and only the run sees the result, so the result is
  checked on the day it is written: `add -500` on `macro.vix` wrote a VIX of
  -485 and the market traded a session against it, because `(vix/15)^2`
  squares the sign away and nothing else looked.
- Six scenarios ship in `scenarios/`, each carrying its measured effect and
  the statement that it is not a forecast. None names a political actor.
- `examples/11-scenario-fork.py` is the whole workflow in one file.

A scenario built only from pins serialises exactly as it always has, schema
1, byte for byte, so every published manifest and every fingerprint over one
still means what it meant.

**`truth(day=N)` and `bars(day=N)` select a day.** They discarded the
argument once anything had been recorded, returning every recorded day with
the right schema and plausible values -- so `truth(day=4)` on a hundred-day
run answered with all hundred and looked like it had answered the question.
`day` is now optional: omitted is every recorded day, as these
tables have always returned and what a streaming consumer wants, so no
existing call changes. A day that was never recorded raises and names the
days that were, as a range when they are contiguous and a list when they are
not.

**A fork's manifest can name the checkpoint it began at.**
`Checkpoint.fingerprint` is a digest over the canonical serialisation, and
`RunManifest.of(..., derived_from=checkpoint)` records it with the
checkpoint's label and its log length. `derived_from` reads it back,
`describe()` prints it, and `verify_lineage(checkpoint)` checks the claim for
a reader who holds both. Lineage was previously derivable -- two branches
share a log prefix -- but only by comparing two manifests, so a reader
holding one could not tell it was a branch of anything.

The claim is checked when it is made, on identity before history. That order
is forced: an order log records inputs, so a run of the same sessions on
another seed carries a log that compares equal entry for entry, and a prefix
check alone would have accepted a checkpoint of an entirely different world.

**A failed `reproduce()` blames what the evidence supports.** The message led
with "an unmeasured platform pair" in every case and then printed the pair,
which was frequently the same platform twice -- a sentence that disproved its
own hypothesis while sending the reader to the Rust core. It now separates
the three cases the evidence already distinguishes: differing draw counts are
an input difference and no platform explains them; matching draws across two
machines are the cross-platform case the release gate exists to measure; and
matching draws on one machine leave build flags, a substituted wheel, or
arithmetic the era probe does not exercise.

**A corrupted checkpoint, a stale calibration surface, and the guards that
had stopped guarding.** `market_vol_gamma` was settable with no calibration
spec, which failed fifteen tests across five files and put the parameter out
of reach of any search; it now joins the reparameterised set, and the
market-factor stationarity check gained the gamma term it was missing, so a
survey can no longer plan a non-stationary factor variance. The MCP, gym and
Arrow surfaces are installed and run in CI rather than skipping. Six
documented environment knobs accept `TRADEFLOOR_` alongside the old
`PRETIUM_` spelling.

**The whole suite runs nightly.** `suite.yml` builds one wheel and runs the
~1,300 tests in four parallel batches, with every optional dependency
installed, plus Python 3.12 and 3.13 -- which the abi3 wheel serves and
nothing was testing. `tools/ci/batches.py` is the single definition of the
split and a test asserts it covers every file exactly once.

**`state_snapshot` drift is detectable.** It is a hand-written list of fields
that has been wrong six times, and since forking became a copy nothing in the
library uses it. A guard now restores a snapshot, compares it to a fork of
the same parent, and continues both -- in a market with every dormant dial
live, because a snapshot that forgets an inert field is invisible until a
preset turns it on. The guard is itself guarded: each field is dropped in
turn and must be caught, or must carry the condition its effect waits on.

**Forking is now a copy of the engine, and four ways it was not exact are
fixed.** `tf.branch` rebuilt a fork by writing a hand-maintained list of
fields into a fresh engine, and the list was incomplete. Most seriously, it
did not carry the day's endogenous news, which is generated once at
`open_market` and read by every tick of that day: a fork taken MID-DAY ran
the rest of the day with the news missing and priced differently from the
parent it was supposed to be a copy of. That was live on the shipped default
preset, `pt-v14`, and on every preset from `pt-v11`.

The other three were silent in a different way. A fork's order log was empty,
so a `Checkpoint` taken on a fork recorded a history that began at the fork
and resumed to a market that began at day zero, with nothing raised; a
`RunManifest` taken on one failed its own digest check and blamed a suspected
platform arithmetic difference between Windows and Windows. A mid-day fork
lost the day's already-recorded ticks, so `record` wrote a day half as long as
its parent's, well-formed and short. And a fork lost the previous close's
pending jump, so the first row of its next recorded day attributed nothing to
a move that happened.

All four had one cause and one fix: `Engine.fork` copies the engine, so there
is no list of fields to be incomplete and a field added later is carried
without anyone remembering to carry it. `tf.branch` calls it. Forks can now be
checkpointed, forked again, and written to a manifest, which they could not be
before. `universe` and `seed` become optional on `tf.branch` -- a copy cannot
land on the wrong roster, the hazard they were there to prevent -- and a
`universe` that is passed is checked against the engine's own tickers.

No trajectory moves: the known-answer digest is unchanged on every target.

**`state_snapshot` carries three more pieces of engine state**: the day's
endogenous news, the universe's remembered stress and the per-name volume
states. The last two are inert under every shipped preset, which is exactly
the position the common log-volume state was in before `pt-v10` turned it on
and a restored engine started trading different volume. `set_universe_stress`
existed for this and nothing called it. What a snapshot does NOT carry -- the
order log, the recorded tape, the pending jump -- is now written on the method
rather than left to be discovered.

**A corrupted checkpoint says it is corrupted.** `Checkpoint.from_json` on a
truncated or non-checkpoint payload raised `KeyError: 'seed'` or a `TypeError`
about string indices. It now names what is missing.

**`pretium.pdb` no longer ships inside the wheel.** `.gitignore` still named
`python/pretium/` after the rename, so `maturin develop` wrote an unignored
1.1 MB extension and a 1.0 MB Windows debug database into the source tree,
both were committed, and maturin packaged the debug database into every wheel
built from such a tree -- including the published 0.5.0. The stale committed
extension also shadowed the real one for anyone importing from `python/`.
The ignore rules name the directory that exists, the artefacts are untracked,
`[tool.maturin] exclude` stops a developer's build sweeping them up, and
`tests/test_packaging.py` checks all three.

**`tests/test_stub_parity.py` runs again.** Its `STUB` path still pointed at
`python/pretium/_core.pyi`, so its `skipif` skipped all ninety-nine of its
tests and reported green while the type stub went unread. A missing stub is
now an error rather than a skip.

**New:** `tests/test_forking.py` (38 invariant tests over the whole
run/checkpoint/fork/intervene/compare chain, including a cross-process
checkpoint resume), `tests/test_packaging.py`, and
`examples/10-forking-a-market.py`, a two-second runnable fork demonstration.
The reproducibility tests now run in CI on all five wheel targets.

**`tradefloor.counterfactual`: run one agent in two worlds that differ by
one variable.** The library had both halves of a controlled experiment and
no way to join them -- `branch` forks a running engine, `evaluate` runs an
agent, and `evaluate` runs start to finish with no moment inside it to
stop, fork, change one thing and continue. A `World` is that run loop:
market, agent, portfolio and macro path advancing a day at a time, with
`checkpoint()`, `fork()` and `intervene()` between days. `agree()` verifies
that two arms started identical across nine checks rather than asserting
it, and `compare()` finds the first step at which the macro, the agent's
decision, its orders, the prices and the portfolios came apart. The agent
is a parameter throughout, so swapping a deterministic policy for an LLM
or a third-party framework changes no line of the experiment.

**The canonical demo: `examples/rate-shock/counterfactual.py`.** Four
companies of differing duration, a macro-aware deterministic agent, twenty
days of shared history, a checkpoint, a fork, +200bps in one branch, and
twenty more days in each. Runs in about two seconds with no keys, no
network and no data files, writes a checkpoint, a `RunManifest` per arm, a
comparison and a chart, and is covered by `tests/test_rate_shock_demo.py`.
The tutorial is `examples/rate-shock/README.md`.

**Evaluate a real FinRobot agent: `tradefloor.integrations.finrobot`.** The
canonical rate-shock experiment, with the agent swapped and nothing else
moved: same seed, same roster, same pins, same twenty days of shared history,
same checkpoint, same fork, same +200bps, same comparison. The agent is a real
[FinRobot](https://github.com/AI4Finance-Foundation/FinRobot)
`SingleAssistant` running over `autogen`. `FinRobotAdapter` implements the
four methods `World` looks for and stays thin. FinRobot owns interpretation,
the portfolio decision and a rationale; Tradefloor owns the market,
execution, accounting, the fork and the comparison. FinRobot never mutates
engine state. It returns JSON, which the adapter parses, validates against the
listed universe and the supported sides, and refuses if it cannot be executed.
The observation is an allowlist, written out field by field, so fair value,
the factor attribution, the mispricing and the macro path ahead stay on the
Tradefloor side of the line. Two tests prove it: one against an engine that
raises when the forbidden surface is touched, one that computes the hidden
values and scans the agent's input for them.

`examples/finrobot/` is the study, script and notebook together. Both default
to replaying a genuine recorded run from `tests/fixtures/finrobot/`, keyed by
the SHA-256 of the exact text FinRobot was sent, so they need no API key, no
network and no FinRobot install. Change the observation mapping and the key
goes missing and the replay refuses, loudly, instead of serving an answer
recorded for a different question. `--live` calls the real thing behind
`pip install "tradefloor[finrobot]"`, which needs Python 3.11 exactly:
FinRobot declares `>=3.10, <3.12` and this project needs `>=3.11`.

**`examples/` now has two tiers.** The numbered files are a curriculum with a
reading order; a study is a directory. `examples/rate-shock/` and
`examples/finrobot/` each hold their script, their notebook, their README and
their own git-ignored `artifacts/`, because the script and the notebook for
one experiment present the same experiment two ways. A run regenerates its
artifacts, so they sit beside the run that wrote them. Recorded inputs a study
replays are committed once under `tests/fixtures/`, because two copies of a
recording can drift apart. `tests/test_examples.py` now walks `examples/`
instead of globbing `0*`, after the first unnumbered example arrived
with nothing checking it at all. `CONTRIBUTING.md` has the rule.

**Fixed: a forked world's log held the shared history twice.** `World`
carried its parent's order log across a fork because a branched engine's began
empty. `Engine.fork` then started copying the engine, which carries the log
for the same reason. Two fixes for one defect, and between them a manifest
built from a forked arm replayed the first days over again, reproducibly, into
a market nobody ran -- the outcome the first fix existed to prevent.
`World.order_log` is the engine's own now, on a root and a fork alike.

**Planned: a shared-book multi-agent arena.** Today `evaluate` and `rank`
give each agent its own copy of the market, which keeps the comparison
clean. The next step is one book with several agents in it,
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

Both presets now report 60, the rate they run. If you pinned either one in
0.4.0, your results are unaffected and need no rerun.

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

The 68.26 came from the calibration search that produced `pt-v14`, and this
record keeps it rather than a field the engine contradicts. Shipping
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
moving together quite so uniformly in a crisis, as real markets do and as the
old default got wrong at exactly the wrong moment.

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
