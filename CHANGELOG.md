# Changelog

## Unreleased

**A run can commit to the state it held at the end of every day**, and a
sampled check verifies k days for the cost of k days.

**The per-name volume states follow the roster**, moving the volume-idio
generator of any run that lists or delists, breaking such runs' old
checkpoints and letting the day ledger check them.

**Every print decomposes into the shock that arrived, the depth that
absorbed it and the breaker's share, with a counterfactual against
unbounded depth on request.**

<!-- release-note-ends -->

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

### The file digest (#127)

`Snapshot.hash` is computed over canonical JSON and was always portable.
`sha256` over the saved file was a property of the operating system: a
snapshot saved on Windows and a fresh clone of the same commit carried
different digests, because `.gitattributes` sets `* text=auto eol=lf` and
git normalised 5,450 CRLF pairs on the way in. The three loaders are
unchanged, so every recording on disk still reads.
`tests/test_portable_writes.py` exercises each writer and then scans the
package for a text-mode write anywhere.

### The exact roster (#128)

An exact fetch returning 498 of 500 has lost two members, so `ciks=`
accounts for every request: each CIK comes back as a row or as an entry in
`Snapshot.excluded` with its own reason, and `notes["requested_ciks"]`
carries the request. `limit` and `rank_by` default to `None` so that
either beside a roster is refused, with the same resolution as before.

`observe(detail=)` adds no field to the allowlist, and the sealed-engine
proxy and hidden-value scan in `tests/test_finrobot.py` cover the compact
rendering too. The panel travels in `state()` and the fork agreement reads
it.

`pyautogen<0.3` and `anthropic<1.0` are the ceilings: the AutoGen 0.3
rewrite provides no `autogen` module, and the 1.0 SDK removed
`anthropic.types.Completion`. `pyautogen` 0.2.35 sends `temperature` on
every request and Claude Opus 5 and Sonnet 5 reject any explicit value, so
recorded runs use `claude-sonnet-4-5-20250929`. `tests/test_integrations.py`
compares refused responses in a committed recording against a count the
recording declares.

### The observed depth (#131)

`liquidity_crisis` takes quoted depth to 40%. Measured on
`Universe.random(6, seed=11)`, forked, with the scenario on one arm, the
engine's `avg_volume` column moved from 543,983 to 217,593 while the volume
the agent was shown and the 27,199-share participation cap both stayed at
their pre-crisis values, so the agent was allowed the same order in a book
holding 40% of the ladder. `World.run` and `harness.evaluate` now re-read
the column at the top of each day, after `Scenario.apply`. Nothing in the
engine writes the column, so once a day is sufficient, and with no scenario
the re-read returns the value the old code held.
`tests/test_observed_depth.py` pins that case beside the four that fail
without the fix.

### The noise floor (#130)

A single pair of trajectories cannot separate a response to the
intervention from an agent answering the same question two ways.
`resample()` replays the two frozen inputs at one decision point N times
per arm and reports the within-arm spread beside the between-arm gap. Live
at temperature 0, the two prompts differed in 2 lines of 376; eight samples
per arm gave the control arm 4 distinct answers and a net of 0.62 +/- 0.99
against 1 distinct answer for the +200bps arm, so the recorded gap sat
inside the control arm's own standard deviation. Inputs that differ beyond
the intervention are refused with the moved fields named, refusals are
counted, a zero-variance arm reports a separation of `None`, and no p-value
is reported. `FrameworkAdapter.reask(entry)` performs one live interaction
from a record entry without touching adapter state, and `agent.record`
entries gain `payload`.

### The refusal policy (#129)

`World.run` called the agent with no guard, so a `DecisionError` from `act`
ended the run past the trace, the checkpoint and every artifact. On a live
pilot of 24 names and 60 planned decisions, call 36 carried a per-action
`rationale` field, `parse` refused it, and 35 recorded interactions went
with it. Under `on_refusal="skip"` the response is counted under
`unusable_responses`, a column kept apart from the market-side `refused`
and never summed with it, and `Comparison.ROWS` carries both. There is no
retry, because a second attempt is a second agent, and `parse` stays
strict.

`replay_response` raises when a recording has no answer, and a blanket skip
turned a transcript covering nothing into an agent that refused everything:
two arms replayed against one reported twenty refusals each and an empty
series. A missing entry and a recorded null now raise `ReplayMiss`, which
`World._ask` re-raises under either policy. It subclasses `DecisionError`,
so callers that catch one keep catching it. `World.fork` carries
`on_refusal` to both arms.

### The resumed recording (#129)

`prior=` is consulted before the provider, on the key the replay path uses,
so a run that died after N paid calls resumes with them. A `prior` whose
`instructions_digest` differs from the current one is refused at
construction, since instructions do not travel in the keyed input. The
recording carries `replayed_from_prior` and `called_live`, derived from the
two transcripts because a fork gives each arm its own adapter over one
shared recorder.

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
