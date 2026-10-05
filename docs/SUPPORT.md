# Support and long-term support

**Status: takes effect with 0.8.5**, the first long-term support (LTS)
release. Until 0.8.5 is tagged, the supported line is the one `SECURITY.md`
names.

tradefloor moved fast in 2026: 0.3.0 on 2026-08-27, 0.8.1 on 2026-09-24,
and five default presets in that month. That is fine for exploring and bad
for a study that has to stay pinned to one model for the life of a grant.
This page says what a researcher can pin to, and what we promise about it.

## Guarantees on every release line

These hold today, on every release line, LTS or not:

- **Old releases stay installable.** Every version on PyPI and crates.io stays published. crates.io versions can be yanked but never replaced, and we do not yank a release to hide a model change.
- **A shipped preset never changes.** A preset's coefficients are fixed when it first ships in a tagged release. A better coefficient is a new preset with a new name. `pt-v1` still runs exactly as it did in 0.3.0.
- **The fingerprint cannot lie.** `ModelParams.fingerprint()` hashes every coefficient's bit pattern. A vector equal to a shipped preset reports that preset's name. Anything else reports `custom-XXXXXXXX`.
- **The same seed gives the same market on every platform.** Each release runs one fixed simulation on five platforms and stops if any digest differs (`tests/known_answer.json`).
- **A named preset's untraded market replays exactly in every later release.** [Digest coverage](#digest-coverage) says how far that reaches for a run with agent orders in it.

A preset under development, on a branch and in no tagged release, can still
change. pt-v19 went through five compositions before it shipped in 0.8.0.
Only the vector a tagged release ships is frozen.

## The Rust crate's version policy

From 0.10.0 the crate follows [Cargo's semver rules](https://doc.rust-lang.org/cargo/reference/semver.html). While the version is 0.x, a minor release (0.10 to 0.11) may break the crate's API and a patch release (0.10.0 to 0.10.1) does not, so `tradefloor = "0.10"` in a `Cargo.toml` takes every patch release safely. The release workflow runs `cargo semver-checks` against the newest published crate under the release type the version bump implies, and a patch release that breaks the API does not publish. A minor release lists each break in the CHANGELOG, together with any change to a state width or to what a constructor does with its arguments, which no compiler or semver check catches.

Releases before 0.10.0 did not follow this: 0.8.5 broke code written for 0.8.1 in a patch release.

## The LTS line

**0.8.5 starts the first LTS line.** It is the first release whose default
preset is pt-v20, and the line covers 0.8.5 and the patch releases that
follow it.

pt-v20 is the right place to start. It centres quotes on the model price,
moves a stock's own news into its fair value, opens the market at its
stationary spread, and gives agents a book with depth, and 0.8.5 applies an
agent's fills to the market once. pt-v19 fails two long-run criteria that
pt-v20 passes, C4a and C4b, because a rule reading only prices finds an edge
on it that real markets do not have, so a study pinned to pt-v19 would pin
that defect. `ModelParams.pt_v20` in `rust/src/params.rs` lists every change.

### Support period

- **24 months** of bug and security fixes from the day 0.8.5 is tagged.
- After that, the line stays installable forever and gets no more fixes.
- The next LTS line is named at least 6 months before the current one ends, so the two overlap.

### Allowed changes in an LTS patch

- Bug fixes that leave every known-answer digest unchanged: the simulation digest and the combined digest in `tests/known_answer.json`, the per-preset digests in `tests/known_answer_presets.json`, the book digest in `tests/known_answer_book.json`, and the traded-run digests in `tests/known_answer_traded.json`.
- Security fixes, under the same condition.
- Wheels for a new CPython version or platform, if they build from the same source and reproduce the same digests.
- Documentation and error messages.

### Fixed for the life of an LTS line

- **Preset coefficients**, for every preset the line ships. Each preset's `ModelParams.fingerprint()` stays the same.
- **Known-answer digests.** A digest that moves means the trajectory moved, and a trajectory change cannot ship in an LTS patch.
- **The default preset.**
- **The random draw schedule.** How many draws are taken, from which stream, in what order.
- **The public API**: nothing is removed or renamed, no signature changes in a way that breaks a call, and no new features. This covers the Rust crate's public items as well as the Python package, and `cargo semver-checks` checks them before each release (RELEASING.md). The Rust API broke once, between 0.8.1 and 0.8.5, before the line began. CHANGELOG.md lists each change.
- **Saved formats**: a checkpoint, `RunManifest` or recorded transcript written by one patch release loads in every other patch release of the same line.
- **What an LLM agent is shown and how it answers.** The observation payload the adapters in `tradefloor.integrations` send (version 1) and the decision contract they accept (version 2) are fixed for the line. See [The agent payload and decision contract](#the-agent-payload-and-decision-contract).

### The agent payload and decision contract

Every adapter in `tradefloor.integrations` (callable, FinRobot, LangGraph,
OpenAI Agents, PydanticAI) shows its framework the same observation payload
and accepts the same decision. A recorded run replays only while both stay
the same, because each replay key is a digest of the payload the model was
shown. So both are frozen for the 0.8.x line. A 0.8.x patch release adds no
key, removes none, renames none, and changes the meaning of no value. A
change to either is a new version, and it ships in a minor release.

Each recording made with 0.8.5 or later carries
`observation_schema_version` and `decision_schema_version` in its `meta`.
A replay refuses a recording made under another payload version before it
looks anything up, and names both versions. A recording made before 0.8.5
carries neither field, predates this payload, and does not replay on 0.8.5.

**Observation payload, version 1** (`OBSERVATION_SCHEMA_VERSION`, built by
`serialize_observation`):

- Top level: `step`, `day`, `steps_per_day`, `macro`, `assets`, `portfolio`.
- `macro`: the fields in `tradefloor.counterfactual.MACRO_FIELDS`.
- Each entry of `assets`: `symbol`, `price`, `return_1d`, `return_5d`, `volatility`, `best_bid`, `best_ask`, `avg_daily_volume`, `max_order_shares`, `position`, `fundamentals`. `fundamentals` holds whatever the caller supplied for that symbol. On a model that pays dividends an entry also carries `dividend`, the cash per share the name went ex for at that day's open, and on a model that runs the earnings calendar `next_earnings_in_sessions`, the sessions to its next report. pt-v21, the default from 0.10.0, does both; no earlier preset does either.
- `portfolio`: `cash`, `net_worth`, `leverage`, `max_leverage`, `buying_power`, `open_orders`. `leverage` is gross exposure as a multiple of net worth. `cash`, `net_worth` and `buying_power` are dollars.
- Each entry of `open_orders`, one per limit order of the agent's still waiting in the book: `symbol`, `side`, `limit_price`, `remaining`.

A return or volatility the agent has not yet seen enough prices for is
`null`. `return_5d` covers 30 steps, five days at six steps a day.

Compared with 0.8.1: `portfolio.gross_exposure` is renamed `leverage` (same
value), `portfolio.open_orders` is new, and `return_5d` covers five days
where it covered 4.83.

**Decision contract, version 2** (`DECISION_SCHEMA_VERSION`,
`decision_schema()`): an `actions` list and an optional `rationale`. Each
action has a `symbol`, a `side` (`BUY`, `SELL`, `HOLD` or `CANCEL`), a
`quantity` in shares, and optionally an `order_type` (`market` or `limit`)
and a `limit_price`. An action with a `limit_price` is a `tf.Limit`, and
`CANCEL` is a `tf.Cancel`. An action that breaks a rule is refused on its
own, with the reason in the decision's `refused` list and the scorecard's
`errors`, and the other actions in the decision trade.

### Errata for trajectory bugs

It does not go into the LTS line. The line gets an erratum instead: the
defect is written down in the release notes and in `tradefloor.envelope`,
where `check()` can refuse the question it affects. The fix ships in the
next minor release, as a new preset if it changes coefficients.

This is the trade the LTS line makes. A known defect that stays put is
better for a published result than a fix that silently changes it.

## Digest coverage

The known-answer tests run on all five platforms at every release, through
`tests/test_known_answer.py` in the determinism workflow.

- `tests/known_answer.py` runs one fixed simulation and hashes it (`tests/known_answer.json`), with a second digest for a roster holding the rate indices. `tests/known_answer_seed64.py` does the same for a seed above 2**32. No agent trades in any of them.
- `tests/known_answer_presets.py` runs one fixed 60-session market on every shipped preset and hashes each on its own (`tests/known_answer_presets.json`). No agent trades in these either.
- `tests/known_answer_book.py` covers the book agents trade against (`tests/known_answer_book.json`). It builds pt-v19 with the seven book dials at pt-v20's values, on a fixed 12-name roster, for 3 days of 6 steps. Four scripted agents send market orders from a tenth of a percent to a whole day's volume, queue limit orders at the touch and a cent inside the spread, and cancel, all through `Engine.submit_many` and `Engine.cancel`. The digest covers every report, fill, waiting order and impact row, the closing prices and the engine's state hash.
- `tests/known_answer_traded.py` covers a run through the Python harness (`tests/known_answer_traded.json`). It runs the five agents from `tf.baselines.reference_agents()` and one scripted agent that sends limit orders and cancels them, through `tf.evaluate` on the default preset by name (pt-v21 from 0.10.0, pt-v20 in 0.8.5 to 0.9.1), with seed 20260930, a fixed 12-name roster and 10 days of 6 steps. For each agent it hashes three things: the order log (what `act()` returned at each step, the prices the agent was shown, and what `explain()` answered), every fill with the closing prices of that agent's market, and every scorecard field. The lines in `errors` and `partial_fills` are hashed as counts, so rewording a message moves nothing. The baseline keeps a digest per agent and per part, so a failure names the agent and the part that moved.

So an untraded market is pinned on every preset, the engine's book and
fill path is pinned on one fixed script, and one traded `tf.evaluate` run is
pinned on the default preset. That run is what an agent benchmark reports. Runs with
your own agents, or through `tf.rank`, use the same harness code, and the
digest checks that code on this one run. A traded run made before 0.8.5
reproduces only on the release that made it.

0.8.5 also changed how an agent's fills reach the market, on every preset.
They are now applied once, on the next tick, where 0.8.1 fed them in as order
flow on every tick of the next step. So a traded run recorded before 0.8.5
replays up to its first trade and differs after it, even on a preset that
0.8.5 did not change. For this reason the liquidity-crisis study's
canonical run was recorded again, live, on 0.8.5: 60 model calls, in
`tests/fixtures/finrobot/liquidity-crisis.json`. Its notebook replays that
recording on 0.8.5 with no model call. The study's four replications, its
resample and its five-arm decomposition are summaries in
`examples/experiments/liquidity-crisis/data/` from runs made before 0.8.5,
and were not recorded again.

## Before the first LTS tag

One of the two things this policy relies on is in place, and one is not:

1. **One known-answer digest per shipped preset.** In place from 0.8.5. `tests/known_answer_presets.py` runs one fixed 60-session market on every shipped preset and hashes each on its own, and `tests/known_answer_presets.json` holds the digests. The determinism workflow checks every one on all five platforms at every release, and a new preset adds its row when it ships. On the day the baseline was recorded, all eighteen presets that 0.8.1 shipped gave the same digest on the published 0.8.1 wheel as on 0.8.5.
2. **A DOI per release.** Not yet. `CITATION.cff` and `.zenodo.json` are ready, and the Zenodo integration has to be switched on by the owner, as the "DOI (Zenodo)" section of [RELEASING.md](https://github.com/simoncoombes/tradefloor/blob/main/RELEASING.md) describes.

## Freezing and retiring presets

**Frozen.** A preset is frozen when it first ships in a tagged release.
From then on its coefficient vector, its fingerprint and its known-answer
digest are fixed. A later release may re-measure a frozen preset and
publish new figures about it. It never changes what the preset computes.

**Default.** From the LTS line onward, a new preset becomes the default only
in a minor release (0.x.0), never in a patch. 0.8.5 is the one exception,
because this policy starts there. The release notes say which preset moved and
what a user will notice. A run that names its preset replays exactly across
the move. A run that relied on the default does not, which is why every
published result should name its preset.

**Retired.** A preset is retired when it is no longer measured or
recommended: its record says retired and why, and the docs list it under
retired presets. A retired preset keeps running exactly, and it stays in
the package. Presets are not removed. Removing one would break every result
that cites it, and old results are the reason presets exist.

## Details for a paper

Name all of these, so a reader can rebuild your market:

- the tradefloor version, and the DOI of that release once one exists
- the preset name and its fingerprint (`tf.model_preset()`)
- the seeds
- the universe (`Universe.random(n, seed=...)` or the roster file) and its fingerprint
- any scenario file, and its digest

`RunManifest` records all of them, and `RunManifest.reproduce()` stops on
the first mismatch. It replays and checks the market, and returns the
engine. It does not recompute a score and carries none: its `result`
block holds the market's `digest`, the number of `days` and
`draws_consumed`, and `tf.evaluate` and `tf.rank` write no manifest. To
let a reader check a score, publish the agent, the call that scored it and
the seeds, and let them rerun it. A manifest that checks a score does not
exist yet.
